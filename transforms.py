"""Holdings dataframe construction and aggregation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd


HOLDING_COLUMNS = [
    "rtaName",
    "lienRefNo",
    "amc",
    "folio",
    "schemeName",
    "isin",
    "lienHoldUnits",
    "lienSubRefNo",
    "TotalLienUnits",
    "schemeCode",
]

COLUMN_LABELS = {
    "rtaName": "RTA",
    "lienRefNo": "Lien ref no.",
    "amc": "AMC",
    "folio": "Folio",
    "schemeName": "Scheme name",
    "isin": "ISIN",
    "lienHoldUnits": "Lien hold units",
    "lienSubRefNo": "Lien sub ref no.",
    "TotalLienUnits": "Total lien units",
    "schemeCode": "Scheme code",
}

NUMERIC_COLUMNS = ["lienHoldUnits", "TotalLienUnits"]
REPEATED_VALUE_COLUMNS = ["rtaName", "amc", "schemeName", "folio"]


class HoldingsDataError(ValueError):
    """The upstream response does not contain a usable holdings array."""


def build_holdings_dataframe(payload: Mapping[str, Any]) -> pd.DataFrame:
    """Build the ten-column raw holdings dataframe from ``data.data``."""

    investor = payload.get("data")
    if not isinstance(investor, Mapping):
        raise HoldingsDataError("The response is missing the investor data object.")

    if "data" not in investor:
        raise HoldingsDataError("The response is missing the holdings array.")
    records = investor.get("data")
    if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
        raise HoldingsDataError("The response holdings value is not a list.")
    if not all(isinstance(record, Mapping) for record in records):
        raise HoldingsDataError("One or more holdings rows have an invalid structure.")

    dataframe = pd.DataFrame.from_records(records)
    dataframe = dataframe.reindex(columns=HOLDING_COLUMNS)
    numeric_values = {
        column: pd.to_numeric(dataframe[column], errors="coerce")
        for column in NUMERIC_COLUMNS
    }
    return dataframe.assign(**numeric_values)


def filter_holdings(
    dataframe: pd.DataFrame,
    *,
    search_text: str = "",
    selected_values: Mapping[str, Sequence[Any]] | None = None,
    sort_by: str = "rtaName",
    ascending: bool = True,
) -> pd.DataFrame:
    """Return a filtered and sorted copy without mutating the raw dataframe."""

    if sort_by not in HOLDING_COLUMNS:
        raise ValueError(f"Unknown holdings sort column: {sort_by}")

    filtered = dataframe.copy(deep=True)
    query = search_text.strip()
    if query and not filtered.empty:
        row_text = filtered.fillna("").astype(str).agg(" ".join, axis=1)
        filtered = filtered.loc[
            row_text.str.contains(query, case=False, regex=False, na=False)
        ].copy()

    for column, values in (selected_values or {}).items():
        if column not in REPEATED_VALUE_COLUMNS:
            raise ValueError(f"Unknown holdings filter column: {column}")
        if values:
            filtered = filtered.loc[filtered[column].isin(values)].copy()

    if filtered.empty:
        return filtered.reset_index(drop=True)

    if sort_by in NUMERIC_COLUMNS:
        filtered = filtered.sort_values(
            by=sort_by,
            ascending=ascending,
            na_position="last",
            kind="stable",
        )
    else:
        sort_key = filtered[sort_by].fillna("").astype(str).str.casefold()
        filtered = (
            filtered.assign(_sort_key=sort_key)
            .sort_values(
                by="_sort_key",
                ascending=ascending,
                na_position="last",
                kind="stable",
            )
            .drop(columns="_sort_key")
        )
    return filtered.reset_index(drop=True)
