"""Shared HTTP client for the upstream MFC API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from config import settings


MFC_PORTFOLIO_PATH = "/mfc_external/api/v1/add_mfc_portfolio"


class MFCClientError(Exception):
    """Base class for expected upstream-client failures."""


class MFCUpstreamTimeout(MFCClientError):
    """The upstream API did not respond before the configured timeout."""


class MFCConnectionError(MFCClientError):
    """The upstream API could not be reached."""


class MFCNonJSONResponse(MFCClientError):
    """The upstream API returned a response that was not a JSON object."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(
            f"The upstream API returned a non-JSON response (HTTP {status_code})."
        )


@dataclass(slots=True)
class MFCResponseError(MFCClientError):
    """An HTTP error response returned by the upstream API."""

    status_code: int
    detail: str

    def __str__(self) -> str:
        return self.detail


_client: httpx.AsyncClient | None = None


async def start_http_client() -> None:
    """Create the one shared client used for the backend process lifetime."""

    global _client
    if _client is not None:
        return

    _client = httpx.AsyncClient(
        base_url=str(settings.mfc_base_url).rstrip("/"),
        headers={
            "clientId": settings.mfc_client_id.get_secret_value(),
            "clientSecret": settings.mfc_client_secret.get_secret_value(),
            "Content-Type": "application/json",
        },
        timeout=httpx.Timeout(settings.mfc_request_timeout_seconds),
    )


async def close_http_client() -> None:
    """Close the shared client during application shutdown."""

    global _client
    if _client is None:
        return
    await _client.aclose()
    _client = None


def _get_http_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError(
            "The upstream HTTP client is not running. Start it during application startup."
        )
    return _client


def _error_detail(payload: dict[str, Any], status_code: int) -> str:
    for key in ("detail", "message", "error"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"The upstream API rejected the request (HTTP {status_code})."


async def lookup_lien_holdings(pan: str, mobile: str) -> dict[str, Any]:
    """Submit one portfolio lookup without retries or background refreshes."""

    client = _get_http_client()
    try:
        response = await client.post(
            MFC_PORTFOLIO_PATH,
            json={
                "pan": pan,
                "phone_number": mobile,
                "email": "",
                "external_id": "",
            },
        )
    except httpx.TimeoutException as exc:
        raise MFCUpstreamTimeout(
            "The upstream MFC API timed out before it returned a response."
        ) from exc
    except httpx.RequestError as exc:
        raise MFCConnectionError(
            "The upstream MFC API could not be reached."
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise MFCNonJSONResponse(response.status_code) from exc

    if not isinstance(payload, dict):
        raise MFCNonJSONResponse(response.status_code)

    if response.is_error:
        raise MFCResponseError(
            status_code=response.status_code,
            detail=_error_detail(payload, response.status_code),
        )

    return payload
