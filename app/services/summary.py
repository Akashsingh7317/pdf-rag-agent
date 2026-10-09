"""A short overview of each document plus questions it can answer, shown
when the user opens it. Generated once, right after indexing.

Summaries are a convenience: any failure here is logged and ignored, it
never fails the ingestion of the document itself."""

import logging

from flask import current_app
from langchain_core.documents import Document as Chunk
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.services import llm

logger = logging.getLogger(__name__)

SAMPLE_CHARS = 9000  # enough to understand a document, small enough to be quick and cheap

OVERVIEW_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You write short overviews of documents. Return JSON only, with no markdown fences, "
            "using exactly these keys:\n"
            '"summary": 2 to 4 plain sentences on what the document is and what it covers.\n'
            '"questions": a list of 4 short, specific questions (under 12 words each) that this '
            "document can answer.\n"
            "Write in the document's own language.",
        ),
        ("human", "File name: {filename}\n\nText from the document:\n{text}"),
    ]
)


def sample_text(chunks: list[Chunk], limit: int = SAMPLE_CHARS) -> str:
    """The opening chunks plus evenly spaced ones, so a long book is
    represented by more than its table of contents."""
    if not chunks:
        return ""
    step = max(1, len(chunks) // 12)
    indices = list(range(min(3, len(chunks)))) + list(range(3, len(chunks), step))
    parts, total = [], 0
    for chunk in (chunks[i] for i in dict.fromkeys(indices)):
        if total + len(chunk.page_content) > limit:
            break
        parts.append(chunk.page_content)
        total += len(chunk.page_content)
    return "\n\n---\n\n".join(parts) or chunks[0].page_content[:limit]


def generate_overview(filename: str, chunks: list[Chunk]) -> dict | None:
    """{"summary": str, "questions": [str]} or None."""
    if not current_app.config["SUMMARY_ENABLED"] or not chunks:
        return None
    try:
        chain = OVERVIEW_PROMPT | llm.get_chat_model(streaming=False) | JsonOutputParser()
        data = chain.invoke({"filename": filename, "text": sample_text(chunks)})
        summary = str(data.get("summary") or "").strip()
        questions = [str(q).strip() for q in (data.get("questions") or []) if str(q).strip()]
        if not summary:
            return None
        return {"summary": summary[:1500], "questions": questions[:5]}
    except Exception as exc:  # noqa: BLE001 - optional feature
        logger.warning("Could not summarise %s: %s", filename, llm.describe_error(exc) or exc)
        return None
