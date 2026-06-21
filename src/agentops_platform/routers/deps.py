"""
FastAPI dependency injection — shared store and judge instances.

The store and judge are created once at application startup and injected
via FastAPI's Depends mechanism.  Swap implementations here without
touching any router code.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from config.defaults import get_settings
from ..judge import JudgeProtocol, get_judge
from ..repository import MemoryStore

# ── Singletons (module-level, safe for single-process Cloud Run) ──────────────
_store: MemoryStore | None = None
_judge: JudgeProtocol | None = None


def get_store() -> MemoryStore:
    global _store  # noqa: PLW0603
    if _store is None:
        _store = MemoryStore()
    return _store


def get_judge_dep() -> JudgeProtocol:
    global _judge  # noqa: PLW0603
    if _judge is None:
        settings = get_settings()
        _judge = get_judge(settings.judge_backend)
    return _judge


# ── Annotated type aliases for cleaner router signatures ──────────────────────

StoreDep = Annotated[MemoryStore, Depends(get_store)]
JudgeDep = Annotated[JudgeProtocol, Depends(get_judge_dep)]
