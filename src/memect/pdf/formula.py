from io import BytesIO
from pathlib import Path
from typing import Any, List
import numpy as np
import cv2
from PIL import Image
import re

from memect.models import get_model_path


class Model:
    def __init__(self, model_path: str | Path, engine: str = "onnxruntime", device: str = "cpu"):
        self._model_path = Path(model_path)
        self._backend = engine.lower()
        self._device = device.lower()
        self._session = None
        self._init_session()

    def _init_session(self):
        if not self._model_path.exists():
            raise FileNotFoundError(f"{self._model_path} does not exist")

        if self._backend == "openvino":
            from openvino.runtime import Core
            core = Core()
            model = core.read_model(str(self._model_path))
            self._session = core.compile_model(model, "CPU")
        elif self._backend == "onnxruntime":
            from onnxruntime import InferenceSession, SessionOptions, GraphOptimizationLevel
            sess_opt = SessionOptions()
            sess_opt.log_severity_level = 4
            sess_opt.enable_cpu_mem_arena = False
            sess_opt.graph_optimization_level = GraphOptimizationLevel.ORT_ENABLE_ALL
            providers = self._get_ort_providers()
            self._session = InferenceSession(str(self._model_path), sess_options=sess_opt, providers=providers)
        else:
            raise ValueError(f"Unsupported backend: {self._backend}")

    def _get_ort_providers(self):
        if self._device == "cuda":
            return [("CUDAExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"}),
                    ("CPUExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"})]
        elif self._device == "dml":
            return ["DmlExecutionProvider", "CPUExecutionProvider"]
        return [("CPUExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"})]

    def __call__(self, input_data: List[np.ndarray]) -> List[np.ndarray]:
        if self._backend == "openvino":
            result = self._session(input_data)
            return [result[key] for key in result.keys()]
        input_names = [v.name for v in self._session.get_inputs()]
        return self._session.run(None, dict(zip(input_names, input_data)))


