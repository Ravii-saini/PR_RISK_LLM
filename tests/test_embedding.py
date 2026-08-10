from worker.retrieval.embedding import EMBEDDING_DIM, embed_text, embed_texts

MODEL = "jinaai/jina-embeddings-v2-base-code"


def test_embed_text_returns_correct_dimension():
    vec = embed_text("def add(a, b):\n    return a + b", MODEL)
    assert len(vec) == EMBEDDING_DIM


def test_embed_texts_preserves_order_and_count():
    texts = ["def add(a, b):\n    return a + b", "def sub(a, b):\n    return a - b"]
    vecs = embed_texts(texts, MODEL)
    assert len(vecs) == 2
    assert all(len(v) == EMBEDDING_DIM for v in vecs)
    assert vecs[0] != vecs[1]  # different inputs must not collide


def test_embed_texts_empty_list_returns_empty():
    assert embed_texts([], MODEL) == []


def test_embed_text_is_deterministic():
    code = "def helper(x):\n    return x * 2\n"
    assert embed_text(code, MODEL) == embed_text(code, MODEL)
