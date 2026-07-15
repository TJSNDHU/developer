"""
services/app_state.py — Iter 212m-230

Process-wide runtime state singleton.  Provides a stable service-layer
container for values that used to live on `main.app.state` and were
read by routers via `from main import app` — a boundary violation
that created the last remaining `architecture_health` circular
import (routers → main → routers).

Anything that needs to be read from multiple routers but computed
at boot in main.py should be `set_state(key, value)` here and read
via `get_state(key)`.  This keeps main.py as the sole *entry point*
(nothing else imports it) while still exposing boot-time results.

Public API
==========
    set_state(key: str, value: Any) -> None
    get_state(key: str, default: Any = None) -> Any
    all_state() -> dict
"""
from __future__ import annotations

from threading import RLock
from typing import Any


_state: dict[str, Any] = {}
_lock: RLock = RLock()


def set_state(key: str, value: Any) -> None:
    """Set (or replace) a runtime state value.  Thread-safe."""
    with _lock:
        _state[key] = value


def get_state(key: str, default: Any = None) -> Any:
    """Return the runtime state value for `key`, or `default` if unset."""
    with _lock:
        return _state.get(key, default)


def all_state() -> dict[str, Any]:
    """Return a shallow copy of the entire state dict — useful for
    debug / health endpoints that want to dump the full picture."""
    with _lock:
        return dict(_state)


__all__ = ["set_state", "get_state", "all_state"]
