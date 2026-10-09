"""Hybrid retrieval.

Vector search finds passages with the same *meaning* ("how do I get online?"
matches a Wi-Fi setup section), but it is weak at exact tokens: error codes,
part numbers, names, "Q25". BM25 keyword search is the opposite. We run both
and merge the two rankings with Reciprocal Rank Fusion (RRF), which only uses
ranks, so the two very different score scales never need to be compared:

    score(chunk) = sum over rankings of 1 / (RRF_K + rank)

Finally at most MAX_CHUNKS_PER_PAGE chunks are taken from any one page, so
the answer sees several parts of the document instead of one page repeated.
"""

import re
import threading
from collections import Counter

from flask import current_app
from langchain_core.documents import Document as Chunk
from rank_bm25 import BM25Okapi

from app.models import Document
from app.services import vector_store

MODES = ("hybrid", "vector", "keyword")
# \w alone splits Devanagari words at vowel signs (भुगतान -> भ, गत, न), so the
# Devanagari block is added explicitly, minus the danda punctuation (। ॥).
_TOKEN = re.compile(r"[\wऀ-ॣ०-ॿ]+")
# Question words that carry no content (English + common Hinglish).
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i if in is it its me my of on or should "
    "the this to was what when where which who why will with you your "
    "hai hain kya kaise kaun kab kitna kitne kitni ka ki ke ko se me mein aur ye yeh wo woh".split()
)

# BM25 needs every chunk's text. Tokenised chunks are cached per document and
# refreshed automatically when the document is re-indexed (version changes).
_cache: dict[str, tuple[str, list[Chunk], list[list[str]]]] = {}
_cache_lock = threading.Lock()


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def forget(doc_id: str) -> None:
    with _cache_lock:
        _cache.pop(doc_id, None)


def _corpus(documents: list[Document]) -> tuple[list[Chunk], list[list[str]]]:
    chunks, tokens = [], []
    for doc in documents:
        with _cache_lock:
            cached = _cache.get(doc.id)
        if cached is None or cached[0] != doc.version:
            doc_chunks = vector_store.get_chunks([doc.id])
            cached = (doc.version, doc_chunks, [tokenize(c.page_content) for c in doc_chunks])
            with _cache_lock:
                _cache[doc.id] = cached
        chunks.extend(cached[1])
        tokens.extend(cached[2])
    return chunks, tokens


def informative_terms(query_tokens: list[str], tokens: list[list[str]]) -> list[str]:
    """Query words worth a keyword search: not stopwords, present in the
    corpus, and in at most half of the chunks. Without this, a common word
    ("printer" in a printer manual) still produces a full but meaningless
    ranking, and RRF, which only looks at ranks, lets that noise push the
    right answer down (measured in evals/: Hit@1 dropped from 97% to 83%)."""
    document_frequency = Counter(term for chunk_tokens in tokens for term in set(chunk_tokens))
    limit = max(1, len(tokens) // 2)
    return [
        term for term in dict.fromkeys(query_tokens)
        if term not in STOPWORDS and 0 < document_frequency[term] <= limit
    ]


def keyword_search(query: str, documents: list[Document], n: int) -> list[Chunk]:
    chunks, tokens = _corpus(documents)
    terms = informative_terms(tokenize(query), tokens) if chunks else []
    if not terms:
        return []  # nothing distinctive to match: leave it to vector search
    scores = BM25Okapi(tokens).get_scores(terms)
    ranked = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
    return [chunks[i] for i in ranked[:n] if scores[i] > 0]


def rrf_fuse(rankings: list[list[Chunk]], k: int = 60) -> list[Chunk]:
    scores: dict[str, float] = {}
    by_id: dict[str, Chunk] = {}
    for ranking in rankings:
        for rank, chunk in enumerate(ranking, start=1):
            chunk_id = chunk.metadata["id"]
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
            by_id.setdefault(chunk_id, chunk)
    return [by_id[chunk_id] for chunk_id in sorted(scores, key=scores.get, reverse=True)]


def diversify(chunks: list[Chunk], k: int, per_page: int) -> list[Chunk]:
    picked, counts = [], {}
    for chunk in chunks:
        key = (chunk.metadata["doc_id"], chunk.metadata["page"])
        if counts.get(key, 0) < per_page:
            counts[key] = counts.get(key, 0) + 1
            picked.append(chunk)
            if len(picked) == k:
                break
    return picked


def search(query: str, documents: list[Document], mode: str | None = None) -> list[Chunk]:
    config = current_app.config
    mode = mode or config["RETRIEVAL_MODE"]
    if mode not in MODES:
        raise ValueError(f"Unknown RETRIEVAL_MODE '{mode}'. Use one of: {', '.join(MODES)}.")
    n = config["RETRIEVAL_CANDIDATES"]

    rankings = []
    if mode in ("hybrid", "vector"):
        rankings.append(vector_store.vector_search(query, [d.id for d in documents], n))
    if mode in ("hybrid", "keyword"):
        rankings.append(keyword_search(query, documents, n))
    fused = rrf_fuse(rankings, config["RRF_K"])
    return diversify(fused, config["RETRIEVAL_K"], config["MAX_CHUNKS_PER_PAGE"])
