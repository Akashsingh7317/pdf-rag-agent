"""Hybrid search (vector + BM25 + RRF) and re-indexing after a model change."""

from langchain_core.documents import Document as Chunk
from sqlalchemy import select

from app.extensions import db
from app.models import Document
from app.services import retrieval
from tests.conftest import make_pdf, parse_sse, upload

ERROR_CODES = make_pdf(
    [
        "Chapter 5: Error codes\nE01 means the paper tray is empty. Load paper.",
        "E07 means the toner cartridge is not detected. Reseat the TN-450 cartridge.",
        "E12 means the fuser is overheating. Switch off and wait 30 minutes.",
        "Chapter 6: Cleaning\nWipe the glass with a dry, lint-free cloth.",
    ]
)


def chunk(chunk_id: str, doc: str = "d", page: int = 1) -> Chunk:
    return Chunk(page_content=chunk_id, metadata={"id": chunk_id, "doc_id": doc, "page": page})


def test_rrf_rewards_chunks_found_by_both_methods():
    vector = [chunk("a"), chunk("b"), chunk("c")]
    keyword = [chunk("c"), chunk("d")]
    fused = [c.metadata["id"] for c in retrieval.rrf_fuse([vector, keyword], k=60)]
    assert fused[0] == "c"  # ranked by both
    assert set(fused) == {"a", "b", "c", "d"}


def test_diversify_limits_chunks_per_page():
    ranked = [chunk("a", page=1), chunk("b", page=1), chunk("c", page=1), chunk("d", page=2)]
    picked = [c.metadata["id"] for c in retrieval.diversify(ranked, k=3, per_page=2)]
    assert picked == ["a", "b", "d"]


def test_tokenizer_keeps_codes_and_numbers():
    assert retrieval.tokenize("Error E07: reseat TN-450!") == ["error", "e07", "reseat", "tn", "450"]


def test_only_distinctive_query_words_are_used_for_keyword_search():
    corpus = [["printer", "setup"], ["printer", "weight", "kg"], ["printer", "e07"], ["printer", "toner"]]
    # "printer" is in every chunk and "how/is/the" are stopwords: only "weight" is informative
    assert retrieval.informative_terms(["how", "is", "the", "printer", "weight"], corpus) == ["weight"]
    assert retrieval.informative_terms(["how", "heavy", "is", "the", "printer"], corpus) == []


def test_keyword_search_finds_exact_codes(app, client):
    upload(client, ERROR_CODES, filename="errors.pdf")
    with app.app_context():
        docs = db.session.scalars(select(Document)).all()
        hits = retrieval.keyword_search("What does E07 mean?", docs, n=5)
        assert hits[0].metadata["page"] == 2


def test_hybrid_search_puts_the_exact_match_first(app, client):
    # The fake embeddings are random, so only the keyword side knows where
    # E07 is; fusion must still rank that chunk first.
    upload(client, ERROR_CODES, filename="errors.pdf")
    with app.app_context():
        docs = db.session.scalars(select(Document)).all()
        for mode in ("hybrid", "keyword"):
            results = retrieval.search("What does E07 mean?", docs, mode=mode)
            assert results[0].metadata["page"] == 2, mode
        assert len(retrieval.search("What does E07 mean?", docs, mode="vector")) == 4


def test_changing_the_embedding_model_asks_for_a_reindex(app, client):
    doc_id = upload(client).get_json()["document"]["id"]
    app.config["EMBEDDING_PROVIDER"] = "openai"  # vectors now come from a different model

    doc = client.get(f"/api/documents/{doc_id}").get_json()["document"]
    assert doc["needs_reindex"] is True
    blocked = client.post(f"/api/documents/{doc_id}/chat", json={"question": "warranty?"})
    assert blocked.status_code == 409
    assert "Re-index" in blocked.get_json()["error"]
    assert client.post("/api/chat", json={"question": "warranty?"}).status_code == 409

    assert client.post(f"/api/documents/{doc_id}/retry").status_code == 202
    assert client.get(f"/api/documents/{doc_id}").get_json()["document"]["needs_reindex"] is False
    events = parse_sse(client.post(f"/api/documents/{doc_id}/chat", json={"question": "warranty?"}).get_data())
    assert events[-1][0] == "done"


def test_ready_documents_cannot_be_reprocessed_without_reason(client):
    doc_id = upload(client).get_json()["document"]["id"]
    assert client.post(f"/api/documents/{doc_id}/retry").status_code == 409
