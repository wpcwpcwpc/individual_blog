"""
Custom tracing exporter compatible with MongoDB 4.0 (no pipeline-style updates).

The built-in agno `DatabaseSpanExporter` uses `upsert_trace()` which relies on
MongoDB 4.2+ aggregation pipeline updates. Our MongoDB is 4.0.9, so we override
the trace upsert with a simple $set operation.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Sequence, Tuple

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from agno.tracing.schemas import Span, create_trace_from_spans

logger = logging.getLogger(__name__)


def dedupe_update_paths(set_on_insert: Dict, other_ops: Dict) -> None:
    """剔除 $setOnInsert 中与其他更新操作符（$set / $addToSet / …）重叠的键（原地修改）。

    同一路径出现在多个更新操作符会被 Mongo 拒绝（code 40
    "Updating the path 'x' would create a conflict at 'x'"）——parse 阶段
    即拒，与文档是否已存在无关。另一操作符语义优先（$set 同样生效；
    $addToSet 在 insert 路径会自建数组），剔除不损失信息。
    """
    for k in set(set_on_insert) & set(other_ops):
        set_on_insert.pop(k)


def compute_token_increment(
    batch_token_spans: Dict[str, Tuple[int, int, int]],
    seen_ids: Sequence[str],
) -> Tuple[int, int, int, List[str]]:
    """按 span_id 去重计算本批 token 增量。

    Args:
        batch_token_spans: 本批含 token 的 model span，
            {span_id: (prompt, completion, cache_read)}。
        seen_ids: trace 文档已计入的 span_id 集合（token_span_ids）。

    Returns:
        (prompt 增量, completion 增量, cache_read 增量, 新计入的 span_id 列表)。
        重试同一批次 → span_id 全部命中 seen → 增量为 0（幂等）。
        cache_read 复用 token_span_ids 同一去重集合——与 prompt 同 span 同批。
    """
    seen = set(seen_ids)
    dp = dc = dcr = 0
    new_ids: List[str] = []
    for sid, (p, c, cr) in batch_token_spans.items():
        if sid in seen:
            continue
        seen.add(sid)
        new_ids.append(sid)
        dp += p
        dc += c
        dcr += cr
    return dp, dc, dcr, new_ids


class Mongo4SpanExporter(SpanExporter):
    """SpanExporter that works with MongoDB 4.0 (uses simple $set upsert)."""

    def __init__(self, db_url: str, db_name: str, traces_collection: str, spans_collection: str):
        from pymongo import MongoClient

        self._client = MongoClient(db_url)
        self._db = self._client[db_name]
        self._traces_col = self._db[traces_collection]
        self._spans_col = self._db[spans_collection]
        self._shutdown = False

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        if self._shutdown or not spans:
            return SpanExportResult.SUCCESS

        try:
            converted: List[Span] = []
            for otel_span in spans:
                try:
                    converted.append(Span.from_otel_span(otel_span))
                except Exception as e:
                    logger.debug("Failed to convert span: %s", e)
                    continue

            if not converted:
                return SpanExportResult.SUCCESS

            spans_by_trace: Dict[str, List[Span]] = defaultdict(list)
            for s in converted:
                spans_by_trace[s.trace_id].append(s)

            for trace_id, trace_spans in spans_by_trace.items():
                trace = create_trace_from_spans(trace_spans)
                if trace:
                    trace_dict = trace.to_dict()

                    # ── Collect token-bearing model spans in THIS batch ──
                    # 按 span_id 记录，跨批 $inc
                    # 增量累加（旧 $max 只保留最大单批和，长 run 少计 95%+）。
                    # cache_read（llm.token_count.prompt_details.cache_read）
                    # 与 prompt 同 span 提取，复用同一去重集合。
                    batch_token_spans: Dict[str, Tuple[int, int, int]] = {}
                    for s in trace_spans:
                        attrs = s.attributes if hasattr(s, "attributes") else {}
                        p = int(attrs.get("llm.token_count.prompt", 0) or 0)
                        c = int(attrs.get("llm.token_count.completion", 0) or 0)
                        if p or c:
                            cr = int(attrs.get("llm.token_count.prompt_details.cache_read", 0) or 0)
                            batch_token_spans[s.span_id] = (p, c, cr)

                    # ── Scan ALL spans in batch for identity (root may be absent) ──
                    # Previous logic only inspected the batch's root span. When child
                    # spans (model/tool) arrive before the agent/workflow run span,
                    # the root fell back to a model span and agent_type/agent_id
                    # locked to wrong values. Scan every span so a late-arriving
                    # root span can still correct classification.
                    agent_type = "agent"
                    agent_id = trace.agent_id
                    workflow_id = trace.workflow_id
                    session_id = trace.session_id
                    run_id = trace.run_id
                    user_id = trace.user_id
                    for s in trace_spans:
                        attrs = s.attributes if hasattr(s, "attributes") else {}
                        wf_attr = attrs.get("agno.workflow.id") or attrs.get("workflow_id")
                        if wf_attr:
                            agent_type = "workflow"
                            workflow_id = workflow_id or wf_attr
                        an = attrs.get("agent.name") or attrs.get("agno.agent")
                        if an and not agent_id:
                            agent_id = an
                        aid = attrs.get("agno.agent.id")
                        if aid and not agent_id:
                            agent_id = aid
                        # user_id / session_id / run_id may live on agent run
                        # spans (not just root) — scan all so mid-run batches
                        # (root span not yet exported) still capture them.
                        uid = attrs.get("user_id") or attrs.get("agno.user.id") or attrs.get("user.id")
                        if uid and not user_id:
                            user_id = uid
                        sid = attrs.get("session_id") or attrs.get("agno.session.id") or attrs.get("session.id")
                        if sid and not session_id:
                            session_id = sid
                        rid = attrs.get("run_id") or attrs.get("agno.run.id")
                        if rid and not run_id:
                            run_id = rid

                    # openinference does NOT set agno.workflow.id on workflow run
                    # spans at stream START (only on completion, inside the span's
                    # post-loop code). For long-running workflows, the root span
                    # stays open until completion, so mid-run batches contain only
                    # step/agent/model spans — no workflow identity attr, no root
                    # span. Detect workflows by span-name patterns instead.
                    if agent_type != "workflow":
                        for s in trace_spans:
                            nm = s.name or ""
                            attrs = s.attributes if hasattr(s, "attributes") else {}
                            has_agent_name = bool(attrs.get("agent.name") or attrs.get("agno.agent"))
                            # Workflow run span: "*.arun" / "*.run" WITHOUT agent.name.
                            # (Agent run spans also match "*.arun" but carry agent.name.)
                            if nm.endswith((".arun_stream", ".arun", ".run_stream", ".run")) and not has_agent_name:
                                base = nm.rsplit(".", 1)[0]
                                if "-" in base or "_" in base:
                                    agent_type = "workflow"
                                    workflow_id = workflow_id or base
                                    break
                            # Step run span: "*.aexecute_stream" / "*.aexecute" /
                            # "*.execute_stream" / "*.execute". These only exist
                            # inside workflows, so any match => workflow trace.
                            # workflow_id stays None (step spans don't carry it);
                            # the workflow run span will fill it on completion.
                            if nm.endswith((".aexecute_stream", ".aexecute", ".execute_stream", ".execute")):
                                agent_type = "workflow"
                                break

                    # ── Resolve a stable, human-friendly trace name ──
                    good_name = None
                    name_authoritative = False  # True => $set (clobber); False => $setOnInsert only
                    if agent_type == "workflow" and workflow_id:
                        good_name = workflow_id
                        name_authoritative = True
                    else:
                        for s in trace_spans:
                            attrs = s.attributes if hasattr(s, "attributes") else {}
                            an = attrs.get("agent.name") or attrs.get("agno.agent")
                            if an:
                                good_name = an
                                # Authoritative only if this is the true root
                                # span (parent_span_id is None). A workflow step's
                                # agent run span carries agent.name too, but it is
                                # NOT the trace root — using it would clobber a
                                # prior workflow_id-based name.
                                name_authoritative = not s.parent_span_id
                                break
                            nm = s.name or ""
                            for suf in (".arun_stream", ".arun", ".run_stream", ".run"):
                                if nm.endswith(suf):
                                    good_name = nm[: -len(suf)]
                                    name_authoritative = not s.parent_span_id
                                    break
                            if good_name:
                                break

                    # agent_id resolution: authoritative sources only.
                    # - workflow_id (workflow run span attr or registry) => workflow_id
                    # - agno.agent.id / agent.name on a ROOT span => that agent id
                    # Non-root agent.name (workflow step agent) is NOT authoritative
                    # — using it would clobber a prior workflow_id-based agent_id.
                    resolved_agent_id = None
                    if agent_type == "workflow" and workflow_id:
                        resolved_agent_id = workflow_id
                    else:
                        for s in trace_spans:
                            if s.parent_span_id:
                                continue  # only root span's identity is authoritative
                            attrs = s.attributes if hasattr(s, "attributes") else {}
                            aid = attrs.get("agno.agent.id") or attrs.get("agent.name") or attrs.get("agno.agent")
                            if aid:
                                resolved_agent_id = aid
                                break

                    # ── Build update with monotonic / non-clobbering operators ──
                    # Mongo 4.0 safe: $setOnInsert, $set, $min, $max, $inc, $addToSet.
                    # Field strategy (fixes the "first-batch locks everything" bug):
                    #   start_time   $min  — earliest span start across batches
                    #   end_time     $max  — latest span end across batches
                    #   duration_ms  $max  — largest batch range (root batch = true)
                    #   total_spans  $inc  — accumulate as batches arrive
                    #   token_*      $inc + $addToSet token_span_ids — span 级去重
                    #                增量累加；
                    #                存量文档（无 token_span_ids，$max 时代写入）
                    #                维持 $max 语义，由回填脚本迁移
                    #   agent_type   UPGRADE-ONLY: $set 'workflow' when this batch
                    #                has workflow evidence (step/workflow run span);
                    #                else $setOnInsert 'agent' fallback. Never $set
                    #                'agent' — a model-only batch must NOT downgrade
                    #                a prior 'workflow' classification.
                    #   agent_id     $set only when resolved this batch (workflow →
                    #                workflow_id; agent → agent.name). Never clobbers.
                    #   workflow_id  $set only when resolved this batch.
                    #   run_id/session_id/user_id/team_id $set — only when present
                    #   name         $set good_name (overwrite bad early name);
                    #                else $setOnInsert fallback (root_span.name) so
                    #                the first insert has a name but later model-only
                    #                batches cannot clobber a name already set.
                    #   status/error_count $setOnInsert — first-batch (unchanged).
                    set_on_insert: dict = {
                        "trace_id": trace_id,
                        "created_at": trace_dict.get("created_at"),
                        "status": trace_dict.get("status"),
                        "error_count": trace_dict.get("error_count", 0),
                        "agent_type": "agent",  # fallback only on insert
                        # 新建即声明 token_span_ids，避免「首批无 token span」
                        # 的 trace 后续批次被误判为存量 $max 文档
                        "token_span_ids": [],
                    }
                    set_fields: dict = {}
                    if agent_type == "workflow":
                        set_fields["agent_type"] = "workflow"
                    # agent_id: $set only from authoritative sources (workflow_id
                    # or a root span's identity). Non-authoritative (workflow
                    # step agent.name) goes to $setOnInsert so it never clobbers
                    # a prior workflow_id-based agent_id.
                    if resolved_agent_id:
                        set_fields["agent_id"] = resolved_agent_id
                    elif agent_id:
                        set_on_insert["agent_id"] = agent_id
                    if workflow_id:
                        set_fields["workflow_id"] = workflow_id
                    # user_id / session_id / run_id: $set when resolved this
                    # batch (scanned from any span, not just root). AgentOS
                    # calls without a user_id stay absent — UI defaults to
                    # "qa-agent".
                    if user_id:
                        set_fields["user_id"] = user_id
                    if session_id:
                        set_fields["session_id"] = session_id
                    if run_id:
                        set_fields["run_id"] = run_id
                    for k in ("team_id",):
                        v = trace_dict.get(k)
                        if v:
                            set_fields[k] = v
                    # name: $set only when authoritative (workflow_id or root
                    # span identity); else $setOnInsert fallback so model-only /
                    # workflow-step batches never clobber a name already set.
                    if good_name and name_authoritative:
                        set_fields["name"] = good_name
                    elif good_name:
                        set_on_insert["name"] = good_name
                    else:
                        set_on_insert["name"] = trace_dict.get("name")

                    inc_fields: dict = {"total_spans": len(trace_spans)}
                    min_fields: dict = {"start_time": trace_dict.get("start_time")}
                    max_fields: dict = {
                        "end_time": trace_dict.get("end_time"),
                        "duration_ms": trace_dict.get("duration_ms"),
                    }
                    add_to_set: dict = {}

                    # token 聚合：先读 trace 文档
                    # 判定新式/存量。新式（含 token_span_ids 或新建）→ $inc 增量；
                    # 存量（无该字段，$max 时代写入）→ 维持 $max，回填脚本迁移。
                    # token_cache_read 仅新式分支写入；
                    # 存量 $max 分支不同步加 cache 字段。
                    existing_doc = self._traces_col.find_one(
                        {"trace_id": trace_id}, {"token_span_ids": 1}
                    )
                    if existing_doc is not None and "token_span_ids" not in existing_doc:
                        total_prompt = sum(p for p, _, _ in batch_token_spans.values())
                        total_completion = sum(c for _, c, _ in batch_token_spans.values())
                        max_fields.update(
                            {
                                "token_prompt": total_prompt,
                                "token_completion": total_completion,
                                "token_total": total_prompt + total_completion,
                            }
                        )
                    else:
                        seen_ids = (existing_doc or {}).get("token_span_ids") or []
                        dp, dc, dcr, new_ids = compute_token_increment(batch_token_spans, seen_ids)
                        if dp or dc:
                            inc_fields["token_prompt"] = dp
                            inc_fields["token_completion"] = dc
                            inc_fields["token_total"] = dp + dc
                            # dcr 为 0 也写入——「字段存在值 0」与「字段缺失」
                            # 在 UI 上语义不同（— vs 0，见 spec）。
                            inc_fields["token_cache_read"] = dcr
                        if new_ids:
                            add_to_set["token_span_ids"] = {"$each": new_ids}

                    update_op: dict = {}
                    # $addToSet 同样要与 $setOnInsert 消解（2026-09-02 事故：
                    # token_span_ids 双操作符冲突 → code 40 → 新 trace 全部
                    # 导出失败、token 不计、span 文档丢失）
                    dedupe_update_paths(set_on_insert, set_fields)
                    dedupe_update_paths(set_on_insert, add_to_set)
                    if set_on_insert:
                        update_op["$setOnInsert"] = set_on_insert
                    if set_fields:
                        update_op["$set"] = set_fields
                    if inc_fields:
                        update_op["$inc"] = inc_fields
                    if min_fields:
                        update_op["$min"] = min_fields
                    if max_fields:
                        update_op["$max"] = max_fields
                    if add_to_set:
                        update_op["$addToSet"] = add_to_set

                    # Per-batch diagnostic. Captures the exact operators applied so
                    # multi-batch name/duration/classification issues are visible.
                    span_names = [s.name for s in trace_spans]
                    logger.debug(
                        "trace_export: trace_id=%s agent_type=%s good_name=%s "
                        "name_auth=%s resolved_agent_id=%s workflow_id=%s "
                        "batch_spans=%d spans=%s ops=%s set=%s min_start=%s "
                        "max_end=%s max_dur=%s",
                        trace_id[:16], agent_type, good_name,
                        name_authoritative, resolved_agent_id, workflow_id,
                        len(trace_spans), span_names[:8],
                        list(update_op.keys()),
                        {k: v for k, v in set_fields.items() if k != "name"},
                        trace_dict.get("start_time"), trace_dict.get("end_time"),
                        trace_dict.get("duration_ms"),
                    )

                    self._traces_col.update_one(
                        {"trace_id": trace_id},
                        update_op,
                        upsert=True,
                    )

                # Insert spans
                span_docs = [s.to_dict() for s in trace_spans]
                if span_docs:
                    self._spans_col.insert_many(span_docs, ordered=False)

            return SpanExportResult.SUCCESS
        except Exception as e:
            logger.warning("Span export failed: %s", e)
            return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        self._shutdown = True

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
