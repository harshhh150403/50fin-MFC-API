"""Holdings dataframe construction and aggregation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
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
GROUPING_COLUMNS = ["isin", "folio", "lienSubRefNo", "lienRefNo"]
SOURCE_ROW_COUNT_COLUMN = "sourceRowCount"
AGGREGATED_COLUMNS = [*HOLDING_COLUMNS, SOURCE_ROW_COUNT_COLUMN]
AGGREGATED_COLUMN_LABELS = {
    **COLUMN_LABELS,
    SOURCE_ROW_COUNT_COLUMN: "Source rows",
}
AGGREGATED_NUMERIC_COLUMNS = [*NUMERIC_COLUMNS, SOURCE_ROW_COUNT_COLUMN]


class HoldingsDataError(ValueError):
    """The upstream response does not contain a usable holdings array."""


@dataclass(frozen=True, slots=True)
class AggregationResult:
    """Aggregated holdings plus the integrity checks required by the UI."""

    dataframe: pd.DataFrame
    raw_units_total: Decimal
    aggregated_units_total: Decimal
    null_grouping_rows: int
    inconsistent_total_groups: int

    @property
    def units_preserved(self) -> bool:
        return self.raw_units_total == self.aggregated_units_total


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


def _first_value(series: pd.Series) -> Any:
    return series.iloc[0]


def _decimal_total(series: pd.Series) -> Decimal:
    return sum(
        (Decimal(str(value)) for value in series.dropna()),
        start=Decimal("0"),
    )


def _sum_units(series: pd.Series) -> float:
    values = series.dropna()
    if values.empty:
        return float("nan")
    return float(_decimal_total(values))


def aggregate_holdings(dataframe: pd.DataFrame) -> AggregationResult:
    """Aggregate holdings on the four-field lien identity without dropping nulls."""

    missing_columns = [
        column for column in HOLDING_COLUMNS if column not in dataframe.columns
    ]
    if missing_columns:
        raise HoldingsDataError(
            "The raw holdings table is missing required columns: "
            + ", ".join(missing_columns)
        )

    source = dataframe.loc[:, HOLDING_COLUMNS].copy(deep=True)
    null_grouping_rows = int(source[GROUPING_COLUMNS].isna().any(axis=1).sum())
    grouped = source.groupby(GROUPING_COLUMNS, dropna=False, sort=False)
    inconsistent_total_groups = int(
        grouped["TotalLienUnits"].nunique(dropna=False).gt(1).sum()
    )

    aggregated = grouped.agg(
        rtaName=("rtaName", _first_value),
        amc=("amc", _first_value),
        schemeName=("schemeName", _first_value),
        schemeCode=("schemeCode", _first_value),
        lienHoldUnits=("lienHoldUnits", _sum_units),
        TotalLienUnits=("TotalLienUnits", _first_value),
        sourceRowCount=("rtaName", "size"),
    ).reset_index()
    aggregated = aggregated.reindex(columns=AGGREGATED_COLUMNS)

    return AggregationResult(
        dataframe=aggregated,
        raw_units_total=_decimal_total(source["lienHoldUnits"]),
        aggregated_units_total=_decimal_total(aggregated["lienHoldUnits"]),
        null_grouping_rows=null_grouping_rows,
        inconsistent_total_groups=inconsistent_total_groups,
    )


def _filter_dataframe(
    dataframe: pd.DataFrame,
    *,
    allowed_columns: Sequence[str],
    search_text: str,
    selected_values: Mapping[str, Sequence[Any]] | None,
    sort_by: str | None,
    ascending: bool,
) -> pd.DataFrame:
    if sort_by is not None and sort_by not in allowed_columns:
        raise ValueError(f"Unknown sort column: {sort_by}")

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

    if filtered.empty or sort_by is None:
        return filtered.reset_index(drop=True)

    numeric_columns = (
        AGGREGATED_NUMERIC_COLUMNS
        if SOURCE_ROW_COUNT_COLUMN in allowed_columns
        else NUMERIC_COLUMNS
    )
    if sort_by in numeric_columns:
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


def filter_holdings(
    dataframe: pd.DataFrame,
    *,
    search_text: str = "",
    selected_values: Mapping[str, Sequence[Any]] | None = None,
    sort_by: str | None = None,
    ascending: bool = True,
) -> pd.DataFrame:
    """Return a filtered and sorted copy without mutating the raw dataframe."""

    return _filter_dataframe(
        dataframe,
        allowed_columns=HOLDING_COLUMNS,
        search_text=search_text,
        selected_values=selected_values,
        sort_by=sort_by,
        ascending=ascending,
    )


def filter_aggregated_holdings(
    dataframe: pd.DataFrame,
    *,
    search_text: str = "",
    selected_values: Mapping[str, Sequence[Any]] | None = None,
    sort_by: str | None = None,
    ascending: bool = True,
) -> pd.DataFrame:
    """Return an independently filtered copy of the aggregated table."""

    return _filter_dataframe(
        dataframe,
        allowed_columns=AGGREGATED_COLUMNS,
        search_text=search_text,
        selected_values=selected_values,
        sort_by=sort_by,
        ascending=ascending,
    )
