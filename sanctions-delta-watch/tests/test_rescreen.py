"""Tests for watcher.rescreen. Stub confirm_fn only, no network calls."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import pytest

from watcher.rescreen import load_ledger, rescreen

NOW = datetime(2026, 9, 18, tzinfo=timezone.utc)

LEDGER_COLUMNS = ["customer_id", "address", "relationship", "direction", "asset", "amount", "tx_date", "tx_hash"]


def _ledger(rows: list[list[Any]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=LEDGER_COLUMNS)


def _added(address: str, chain: str = "Ethereum", block: int = 1, tx_hash: str = "0xevent") -> dict[str, Any]:
    return {"chain": chain, "block": block, "tx_hash": tx_hash, "kind": "added", "address": address}


def _removed(address: str, chain: str = "Ethereum", block: int = 1, tx_hash: str = "0xevent") -> dict[str, Any]:
    return {"chain": chain, "block": block, "tx_hash": tx_hash, "kind": "removed", "address": address}


def _days_ago(days: int) -> str:
    return (NOW - timedelta(days=days)).strftime("%Y-%m-%d")


class StubConfirm:
    """Records calls; returns per-(chain, address) canned answers or raises."""

    def __init__(self, answers: dict[tuple[str, str], Any]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, str]] = []

    def __call__(self, chain: str, address: str) -> bool:
        self.calls.append((chain, address))
        result = self.answers[(chain, address)]
        if isinstance(result, Exception):
            raise result
        return result


# --- matching -----------------------------------------------------------


def test_matching_address_produces_a_hit() -> None:
    ledger = _ledger([["CUST-1", "0xaaaa", "deposit_source", "in", "USDC", 100, _days_ago(1), "0xtx1"]])
    confirm = StubConfirm({("Ethereum", "0xaaaa"): True})
    hits, notices = rescreen([_added("0xaaaa")], ledger, confirm, now=NOW)
    assert len(hits) == 1
    assert notices == []
    hit = hits[0]
    assert hit["customer_id"] == "CUST-1"
    assert hit["chain"] == "Ethereum"
    assert hit["address"] == "0xaaaa"


def test_non_matching_address_produces_nothing() -> None:
    ledger = _ledger([["CUST-1", "0xaaaa", "deposit_source", "in", "USDC", 100, _days_ago(1), "0xtx1"]])
    confirm = StubConfirm({})
    hits, notices = rescreen([_added("0xbbbb")], ledger, confirm, now=NOW)
    assert hits == []
    assert notices == []
    assert confirm.calls == []


def test_mixed_case_addresses_still_match() -> None:
    ledger = _ledger([["CUST-1", "0xAAAA", "deposit_source", "in", "USDC", 100, _days_ago(1), "0xtx1"]])
    confirm = StubConfirm({("Ethereum", "0xaaaa"): True})
    hits, _ = rescreen([_added("0xAaAa")], ledger, confirm, now=NOW)
    assert len(hits) == 1
    assert confirm.calls == [("Ethereum", "0xaaaa")]


def test_customer_with_two_matching_rows_produces_two_hits() -> None:
    ledger = _ledger(
        [
            ["CUST-1", "0xaaaa", "deposit_source", "in", "USDC", 100, _days_ago(1), "0xtx1"],
            ["CUST-1", "0xaaaa", "counterparty", "out", "USDT", 500, _days_ago(2), "0xtx2"],
        ]
    )
    confirm = StubConfirm({("Ethereum", "0xaaaa"): True})
    hits, _ = rescreen([_added("0xaaaa")], ledger, confirm, now=NOW)
    assert len(hits) == 2
    assert confirm.calls == [("Ethereum", "0xaaaa")]  # confirmed once, applied to both rows


def test_confirm_fn_called_once_per_distinct_address() -> None:
    ledger = _ledger(
        [
            ["CUST-1", "0xaaaa", "deposit_source", "in", "USDC", 100, _days_ago(1), "0xtx1"],
            ["CUST-2", "0xaaaa", "counterparty", "out", "USDT", 500, _days_ago(2), "0xtx2"],
            ["CUST-3", "0xbbbb", "deposit_source", "in", "USDC", 50, _days_ago(1), "0xtx3"],
        ]
    )
    confirm = StubConfirm({("Ethereum", "0xaaaa"): True, ("Ethereum", "0xbbbb"): True})
    hits, _ = rescreen([_added("0xaaaa"), _added("0xbbbb")], ledger, confirm, now=NOW)
    assert len(hits) == 3
    assert sorted(confirm.calls) == [("Ethereum", "0xaaaa"), ("Ethereum", "0xbbbb")]


# --- confirmation gate ----------------------------------------------------


def test_unconfirmed_match_is_dropped() -> None:
    ledger = _ledger([["CUST-1", "0xaaaa", "deposit_source", "in", "USDC", 100, _days_ago(1), "0xtx1"]])
    confirm = StubConfirm({("Ethereum", "0xaaaa"): False})
    hits, notices = rescreen([_added("0xaaaa")], ledger, confirm, now=NOW)
    assert hits == []
    assert notices == []


def test_confirm_fn_raising_drops_the_hit_without_aborting(caplog: pytest.LogCaptureFixture) -> None:
    ledger = _ledger(
        [
            ["CUST-1", "0xaaaa", "deposit_source", "in", "USDC", 100, _days_ago(1), "0xtx1"],
            ["CUST-2", "0xbbbb", "deposit_source", "in", "USDC", 50, _days_ago(1), "0xtx2"],
        ]
    )
    confirm = StubConfirm({("Ethereum", "0xaaaa"): RuntimeError("rpc down"), ("Ethereum", "0xbbbb"): True})
    with caplog.at_level("ERROR"):
        hits, notices = rescreen([_added("0xaaaa"), _added("0xbbbb")], ledger, confirm, now=NOW)
    assert len(hits) == 1
    assert hits[0]["customer_id"] == "CUST-2"
    assert any("rpc down" in message for message in caplog.messages)


# --- severity --------------------------------------------------------------


def test_severity_critical_for_withdrawal_whitelist() -> None:
    ledger = _ledger([["CUST-1", "0xaaaa", "withdrawal_whitelist", "out", "ETH", 0, _days_ago(500), "0xtx1"]])
    confirm = StubConfirm({("Ethereum", "0xaaaa"): True})
    hits, _ = rescreen([_added("0xaaaa")], ledger, confirm, now=NOW)
    assert hits[0]["severity"] == "Critical"


def test_severity_critical_for_recent_counterparty_with_amount() -> None:
    ledger = _ledger([["CUST-1", "0xaaaa", "counterparty", "out", "USDT", 500, _days_ago(10), "0xtx1"]])
    confirm = StubConfirm({("Ethereum", "0xaaaa"): True})
    hits, _ = rescreen([_added("0xaaaa")], ledger, confirm, now=NOW)
    assert hits[0]["severity"] == "Critical"


def test_severity_medium_for_counterparty_with_zero_amount() -> None:
    ledger = _ledger([["CUST-1", "0xaaaa", "counterparty", "out", "USDT", 0, _days_ago(10), "0xtx1"]])
    confirm = StubConfirm({("Ethereum", "0xaaaa"): True})
    hits, _ = rescreen([_added("0xaaaa")], ledger, confirm, now=NOW)
    assert hits[0]["severity"] == "Medium"


def test_severity_medium_for_counterparty_outside_90_days() -> None:
    ledger = _ledger([["CUST-1", "0xaaaa", "counterparty", "out", "USDT", 500, _days_ago(91), "0xtx1"]])
    confirm = StubConfirm({("Ethereum", "0xaaaa"): True})
    hits, _ = rescreen([_added("0xaaaa")], ledger, confirm, now=NOW)
    assert hits[0]["severity"] == "Medium"


def test_severity_critical_at_exactly_90_day_boundary() -> None:
    ledger = _ledger([["CUST-1", "0xaaaa", "counterparty", "out", "USDT", 500, _days_ago(90), "0xtx1"]])
    confirm = StubConfirm({("Ethereum", "0xaaaa"): True})
    hits, _ = rescreen([_added("0xaaaa")], ledger, confirm, now=NOW)
    assert hits[0]["severity"] == "Critical"


def test_severity_high_for_deposit_source() -> None:
    ledger = _ledger([["CUST-1", "0xaaaa", "deposit_source", "in", "USDC", 100, _days_ago(1), "0xtx1"]])
    confirm = StubConfirm({("Ethereum", "0xaaaa"): True})
    hits, _ = rescreen([_added("0xaaaa")], ledger, confirm, now=NOW)
    assert hits[0]["severity"] == "High"


def test_severity_medium_for_anything_else() -> None:
    ledger = _ledger([["CUST-1", "0xaaaa", "internal_transfer", "in", "USDC", 100, _days_ago(1), "0xtx1"]])
    confirm = StubConfirm({("Ethereum", "0xaaaa"): True})
    hits, _ = rescreen([_added("0xaaaa")], ledger, confirm, now=NOW)
    assert hits[0]["severity"] == "Medium"


# --- removed / notices -------------------------------------------------------


def test_removed_change_produces_notice_not_hit() -> None:
    ledger = _ledger([["CUST-1", "0xaaaa", "withdrawal_whitelist", "out", "ETH", 0, _days_ago(1), "0xtx1"]])
    confirm = StubConfirm({})
    hits, notices = rescreen([_removed("0xaaaa")], ledger, confirm, now=NOW)
    assert hits == []
    assert len(notices) == 1
    assert notices[0]["chain"] == "Ethereum"
    assert notices[0]["address"] == "0xaaaa"
    assert notices[0]["customer_ids"] == ["CUST-1"]
    assert confirm.calls == []  # no live confirmation needed for delistings


def test_empty_change_list_produces_nothing() -> None:
    ledger = _ledger([["CUST-1", "0xaaaa", "deposit_source", "in", "USDC", 100, _days_ago(1), "0xtx1"]])
    confirm = StubConfirm({})
    hits, notices = rescreen([], ledger, confirm, now=NOW)
    assert hits == []
    assert notices == []
    assert confirm.calls == []


# --- load_ledger -------------------------------------------------------------


def test_load_ledger_lowercases_addresses(tmp_path: Any) -> None:
    path = tmp_path / "ledger.csv"
    path.write_text(
        "customer_id,address,relationship,direction,asset,amount,tx_date,tx_hash\n"
        "CUST-1,0xABCDEF0000000000000000000000000000000000,deposit_source,in,USDC,1,2026-01-01,\n"
    )
    df = load_ledger(str(path))
    assert df.loc[0, "address"] == "0xabcdef0000000000000000000000000000000000"


def test_load_ledger_missing_column_raises_clear_error(tmp_path: Any) -> None:
    path = tmp_path / "ledger.csv"
    path.write_text("customer_id,address\nCUST-1,0xaaaa\n")
    with pytest.raises(ValueError, match="relationship"):
        load_ledger(str(path))
