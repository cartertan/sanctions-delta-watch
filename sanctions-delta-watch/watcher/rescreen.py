"""Re-screen the known ledger against oracle designation changes.

No case ever opens on the strength of a historical event alone: every
'added' match is confirmed with a live isSanctioned call before it becomes
a hit. A match that fails confirmation -- or whose confirmation call raises
-- is logged and dropped, never surfaced as a case.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pandas as pd

logger = logging.getLogger(__name__)

REQUIRED_LEDGER_COLUMNS = [
    "customer_id",
    "address",
    "relationship",
    "direction",
    "asset",
    "amount",
    "tx_date",
    "tx_hash",
]

ConfirmFn = Callable[[str, str], bool]


def load_ledger(path: str) -> pd.DataFrame:
    """Load the customer ledger, with addresses lowercased for comparison."""
    df = pd.read_csv(path)
    missing = [c for c in REQUIRED_LEDGER_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"ledger {path} is missing required column(s): {', '.join(missing)}")
    df = df.copy()
    df["address"] = df["address"].astype(str).str.lower()
    df["tx_hash"] = df["tx_hash"].fillna("")
    return df


def _now_iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat()


def _within_90_days(tx_date: Any, now: datetime) -> bool:
    try:
        parsed = datetime.strptime(str(tx_date), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        logger.warning("unparseable tx_date %r; treating as outside the 90-day window", tx_date)
        return False
    age = now - parsed
    return timedelta(0) <= age <= timedelta(days=90)


def _severity(relationship: str, amount: Any, tx_date: Any, now: datetime) -> str:
    if relationship == "withdrawal_whitelist":
        return "Critical"
    if relationship == "counterparty":
        try:
            amount_ok = float(amount) > 0
        except (TypeError, ValueError):
            amount_ok = False
        if amount_ok and _within_90_days(tx_date, now):
            return "Critical"
        return "Medium"
    if relationship == "deposit_source":
        return "High"
    return "Medium"


def rescreen(
    changes: list[dict[str, Any]],
    ledger_df: pd.DataFrame,
    confirm_fn: ConfirmFn,
    *,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Re-screen the ledger against a batch of designation changes.

    Returns (hits, notices): hits from live-confirmed 'added' changes with a
    ledger match, notices from 'removed' changes (no confirmation needed --
    delisting is informational, not a compliance case).
    """
    now = now if now is not None else datetime.now(timezone.utc)
    detected_at = _now_iso(now)

    hits: list[dict[str, Any]] = []
    notices: list[dict[str, Any]] = []
    confirmation_cache: dict[tuple[str, str], tuple[bool, str]] = {}
    ledger_addresses = ledger_df["address"].astype(str).str.lower()

    for change in changes:
        chain = change["chain"]
        address = str(change["address"]).lower()
        kind = change["kind"]
        matches = ledger_df[ledger_addresses == address]

        if kind == "removed":
            notices.append(
                {
                    "chain": chain,
                    "address": address,
                    "event_block": change.get("block"),
                    "event_tx": change.get("tx_hash"),
                    "customer_ids": matches["customer_id"].tolist(),
                    "detected_at": detected_at,
                }
            )
            continue

        if kind != "added":
            logger.warning("unknown change kind %r for %s on %s; skipping", kind, address, chain)
            continue

        if matches.empty:
            continue

        cache_key = (chain, address)
        if cache_key not in confirmation_cache:
            confirmed_at = _now_iso(now)
            try:
                confirmed = bool(confirm_fn(chain, address))
            except Exception as exc:
                logger.error("confirmation failed for %s on %s: %s", address, chain, exc)
                confirmed = False
            confirmation_cache[cache_key] = (confirmed, confirmed_at)

        confirmed, confirmed_at = confirmation_cache[cache_key]
        if not confirmed:
            logger.info("dropping unconfirmed match for %s on %s", address, chain)
            continue

        for _, row in matches.iterrows():
            hits.append(
                {
                    "chain": chain,
                    "address": address,
                    "customer_id": row["customer_id"],
                    "relationship": row["relationship"],
                    "direction": row["direction"],
                    "asset": row["asset"],
                    "amount": row["amount"],
                    "tx_date": row["tx_date"],
                    "tx_hash": row["tx_hash"],
                    "event_block": change.get("block"),
                    "event_tx": change.get("tx_hash"),
                    "detected_at": detected_at,
                    "confirmed_at": confirmed_at,
                    "severity": _severity(row["relationship"], row["amount"], row["tx_date"], now),
                }
            )

    return hits, notices
