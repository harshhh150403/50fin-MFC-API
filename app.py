"""Streamlit user interface."""

from __future__ import annotations

import os
import re
from typing import Any

import httpx
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from transforms import (
    COLUMN_LABELS,
    HOLDING_COLUMNS,
    REPEATED_VALUE_COLUMNS,
    HoldingsDataError,
    build_holdings_dataframe,
    filter_holdings,
)


PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
MOBILE_PATTERN = re.compile(r"^[6-9][0-9]{9}$")
LOOKUP_PATH = "/api/lien-holdings"


class FrontendRequestError(Exception):
    """A readable backend-call failure safe to show in the UI."""


def normalise_pan(value: str) -> tuple[str | None, str | None]:
    normalised = value.strip().upper()
    if not PAN_PATTERN.fullmatch(normalised):
        return None, "Enter a valid PAN: five letters, four digits, and one final letter."
    return normalised, None


def normalise_mobile(value: str) -> tuple[str | None, str | None]:
    normalised = re.sub(r"[\s-]", "", value.strip())
    if normalised.startswith("+91"):
        normalised = normalised[3:]
    elif normalised.startswith("0"):
        normalised = normalised[1:]

    if not MOBILE_PATTERN.fullmatch(normalised):
        return None, (
            "Enter a valid ten-digit mobile number starting with 6, 7, 8, or 9."
        )
    return normalised, None


def _backend_settings() -> tuple[str, float]:
    load_dotenv()
    backend_url = os.getenv("BACKEND_API_URL", "").strip().rstrip("/")
    if not backend_url:
        raise FrontendRequestError(
            "BACKEND_API_URL is missing. Add it to .env, then restart Streamlit."
        )

    raw_timeout = os.getenv("BACKEND_REQUEST_TIMEOUT_SECONDS", "30")
    try:
        timeout = float(raw_timeout)
    except ValueError as exc:
        raise FrontendRequestError(
            "BACKEND_REQUEST_TIMEOUT_SECONDS must be a number in .env."
        ) from exc
    if timeout <= 0:
        raise FrontendRequestError(
            "BACKEND_REQUEST_TIMEOUT_SECONDS must be greater than zero in .env."
        )
    return backend_url, timeout


def _response_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return "The backend returned an unexpected non-JSON response."
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
    return f"The backend rejected the request (HTTP {response.status_code})."


def submit_lookup(pan: str, mobile: str) -> dict[str, Any]:
    backend_url, timeout = _backend_settings()
    try:
        response = httpx.post(
            f"{backend_url}{LOOKUP_PATH}",
            json={"pan": pan, "mobile": mobile},
            timeout=timeout,
        )
    except httpx.ConnectError as exc:
        host = os.getenv("BACKEND_HOST", "127.0.0.1")
        port = os.getenv("BACKEND_PORT", "8000")
        raise FrontendRequestError(
            "The backend isn't running. Start it with: "
            f".venv/bin/uvicorn backend:app --host {host} --port {port}"
        ) from exc
    except httpx.TimeoutException as exc:
        raise FrontendRequestError(
            "The backend timed out. The request outcome is unknown and it was not "
            "retried automatically."
        ) from exc
    except httpx.RequestError as exc:
        raise FrontendRequestError(
            "The frontend could not reach the backend. Check the backend URL and network."
        ) from exc

    if response.is_error:
        raise FrontendRequestError(_response_detail(response))

    try:
        payload = response.json()
    except ValueError as exc:
        raise FrontendRequestError(
            "The backend returned an unexpected non-JSON response. Restart it and try again."
        ) from exc
    if not isinstance(payload, dict):
        raise FrontendRequestError(
            "The backend returned an unexpected response. Restart it and try again."
        )
    return payload


