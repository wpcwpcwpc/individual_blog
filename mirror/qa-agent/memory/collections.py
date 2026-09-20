"""
Milvus collection name constants and filter helpers.

These constants define the Milvus collection names used by the KnowledgeHub.
Collection creation is handled automatically by Agno's Knowledge + Milvus adapter
(enable_dynamic_field=True) — no manual schema definitions needed.

Collections
-----------
  qa_execution_log  — Per-tool-call execution records
  qa_knowledge      — LLM-distilled compressed knowledge from context threshold
  qa_conclusions    — Human-confirmed test conclusions

Filter helpers
--------------
Agno's Milvus adapter (verified against agno==2.x in `venv/Lib/site-packages/
agno/vectordb/milvus/milvus.py`) stores user metadata under the dynamic field
name ``meta_data`` (with an underscore), NOT ``metadata``. All delete/query
filters that target ``session_id`` MUST use the helpers below so we cannot
accidentally re-introduce the ``metadata["session_id"]`` typo that caused
production deletes to silently no-op.
"""

from __future__ import annotations

from typing import Iterable

# Collection names used by KnowledgeHub
COL_EXECUTION_LOG = "qa_execution_log"
COL_KNOWLEDGE = "qa_knowledge"
COL_CONCLUSIONS = "qa_conclusions"

# ---------------------------------------------------------------------------
# Dynamic metadata field
# ---------------------------------------------------------------------------

#: Name of the dynamic metadata field used by Agno's Milvus adapter.
#: Confirmed against agno's ``vectordb/milvus/milvus.py`` — the adapter maps
#: ``Document.meta_data`` to the Milvus dynamic field of the same name. Prior
#: to the current schema, production code used
#: the wrong name ``metadata`` which caused all delete-by-session operations
#: to silently match 0 rows.
META_FIELD = "meta_data"


# ---------------------------------------------------------------------------
# Legacy / deprecated collections
# ---------------------------------------------------------------------------

#: Collections that pre-date the current Agno-backed schema and are kept only
#: because `client.list_collections()` still reports them. See ``DEPLOYMENT.md``
#: for the pre-Agno migration notes. The ``drop-legacy`` housekeeping
#: subcommand is the only path that should remove them, and only after
#: confirming they are empty.
LEGACY_COLLECTIONS: tuple[str, ...] = ("qa_memory", "qa_session")


# ---------------------------------------------------------------------------
# Filter helpers
# ---------------------------------------------------------------------------
def build_session_filter(session_id: str) -> str:
    """Return a Milvus filter expression matching one session_id exactly.

    Example::

        >>> build_session_filter("abc123")
        'meta_data["session_id"] == "abc123"'
    """
    return f'{META_FIELD}["session_id"] == "{session_id}"'


def build_session_prefix_filter(prefix: str) -> str:
    """Return a Milvus filter expression matching every session_id starting with ``prefix``.

    Used primarily for ``__probe_`` diagnostic leftovers.

    Example::

        >>> build_session_prefix_filter("__probe_")
        'meta_data["session_id"] like "__probe_%"'
    """
    return f'{META_FIELD}["session_id"] like "{prefix}%"'


def build_session_exclusion_filter(live_ids: Iterable[str]) -> str:
    """Return a Milvus filter expression matching any session_id NOT in ``live_ids``.

    This is the authoritative filter for orphan pruning. An empty ``live_ids``
    would produce a filter that matches every record in the collection (i.e.
    "delete everything"), so we refuse to build such a filter and raise
    :class:`ValueError` instead. Callers MUST handle the empty-authoritative-set
    case explicitly (typically by aborting with an error, never by deleting).

    Example::

        >>> build_session_exclusion_filter(["s1", "s2"])
        'meta_data["session_id"] not in ["s1", "s2"]'
    """
    ids_list = list(live_ids)
    if not ids_list:
        raise ValueError(
            "build_session_exclusion_filter refuses to build a filter from an "
            "empty live_ids set — that would match every record in the "
            "collection. Callers must handle the empty case before calling."
        )
    quoted = ", ".join(f'"{sid}"' for sid in ids_list)
    return f'{META_FIELD}["session_id"] not in [{quoted}]'


__all__ = [
    "COL_EXECUTION_LOG",
    "COL_KNOWLEDGE",
    "COL_CONCLUSIONS",
    "META_FIELD",
    "LEGACY_COLLECTIONS",
    "build_session_filter",
    "build_session_prefix_filter",
    "build_session_exclusion_filter",
]