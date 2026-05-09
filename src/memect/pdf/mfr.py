from io import BytesIO
from pathlib import Path
from typing import Any
import numpy as np
from PIL import Image
import json

from memect.models import get_model_path


class Model:
    def __init__(
        self, model_path: str | Path, engine: str = "onnxruntime", device: str = "cpu"
    ):
        self._model_path = Path(model_path)
        self._engine = engine.lower()
        self._device = device.lower()
        self._session = None
        self._init_session()

    def _init_session(self):
        if not self._model_path.exists():
            raise FileNotFoundError(f"{self._model_path} does not exist")

        if self._engine == "openvino":
            from openvino import Core

            core = Core()
            core.set_property("CPU", {
                "INFERENCE_NUM_THREADS": 0,
                "PERFORMANCE_HINT": "LATENCY",
            })
            model = core.read_model(str(self._model_path))
            self._session = core.compile_model(model, "CPU")
        elif self._engine == "onnxruntime":
            import os
            from onnxruntime import (
                InferenceSession,
                SessionOptions,
                GraphOptimizationLevel,
                ExecutionMode,
            )

            sess_opt = SessionOptions()
            sess_opt.log_severity_level = 4
            sess_opt.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_ALL
            #sess_opt.execution_mode = ExecutionMode.ORT_SEQUENTIAL
            #sess_opt.intra_op_num_threads = os.cpu_count() or 4
            #sess_opt.inter_op_num_threads = 1
            sess_opt.enable_mem_pattern = True
            sess_opt.enable_cpu_mem_arena = True
            providers = self._get_providers()
            self._session = InferenceSession(
                str(self._model_path), sess_options=sess_opt, providers=providers
            )
        else:
            raise ValueError(f"Unsupported engine: {self._engine}")

    def _get_providers(self):
        if self._device == "cuda":
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        elif self._device == "cann":
            return ["CANNExecutionProvider", "CPUExecutionProvider"]
        elif self._device == "dml":
            return ["DmlExecutionProvider", "CPUExecutionProvider"]
        return ["CPUExecutionProvider"]

    def __call__(self, **inputs):
        if self._engine == "openvino":
            return self._session(inputs)
        return self._session.run(None, inputs)


class Tokenizer:
    def __init__(self, tokenizer_path: str | Path):
        with open(tokenizer_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._vocab = {v: k for k, v in data["model"]["vocab"].items()}
        self._bos_token_id = 1
        self._eos_token_id = 2
        self._pad_token_id = 0

    def decode(self, token_ids: list[int]) -> str:
        tokens = [
            self._vocab.get(tid, "")
            for tid in token_ids
            if tid not in [self._bos_token_id, self._eos_token_id, self._pad_token_id]
        ]
        return "".join(tokens).replace("Ġ", " ").strip()


class Parser:
    def __init__(
        self,
        model_dir: str | Path | None = None,
        engine: str = "onnxruntime",
        use_cuda: bool = False,
        use_cann: bool = False,
        use_dml: bool = False,
    ):
        model_dir = Path(model_dir) if model_dir else Path(get_model_path("mfr"))
        if use_cuda:
            device = "cuda"
        elif use_cann:
            device = "cann"
        elif use_dml:
            device = "dml"
        else:
            device = "cpu"

        self._encoder = Model(model_dir / "encoder_model.onnx", engine, device)
        self._decoder = Model(model_dir / "decoder_model.onnx", engine, device)
        self._tokenizer = Tokenizer(model_dir / "tokenizer.json")

        with open(model_dir / "config.json", "r") as f:
            config = json.load(f)
        self._bos_token_id = config["decoder_start_token_id"]
        self._eos_token_id = config["eos_token_id"]
        self._max_length = config["decoder"]["max_position_embeddings"]

    def parse(self, img: Any) -> str:
        if isinstance(img, (str, Path)):
            img = Image.open(img).convert("RGB")
        elif isinstance(img, bytes):
            img = Image.open(BytesIO(img)).convert("RGB")
        elif isinstance(img, np.ndarray):
            import cv2

            img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        else:
            raise ValueError(f"不支持的图片类型:{type(img)}")

        pixel_values = self._preprocess(img)
        encoder_outputs = self._encoder(pixel_values=pixel_values)
        encoder_hidden_states = (
            encoder_outputs[0]
            if isinstance(encoder_outputs, (list, tuple))
            else encoder_outputs["last_hidden_state"]
        )

        generated_ids = self._generate(encoder_hidden_states)
        return self._tokenizer.decode(generated_ids)

    def _preprocess(self, img: Image.Image) -> np.ndarray:
        img = img.resize((384, 384), Image.BILINEAR)
        img_array = np.array(img).astype(np.float32) / 255.0
        img_array = (img_array - 0.5) / 0.5
        return img_array.transpose(2, 0, 1)[np.newaxis, ...]

    def _generate(self, encoder_hidden_states: np.ndarray) -> list[int]:
        decoder_input_ids = np.array([[self._bos_token_id]], dtype=np.int64)
        generated = []

        for _ in range(self._max_length):
            outputs = self._decoder(
                input_ids=decoder_input_ids, encoder_hidden_states=encoder_hidden_states
            )
            logits = (
                outputs[0] if isinstance(outputs, (list, tuple)) else outputs["logits"]
            )
            next_token_id = int(np.argmax(logits[0, -1, :]))

            if next_token_id == self._eos_token_id:
                break

            generated.append(next_token_id)
            decoder_input_ids = np.concatenate(
                [decoder_input_ids, [[next_token_id]]], axis=1
            )

        return generated
