"""Read images with a vision model.

- Photos: an exact transcription of any text plus a detailed description,
  after which a photo is indexed and chatted with like a one-page document.
- Scanned PDF pages (no text layer): an exact transcription (OCR).
"""

import base64
import io

from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import StrOutputParser
from PIL import Image, ImageOps

from app.services import llm

MAX_SIDE = 1600  # px; keeps phone photos well under provider upload limits

PHOTO_PROMPT = (
    "You are indexing an uploaded image so that questions about it can be answered later.\n"
    "1. Under the heading 'Text in image:', transcribe ALL readable text exactly, keeping "
    "line breaks. Write tables as markdown tables. Write 'None' if there is no text.\n"
    "2. Under the heading 'Description:', describe the image in detail: what it shows, "
    "objects, people, layout, colours, and every value in any chart, table or diagram.\n"
    "Reply with these two sections only."
)

PAGE_PROMPT = (
    "This is a scanned page of a document. Transcribe ALL of its text exactly, in reading "
    "order, keeping headings, lists and line breaks. Write tables as markdown tables. For a "
    "figure or photo, add one line: [Figure: short description]. Reply with the transcription "
    "only. If the page is blank, reply with nothing."
)


class ImageReadError(Exception):
    pass


def _to_data_url(image: Image.Image) -> str:
    """Downscale, flatten transparency and encode as JPEG."""
    image = image.copy()
    image.thumbnail((MAX_SIDE, MAX_SIDE))
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        image = Image.new("RGB", rgba.size, "white")
        image.paste(rgba, mask=rgba.getchannel("A"))
    elif image.mode != "RGB":
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def image_to_data_url(path: str) -> str:
    try:
        with Image.open(path) as source:
            return _to_data_url(ImageOps.exif_transpose(source))  # fix phone rotation
    except OSError as exc:  # includes PIL's UnidentifiedImageError
        raise ImageReadError("Could not open this image. The file may be damaged.") from exc


def _ask(prompt: str, data_url: str) -> str:
    message = HumanMessage(
        content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
    )
    chain = llm.get_vision_model() | StrOutputParser()
    return chain.invoke([message]).strip()


def describe_image(path: str) -> str:
    return _ask(PHOTO_PROMPT, image_to_data_url(path))


def transcribe_page(image: Image.Image) -> str:
    return _ask(PAGE_PROMPT, _to_data_url(image))
