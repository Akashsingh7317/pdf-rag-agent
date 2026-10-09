"""Factories for the chat model and the embedding model.

Everything else goes through get_chat_model() / get_embeddings(), so the
provider is chosen in one place (LLM_PROVIDER, EMBEDDING_PROVIDER) and tests
can swap in fakes.

  Chat:       openai (paid) | groq (free tier, OpenAI-compatible, very fast)
  Vision:     same provider as chat; reads photos and scanned PDF pages
  Speech:     same provider as chat; Whisper turns voice questions into text
  Embeddings: openai (paid) | local (all-MiniLM-L6-v2 via ONNX on the CPU)

Groq has no embeddings API, so the free setup is groq + local.
"""

import functools
import os

import groq
import openai
from flask import current_app
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

LOCAL_EMBEDDING_MODEL = "all-MiniLM-L6-v2"

_AUTH_ERRORS = (openai.AuthenticationError, groq.AuthenticationError)
_RATE_LIMIT_ERRORS = (openai.RateLimitError, groq.RateLimitError)
_CONNECTION_ERRORS = (openai.APIConnectionError, groq.APIConnectionError)


class LLMConfigError(RuntimeError):
    pass


def _require_key(env_name: str) -> None:
    value = os.environ.get(env_name, "").strip()
    if not value or "your-key" in value:
        raise LLMConfigError(f"{env_name} is not set. Add it to your .env file and restart the server.")


def _provider(setting: str, allowed: tuple[str, ...]) -> str:
    value = current_app.config[setting]
    if value not in allowed:
        raise LLMConfigError(f"Unknown {setting} '{value}'. Use one of: {', '.join(allowed)}.")
    return value


def describe_error(exc: Exception) -> str | None:
    """A short user-facing message for known LLM failures, else None."""
    if isinstance(exc, LLMConfigError):
        return str(exc)
    name = "Groq" if type(exc).__module__.startswith("groq") else "OpenAI"
    if isinstance(exc, _AUTH_ERRORS):
        return f"{name} rejected the API key. Check {name.upper()}_API_KEY in your .env file."
    if isinstance(exc, _RATE_LIMIT_ERRORS):
        return f"{name} rate limit reached. Wait a minute and try again."
    if isinstance(exc, _CONNECTION_ERRORS):
        return f"Could not reach {name}. Check the server's internet connection."
    return None


# --- Embeddings ------------------------------------------------------------------

class LocalEmbeddings(Embeddings):
    """all-MiniLM-L6-v2 (384 dimensions) running on the CPU through ONNX Runtime.
    The model ships with chromadb: free, no API key, offline after a one-time
    ~80 MB download. It reads up to 256 tokens per chunk, so keep CHUNK_SIZE
    around 1000 characters."""

    def __init__(self):
        from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

        self._model = ONNXMiniLM_L6_V2()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._model(texts)]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


@functools.cache
def _local_embeddings() -> LocalEmbeddings:
    return LocalEmbeddings()  # load the model once per process


def embedding_model_name() -> str:
    """Stored on each vector collection; vectors from different models can't be mixed."""
    if _provider("EMBEDDING_PROVIDER", ("openai", "local")) == "local":
        return f"local/{LOCAL_EMBEDDING_MODEL}"
    return f"openai/{current_app.config['OPENAI_EMBEDDING_MODEL']}"


def get_embeddings() -> Embeddings:
    if _provider("EMBEDDING_PROVIDER", ("openai", "local")) == "local":
        return _local_embeddings()
    _require_key("OPENAI_API_KEY")
    return OpenAIEmbeddings(
        model=current_app.config["OPENAI_EMBEDDING_MODEL"],
        max_retries=6,  # built-in exponential backoff for rate limits on big PDFs
    )


# --- Chat model --------------------------------------------------------------------

def _groq_reasoning_options(model: str) -> dict:
    # Reasoning models: keep their thinking out of the streamed answer.
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low"}
    if "qwen3" in model:
        return {"reasoning_format": "hidden"}
    return {}


def get_chat_model(streaming: bool = True) -> BaseChatModel:
    if _provider("LLM_PROVIDER", ("openai", "groq")) == "groq":
        _require_key("GROQ_API_KEY")
        model = current_app.config["GROQ_CHAT_MODEL"]
        return ChatGroq(
            model=model,
            temperature=0,
            streaming=streaming,
            max_retries=3,
            **_groq_reasoning_options(model),
        )
    _require_key("OPENAI_API_KEY")
    return ChatOpenAI(
        model=current_app.config["OPENAI_CHAT_MODEL"],
        temperature=0,
        streaming=streaming,
        max_retries=3,
    )


# Whisper uses the prompt as preceding context: this nudges Hinglish speech
# towards Roman letters, matching how users type their questions.
TRANSCRIBE_HINT = (
    "Questions about documents in English or Hinglish (Hindi in Roman letters), for example: "
    "total kitne pages hain? payment kab tak karna hai? What is the warranty period?"
)


def transcribe_audio(data: bytes, filename: str) -> str:
    """Speech to text for voice questions (Whisper on Groq or OpenAI)."""
    if _provider("LLM_PROVIDER", ("openai", "groq")) == "groq":
        _require_key("GROQ_API_KEY")
        client = groq.Groq(max_retries=2)
        model = current_app.config["GROQ_TRANSCRIBE_MODEL"]
    else:
        _require_key("OPENAI_API_KEY")
        client = openai.OpenAI(max_retries=2)
        model = current_app.config["OPENAI_TRANSCRIBE_MODEL"]
    result = client.audio.transcriptions.create(
        model=model,
        file=(filename, data),
        prompt=TRANSCRIBE_HINT,
        response_format="json",
        temperature=0.0,
    )
    return (result.text or "").strip()


def get_vision_model() -> BaseChatModel:
    """A model that accepts images, used once per uploaded photo to read it."""
    if _provider("LLM_PROVIDER", ("openai", "groq")) == "groq":
        _require_key("GROQ_API_KEY")
        model = current_app.config["GROQ_VISION_MODEL"]
        return ChatGroq(model=model, temperature=0, max_retries=3, **_groq_reasoning_options(model))
    _require_key("OPENAI_API_KEY")
    return ChatOpenAI(model=current_app.config["OPENAI_VISION_MODEL"], temperature=0, max_retries=3)
