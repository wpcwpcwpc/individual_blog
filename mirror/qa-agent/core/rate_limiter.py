"""
QA Agent System — Adaptive Rate Limiter

Provides self-adjusting rate limiting for LLM API calls to handle TPM (Tokens Per Minute)
quota limits gracefully. When a 429 error is detected, the system backs off and gradually
recovers after consecutive successful requests.

Design:
- AdaptiveRateLimiter: Singleton managing global backoff state
- RateLimitedModel: Wrapper class that proxies OpenAILike with rate limiting
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agno.models.openai.like import OpenAILike

from core.config import QAAgentSettings, settings

logger = logging.getLogger(__name__)


class AdaptiveRateLimiter:
    """Singleton rate limiter with adaptive backoff and recovery.
    
    State is shared globally across all model instances to coordinate
    request pacing at the account/API key level.
    """
    
    _instance: "AdaptiveRateLimiter | None" = None
    _initialized: bool = False
    _init_lock: threading.Lock = threading.Lock()
    
    def __new__(cls, config: QAAgentSettings | None = None) -> "AdaptiveRateLimiter":
        if cls._instance is None:
            with cls._init_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, config: QAAgentSettings | None = None):
        if AdaptiveRateLimiter._initialized:
            return
        AdaptiveRateLimiter._initialized = True
        
        cfg = config or settings
        self._initial_backoff = cfg.rate_limit_initial_backoff
        self._max_backoff = cfg.rate_limit_max_backoff
        self._backoff_multiplier = cfg.rate_limit_backoff_multiplier
        self._recovery_threshold = cfg.rate_limit_recovery_threshold
        self._recovery_divisor = cfg.rate_limit_recovery_divisor
        
        # State
        self._backoff_delay: float = 0.0
        self._success_streak: int = 0
        self._last_request_time: float = 0.0
        
        # Lock for protecting state changes across sync/async callers
        self._state_lock = threading.Lock()
    
    @property
    def current_delay(self) -> float:
        """Current backoff delay in seconds."""
        return self._backoff_delay
    
    @property
    def is_backing_off(self) -> bool:
        """Whether the limiter is currently in backoff state."""
        return self._backoff_delay > 0
    
    async def wait_if_needed(self) -> None:
        """Wait for backoff delay if currently rate limited."""
        if self._backoff_delay > 0:
            logger.warning(
                "[RateLimiter] LLM rate-limited — sleeping %.1fs (backoff_delay=%.1fs)",
                self._backoff_delay, self._backoff_delay,
            )
            await asyncio.sleep(self._backoff_delay)
    
    def on_rate_limit_error(self) -> None:
        """Called when a 429/rate limit error is encountered.
        
        Increases backoff delay (up to max) and resets success streak.
        """
        with self._state_lock:
            prev_delay = self._backoff_delay
            if self._backoff_delay == 0:
                self._backoff_delay = self._initial_backoff
            else:
                self._backoff_delay = min(
                    self._backoff_delay * self._backoff_multiplier,
                    self._max_backoff
                )
            self._success_streak = 0
            new_delay = self._backoff_delay
        logger.warning(
            "[RateLimiter] 429 detected — backoff %.1fs → %.1fs",
            prev_delay, new_delay,
        )
    
    def on_success(self) -> None:
        """Called after a successful request.
        
        Increments success streak; when threshold is reached, reduces
        backoff delay (recovery). Full recovery when delay drops below 1s.
        """
        with self._state_lock:
            if self._backoff_delay > 0:
                self._success_streak += 1
                if self._success_streak >= self._recovery_threshold:
                    prev_delay = self._backoff_delay
                    self._backoff_delay /= self._recovery_divisor
                    if self._backoff_delay < 1.0:
                        self._backoff_delay = 0.0
                        logger.info(
                            "[RateLimiter] Fully recovered — backoff cleared "
                            "(was %.1fs after %d consecutive successes)",
                            prev_delay, self._success_streak,
                        )
                    else:
                        logger.info(
                            "[RateLimiter] Backoff reduced: %.1fs → %.1fs "
                            "(streak=%d, threshold=%d)",
                            prev_delay, self._backoff_delay,
                            self._success_streak, self._recovery_threshold,
                        )
                    self._success_streak = 0
    
    def reset(self) -> None:
        """Reset limiter state (for testing)."""
        with self._state_lock:
            self._backoff_delay = 0.0
            self._success_streak = 0


# Global singleton instance
_rate_limiter: AdaptiveRateLimiter | None = None


def get_rate_limiter(config: QAAgentSettings | None = None) -> AdaptiveRateLimiter:
    """Get the global AdaptiveRateLimiter singleton."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = AdaptiveRateLimiter(config)
    return _rate_limiter


