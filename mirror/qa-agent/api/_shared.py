"""
QA Agent System — Adapter shared constants

Symbols shared by the adapter layers.
"""

from __future__ import annotations

#: Default identity stamped on adapter-created sessions when the
#: caller does not provide a ``user_id``.  Shared by the adapter layers so
#: that switching between them does not change ownership semantics in MongoDB.
DEFAULT_EMAIL = "local"
