"""FastAPI backend application."""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from api_client import (
    MFCConnectionError,
    MFCNonJSONResponse,
    MFCResponseError,
    MFCUpstreamTimeout,
    close_http_client,
    lookup_lien_holdings,
    start_http_client,
)
from config import settings


PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
MOBILE_PATTERN = re.compile(r"^[6-9][0-9]{9}$")


class LienLookupRequest(BaseModel):
    """Validated lookup input accepted by the backend."""

    pan: str = Field(description="Indian Permanent Account Number")
    mobile: str = Field(description="Ten-digit Indian mobile number")

    @field_validator("pan", mode="before")
    @classmethod
    def normalise_pan(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("PAN must be text")
        normalised = value.strip().upper()
        if not PAN_PATTERN.fullmatch(normalised):
            raise ValueError(
                "PAN must contain five letters, four digits, and one final letter"
            )
        return normalised

    @field_validator("mobile", mode="before")
    @classmethod
    def normalise_mobile(cls, value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("Mobile number must be text")

        normalised = re.sub(r"[\s-]", "", value.strip())
        if normalised.startswith("+91"):
            normalised = normalised[3:]
        elif normalised.startswith("0"):
            normalised = normalised[1:]

        if not MOBILE_PATTERN.fullmatch(normalised):
            raise ValueError(
                "Mobile number must have ten digits and start with 6, 7, 8, or 9"
            )
        return normalised


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await start_http_client()
    try:
        yield
    finally:
        await close_http_client()


app = FastAPI(
    title="50fin MFC lien holdings API",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    first_error = exc.errors()[0] if exc.errors() else {}
    message = str(first_error.get("msg", "The submitted details are invalid."))
    if message.startswith("Value error, "):
        message = message.removeprefix("Value error, ")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": message},
    )


def _credentials_loaded() -> bool:
    return bool(
        settings.mfc_client_id.get_secret_value().strip()
        and settings.mfc_client_secret.get_secret_value().strip()
    )


def _response_code(payload: dict[str, Any]) -> int | None:
    code = payload.get("code")
    if isinstance(code, bool):
        return None
    if isinstance(code, int):
        return code
    if isinstance(code, str) and code.strip().isdigit():
        return int(code.strip())
    return None


def _upstream_http_error(status_code: int) -> HTTPException:
    if status_code == status.HTTP_401_UNAUTHORIZED:
        return HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "The MFC API rejected the configured credentials. Check the client ID "
                "and client secret in .env, then restart the backend."
            ),
        )
    if status_code == status.HTTP_400_BAD_REQUEST:
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "The MFC API rejected the PAN or mobile number. Check both values and "
                "submit again."
            ),
        )
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=(
            f"The MFC API rejected the request (code {status_code}). Check the "
            "upstream service before submitting again."
        ),
    )


@app.get("/health")
async def health() -> dict[str, Any]:
    loaded = _credentials_loaded()
    return {
        "status": "ok" if loaded else "configuration_error",
        "credentials_loaded": loaded,
    }


@app.post("/api/lien-holdings")
async def get_lien_holdings(lookup: LienLookupRequest) -> dict[str, Any]:
    try:
        payload = await lookup_lien_holdings(lookup.pan, lookup.mobile)
    except MFCUpstreamTimeout as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=(
                "The MFC API timed out. The outcome is unknown, so this request was not "
                "retried automatically."
            ),
        ) from exc
    except MFCConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "The backend could not reach the MFC API. Check the network connection "
                "and upstream service."
            ),
        ) from exc
    except MFCNonJSONResponse as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "The MFC API returned an unexpected non-JSON response. Check the "
                "upstream service before submitting again."
            ),
        ) from exc
    except MFCResponseError as exc:
        raise _upstream_http_error(exc.status_code) from exc

    response_code = _response_code(payload)
    if response_code != status.HTTP_200_OK:
        if response_code is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "The MFC API response did not include a valid result code. Check the "
                    "upstream service before submitting again."
                ),
            )
        raise _upstream_http_error(response_code)

    return payload