class PreProcess:
    def __init__(self, max_dims: List[int] = [672, 192], min_dims: List[int] = [32, 32]):
        self._max_dims = max_dims
        self._min_dims = min_dims
        self._mean = np.array([0.7931, 0.7931, 0.7931], dtype=np.float32)
        self._std = np.array([0.1738, 0.1738, 0.1738], dtype=np.float32)

    def pad(self, img: Image.Image, divable: int = 32) -> Image.Image:
        threshold = 128
        data = np.array(img.convert("LA"))
        if data[..., -1].var() == 0:
            data = data[..., 0].astype(np.uint8)
        else:
            data = (255 - data[..., -1]).astype(np.uint8)
        data = (data - data.min()) / (data.max() - data.min()) * 255
        if data.mean() > threshold:
            gray = 255 * (data < threshold).astype(np.uint8)
        else:
            gray = 255 * (data > threshold).astype(np.uint8)
            data = 255 - data
        a, b, w, h = cv2.boundingRect(cv2.findNonZero(gray))
        im = Image.fromarray(data[b:b+h, a:a+w]).convert("L")
        dims = [divable * (x // divable + (1 if x % divable else 0)) for x in [w, h]]
        padded = Image.new("L", tuple(dims), 255)
        padded.paste(im, (0, 0, im.size[0], im.size[1]))
        return padded

    def minmax_size(self, img: Image.Image) -> Image.Image:
        if self._max_dims and any(a / b > 1 for a, b in zip(img.size, self._max_dims)):
            img = img.resize((np.array(img.size) // max(a / b for a, b in zip(img.size, self._max_dims))).astype(int), Image.BILINEAR)
        if self._min_dims:
            padded_size = tuple(max(img_dim, min_dim) for img_dim, min_dim in zip(img.size, self._min_dims))
            if padded_size != img.size:
                padded_im = Image.new("L", padded_size, 255)
                padded_im.paste(img, img.getbbox())
                img = padded_im
        return img

    def normalize(self, img: np.ndarray) -> np.ndarray:
        return (img.astype(np.float32) - self._mean * 255) * np.reciprocal(self._std * 255, dtype=np.float32)

    def to_gray(self, img: np.ndarray) -> np.ndarray:
        return cv2.cvtColor(cv2.cvtColor(img, cv2.COLOR_RGB2GRAY), cv2.COLOR_GRAY2RGB)

    def __call__(self, img: Image.Image) -> np.ndarray:
        pad_img = self.pad(self.minmax_size(img))
        cvt_img = np.array(pad_img.convert("RGB"))
        return self.normalize(self.to_gray(cvt_img)).transpose(2, 0, 1)[:1][None, ...]


class Decoder:
    def __init__(self, model: Model, max_seq_len: int = 512):
        self._model = model
        self._max_seq_len = max_seq_len

    def __call__(self, start_tokens: np.ndarray, seq_len: int, eos_token: int, context: np.ndarray, temperature: float = 1.0, filter_thres: float = 0.9) -> np.ndarray:
        out = start_tokens
        mask = np.full_like(start_tokens, True, dtype=bool)
        for _ in range(seq_len):
            logits = self._model([out[:, -self._max_seq_len:].astype(np.int64), mask[:, -self._max_seq_len:], context])[0][:, -1, :]
            probs = self._softmax(self._top_k(logits, filter_thres) / temperature, axis=-1)
            sample = np.random.choice(len(probs.squeeze()), 1, p=probs.squeeze() / probs.sum())[None, :]
            out = np.concatenate([out, sample], axis=-1)
            mask = np.pad(mask, [(0, 0), (0, 1)], constant_values=True)
            if (np.cumsum(out == eos_token, axis=1)[:, -1] >= 1).all():
                break
        return out[:, start_tokens.shape[1]:]

    def _softmax(self, x: np.ndarray, axis: int = None) -> np.ndarray:
        a_max = np.amax(x, axis=axis, keepdims=True)
        a_max[~np.isfinite(a_max)] = 0
        return np.exp(x - a_max) / np.sum(np.exp(x - a_max), axis=axis, keepdims=True)

    def _top_k(self, logits: np.ndarray, thres: float) -> np.ndarray:
        k = int((1 - thres) * logits.shape[-1])
        ind = np.argpartition(logits, -k, axis=-1)[:, -k:]
        val = np.take_along_axis(logits, ind, axis=-1)
        probs = np.full_like(logits, float("-inf"))
        np.put_along_axis(probs, ind, val, axis=-1)
        return probs


class Tokenizer:
    def __init__(self, tokenizer_path: str | Path):
        from tokenizers import Tokenizer as HFTokenizer
        from tokenizers.models import BPE
        self._tokenizer = HFTokenizer(BPE()).from_file(str(tokenizer_path))

    def decode(self, tokens: np.ndarray) -> str:
        if len(tokens.shape) == 1:
            tokens = tokens[None, :]
        dec = [self._tokenizer.decode(tok.tolist()) for tok in tokens]
        return ["".join(d.split(" ")).replace("Ġ", " ").replace("[EOS]", "").replace("[BOS]", "").replace("[PAD]", "").strip() for d in dec][0]


class Parser:
    def __init__(self, resizer_path: str | Path|None=None, encoder_path: str | Path|None=None, decoder_path: str | Path|None=None,
                 tokenizer_path: str | Path|None=None, engine: str = "onnxruntime",use_cuda:bool=False,use_cann:bool=False,use_dml:bool=False):
        
        resizer_path = resizer_path or get_model_path('formula/image_resizer.onnx')
        encoder_path = encoder_path or get_model_path('formula/encoder.onnx')
        decoder_path = decoder_path or get_model_path('formula/decoder.onnx')
        tokenizer_path = tokenizer_path or get_model_path('formula/tokenizer.json')

        if use_cuda:
            device='cuda'
        elif use_cann:
            device='cann'
        elif use_dml:
            device='dml'
        else:
            device='cpu'

        self._preprocessor = PreProcess()
        self._resizer = Model(resizer_path, engine, device)
        self._encoder = Model(encoder_path, engine, device)
        self._decoder = Decoder(Model(decoder_path, engine, device))
        self._tokenizer = Tokenizer(tokenizer_path)
        self._bos_token = 1
        self._eos_token = 2
        self._max_seq_len = 512
        self._temperature = 0.00001

    def parse(self, img: Any) -> str:
        if isinstance(img, (str, Path)):
            img = Image.open(img)
        elif isinstance(img,bytes):
            img = Image.open(BytesIO(img))
        elif isinstance(img, np.ndarray):
            #必须为bgr
            img = Image.fromarray(cv2.cvtColor(img,cv2.COLOR_BGR2RGB))
        else:
            raise ValueError(f'不支持的图片类型:{type(img)}')

        resized_img = self._resize_image(img)
        context = self._encoder([resized_img])[0]
        tokens = self._decoder(np.array([[self._bos_token]]), self._max_seq_len, self._eos_token, context, self._temperature)
        latex = self._tokenizer.decode(tokens)
        return self._post_process(latex)

    def _resize_image(self, img: Image.Image) -> np.ndarray:
        pad_img = self._preprocessor.pad(img)
        input_image = self._preprocessor.minmax_size(pad_img).convert("RGB")
        r, w, h = 1, input_image.size[0], input_image.size[1]
        for _ in range(10):
            h = int(h * r)
            final_img, pad_img = self._preprocess_for_resize(input_image, r, w, h)
            argmax_idx = int(np.argmax(self._resizer([final_img.astype(np.float32)])[0], axis=-1))
            w = (argmax_idx + 1) * 32
            if w == pad_img.size[0]:
                break
            r = w / pad_img.size[0]
        return final_img

    def _preprocess_for_resize(self, input_image: Image.Image, r: float, w: int, h: int) -> tuple[np.ndarray, Image.Image]:
        resize_img = input_image.resize((w, h), Image.BILINEAR if r > 1 else Image.LANCZOS)
        pad_img = self._preprocessor.pad(self._preprocessor.minmax_size(resize_img))
        cvt_img = np.array(pad_img.convert("RGB"))
        gray_img = self._preprocessor.to_gray(cvt_img)
        normal_img = self._preprocessor.normalize(gray_img)
        final_img = normal_img.transpose(2, 0, 1)[:1][None, ...]
        return final_img, pad_img

    def _post_process(self, s: str) -> str:
        text_reg = r"(\\(operatorname|mathrm|text|mathbf)\s?\*? {.*?})"
        names = [x[0].replace(" ", "") for x in re.findall(text_reg, s)]
        s = re.sub(text_reg, lambda _: names.pop(0), s)
        while True:
            news = re.sub(r"(?!\\ )([\W_^\d])\s+?([\W_^\d])", r"\1\2", s)
            news = re.sub(r"(?!\\ )([\W_^\d])\s+?([a-zA-Z])", r"\1\2", news)
            news = re.sub(r"([a-zA-Z])\s+?([\W_^\d])", r"\1\2", news)
            if news == s:
                break
            s = news
        return s