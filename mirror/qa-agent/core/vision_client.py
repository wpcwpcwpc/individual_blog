"""
QA Agent System — Vision Client (Image-to-Text)

Converts uploaded images to text descriptions via a multimodal vision model.
Used by api.session_uploads when a user uploads an image file: the image is
read as base64, sent to the configured VISION slot model (OpenAI multimodal
``image_url`` content block format), and the returned text is written to a
``.txt`` file so the AI agent can read it via the standard file_read path
(same paradigm as txt/csv/xlsx uploads).

"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any, Dict

from agno.media import Image
from agno.models.message import Message

from core.config import QAAgentSettings, settings

logger = logging.getLogger(__name__)


# ── Module-level constants ───────────────────────────────────────────────────

VISION_PROMPT = (
    "请详细描述这张图片的内容，包括：\n"
    "1. 整体场景与布局\n"
    "2. 关键文字内容（如有，逐字转录，保留原始格式如表格/列表）\n"
    "3. UI 元素、按钮、输入框及其文字\n"
    "4. 数据、图表、流程图的结构与数值\n"
    "5. 任何对软件测试用例生成有价值的信息\n"
    "输出纯文本，不要加 Markdown 标题。\n"
    "根据图片信息量控制在 300-1000 字左右：信息稀疏的图简短描述，"
    "含密集表格/长文本的图可适当加长，但不要超过 1000 字。"
)

# Max output tokens for vision model response. Prevents runaway long
# descriptions on dense images; combined with prompt's 300-1000 char guidance.
VISION_MAX_TOKENS = 4096

_MEDIA_TYPE_MAP: Dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


# ── Public API ──────────────────────────────────────────────────────────────

async def image_to_text(image_path: str, app_settings: QAAgentSettings | None = None) -> str:
    """Convert an image file to a text description via the vision model.

    Resolves the VISION slot from ModelSlotRegistry, encodes the image as
    base64 data URI, sends a multimodal message to the model, and returns
    the model's text response.

    Args:
        image_path: Absolute path to the image file (png/jpg/jpeg/gif/webp).
        app_settings: Settings override. Uses global ``settings`` if None.

    Returns:
        Text description of the image content from the vision model.

    Raises:
        ValueError: Unsupported image extension.
        RuntimeError: Vision model call failed (wraps underlying cause).
    """
    from core.model_slots import get_model_slot_registry, ModelSlot

    cfg = app_settings or settings
    p = Path(image_path)
    ext = p.suffix.lstrip(".").lower()
    if ext not in _MEDIA_TYPE_MAP:
        raise ValueError(f"Unsupported image extension: .{ext}")

    media_type = _MEDIA_TYPE_MAP[ext]
    try:
        b64_bytes = p.read_bytes()
    except OSError as e:
        logger.error("image_to_text: failed to read image %s: %s", image_path, e)
        raise RuntimeError(f"读取图片失败: {e}") from e
    b64_str = base64.b64encode(b64_bytes).decode("ascii")
    data_uri = f"data:{media_type};base64,{b64_str}"

    # Agno Model.ainvoke signature requires ``assistant_message`` (a Message
    # placeholder the model fills with its response) and a list of ``Message``
    # objects — raw dicts are not accepted. Image data is passed via the agno
    # ``Image`` media object on the user Message (data URI is supported).
    user_msg = Message(
        role="user",
        content=VISION_PROMPT,
        images=[Image(url=data_uri)],
    )
    assistant_msg = Message(role="assistant")

    model = get_model_slot_registry().resolve(ModelSlot.VISION)
    # ``max_tokens`` is a constructor-time field on OpenAIChat; agno's
    # ``ainvoke`` does not accept it as a kwarg. resolve() returns a fresh
    # RateLimitedModel (which proxies setattr to the inner OpenAICompatModel) each
    # call, so mutating here is safe and scoped to this conversion.
    model.max_tokens = VISION_MAX_TOKENS
    logger.info(
        "image_to_text: calling vision model for %s (size=%d bytes, media=%s)",
        image_path, len(b64_bytes), media_type,
    )

    try:
        result = await model.ainvoke(
            messages=[user_msg], assistant_message=assistant_msg
        )
    except Exception as e:
        logger.error(
            "image_to_text: vision model call failed for %s: %s",
            image_path, e, exc_info=True,
        )
        raise RuntimeError(f"vision 模型调用失败: {e}") from e

    text = _extract_content_text(result)
    if not text:
        raise RuntimeError("vision 模型返回空内容")
    logger.info(
        "image_to_text: success for %s, text_len=%d", image_path, len(text)
    )
    return text


# ── Internal helpers ────────────────────────────────────────────────────────

def _extract_content_text(result: Any) -> str:
    """Extract text content from an Agno RunResponse/RunOutput.

    Mirrors RateLimitedModel._extract_content_text logic but inlined here
    to avoid importing rate_limiter (which pulls in agno/httpx heavy deps)
    at module load time.

    Args:
        result: ainvoke return value.

    Returns:
        Extracted text string; empty string if no text.
    """
    if result is None:
        return ""
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return content
    if content is None:
        try:
            s = str(result)
            return s if any(c.isalpha() for c in s) else ""
        except Exception:
            return ""
    try:
        return str(content)
    except Exception:
        return ""
