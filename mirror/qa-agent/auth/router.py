"""
QA Agent System — Auth Router

Authentication is a standard OAuth 2.0 / OpenID Connect authorization-code
flow → signed cookie session. The provider is deployment configuration
(``OAUTH_AUTHORIZE_URL`` / ``OAUTH_TOKEN_URL`` / ``OAUTH_USERINFO_URL`` together
with ``OAUTH_CLIENT_ID`` / ``OAUTH_CLIENT_SECRET``), so any compliant provider
works — no provider name is baked into the code.

Endpoints:
  GET  /auth/login_required   — redirect to the provider's authorize endpoint
  GET  /auth/login_callback   — exchange the code, write session, register user
  GET  /auth/get_login_user   — return current logged-in user info
  GET  /auth/logout           — clear session, redirect to frontend

D12: the session cookie carries only this system's own user identifier
(``email``) plus a display name. Provider access/refresh tokens stay in the
request scope and are never stored.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from typing import Optional, Tuple
from urllib.parse import urlencode, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from auth.service import get_or_register_user, get_user_info
from core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Session keys used only to carry the in-flight authorization request.
_STATE_KEY = "oauth_state"
_VERIFIER_KEY = "oauth_code_verifier"
_NEXT_KEY = "oauth_next"

_HTTP_TIMEOUT_S = 15.0


def _oauth_config() -> dict:
    """Return the configured OAuth client settings.

    Raises 503 when the deployment has no provider configured — the login
    routes are unusable until the ``OAUTH_*`` variables are set.
    """
    cfg = {
        "authorize_url": (settings.oauth_authorize_url or "").strip(),
        "token_url": (settings.oauth_token_url or "").strip(),
        "userinfo_url": (settings.oauth_userinfo_url or "").strip(),
        "client_id": (settings.oauth_client_id or "").strip(),
        "client_secret": (settings.oauth_client_secret or "").strip(),
        "scope": (settings.oauth_scope or "").strip(),
        "pkce": bool(settings.oauth_pkce_enabled),
    }
    missing = [
        name for name in ("authorize_url", "token_url", "userinfo_url", "client_id")
        if not cfg[name]
    ]
    if missing:
        raise HTTPException(
            status_code=503,
            detail=(
                "OAuth login is not configured. Set "
                + ", ".join(f"OAUTH_{name.upper()}" for name in missing)
                + " to the provider's endpoints / client id."
            ),
        )
    return cfg


def _redirect_uri(request: Request) -> str:
    """Absolute callback URL (must be registered with the provider)."""
    return str(request.base_url).rstrip("/") + "/api/auth/login_callback"


def _safe_next_url(raw: Optional[str]) -> str:
    """Return a post-login redirect target that is safe to hand to a browser.

    Only absolute ``http(s)`` URLs and site-relative paths are accepted;
    ``javascript:`` / ``data:`` / protocol-relative values fall back to
    ``frontend_url``. The target is deliberately NOT pinned to one origin: the
    SPA is either served by this process (``frontend_url='/'``) or by a separate
    dev server, and both must return the user where they came from. Pinning to a
    trusted-origin allowlist is tracked as a follow-up hardening item.
    """
    candidate = (raw or "").strip()
    if not candidate or candidate.startswith("//"):
        return settings.frontend_url
    parsed = urlparse(candidate)
    if parsed.scheme in ("http", "https"):
        return candidate
    if not parsed.scheme and candidate.startswith("/"):
        return candidate
    logger.warning("Rejecting unsafe post-login redirect target: %r", raw)
    return settings.frontend_url


def _build_authorize_url(request: Request, next_url: str) -> str:
    """Build the authorization request and stash state / PKCE / next in session."""
    cfg = _oauth_config()

    state = secrets.token_urlsafe(24)
    request.session[_STATE_KEY] = state
    request.session[_NEXT_KEY] = next_url

    params = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": _redirect_uri(request),
        "scope": cfg["scope"],
        "state": state,
    }

    if cfg["pkce"]:
        # RFC 7636 S256: verifier stays in the session, only its hash travels.
        verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        params["code_challenge"] = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        params["code_challenge_method"] = "S256"
        request.session[_VERIFIER_KEY] = verifier

    query = urlencode(params)
    separator = "&" if "?" in cfg["authorize_url"] else "?"
    return f"{cfg['authorize_url']}{separator}{query}"


async def _exchange_code(request: Request, code: str) -> dict:
    """Exchange the authorization code for an access token (server-side only)."""
    cfg = _oauth_config()
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _redirect_uri(request),
        "client_id": cfg["client_id"],
    }
    if cfg["client_secret"]:
        payload["client_secret"] = cfg["client_secret"]

    verifier = request.session.pop(_VERIFIER_KEY, None)
    if verifier:
        payload["code_verifier"] = verifier

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.post(
                cfg["token_url"],
                data=payload,
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        logger.warning("OAuth token exchange failed: %s", exc)
        raise HTTPException(status_code=502, detail="OAuth token endpoint unreachable")

    if resp.status_code != 200:
        logger.warning(
            "OAuth token exchange rejected: HTTP %s %s",
            resp.status_code, resp.text[:200],
        )
        raise HTTPException(status_code=502, detail="OAuth token exchange rejected")

    try:
        token = resp.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="OAuth token endpoint returned non-JSON")

    if not token.get("access_token"):
        raise HTTPException(status_code=502, detail="OAuth token response has no access_token")
    return token


async def _fetch_userinfo(access_token: str) -> dict:
    """Fetch the authenticated user's profile from the userinfo endpoint."""
    cfg = _oauth_config()
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.get(
                cfg["userinfo_url"],
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
    except httpx.HTTPError as exc:
        logger.warning("OAuth userinfo request failed: %s", exc)
        raise HTTPException(status_code=502, detail="OAuth userinfo endpoint unreachable")

    if resp.status_code != 200:
        logger.warning(
            "OAuth userinfo rejected: HTTP %s %s",
            resp.status_code, resp.text[:200],
        )
        raise HTTPException(status_code=502, detail="OAuth userinfo request rejected")

    try:
        return resp.json()
    except ValueError:
        raise HTTPException(status_code=502, detail="OAuth userinfo returned non-JSON")


def _identity_from_userinfo(profile: dict) -> Tuple[str, str]:
    """Map a provider profile onto ``(email, display_name)``.

    ``email`` is this system's user key (see ``db.models.AgentUser``). Providers
    that expose no email get a stable ``<sub>@<provider-host>`` fallback so the
    account still works and stays unique per provider.
    """
    email = str(profile.get("email") or "").strip()
    name = str(
        profile.get("name")
        or profile.get("preferred_username")
        or profile.get("login")
        or profile.get("nickname")
        or ""
    ).strip()

    if not email:
        subject = str(profile.get("sub") or profile.get("id") or "").strip()
        if not subject:
            raise HTTPException(
                status_code=502,
                detail="OAuth userinfo carries neither email nor sub",
            )
        host = urlparse(_oauth_config()["userinfo_url"]).netloc or "oauth"
        email = f"{subject}@{host}"

    if not name:
        name = email.split("@")[0]

    return email, name


@router.get("/login_required", tags=["auth"])
async def login_required(request: Request):
    """Redirect unauthenticated browser users to the provider's authorize endpoint."""
    origin_url = request.query_params.get("origin_url", settings.frontend_url)
    redirect_url = _build_authorize_url(request, next_url=origin_url)
    return RedirectResponse(redirect_url, status_code=302)


@router.get("/login_callback", tags=["auth"])
async def login_callback(request: Request):
    """Handle the OAuth callback: verify state, exchange the code, write session."""
    provider_error = request.query_params.get("error")
    if provider_error:
        description = request.query_params.get("error_description", "")
        logger.warning("OAuth provider returned error=%s %s", provider_error, description)
        raise HTTPException(
            status_code=400,
            detail=f"OAuth provider returned error: {provider_error}",
        )

    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    expected_state = request.session.pop(_STATE_KEY, None)

    if not code:
        raise HTTPException(status_code=400, detail="Missing authorization code in the OAuth callback")
    if not expected_state or not secrets.compare_digest(state, expected_state):
        logger.warning("OAuth callback state mismatch (possible CSRF)")
        raise HTTPException(status_code=400, detail="OAuth state mismatch — restart the login flow")

    next_url = _safe_next_url(request.session.pop(_NEXT_KEY, None))

    token = await _exchange_code(request, code)
    profile = await _fetch_userinfo(token["access_token"])
    email, name = _identity_from_userinfo(profile)

    # D12: only this system's own user key + display name enter the cookie.
    request.session["email"] = email
    request.session["fullname"] = name

    # Auto-register or refresh the display name (qa_agent_db.agent_users)
    await get_or_register_user(email, name)

    logger.info("User logged in via OAuth: %s", email)
    return RedirectResponse(next_url, status_code=302)


@router.get("/get_login_user", tags=["auth"])
async def get_login_user(request: Request):
    """Return current logged-in user info (Cookie session)."""
    email = request.session.get("email")
    origin_url = request.headers.get("Referer", settings.frontend_url)

    if not email:
        return {
            "code": 300,
            "msg": "请先登录",
            "data": {
                "redirect_url": str(request.base_url).rstrip("/")
                + f"/api/auth/login_required?origin_url={origin_url}",
            },
        }

    user_info = await get_user_info(email)
    if user_info is None:
        return {
            "code": 300,
            "msg": "请先登录",
            "data": {
                "redirect_url": str(request.base_url).rstrip("/")
                + f"/api/auth/login_required?origin_url={origin_url}",
            },
        }

    return {
        "code": 0,
        "msg": "登录成功",
        "data": {"user_info": user_info},
    }


@router.get("/logout", tags=["auth"])
async def logout(request: Request):
    """Clear session and redirect to the frontend origin the user came from.

    Uses the ``Referer`` header's origin (scheme + host + port) so that in dev
    (frontend on :5173, backend on :8000) the user is sent back to the Vite
    dev server rather than trapped on the backend's static-SPA mount at ``/``.
    Falls back to ``settings.frontend_url`` when Referer is missing/unparseable.
    """
    request.session.pop("email", None)
    request.session.pop("fullname", None)

    referer = request.headers.get("Referer", "")
    redirect_target = settings.frontend_url
    if referer:
        try:
            parsed = urlparse(referer)
            if parsed.scheme and parsed.netloc:
                redirect_target = f"{parsed.scheme}://{parsed.netloc}/"
        except Exception:  # noqa: B — defensive, never break logout
            logger.warning("logout: failed to parse Referer=%r, using frontend_url", referer)

    return RedirectResponse(redirect_target, status_code=302)
