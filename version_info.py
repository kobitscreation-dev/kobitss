"""Build metadata for the Kobits service (stdlib-only, pure).

This module exposes a single public helper, :func:`get_build_metadata`, that
reports the service name, readiness status and API version as a freshly
constructed mapping on every call.

The module is intentionally dependency-free and side-effect free: it performs
no I/O, reads no environment or configuration, emits no logs, and keeps no
module-level mutable state. The three literal string values returned by the
function are part of the public API contract.
"""

from __future__ import annotations

__all__ = ["get_build_metadata"]


def get_build_metadata() -> dict[str, str]:
    """Return immutable-by-contract build metadata for Kobits.

    A brand-new ``dict`` is constructed on every invocation, so callers can
    freely mutate the result without affecting subsequent calls.

    Returns:
        dict[str, str]: a freshly constructed mapping with keys ``name``
        (``'kobits'``), ``status`` (``'ready'``) and ``api_version``
        (``'v1'``).
    """
    return {"name": "kobits", "status": "ready", "api_version": "v1"}