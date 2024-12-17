# ---------------------------------------------------------------------
# Copyright (c) 2024 Qualcomm Innovation Center, Inc. All rights reserved.
# SPDX-License-Identifier: BSD-3-Clause
# ---------------------------------------------------------------------

import os
import cv2
import numpy as np
import torch
from torch.nn.functional import interpolate, pad
from torchvision.ops import nms # nms from torch is not avaliable
import torchvision.transforms as transforms
from PIL import Image
from PIL.Image import fromarray as ImageFromArray
from typing import List, Tuple, Optional, Union, Callable
from qai_appbuilder import (QNNContext, Runtime, LogLevel, ProfilingLevel, PerfProfile, QNNConfig)

nms_score_threshold: float = 0.45
nms_iou_threshold: float = 0.7
yolov8 = None

# define class type
class_map = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    4: "airplane",
    5: "bus",
    6: "train",
    7: "truck",
    8: "boat",
    9: "traffic light",
    10: "fire hydrant",
    11: "stop sign",
    12: "parking meter",
    13: "bench",
    14: "bird",
    15: "cat",
    16: "dog",
    17: "horse",
    18: "sheep",
    19: "cow",
    20: "elephant",
    21: "bear",
    22: "zebra",
    23: "giraffe",
    24: "backpack",
    25: "umbrella",
    26: "handbag",
    27: "tie",
    28: "suitcase",
    29: "frisbee",
    30: "skis",
    31: "snowboard",
    32: "sports ball",
    33: "kite",
    34: "baseball bat",
    35: "baseball glove",
    36: "skateboard",
    37: "surfboard",
    38: "tennis racket",
    39: "bottle",
    40: "wine glass",
    41: "cup",
    42: "fork",
    43: "knife",
    44: "spoon",
    45: "bowl",
    46: "banana",
    47: "apple",
    48: "sandwich",
    49: "orange",
    50: "broccoli",
    51: "carrot",
    52: "hot dog",
    53: "pizza",
    54: "donut",
    55: "cake",
    56: "chair",
    57: "couch",
    58: "potted plant",
    59: "bed",
    60: "dining table",
    61: "toilet",
    62: "tv",
    63: "laptop",
    64: "mouse",
    65: "remote",
    66: "keyboard",
    67: "cell phone",
    68: "microwave",
    69: "oven",
    70: "toaster",
    71: "sink",
    72: "refrigerator",
    73: "book",
    74: "clock",
    75: "vase",
    76: "scissors",
    77: "teddy bear",
    78: "hair drier",
    79: "toothbrush"
}

def preprocess_PIL_image(image: Image) -> torch.Tensor:
    """Convert a PIL image into a pyTorch tensor with range [0, 1] and shape NCHW."""
    transform = transforms.Compose([transforms.PILToTensor()])  # bgr image
    img: torch.Tensor = transform(image)  # type: ignore
    img = img.float().unsqueeze(0) / 255.0  # int 0 - 255 to float 0.0 - 1.0
    return img

def torch_tensor_to_PIL_image(data: torch.Tensor) -> Image:
    """
    Convert a Torch tensor (dtype float32) with range [0, 1] and shape CHW into PIL image CHW
    """
    out = torch.clip(data, min=0.0, max=1.0)
    np_out = (out.permute(1, 2, 0).detach().numpy() * 255).astype(np.uint8)
    return ImageFromArray(np_out)

def custom_nms(boxes, scores, iou_threshold):
    '''
    self definition of nms function cause nms from torch is not avaliable on this device without cuda
    '''
    
    if len(boxes) == 0:
        return torch.empty((0,), dtype=torch.int64)
    
    # transfer to numpy array
    boxes_np = boxes.cpu().numpy()
    scores_np = scores.cpu().numpy()

    # get the coor of boxes
    x1 = boxes_np[:, 0]
    y1 = boxes_np[:, 1]
    x2 = boxes_np[:, 2]
    y2 = boxes_np[:, 3]

    # compute the area of each single boxes
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores_np.argsort()[::-1]

    keep = []
    while order.size > 0:
        i = order[0]
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])

        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        ovr = inter / (areas[i] + areas[order[1:]] - inter)

        inds = np.where(ovr <= iou_threshold)[0]
        order = order[inds + 1]

    return torch.tensor(keep, dtype=torch.int64)

