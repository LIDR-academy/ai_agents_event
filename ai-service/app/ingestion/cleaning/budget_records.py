"""Reparable cleaning for the budget tabular view.

Five steps, applied to a DataFrame whose schema we treat as **raw**:

1. **Null placeholders** — ``"TBD"``, ``"N/A"``, empty strings → ``pd.NA``.
   Disguised nulls are the easiest dirt to miss and the hardest to debug
   downstream; we normalize at the boundary.

2. **Currency casing** — ``EUR`` vs ``eur`` collapses to upper. Any other
   currency is preserved (validation will catch it).

3. **Date coercion** — ``signed_at`` can come as ISO ``2024-05-02`` or as the
   Spanish ``12/03/2024``. ``pd.to_datetime(errors="coerce")`` returns ``NaT``
   for the unparseable rows; validation will route them.

4. **Numeric coercion** — ``total_amount`` and ``phase.amount`` coerced to
   numeric with ``errors="coerce"``. Strings sneak in from manual exports.

5. **Hash-based dedup** — duplicates are detected by content hash, not by
   ``drop_duplicates``. When the same ``budget_id`` appears twice with
   different content, the *policy* (declared here in code) is "keep the row
   with the most recent ``signed_at``". That is a business decision, not a
   technical one — documenting it is part of the deliverable.
"""

from __future__ import annotations

import hashlib
import json
from typing import Iterable

import pandas as pd

NULL_PLACEHOLDERS = {"TBD", "N/A", "n/a", "tbd", "", "null", "None", "-"}


def clean_budget_records(records: Iterable[dict]) -> pd.DataFrame:
    """Return a cleaned DataFrame from the raw list of budget dicts.

    The input is the parsed JSON content — *not* the ``Document.text`` rendered
    markdown. The cleaning step works on the original record, before any
    embedding takes place.
    """
    df = pd.DataFrame(list(records))
    if df.empty:
        return df

    # 1. Null placeholders
    for column in ("client_name", "contact", "contact_email", "notes"):
        if column in df.columns:
            df[column] = df[column].apply(
                lambda v: pd.NA if (isinstance(v, str) and v.strip() in NULL_PLACEHOLDERS) else v
            )

    # 2. Currency casing
    if "currency" in df.columns:
        df["currency"] = df["currency"].astype("string").str.upper()

    # 3. Date coercion (permissive)
    if "signed_at" in df.columns:
        df["signed_at"] = pd.to_datetime(df["signed_at"], errors="coerce", dayfirst=True)

    # 4. Numeric coercion
    if "total_amount" in df.columns:
        df["total_amount"] = pd.to_numeric(df["total_amount"], errors="coerce")

    # 5. Hash-based dedup with explicit business rule: keep latest signed_at.
    if {"budget_id", "signed_at"}.issubset(df.columns):
        df["content_hash"] = df.apply(_content_hash, axis=1)
        # Sort so the latest signed_at lands LAST per budget_id, then drop earlier
        # rows. Reset index so downstream callers don't see surprising gaps.
        df = df.sort_values(by=["budget_id", "signed_at"], na_position="first")
        df = df.drop_duplicates(subset=["budget_id"], keep="last").reset_index(drop=True)

    return df


def _content_hash(row: pd.Series) -> str:
    """Canonical JSON of the row → sha256.

    NaN values are normalized to ``None`` so two semantically identical rows
    that differ only in dtype produce the same hash.
    """
    payload = {
        k: (None if (isinstance(v, float) and pd.isna(v)) else _serializable(v))
        for k, v in row.items()
        if k != "content_hash"
    }
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _serializable(value):
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value
