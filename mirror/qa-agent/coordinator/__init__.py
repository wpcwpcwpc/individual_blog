"""
QA Agent System — Coordinator Package

Multi-agent coordination for QA task orchestration.

Main entry points:
- ``create_coordinator_agent()`` — New Agent-based Coordinator (recommended)
- ``create_coordinator_session()`` — Convenience wrapper
- ``create_coordinator_team()`` — Legacy Team-based Coordinator (deprecated)
"""

from coordinator.coordinator_agent import create_coordinator_agent
from coordinator.team_builder import create_coordinator_session, create_coordinator_team
from coordinator.worker_pool import WorkerEntry, WorkerPool, WorkerStatus, worker_pool

__all__ = [
    "create_coordinator_agent",
    "create_coordinator_session",
    "create_coordinator_team",
    "WorkerEntry",
    "WorkerPool",
    "WorkerStatus",
    "worker_pool",
]