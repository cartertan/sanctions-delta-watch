"""Tests for watcher.cases. gh is always invoked through an injected runner; no subprocess ever runs."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from watcher import cases

NOW = datetime(2026, 9, 18, 7, 30, 54, tzinfo=timezone.utc)


def _hit(**overrides: Any) -> dict[str, Any]:
    base = {
        "chain": "Ethereum",
        "address": "0xcb74874f1e06fcf80a306e06e5379a44b488ba2d",
        "customer_id": "CUST-2011",
        "relationship": "counterparty",
        "direction": "out",
        "asset": "USDT",
        "amount": 18400,
        "tx_date": "2026-08-22",
        "tx_hash": "0xSYNTHETIC0101",
        "event_block": 24687198,
        "event_tx": "0x5446ac44bc6aa4558911d71b95627a7dedf97d7ebc248bdd9644a617e15cd72f",
        "detected_at": "2026-09-18T07:30:00+00:00",
        "confirmed_at": "2026-09-18T07:30:01+00:00",
        "severity": "Critical",
    }
    base.update(overrides)
    return base


def _notice(**overrides: Any) -> dict[str, Any]:
    base = {
        "chain": "Ethereum",
        "address": "0xaaaa",
        "event_block": 1,
        "event_tx": "0xevent",
        "customer_ids": ["CUST-1"],
        "detected_at": "2026-09-18T07:30:00+00:00",
    }
    base.update(overrides)
    return base


# --- write_delta -------------------------------------------------------------


def test_write_delta_writes_a_file_under_deltas_dir(tmp_path: Path) -> None:
    deltas_dir = tmp_path / "deltas"
    path = cases.write_delta([_hit()], [_notice()], deltas_dir=deltas_dir, now=NOW)
    assert path.parent == deltas_dir
    assert path.exists()
    content = json.loads(path.read_text())
    assert content["hits"] == [_hit()]
    assert content["notices"] == [_notice()]
    assert content["timestamp"]


def test_write_delta_filename_is_a_utc_timestamp(tmp_path: Path) -> None:
    deltas_dir = tmp_path / "deltas"
    path = cases.write_delta([], [], deltas_dir=deltas_dir, now=NOW)
    assert "20260918" in path.name
    assert path.suffix == ".json"


# --- merge_cases ---------------------------------------------------------------


def test_merge_cases_creates_a_new_case(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text("[]")
    new_cases = cases.merge_cases([_hit()], cases_path=cases_path, now=NOW)
    assert len(new_cases) == 1
    stored = json.loads(cases_path.read_text())
    assert len(stored) == 1
    case = stored[0]
    assert case["chain"] == "Ethereum"
    assert case["address"] == "0xcb74874f1e06fcf80a306e06e5379a44b488ba2d"
    assert case["customer_id"] == "CUST-2011"
    assert case["relationship"] == "counterparty"
    assert case["severity"] == "Critical"
    assert case["status"] == "open"
    assert case["issue_number"] is None
    assert case["opened"] == NOW.isoformat()


def test_merge_cases_dedupes_on_chain_address_customer(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text("[]")
    cases.merge_cases([_hit()], cases_path=cases_path, now=NOW)
    later = NOW.replace(hour=8)
    new_cases = cases.merge_cases([_hit(severity="Medium")], cases_path=cases_path, now=later)
    assert new_cases == []  # already known, not new
    stored = json.loads(cases_path.read_text())
    assert len(stored) == 1
    assert stored[0]["opened"] == NOW.isoformat()  # unchanged
    assert stored[0]["severity"] == "Critical"  # original kept, not overwritten


def test_merge_cases_keeps_issue_number_across_runs(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text("[]")
    cases.merge_cases([_hit()], cases_path=cases_path, now=NOW)
    cases.record_issue_number("Ethereum", _hit()["address"], "CUST-2011", 42, cases_path=cases_path)
    cases.merge_cases([_hit()], cases_path=cases_path, now=NOW.replace(hour=9))
    stored = json.loads(cases_path.read_text())
    assert stored[0]["issue_number"] == 42


def test_merge_cases_different_customer_same_address_is_a_separate_case(tmp_path: Path) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text("[]")
    cases.merge_cases([_hit(customer_id="CUST-A")], cases_path=cases_path, now=NOW)
    new_cases = cases.merge_cases([_hit(customer_id="CUST-B")], cases_path=cases_path, now=NOW)
    assert len(new_cases) == 1
    stored = json.loads(cases_path.read_text())
    assert len(stored) == 2


# --- open_issue ---------------------------------------------------------------


class FakeGh:
    def __init__(self, existing_labels: list[str], issue_url: str) -> None:
        self.existing_labels = list(existing_labels)
        self.issue_url = issue_url
        self.calls: list[list[str]] = []
        self.created_labels: list[str] = []

    def __call__(self, args: list[str], **kwargs: Any) -> SimpleNamespace:
        self.calls.append(args)
        if args[:3] == ["gh", "label", "list"]:
            return SimpleNamespace(stdout=json.dumps([{"name": n} for n in self.existing_labels]), returncode=0)
        if args[:3] == ["gh", "label", "create"]:
            self.created_labels.append(args[3])
            self.existing_labels.append(args[3])
            return SimpleNamespace(stdout="", returncode=0)
        if args[:3] == ["gh", "issue", "create"]:
            return SimpleNamespace(stdout=self.issue_url + "\n", returncode=0)
        raise AssertionError(f"unexpected gh call: {args}")


def test_open_issue_returns_the_issue_number() -> None:
    gh = FakeGh(existing_labels=["sanctions-hit", "severity:Critical"], issue_url="https://github.com/acme/repo/issues/7")
    number = cases.open_issue(_hit(), runner=gh)
    assert number == 7


def test_open_issue_title_format() -> None:
    gh = FakeGh(existing_labels=["sanctions-hit", "severity:Critical"], issue_url="https://github.com/acme/repo/issues/1")
    cases.open_issue(_hit(), runner=gh)
    create_call = next(c for c in gh.calls if c[:3] == ["gh", "issue", "create"])
    title = create_call[create_call.index("--title") + 1]
    assert title == "[Sanctions hit] CUST-2011 0xcb74874f on Ethereum"


def test_open_issue_body_renders_exposure_table_and_links() -> None:
    gh = FakeGh(existing_labels=["sanctions-hit", "severity:Critical"], issue_url="https://github.com/acme/repo/issues/1")
    cases.open_issue(_hit(), runner=gh)
    create_call = next(c for c in gh.calls if c[:3] == ["gh", "issue", "create"])
    body = create_call[create_call.index("--body") + 1]
    assert "CUST-2011" in body
    assert "counterparty" in body
    assert "18400" in body
    assert "24687198" in body
    assert "0x5446ac44bc6aa4558911d71b95627a7dedf97d7ebc248bdd9644a617e15cd72f" in body
    assert "2026-09-18T07:30:01+00:00" in body
    assert "etherscan.io/tx/0x5446ac44" in body
    assert "etherscan.io/address/0xcb74874f1e06fcf80a306e06e5379a44b488ba2d" in body
    assert "synthetic" in body.lower()
    assert "|" in body  # markdown table


def test_open_issue_applies_both_labels() -> None:
    gh = FakeGh(existing_labels=["sanctions-hit", "severity:Critical"], issue_url="https://github.com/acme/repo/issues/1")
    cases.open_issue(_hit(), runner=gh)
    create_call = next(c for c in gh.calls if c[:3] == ["gh", "issue", "create"])
    labels = [create_call[i + 1] for i, tok in enumerate(create_call) if tok == "--label"]
    assert set(labels) == {"sanctions-hit", "severity:Critical"}


def test_open_issue_creates_missing_labels() -> None:
    gh = FakeGh(existing_labels=[], issue_url="https://github.com/acme/repo/issues/1")
    cases.open_issue(_hit(), runner=gh)
    assert set(gh.created_labels) == {"sanctions-hit", "severity:Critical"}


def test_open_issue_does_not_recreate_existing_labels() -> None:
    gh = FakeGh(existing_labels=["sanctions-hit", "severity:Critical"], issue_url="https://github.com/acme/repo/issues/1")
    cases.open_issue(_hit(), runner=gh)
    assert gh.created_labels == []


# --- case_key / no-duplicate-issues integration -------------------------------


def test_case_key_is_chain_address_customer() -> None:
    key = cases.case_key(_hit())
    assert key == ("Ethereum", "0xcb74874f1e06fcf80a306e06e5379a44b488ba2d", "CUST-2011")
