"""
Read File State Cache

Tracks which files have been read by file_read, enabling file_edit's
validateInput to enforce "read before write" and detect external modifications.

Design reference: Claude Code's readFileState cache.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class FileReadRecord:
    """Snapshot of a file at the time it was read."""

    path: str                    # Resolved absolute path
    mtime: float                 # os.path.getmtime() at read time
    content_hash: str            # SHA-256 hex digest of full content
    offset: int = 0              # Start line offset used in read
    limit: int = 500             # Line limit used in read
    is_partial: bool = False     # True if offset/limit means partial view


class ReadFileStateCache:
    """Singleton cache of file-read records.

    Thread-safe via a simple lock — tool calls may be concurrent.
    """

    _instance: Optional["ReadFileStateCache"] = None
    _lock_cls = threading.Lock()

    def __new__(cls) -> "ReadFileStateCache":
        with cls._lock_cls:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._records: Dict[str, FileReadRecord] = {}
                cls._instance._lock = threading.Lock()
            return cls._instance

    # ── Public API ──────────────────────────────────────────────────────

    def record_read(
        self,
        path: str,
        content: str | bytes,
        *,
        offset: int = 0,
        limit: int = 500,
        total_lines: int = 0,
    ) -> FileReadRecord:
        """Record that a file was just read.

        Args:
            path: File path (will be resolved to absolute).
            content: The raw bytes or string content read from the file.
            offset: Starting line offset used.
            limit: Line limit used.
            total_lines: Total lines in the file (for partial detection).

        Returns:
            The created FileReadRecord.
        """
        abs_path = str(Path(path).resolve())

        # Compute content hash
        if isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            content_bytes = content
        content_hash = hashlib.sha256(content_bytes).hexdigest()

        # Determine if this was a partial read
        is_partial = offset > 0 or (total_lines > 0 and offset + limit < total_lines)

        try:
            mtime = os.path.getmtime(abs_path)
        except OSError:
            mtime = 0.0

        record = FileReadRecord(
            path=abs_path,
            mtime=mtime,
            content_hash=content_hash,
            offset=offset,
            limit=limit,
            is_partial=is_partial,
        )

        with self._lock:
            self._records[abs_path] = record

        logger.debug(
            "[file_state_cache] Recorded read: %s (partial=%s, hash=%s...)",
            abs_path, is_partial, content_hash[:12],
        )
        return record

    def get_record(self, path: str) -> Optional[FileReadRecord]:
        """Get the most recent read record for a file.

        Args:
            path: File path (will be resolved to absolute).

        Returns:
            FileReadRecord if file was previously read, else None.
        """
        abs_path = str(Path(path).resolve())
        with self._lock:
            return self._records.get(abs_path)

    def is_stale(self, path: str) -> bool:
        """Check if a file has been modified since last read.

        Args:
            path: File path to check.

        Returns:
            True if file was modified externally since last read,
            or if the file was never read.
        """
        record = self.get_record(path)
        if record is None:
            return True  # Never read → definitely stale

        abs_path = str(Path(path).resolve())
        try:
            current_mtime = os.path.getmtime(abs_path)
        except OSError:
            return True  # File gone → stale

        if current_mtime > record.mtime:
            # mtime changed — but on some systems (cloud sync, antivirus)
            # mtime can change without content change.  Compare content hash.
            try:
                current_content = Path(abs_path).read_bytes()
                current_hash = hashlib.sha256(current_content).hexdigest()
                if current_hash == record.content_hash:
                    # Content unchanged despite mtime bump → not stale
                    return False
            except OSError:
                return True

            return True

        return False

    def remove(self, path: str) -> None:
        """Remove a record (e.g., after successful edit updates the cache)."""
        abs_path = str(Path(path).resolve())
        with self._lock:
            self._records.pop(abs_path, None)

    def clear(self) -> None:
        """Clear all records (e.g., on session reset)."""
        with self._lock:
            self._records.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._records)


# Module-level convenience accessor
def get_file_state_cache() -> ReadFileStateCache:
    """Get the singleton ReadFileStateCache instance."""
    return ReadFileStateCache()
