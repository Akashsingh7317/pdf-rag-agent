"""Question answering over one document, or across all of a user's documents.

1. Rewrite a follow-up ("what about the second one?") into a standalone
   question using the recent chat history, so retrieval has real keywords.
2. Retrieve the most relevant chunks with hybrid search (see retrieval.py).
3. Stream an answer that uses only those chunks and cites where each fact
   came from: (p. 12) for one document, (report.pdf, p. 12) across documents.
"""

from typing import Iterator

from langchain_core.documents import Document as Chunk
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.models import Document
from app.services import llm

CONDENSE_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Rewrite the user's latest question as a standalone question that can be "
            "understood without the conversation. Keep names, numbers and terms from the "
            "conversation that the question refers to. If it is already standalone, return "
            "it unchanged. Reply with the question only.",
        ),
        ("human", "Conversation:\n{history}\n\nLatest question: {question}"),
    ]
)

ANSWER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "{scope}\n"
            "Rules:\n"
            "- Use ONLY the excerpts and the information above. Questions about the documents "
            "themselves (names, types, number of pages) are answered from that information.\n"
            "- If the excerpts don't contain the answer, say {not_found}, and mention related "
            "topics the excerpts do cover, if any. Never guess or use outside knowledge.\n"
            "- The user never sees the excerpts, so never mention \"excerpts\"; refer to "
            "{source_name}.\n"
            "- {citation_rule} Don't add a separate sources list at the end; the app shows one.\n"
            "- You can only answer questions about the documents. If asked to edit, create or "
            "change a file (e.g. remove something from a photo), say in one sentence that this "
            "app answers questions about files and can't edit them, then suggest a question you "
            "can answer.\n"
            "- Be clear and concise. Use short paragraphs, bullet points, or a table for comparisons.\n\n"
            "Excerpts:\n{context}\n\n"
            "Language: {language_rule}",
        ),
        MessagesPlaceholder("history"),
        ("human", "{question}"),
    ]
)

SNIPPET_CHARS = 320
HISTORY_ANSWER_CHARS = 600  # trim long past answers to keep prompts small
MAX_LISTED_DOCUMENTS = 40

# Some models write non-breaking hyphens and spaces ("1800‑123‑4567",
# "2 years"). They look the same but break copy-paste, search and dialling.
PLAIN_TYPOGRAPHY = str.maketrans({"‐": "-", "‑": "-", " ": " ", " ": " ", " ": " ", " ": " "})


def _history_messages(history: list[dict]) -> list[BaseMessage]:
    messages = []
    for item in history:
        text = item["content"][:HISTORY_ANSWER_CHARS]
        messages.append(HumanMessage(text) if item["role"] == "user" else AIMessage(text))
    return messages


def condense_question(question: str, history: list[dict]) -> str:
    if not history:
        return question
    transcript = "\n".join(
        f"{item['role'].title()}: {item['content'][:HISTORY_ANSWER_CHARS]}" for item in history
    )
    chain = CONDENSE_PROMPT | llm.get_chat_model(streaming=False) | StrOutputParser()
    standalone = chain.invoke({"history": transcript, "question": question}).strip()
    return standalone or question


def build_context(chunks: list[Chunk], filenames: dict[str, str] | None = None) -> str:
    """Excerpts with a header the model cites from. With `filenames`
    (all-documents chat) the header also names the file."""

    def header(i: int, chunk: Chunk) -> str:
        page = chunk.metadata.get("page", "?")
        if filenames:
            return f"[Excerpt {i} | {filenames.get(chunk.metadata.get('doc_id'), '?')} | Page {page}]"
        return f"[Excerpt {i} | Page {page}]"

    return "\n\n---\n\n".join(
        f"{header(i, chunk)}\n{chunk.page_content}" for i, chunk in enumerate(chunks, start=1)
    )


