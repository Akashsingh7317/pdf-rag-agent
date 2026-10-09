"""Chroma vector store.

All chunks live in one collection per embedding model. Each chunk carries
its doc_id in metadata, and every search is filtered to the documents the
caller may see: one document, or all of a user's ready documents. Ownership
itself is checked in SQL; Chroma only ever receives explicit document ids.
"""

import logging
import re
import threading

import chromadb
from chromadb.config import Settings
from flask import current_app
from langchain_core.documents import Document as Chunk

from app.services import llm

logger = logging.getLogger(__name__)

_client_lock = threading.Lock()
COLLECTION_PREFIX = "chunks-"
LEGACY_PREFIX = "doc_"  # old layout: one collection per document


def _client() -> chromadb.ClientAPI:
    """One Chroma client per app, created lazily and shared across threads."""
    app = current_app._get_current_object()
    client = app.extensions.get("chroma_client")
    if client is None:
        with _client_lock:
            client = app.extensions.get("chroma_client")
            if client is None:
                settings = Settings(anonymized_telemetry=False)
                host = app.config["CHROMA_HOST"]
                if host:
                    client = chromadb.HttpClient(
                        host=host, port=app.config["CHROMA_PORT"], settings=settings
                    )
                else:
                    client = chromadb.PersistentClient(
                        path=app.config["CHROMA_DIR"], settings=settings
                    )
                app.extensions["chroma_client"] = client
    return client


def collection_name_for(embedding_model: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", embedding_model.lower()).strip("-")
    return (COLLECTION_PREFIX + slug)[:60]


def _collection(embedding_model: str | None = None):
    model = embedding_model or llm.embedding_model_name()
    # We always pass our own embeddings, so Chroma's embedding function is off.
    return _client().get_or_create_collection(
        collection_name_for(model), metadata={"embedding_model": model}, embedding_function=None
    )


def _where(doc_ids: list[str]) -> dict:
    return {"doc_id": doc_ids[0]} if len(doc_ids) == 1 else {"doc_id": {"$in": doc_ids}}


def _to_chunks(ids, texts, metadatas) -> list[Chunk]:
    return [
        Chunk(page_content=text, metadata={**meta, "id": chunk_id})
        for chunk_id, text, meta in zip(ids, texts, metadatas)
    ]


def add_chunks(doc_id: str, chunks: list[Chunk], vectors: list[list[float]]) -> None:
    _collection().upsert(  # upsert: re-running an ingestion never duplicates chunks
        ids=[f"{doc_id}-{c.metadata['chunk']}" for c in chunks],
        embeddings=vectors,
        documents=[c.page_content for c in chunks],
        metadatas=[
            {"doc_id": doc_id, "page": c.metadata["page"], "chunk": c.metadata["chunk"]}
            for c in chunks
        ],
    )


def vector_search(query: str, doc_ids: list[str], n: int) -> list[Chunk]:
    """Nearest chunks by embedding similarity, best first."""
    if not doc_ids:
        return []
    vector = llm.get_embeddings().embed_query(query)
    result = _collection().query(
        query_embeddings=[vector],
        n_results=n,
        where=_where(doc_ids),
        include=["documents", "metadatas"],
    )
    return _to_chunks(result["ids"][0], result["documents"][0], result["metadatas"][0])


def get_chunks(doc_ids: list[str]) -> list[Chunk]:
    """Every chunk of the given documents, in reading order."""
    if not doc_ids:
        return []
    result = _collection().get(where=_where(doc_ids), include=["documents", "metadatas"])
    chunks = _to_chunks(result["ids"], result["documents"], result["metadatas"])
    return sorted(chunks, key=lambda c: (c.metadata["doc_id"], c.metadata["chunk"]))


def delete_document(doc_id: str) -> None:
    """Remove a document's chunks from every embedding model's collection."""
    for collection in _client().list_collections():
        if collection.name.startswith(COLLECTION_PREFIX):
            try:
                _client().get_collection(collection.name, embedding_function=None).delete(
                    where={"doc_id": doc_id}
                )
            except Exception:  # noqa: BLE001
                logger.warning("Could not delete chunks of %s from %s", doc_id, collection.name)


def migrate_legacy_collections() -> dict[str, str]:
    """Move vectors from the old one-collection-per-document layout ("doc_<id>")
    into the shared collection of their embedding model, without re-embedding.
    Returns {doc_id: embedding_model} for every document moved."""
    moved = {}
    client = _client()
    for info in client.list_collections():
        if not info.name.startswith(LEGACY_PREFIX):
            continue
        old = client.get_collection(info.name, embedding_function=None)
        model = (old.metadata or {}).get("embedding_model")
        if not model:
            continue  # unknown model: the document will ask for a re-index
        doc_id = info.name[len(LEGACY_PREFIX):]
        data = old.get(include=["embeddings", "documents", "metadatas"])
        if len(data["ids"]):
            _collection(model).upsert(
                ids=list(data["ids"]),
                embeddings=[list(map(float, vector)) for vector in data["embeddings"]],
                documents=list(data["documents"]),
                metadatas=[
                    {"doc_id": doc_id, "page": meta.get("page", 1), "chunk": meta.get("chunk", i)}
                    for i, meta in enumerate(data["metadatas"])
                ],
            )
        client.delete_collection(info.name)
        moved[doc_id] = model
        logger.info("Moved %d vectors of document %s to the shared collection", len(data["ids"]), doc_id)
    return moved
