"""Tests for clean_budget_records — the four dirt families and dedup policy."""
from __future__ import annotations

import pandas as pd

from app.ingestion.cleaning.budget_records import clean_budget_records


def test_disguised_nulls_become_NA():
    df = clean_budget_records(
        [
            {
                "budget_id": "BUDGET-2024-0001",
                "client_name": "TBD",
                "client_code": "CLI-0001",
                "currency": "EUR",
                "total_amount": 100,
                "signed_at": "2024-01-01",
            },
        ]
    )
    assert pd.isna(df.loc[0, "client_name"])


def test_currency_casing_collapses_to_upper():
    df = clean_budget_records(
        [
            {
                "budget_id": "BUDGET-2024-0002",
                "client_name": "Beta",
                "client_code": "CLI-0002",
                "currency": "eur",
                "total_amount": 100,
                "signed_at": "2024-01-02",
            }
        ]
    )
    assert df.loc[0, "currency"] == "EUR"


def test_date_coercion_handles_spanish_format():
    df = clean_budget_records(
        [
            {
                "budget_id": "BUDGET-2024-0003",
                "client_name": "Gamma",
                "client_code": "CLI-0003",
                "currency": "EUR",
                "total_amount": 100,
                "signed_at": "12/03/2024",
            }
        ]
    )
    parsed = df.loc[0, "signed_at"]
    assert parsed.year == 2024
    assert parsed.month == 3
    assert parsed.day == 12


def test_numeric_coercion_handles_strings():
    df = clean_budget_records(
        [
            {
                "budget_id": "BUDGET-2024-0004",
                "client_name": "Delta",
                "client_code": "CLI-0004",
                "currency": "EUR",
                "total_amount": "not-a-number",
                "signed_at": "2024-01-04",
            }
        ]
    )
    assert pd.isna(df.loc[0, "total_amount"])


def test_dedup_keeps_latest_signed_at():
    df = clean_budget_records(
        [
            {
                "budget_id": "BUDGET-2024-0005",
                "client_name": "Industrias",
                "client_code": "CLI-0288",
                "currency": "EUR",
                "total_amount": 30000,
                "signed_at": "2024-04-10",
            },
            {
                "budget_id": "BUDGET-2024-0005",
                "client_name": "Industrias",
                "client_code": "CLI-0288",
                "currency": "EUR",
                "total_amount": 32000,
                "signed_at": "2024-04-12",
            },
        ]
    )
    # Only the latest row survives.
    assert len(df) == 1
    assert int(df.loc[0, "total_amount"]) == 32000
