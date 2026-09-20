"""
QA Agent System — Session File Uploads

Provides REST endpoints for per-session file uploads (multipart):
  - POST   /sessions/{sid}/uploads         — Upload single file (multipart/form-data)
  - GET    /sessions/{sid}/uploads         — List uploaded file metadata
  - DELETE /sessions/{sid}/uploads/{fid}   — Delete one uploaded file

Files land under ``<tempfile.gettempdir()>/qa_uploads/<sid>/<uuid>_<filename>``.
The path is cross-platform (Linux ``/tmp``, Windows ``%TEMP%``). Metadata is
kept only in ``session_state.uploads`` (in-memory list) — no DB persistence.
Session delete (delete_user_session) cascades a ``shutil.rmtree`` to clean up.

Constraints:
  - Single file per request (no batch).
  - Max size: ``settings.upload_max_size_mb`` (default 1.0 MB).
  - Allowed extensions: txt/md/csv/xlsx/docx.
  - Images (png/jpg/jpeg/gif/webp) are NOT accepted here — they must be
    converted via the dedicated ``POST /vision/convert`` endpoint first;
    the frontend wraps the returned text as a ``.txt`` File and uploads
    that through this endpoint (same paradigm as a user-selected .txt).
  - xmind is explicitly forbidden (no mature parser; spec D4).

"""

from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path
from typing import List, Tuple

from fastapi import APIRouter, File, HTTPException, UploadFile

from api.schemas import UploadedFileRef
from api.session_manager import session_manager
from core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["session-uploads"])

# ── Format policy ──────────────────────────────────────────────────────
# Allowed upload extensions (whitelist). Anything else → 415.
# Images are intentionally EXCLUDED — they go through POST /vision/convert
# first, the frontend wraps the returned text as a .txt File, then uploads
# that .txt here (same as a user-selected .txt file).
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({
    "txt", "md", "csv", "xlsx", "docx",
})

# Image extensions — REJECTED at session upload (must use /vision/convert).
# Kept as a set so upload_file can give a helpful redirect error message.
IMAGE_EXTENSIONS: frozenset[str] = frozenset({
    "png", "jpg", "jpeg", "gif", "webp",
})

# Explicitly forbidden extensions with a dedicated error message.
# xmind: no mature parser; design D4 disables upload.
FORBIDDEN_EXTENSIONS: frozenset[str] = frozenset({"xmind"})

# Maximum number of files accepted by POST /sessions/{sid}/uploads/batch.
MAX_BATCH_FILES: int = 5


# ── Internal helpers ───────────────────────────────────────────────────

def _resolve_upload_dir(session_id: str) -> Path:
    """Return per-session upload dir, creating it if missing.

    Path layout: ``<tempfile.gettempdir()>/qa_uploads/<session_id>/``.
    Cross-platform via Path + tempfile.gettempdir() (no hardcoded separators).
    """
    upload_dir = Path(tempfile.gettempdir()) / "qa_uploads" / session_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def _get_extension(filename: str) -> str:
    """Return lowercase extension without the leading dot, or "" if none."""
    return Path(filename).suffix.lower().lstrip(".")


def _validate_file(file: UploadFile) -> Tuple[bool, str]:
    """Pre-flight check: extension whitelist + size limit.

    Returns ``(ok, error_msg)``. On success ``error_msg`` is "".
    On failure ``ok=False`` and ``error_msg`` carries the user-facing reason
    (the caller maps it to HTTP 413 / 415).
    """
    ext = _get_extension(file.filename or "")

    if ext in FORBIDDEN_EXTENSIONS:
        return False, f"{ext} 格式暂不支持上传"

    if ext not in ALLOWED_EXTENSIONS:
        return False, f"文件格式不支持: .{ext}"

    # Size check (best-effort: rely on spooled file size before read).
    max_bytes = int(settings.upload_max_size_mb * 1024 * 1024)
    size = 0
    try:
        # UploadFile.file is the underlying SpooledTemporaryFile; seek to end
        # to get size, then rewind so downstream read sees the whole payload.
        size = file.size if getattr(file, "size", None) is not None else 0
        if size == 0:
            file.file.seek(0, 2)
            size = file.file.tell()
            file.file.seek(0)
    except Exception:
        logger.warning("_validate_file: failed to stat %s", file.filename, exc_info=True)
        return False, "无法读取文件大小"

    if size > max_bytes:
        return False, f"文件大小超过上限 {settings.upload_max_size_mb}MB"

    return True, ""


