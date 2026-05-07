from io import BytesIO
from pathlib import Path
from typing import Any
import math
import re
import json
import numpy as np
import cv2
import yaml
from PIL import Image, ImageOps
from tokenizers import AddedToken, Tokenizer as TokenizerFast

from memect.models import get_model_path


class Model:
    def __init__(self, model_path: str | Path, engine: str = "onnxruntime", device: str = "cpu"):
        self._model_path = Path(model_path)
        self._engine = engine.lower()
        self._device = device.lower()
        self._session = None
        self._init_session()

    def _init_session(self):
        if not self._model_path.exists():
            raise FileNotFoundError(f"{self._model_path} does not exist")

        if self._engine == "openvino":
            from openvino import Core, PartialShape
            core = Core()
            core.set_property("CPU", {"INFERENCE_NUM_THREADS": 0, "PERFORMANCE_HINT": "LATENCY"})
            model = core.read_model(str(self._model_path))
            for inp in model.inputs:
                shape = inp.get_partial_shape()
                if shape.rank.is_dynamic:
                    continue
                new_dims = [1 if d.is_dynamic else d.get_length() for d in shape]
                model.reshape({inp.get_any_name(): PartialShape(new_dims)})
            self._session = core.compile_model(model, "CPU")
        elif self._engine == "onnxruntime":
            from onnxruntime import InferenceSession, SessionOptions, GraphOptimizationLevel
            sess_opt = SessionOptions()
            sess_opt.log_severity_level = 4
            sess_opt.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_ALL
            sess_opt.enable_mem_pattern = True
            sess_opt.enable_cpu_mem_arena = True
            self._session = InferenceSession(str(self._model_path), sess_options=sess_opt, providers=self._get_providers())
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

    def __call__(self, inputs: dict) -> list:
        if self._engine == "openvino":
            result = self._session(inputs)
            return [result[k] for k in result.keys()]
        return self._session.run(None, inputs)

    def get_input_names(self) -> list[str]:
        if self._engine == "openvino":
            return [list(i.names)[0] for i in self._session.inputs]
        return [v.name for v in self._session.get_inputs()]

    def get_output_names(self) -> list[str]:
        if self._engine == "openvino":
            return [list(o.names)[0] for o in self._session.outputs]
        return [v.name for v in self._session.get_outputs()]


class _UniMERNetImgDecode:
    def __init__(self, input_size: list[int]):
        self._input_size = input_size

    def _crop_margin(self, img: Image.Image) -> Image.Image:
        data = np.array(img.convert("L")).astype(np.uint8)
        max_val, min_val = data.max(), data.min()
        if max_val == min_val:
            return img
        data = (data - min_val) / (max_val - min_val) * 255
        gray = 255 * (data < 200).astype(np.uint8)
        coords = cv2.findNonZero(gray)
        a, b, w, h = cv2.boundingRect(coords)
        return img.crop((a, b, w + a, h + b))

    def _resize_keep_ratio(self, img: Image.Image, size: int) -> Image.Image:
        w, h = img.size
        short, long = (w, h) if w <= h else (h, w)
        new_short = size
        new_long = int(size * long / short)
        new_w, new_h = (new_short, new_long) if w <= h else (new_long, new_short)
        return img.resize((new_w, new_h), resample=Image.BILINEAR)

    def __call__(self, img: np.ndarray) -> np.ndarray | None:
        try:
            img = self._crop_margin(Image.fromarray(img).convert("RGB"))
        except OSError:
            return None
        if img.height == 0 or img.width == 0:
            return None
        img = self._resize_keep_ratio(img, min(self._input_size))
        img.thumbnail((self._input_size[1], self._input_size[0]))
        delta_w = self._input_size[1] - img.width
        delta_h = self._input_size[0] - img.height
        pad_w = delta_w // 2
        pad_h = delta_h // 2
        padding = (pad_w, pad_h, delta_w - pad_w, delta_h - pad_h)
        return np.array(ImageOps.expand(img, padding))


class _UniMERNetTestTransform:
    def __call__(self, img: np.ndarray) -> np.ndarray:
        mean = np.array([0.7931, 0.7931, 0.7931]).reshape(1, 1, 3).astype("float32")
        std = np.array([0.1738, 0.1738, 0.1738]).reshape(1, 1, 3).astype("float32")
        scale = float(1 / 255.0)
        img = (img.astype("float32") * scale - mean) / std
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        squeezed = np.squeeze(gray)
        return cv2.merge([squeezed] * 3)


