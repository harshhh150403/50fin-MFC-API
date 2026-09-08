"""Streamlit user interface."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any

import httpx
import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from transforms import (
    AGGREGATED_COLUMNS,
    AGGREGATED_COLUMN_LABELS,
    AGGREGATED_NUMERIC_COLUMNS,
    COLUMN_LABELS,
    HOLDING_COLUMNS,
    NUMERIC_COLUMNS,
    REPEATED_VALUE_COLUMNS,
    SOURCE_ROW_COUNT_COLUMN,
    AggregationResult,
    HoldingsDataError,
    aggregate_holdings,
    build_holdings_dataframe,
    filter_aggregated_holdings,
    filter_holdings,
)


PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
MOBILE_PATTERN = re.compile(r"^[6-9][0-9]{9}$")
LOOKUP_PATH = "/api/lien-holdings"
LOGIN_PASSWORD_ENV = "APP_LOGIN_PASSWORD"
AUTHENTICATED_SESSION_KEY = "authenticated"
LOGIN_ERROR_SESSION_KEY = "login_error"


class FrontendRequestError(Exception):
    """A readable backend-call failure safe to show in the UI."""


def _configured_login_password() -> str | None:
    load_dotenv()
    password = os.getenv(LOGIN_PASSWORD_ENV)
    return password if password and password.strip() else None


def passwords_match(provided: str, expected: str) -> bool:
    """Compare fixed-length password digests without exposing either password."""

    if len(provided) > 256:
        return False
    provided_digest = hashlib.sha256(provided.encode("utf-8")).digest()
    expected_digest = hashlib.sha256(expected.encode("utf-8")).digest()
    return hmac.compare_digest(provided_digest, expected_digest)


def log_out() -> None:
    """Remove authentication and any cached investor data from this session."""

    st.session_state.clear()


def render_login_gate() -> bool:
    """Render the shared-password gate and report whether access is allowed."""

    expected_password = _configured_login_password()
    if expected_password is None:
        st.title("MFC lien holdings")
        st.write("Secure access to the mutual fund lien holdings lookup.")
        st.error(
            "APP_LOGIN_PASSWORD is missing or blank. Add a strong shared password to "
            ".env, then restart Streamlit."
        )
        return False

    if st.session_state.get(AUTHENTICATED_SESSION_KEY) is True:
        return True

    st.title("MFC lien holdings")
    st.write("Secure access to the mutual fund lien holdings lookup.")
    st.subheader("Sign in")
    st.write("Enter the shared access password to continue.")
    with st.form("login_form", clear_on_submit=True):
        password = st.text_input(
            "Password",
            type="password",
            autocomplete="off",
        )
        submitted = st.form_submit_button(
            "Sign in",
            type="primary",
            use_container_width=False,
        )

    if submitted:
        if passwords_match(password, expected_password):
            st.session_state[AUTHENTICATED_SESSION_KEY] = True
            st.session_state.pop(LOGIN_ERROR_SESSION_KEY, None)
            st.rerun()
        else:
            st.session_state[LOGIN_ERROR_SESSION_KEY] = (
                "Incorrect password. Check it and try again."
            )

    if st.session_state.get(LOGIN_ERROR_SESSION_KEY):
        st.error(st.session_state[LOGIN_ERROR_SESSION_KEY])
    return False


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


def _backend_settings() -> tuple[str, float, str, str]:
    load_dotenv()
    backend_url = os.getenv("BACKEND_API_URL", "").strip().rstrip("/")
    if not backend_url:
        raise FrontendRequestError(
            "BACKEND_API_URL is missing. Add it to .env, then restart Streamlit."
        )
    try:
        parsed_url = httpx.URL(backend_url)
    except httpx.InvalidURL as exc:
        raise FrontendRequestError(
            "BACKEND_API_URL is invalid. Enter a complete HTTP or HTTPS URL in .env."
        ) from exc
    if parsed_url.scheme not in ("http", "https") or not parsed_url.host:
        raise FrontendRequestError(
            "BACKEND_API_URL is invalid. Enter a complete HTTP or HTTPS URL in .env."
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

    backend_host = os.getenv("BACKEND_HOST", "").strip()
    backend_port = os.getenv("BACKEND_PORT", "").strip()
    if not backend_host or not backend_port:
        raise FrontendRequestError(
            "BACKEND_HOST and BACKEND_PORT are missing. Add them to .env, then restart "
            "Streamlit."
        )
    if not backend_port.isdigit() or not 1 <= int(backend_port) <= 65535:
        raise FrontendRequestError(
            "BACKEND_PORT must be a whole number from 1 to 65535 in .env."
        )
    return backend_url, timeout, backend_host, backend_port


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
    backend_url, timeout, backend_host, backend_port = _backend_settings()
    try:
        response = httpx.post(
            f"{backend_url}{LOOKUP_PATH}",
            json={"pan": pan, "mobile": mobile},
            timeout=timeout,
        )
    except httpx.ConnectError as exc:
        raise FrontendRequestError(
            f"The backend could not be reached at {backend_url}. Start it with: "
            "python -m uvicorn backend:app "
            f"--host {backend_host} --port {backend_port}. If it is already running, "
            "check BACKEND_API_URL in .env."
        ) from exc
    except httpx.TimeoutException as exc:
        raise FrontendRequestError(
            "The backend timed out. The request outcome is unknown, so it was not "
            "retried automatically. Check whether it was processed before submitting "
            "again."
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
            "The backend returned an unexpected non-JSON response. Check the backend "
            "terminal for errors, then restart it."
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
        st.subheader("Investor details")
        columns = st.columns(3)
        fields = (
            ("PAN", investor.get("pan")),
            ("Mobile", investor.get("mobile")),
            ("Email", investor.get("email")),
        )
        for column, (label, value) in zip(columns, fields):
            with column:
                st.caption(label)
                st.markdown(f"**{_display_value(value)}**")


FILTER_KEY_SUFFIXES = (
    "sort_by",
    "sort_direction",
    "search",
    *REPEATED_VALUE_COLUMNS,
)


def clear_table_filters(key_prefix: str) -> None:
    for suffix in FILTER_KEY_SUFFIXES:
        st.session_state.pop(f"{key_prefix}_{suffix}", None)


def clear_raw_filters() -> None:
    clear_table_filters("raw")


def _filter_options(dataframe: pd.DataFrame, column: str) -> list[Any]:
    values = dataframe[column].dropna().unique().tolist()
    return sorted(values, key=lambda value: str(value).casefold())


def _sort_options(columns: Sequence[str]) -> list[str | None]:
    return [None, "isin", *(column for column in columns if column != "isin")]


def render_filterable_table(
    dataframe: pd.DataFrame,
    *,
    heading: str | None,
    key_prefix: str,
    columns: Sequence[str],
    labels: dict[str, str],
    numeric_columns: Sequence[str],
    filter_function: Callable[..., pd.DataFrame],
    row_label: str,
    download_label: str,
    file_name: str,
) -> None:
    if heading:
        st.subheader(heading)
    st.button(
        "Clear filters",
        key=f"{key_prefix}_clear_filters",
        on_click=clear_table_filters,
        args=(key_prefix,),
    )

    sort_columns = st.columns([2, 2, 3])
    with sort_columns[0]:
        sort_by = st.selectbox(
            "Sort by",
            options=_sort_options(columns),
            format_func=lambda column: "Select" if column is None else labels[column],
            key=f"{key_prefix}_sort_by",
        )
    with sort_columns[1]:
        sort_direction = st.radio(
            "Direction",
            options=("Ascending", "Descending"),
            horizontal=True,
            key=f"{key_prefix}_sort_direction",
            disabled=sort_by is None,
        )
    with sort_columns[2]:
        search_text = st.text_input(
            "Search all holdings",
            placeholder="Paste an ISIN or enter part of a scheme name",
            key=f"{key_prefix}_search",
        )

    filter_columns = st.columns(4)
    selected_values: dict[str, list[Any]] = {}
    for container, column in zip(filter_columns, REPEATED_VALUE_COLUMNS):
        with container:
            selected_values[column] = st.multiselect(
                labels[column],
                options=_filter_options(dataframe, column),
                key=f"{key_prefix}_{column}",
            )

    filtered = filter_function(
        dataframe,
        search_text=search_text,
        selected_values=selected_values,
        sort_by=sort_by,
        ascending=sort_direction == "Ascending",
    )
    st.caption(f"{len(filtered):,} of {len(dataframe):,} {row_label}")

    export_dataframe = filtered.rename(columns=labels).copy()
    display_dataframe = export_dataframe.copy()
    for column in columns:
        if column not in numeric_columns:
            label = labels[column]
            display_dataframe.loc[:, label] = display_dataframe[label].fillna("—")
    display_dataframe.insert(0, "Index", range(1, len(display_dataframe) + 1))
    column_config: dict[str, Any] = {
        "Index": st.column_config.NumberColumn("Index", format="%d", width="small")
    }
    for column in numeric_columns:
        label = labels[column]
        number_format = "%d" if column == SOURCE_ROW_COUNT_COLUMN else "%.4f"
        column_config[label] = st.column_config.NumberColumn(
            label,
            format=number_format,
        )
    st.dataframe(
        display_dataframe,
        hide_index=True,
        use_container_width=True,
        column_config=column_config,
    )
    st.download_button(
        download_label,
        data=export_dataframe.to_csv(index=False).encode("utf-8"),
        file_name=file_name,
        mime="text/csv",
        key=f"{key_prefix}_csv_download",
        disabled=export_dataframe.empty,
    )


def render_holdings_table(dataframe: pd.DataFrame) -> None:
    render_filterable_table(
        dataframe,
        heading="Holdings",
        key_prefix="raw",
        columns=HOLDING_COLUMNS,
        labels=COLUMN_LABELS,
        numeric_columns=NUMERIC_COLUMNS,
        filter_function=filter_holdings,
        row_label="holdings",
        download_label="Download filtered holdings as CSV",
        file_name="mfc_lien_holdings.csv",
    )


def _format_units(total: Decimal) -> str:
    return f"{total:,.4f}"


def render_aggregated_holdings(result: AggregationResult) -> None:
    st.divider()
    st.subheader("Aggregated holdings")
    total_columns = st.columns(2)
    total_columns[0].metric(
        "Raw lien hold units",
        _format_units(result.raw_units_total),
    )
    total_columns[1].metric(
        "Aggregated lien hold units",
        _format_units(result.aggregated_units_total),
    )

    if result.units_preserved:
        st.caption("Unit check passed: aggregation preserved the full units total.")
    else:
        st.error(
            "Unit check failed: raw and aggregated lien hold units do not match. "
            "Review the source data before using this table."
        )

    if result.null_grouping_rows:
        row_word = "row" if result.null_grouping_rows == 1 else "rows"
        verb = "has" if result.null_grouping_rows == 1 else "have"
        retention = "It was" if result.null_grouping_rows == 1 else "They were"
        st.warning(
            f"{result.null_grouping_rows:,} source {row_word} {verb} at least one missing "
            f"grouping key. {retention} retained in the aggregated table."
        )
    else:
        st.caption("Source rows with a missing grouping key: 0.")

    if result.inconsistent_total_groups:
        group_word = (
            "group" if result.inconsistent_total_groups == 1 else "groups"
        )
        verb = "varies" if result.inconsistent_total_groups == 1 else "vary"
        st.warning(
            f"Total lien units {verb} within {result.inconsistent_total_groups:,} "
            f"aggregated {group_word}. The first value in each group was retained."
        )

    render_filterable_table(
        result.dataframe,
        heading=None,
        key_prefix="aggregated",
        columns=AGGREGATED_COLUMNS,
        labels=AGGREGATED_COLUMN_LABELS,
        numeric_columns=AGGREGATED_NUMERIC_COLUMNS,
        filter_function=filter_aggregated_holdings,
        row_label="aggregated holdings",
        download_label="Download filtered aggregation as CSV",
        file_name="mfc_aggregated_lien_holdings.csv",
    )


def main() -> None:
    st.set_page_config(
        page_title="MFC lien holdings",
        layout="wide",
    )
    st.markdown(
        """
        <style>
        button[kind="primary"],
        button[kind="primaryFormSubmit"] {
            background-color: #a83232 !important;
            border-color: #a83232 !important;
            color: #ffffff !important;
        }
        button[kind="primary"]:hover,
        button[kind="primaryFormSubmit"]:hover {
            background-color: #8f2929 !important;
            border-color: #8f2929 !important;
            color: #ffffff !important;
        }
        div[data-testid="InputInstructions"] {
            display: none;
        }
        @media (max-width: 640px) {
            div[data-testid="stTextInput"] input,
            div[data-testid="stTextInput"] button,
            button[kind="primary"],
            button[kind="primaryFormSubmit"],
            button[kind="secondary"] {
                min-height: 44px;
            }
            div[data-testid="stTextInput"] button {
                min-width: 44px;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    if not render_login_gate():
        return

    title_columns = st.columns([6, 1])
    with title_columns[0]:
        st.title("MFC lien holdings")
    with title_columns[1]:
        st.button("Log out", on_click=log_out, use_container_width=True)
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
                autocomplete="off",
            )
        with form_columns[1]:
            mobile_input = st.text_input(
                "Mobile number",
                placeholder="9876543210",
                help="Ten digits starting with 6, 7, 8, or 9.",
                autocomplete="off",
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
                    clear_table_filters("aggregated")
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
                render_aggregated_holdings(aggregate_holdings(holdings))


if __name__ == "__main__":
    main()