def _persist_file(session_id: str, file: UploadFile) -> dict:
    """Write the uploaded file to disk and return metadata dict.

    Filename layout: ``<upload_dir>/<uuid>_<original_filename>``. The uuid
    prefix prevents collisions when multiple users upload same-named files
    to different sessions, and also guards against filename-encoding issues.

    Returns ``{file_id, name, size, type, abs_path}``.
    """
    upload_dir = _resolve_upload_dir(session_id)
    file_id = str(uuid.uuid4())
    original_name = file.filename or "unnamed"
    dest = upload_dir / f"{file_id}_{original_name}"

    with dest.open("wb") as out:
        while True:
            chunk = file.file.read(64 * 1024)  # 64KB chunks
            if not chunk:
                break
            out.write(chunk)

    try:
        size = dest.stat().st_size
    except OSError:
        size = 0

    ext = _get_extension(original_name)
    return {
        "file_id": file_id,
        "name": original_name,
        "size": size,
        "type": ext,
        "abs_path": str(dest.resolve()),
    }


def _rollback_batch(session_id: str, persisted_metas: list[dict]) -> None:
    """Best-effort rollback of files already persisted + state-registered
    during a batch upload that failed midway.

    For each meta: unlink the disk file (``missing_ok=True`` → idempotent)
    and remove the entry from ``session_state.uploads`` by exact ``file_id``
    match (NOT pop/index — the list may have unrelated entries from prior
    successful single uploads). Exceptions are logged as warnings and never
    raised, matching the best-effort pattern of the existing cascade-delete
    helpers.

    """
    for m in persisted_metas:
        abs_path = m.get("abs_path")
        if abs_path:
            try:
                Path(abs_path).unlink(missing_ok=True)
            except Exception:
                logger.warning(
                    "batch rollback: unlink failed for %s",
                    abs_path, exc_info=True,
                )
        # Remove from session_state.uploads by exact file_id match.
        record = session_manager.get(session_id)
        if record is None or record.agent is None:
            continue
        state = getattr(record.agent, "session_state", None)
        if state is None:
            continue
        uploads = getattr(state, "uploads", None)
        if not uploads:
            continue
        file_id = m.get("file_id")
        try:
            uploads[:] = [u for u in uploads if u.get("file_id") != file_id]
        except Exception:
            logger.warning(
                "batch rollback: failed to remove meta from session_state "
                "for session %s (file_id=%s)",
                session_id, file_id, exc_info=True,
            )


def _add_to_session_state(session_id: str, meta: dict) -> None:
    """Append upload metadata to in-memory session_state.uploads.

    If the session is not in memory (e.g. cold-restored session that has no
    attached agent yet), we log a warning and skip — the file is still on
    disk and the agent can still read it via file_read if the abs_path is
    surfaced another way (rare; the spec's primary flow assumes a live session).
    """
    record = session_manager.get(session_id)
    if record is None or record.agent is None:
        logger.warning(
            "_add_to_session_state: session %s not in memory or has no agent; "
            "upload metadata will not be tracked (file is still on disk)",
            session_id, exc_info=False,
        )
        return
    state = getattr(record.agent, "session_state", None)
    if state is None:
        logger.warning(
            "_add_to_session_state: session %s agent has no session_state; "
            "skipping uploads append", session_id,
        )
        return
    try:
        uploads = getattr(state, "uploads", None)
        if uploads is None:
            uploads = []
            setattr(state, "uploads", uploads)
        uploads.append(meta)
    except Exception:
        logger.warning(
            "_add_to_session_state: failed to append meta for session %s",
            session_id, exc_info=True,
        )


