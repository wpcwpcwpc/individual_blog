"""
QA Agent System — Application Entry Point

Usage:
    # Development
    python main.py

    # Production
    uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1

Note: agent execution relies on asyncio; use a single worker process.
      Scale horizontally by running multiple containers behind a load balancer.
"""
import io
import logging
import sys
from pathlib import Path
from dotenv import load_dotenv
# ---- Encoding fix for Windows GBK consoles ----
# On Chinese Windows, stdout/stderr default to GBK (cp936) which cannot
# encode many Unicode characters (e.g. U+26A0 ⚠).  This causes
# UnicodeEncodeError in any print() or console logger output.
# Re-wrap with UTF-8 + errors='replace' so the process never crashes on
# a harmless print.
if sys.platform == "win32":
    _enc = "utf-8"
    try:
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding=_enc, errors="replace", line_buffering=True
        )
    except Exception:
        pass
    try:
        sys.stderr = io.TextIOWrapper(
            sys.stderr.buffer, encoding=_enc, errors="replace", line_buffering=True
        )
    except Exception:
        pass

# Ensure the project root is on sys.path when running directly
sys.path.insert(0, str(Path(__file__).parent))

# ---- --run-script dispatch (skill script runner entrypoint) ----
# One executable, several entrypoints via argv. When invoked as
# `python main.py --run-script <path> [args...]` (dev) or through the frozen
# executable, the script is executed in-process via runpy BEFORE any server
# bootstrap so the runner subprocess does not pay server startup cost.
# In frozen mode this also makes bundled packages importable, because
# _internal is on sys.path[0]. Must run before `from core.config import
# settings`. SystemExit from the script propagates → the subprocess exits
# with the script's own exit code.
if len(sys.argv) >= 2 and sys.argv[1] == "--run-script":
    import runpy
    _script = sys.argv[2]
    # Resolve the script path. Agent prompts emit project-relative paths such as
    # "qa-agent/.claude/skills/<skill>/<script>.py" (dev form). In dev, CWD is
    # the project root so the path resolves directly. In frozen mode skills are
    # bundled under _MEIPASS/.claude/skills/ without the leading project dir,
    # so try _MEIPASS with that prefix stripped.
    if not Path(_script).exists() and getattr(sys, "frozen", False):
        _meipass = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        _stripped = _script.replace("\\", "/").split("qa-agent/", 1)[-1]
        for _c in (_meipass / _script, _meipass / _stripped):
            if _c.exists():
                _script = str(_c)
                break
    sys.argv = [_script] + sys.argv[3:]
    runpy.run_path(_script, run_name="__main__")
    sys.exit(0)

# ----- Minimal early imports (keep this section lightweight) -----
# Only load settings here; do NOT import api.server at module top-level.
# api.server triggers the full Agno/pymilvus/tiktoken import chain, adding
# 1-2s to startup even before lifespan runs. We defer it to __main__ so
# `python main.py` pays the cost only once, and uvicorn's own reload logic
# can still reference `main:app` via the lazy import below.
from core.config import settings

# ---- Load .env (local file wins over ambient environment variables) ----
load_dotenv(Path(__file__).parent / ".env", override=True)

