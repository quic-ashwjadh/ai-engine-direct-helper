# ---------------------------------------------------------------------
# Copyright (c) 2024 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause
# ---------------------------------------------------------------------
import os
import sys
import subprocess
import zipfile
import requests
import shutil
from tqdm import tqdm
from pathlib import Path
import qai_hub
import wget
import threading
import urllib.request as request

qnn_sdk_version =  {
    "2.24": "2.24.0.240626",
    "2.26": "2.26.0.240828",
    "2.28": "2.28.0.241029",
}

DSP_ARCH = "73"  # For X-Elite device.
QNN_LIBS_DIR = "qai_libs"

QNN_SDK_URL = "https://softwarecenter.qualcomm.com/api/download/software/qualcomm_neural_processing_sdk/"
QAI_APPBUILDER_WHEEL = "https://github.com/quic/ai-engine-direct-helper/releases/download/vversion.0/qai_appbuilder-version.0-cp312-cp312-win_amd64.whl"
QNN_DOWNLOAD_URL = "https://softwarecenter.qualcomm.com/#/catalog/item/a0844287-db23-11ed-a260-063166a9270b?type=Tool"
TEXT_RUN_SCRIPT_AGAIN = "Then run this Python script again."

QNN_SDK_ROOT="C:\\Qualcomm\\AIStack\\QAIRT\\"
HUB_ID="aac24f12d047e7f558d8effe4b2fdad0f5c2c341"
QAI_HUB_CONFIG = os.path.join(Path.home(), ".qai_hub", "client.ini")
QAI_HUB_CONFIG_BACKUP = os.path.join(Path.home(), ".qai_hub", "client.ini.bk")


def setup_qai_hub():
    if os.path.isfile(QAI_HUB_CONFIG):
        shutil.copy(QAI_HUB_CONFIG, QAI_HUB_CONFIG_BACKUP)
    run_command(f"qai-hub.exe configure --api_token {HUB_ID} > NUL", False)


def reset_qai_hub():
    if os.path.isfile(QAI_HUB_CONFIG_BACKUP):
        shutil.copy(QAI_HUB_CONFIG_BACKUP, QAI_HUB_CONFIG)

def is_file_exists(filepath):
    if os.path.exists(filepath):
        # print(f"{os.path.basename(filepath)} already exist.")
        return True
    return False

def download_qai_hubmodel(model_id, filepath, desc=None, fail=None):
    ret = True

    if is_file_exists(filepath):
        return ret

    path = os.path.dirname(filepath)
    os.makedirs(path, exist_ok=True)

    if desc is not None:
        print(desc)
    else:
        print(f"Downloading {os.path.basename(filepath)}...")

    setup_qai_hub()
    try:
        model = qai_hub.get_model(model_id)
        model.download(filename=filepath)
    except Exception as e:
        # print(str(e))
        print()
        ret = False
        if fail is not None:
            print(fail)
        else:
            print(f"Failed to download model {os.path.basename(filepath)} from AI Hub. Please try to download it manually and place it to {filepath}.")
        print("If you still can't download, please consider using proxy.")
    reset_qai_hub()

    return ret


def verify_package(url, filepath, desc=None, fail=None):
     # verify if package is ready.
     #  1. package exists.
     #  2. package size is correct.

    is_continue = False

    if os.path.exists(filepath):
        response = request.urlopen(url)
        remote_size = int(response.headers["Content-Length"])
        local_size = os.path.getsize(filepath)

        if remote_size == local_size:   # file is ready for using.
            # print(f"{filepath} is ready for using.")
            return True
        else:
            is_continue = True

    if is_continue:
        print(f"The file '{filepath}' is not ready. Please wait for downloading to complete.")

    if desc is not None:
        print(desc)
    else:
        print(f"Downloading {os.path.basename(filepath)}...")

    return False


class tqdmWget(tqdm):
    last_block = 0
    def update_progress(self, block_num=1, block_size=1, total_size=None):
        if total_size is not None:
            self.total = total_size
        self.update((block_num - self.last_block) * block_size)
        self.last_block = block_num

def download_url_pywget(url, filepath, desc=None, fail=None):
    ret = True

    # Disable warning for insecure request since we set 'verify=False'.
    import ssl
    ssl._create_default_https_context = ssl._create_unverified_context

    if verify_package(url, filepath):
        return ret

    path = os.path.dirname(filepath)
    os.makedirs(path, exist_ok=True)

    try:
        # wget.download(url, filepath, wget.bar_adaptive)
        with tqdmWget(unit='B', unit_scale=True, unit_divisor=1024, desc=os.path.basename(filepath)) as t:
            def download_callback(blocks, block_size, total_size, bar_function):
                t.update_progress(blocks, block_size, total_size)
            wget.callback_progress = download_callback
            wget.download(url, filepath, wget.bar_adaptive)

    except Exception as e:
        # print(str(e))
        print()
        ret = False
        if fail is not None:
            print(fail)
        else:
            print(f"Failed to download file from {url}. Please try to download it manually and place it to {filepath}.")
        print("If you still can't download, please consider using proxy.")

    return ret


