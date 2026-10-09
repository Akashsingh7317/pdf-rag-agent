"""Voice questions: recorded audio -> Whisper -> text."""

import io

from app.services import llm


def send(client, data: bytes, name: str = "question.webm"):
    return client.post(
        "/api/transcribe",
        data={"audio": (io.BytesIO(data), name)},
        content_type="multipart/form-data",
    )


def test_recording_is_transcribed(client, monkeypatch):
    received = {}

    def fake_transcribe(data, filename):
        received.update(size=len(data), filename=filename)
        return "total kitne pages hain?"

    monkeypatch.setattr(llm, "transcribe_audio", fake_transcribe)
    audio = b"\x1aE\xdf\xa3 fake webm audio"
    res = send(client, audio)
    assert res.status_code == 200
    assert res.get_json() == {"text": "total kitne pages hain?"}
    assert received == {"size": len(audio), "filename": "question.webm"}


def test_empty_or_missing_recording(client):
    assert client.post("/api/transcribe", data={}, content_type="multipart/form-data").status_code == 400
    assert send(client, b"").status_code == 400


def test_recording_size_limit(app, client, monkeypatch):
    app.config["MAX_AUDIO_MB"] = 1
    monkeypatch.setattr(llm, "transcribe_audio", lambda data, filename: "hi")
    assert send(client, b"x" * (1024 * 1024 + 1)).status_code == 413


def test_silence_and_provider_errors_are_explained(client, monkeypatch):
    monkeypatch.setattr(llm, "transcribe_audio", lambda data, filename: "")
    silent = send(client, b"audio")
    assert silent.status_code == 422
    assert "Couldn't hear any speech" in silent.get_json()["error"]

    def no_key(data, filename):
        raise llm.LLMConfigError("GROQ_API_KEY is not set. Add it to your .env file and restart the server.")

    monkeypatch.setattr(llm, "transcribe_audio", no_key)
    failed = send(client, b"audio")
    assert failed.status_code == 502
    assert "GROQ_API_KEY" in failed.get_json()["error"]
