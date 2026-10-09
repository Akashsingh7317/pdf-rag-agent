"""Voice questions: the browser records audio, this turns it into text with
Whisper, and the text goes into the question box for the user to send."""

import logging

from flask import current_app, jsonify, request

from app.api import api
from app.errors import ApiError
from app.extensions import limiter
from app.services import llm

logger = logging.getLogger(__name__)


@api.post("/transcribe")
@limiter.limit(lambda: current_app.config["TRANSCRIBE_RATE_LIMIT"])
def transcribe():
    audio = request.files.get("audio")
    if audio is None:
        raise ApiError("Attach the recording in a form field named 'audio'.")
    data = audio.read()
    if not data:
        raise ApiError("The recording is empty. Hold the mic a little longer and try again.")
    max_mb = current_app.config["MAX_AUDIO_MB"]
    if len(data) > max_mb * 1024 * 1024:
        raise ApiError(f"The recording is too long (max {max_mb} MB).", 413)

    try:
        text = llm.transcribe_audio(data, audio.filename or "question.webm")
    except Exception as exc:  # noqa: BLE001
        known = llm.describe_error(exc)
        logger.log(logging.ERROR if known else logging.WARNING, "Transcription failed: %s", known or exc)
        raise ApiError(known or "Couldn't turn the recording into text. Please try again.", 502) from exc
    if not text:
        raise ApiError("Couldn't hear any speech. Try again a little closer to the microphone.", 422)
    return jsonify(text=text)
