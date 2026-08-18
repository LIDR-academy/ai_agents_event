"""Validation policy router: reparar / cuarentena / descartar.

The Pandera schema can raise on the first failing check, but in production we
want the *full* list of failures — that is what tells the operator which dirt
family hit the run. We use ``lazy=True`` so Pandera collects all failures and
then route each failing row into one of three buckets:

* ``valid``       — passes every check.
* ``quarantined`` — recoverable failures (e.g. ``client_name`` NA after
  cleaning, missing optional metadata). Kept for owner review.
* ``discarded``   — fatal failures (e.g. negative ``total_amount``, malformed
  ``budget_id``). Logged but not retried.

The ``report`` dict is JSON-serializable so it can sit next to the
``ingestion_jobs`` row in future migrations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import pandera.pandas as pa

from app.ingestion.cleaning.schemas import BudgetRecord

# Which failure types belong in quarantine vs. discard. Quarantine = the row
# CAN come back after manual review; discard = the row is structurally wrong.
QUARANTINE_CHECKS = {
    "not_nullable",  # client_name was NA
    "no_default",
    "column_in_dataframe",  # missing optional metadata column
}
DISCARD_CHECKS = {
    "in_range",  # total_amount negative / too large
    "less_than_or_equal_to",
    "greater_than_or_equal_to",
    "isin",  # currency not in {EUR,USD,GBP}
    "str_matches",  # malformed budget_id or client_code
}


@dataclass
class ValidationResult:
    valid: pd.DataFrame
    quarantined: pd.DataFrame
    discarded: pd.DataFrame
    report: dict[str, Any] = field(default_factory=dict)


def validate_with_policy(df: pd.DataFrame) -> ValidationResult:
    """Run :data:`BudgetRecord` against ``df`` and partition the rows."""
    if df.empty:
        return ValidationResult(
            valid=df.copy(),
            quarantined=df.iloc[0:0].copy(),
            discarded=df.iloc[0:0].copy(),
            report={"input_rows": 0},
        )

    try:
        validated = BudgetRecord.validate(df, lazy=True)
        return ValidationResult(
            valid=validated,
            quarantined=df.iloc[0:0].copy(),
            discarded=df.iloc[0:0].copy(),
            report={
                "input_rows": len(df),
                "valid_rows": len(validated),
                "quarantined_rows": 0,
                "discarded_rows": 0,
                "failures_by_check": {},
            },
        )
    except pa.errors.SchemaErrors as err:
        return _route_failures(df, err)


def _route_failures(df: pd.DataFrame, err: pa.errors.SchemaErrors) -> ValidationResult:
    failure_cases = err.failure_cases.copy()
    indices_quarantine: set[int] = set()
    indices_discard: set[int] = set()
    failures_by_check: dict[str, int] = {}

    for _, row in failure_cases.iterrows():
        check_name = str(row.get("check", "unknown"))
        failures_by_check[check_name] = failures_by_check.get(check_name, 0) + 1
        idx = row.get("index")
        if idx is None or pd.isna(idx):
            continue
        try:
            row_idx = int(idx)
        except (TypeError, ValueError):
            continue

        if any(disc in check_name for disc in DISCARD_CHECKS):
            indices_discard.add(row_idx)
        elif any(quar in check_name for quar in QUARANTINE_CHECKS):
            indices_quarantine.add(row_idx)
        else:
            # Unknown failure type — be safe, quarantine for review.
            indices_quarantine.add(row_idx)

    # Rows in discard set always lose, even if they also hit a quarantine check.
    indices_quarantine -= indices_discard
    discarded_mask = df.index.isin(indices_discard)
    quarantined_mask = df.index.isin(indices_quarantine)
    valid_mask = ~(discarded_mask | quarantined_mask)

    return ValidationResult(
        valid=df.loc[valid_mask].copy(),
        quarantined=df.loc[quarantined_mask].copy(),
        discarded=df.loc[discarded_mask].copy(),
        report={
            "input_rows": len(df),
            "valid_rows": int(valid_mask.sum()),
            "quarantined_rows": int(quarantined_mask.sum()),
            "discarded_rows": int(discarded_mask.sum()),
            "failures_by_check": failures_by_check,
        },
    )
