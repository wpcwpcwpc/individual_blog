"""
QA Agent System — Auth Service (Beanie ODM)

Handles the qa-agent self-managed user auth store (qa_agent_db.agent_users).
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def get_user_info(email: str) -> Optional[dict]:
    """Fetch user info from qa_agent_db.agent_users.

    Returns:
        {email, name, role} dict or None if not found.
    """
    try:
        from db.models import AgentUser
        doc = await AgentUser.find_one(AgentUser.email == email)
        if doc is None:
            return None
        return {"email": doc.email, "name": doc.name, "role": doc.role}
    except Exception:
        logger.warning("get_user_info failed for email=%s", email, exc_info=True)
        return None


async def get_or_register_user(email: str, fullname: str) -> dict:
    """Get existing user or register new user in qa_agent_db.agent_users.

    Upsert semantics:
      - Not exists → insert {email, name: fullname, role: "normal"}
      - Exists     → $set {name: fullname}, role untouched (ops-managed)

    Returns:
        {email, name, role} dict.
    """
    try:
        from db.models import AgentUser

        doc = await AgentUser.find_one(AgentUser.email == email)
        if doc is not None:
            # Update name from the identity provider; never touch role (ops-managed).
            if doc.name != fullname:
                doc.name = fullname
                await doc.save()
            return {"email": doc.email, "name": doc.name, "role": doc.role}

        # Register new user
        new_doc = AgentUser(email=email, name=fullname, role="normal")
        await new_doc.insert()
        logger.info("Registered new user: %s", email)
        return {"email": new_doc.email, "name": new_doc.name, "role": new_doc.role}

    except Exception:
        logger.warning("get_or_register_user failed for email=%s", email, exc_info=True)
        return {"email": email, "name": fullname, "role": "normal"}
