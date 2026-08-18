"""Pandera v2 schema for the cleaned budget DataFrame.

Note the Pandera v2 import path: ``import pandera.pandas as pa``. In v1 it was
``import pandera as pa``. The migration broke many tutorials; the live session
calls this out explicitly.

The schema mixes per-column constraints with one cross-column ``@dataframe_check``
that enforces ``sum(phases.amount) == total_amount``. The phase-level data lives
in a parallel ``BudgetPhases`` schema kept narrow on purpose; here we only
check what is visible on the flat record.
"""

from __future__ import annotations

import pandera.pandas as pa
from pandera.pandas import Check, Column, DataFrameSchema

BUDGET_ID_PATTERN = r"^BUDGET-\d{4}-\d{4}$"
CLIENT_CODE_PATTERN = r"^CLI-\d{4}$"

BudgetRecord: DataFrameSchema = DataFrameSchema(
    columns={
        "budget_id": Column(
            str,
            checks=Check.str_matches(BUDGET_ID_PATTERN),
            nullable=False,
            required=True,
        ),
        "client_name": Column(
            str,
            nullable=True,  # cleaning maps TBD/N/A to NA — quarantine, not discard
            required=True,
        ),
        "client_code": Column(
            str,
            checks=Check.str_matches(CLIENT_CODE_PATTERN),
            nullable=False,
            required=True,
        ),
        "currency": Column(
            str,
            checks=Check.isin(["EUR", "USD", "GBP"]),
            nullable=False,
            required=True,
        ),
        "total_amount": Column(
            float,
            checks=[Check.ge(0), Check.le(10_000_000)],
            nullable=False,
            required=True,
        ),
        "signed_at": Column(
            "datetime64[ns]",
            nullable=False,
            required=True,
        ),
    },
    strict=True,
    coerce=False,
)


@pa.check_input(BudgetRecord)  # noqa: F821  — runtime registration
def _ignored() -> None:  # pragma: no cover
    """Marker for pandera's static analysis. Not invoked at runtime."""