def _display_value(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return str(value)


def render_investor_summary(payload: dict[str, Any]) -> None:
    investor = payload.get("data")
    if not isinstance(investor, dict):
        st.error(
            "The MFC API response is missing investor details. Check the upstream service."
        )
        return

    with st.container(border=True):
        st.subheader(_display_value(investor.get("clientName")))
        first_row = st.columns(3)
        second_row = st.columns(3)
        fields = (
            ("PAN", investor.get("pan")),
            ("Mobile", investor.get("mobile")),
            ("Email", investor.get("email")),
            ("Client ID", investor.get("clientId")),
            ("Lender code", investor.get("lenderCode")),
            ("Request ID", investor.get("reqId")),
        )
        for column, (label, value) in zip((*first_row, *second_row), fields):
            with column:
                st.caption(label)
                st.markdown(f"**{_display_value(value)}**")

        code = _display_value(payload.get("code"))
        detail = _display_value(payload.get("detail"))
        st.caption(f"Result code {code} · {detail}")


RAW_FILTER_KEYS = (
    "raw_sort_by",
    "raw_sort_direction",
    "raw_search",
    "raw_rtaName",
    "raw_amc",
    "raw_schemeName",
    "raw_folio",
)


def clear_raw_filters() -> None:
    for key in RAW_FILTER_KEYS:
        st.session_state.pop(key, None)


def _filter_options(dataframe: pd.DataFrame, column: str) -> list[Any]:
    values = dataframe[column].dropna().unique().tolist()
    return sorted(values, key=lambda value: str(value).casefold())


def render_holdings_table(dataframe: pd.DataFrame) -> None:
    st.subheader("Holdings")
    st.button(
        "Clear filters",
        key="raw_clear_filters",
        on_click=clear_raw_filters,
    )

    sort_columns = st.columns([2, 2, 3])
    with sort_columns[0]:
        sort_by = st.selectbox(
            "Sort by",
            options=HOLDING_COLUMNS,
            format_func=lambda column: COLUMN_LABELS[column],
            key="raw_sort_by",
        )
    with sort_columns[1]:
        sort_direction = st.radio(
            "Direction",
            options=("Ascending", "Descending"),
            horizontal=True,
            key="raw_sort_direction",
        )
    with sort_columns[2]:
        search_text = st.text_input(
            "Search all holdings",
            placeholder="Paste an ISIN or enter part of a scheme name",
            key="raw_search",
        )

    filter_columns = st.columns(4)
    selected_values: dict[str, list[Any]] = {}
    for container, column in zip(filter_columns, REPEATED_VALUE_COLUMNS):
        with container:
            selected_values[column] = st.multiselect(
                COLUMN_LABELS[column],
                options=_filter_options(dataframe, column),
                key=f"raw_{column}",
            )

    filtered = filter_holdings(
        dataframe,
        search_text=search_text,
        selected_values=selected_values,
        sort_by=sort_by,
        ascending=sort_direction == "Ascending",
    )
    st.caption(f"{len(filtered):,} of {len(dataframe):,} holdings")

    display_dataframe = filtered.rename(columns=COLUMN_LABELS)
    st.dataframe(
        display_dataframe,
        hide_index=True,
        use_container_width=True,
        column_config={
            COLUMN_LABELS["lienHoldUnits"]: st.column_config.NumberColumn(
                COLUMN_LABELS["lienHoldUnits"], format="%.4f"
            ),
            COLUMN_LABELS["TotalLienUnits"]: st.column_config.NumberColumn(
                COLUMN_LABELS["TotalLienUnits"], format="%.4f"
            ),
        },
    )
    st.download_button(
        "Download filtered holdings as CSV",
        data=display_dataframe.to_csv(index=False).encode("utf-8"),
        file_name="mfc_lien_holdings.csv",
        mime="text/csv",
        key="raw_csv_download",
        disabled=display_dataframe.empty,
    )


def main() -> None:
    st.set_page_config(
        page_title="MFC lien holdings",
        layout="wide",
    )
    st.title("MFC lien holdings")
    st.write("Look up an investor's mutual fund lien holdings by PAN and mobile number.")

    st.session_state.setdefault("lookup_result", None)
    st.session_state.setdefault("lookup_error", None)

    with st.form("lien_lookup_form", clear_on_submit=False):
        form_columns = st.columns(2)
        with form_columns[0]:
            pan_input = st.text_input(
                "PAN",
                placeholder="ABCDE1234F",
                help="Five letters, four digits, and one final letter.",
            )
        with form_columns[1]:
            mobile_input = st.text_input(
                "Mobile number",
                placeholder="9876543210",
                help="Ten digits starting with 6, 7, 8, or 9.",
            )
        submitted = st.form_submit_button(
            "Look up holdings",
            type="primary",
            use_container_width=False,
        )

    if submitted:
        st.session_state["lookup_result"] = None
        st.session_state["lookup_error"] = None
        pan, pan_error = normalise_pan(pan_input)
        mobile, mobile_error = normalise_mobile(mobile_input)
        validation_errors = [error for error in (pan_error, mobile_error) if error]

        if validation_errors:
            st.session_state["lookup_error"] = " ".join(validation_errors)
        else:
            with st.spinner("Requesting lien holdings…"):
                try:
                    st.session_state["lookup_result"] = submit_lookup(pan, mobile)
                    clear_raw_filters()
                except FrontendRequestError as exc:
                    st.session_state["lookup_error"] = str(exc)

    if st.session_state["lookup_error"]:
        st.error(st.session_state["lookup_error"])

    result = st.session_state["lookup_result"]
    if isinstance(result, dict):
        st.divider()
        render_investor_summary(result)
        try:
            holdings = build_holdings_dataframe(result)
        except HoldingsDataError as exc:
            st.error(
                f"The MFC API returned holdings in an unexpected format: {exc} "
                "Check the upstream service before submitting again."
            )
        else:
            if holdings.empty:
                st.info("This PAN has no lien holdings.")
            else:
                render_holdings_table(holdings)


if __name__ == "__main__":
    main()
