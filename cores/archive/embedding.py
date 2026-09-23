"""Legacy vector decoding; new embeddings are disabled for subscription-only use."""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536
_MAX_INPUT_CHARS = 8000




async def embed_text(text: str, api_key: str = '') -> Optional[bytes]:
    """Embedding is disabled in the subscription-only profile; FTS remains available."""
    from prism_core.ai_models import settings
    settings('embedding')  # rejects accidental text-model substitution
    return None


def decode_embedding(blob: Optional[bytes]) -> Optional[np.ndarray]:
    if not blob:
        return None
    try:
        vec = np.frombuffer(blob, dtype=np.float32)
        if vec.shape != (EMBEDDING_DIM,):
            return None
        return vec
    except Exception:
        return None


def cosine(a: bytes, b: bytes) -> float:
    va = decode_embedding(a)
    vb = decode_embedding(b)
    if va is None or vb is None:
        return 0.0
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))
