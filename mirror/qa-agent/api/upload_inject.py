"""
QA Agent System — Uploaded Files Prompt Injection Helper

Extracted from api.server.send_message so it can be unit-tested without
pulling the full server import chain. The helper injects a [用户上传文件]
block into user_content, mirroring workspace_context.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def inject_uploaded_files_block(
    user_content: str,
    uploaded_files: list,
    session_uploads: list,
    session_id: str,
) -> str:
    """Inject a [用户上传文件] block before [用户消息] in user_content.

    Block order with workspace_context: [工作区上下文] → [用户上传文件] → [用户消息].
    Entries whose abs_path is NOT in session_uploads are skipped + warned
    (stale ref, deleted file, or fabricated client payload) — never block send.
    If no valid ref survives (empty list or all skipped), user_content is
    returned unchanged.

    Args:
        user_content: Current user message content (possibly already wrapped by
            workspace_context injection).
        uploaded_files: List of UploadedFileRef (or objects with .abs_path /
            .file_id attributes) from SendMessageRequest.
        session_uploads: List of metadata dicts from session_state.uploads
            (each must have an "abs_path" key).
        session_id: For warning log correlation only.

    Returns:
        Mutated user_content with [用户上传文件] block inserted, or the
        original user_content if no valid ref survived.
    """
    if not uploaded_files:
        return user_content
    known_paths = {m.get("abs_path") for m in (session_uploads or []) if isinstance(m, dict)}
    upload_lines = ["[用户上传文件]", "用户上传了以下文件供参考:"]
    valid_count = 0
    for ref in uploaded_files:
        abs_path = getattr(ref, "abs_path", None)
        if abs_path not in known_paths:
            logger.warning(
                "send_message: uploaded_files abs_path %s not in session_state.uploads "
                "(session=%s, file_id=%s) — skipping",
                abs_path, session_id, getattr(ref, "file_id", "?"),
            )
            continue
        upload_lines.append(f"- {abs_path} (文件)")
        valid_count += 1
    if valid_count == 0:
        return user_content
    upload_lines.append("")
    upload_block = "\n".join(upload_lines)
    if "[用户消息]" in user_content:
        # workspace_context already wrapped: insert before [用户消息] marker.
        return user_content.replace("[用户消息]", upload_block + "[用户消息]", 1)
    # No workspace_context wrap: prepend block + [用户消息] marker.
    return upload_block + "[用户消息]\n" + user_content