# Configure structured logging before importing anything else
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Silence noisy /health access logs (health probes hit /health frequently, and
# one access line per probe drowns out useful output). Only the access line is
# muted; application-level health logic is unaffected.
class _HealthAccessFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        return "/health" not in msg


logging.getLogger("uvicorn.access").addFilter(_HealthAccessFilter())

# Silence noisy HTTP-layer INFO logs that fire once per LLM call (every
# POST to /v1/chat/completions produces one "HTTP/1.1 200 OK" line from
# httpx + httpcore). WARNING+ still surfaces real network failures.
for _noisy in ("httpx", "httpcore", "openai._base_client", "openai"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# agno 自带 RichHandler 以裸 "INFO ..." 格式打每个 step/run 生命周期的
# 噪音日志（如 "Executing async step (non-streaming): payload"），观感
# 异常且无时间戳。WARNING+ 仍会透出（agno logger propagate=False，须
# 逐个设置 level，含 -team/-workflow 变体）。
for _agno_logger in ("agno", "agno-team", "agno-workflow"):
    logging.getLogger(_agno_logger).setLevel(logging.WARNING)

# Configure dedicated event logger (writes to agent_events.log)
# Guard against duplicate handlers caused by uvicorn hot-reload re-executing
# this module: Python's logging.getLogger() returns the same singleton each time,
# so addHandler() would accumulate handlers and print every log line N times.
_event_logger = logging.getLogger("qa_agent.events")
_event_logger.setLevel(logging.INFO)
_event_logger.propagate = False
if not _event_logger.handlers:
    _event_file_handler = logging.FileHandler("agent_events.log", encoding="utf-8")
    _event_file_handler.setFormatter(logging.Formatter("%(message)s"))
    _event_logger.addHandler(_event_file_handler)

# ---- Agno Tracing (OpenTelemetry) ----
# Initialize BEFORE importing api.server to ensure TracerProvider is set
# before any Agent/Workflow is constructed. Follows agno.tracing.setup_tracing API.
# Quality rule: monitoring decoupled from business — failure here never blocks startup.
# NOTE: Uses a custom Mongo span exporter because the target MongoDB deployment
# may predate pipeline-based updates.
_tracing_logger = logging.getLogger("qa_agent.tracing")

# ---- Agno approval patch ----
# Must install BEFORE any run starts: in agno 2.6.22 the dedup scope of
# acreate_approval_from_pause swallows the approval records of subsequent
# runs, so a multi-round approval run breaks from the second pause onward.
# Idempotent; a failure here never blocks startup.
try:
    from core.agno_approval_patch import apply_agno_approval_patch

    apply_agno_approval_patch()
except Exception as _patch_err:
    logging.getLogger(__name__).warning(
        "agno approval patch install failed: %s — continuing", _patch_err
    )

# ---- Agno reject note patch ----
# agno _apply_approval_to_tools rejected 分支丢弃 resolution_data.note →
# LLM 只见通用拒绝文案。patch 后意见（wrap_reject_note 包装）直达
# confirmation_note → reject_tool_call → function_call.error。幂等、fail-open。
try:
    from core.agno_approval_patch import apply_agno_reject_note_patch

    apply_agno_reject_note_patch()
except Exception as _note_patch_err:
    logging.getLogger(__name__).warning(
        "agno reject note patch install failed: %s — continuing", _note_patch_err
    )

if not settings.tracing_enabled:
    _tracing_logger.info("Tracing disabled by configuration")
else:
    try:
        from opentelemetry import trace as _trace_api
        from opentelemetry.sdk.trace import TracerProvider as _TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor as _BatchProcessor
        from openinference.instrumentation.agno import AgnoInstrumentor as _AgnoInstrumentor

        from core.tracing_exporter import Mongo4SpanExporter

        _exporter = Mongo4SpanExporter(
            db_url=settings.mongo_uri,
            db_name="qa_agent_db",
            traces_collection="agno_traces",
            spans_collection="agno_spans",
        )
        _provider = _TracerProvider()
        _provider.add_span_processor(_BatchProcessor(
            _exporter,
            max_queue_size=settings.tracing_max_queue_size,
            max_export_batch_size=settings.tracing_max_batch_size,
            schedule_delay_millis=settings.tracing_flush_interval_ms,
        ))
        _trace_api.set_tracer_provider(_provider)
        _AgnoInstrumentor().instrument()

        # Continue-path tracing patch: openinference does not wrap
        # _acontinue_run_stream, so model/tool spans produced during a resumed
        # run have no root to attach to and each becomes its own trace.
        try:
            from core.agno_approval_patch import apply_agno_continue_trace_patch

            apply_agno_continue_trace_patch()
        except Exception as _trace_patch_err:
            _tracing_logger.warning(
                "agno continue trace patch install failed: %s", _trace_patch_err
            )

        _tracing_logger.info("Tracing initialized (Mongo4SpanExporter, BatchSpanProcessor)")
    except ImportError as _imp_err:
        _tracing_logger.warning("Tracing dependencies missing (%s), tracing disabled", _imp_err)
    except Exception as _tracing_err:
        _tracing_logger.warning(
            "Tracing setup failed: %s — continuing without tracing", _tracing_err
        )

# Re-export the app object so `uvicorn main:app` works.
# Importing api.server here (outside __main__) is required for the
# `uvicorn main:app` command-line usage — uvicorn imports the module and
# looks up the `app` attribute. The cost is paid only once per process start.
from api.server import app  # noqa: E402  (imported after logging setup)


def run():
    """Entry point for PyInstaller frozen mode.

    Passes the app object directly (not a string reference) so PyInstaller
    can resolve it without relying on importlib's module path lookup.
    """
    import uvicorn

    uvicorn.run(
        app,
        host=settings.server_host,
        port=settings.server_port,
        reload=settings.debug,
        log_level=settings.log_level.lower(),
        # Single worker is required for shared asyncio state (interrupt events)
        workers=1,
    )


if __name__ == "__main__":
    run()
