"""
QA Agent System — Shared error types

Errors that cross module boundaries (core → api) live here so the API layer can
register handlers without importing from the layers that raise them.
"""

from __future__ import annotations

_DEFAULT_MESSAGE = (
    "LLM_API_KEY is not set. Put your provider API key in .env "
    "(see .env.example) and restart the service."
)


class LLMNotConfiguredError(Exception):
    """Raised when LLM-backed work is attempted without provider credentials (D15).

    The API layer maps this to ``409 {"code": 409, "error": "LLM_NOT_CONFIGURED"}``
    — the response shape the frontend already implements — instead of letting a
    placeholder key produce a confusing provider-side 401.
    """

    def __init__(self, message: str = "") -> None:
        super().__init__(message or _DEFAULT_MESSAGE)