def download_url_requests(url, filepath, desc=None, fail=None, chunk_size=8192):
    ret = True

    # Disable warning for insecure request since we set 'verify=False'.
    from requests.packages.urllib3.exceptions import InsecureRequestWarning
    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

    if verify_package(url, filepath):
        return ret

    path = os.path.dirname(filepath)
    os.makedirs(path, exist_ok=True)

    try:
        response = requests.get(url, stream=True, verify=False)
        if response.status_code != 200:
            raise ValueError(f"Unable to download file at {url}")

        total_size = int(response.headers.get('content-length', 0))

        with tqdm(total=total_size, unit='B', unit_scale=True, desc=os.path.basename(filepath)) as bar:
            with open(filepath, 'wb') as file:
                for data in response.iter_content(chunk_size=chunk_size):
                    file.write(data)
                    bar.update(len(data))

    except Exception as e:
        #print(str(e))
        print()
        ret = False
        if fail is not None:
            print(fail)
        else:
            print(f"Failed to download file from {url}. Please try to download it manually and place it to {filepath}.")
        print("If you still can't download, please consider using proxy.")

    return ret


def download_url_wget(url, filepath, desc=None, fail=None):
    ret = True

    if verify_package(url, filepath):
        return ret

    if fail is None:
        fail = f"Failed to download file from {url}. Please try to download it manually and place it to {filepath}."
    fail += "\nIf you still can't download, please consider using proxy."

    path = os.path.dirname(filepath)
    name = os.path.basename(filepath)
    os.makedirs(path, exist_ok=True)

    try:
        wget_exe_path = "tools\\wget\\wget.exe"
        wget_url = "https://eternallybored.org/misc/wget/releases/wget-1.21.4-winarm64.zip"

        if not os.path.exists(wget_exe_path):
            print(f"wget.exe not found. Please download it manually from '{wget_url}' and unzip it to '{wget_exe_path}'")
            return

        command = f'"{wget_exe_path}" --no-check-certificate -q --show-progress --continue -P "{path}" -O "{filepath}" {url}'
        # print(command)
        result = run(command, desc=desc, errdesc=fail, live=True)
        #print(result)

    except Exception as e:
        # print(str(e))
        ret = False

    return ret

def download_url(url, filepath, desc=None, fail=None):
    return download_url_wget(url, filepath, desc, fail)


def run_command(command, live: bool = True):
    try:
        env = os.environ.copy()
        env['PYTHONPATH'] = f"{os.path.abspath('.')}{os.pathsep}{env.get('PYTHONPATH', '')}"

        stdout = run(command, errdesc=f"Error running command", live=live, custom_env=env).strip()
        if stdout:
            print(stdout)
    except Exception as e:
        print(str(e))
        exit()


def run(command, desc=None, errdesc=None, custom_env=None, live: bool = True) -> str:

    run_kwargs = {
        "args": command,
        "shell": True,
        "env": os.environ if custom_env is None else custom_env,
        "errors": 'ignore',
    }

    if not live:
        run_kwargs["stdout"] = run_kwargs["stderr"] = subprocess.PIPE

    result = subprocess.run(**run_kwargs)

    if result.returncode != 0:
        error_bits = [
            f"{errdesc or 'Error running command'}.",
            f"Command: {command}",
            f"Error code: {result.returncode}",
        ]
        if result.stdout:
            error_bits.append(f"stdout: {result.stdout}")

        if result.stderr:
            error_bits.append(f"stderr: {result.stderr}")

        raise RuntimeError("\n".join(error_bits))

    return (result.stdout or "")


def is_installed(package):
    try:
        import importlib.metadata
        import importlib.util
        dist = importlib.metadata.distribution(package)
    except importlib.metadata.PackageNotFoundError:
        try:
            spec = importlib.util.find_spec(package)
        except ModuleNotFoundError:
            return False

        return spec is not None

    return dist


def run_pip(command, desc=None, live=False):
    python = sys.executable
    return run(f'"{python}" -m pip {command} ', desc=f"Installing {desc}", errdesc=f"Couldn't install {desc}", live=live)

def run_uninstall_pip(command, desc=None, live=False):
    python = sys.executable
    return run(f'"{python}" -m pip {command} ', desc=f"Uninstalling {desc}", errdesc=f"Couldn't install {desc}", live=live)


