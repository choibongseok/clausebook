"""다국어 임베딩. multilingual-e5-small 의 int8 ONNX 를 onnxruntime 으로 CPU 에서 돌린다.

e5 계열은 질문에 'query: ', 문서에 'passage: ' 를 붙이고 평균 풀링 뒤 정규화한 벡터를 쓴다.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol

import numpy as np

from .cache import LRU
from .config import EMBED_DIM, MODEL_DIR


class Embedder(Protocol):
    def embed_queries(self, texts: list[str]) -> np.ndarray: ...
    def embed_passages(self, texts: list[str]) -> np.ndarray: ...


class E5Onnx:
    name = "intfloat/multilingual-e5-small (int8 ONNX)"

    def __init__(self, model_dir: Path | None = None, max_len: int = 384):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        d = Path(model_dir or MODEL_DIR / "embed")
        self.tok = Tokenizer.from_file(str(d / "tokenizer.json"))
        self.tok.enable_truncation(max_length=max_len)
        self.tok.enable_padding(pad_id=1, pad_token="<pad>")
        so = ort.SessionOptions()
        so.intra_op_num_threads = 4
        self.sess = ort.InferenceSession(str(d / "model_qint8.onnx"), so, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self.sess.get_inputs()}
        self.cache = LRU(2048)

    def _run(self, texts: list[str]) -> np.ndarray:
        out = []
        for i in range(0, len(texts), 16):
            enc = self.tok.encode_batch(texts[i : i + 16])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._inputs:
                feeds["token_type_ids"] = np.zeros_like(ids)
            hidden = self.sess.run(None, feeds)[0]
            m = mask[..., None].astype(np.float32)
            pooled = (hidden * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
            pooled /= np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None)
            out.append(pooled.astype(np.float32))
        return np.vstack(out) if out else np.zeros((0, EMBED_DIM), dtype=np.float32)

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return self._run([f"passage: {t}" for t in texts])

    def embed_queries(self, texts: list[str]) -> np.ndarray:
        rows = []
        for t in texts:
            key = hashlib.sha1(t.encode()).hexdigest()
            rows.append(self.cache.get_or_set(key, lambda t=t: self._run([f"query: {t}"])[0]))
        return np.vstack(rows)


class HashEmbedder:
    """모델 없이 도는 결정적 대용품(단위 테스트 전용). 같은 글자 2-gram 이 많을수록 가깝다."""

    name = "hash-bigram (test only)"

    def _one(self, text: str) -> np.ndarray:
        v = np.zeros(EMBED_DIM, dtype=np.float32)
        t = "".join(text.split())
        for i in range(len(t) - 1):
            v[int(hashlib.md5(t[i : i + 2].encode()).hexdigest(), 16) % EMBED_DIM] += 1.0
        n = np.linalg.norm(v)
        return v / n if n else v

    def embed_queries(self, texts):
        return np.vstack([self._one(t) for t in texts])

    def embed_passages(self, texts):
        return np.vstack([self._one(t) for t in texts])
