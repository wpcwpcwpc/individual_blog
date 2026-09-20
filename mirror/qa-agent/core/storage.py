"""
QA Agent System — Storage Factory

Returns the appropriate Agno Storage backend (MongoDb, SqliteDb, or PostgresDb)
based on the configuration.

Compatible with Agno 2.5.14+ (uses agno.db module)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from core.config import QAAgentSettings, StorageBackend, settings

logger = logging.getLogger(__name__)

# 会话流事件通道：upsert 失败时推送系统级告警（用户可见，不再静默）。
# 背景：agno upsert_session 失败只 log_error（mongo.py 静默吞），run 不落库
# → 审批批准后 acontinue_run 从 DB 找不到 run → 续跑断裂且全程无感知
# （2026-08-31 真机：4 个 run 永久丢失，重启服务后恢复）。
_event_logger = logging.getLogger("qa_agent.events")


def _define_mongo_db_with_approvals():
    """惰性定义 ``MongoDbWithApprovals`` 子类（避免 pymongo 缺失时 import 崩）。

    agno 2.5.17 的 ``MongoDb`` 未实现 approval 7 方法（BaseDb stub，
    raise NotImplementedError）——审批记录静默不落库、续跑报
    "No approval record found"。本子类按 SqliteDb 语义补齐（pymongo，
    ``agno_approvals`` 集合）。spike 验证：spike_approval_demo.py --db mongo-patch。
    """
    from pymongo import ReturnDocument

    from agno.db.mongo import MongoDb

    class _MongoDbWithApprovals(MongoDb):
        """MongoDb + approvals 持久化（agno 2.5.17 缺失实现）。"""

        def _approvals_col(self):
            return self.database[self.approvals_table_name]

        def upsert_session(self, session: Any, **kwargs: Any) -> Any:
            """upsert 失败显式告警（run 丢失 = 审批续跑断裂，不可静默）。

            agno 原实现对异常只 log_error 一行，调用方（cleanup_and_store /
            pause handler）继续走完 run 生命周期 → run 永久不落库、审批
            批准后 acontinue_run 报 RunNotFoundError。此处补完整栈日志 +
            事件流告警后原样抛出（保持 agno 上层语义不变）。
            """
            try:
                return super().upsert_session(session, **kwargs)
            except Exception:
                sid = getattr(session, "session_id", "") or ""
                n_runs = len(getattr(session, "runs", None) or [])
                logger.exception(
                    "❌ agno session upsert FAILED (session=%s runs_in_mem=%d) "
                    "— 本次 run 将不落库，审批批准后续跑会断裂！",
                    sid, n_runs,
                )
                _event_logger.error(
                    "\n🚨 EVENT [storage_upsert_failed]: session=%s runs_in_mem=%d "
                    "— 会话保存失败，本轮 run 不落库，审批后续跑将断裂（详见服务端日志）",
                    sid, n_runs,
                )
                raise

        def create_approval(self, approval_data: Dict[str, Any]) -> Dict[str, Any]:
            data = {**approval_data}
            now = int(time.time())
            data.setdefault("created_at", now)
            data.setdefault("updated_at", now)
            self._approvals_col().insert_one(dict(data))
            return data

        def get_approval(self, approval_id: str) -> Optional[Dict[str, Any]]:
            return self._approvals_col().find_one({"id": approval_id}, {"_id": 0})

        def get_approvals(
            self,
            status: Optional[str] = None,
            source_type: Optional[str] = None,
            approval_type: Optional[str] = None,
            pause_type: Optional[str] = None,
            agent_id: Optional[str] = None,
            team_id: Optional[str] = None,
            workflow_id: Optional[str] = None,
            user_id: Optional[str] = None,
            schedule_id: Optional[str] = None,
            run_id: Optional[str] = None,
            limit: int = 100,
            page: int = 1,
        ) -> Tuple[List[Dict[str, Any]], int]:
            query: Dict[str, Any] = {}
            for key, val in (
                ("status", status), ("source_type", source_type),
                ("approval_type", approval_type), ("pause_type", pause_type),
                ("agent_id", agent_id), ("team_id", team_id),
                ("workflow_id", workflow_id), ("user_id", user_id),
                ("schedule_id", schedule_id), ("run_id", run_id),
            ):
                if val is not None:
                    query[key] = val
            col = self._approvals_col()
            total = col.count_documents(query)
            cursor = (
                col.find(query, {"_id": 0})
                .sort("created_at", -1)
                .skip((page - 1) * limit)
                .limit(limit)
            )
            return list(cursor), total

        def update_approval(
            self, approval_id: str, expected_status: Optional[str] = None, **kwargs: Any
        ) -> Optional[Dict[str, Any]]:
            query: Dict[str, Any] = {"id": approval_id}
            if expected_status is not None:
                query["status"] = expected_status
            kwargs["updated_at"] = int(time.time())
            return self._approvals_col().find_one_and_update(
                query, {"$set": kwargs},
                return_document=ReturnDocument.AFTER,
                projection={"_id": 0},
            )

        def delete_approval(self, approval_id: str) -> bool:
            return self._approvals_col().delete_one({"id": approval_id}).deleted_count > 0

        def get_pending_approval_count(self, user_id: Optional[str] = None) -> int:
            query: Dict[str, Any] = {"status": "pending"}
            if user_id is not None:
                query["user_id"] = user_id
            return self._approvals_col().count_documents(query)

        def update_approval_run_status(self, run_id: str, run_status: Any) -> int:
            res = self._approvals_col().update_many(
                {"run_id": run_id},
                {"$set": {
                    "run_status": getattr(run_status, "value", run_status),
                    "updated_at": int(time.time()),
                }},
            )
            return res.modified_count

    return _MongoDbWithApprovals


#: 惰性缓存（pymongo 存在时定义一次）
_mongo_approvals_cls = None


def get_mongo_db_with_approvals_class():
    """返回 ``MongoDbWithApprovals`` 类（惰性定义 + 缓存）。"""
    global _mongo_approvals_cls
    if _mongo_approvals_cls is None:
        _mongo_approvals_cls = _define_mongo_db_with_approvals()
    return _mongo_approvals_cls


def get_storage(config: QAAgentSettings | None = None) -> Any:
    """Create an Agno storage instance based on config.

    Args:
        config: Settings override. Uses global ``settings`` if None.

    Returns:
        An Agno storage instance (MongoDb, SqliteDb, or PostgresDb).
    """
    cfg = config or settings

    # --- MongoDB (default) ---
    if cfg.storage_backend == StorageBackend.MONGO:
        try:
            logger.info(
                "Using MongoDB storage: %s (db=qa_agent_db, sessions=%s)",
                cfg.mongo_uri[:40] + "...",
                cfg.agno_mongo_session_collection,
            )
            # MongoDbWithApprovals：agno 2.5.17 Mongo 缺 approval 实现，
            # 审批门依赖完整 7 方法，故 MONGO 分支一律用子类。
            cls = get_mongo_db_with_approvals_class()
            return cls(
                db_url=cfg.mongo_uri,
                db_name="qa_agent_db",
                session_collection=cfg.agno_mongo_session_collection,
                memory_collection=cfg.agno_mongo_memory_collection,
                traces_collection="agno_traces",
                spans_collection="agno_spans",
            )
        except ImportError:
            logger.warning(
                "MongoDb not available (missing pymongo?). Falling back to SQLite."
            )

    # --- PostgreSQL ---
    if cfg.storage_backend == StorageBackend.POSTGRES:
        if not (cfg.storage_dsn or "").strip():
            raise RuntimeError(
                "STORAGE_BACKEND=postgres but STORAGE_DSN is empty. Set it in "
                ".env (see .env.example) — there is no shipped default, so that "
                "no password literal is reused across deployments."
            )
        try:
            from agno.db.postgres import PostgresDb
            logger.info("Using PostgreSQL storage: %s", cfg.storage_dsn[:40] + "...")
            return PostgresDb(
                db_url=cfg.storage_dsn,
                session_table="qa_agent_sessions",
            )
        except ImportError:
            logger.warning("PostgresDb not available (missing psycopg?). Falling back to SQLite.")

    # --- SQLite (fallback) ---
    from agno.db.sqlite import SqliteDb
    db_path = str(cfg.sqlite_path_resolved)
    logger.info("Using SQLite storage: %s", db_path)
    return SqliteDb(
        db_file=db_path,
        session_table="qa_agent_sessions",
    )
