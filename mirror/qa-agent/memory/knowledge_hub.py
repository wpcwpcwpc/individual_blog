"""
QA Agent System — Knowledge Hub

Central hub for managing three Agno Knowledge instances backed by Milvus,
providing unified write (safe_insert) and read (safe_search, hub_retriever)
with per-operation try/catch degradation (no persistent flags).

Architecture
------------
  exec_log_knowledge   → Milvus collection "qa_execution_log"
  distill_knowledge    → Milvus collection "qa_knowledge"
  conclusion_knowledge → Milvus collection "qa_conclusions"
  facade_knowledge     → Milvus collection "qa_knowledge" (for Agent tool registration only)

The ``hub_retriever`` function is designed to be passed as
``knowledge_retriever`` to the Agno Agent, which completely short-circuits
the default Knowledge.search() and routes queries to all three collections.

Startup self-check
------------------
Each :class:`KnowledgeHub` instance runs a background insert → query →
delete probe across all three live collections on first construction. The
result is exposed via :attr:`KnowledgeHub.startup_self_check` and awaitable
via :meth:`KnowledgeHub.ensure_self_check`. Probe records use a
``__startup_<12-hex>`` prefix so housekeeping can identify leftovers if the
delete step fails. Set ``QA_MEMORY_SKIP_STARTUP_CHECK=1`` or pass
``skip_startup_check=True`` to disable (e.g. for CLI scripts / tests).
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from memory.collections import (
    COL_CONCLUSIONS,
    COL_EXECUTION_LOG,
    COL_KNOWLEDGE,
    build_session_filter,
)

logger = logging.getLogger(__name__)

# Singleton instance
_hub_instance: Optional["KnowledgeHub"] = None
_hub_init_attempted: bool = False  # Prevents repeated timeout waits


class KnowledgeHub:
    """Manages three Agno Knowledge instances with degradation support.

    Each write/search operation is wrapped in an independent try/catch:
    - On failure: log ERROR with traceback and return safe default
      (skip write / empty results)
    - On recovery: next operation automatically succeeds (no persistent flag)
    """

    def __init__(
        self,
        milvus_uri: str,
        embedder: Any,
        *,
        skip_startup_check: bool = False,
    ):
        """Initialize three Knowledge instances and one facade.

        Args:
            milvus_uri: Milvus connection URI (e.g. "http://localhost:19530")
            embedder: An Agno OpenAIEmbedder instance for vector generation.
            skip_startup_check: If True, skip the insert/query/delete probe at
                boot. Also honored via env var ``QA_MEMORY_SKIP_STARTUP_CHECK=1``.
        """
        from agno.knowledge.knowledge import Knowledge
        from agno.vectordb.milvus import Milvus

        self._milvus_uri = milvus_uri
        self._embedder = embedder

        # Shared MilvusClient (lazy, reused across operations): per-call
        # MilvusClient construction re-establishes a remote gRPC connection
        # every time.
        self._milvus_client: Any = None
        self._client_lock = threading.Lock()

        # --- Three real Knowledge instances ---
        self.exec_log_knowledge = Knowledge(
            name="exec_log",
            vector_db=Milvus(
                collection=COL_EXECUTION_LOG,
                uri=milvus_uri,
                embedder=embedder,
            ),
        )

        self.distill_knowledge = Knowledge(
            name="qa_knowledge",
            vector_db=Milvus(
                collection=COL_KNOWLEDGE,
                uri=milvus_uri,
                embedder=embedder,
            ),
        )

        self.conclusion_knowledge = Knowledge(
            name="qa_conclusions",
            vector_db=Milvus(
                collection=COL_CONCLUSIONS,
                uri=milvus_uri,
                embedder=embedder,
            ),
        )

        # --- Facade: only used to satisfy Agent(knowledge=...) requirement ---
        # Points to the same qa_knowledge collection. Agno requires
        # knowledge != None when search_knowledge=True, but actual search
        # is fully handled by hub_retriever (which short-circuits Knowledge.search).
        self.facade_knowledge = Knowledge(
            name="qa_agent_knowledge",
            vector_db=Milvus(
                collection=COL_KNOWLEDGE,
                uri=milvus_uri,
                embedder=embedder,
            ),
        )

        # --- Per-collection write/delete counters (process-local) ---
        # Protected by a plain threading.Lock because safe_insert is an
        # async coroutine that may be scheduled from different threads in
        # practice (worker threads, uvicorn task groups, CLI scripts).
        # Critical sections here are tiny integer increments inside
        # deepcopy/get_stats() — lock contention is negligible and a
        # threading.Lock is correct across both sync and async call sites.
        self._stats_lock = threading.Lock()
        self._stats: Dict[str, Dict[str, int]] = {
            col: {
                "inserts_ok": 0,
                "inserts_failed": 0,
                "deletes_ok": 0,
                "deletes_failed": 0,
                # embedding_failures is a SUBSET of inserts_failed — when an
                # insert fails because the embedder could not produce a
                # vector (TPM/429, nil vector, etc.), BOTH counters
                # increment. A non-zero value here with a matching
                # inserts_failed means "the embedder is the bottleneck,
                # not Milvus itself". See memory-write-reliability spec,
                # "Embedding Failure Observability".
                "embedding_failures": 0,
            }
            for col in (COL_EXECUTION_LOG, COL_KNOWLEDGE, COL_CONCLUSIONS)
        }

        # --- Startup self-check state ---
        # Populated by _startup_self_check_impl; inspect after awaiting
        # ensure_self_check() to see per-collection outcome.
        self.startup_self_check: Dict[str, Dict[str, Any]] = {}
        # Plain bool flag instead of asyncio.Event — KnowledgeHub is often
        # constructed inside a worker thread (see get_knowledge_hub's threaded
        # init) where no running loop exists yet; asyncio primitives can
        # misbehave when created in one loop and awaited in another.
        self._self_check_done_flag: bool = False
        # The lock is created lazily on first ensure_self_check() call, so
        # KnowledgeHub can be constructed in a thread without a running loop.
        self._self_check_lock: Optional[asyncio.Lock] = None

        env_skip = os.environ.get("QA_MEMORY_SKIP_STARTUP_CHECK", "").strip() == "1"
        self._skip_startup_check = bool(skip_startup_check or env_skip)

        logger.info(
            "KnowledgeHub initialized (milvus=%s, collections=[%s, %s, %s], self_check=%s)",
            milvus_uri,
            COL_EXECUTION_LOG,
            COL_KNOWLEDGE,
            COL_CONCLUSIONS,
            "skipped" if self._skip_startup_check else "pending",
        )

        if self._skip_startup_check:
            # Populate the dict so callers/inspectors see a deterministic shape.
            for col in (COL_EXECUTION_LOG, COL_KNOWLEDGE, COL_CONCLUSIONS):
                self.startup_self_check[col] = {
                    "insert": None,
                    "query_count": None,
                    "delete_count": None,
                    "error": None,
                    "skipped": True,
                }
            # Signal "done" so ensure_self_check() returns immediately.
            self._self_check_done_flag = True

    # ------------------------------------------------------------------
    # Per-collection counter helpers
    # ------------------------------------------------------------------
    def _knowledge_to_collection(self, knowledge: Any) -> Optional[str]:
        """Map one of the three Knowledge instances back to its collection name.

        Returns None for unknown instances (e.g. facade_knowledge) so callers
        can skip counter updates without erroring.
        """
        if knowledge is self.exec_log_knowledge:
            return COL_EXECUTION_LOG
        if knowledge is self.distill_knowledge:
            return COL_KNOWLEDGE
        if knowledge is self.conclusion_knowledge:
            return COL_CONCLUSIONS
        return None

    def _bump(self, collection: Optional[str], key: str) -> None:
        if collection is None or collection not in self._stats:
            return
        with self._stats_lock:
            self._stats[collection][key] = self._stats[collection].get(key, 0) + 1

    def get_stats(self) -> Dict[str, Dict[str, int]]:
        """Return a deep copy of per-collection write/delete counters."""
        with self._stats_lock:
            return copy.deepcopy(self._stats)

    # Regex fragments that identify an "embedding-side" failure inside a
    # Milvus insert. We match on class name AND message because Agno's
    # OpenAIEmbedder swallows the upstream 429 into a silent None, which
    # only surfaces later as the pymilvus "nil vector" / "vector field"
    # error. Keeping this loose is intentional — see design.md Decision 3.
    _EMBED_FAILURE_PATTERNS: tuple = (
        "TokenLimit",
        "RateLimit",
        "429",
        "Too Many Requests",
        "nil vector",
        "vector field",
        "empty vector",
    )

    # Post-insert verify-by-query parameters. After ainsert() returns we
    # query Milvus for the row we just inserted (identified by a
    # writer-minted `__insert_id` we stash in meta_data). Agno's Knowledge
    # layer catches MilvusException internally and does NOT re-raise, so
    # the query is our only authoritative signal that the row actually
    # landed. Retries absorb Milvus eventual consistency (same pattern as
    # the startup self-check).
    _VERIFY_RETRIES: int = 3
    _VERIFY_BACKOFF_S: float = 1.0

    @classmethod
    def _classify_failure(cls, exc: BaseException) -> str:
        """Classify a safe_insert exception as ``"embedding"`` or ``"milvus"``.

        Walks the exception chain (``__cause__`` / ``__context__``) so a
        rate-limit raised by the embedder and wrapped by the Milvus client
        is still correctly attributed to the embedder.
        """
        seen: set[int] = set()
        cur: Optional[BaseException] = exc
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            haystack = f"{type(cur).__name__}: {cur}"
            for needle in cls._EMBED_FAILURE_PATTERNS:
                if needle in haystack:
                    return "embedding"
            # Walk both __cause__ (explicit raise-from) and __context__
            # (implicit re-raise) so we don't miss the real culprit.
            cur = cur.__cause__ or cur.__context__
        return "milvus"

    @classmethod
    def _text_matches_embed_failure(cls, text: str) -> bool:
        """Return True if ``text`` contains any embedder-failure pattern."""
        if not text:
            return False
        return any(needle in text for needle in cls._EMBED_FAILURE_PATTERNS)

    async def safe_insert(
        self,
        knowledge: Any,
        *,
        text_content: str,
        metadata: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
    ) -> bool:
        """Insert a document into a Knowledge instance with degradation.

        Contract
        --------
        - ``True``  → the row IS in Milvus (verified by post-insert query).
        - ``False`` → the row is NOT in Milvus; ``_stats`` has been
          updated with the appropriate failure counter(s).

        Failure-detection strategy (see design.md Decision 3)
        ----------------------------------------------------
        Agno 2.x's ``Knowledge._ahandle_vector_db_insert`` catches
        MilvusException itself, logs ``Error upserting document``, marks
        ``content.status = FAILED``, and returns normally. ``ainsert``
        therefore does NOT raise on the most important failure path we
        care about (rate-limited embedder → nil vector → Milvus rejects).
        To catch this, we:

        1. Stamp a writer-minted ``__insert_id`` into ``metadata`` so we
           can query the exact row back.
        2. Attach a one-shot ``logging.Handler`` to Agno's loggers during
           the call to capture any "Error upserting document" /
           rate-limit / nil-vector log record.
        3. After ``ainsert`` returns, issue ``client.query(filter=
           meta_data["__insert_id"] == <id>)`` with up to
           ``_VERIFY_RETRIES`` attempts to absorb Milvus eventual
           consistency.
        4. If the query never finds the row: the insert failed. Classify
           as ``embedding_failures`` if the captured log matched an
           embedder pattern, else as generic ``inserts_failed``.
        """
        collection = self._knowledge_to_collection(knowledge)

        # Stamp a unique id into metadata so the post-insert query is
        # deterministic even if the caller's session_id is reused across
        # concurrent writes.
        insert_id = uuid.uuid4().hex
        effective_metadata: Dict[str, Any] = dict(metadata) if metadata else {}
        effective_metadata["__insert_id"] = insert_id

        insert_kwargs: Dict[str, Any] = {
            "text_content": text_content,
            "metadata": effective_metadata,
        }
        if name:
            insert_kwargs["name"] = name

        # Attach a log-capturing handler to Agno's loggers during the
        # call. Agno logs the upsert error via its own logger tree; we
        # hook into the root logger so we catch whatever name Agno uses.
        capture = _EmbedFailureLogCapture(self._EMBED_FAILURE_PATTERNS)
        root_logger = logging.getLogger()
        root_logger.addHandler(capture)
        try:
            try:
                await knowledge.ainsert(**insert_kwargs)
            except Exception as exc:
                # ainsert DID raise — rare, but possible (config errors,
                # schema mismatches that pre-empt Agno's own try/except).
                # Use the classic exception-classification path.
                kind = self._classify_failure(exc)
                if kind == "embedding":
                    logger.error(
                        "KnowledgeHub: embedding failed for %s "
                        "(collection=%s, insert_id=%s) — ainsert raised",
                        getattr(knowledge, "name", "?"),
                        collection,
                        insert_id,
                        exc_info=True,
                    )
                    self._bump(collection, "embedding_failures")
                else:
                    logger.error(
                        "KnowledgeHub: Milvus write failed for %s "
                        "(collection=%s, insert_id=%s) — ainsert raised",
                        getattr(knowledge, "name", "?"),
                        collection,
                        insert_id,
                        exc_info=True,
                    )
                self._bump(collection, "inserts_failed")
                return False
        finally:
            root_logger.removeHandler(capture)

        # ainsert returned without raising. Verify the row actually
        # landed. If the capture saw an embedder-error log line during
        # the call, we already know Agno swallowed an embedding failure.
        embedder_error_seen = capture.hit

        landed = await self._verify_row_landed(collection, insert_id)
        if landed:
            logger.debug(
                "KnowledgeHub: inserted into %s (insert_id=%s, text_len=%d)",
                getattr(knowledge, "name", "?"), insert_id, len(text_content),
            )
            self._bump(collection, "inserts_ok")
            return True

        # Row did NOT land. Classify.
        if embedder_error_seen:
            logger.error(
                "KnowledgeHub: embedding failed for %s "
                "(collection=%s, insert_id=%s) — post-insert query found "
                "no row, and an embedder-error log was captured during ainsert. "
                "Captured signal: %s",
                getattr(knowledge, "name", "?"),
                collection,
                insert_id,
                capture.sample,
            )
            self._bump(collection, "embedding_failures")
        else:
            logger.error(
                "KnowledgeHub: Milvus write failed for %s "
                "(collection=%s, insert_id=%s) — post-insert query found "
                "no row, no embedder-error log captured. Possible causes: "
                "schema mismatch, connection reset, or an Agno-internal "
                "swallowed exception not matching known embedder patterns.",
                getattr(knowledge, "name", "?"),
                collection,
                insert_id,
            )
        self._bump(collection, "inserts_failed")
        return False

    async def _verify_row_landed(
        self,
        collection: Optional[str],
        insert_id: str,
    ) -> bool:
        """Query Milvus for the row stamped with ``__insert_id`` == ``insert_id``.

        Retries up to ``_VERIFY_RETRIES`` times with
        ``_VERIFY_BACKOFF_S`` seconds between attempts. Returns True at
        the first non-empty response. If the collection is unknown (e.g.
        facade_knowledge) or pymilvus isn't importable, returns True
        conservatively (we don't want to flip previously-ok writes to
        failed just because the verify step could not run).
        """
        if collection is None:
            return True
        client = self._get_milvus_client()
        if client is None:
            logger.warning(
                "KnowledgeHub: verify skipped — could not open MilvusClient"
            )
            return True

        filter_expr = f'meta_data["__insert_id"] == "{insert_id}"'
        for attempt in range(1, self._VERIFY_RETRIES + 1):
            try:
                rows = client.query(
                    collection_name=collection,
                    filter=filter_expr,
                    output_fields=["id"],
                    limit=1,
                )
                if rows:
                    return True
            except Exception:
                logger.debug(
                    "KnowledgeHub: verify query raised on attempt %d",
                    attempt,
                    exc_info=True,
                )
            if attempt < self._VERIFY_RETRIES:
                await asyncio.sleep(self._VERIFY_BACKOFF_S)
        return False

    def safe_search(
        self,
        knowledge: Any,
        *,
        query: str,
        num_documents: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Search a Knowledge instance with degradation.

        Args:
            knowledge: One of the three Knowledge instances.
            query: Search query text.
            num_documents: Maximum number of results to return.
            filters: Optional metadata filters.

        Returns:
            List of document dicts, or empty list if Milvus is unavailable.
        """
        try:
            search_kwargs: Dict[str, Any] = {
                "query": query,
                "max_results": num_documents,
            }
            if filters:
                search_kwargs["filters"] = filters

            docs = knowledge.search(**search_kwargs)
            if docs is None:
                return []
            # Convert Document objects to dicts
            return [doc.to_dict() if hasattr(doc, "to_dict") else doc for doc in docs]
        except Exception as e:
            logger.warning(
                "KnowledgeHub: Milvus search failed for %s, returning empty: %s",
                getattr(knowledge, "name", "?"), e,
            )
            return []

    def _get_milvus_client(self) -> Any:
        """Return the process-reusable MilvusClient, creating it lazily.

        Returns None if pymilvus is unavailable or the connection cannot be
        established (callers degrade per their own best-effort semantics).
        """
        with self._client_lock:
            if self._milvus_client is None:
                try:
                    from pymilvus import MilvusClient

                    self._milvus_client = MilvusClient(uri=self._milvus_uri)
                except Exception:
                    logger.warning(
                        "KnowledgeHub: failed to create shared MilvusClient at %s",
                        self._milvus_uri,
                        exc_info=True,
                    )
                    return None
            return self._milvus_client

    def _drop_milvus_client(self) -> None:
        """Drop a (possibly stale) shared client; next call rebuilds it."""
        with self._client_lock:
            self._milvus_client = None

    async def safe_delete_by_session(self, session_id: str) -> dict:
        """Delete all vectors in the three Milvus collections for a given session_id.

        Uses pymilvus MilvusClient.delete() with a filter expression on the
        ``meta_data.session_id`` dynamic field (note the underscore — Agno's
        Milvus adapter stores user metadata under ``meta_data``, not
        ``metadata``). The filter expression is built by
        :func:`memory.collections.build_session_filter` to guarantee a single
        source of truth for the field name.

        Returns:
            Dict with collection names as keys and deleted count (or -1 on error) as values.
            Example: {"qa_execution_log": 5, "qa_knowledge": 2, "qa_conclusions": 1}
        """
        # pymilvus is sync; the whole 3-collection delete runs in a worker thread.
        return await asyncio.to_thread(self._delete_session_vectors_sync, session_id)

    def _delete_session_vectors_sync(self, session_id: str) -> dict:
        """Sync worker for :meth:`safe_delete_by_session` (runs off-loop)."""
        from memory.collections import COL_CONCLUSIONS, COL_EXECUTION_LOG, COL_KNOWLEDGE

        results = {}
        # NOTE: Agno stores metadata under the dynamic field `meta_data` (with
        # underscore). Never hand-roll this filter — use build_session_filter
        # from memory.collections so the field name stays single-sourced.
        filter_expr = build_session_filter(session_id)
        logger.debug(
            "safe_delete_by_session: session=%s filter_expr=%r",
            session_id, filter_expr,
        )

        client = self._get_milvus_client()
        if client is None:
            logger.error(
                "safe_delete_by_session: failed to create MilvusClient",
            )
            # All three collections count as "failed" for stats purposes.
            for col in (COL_EXECUTION_LOG, COL_KNOWLEDGE, COL_CONCLUSIONS):
                self._bump(col, "deletes_failed")
            return {COL_EXECUTION_LOG: -1, COL_KNOWLEDGE: -1, COL_CONCLUSIONS: -1}

        for col in (COL_EXECUTION_LOG, COL_KNOWLEDGE, COL_CONCLUSIONS):
            deleted_ok = False
            for attempt in (1, 2):
                try:
                    res = client.delete(collection_name=col, filter=filter_expr)
                    deleted = res.get("delete_count", 0) if isinstance(res, dict) else 0
                    results[col] = deleted
                    logger.debug(
                        "safe_delete_by_session: deleted %d vectors from %s (session=%s)",
                        deleted, col, session_id,
                    )
                    self._bump(col, "deletes_ok")
                    deleted_ok = True
                    break
                except Exception:
                    if attempt == 1:
                        # Shared connection may have gone stale — drop it,
                        # rebuild once and retry this collection.
                        logger.warning(
                            "safe_delete_by_session: collection %s delete failed on "
                            "attempt 1, rebuilding MilvusClient and retrying once "
                            "(session=%s)",
                            col, session_id, exc_info=True,
                        )
                        self._drop_milvus_client()
                        client = self._get_milvus_client()
                        if client is None:
                            break
                    else:
                        logger.error(
                            "safe_delete_by_session: failed for collection %s (session=%s)",
                            col, session_id,
                            exc_info=True,
                        )
            if not deleted_ok:
                results[col] = -1
                self._bump(col, "deletes_failed")

        return results

    # ------------------------------------------------------------------
    # Startup self-check
    # ------------------------------------------------------------------
    async def ensure_self_check(self) -> Dict[str, Dict[str, Any]]:
        """Drive the startup self-check in the CURRENT async scope.

        The implementation intentionally does NOT use a detached
        ``loop.create_task`` — under FastAPI/Starlette lifespan, AnyIO's
        cancel scope can and does cancel detached tasks mid-await (observed:
        self-check silently aborting between insert and query retry). By
        running the probe inline, we inherit the caller's scope so it either
        completes or fails with a visible exception.

        Safe to call multiple times. The first call drives the probe; every
        later call returns the already-populated result immediately. A lock
        serialises concurrent first-callers so only one probe runs.
        """
        # Fast path: already done.
        if self._self_check_done_flag:
            return dict(self.startup_self_check)

        # Lazily create the lock — __init__ cannot always create one because
        # it may run outside a running event loop (see threaded init in
        # get_knowledge_hub).
        if self._self_check_lock is None:
            self._self_check_lock = asyncio.Lock()

        async with self._self_check_lock:
            # Re-check under the lock in case another coroutine already ran it.
            if self._self_check_done_flag:
                return dict(self.startup_self_check)

            if self._skip_startup_check:
                # Should already be marked done in __init__, but be defensive.
                self._self_check_done_flag = True
                return dict(self.startup_self_check)

            await self._startup_self_check_impl()
            return dict(self.startup_self_check)

    # Self-check query retry policy — Milvus is eventually consistent between
    # insert() and query(filter=...). We retry up to _SELF_CHECK_QUERY_RETRIES
    # times with _SELF_CHECK_QUERY_BACKOFF_S seconds between attempts, so the
    # delete buffer / segment flush has a chance to catch up before we call
    # the probe "invisible". Same pattern as scripts/milvus_housekeeping.py's
    # _RESIDUAL_GRACE_SECONDS.
    _SELF_CHECK_QUERY_RETRIES: int = 5
    _SELF_CHECK_QUERY_BACKOFF_S: float = 1.0

    async def _startup_self_check_impl(self) -> Dict[str, Dict[str, Any]]:
        """Insert → query (with retry) → delete a throwaway probe for each
        live collection.

        Populates :attr:`startup_self_check` and sets :attr:`_self_check_done_flag`.
        Never raises to callers: failures are reported per-collection with a
        populated ``error`` field.
        """
        startup_id = f"__startup_{uuid.uuid4().hex[:12]}"
        logger.info("KnowledgeHub self-check start (id=%s)", startup_id)

        targets = (
            (COL_EXECUTION_LOG, self.exec_log_knowledge),
            (COL_KNOWLEDGE, self.distill_knowledge),
            (COL_CONCLUSIONS, self.conclusion_knowledge),
        )

        # Shared pymilvus client for authoritative query checks.
        client = self._get_milvus_client()
        if client is None:
            logger.error(
                "KnowledgeHub self-check: failed to create MilvusClient at %s",
                self._milvus_uri,
            )

        for col_name, knowledge in targets:
            entry: Dict[str, Any] = {
                "insert": False,
                "query_count": 0,
                "delete_count": 0,
                "error": None,
                "query_attempts": 0,
            }
            try:
                ok = await self.safe_insert(
                    knowledge,
                    text_content=f"__startup_probe_{startup_id}",
                    metadata={
                        "session_id": startup_id,
                        "probe_kind": "startup",
                        "collection": col_name,
                    },
                )
                entry["insert"] = bool(ok)

                if not ok:
                    # If insert returned False, safe_insert has already logged
                    # the root cause at ERROR. Record the symptom and move on.
                    entry["error"] = "insert returned False (see prior ERROR)"
                elif client is not None:
                    # Retry the query a few times — Milvus is eventually
                    # consistent between Agno's ainsert() and a direct
                    # client.query(filter=...). Break out as soon as we see
                    # any matching row.
                    last_exc: Optional[BaseException] = None
                    for attempt in range(1, self._SELF_CHECK_QUERY_RETRIES + 1):
                        entry["query_attempts"] = attempt
                        try:
                            rows = client.query(
                                collection_name=col_name,
                                filter=build_session_filter(startup_id),
                                output_fields=["id"],
                                limit=10,
                            )
                            n = len(rows) if rows is not None else 0
                            entry["query_count"] = n
                            if n > 0:
                                last_exc = None
                                break
                        except Exception as exc:
                            last_exc = exc
                        if attempt < self._SELF_CHECK_QUERY_RETRIES:
                            await asyncio.sleep(self._SELF_CHECK_QUERY_BACKOFF_S)
                    if last_exc is not None:
                        entry["error"] = (
                            f"query raised on attempt "
                            f"{entry['query_attempts']}: "
                            f"{type(last_exc).__name__}: {last_exc}"
                        )
                        logger.error(
                            "KnowledgeHub self-check: query kept raising for %s",
                            col_name,
                            exc_info=last_exc,
                        )
                    elif entry["query_count"] == 0:
                        entry["error"] = (
                            f"query returned 0 rows after "
                            f"{entry['query_attempts']} attempts "
                            f"({self._SELF_CHECK_QUERY_RETRIES * self._SELF_CHECK_QUERY_BACKOFF_S:.0f}s total)"
                        )
                else:
                    entry["error"] = "milvus client unavailable"
            except Exception as exc:
                entry["error"] = f"self-check raised: {type(exc).__name__}: {exc}"

            # Record entry before cleanup so failures are visible even if
            # delete throws.
            self.startup_self_check[col_name] = entry

            if not entry["insert"] or entry["query_count"] == 0:
                logger.error(
                    "KnowledgeHub startup self-check failed for collection %s "
                    "(insert=%s, query_count=%d, attempts=%d, error=%s)",
                    col_name,
                    entry["insert"],
                    entry["query_count"],
                    entry.get("query_attempts", 0),
                    entry["error"],
                )

        # Cleanup: delete the probe across all three collections.
        try:
            delete_results = await self.safe_delete_by_session(startup_id)
            for col_name, _ in targets:
                dc = delete_results.get(col_name, -1)
                if col_name in self.startup_self_check:
                    self.startup_self_check[col_name]["delete_count"] = dc
        except Exception:
            logger.error(
                "KnowledgeHub self-check: delete cleanup failed (id=%s)",
                startup_id,
                exc_info=True,
            )

        self._self_check_done_flag = True
        logger.info(
            "KnowledgeHub self-check complete: %s",
            {
                col: {
                    "insert": r.get("insert"),
                    "query_count": r.get("query_count"),
                    "attempts": r.get("query_attempts"),
                    "delete_count": r.get("delete_count"),
                    "error": r.get("error"),
                }
                for col, r in self.startup_self_check.items()
            },
        )
        return dict(self.startup_self_check)