def _get_session_uploads(session_id: str) -> List[dict]:
    """Return the in-memory uploads list for a session, or [] if unset."""
    record = session_manager.get(session_id)
    if record is None or record.agent is None:
        return []
    state = getattr(record.agent, "session_state", None)
    if state is None:
        return []
    uploads = getattr(state, "uploads", None)
    return uploads if uploads is not None else []


def clean_session_upload_dir(session_id: str) -> None:
    """Best-effort rmtree of ``<temp>/qa_uploads/<sid>/``.

    Called by delete_user_session (Step 9) to cascade-clean uploaded files
    when a session is deleted. Idempotent: a missing dir is a no-op.
    Failures are logged (warning) and never raised, matching the best-effort
    pattern of the existing 8 cascade-delete steps.

    Note: resolves the path directly (does NOT call _resolve_upload_dir,
    which would mkdir the dir we are about to delete).
    """
    try:
        import shutil
        upload_dir = Path(tempfile.gettempdir()) / "qa_uploads" / session_id
        shutil.rmtree(upload_dir, ignore_errors=True)
    except Exception:
        logger.warning(
            "clean_session_upload_dir: failed to rmtree uploads for session %s",
            session_id, exc_info=True,
        )


# ── Endpoints ──────────────────────────────────────────────────────────

@router.post("/sessions/{session_id}/uploads", response_model=UploadedFileRef)
async def upload_file(session_id: str, file: UploadFile = File(..., description="Single file to upload (multipart)")):
    """Upload a single file to a session's temp upload dir.

    Constraints: ≤ ``settings.upload_max_size_mb`` MB, extension whitelist
    (txt/md/csv/xlsx/docx). xmind is forbidden.

    Images (png/jpg/jpeg/gif/webp) are NOT accepted here — they must go
    through ``POST /vision/convert`` first; the frontend wraps the returned
    text as a ``.txt`` File and uploads that through this endpoint (same
    paradigm as a user-selected .txt file).

    For multi-file batch uploads (atomic, single request), use
    ``POST /sessions/{sid}/uploads/batch`` instead.
    """
    # Validate session exists (in-memory OR persistent storage).
    record = session_manager.get(session_id)
    if record is None:
        exists = await session_manager.exists_in_storage(session_id)
        if not exists:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    # Reject images with a helpful redirect to the convert endpoint.
    ext = _get_extension(file.filename or "")
    if ext in IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"图片请通过 /vision/convert 端点转换后再上传（不支持 .{ext}）",
        )

    ok, err_msg = _validate_file(file)
    if not ok:
        # Map: forbidden/unsupported ext → 415; oversize → 413.
        if "超过上限" in err_msg:
            raise HTTPException(status_code=413, detail=err_msg)
        raise HTTPException(status_code=415, detail=err_msg)

    meta = _persist_file(session_id, file)

    _add_to_session_state(session_id, meta)

    logger.info(
        "upload_file: session=%s file_id=%s name=%s size=%d",
        session_id, meta["file_id"], meta["name"], meta["size"],
    )

    return UploadedFileRef(
        file_id=meta["file_id"],
        name=meta["name"],
        abs_path=meta["abs_path"],
    )


