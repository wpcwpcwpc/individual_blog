#!/usr/bin/env bash
# ============================================================
# QA Agent — Linux 部署 & 启动脚本
# ------------------------------------------------------------
# 工作流: git pull → 建 venv → 装 requirements.txt
#         → 校验 → 启动 uvicorn (main:app)
# 用法:   ./deploy.sh {pull|install|start|stop|status|check|restart}
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
err()  { echo -e "${RED}[ERROR]${NC} $*"; }

# ── 命令行参数 ──────────────────────────────────
ACTION="${1:-start}"   # pull | install | start | stop | status | check | restart
HOST="${2:-0.0.0.0}"
PORT="${3:-8000}"
PID_FILE="$SCRIPT_DIR/.qa_agent.pid"
LOG_FILE="$SCRIPT_DIR/qa_agent.log"

# ── 端口残留进程清理 ────────────────────────────
# PID 文件可能与实际进程脱钩 (被手动删 / 上次 stop 杀错 PID)。
# 按端口查占用进程兜底杀掉, 防新进程因端口被占起不来、curl 命中旧进程加载旧代码。
kill_port_processes() {
    if ! command -v ss &>/dev/null; then
        return 0
    fi
    local pids
    # 提取监听 $PORT 的所有 PID (可能有多个), 跳过自身 ss 进程
    # || true 兜底: 端口干净时 grep 无匹配返回 1, set -e + pipefail 会 abort 脚本
    pids=$(ss -tlnpH 2>/dev/null | grep -E "[:.]$PORT\b" | grep -oE 'pid=[0-9]+' | grep -oE '[0-9]+' | sort -u || true)
    if [ -z "$pids" ]; then
        return 0
    fi
    for p in $pids; do
        if kill -0 "$p" 2>/dev/null; then
            warn "Port $PORT still held by stale process (PID $p) — killing"
            kill "$p" 2>/dev/null || true
        fi
    done
    sleep 1
    # 仍有残留则强杀
    for p in $pids; do
        if kill -0 "$p" 2>/dev/null; then
            warn "Graceful kill failed for PID $p, force killing"
            kill -9 "$p" 2>/dev/null || true
        fi
    done
    sleep 1
}

# ── 检查函数 ────────────────────────────────────
check_python() {
    if command -v python3.11 &>/dev/null; then
        PYTHON=python3.11
    elif command -v python3 &>/dev/null; then
        PYTHON=python3
        local ver=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        if [[ "${ver%%.*}" -lt 3 || ( "${ver%%.*}" -eq 3 && "${ver##*.}" -lt 11 ) ]]; then
            err "Python >= 3.11 required, found $ver"
            return 1
        fi
    else
        err "Python 3 not found. Install: apt install python3.11 python3.11-venv"
        return 1
    fi
    log "Python: $($PYTHON --version)"
}

check_env() {
    if [ ! -f ".env" ]; then
        err ".env not found — create from .env.example and fill LLM_API_KEY etc."
        return 1
    fi
    # Check here instead of letting the app abort: SESSION_SECRET_KEY has no
    # default and QAAgentSettings refuses to load without it, so the process
    # would exit during import with a stack trace.
    if ! grep -qE '^[[:space:]]*SESSION_SECRET_KEY=[^[:space:]]' .env; then
        err "SESSION_SECRET_KEY missing or empty in .env — generate one:"
        err "  python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        return 1
    fi
}

activate_venv() {
    if [ ! -d ".venv" ]; then
        err ".venv not found — run: ./deploy.sh install"
        return 1
    fi
    # shellcheck disable=SC1091
    source .venv/bin/activate
}

# ── git pull ────────────────────────────────────
do_pull() {
    log "Pulling latest code from git..."
    if ! command -v git &>/dev/null; then
        err "git not found. Install: apt install git"
        return 1
    fi
    git pull --ff-only
    log "Code updated"
}

# ── 安装 ────────────────────────────────────────
do_install() {
    log "Installing qa-agent dependencies..."
    check_python

    # 1. venv
    if [ ! -d ".venv" ]; then
        $PYTHON -m venv .venv
        log "Created .venv"
    fi
    # shellcheck disable=SC1091
    source .venv/bin/activate
    pip install --upgrade pip -q || true

    # 2. requirements.txt (locked versions, 跨平台纯 Python 包)
    log "Installing requirements.txt..."
    pip install -r requirements.txt 2>&1 | tee /tmp/qa_agent_pip.log || true
    if grep -qE "ERROR|Could not find" /tmp/qa_agent_pip.log 2>/dev/null; then
        warn "部分依赖安装失败, 详情: /tmp/qa_agent_pip.log"
    fi

    # 3. qa-agent 本身 (editable — 让 main:app 可被 uvicorn import)
    log "Installing qa-agent (editable)..."
    pip install -e . -q 2>&1 | tee /tmp/qa_agent_editable.log || true

    # 4. 校验最小可运行依赖 (含 mcp —— mcp_service/loader.py:150 硬依赖)
    log "Verifying core dependencies..."
    if python -c "import fastapi, uvicorn, agno, mcp, motor, beanie" 2>/dev/null; then
        log "Core deps OK (fastapi/uvicorn/agno/mcp/motor/beanie)"
    else
        err "Core deps missing. Check /tmp/qa_agent_pip.log"
        return 1
    fi

    check_env

    log "Install complete. Run: ./deploy.sh start"
}