def hub_retriever(
    agent: Any = None,
    query: str = "",
    num_documents: Optional[int] = None,
    filters: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    """Custom knowledge retriever that searches all three Knowledge instances.

    This function is designed to be passed as ``knowledge_retriever`` to Agno Agent.
    When set, Agno's ``get_relevant_docs_from_knowledge()`` calls this function
    instead of the default ``Knowledge.search()``, providing multi-collection routing.

    Search strategy:
        - distill_knowledge (qa_knowledge): top_k = num_documents or 5
        - exec_log_knowledge (qa_execution_log): top_k = num_documents or 5
        - conclusion_knowledge (qa_conclusions): top_k = min(3, num_documents or 3)
        - Results are merged and returned as List[Dict]

    Args:
        agent: The Agno Agent instance (optional, passed by Agno via reflection).
        query: The search query text.
        num_documents: Maximum results per collection (default 5).
        filters: Optional metadata filters to apply.
        **kwargs: Additional kwargs (ignored, for forward compatibility).

    Returns:
        Merged list of document dicts from all three collections.
    """
    hub = get_knowledge_hub()
    if hub is None:
        logger.warning("KnowledgeHub not initialized — returning empty results")
        return []

    top_k = num_documents or 5
    conclusion_k = min(3, top_k)

    results: List[Dict[str, Any]] = []

    # Search distilled knowledge (highest priority)
    distill_docs = hub.safe_search(
        hub.distill_knowledge,
        query=query,
        num_documents=top_k,
        filters=filters,
    )
    for doc in distill_docs:
        doc["_source"] = "qa_knowledge"
    results.extend(distill_docs)

    # Search execution logs
    exec_docs = hub.safe_search(
        hub.exec_log_knowledge,
        query=query,
        num_documents=top_k,
        filters=filters,
    )
    for doc in exec_docs:
        doc["_source"] = "qa_execution_log"
    results.extend(exec_docs)

    # Search conclusions
    conclusion_docs = hub.safe_search(
        hub.conclusion_knowledge,
        query=query,
        num_documents=conclusion_k,
        filters=filters,
    )
    for doc in conclusion_docs:
        doc["_source"] = "qa_conclusions"
    results.extend(conclusion_docs)

    logger.debug(
        "hub_retriever: query=%s, results=%d (knowledge=%d, exec=%d, conclusions=%d)",
        query[:60], len(results),
        len(distill_docs), len(exec_docs), len(conclusion_docs),
    )

    return results


# Timeout for KnowledgeHub initialization (Milvus connection)
_INIT_TIMEOUT_SECONDS = 10


class _EmbedFailureLogCapture(logging.Handler):
    """One-shot log handler that trips when it sees an embedder-error line.

    Agno's ``Knowledge._ahandle_vector_db_insert`` catches
    ``MilvusException`` internally and emits a log line like::

        ERROR  Error upserting document: <MilvusException: (code=1100,
        message=float vector field 'vector' is illegal, array type
        mismatch: invalid parameter[expected=need float vector][actual=got
        nil])>

    The upstream embedder typically logs its own line first (``Error
    getting embedding: Error code: 429 ...``). By attaching this handler
    to the root logger during ``safe_insert``'s ainsert call we capture
    whichever signal appears first, without depending on Agno to re-raise.

    Not thread-safe in the sense that multiple concurrent ``safe_insert``
    calls will each attach+detach their own handler on the root logger —
    that's fine; each handler only observes records emitted during its
    own attached window, and Python's logging module handles concurrent
    handler lists under its internal lock.
    """

    def __init__(self, patterns: tuple) -> None:
        super().__init__(level=logging.WARNING)
        self._patterns = patterns
        self.hit: bool = False
        self.sample: str = ""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401
        if self.hit:
            return
        try:
            msg = record.getMessage()
        except Exception:
            msg = record.msg if isinstance(record.msg, str) else ""
        for needle in self._patterns:
            if needle in msg:
                self.hit = True
                # Keep a short sample of the log line for the post-mortem
                # ERROR log emitted by safe_insert.
                self.sample = (msg[:200] + "…") if len(msg) > 200 else msg
                return


def get_knowledge_hub() -> Optional[KnowledgeHub]:
    """Get or create the singleton KnowledgeHub instance.

    Reads Milvus and OpenAI configuration from settings.
    Uses a thread with timeout to prevent blocking if Milvus is unreachable
    (Knowledge.__post_init__ calls vector_db.create() which connects synchronously).

    Returns:
        KnowledgeHub instance, or None if initialization fails or times out.
    """
    global _hub_instance, _hub_init_attempted

    if _hub_instance is not None:
        return _hub_instance

    # Avoid repeated 10s timeout waits when Milvus is known to be unreachable.
    # Reset only on process restart.
    if _hub_init_attempted:
        return None
    _hub_init_attempted = True

    try:
        from core.config import settings

        if not settings.milvus_enabled:
            logger.info("Milvus disabled — KnowledgeHub not initialized")
            return None

        from agno.knowledge.embedder.openai import OpenAIEmbedder

        milvus_uri = f"http://{settings.milvus_host}:{settings.milvus_port}"

        raw_embedder = OpenAIEmbedder(
            id=settings.embedding_model,
            dimensions=1536,
            api_key=settings.embedding_api_key_resolved,
            base_url=settings.embedding_base_url_resolved,
        )

        from memory.embedder_cache import EmbedderCache

        embedder = EmbedderCache(raw_embedder)

        # Use thread + timeout because Knowledge.__post_init__ connects to
        # Milvus synchronously (calls vector_db.exists() + vector_db.create()).
        # If Milvus is unreachable, this would block indefinitely.
        init_result: Dict[str, Any] = {"hub": None, "error": None}

        def _init():
            try:
                init_result["hub"] = KnowledgeHub(
                    milvus_uri=milvus_uri,
                    embedder=embedder,
                )
            except Exception as e:
                init_result["error"] = e

        t = threading.Thread(target=_init, daemon=True)
        t.start()
        t.join(timeout=_INIT_TIMEOUT_SECONDS)

        if t.is_alive():
            logger.warning(
                "KnowledgeHub initialization timed out after %ds "
                "(Milvus at %s likely unreachable). "
                "Agent will run without knowledge search capability.",
                _INIT_TIMEOUT_SECONDS, milvus_uri,
            )
            return None

        if init_result["error"]:
            raise init_result["error"]

        _hub_instance = init_result["hub"]

        # NOTE: We deliberately do NOT kick off the self-check here with
        # `loop.create_task(...)`. Under FastAPI/Starlette lifespan, AnyIO
        # manages a strict cancel scope around startup; a detached
        # create_task can be cancelled mid-await (we've seen the self-check
        # abort silently between the insert and the query retry loop).
        #
        # The lifespan in api/server.py awaits `hub.ensure_self_check()`
        # directly, which now drives the self-check from inside the
        # supervised scope (see `ensure_self_check` below). CLI callers that
        # care about self-check results also go through `ensure_self_check`.
        return _hub_instance

    except Exception as e:
        logger.error("Failed to initialize KnowledgeHub: %s", e, exc_info=True)
        return None


async def delete_session_vectors(session_id: str) -> dict:
    """Delete all Milvus vectors for a session. Returns empty dict if hub unavailable."""
    hub = get_knowledge_hub()
    if hub is None:
        logger.warning("delete_session_vectors: KnowledgeHub not available, skipping Milvus cleanup")
        return {}
    return await hub.safe_delete_by_session(session_id)