@router.post("/sessions/{session_id}/uploads/batch")
async def upload_files_batch(
    session_id: str,
    files: List[UploadFile] = File(
        ..., description="Multiple files (multipart, repeated field name 'files')"
    ),
):
    """Batch-upload multiple files to a session's temp upload dir in a single
    request.

    Constraints per file mirror ``upload_file`` (size ≤
    ``settings.upload_max_size_mb``, extension whitelist txt/md/csv/xlsx/docx,
    images rejected → 415 + redirect to /vision/convert). Plus:
      - ``files`` empty array → 422 ``至少上传 1 个文件``
      - ``len(files) > MAX_BATCH_FILES`` (5) → 422 ``文件数量超过上限 5 个``

    Atomicity: if ANY file fails validation (extension/size), the entire
    batch is rolled back — already-persisted files are ``unlink``-ed and
    their ``session_state.uploads`` entries removed by ``file_id`` match
    (see ``_rollback_batch``). Returns 4xx with the offending filename +
    reason.

    """
    # Validate session exists (in-memory OR persistent storage).
    record = session_manager.get(session_id)
    if record is None:
        exists = await session_manager.exists_in_storage(session_id)
        if not exists:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    if not files:
        raise HTTPException(status_code=422, detail="至少上传 1 个文件")
    if len(files) > MAX_BATCH_FILES:
        raise HTTPException(
            status_code=422,
            detail=f"文件数量超过上限 {MAX_BATCH_FILES} 个",
        )

    persisted: list[dict] = []
    try:
        for file in files:
            name = file.filename or "unnamed"
            ext = _get_extension(name)
            if ext in IMAGE_EXTENSIONS:
                raise HTTPException(
                    status_code=415,
                    detail=(
                        f"文件 {name} 图片请通过 /vision/convert 端点转换后再上传"
                        f"（不支持 .{ext}）"
                    ),
                )
            ok, err_msg = _validate_file(file)
            if not ok:
                if "超过上限" in err_msg:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件 {name} {err_msg}",
                    )
                raise HTTPException(
                    status_code=415,
                    detail=f"文件 {name} {err_msg}",
                )
            meta = _persist_file(session_id, file)
            _add_to_session_state(session_id, meta)
            persisted.append(meta)
    except HTTPException:
        _rollback_batch(session_id, persisted)
        raise

    logger.info(
        "upload_files_batch: session=%s count=%d names=%s",
        session_id, len(persisted), [m["name"] for m in persisted],
    )

    return {
        "files": [
            UploadedFileRef(
                file_id=m["file_id"],
                name=m["name"],
                abs_path=m["abs_path"],
            )
            for m in persisted
        ]
    }


@router.get("/sessions/{session_id}/uploads")
async def list_uploads(session_id: str):
    """List metadata of files uploaded to a session (no file content)."""
    record = session_manager.get(session_id)
    if record is None:
        exists = await session_manager.exists_in_storage(session_id)
        if not exists:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    files = _get_session_uploads(session_id)
    return {"files": files}


@router.delete("/sessions/{session_id}/uploads/{file_id}", status_code=204, response_model=None)
async def delete_upload(session_id: str, file_id: str):
    """Delete one uploaded file (disk + session_state entry).

    Idempotent w.r.t. missing disk file: if the on-disk file is gone (e.g.
    cleaned by an external tmp reaper), we still remove the session_state
    entry and return 204. A 404 is returned only when file_id is not in
    session_state.uploads at all.
    """
    record = session_manager.get(session_id)
    if record is None:
        exists = await session_manager.exists_in_storage(session_id)
        if not exists:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    uploads = _get_session_uploads(session_id)
    target = next((m for m in uploads if m.get("file_id") == file_id), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"file_id not found: {file_id}")

    abs_path = target.get("abs_path")
    if abs_path:
        try:
            Path(abs_path).unlink(missing_ok=True)
        except Exception:
            logger.warning(
                "delete_upload: failed to unlink %s (removing entry anyway)",
                abs_path, exc_info=True,
            )
    else:
        logger.warning("delete_upload: file_id %s has no abs_path", file_id)

    # Remove the entry from session_state.uploads (mutate in place).
    uploads.remove(target)

    logger.info("delete_upload: session=%s file_id=%s removed", session_id, file_id)
    return None