class _LatexImageFormat:
    def __call__(self, img: np.ndarray) -> np.ndarray:
        im_h, im_w = img.shape[:2]
        divide_h = math.ceil(im_h / 16) * 16
        divide_w = math.ceil(im_w / 16) * 16
        img = img[:, :, 0]
        img = np.pad(img, ((0, divide_h - im_h), (0, divide_w - im_w)), constant_values=(1, 1))
        img_expanded = img[:, :, np.newaxis].transpose(2, 0, 1)
        return img_expanded[np.newaxis, :]


class _UniMERNetDecode:
    SPECIAL_TOKENS_ATTRIBUTES = ["bos_token", "eos_token", "unk_token", "sep_token", "pad_token", "cls_token", "mask_token", "additional_special_tokens"]

    def __init__(self, character_list: dict):
        self._unk_token = "<unk>"
        self._bos_token = "<s>"
        self._eos_token = "</s>"
        self._pad_token = "<pad>"
        self._sep_token = None
        self._cls_token = None
        self._mask_token = None
        self._additional_special_tokens = []
        self._eos_token_id = 2

        fast_tokenizer_str = json.dumps(character_list["fast_tokenizer_file"])
        self._tokenizer = TokenizerFast.from_buffer(fast_tokenizer_str.encode("utf-8"))

        tokenizer_config = character_list.get("tokenizer_config_file")
        if tokenizer_config and "added_tokens_decoder" in tokenizer_config:
            added_tokens_decoder = {}
            for idx, token in tokenizer_config["added_tokens_decoder"].items():
                if isinstance(token, dict):
                    token = AddedToken(**token)
                if isinstance(token, AddedToken):
                    added_tokens_decoder[int(idx)] = token

            added_tokens_encoder = {k.content: v for v, k in sorted(added_tokens_decoder.items(), key=lambda x: x[0])}
            tokens_to_add = list(added_tokens_decoder.values())
            encoder = list(added_tokens_encoder.keys())
            tokens_to_add += [t for t in self._all_special_tokens_extended() if str(t) not in encoder and t not in tokens_to_add]

            if tokens_to_add:
                special_tokens = self._all_special_tokens()
                tokens, is_last_special = [], None
                for token in tokens_to_add:
                    is_special = (token.special or str(token) in special_tokens) if isinstance(token, AddedToken) else str(token) in special_tokens
                    if is_last_special is None or is_last_special == is_special:
                        tokens.append(token)
                    else:
                        self._add_tokens_to_tokenizer(tokens, is_last_special)
                        tokens = [token]
                    is_last_special = is_special
                if tokens:
                    self._add_tokens_to_tokenizer(tokens, is_last_special)

    def _add_tokens_to_tokenizer(self, tokens, special: bool):
        if special:
            self._tokenizer.add_special_tokens(tokens)
        else:
            self._tokenizer.add_tokens(tokens)

    def _all_special_tokens_extended(self) -> list:
        all_tokens, seen = [], set()
        for attr in self.SPECIAL_TOKENS_ATTRIBUTES:
            value = getattr(self, "_" + attr, None)
            if not value:
                continue
            tokens_to_add = value if isinstance(value, (list, tuple)) else [value]
            for t in tokens_to_add:
                if str(t) not in seen:
                    seen.add(str(t))
                    all_tokens.append(t)
        return all_tokens

    def _all_special_tokens(self) -> list[str]:
        return [str(t) for t in self._all_special_tokens_extended()]

    def _normalize(self, s: str) -> str:
        text_reg = r"(\\(operatorname|mathrm|text|mathbf)\s?\*? {.*?})"
        letter = r"[a-zA-Z]"
        noletter = r"[\W_^\d]"
        names = []
        for x in re.findall(text_reg, s):
            pattern = r"(\\[a-zA-Z]+)\s(?=\w)|\\[a-zA-Z]+\s(?=})"
            for m in re.findall(pattern, x[0]):
                if m not in ["\\operatorname", "\\mathrm", "\\text", "\\mathbf"] and m.strip():
                    s = s.replace(m, m + "XXXXXXX").replace(" ", "")
                    names.append(s)
        if names:
            s = re.sub(text_reg, lambda match: str(names.pop(0)), s)
        news = s
        while True:
            s = news
            news = re.sub(r"(?!\\ )(%s)\s+?(%s)" % (noletter, noletter), r"\1\2", s)
            news = re.sub(r"(?!\\ )(%s)\s+?(%s)" % (noletter, letter), r"\1\2", news)
            news = re.sub(r"(%s)\s+?(%s)" % (letter, noletter), r"\1\2", news)
            if news == s:
                break
        return s.replace("XXXXXXX", " ")

    def _remove_chinese_text_wrapping(self, formula: str) -> str:
        pattern = re.compile(r"\\text\s*{([^{}]*[一-鿿]+[^{}]*)}")
        return pattern.sub(lambda m: m.group(1), formula).replace('"', "")

    def _post_process(self, text: str) -> str:
        try:
            from ftfy import fix_text
            text = fix_text(text)
        except ImportError:
            pass
        text = self._remove_chinese_text_wrapping(text)
        return self._normalize(text)

    def __call__(self, preds: np.ndarray) -> list[str]:
        token_ids = np.array(preds).astype(np.int32)
        results = []
        for tok_id in token_ids:
            end_idx = np.argwhere(tok_id == self._eos_token_id)
            if len(end_idx) > 0:
                tok_id = tok_id[: int(end_idx[0][0]) + 1]
            text = self._tokenizer.decode(tok_id.tolist(), skip_special_tokens=True)
            results.append(self._post_process(text))
        return results


