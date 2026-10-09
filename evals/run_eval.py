"""Measure retrieval quality: vector vs keyword (BM25) vs hybrid search.

    python -m evals.run_eval              # retrieval metrics (free, offline with local embeddings)
    python -m evals.run_eval --answers    # also score full answers with the configured LLM

The sample manual (evals/sample_manual.py) is indexed with the app's real
pipeline and configuration, then every question in evals/dataset.json is
searched in each mode. Metrics, over the chunks that would be sent to the LLM:

    Hit@1  the top chunk comes from a page that contains the answer
    Hit@5  any of the retrieved chunks does (the LLM gets to see the answer)
    MRR    mean of 1 / rank of the first correct chunk (0 if none)

Results are printed and written to evals/RESULTS.md.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Document, DocumentStatus, User  # noqa: E402
from app.services import ingestion, llm, rag, retrieval  # noqa: E402
from evals.pdf_writer import make_pdf  # noqa: E402
from evals.sample_manual import PAGES  # noqa: E402

MODES = ("vector", "keyword", "hybrid")


def build_app(workdir: str):
    return create_app(
        {
            "SECRET_KEY": "evaluation-only-secret-key-with-enough-length",
            "DATA_DIR": workdir,
            "SQLALCHEMY_DATABASE_URI": "sqlite:///" + os.path.join(workdir, "eval.db"),
            "UPLOAD_DIR": os.path.join(workdir, "uploads"),
            "CHROMA_DIR": os.path.join(workdir, "chroma"),
            "CHROMA_HOST": None,
            "INGESTION_BACKEND": "sync",
            "SUMMARY_ENABLED": False,
            "OCR_ENABLED": False,
            "LOG_LEVEL": "WARNING",
        }
    )


def index_manual(workdir: str) -> Document:
    path = os.path.join(workdir, "uploads", "x200-manual.pdf")
    with open(path, "wb") as file:
        file.write(make_pdf(PAGES))
    user = User(email="eval@example.com", name="Eval")
    user.set_password("evaluation-password")
    db.session.add(user)
    db.session.flush()
    doc = Document(
        user_id=user.id, filename="x200-manual.pdf", file_path=path, file_hash="e" * 64,
        size_bytes=os.path.getsize(path), status=DocumentStatus.QUEUED, progress=0,
    )
    db.session.add(doc)
    db.session.commit()
    ingestion.ingest_document(doc.id)
    db.session.refresh(doc)
    if doc.status != DocumentStatus.READY:
        raise SystemExit(f"Indexing failed: {doc.error}")
    return doc


def score(retrieved_pages: list[int], expected: list[int]) -> tuple[int, int, float]:
    ranks = [i for i, page in enumerate(retrieved_pages, start=1) if page in expected]
    first = ranks[0] if ranks else None
    return int(first == 1), int(first is not None), (1 / first if first else 0.0)


def evaluate_retrieval(doc: Document, questions: list[dict]) -> dict:
    results = {}
    for mode in MODES:
        rows = []
        for item in questions:
            pages = [c.metadata["page"] for c in retrieval.search(item["q"], [doc], mode=mode)]
            hit1, hit5, rr = score(pages, item["pages"])
            rows.append({**item, "retrieved": pages, "hit1": hit1, "hit5": hit5, "rr": rr})
        results[mode] = rows
    return results


def normalise(text: str) -> str:
    """Compare text, not typography: markdown bold, unicode dashes and spaces."""
    text = unicodedata.normalize("NFKC", text).replace("**", "")
    text = re.sub(r"[‐-―−]", "-", text)
    return re.sub(r"\s+", " ", text).lower()


def evaluate_answers(doc: Document, questions: list[dict]) -> list[dict]:
    rows = []
    for item in questions:
        chunks = retrieval.search(item["q"], [doc], mode="hybrid")
        answer = "".join(rag.stream_answer([doc], item["q"], [], chunks))
        text = normalise(answer)
        # every keyword must appear; "a|b" accepts either phrasing
        found = all(any(normalise(alt) in text for alt in k.split("|")) for k in item["keywords"])
        rows.append({**item, "answer": answer, "correct": int(found)})
        time.sleep(1.5)  # stay inside free-tier rate limits
    return rows


def summarise(rows: list[dict], kind: str | None = None) -> dict:
    rows = [r for r in rows if kind is None or r["type"] == kind]
    n = len(rows) or 1
    return {
        "n": len(rows),
        "hit1": sum(r["hit1"] for r in rows) / n,
        "hit5": sum(r["hit5"] for r in rows) / n,
        "mrr": sum(r["rr"] for r in rows) / n,
    }


def pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def report(results: dict, answers: list[dict] | None, config: dict) -> str:
    lines = [
        "# Retrieval evaluation",
        "",
        f"Run on {date.today().isoformat()} with `python -m evals.run_eval`. Document: a 12-page "
        f"printer manual ({len(results['hybrid'])} questions: "
        f"{summarise(results['hybrid'], 'meaning')['n']} paraphrased *meaning* questions, "
        f"{summarise(results['hybrid'], 'exact')['n']} *exact* questions about codes, numbers and names).",
        "",
        f"Embeddings: `{config['embedding']}` · chunk size {config['chunk_size']} · "
        f"top {config['k']} chunks sent to the LLM.",
        "",
        "| Search | Hit@1 | Hit@5 | MRR | Hit@5 (meaning) | Hit@5 (exact) |",
        "|---|---|---|---|---|---|",
    ]
    for mode in MODES:
        overall = summarise(results[mode])
        meaning, exact = summarise(results[mode], "meaning"), summarise(results[mode], "exact")
        lines.append(
            f"| {mode} | {pct(overall['hit1'])} | {pct(overall['hit5'])} | {overall['mrr']:.2f} | "
            f"{pct(meaning['hit5'])} | {pct(exact['hit5'])} |"
        )
    misses = [r for r in results["hybrid"] if not r["hit5"]]
    lines += ["", "Hybrid misses (answer page not in the retrieved chunks):" if misses else "Hybrid found the answer page for every question."]
    lines += [f"- {r['q']} (expected p. {r['pages']}, got {r['retrieved']})" for r in misses]
    if answers:
        correct = sum(r["correct"] for r in answers)
        lines += [
            "",
            f"## Answers (hybrid search + `{config['llm']}`)",
            "",
            f"{correct}/{len(answers)} answers ({pct(correct / len(answers))}) contain every expected keyword.",
        ]
        lines += [
            f"- Missed: {r['q']} (expected {r['keywords']}); answer: \"{' '.join(r['answer'].split())[:200]}\""
            for r in answers if not r["correct"]
        ]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--answers", action="store_true", help="also generate and score answers (uses the LLM)")
    args = parser.parse_args()

    with open(os.path.join(HERE, "dataset.json"), encoding="utf-8") as file:
        questions = json.load(file)["questions"]

    # Chroma keeps its files open until the process exits (Windows locks them).
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as workdir:
        os.makedirs(os.path.join(workdir, "uploads"), exist_ok=True)
        app = build_app(workdir)
        with app.app_context():
            config = {
                "embedding": llm.embedding_model_name(),
                "chunk_size": app.config["CHUNK_SIZE"],
                "k": app.config["RETRIEVAL_K"],
                "llm": app.config["GROQ_CHAT_MODEL"] if app.config["LLM_PROVIDER"] == "groq" else app.config["OPENAI_CHAT_MODEL"],
            }
            print(f"Indexing the sample manual with {config['embedding']}…")
            doc = index_manual(workdir)
            results = evaluate_retrieval(doc, questions)
            answers = evaluate_answers(doc, questions) if args.answers else None
            text = report(results, answers, config)
            db.session.remove()
            db.engine.dispose()
            app.extensions.pop("chroma_client", None)

    with open(os.path.join(HERE, "RESULTS.md"), "w", encoding="utf-8") as file:
        file.write(text)
    print(text)


if __name__ == "__main__":
    main()
