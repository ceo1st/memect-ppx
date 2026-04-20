import numpy as np
import cv2
import yaml
from typing import Any, Sequence, override
import onnxruntime as ort

from .model import OnnxModel
from .commons import FileInfo


class PPFormulaNetModel(OnnxModel):
    """PP-FormulaNet_plus ONNX模型，用于公式识别"""

    def __init__(self, model_path: str, yml_path: str):
        super().__init__()
        self._session = ort.InferenceSession(model_path)
        self._input_name = self._session.get_inputs()[0].name
        self._output_name = self._session.get_outputs()[0].name

        # 从yml加载字符集
        with open(yml_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        # 从 PostProcess.character_dict.fast_tokenizer_file 提取词表
        tokenizer = config['PostProcess']['character_dict']['fast_tokenizer_file']

        # 构建词表：added_tokens + vocab
        self._vocab = []
        for token in tokenizer.get('added_tokens', []):
            self._vocab.append(token['content'])

        # vocab是id到token的映射
        vocab_dict = tokenizer.get('model', {}).get('vocab', {})
        max_id = max(vocab_dict.values()) if vocab_dict else len(self._vocab) - 1
        vocab_list = [''] * (max_id + 1)
        for token, idx in vocab_dict.items():
            vocab_list[idx] = token

        self._vocab.extend([t for t in vocab_list if t and t not in self._vocab])
        self._idx_to_char = {idx: char for idx, char in enumerate(self._vocab)}

    def _preprocess(self, img: np.ndarray) -> np.ndarray:
        """预处理图片"""
        # 转灰度
        if len(img.shape) == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # 调整大小到模型输入尺寸 (通常是 32xW)
        h, w = img.shape
        target_h = 32
        target_w = int(w * target_h / h)
        img = cv2.resize(img, (target_w, target_h))

        # 归一化
        img = img.astype(np.float32) / 255.0
        img = (img - 0.5) / 0.5

        # 添加batch和channel维度 (1, 1, H, W)
        img = np.expand_dims(np.expand_dims(img, 0), 0)

        return img

    def _postprocess(self, output: np.ndarray) -> str:
        """后处理，将模型输出转为文本"""
        # output shape: (1, seq_len, vocab_size)
        pred_ids = np.argmax(output[0], axis=-1)

        # 解码，去除重复和blank
        chars = []
        prev_id = -1
        for idx in pred_ids:
            if idx != prev_id and idx != 0:  # 0通常是blank token
                if idx < len(self._idx_to_char):
                    chars.append(self._idx_to_char[idx])
            prev_id = idx

        return ''.join(chars)

    @override
    def _execute(self, files: Sequence[FileInfo]) -> list[str]:
        results = []
        for file in files:
            img = file.open_cv2()

            # 预处理
            input_data = self._preprocess(img)

            # 推理
            output = self._session.run([self._output_name], {self._input_name: input_data})[0]

            # 后处理
            text = self._postprocess(output)
            results.append(text)

        return results
