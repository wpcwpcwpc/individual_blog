"""
QA Agent System — Auth Dependencies

FastAPI Dependency: get_current_user

Authentication is handled entirely by an OAuth provider callback → signed
cookie session. No JWT — the OAuth flow is the only login path.

There is no role model: every authenticated user is scoped to their own data.
"""

from __future__ import annotations

from typing import Optional

from fastapi import Request


async def get_current_user(request: Request) -> Optional[str]:
    """Extract the current user's identifier from the signed cookie session.

    The identifier is this system's own user key (``email``, see
    ``db.models.AgentUser``) — never a provider token.

    Returns:
        User identifier string if authenticated, None if anonymous.
    """
    return request.session.get("email") or None

