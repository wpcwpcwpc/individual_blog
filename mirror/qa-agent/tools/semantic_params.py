"""
Semantic Parameter Parsers

Handles LLM-common type variants to avoid tool call failures:
- semantic_bool: "true"/"yes"/"1"/True → bool
- semantic_int: "30"/30 → int
- strip_unknown_params: remove unexpected kwargs with warning
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)

# ── Boolean truthy/falsy sets ───────────────────────────────────────────
_TRUTHY = frozenset({"true", "yes", "1", "on", "y", "t"})
_FALSY = frozenset({"false", "no", "0", "off", "n", "f", "none", "null", ""})


def semantic_bool(value: Any, *, default: Optional[bool] = None) -> bool:
    """Parse a value into a Python bool, tolerating LLM string variants.

    Accepted truthy:  True, "true", "True", "yes", "1", "on", "y", "t"
    Accepted falsy:   False, "false", "False", "no", "0", "off", "n", "f", None, ""

    Args:
        value: The raw value from tool call arguments.
        default: Fallback if value is None and no default is given → raises.

    Returns:
        Parsed boolean.

    Raises:
        ValueError: If value cannot be interpreted as boolean.
    """
    if value is None:
        if default is not None:
            return default
        raise ValueError("Cannot parse None as bool (no default provided)")

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return bool(value)

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUTHY:
            return True
        if normalized in _FALSY:
            return False

    raise ValueError(
        f"Cannot parse {value!r} as bool. "
        f"Expected one of: true/false, yes/no, 1/0, on/off"
    )


def semantic_int(value: Any, *, default: Optional[int] = None) -> int:
    """Parse a value into a Python int, tolerating string numbers.

    Args:
        value: The raw value from tool call arguments.
        default: Fallback if value is None.

    Returns:
        Parsed integer.

    Raises:
        ValueError: If value cannot be interpreted as int.
    """
    if value is None:
        if default is not None:
            return default
        raise ValueError("Cannot parse None as int (no default provided)")

    if isinstance(value, int) and not isinstance(value, bool):
        return value

    if isinstance(value, float):
        if value == int(value):
            return int(value)
        raise ValueError(f"Cannot losslessly convert float {value} to int")

    if isinstance(value, str):
        stripped = value.strip()
        try:
            return int(stripped)
        except ValueError:
            pass
        # Try float→int for "30.0"
        try:
            f = float(stripped)
            if f == int(f):
                return int(f)
        except ValueError:
            pass

    raise ValueError(
        f"Cannot parse {value!r} as int. Expected a number or numeric string."
    )


def semantic_float(value: Any, *, default: Optional[float] = None) -> float:
    """Parse a value into a Python float, tolerating string numbers.

    Args:
        value: The raw value from tool call arguments.
        default: Fallback if value is None.

    Returns:
        Parsed float.

    Raises:
        ValueError: If value cannot be interpreted as float.
    """
    if value is None:
        if default is not None:
            return default
        raise ValueError("Cannot parse None as float (no default provided)")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)

    if isinstance(value, str):
        stripped = value.strip()
        try:
            return float(stripped)
        except ValueError:
            pass

    raise ValueError(
        f"Cannot parse {value!r} as float. Expected a number or numeric string."
    )


def strip_unknown_params(
    params: Dict[str, Any],
    known_params: Set[str],
    *,
    tool_name: str = "unknown",
) -> Dict[str, Any]:
    """Remove unexpected parameters from a tool call, logging warnings.

    Args:
        params: Raw parameter dict from LLM tool call.
        known_params: Set of valid parameter names for this tool.
        tool_name: Tool name for logging context.

    Returns:
        Filtered dict containing only known parameters.
    """
    unknown = set(params.keys()) - known_params
    if unknown:
        logger.warning(
            "[%s] Ignoring unknown parameters: %s (known: %s)",
            tool_name,
            sorted(unknown),
            sorted(known_params),
        )

    return {k: v for k, v in params.items() if k in known_params}