def batched_nms(
    iou_threshold: float,
    score_threshold: float,
    boxes: torch.Tensor,
    scores: torch.Tensor,
    *gather_additional_args,
) -> Tuple[List[torch.Tensor], ...]:
    """
    Non maximum suppression over several batches.

    Inputs:
        iou_threshold: float
            Intersection over union (IoU) threshold

        score_threshold: float
            Score threshold (throw away any boxes with scores under this threshold)

        boxes: torch.Tensor
            Boxes to run NMS on. Shape is [B, N, 4], B == batch, N == num boxes, and 4 == (x1, x2, y1, y2)

        scores: torch.Tensor
            Scores for each box. Shape is [B, N], range is [0:1]

        *gather_additional_args: torch.Tensor, ...
            Additional tensor(s) to be gathered in the same way as boxes and scores.
            In other words, each arg is returned with only the elements for the boxes selected by NMS.
            Should be shape [B, N, ...]

    Outputs:
        boxes_out: List[torch.Tensor]
            Output boxes. This is list of tensors--one tensor per batch.
            Each tensor is shape [S, 4], where S == number of selected boxes, and 4 == (x1, x2, y1, y2)

        boxes_out: List[torch.Tensor]
            Output scores. This is list of tensors--one tensor per batch.
            Each tensor is shape [S], where S == number of selected boxes.

        *args : List[torch.Tensor], ...
            "Gathered" additional arguments, if provided.
    """
    scores_out: List[torch.Tensor] = []
    boxes_out: List[torch.Tensor] = []
    args_out: List[List[torch.Tensor]] = (
        [[] for _ in gather_additional_args] if gather_additional_args else []
    )

    for batch_idx in range(0, boxes.shape[0]):
        # Clip outputs to valid scores
        batch_scores = scores[batch_idx]
        scores_idx = torch.nonzero(scores[batch_idx] >= score_threshold).squeeze(-1)
        batch_scores = batch_scores[scores_idx]
        batch_boxes = boxes[batch_idx, scores_idx]
        batch_args = (
            [arg[batch_idx, scores_idx] for arg in gather_additional_args]
            if gather_additional_args
            else []
        )

        if len(batch_scores > 0):
            nms_indices = custom_nms(batch_boxes[..., :4], batch_scores, iou_threshold)
            batch_boxes = batch_boxes[nms_indices]
            batch_scores = batch_scores[nms_indices]
            batch_args = [arg[nms_indices] for arg in batch_args]

        boxes_out.append(batch_boxes)
        scores_out.append(batch_scores)
        for arg_idx, arg in enumerate(batch_args):
            args_out[arg_idx].append(arg)

    return boxes_out, scores_out, *args_out

def draw_box_from_xyxy(
    frame: np.ndarray,
    top_left: np.ndarray | torch.Tensor | Tuple[int, int],
    bottom_right: np.ndarray | torch.Tensor | Tuple[int, int],
    color: Tuple[int, int, int] = (0, 0, 0),
    size: int = 3,
    text: Optional[str] = None,
):
    """
    Draw a box using the provided top left / bottom right points to compute the box.

    Parameters:
        frame: np.ndarray
            np array (H W C x uint8, BGR)

        box: np.ndarray | torch.Tensor
            array (4), where layout is
                [xc, yc, h, w]

        color: Tuple[int, int, int]
            Color of drawn points and connection lines (RGB)

        size: int
            Size of drawn points and connection lines BGR channel layout

        text: None | str
            Overlay text at the top of the box.

    Returns:
        None; modifies frame in place.
    """
    if not isinstance(top_left, tuple):
        top_left = (int(top_left[0].item()), int(top_left[1].item()))
    if not isinstance(bottom_right, tuple):
        bottom_right = (int(bottom_right[0].item()), int(bottom_right[1].item()))
    cv2.rectangle(frame, top_left, bottom_right, color, size)
    if text is not None:
        cv2.putText(
            frame,
            text,
            (top_left[0], top_left[1] - 10),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            size,
        )

# YoloV8 class which inherited from the class QNNContext.
class YoloV8(QNNContext):
    def Inference(self, input_data):
        input_datas=[input_data]
        output_data = super().Inference(input_datas)    
        return output_data

def Init():
    global yolov8

    # Config AppBuilder environment.
    QNNConfig.Config(os.getcwd() + "\\qnn", Runtime.HTP, LogLevel.WARN, ProfilingLevel.BASIC)

    # Instance for YoloV8 objects.
    yolov8_model = "models\\yolov8_det.bin"
    yolov8 = YoloV8("yolov8", yolov8_model)

def Inference(input_image_path, output_image_path):
    global image_buffer, nms_iou_threshold, nms_score_threshold

    # Read and preprocess the image.
    image = Image.open(input_image_path)
    image = image.resize((640, 640))
    outputImg = Image.open(input_image_path)
    outputImg = outputImg.resize((640, 640))
    image = preprocess_PIL_image(image) # transfer raw image to torch tensor format
    image  = image.permute(0, 2, 3, 1)
    image = image.numpy()

    output_image = np.array(outputImg.convert("RGB"))  # transfer to numpy array

    # Burst the HTP.
    PerfProfile.SetPerfProfileGlobal(PerfProfile.BURST)

    # Run the inference.
    model_output = yolov8.Inference([image])
    pred_boxes = torch.tensor(model_output[0].reshape(1, -1, 4))
    pred_scores = torch.tensor(model_output[1].reshape(1, -1))
    pred_class_idx = torch.tensor(model_output[2].reshape(1, -1))

    # Reset the HTP.
    PerfProfile.RelPerfProfileGlobal()

    # Non Maximum Suppression on each batch
    pred_boxes, pred_scores, pred_class_idx = batched_nms(
        nms_iou_threshold,
        nms_score_threshold,
        pred_boxes,
        pred_scores,
        pred_class_idx,
    )
    
    # Add boxes to each batch
    for batch_idx in range(len(pred_boxes)):
        pred_boxes_batch = pred_boxes[batch_idx]
        pred_scores_batch = pred_scores[batch_idx]
        pred_class_idx_batch = pred_class_idx[batch_idx]
        for box, score, class_idx in zip(pred_boxes_batch, pred_scores_batch, pred_class_idx_batch):
            class_idx_item = class_idx.item() 
            class_name = class_map.get(class_idx_item, "Unknown")

            draw_box_from_xyxy(
                output_image,
                box[0:2].int(),
                box[2:4].int(),
                color=(0, 255, 0),
                size=2,
                text=f'{score.item():.2f} {class_name}'
            )

    #save and display the output_image
    output_image = Image.fromarray(output_image)
    output_image.save(output_image_path)
    output_image.show()

def Release():
    global yolov8

    # Release the resources.
    del(yolov8)


Init()

Inference("input.jpg", "output.jpg")

Release()