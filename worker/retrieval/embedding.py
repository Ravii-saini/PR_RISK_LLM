"""Local code-embedding client (SPEC §5.4, §5.5).

Pinned model: jinaai/jina-embeddings-v2-base-code (161M params, code-aware,
Apache 2.0, runs locally via sentence-transformers — zero-cost constraint).
BAAI/bge-small-en-v1.5 is the documented general-purpose fallback if this
proves too slow on constrained hardware; not wired in unless needed.

Loading the model is the expensive part (seconds, CPU-bound download+init),
so it's cached process-wide rather than reloaded per call.
"""
from functools import lru_cache

from sentence_transformers import SentenceTransformer

EMBEDDING_DIM = 768


@lru_cache(maxsize=4)
def _load_model(model_name: str) -> SentenceTransformer:
    return SentenceTransformer(model_name, trust_remote_code=True)


def embed_texts(texts: list[str], model_name: str) -> list[list[float]]:
    """Embed a batch of code chunks. Returns one vector per input text, in
    the same order.
    """
    if not texts:
        return []
    model = _load_model(model_name)
    vectors = model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]


def embed_text(text: str, model_name: str) -> list[float]:
    return embed_texts([text], model_name)[0]