def setup_qai_env(version):
    if version in qnn_sdk_version:
        full_version = qnn_sdk_version[version]
        qnn_root_path = QNN_SDK_ROOT + full_version

        SDK_lib_dir = qnn_root_path + "\\lib\\arm64x-windows-msvc"
        SDK_hexagon_dir = qnn_root_path + "\\lib\\hexagon-v{}\\unsigned".format(DSP_ARCH)

        os.makedirs(QNN_LIBS_DIR, exist_ok=True)

        libs = [
            "QnnHtp.dll",
            "QnnSystem.dll",
            "QnnHtpPrepare.dll",
            "QnnHtpV{}Stub.dll".format(DSP_ARCH),
        ]

        hexagon_libs = [
            "libQnnHtpV{}Skel.so".format(DSP_ARCH),
            "libqnnhtpv73.cat",
        ]

        for lib in libs:
            if os.path.isfile(os.path.join(QNN_LIBS_DIR, lib)):
                os.remove(os.path.join(QNN_LIBS_DIR, lib)) 
            shutil.copy(os.path.join(SDK_lib_dir, lib), QNN_LIBS_DIR)

        for lib in hexagon_libs:
            if os.path.isfile(os.path.join(QNN_LIBS_DIR, lib)):
                os.remove(os.path.join(QNN_LIBS_DIR, lib))
            shutil.copy(os.path.join(SDK_hexagon_dir, lib), QNN_LIBS_DIR)


def install_tools():
    tool_path = "tools"
    wget_path = tool_path + "\\wget"
    wget_zip_path = tool_path + "\\wget.zip"
    wget_exe_path = wget_path + "\\wget.exe"

    if os.path.exists(wget_exe_path):
        return

    url = "https://eternallybored.org/misc/wget/releases/wget-1.21.4-winarm64.zip"
    fail = f"Failed to download tool from '{url}'. Please download it manually and unzip it to '{tool_path}'. " + TEXT_RUN_SCRIPT_AGAIN
    desc = f"Downloading '{url}' to {wget_path}"

    os.makedirs(tool_path, exist_ok=True)
    
    ret = download_url_pywget(url, wget_zip_path, desc=desc, fail=fail)
    if not ret:
        exit()
    
    print(f"Install 'wget.exe' to {wget_exe_path}")
    with zipfile.ZipFile(wget_zip_path, 'r') as zip_ref:
        zip_ref.extractall(wget_path)
        print()


def install_clean(directory, zip_name):
    for filename in os.listdir(directory):
        if filename.startswith(zip_name) and filename.endswith('.tmp'):
            filepath = os.path.join(directory, filename)
            os.remove(filepath)
            print(f"Deleted file: {filepath}")


def install_qai_sdk(version):
    if version in qnn_sdk_version:
        full_version = qnn_sdk_version[version]
        zip_name = "v" + full_version + ".zip"

        url = QNN_SDK_URL + zip_name
        qnn_zip_path = QNN_SDK_ROOT + zip_name
        qnn_root_path = QNN_SDK_ROOT + full_version

        if os.path.exists(qnn_root_path):
            print(f"QNN SDK {version} already installed at {qnn_root_path}")
            return

        if not os.path.exists(QNN_SDK_ROOT):
            os.makedirs(QNN_SDK_ROOT, exist_ok=True)

        # install_clean(QNN_SDK_ROOT, zip_name)
        desc = f"Downloading QNN SDK to {qnn_zip_path}\n"\
               f"If the downloading speed is too slow, please download it manually from {QNN_DOWNLOAD_URL} and install it. " + TEXT_RUN_SCRIPT_AGAIN
        fail = f"Failed to download file from {url}: \n\t1. Please try again a few times.\n\t2. If still doesn't work, please try to download it manually"\
               f" from {QNN_DOWNLOAD_URL} and install it. " + TEXT_RUN_SCRIPT_AGAIN
        ret = download_url(url, qnn_zip_path, desc=desc, fail=fail)
        # install_clean(QNN_SDK_ROOT, zip_name)
        if not ret:
            exit()

        print(f"Install QNN SDK to '{QNN_SDK_ROOT}'")
        with zipfile.ZipFile(qnn_zip_path, 'r') as zip_ref:
            zip_ref.extractall(QNN_SDK_ROOT)

        shutil.move(
            os.path.join(QNN_SDK_ROOT, "qairt", full_version),
            QNN_SDK_ROOT,
        )
        shutil.rmtree(os.path.join(QNN_SDK_ROOT, "qairt"))
        # os.remove(qnn_zip_path)  # remove downloaded package.
    
        # run_command(f"setx QNN_SDK_ROOT {qnn_root_path}")
        print(f"Installed QNN SDK {version} to '{qnn_root_path}' successfully.")

        return qnn_zip_path
    else:
        keys_list = list(qnn_sdk_version.keys())
        print("Supported versions are:")
        print(keys_list)
        return None


def install_qai_appbuilder(version):
    if version in qnn_sdk_version:
        qai_appbuilder_wheel = QAI_APPBUILDER_WHEEL.replace("version", version)
        dist = is_installed("qai_appbuilder")
        version_install = version + ".0"

        if dist and (dist.version != version_install):
            run_uninstall_pip(f"uninstall qai_appbuilder -y", "QAI AppBuilder " + dist.version)

        if (not dist) or (dist.version != version_install) :
            run_pip(f"install {qai_appbuilder_wheel}", "QAI AppBuilder " + version_install)

