"""Local sentence-embedding wrapper (sentence-transformers, GPU by default).

The default model is BAAI/bge-m3 (1024-dim, 8192-token context → handles long
synopses, strong Chinese retrieval). Documents are embedded as the record's
embed_text(); queries get a model-appropriate instruction prefix for the
asymmetric bge-zh-v1.5 family (bge-m3 needs none).
"""
from __future__ import annotations

import numpy as np

DEFAULT_MODEL = "BAAI/bge-m3"

# Synopsis (+ optional opening excerpt) is short; capping the sequence length far
# below bge-m3's 8192 default speeds up encoding several fold. 1024 leaves room
# for 简介 plus the EXCERPT_MAX_CHARS opening slice.
DEFAULT_MAX_SEQ_LEN = 1024

# bge-*-zh-v1.5 are asymmetric and expect this query instruction; bge-m3 doesn't.
_ZH_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："


def resolve_device(device: str = "auto") -> str:
    if device != "auto":
        return device
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


class Embedder:
    def __init__(self, model_name: str = DEFAULT_MODEL, device: str = "auto",
                 max_seq_len: int = DEFAULT_MAX_SEQ_LEN) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self.device = resolve_device(device)
        self.model = SentenceTransformer(model_name, device=self.device)
        if max_seq_len:
            self.model.max_seq_length = max_seq_len
        get_dim = getattr(self.model, "get_embedding_dimension", None) or \
            self.model.get_sentence_embedding_dimension
        self.dim = get_dim()
        name = model_name.lower()
        self._query_instruction = (
            _ZH_QUERY_INSTRUCTION if ("bge" in name and "zh" in name) else ""
        )

    def encode_docs(self, texts: list[str], batch_size: int = 32,
                    show_progress: bool = True) -> np.ndarray:
        vecs = self.model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            show_progress_bar=show_progress,
            convert_to_numpy=True,
        )
        return vecs.astype(np.float32)

    def encode_query(self, text: str) -> np.ndarray:
        vec = self.model.encode(
            self._query_instruction + text,
            normalize_embeddings=True,
            convert_to_numpy=True,
        )
        return vec.astype(np.float32)
