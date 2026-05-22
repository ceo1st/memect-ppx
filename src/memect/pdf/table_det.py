import logging
from pathlib import Path
from typing import Any, TypedDict

import PIL
import PIL.Image
import cv2
import numpy as np

class _Result(TypedDict):
    width:int
    height:int
    cells:list[tuple[float, float, float, float]]

class RTDETRTableCellDet:
    """RT-DETR-L_wireless_table_cell_det (PaddlePaddle ONNX)"""

    _logger=logging.getLogger(f'{__module__}.{__qualname__}')
    
    _INPUT_SIZE = (640, 640)  # (H, W)

    def __init__(self, model_path: str|Path, score_threshold: float = 0.5,engine:str='openvino',use_cuda:bool=False,use_cann:bool=False,use_dml:bool=False):
        self._score_threshold = score_threshold
        self._session=None
        self._ov_compiled=None
        if engine=='onnxruntime':
            import onnxruntime as ort
            providers:list[Any]=[]
            if use_cuda:
                providers.append("CUDAExecutionProvider")
            elif use_cann:
                providers.append("CANNExecutionProvider")
            elif use_dml:
                providers.append('DmlExecutionProvider')
            else:
                pass
            providers.append("CPUExecutionProvider")
            self._session = ort.InferenceSession(model_path, providers=providers)
            session_providers = self._session.get_providers()
            self._logger.info('use engine=%s,providers=%s',engine,session_providers)
        elif engine=='openvino':
            from openvino import Core
            ie = Core()
            model = ie.read_model(model_path)
            self._ov_compiled = ie.compile_model(model, "CPU")
            self._logger.info('use engine=%s',engine)
        else:
            raise ValueError(f'不支持的engine={engine}')

    def _preprocess(self, image: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Returns (image blob, im_shape, scale_factor) as paddle model expects."""
        orig_h, orig_w = image.shape[:2]
        img = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (self._INPUT_SIZE[1], self._INPUT_SIZE[0]))
        # mean=0, std=1 → no normalization, just /255
        blob = (img.astype(np.float32) / 255.0).transpose(2, 0, 1)[np.newaxis]
        im_shape = np.array([[self._INPUT_SIZE[0], self._INPUT_SIZE[1]]], dtype=np.float32)
        scale_factor = np.array([[self._INPUT_SIZE[0] / orig_h, self._INPUT_SIZE[1] / orig_w]], dtype=np.float32)
        return blob, im_shape, scale_factor

    def _postprocess(
        self, detections: np.ndarray
    ) -> list[tuple[float, float, float, float]]:
        """Paddle RT-DETR output: (N, 6) with [cls, score, x0, y0, x1, y1]."""
        cells: list[tuple[float, float, float, float]] = []
        for det in detections:
            score = float(det[1])
            if score >= self._score_threshold:
                cells.append((round(float(det[2]),1),round(float(det[3]),1),round(float(det[4]),1), round(float(det[5]),1)))
        return cells

    def __call__(self, image: np.ndarray,*,show_gui:bool=False) -> _Result:
        origin_h,origin_w = image.shape[0:2]
        blob, im_shape, scale_factor = self._preprocess(image)
        if self._ov_compiled is not None:
            infer = self._ov_compiled.create_infer_request()
            infer.infer({"image": blob, "im_shape": im_shape, "scale_factor": scale_factor})
            outputs = [infer.get_output_tensor(0).data]
        else:
            outputs: list[np.ndarray] = self._session.run(None, {  # type: ignore[assignment]
                "image": blob,
                "im_shape": im_shape,
                "scale_factor": scale_factor,
            })
        cells = self._postprocess(outputs[0])
        if show_gui:
            vis = image.copy()
            for x0, y0, x1, y1 in cells:
                cv2.rectangle(vis, (int(x0), int(y0)), (int(x1), int(y1)), (0, 255, 0), 2)
            combined = np.concatenate([image, vis], axis=1)
            PIL.Image.fromarray(cv2.cvtColor(combined, cv2.COLOR_BGR2RGB)).show()
        return {
            "width":origin_w,
            "height":origin_h,
            "cells":cells
        }