def is_rate_limit_error(error: Exception) -> bool:
    """Check if an exception indicates a rate limit (429) error.
    
    Checks for common patterns across different API gateways:
    - HTTP status code 429
    - Error messages containing "rate limit", "too many requests", "TPM"
    """
    error_str = str(error).lower()
    indicators = [
        "429",
        "rate limit",
        "rate_limit",
        "ratelimit",
        "too many requests",
        "tpm",
        "tokens per minute",
        "tokenlimiterror",
    ]
    return any(indicator in error_str for indicator in indicators)


# 超时类重试配置(与 TPM 重试独立计数)
# 注意:上游 read timeout ~120s,连续超时 = 大概率非瞬态(服务端故障/额度耗尽),
# 不值得多试。2 次 + 短退避,最坏单 turn ~6 分钟放弃。
_TIMEOUT_MAX_RETRIES = 2
_TIMEOUT_BASE_DELAY = 3.0  # 秒,线性 3s→6s


def is_timeout_error(error: Exception) -> bool:
    """Check if an exception indicates a network/read timeout (not 429).

    `Request timed out` / httpx ReadTimeout / ConnectTimeout 等,
    通常是 LLM 响应慢或连接瞬断,值得重试。
    """
    error_str = str(error).lower()
    indicators = [
        "timed out",
        "timeout",
        "readtimeout",
        "connecttimeout",
        "pooltimeout",
        "request timed out",
    ]
    return any(indicator in error_str for indicator in indicators)


def is_retryable_error(error: Exception) -> bool:
    """429(限流) 或 timeout(超时)均可重试。"""
    return is_rate_limit_error(error) or is_timeout_error(error)


# ── 内容层 TPM 错误检测 ──────────────────────────────────────────
# 上游网关对 TPM 限流有时不返回标准 429,而是 HTTP 200 + 自然语言错误
# 文本(如 "please try again later due to token limit (TPM) for app ...")。
# 此时 Agno 把错误文本当正常 assistant content yield,既有 is_rate_limit_error
# 检测异常对象的逻辑失效。此处补内容层检测:对 response/chunk 文本做特征
# 组合匹配(≥2 特征同时命中),命中后转成可重试异常,复用既有重试循环。
#
# 组合匹配而非单特征,避免正常技术讨论文本(如 "token limit is 128k")误报。
# 特征串列表变更只需改此一处(stream_adapter 兜底层复用同常量)。
_TPM_CONTENT_INDICATORS: tuple[str, ...] = (
    "token limit",
    "tpm",
    "please try again later",
    "tokens per minute",
    "tokenlimiterror",
)
_TPM_CONTENT_MIN_HITS: int = 2


def _detect_tpm_content_error(text: str) -> bool:
    """检测文本是否疑似上游以 200 body 返回的 TPM 限流错误。

    对入参 ``text`` 小写化后统计 ``_TPM_CONTENT_INDICATORS`` 中命中的
    特征数,命中数 ≥ ``_TPM_CONTENT_MIN_HITS`` 返回 True;空串返回 False。

    组合匹配降低误报:正常技术讨论文本(如 "the model's token limit is 128k")
    仅命中 1 个特征,不触发。

    Args:
        text: LLM 响应的 content 文本(可为 chunk 增量或完整 response)。

    Returns:
        True 表示文本疑似上游 TPM 限流错误文本。
    """
    if not text:
        return False
    lowered = text.lower()
    hits = sum(1 for ind in _TPM_CONTENT_INDICATORS if ind in lowered)
    return hits >= _TPM_CONTENT_MIN_HITS