def to_sources(chunks: list[Chunk], documents: list[Document]) -> list[dict]:
    """Sources shown under the answer: one per (document, page)."""
    by_id = {doc.id: doc for doc in documents}
    found: dict[tuple[str, int], dict] = {}
    for chunk in chunks:
        doc = by_id.get(chunk.metadata.get("doc_id"))
        page = chunk.metadata.get("page")
        if doc is None or page is None or (doc.id, page) in found:
            continue
        snippet = " ".join(chunk.page_content.split())
        if len(snippet) > SNIPPET_CHARS:
            snippet = snippet[:SNIPPET_CHARS].rsplit(" ", 1)[0] + "…"
        found[(doc.id, page)] = {
            "doc_id": doc.id,
            "filename": doc.filename,
            "kind": doc.kind,
            "page": page,
            "snippet": snippet,
        }
    return sorted(found.values(), key=lambda s: (s["filename"].lower(), s["page"]))


def describe_document(kind: str, page_count: int | None) -> tuple[str, str]:
    """(facts about the document, how to cite) for the single-document prompt."""
    pages = page_count or 0
    if kind == "image":
        return (
            "an image. The excerpt is a transcription of its text plus a description of it.",
            "Don't cite page numbers; the document is a single image.",
        )
    cite = "Cite the page after each fact, like (p. 12), using the page numbers in the excerpt headers."
    if kind == "pdf":
        return f"a PDF with {pages} pages in total.", cite
    label = "a Word document" if kind == "docx" else "a text file"
    return (
        f"{label}, split into {pages} sections of about one page each "
        "(page numbers refer to these sections).",
        cite,
    )


def language_rule(question: str) -> str:
    """Pin the answer's script to the question's. Models often answer Hinglish
    written in Roman letters ("kitne pages hai?") in Devanagari; deciding the
    script in code is more reliable than asking the model to notice."""
    if any("ऀ" <= ch <= "ॿ" for ch in question):
        return "the question is in Devanagari script, so write the answer in Devanagari."
    return (
        "the question is written in Roman (Latin) letters, so write the answer ONLY in Roman "
        "letters, in the same language as the question: English stays English, and Hinglish "
        '(e.g. "payment kab tak karna hai?") is answered in Hinglish with Roman letters '
        '(e.g. "Payment 15 Oct 2026 tak karna hai."). Never use Devanagari.'
    )


def _prompt_scope(documents: list[Document], across_all: bool) -> dict:
    if not across_all:
        doc = documents[0]
        info, citation = describe_document(doc.kind, doc.page_count)
        return {
            "scope": (
                f'You answer questions about the document "{doc.filename}" using only the '
                f"excerpts below.\nAbout the document: {info}"
            ),
            "not_found": "this document doesn't seem to cover it",
            "source_name": '"the document"',
            "citation_rule": citation,
        }
    listed = "\n".join(
        f"- {doc.filename}: {describe_document(doc.kind, doc.page_count)[0]}"
        for doc in documents[:MAX_LISTED_DOCUMENTS]
    )
    return {
        "scope": (
            f"You answer questions across the user's {len(documents)} documents using only the "
            f"excerpts below. Their documents are:\n{listed}"
        ),
        "not_found": "their documents don't seem to cover it",
        "source_name": "the documents by their file names",
        "citation_rule": (
            "Cite the file and page after each fact, like (report.pdf, p. 12), using the excerpt "
            "headers. For a photo cite only the file name, like (receipt.jpg)."
        ),
    }


def stream_answer(
    documents: list[Document],
    question: str,
    history: list[dict],
    chunks: list[Chunk],
    across_all: bool = False,
) -> Iterator[str]:
    filenames = {doc.id: doc.filename for doc in documents} if across_all else None
    chain = ANSWER_PROMPT | llm.get_chat_model(streaming=True) | StrOutputParser()
    tokens = chain.stream(
        {
            **_prompt_scope(documents, across_all),
            "language_rule": language_rule(question),
            "context": build_context(chunks, filenames),
            "history": _history_messages(history),
            "question": question,
        }
    )
    for token in tokens:
        yield token.translate(PLAIN_TYPOGRAPHY)
