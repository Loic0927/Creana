"""In-memory image preparation for AI Post Agent vision requests."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from io import BytesIO
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError
from django.utils.translation import gettext as _


MAX_AGENT_IMAGES = 4
MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_DIMENSION = 1600
JPEG_QUALITY = 82
SUPPORTED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})


class AgentImageError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.safe_message = message


@dataclass(frozen=True)
class PreparedAgentImage:
    data: bytes
    content_type: str = "image/jpeg"


def _read_upload(uploaded_file):
    raw_name = str(getattr(uploaded_file, "name", "") or "").replace("\\", "/")
    safe_name = "".join(character for character in raw_name.rsplit("/", 1)[-1] if character.isprintable())[:100]
    too_large_message = (
        _("%(filename)s is larger than the 10 MB AI analysis limit.") % {"filename": safe_name}
        if safe_name
        else _("This image is larger than the 10 MB AI analysis limit. Choose a smaller image or compress it before trying again.")
    )
    declared_size = getattr(uploaded_file, "size", None)
    if declared_size is not None and declared_size > MAX_IMAGE_UPLOAD_BYTES:
        raise AgentImageError("image_too_large", too_large_message)
    try:
        uploaded_file.seek(0)
        data = uploaded_file.read(MAX_IMAGE_UPLOAD_BYTES + 1)
        uploaded_file.seek(0)
    except (AttributeError, OSError, ValueError) as exc:
        raise AgentImageError("invalid_image", "The image could not be read.") from exc
    if not data:
        raise AgentImageError("invalid_image", "The image is empty or invalid.")
    if len(data) > MAX_IMAGE_UPLOAD_BYTES:
        raise AgentImageError("image_too_large", too_large_message)
    return data


def validate_agent_image(uploaded_file):
    data = _read_upload(uploaded_file)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in SUPPORTED_FORMATS:
                    raise AgentImageError(
                        "unsupported_image_type",
                        "Upload a JPEG, PNG, or WebP image.",
                    )
                if image.width * image.height > MAX_IMAGE_PIXELS:
                    raise AgentImageError("image_too_large", "The image dimensions are too large.")
                image.verify()
    except AgentImageError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise AgentImageError("image_too_large", "The image dimensions are too large.")
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise AgentImageError("invalid_image", "The uploaded file is not a valid image.") from exc
    return data


def prepare_agent_image(uploaded_file):
    data = validate_agent_image(uploaded_file)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                image = ImageOps.exif_transpose(source)
                image.load()
                if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                    rgba = image.convert("RGBA")
                    background = Image.new("RGBA", rgba.size, "white")
                    image = Image.alpha_composite(background, rgba).convert("RGB")
                else:
                    image = image.convert("RGB")
                image.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION), Image.Resampling.LANCZOS)
                output = BytesIO()
                image.save(
                    output,
                    format="JPEG",
                    quality=JPEG_QUALITY,
                    optimize=True,
                    exif=b"",
                )
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise AgentImageError("image_too_large", "The image dimensions are too large.")
    except (OSError, ValueError) as exc:
        raise AgentImageError("image_processing_failed", "The image could not be prepared for analysis.") from exc
    return PreparedAgentImage(output.getvalue())


def encode_agent_image_data_url(prepared_image):
    encoded = base64.b64encode(prepared_image.data).decode("ascii")
    return f"data:{prepared_image.content_type};base64,{encoded}"


def load_existing_post_images(post):
    """Read existing post images through Django storage, never through public URLs."""
    fields = []
    seen_names = set()
    for post_image in post.images.order_by("order", "created_at", "pk"):
        if post_image.image and post_image.image.name not in seen_names:
            fields.append(post_image.image)
            seen_names.add(post_image.image.name)
    if post.image and post.image.name not in seen_names:
        fields.append(post.image)
    prepared = []
    for field in fields[:MAX_AGENT_IMAGES]:
        try:
            with field.open("rb") as stored_file:
                prepared.append(prepare_agent_image(stored_file))
        except AgentImageError as exc:
            if exc.code == "image_too_large":
                raise
            continue
        except (OSError, ValueError):
            continue
    return prepared