# Import Model base class for isinstance checks
from agno.models.base import Model as AgnoModel


class RateLimitedModel(AgnoModel):
    """Wrapper for OpenAILike that adds adaptive rate limiting.
    
    Inherits from agno.models.base.Model to pass Agno's isinstance checks.
    
    Proxies all method calls to the inner model while:
    1. Waiting for backoff delay before requests
    2. Detecting 429 errors and triggering backoff
    3. Recording successes for recovery
    
    Uses __getattr__ to transparently proxy all attributes/methods
    not explicitly defined.
    """
    
    # Required by Model base class
    id: str = "rate-limited-model"
    
    def __init__(self, inner_model: "OpenAILike", limiter: AdaptiveRateLimiter | None = None):
        # Don't call super().__init__() to avoid dataclass field issues
        # Just set the required attributes directly
        object.__setattr__(self, "_inner_model", inner_model)
        object.__setattr__(self, "_limiter", limiter or get_rate_limiter())
        # Copy essential attributes from inner model for Agno compatibility
        object.__setattr__(self, "id", inner_model.id)
        object.__setattr__(self, "name", getattr(inner_model, "name", None))
        object.__setattr__(self, "provider", getattr(inner_model, "provider", None))
    
    def __getattr__(self, name: str) -> Any:
        """Proxy attribute access to inner model."""
        # First check if it's a private attribute of this class
        if name.startswith("_"):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        return getattr(self._inner_model, name)
    
    def __setattr__(self, name: str, value: Any) -> None:
        """Proxy attribute setting to inner model."""
        if name in ("_inner_model", "_limiter", "id", "name", "provider"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._inner_model, name, value)
    
    # ── TPM retry configuration ───────────────────────────────────────
    _TPM_MAX_RETRIES = 5           # non-streaming: retry up to 5 times
    _TPM_MAX_RETRIES_STREAM = 3   # streaming: retry up to 3 times

    @staticmethod
    def _extract_content_text(result: Any) -> str:
        """从 LLM 调用结果中提取 content 文本用于内容层错误检测。

        Agno model 的 ainvoke/aresponse 返回 RunResponse/RunOutput 对象,
        其 ``content`` 字段可能是 str / list / None。流式 chunk 同理。

        Args:
            result: ainvoke/aresponse 返回值,或流式 yield 的单个 chunk。

        Returns:
            提取的文本(str);无文本时返回空串(避免 None.lower() 崩溃)。
        """
        if result is None:
            return ""
        # 优先取 .content(Agno RunResponse/RunOutput 标准字段)
        content = getattr(result, "content", None)
        if isinstance(content, str):
            return content
        if content is None:
            # content 为 None 时,fallback 到 str(result) 兜底
            # (可能命中,也可能不命中,由 _detect_tpm_content_error 判定)
            try:
                s = str(result)
                # 避免把对象 repr(如 "<RunResponse ...>")当文本,仅当含字母时用
                return s if any(c.isalpha() for c in s) else ""
            except Exception:
                return ""
        # content 是 list/token 等复杂结构,转 str
        try:
            return str(content)
        except Exception:
            return ""

    async def _ainvoke_with_tpm_retry(self, method_name: str, *args, **kwargs):
        """Invoke a non-streaming method with TPM-aware + timeout auto-retry.

        Retries on:
        - TPM/429 errors (up to ``_TPM_MAX_RETRIES``): adaptive backoff
        - Timeout errors (up to ``_TIMEOUT_MAX_RETRIES``): fixed 5s base delay

        If all retries are exhausted the original exception is re-raised so
        the caller (agno) can apply its own retry strategy.
        """
        timeout_retries = 0
        for attempt in range(self._TPM_MAX_RETRIES + _TIMEOUT_MAX_RETRIES):
            await self._limiter.wait_if_needed()
            method = getattr(self._inner_model, method_name)
            t0 = time.monotonic()
            try:
                result = await method(*args, **kwargs)
                # ── 内容层 TPM 错误检测 ────────────────────────────────
                # 上游可能以 200 + 错误文本形式返回 TPM 限流(非 429 异常),
                # 此时 result.content 含错误文本。检测命中则抛 RuntimeError
                # (message 含 tpm/token limit 特征),走下方 is_rate_limit_error
                # 分支触发 on_rate_limit_error + continue 复用既有重试循环。
                content_text = self._extract_content_text(result)
                if content_text and _detect_tpm_content_error(content_text):
                    logger.warning(
                        "[TPM-Content] %s attempt %d 命中 200-body 错误文本, "
                        "原文摘要: %s",
                        method_name, attempt + 1, content_text[:200],
                    )
                    raise RuntimeError(
                        f"[TPM-Content] upstream returned error text in 200 body, "
                        f"please try again later due to token limit (TPM): "
                        f"{content_text[:200]}"
                    )
                self._limiter.on_success()
                return result
            except Exception as e:
                logger.warning(
                    "[Invoke] %s attempt %d 异常: %s: %s (耗时 %.2fs)",
                    method_name, attempt + 1, type(e).__name__, e, time.monotonic() - t0,
                )
                if is_rate_limit_error(e):
                    self._limiter.on_rate_limit_error()
                    if attempt < self._TPM_MAX_RETRIES + _TIMEOUT_MAX_RETRIES - 1:
                        delay = self._limiter.current_delay or 5.0
                        logger.warning(
                            "[TPM-Retry] %s attempt %d failed (429), "
                            "retrying after %.1fs...",
                            method_name, attempt + 1, delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                elif is_timeout_error(e):
                    timeout_retries += 1
                    if timeout_retries <= _TIMEOUT_MAX_RETRIES:
                        delay = _TIMEOUT_BASE_DELAY * timeout_retries
                        logger.warning(
                            "[Timeout-Retry] %s attempt %d/%d failed (timeout), "
                            "retrying after %.1fs...",
                            method_name, timeout_retries, _TIMEOUT_MAX_RETRIES, delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                raise

    async def _astream_with_tpm_retry(self, method_name: str, *args, **kwargs):
        """Invoke a streaming method with TPM-aware + timeout auto-retry.

        The entire streaming call is retried on TPM/timeout errors (up to
        ``_TPM_MAX_RETRIES_STREAM`` + ``_TIMEOUT_MAX_RETRIES``).  Mid-stream
        errors (after the first chunk has already been yielded) are NOT
        retried.
        """
        timeout_retries = 0
        logger.info("[Stream] %s - 发起重试请求", method_name)
        for attempt in range(self._TPM_MAX_RETRIES_STREAM + _TIMEOUT_MAX_RETRIES):
            await self._limiter.wait_if_needed()
            method = getattr(self._inner_model, method_name)
            t0 = time.monotonic()
            first_chunk_ts: float | None = None
            chunk_count = 0
            # ── 内容层 TPM 错误检测 buffer ────────────────────────
            # 累积 chunk content 做特征组合匹配。上游 200+错误文本可能跨多
            # chunk 返回，单 chunk 检测会漏；buffer 整体检测确保命中。
            # 命中后 break 停止 yield 后续 chunk → 抛 RuntimeError → 走 except
            # 块 is_rate_limit_error 分支复用既有重试循环。
            content_buffer: str = ""
            tpm_content_hit: bool = False
            try:
                stream_call = method(*args, **kwargs)
                async for chunk in stream_call:
                    if first_chunk_ts is None:
                        first_chunk_ts = time.monotonic()
                    chunk_count += 1
                    # 内容层检测：拼入 buffer 后整体匹配
                    chunk_text = self._extract_content_text(chunk)
                    if chunk_text:
                        content_buffer += chunk_text
                        if _detect_tpm_content_error(content_buffer):
                            logger.warning(
                                "[TPM-Content] %s attempt %d 流中 chunk %d 命中 "
                                "200-body 错误文本，停止 yield 后续 chunk，"
                                "原文摘要: %s",
                                method_name, attempt + 1, chunk_count,
                                content_buffer[:200],
                            )
                            tpm_content_hit = True
                            break
                    yield chunk
                if tpm_content_hit:
                    # 命中后抛 RuntimeError(message 含 tpm/token limit 特征，
                    # 使 is_rate_limit_error 返回 True)，走下方 except 分支重试
                    raise RuntimeError(
                        f"[TPM-Content] upstream returned error text in 200 body, "
                        f"please try again later due to token limit (TPM): "
                        f"{content_buffer[:200]}"
                    )
                self._limiter.on_success()
                return
            except Exception as e:
                ttfb_part = (
                    f", TTFB={first_chunk_ts - t0:.2f}s" if first_chunk_ts else ", 无首 chunk"
                )
                logger.warning(
                    "[Stream] %s attempt %d 异常: %s: %s (已收 %d chunk, 耗时 %.2fs%s)",
                    method_name, attempt + 1, type(e).__name__, e, chunk_count,
                    time.monotonic() - t0, ttfb_part,
                )
                if is_rate_limit_error(e):
                    self._limiter.on_rate_limit_error()
                    if attempt < self._TPM_MAX_RETRIES_STREAM + _TIMEOUT_MAX_RETRIES - 1:
                        delay = self._limiter.current_delay or 5.0
                        logger.warning(
                            "[TPM-Retry] %s attempt %d failed (429), "
                            "retrying after %.1fs...",
                            method_name, attempt + 1, delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                elif is_timeout_error(e):
                    timeout_retries += 1
                    if timeout_retries <= _TIMEOUT_MAX_RETRIES:
                        delay = _TIMEOUT_BASE_DELAY * timeout_retries
                        logger.warning(
                            "[Timeout-Retry] %s attempt %d/%d failed (timeout), "
                            "retrying after %.1fs...",
                            method_name, timeout_retries, _TIMEOUT_MAX_RETRIES, delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                raise

    # ── Async non-streaming ───────────────────────────────────────────

    async def ainvoke(self, *args, **kwargs):
        return await self._ainvoke_with_tpm_retry("ainvoke", *args, **kwargs)

    async def aresponse(self, *args, **kwargs):
        return await self._ainvoke_with_tpm_retry("aresponse", *args, **kwargs)

    # ── Async streaming ───────────────────────────────────────────────

    async def ainvoke_stream(self, *args, **kwargs):
        async for chunk in self._astream_with_tpm_retry(
            "ainvoke_stream", *args, **kwargs
        ):
            yield chunk

    async def aresponse_stream(self, *args, **kwargs):
        async for chunk in self._astream_with_tpm_retry(
            "aresponse_stream", *args, **kwargs
        ):
            yield chunk

    # ── Sync methods (keep simple — TPM retry is blocking) ────────────

    def invoke(self, *args, **kwargs):
        if self._limiter.current_delay > 0:
            time.sleep(self._limiter.current_delay)
        try:
            result = self._inner_model.invoke(*args, **kwargs)
            self._limiter.on_success()
            return result
        except Exception as e:
            if is_rate_limit_error(e):
                self._limiter.on_rate_limit_error()
            raise

    def invoke_stream(self, *args, **kwargs):
        if self._limiter.current_delay > 0:
            time.sleep(self._limiter.current_delay)
        try:
            for chunk in self._inner_model.invoke_stream(*args, **kwargs):
                yield chunk
            self._limiter.on_success()
        except Exception as e:
            if is_rate_limit_error(e):
                self._limiter.on_rate_limit_error()
            raise

    def response(self, *args, **kwargs):
        if self._limiter.current_delay > 0:
            time.sleep(self._limiter.current_delay)
        try:
            result = self._inner_model.response(*args, **kwargs)
            self._limiter.on_success()
            return result
        except Exception as e:
            if is_rate_limit_error(e):
                self._limiter.on_rate_limit_error()
            raise
    
    # Abstract method implementations (delegate to inner model)
    def _parse_provider_response(self, *args, **kwargs):
        """Delegate to inner model's implementation."""
        return self._inner_model._parse_provider_response(*args, **kwargs)
    
    def _parse_provider_response_delta(self, *args, **kwargs):
        """Delegate to inner model's implementation."""
        return self._inner_model._parse_provider_response_delta(*args, **kwargs)