# ── 启动（后台静默） ────────────────────────────
do_start() {
    log "Starting QA Agent on ${HOST}:${PORT} (background)..."

    # 清残留: PID 文件 + 端口占用 (防旧进程加载旧代码, curl 命中僵尸)
    if [ -f "$PID_FILE" ]; then
        local old_pid
        old_pid=$(cat "$PID_FILE")
        if kill -0 "$old_pid" 2>/dev/null; then
            warn "QA Agent already running (PID $old_pid). Stopping it first."
            do_stop
        else
            rm -f "$PID_FILE"
        fi
    fi
    kill_port_processes

    check_python
    activate_venv
    check_env

    # 用 main:app 而非 api.server:app —— main.py 顶层负责:
    #   - logging.basicConfig 结构化格式
    #   - qa_agent.events 事件 logger 文件 handler
    #   - OpenTelemetry Tracing 初始化
    #   - /health 访问日志静音
    # 直接 import api.server 会绕过这些 setup, 导致日志裸输出 + 事件丢失 + 无 trace.
    nohup python -m uvicorn main:app \
        --host "$HOST" \
        --port "$PORT" \
        --workers 1 \
        --log-level info \
        >> "$LOG_FILE" 2>&1 &

    local pid=$!
    echo "$pid" > "$PID_FILE"
    sleep 2

    if kill -0 "$pid" 2>/dev/null; then
        log "QA Agent started (PID $pid, port $PORT)"
        log "Logs: $LOG_FILE"
        log "Stop:  ./deploy.sh stop"
        log "Status: ./deploy.sh status"
    else
        err "启动失败, 查看日志: $LOG_FILE"
        rm -f "$PID_FILE"
        return 1
    fi
}

# ── 停止 ────────────────────────────────────────
do_stop() {
    # 1. 先按 PID 文件停 (正常路径)
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            log "Stopping QA Agent (PID $pid)..."
            kill "$pid"
            # Graceful 等待最多 10s: uvicorn 需时间收尾 (in-flight 请求 + lifespan shutdown)
            local waited=0
            while kill -0 "$pid" 2>/dev/null && [ "$waited" -lt 10 ]; do
                sleep 1
                waited=$((waited + 1))
            done
            if kill -0 "$pid" 2>/dev/null; then
                warn "Graceful shutdown timed out after ${waited}s, force killing..."
                kill -9 "$pid" 2>/dev/null || true
            else
                log "Graceful shutdown OK (${waited}s)"
            fi
            log "QA Agent stopped"
        else
            warn "PID $pid not alive, cleaning up stale PID file"
        fi
        rm -f "$PID_FILE"
    fi

    # 2. 端口兜底: PID 文件丢失 / 指向错误 PID 时, 仍可能有进程占用 $PORT
    kill_port_processes
}

# ── 状态 ────────────────────────────────────────
do_status() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            log "QA Agent running — PID $pid, port $(ss -tlnp 2>/dev/null | grep -o ":$PORT" | head -1 || echo "?")"
            return 0
        fi
    fi
    warn "QA Agent not running"
    return 1
}

# ── 环境检查 ────────────────────────────────────
do_check() {
    log "QA Agent environment check..."
    check_python
    check_env

    # check MongoDB client
    if command -v mongosh &>/dev/null; then
        log "mongosh available"
    else
        warn "mongosh not found (optional, only needed for DB debugging)"
    fi

    # check venv + core deps
    if [ -d ".venv" ]; then
        log ".venv exists"
        # shellcheck disable=SC1091
        source .venv/bin/activate
        if python -c "import agno, mcp, motor, beanie" 2>/dev/null; then
            log "agno + mcp + motor + beanie installed OK"
        else
            warn "core deps missing — run: ./deploy.sh install"
        fi
    else
        warn ".venv not found — run: ./deploy.sh install"
    fi
}

# ── 入口 ────────────────────────────────────────
# ── 一键部署 (完整链路) ──────────────────────────
# 流程: stop (若运行) → git pull → 建 venv → 装 requirements.txt
#       → 装 editable qa-agent → 校验 → start
do_deploy() {
    log "=== Full deploy chain start ==="

    # 1. stop 旧进程 (PID 文件 + 端口兜底, 容错脱钩场景)
    log "[1/5] Stopping old QA Agent (if any)..."
    do_stop || true

    # 2. git pull (best-effort: a checkout without an upstream, or one with
    #    local changes, must not abort the whole deploy chain)
    log "[2/5] Pulling latest code..."
    do_pull || warn "git pull skipped/failed — continuing with the local checkout"

    # 3. install (venv + requirements + editable + 校验)
    log "[3/5] Installing dependencies..."
    do_install

    # 4. 校验通过后 truncate 旧日志 (保留本次启动干净日志)
    : > "$LOG_FILE"
    log "[4/5] Log truncated: $LOG_FILE"

    # 5. start
    log "[5/5] Starting QA Agent..."
    do_start "$HOST" "$PORT"

    log "=== Full deploy chain done ==="
}

# ── 入口 ────────────────────────────────────────
case "$ACTION" in
    pull)     do_pull ;;
    install)  do_install ;;
    start)    do_start ;;
    stop)     do_stop ;;
    status)   do_status ;;
    check)    do_check ;;
    deploy)   do_deploy ;;
    restart)
        do_stop || true
        do_start
        ;;
    *)
        echo "Usage: ./deploy.sh {deploy|pull|install|start|stop|status|check|restart} [host] [port]"
        echo ""
        echo "  deploy   一键完整链路: stop → git pull → install → start (推荐)"
        echo "  install  建 .venv、装 requirements.txt + editable qa-agent、校验"
        echo "  pull     git pull --ff-only 拉最新代码"
        echo "  start    后台启动 qa-agent (default: 0.0.0.0:8000)"
        echo "  stop     停止 qa-agent"
        echo "  restart  stop + start"
        echo "  status   查看运行状态"
        echo "  check    检查运行环境是否就绪"
        exit 1
        ;;
esac
