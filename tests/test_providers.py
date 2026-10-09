"""Provider selection in app/services/llm.py. No network calls: the models
are only constructed, never invoked."""

import pytest
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from app import create_app
from app.services import llm


@pytest.fixture
def make_app(tmp_path):
    def factory(**overrides):
        return create_app(
            {
                "TESTING": True,
                "SECRET_KEY": "test-secret-key-that-is-long-enough-for-hs256",
                "DATA_DIR": str(tmp_path),
                "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}",
                "UPLOAD_DIR": str(tmp_path / "uploads"),
                "CHROMA_DIR": str(tmp_path / "chroma"),
                "INGESTION_BACKEND": "sync",
                "RATELIMIT_ENABLED": False,
                **overrides,
            }
        )

    return factory


def test_groq_chat_model(make_app, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    app = make_app(LLM_PROVIDER="groq", GROQ_CHAT_MODEL="openai/gpt-oss-120b")
    with app.app_context():
        model = llm.get_chat_model()
    assert isinstance(model, ChatGroq)
    assert model.model_name == "openai/gpt-oss-120b"
    assert model.reasoning_effort == "low"


def test_groq_vision_model(make_app, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    app = make_app(LLM_PROVIDER="groq", GROQ_VISION_MODEL="qwen/qwen3.8-27b")
    with app.app_context():
        model = llm.get_vision_model()
    assert isinstance(model, ChatGroq)
    assert model.model_name == "qwen/qwen3.8-27b"
    assert model.reasoning_format == "hidden"


def test_openai_chat_model(make_app, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    app = make_app(LLM_PROVIDER="openai", OPENAI_CHAT_MODEL="gpt-4o-mini")
    with app.app_context():
        assert isinstance(llm.get_chat_model(), ChatOpenAI)


@pytest.mark.parametrize("value", ["", "gsk-your-key-here"])
def test_missing_or_placeholder_key_is_reported(make_app, monkeypatch, value):
    monkeypatch.setenv("GROQ_API_KEY", value)
    app = make_app(LLM_PROVIDER="groq")
    with app.app_context(), pytest.raises(llm.LLMConfigError, match="GROQ_API_KEY is not set"):
        llm.get_chat_model()


def test_unknown_provider_is_reported(make_app):
    app = make_app(LLM_PROVIDER="gemini")
    with app.app_context(), pytest.raises(llm.LLMConfigError, match="Unknown LLM_PROVIDER"):
        llm.get_chat_model()


def test_local_embeddings_need_no_key(make_app, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    app = make_app(EMBEDDING_PROVIDER="local")
    with app.app_context():
        assert isinstance(llm.get_embeddings(), llm.LocalEmbeddings)
        assert llm.embedding_model_name() == "local/all-MiniLM-L6-v2"

