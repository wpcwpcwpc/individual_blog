"""
QA Agent System — Vision Convert (Image-to-Text)

A one-shot OCR-like service: accepts an image file (multipart), calls the
configured VISION slot multimodal model, and returns the text description.
The caller (frontend FileUploader) wraps the returned text as a ``.txt``
File object and uploads it via the normal session upload flow — same
paradigm as if the user had selected a text file directly.

This endpoint deliberately has NO session coupling: image-to-text is a
stateless transformation, not part of the conversation lifecycle. Sessions
only ever see the resulting ``.txt`` file.

"""

from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path
from typing import Tuple

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vision", tags=["vision"])


# ── Format policy ──────────────────────────────────────────────────────
# Mirror session_uploads policy for image-only conversion. Anything else → 415.
IMAGE_EXTENSIONS: frozenset[str] = frozenset({
    "png", "jpg", "jpeg", "gif", "webp",
})


class ConvertResponse(BaseModel):
    """Response for POST /vision/convert.

    Frontend wraps ``text`` as a new ``File([text], suggested_filename,
    {type: 'text/plain'})`` and stages/uploads it as a normal .txt file.
    """
    text: str
    suggested_filename: str
    source_name: str
    size_bytes: int


# ── Internal helpers ───────────────────────────────────────────────────


def _get_extension(filename: str) -> str:
    return Path(filename).suffix.lower().lstrip(".")


def _validate_image(file: UploadFile) -> Tuple[bool, str]:
    """Pre-flight: extension whitelist (image-only) + size limit."""
    ext = _get_extension(file.filename or "")
    if ext not in IMAGE_EXTENSIONS:
        return False, f"vision 端点仅支持图片: .{ext or '(无扩展名)'}"
    max_bytes = int(settings.upload_max_size_mb * 1024 * 1024)
    size = 0
    try:
        size = file.size if getattr(file, "size", None) is not None else 0
        if size == 0:
            file.file.seek(0, 2)
            size = file.file.tell()
            file.file.seek(0)
    except Exception:
        logger.warning("_validate_image: failed to stat %s", file.filename, exc_info=True)
        return False, "无法读取文件大小"
    if size > max_bytes:
        return False, f"文件大小超过上限 {settings.upload_max_size_mb}MB"
    return True, ""


def _persist_to_temp(file: UploadFile) -> Path:
    """Write the uploaded image to a temp file and return its path.

    Uses a stable ``<uuid>_<filename>`` layout under the OS temp dir so the
    vision client can read it via plain ``Path.read_bytes``.
    """
    tmp_dir = Path(tempfile.gettempdir()) / "qa_vision"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    file_id = uuid.uuid4().hex
    original_name = file.filename or "image"
    dest = tmp_dir / f"{file_id}_{original_name}"
    with dest.open("wb") as out:
        while True:
            chunk = file.file.read(64 * 1024)
            if not chunk:
                break
            out.write(chunk)
    return dest


# ── Endpoint ────────────────────────────────────────────────────────────

@router.post("/convert", response_model=ConvertResponse)
async def convert_image(file: UploadFile = File(..., description="Image to convert (multipart). png/jpg/jpeg/gif/webp only.")):
    """Convert a single image to text via the VISION slot multimodal model.

    Stateless: no session_id, no conversation context, no persistence on
    the backend beyond the temporary image file (deleted after conversion).
    The returned ``text`` is wrapped by the frontend as a ``.txt`` File and
    uploaded via the normal session upload flow when the user proceeds.

    Vision call failure → HTTP 502 (no fallback).
    """
    ok, err_msg = _validate_image(file)
    if not ok:
        if "超过上限" in err_msg:
            raise HTTPException(status_code=413, detail=err_msg)
        raise HTTPException(status_code=415, detail=err_msg)

    image_path = _persist_to_temp(file)
    source_name = file.filename or image_path.name

    from core.vision_client import image_to_text

    try:
        text = await image_to_text(str(image_path))
    except Exception as e:
        logger.error(
            "convert_image: image-to-text failed image=%s: %s",
            image_path, e, exc_info=True,
        )
        raise HTTPException(
            status_code=502,
            detail=f"图片解析失败：{e}，请重试或改传文本文件",
        ) from e
    finally:
        # Always clean up the temp image — the .txt is owned by the frontend
        # (it wraps the returned text) and the session upload flow persists
        # it again under the session's own upload dir.
        try:
            image_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("convert_image: failed to clean up %s", image_path)

    suggested_filename = f"{Path(source_name).stem}.txt"
    size_bytes = len(text.encode("utf-8"))

    logger.info(
        "convert_image: success source=%s → %s (text_len=%d)",
        source_name, suggested_filename, size_bytes,
    )

    return ConvertResponse(
        text=text,
        suggested_filename=suggested_filename,
        source_name=source_name,
        size_bytes=size_bytes,
    )