class Parser:
    """目前不支持cpu+openvino，因为有算子不支持"""
    def __init__(self, model_dir: str | Path | None = None, engine: str = "onnxruntime",
                 use_cuda: bool = False, use_cann: bool = False, use_dml: bool = False):
        if isinstance(model_dir,str) and model_dir in ("PP-FormulaNet_plus-S_infer","PP-FormulaNet_plus-M_infer"):
            model_dir = get_model_path(model_dir)
        model_dir = Path(model_dir) if model_dir else get_model_path("PP-FormulaNet_plus-M_infer")
        self._model_dir = model_dir
        self._model_path = model_dir / "inference.onnx"
        self._config_path = model_dir / "inference.yml"

        if use_cuda:
            device = "cuda"
        elif use_cann:
            device = "cann"
        elif use_dml:
            device = "dml"
        else:
            device = "cpu"

        with open(self._config_path, "r", encoding="utf-8") as f:
            self._config = yaml.safe_load(f)

        self._model_name = self._config["Global"]["model_name"]
        self._model = Model(self._model_path, engine, device)
        self._pre_ops = self._build_preprocess()
        self._post_op = self._build_postprocess()

    def _build_preprocess(self) -> list[Any]:
        ops:list[Any] = []
        for cfg in self._config["PreProcess"]["transform_ops"]:
            key = list(cfg.keys())[0]
            args = cfg.get(key) or {}
            if key == "UniMERNetImgDecode":
                ops.append(_UniMERNetImgDecode(args["input_size"]))
            elif key == "UniMERNetTestTransform":
                ops.append(_UniMERNetTestTransform())
            elif key == "LatexImageFormat":
                ops.append(_LatexImageFormat())
            elif key in ("UniMERNetLabelEncode", "KeepKeys"):
                continue
            else:
                raise ValueError(f"Unsupported preprocess op: {key}")
        return ops

    def _build_postprocess(self) -> _UniMERNetDecode:
        post_cfg = self._config["PostProcess"]
        if post_cfg["name"] != "UniMERNetDecode":
            raise ValueError(f"Unsupported postprocess: {post_cfg['name']}")
        return _UniMERNetDecode(post_cfg["character_dict"])

    def parse(self, img: Any) -> str:
        if isinstance(img, (str, Path)):
            img = cv2.cvtColor(cv2.imread(str(img)), cv2.COLOR_BGR2RGB)
        elif isinstance(img, bytes):
            img = np.array(Image.open(BytesIO(img)).convert("RGB"))
        elif isinstance(img, Image.Image):
            img = np.array(img.convert("RGB"))
        elif isinstance(img, np.ndarray):
            img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        else:
            raise ValueError(f"不支持的图片类型:{type(img)}")

        for op in self._pre_ops:
            img = op(img)

        input_names = self._model.get_input_names()
        inputs = {input_names[0]: img}
        outputs = self._model(inputs)

        preds = outputs[0]
        if preds.ndim == 1:
            preds = preds.reshape(1, -1)
        results = self._post_op(preds)
        return results[0] if results else ""
