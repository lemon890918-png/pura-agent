"""Auth middleware: simple API key check for non-localhost requests.

Phase 9. Header X-API-Key: <key> or ?api_key=<key>.
Localhost is exempt (for dev convenience).
"""

from __future__ import annotations

import os

from fastapi import HTTPException, Request, status


def _is_localhost(host: str | None) -> bool:
    if host is None:
        return True
    return host.startswith("127.") or host.startswith("localhost") or host == "::1"


def check_api_key(request: Request) -> None:
    """Raise 401 if the request needs a key and doesn't have one.

    The expected key is read from PURE_AGENT_API_KEY env var.
    If no key is set, all requests are allowed (dev mode).
    """
    expected = os.environ.get("PURE_AGENT_API_KEY", "")
    if not expected:
        return  # no key configured → open access
    # localhost bypass (dev)
    if _is_localhost(request.client.host if request.client else None):
        return
    # check header
    api_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    if api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing API key",
        )


__all__ = ["check_api_key"]
