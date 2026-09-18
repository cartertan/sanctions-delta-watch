"""Tests for watcher.run. Event fetching, head-block lookup, confirmation, and
gh (via issue_opener) are all injected -- no network calls."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from watcher import cases, events, run

ADDRESS = "0xcb74874f1e06fcf80a306e06e5379a44b488ba2d"


def _write_ledger(path: Path, rows: list[str]) -> None:
    header = "customer_id,address,relationship,direction,asset,amount,tx_date,tx_hash\n"
    path.write_text(header + "\n".join(rows) + "\n")


@pytest.fixture()
def paths(tmp_path: Path) -> dict[str, Path]:
    ledger_path = tmp_path / "ledger.csv"
    _write_ledger(
        ledger_path,
        [f"CUST-1,{ADDRESS},withdrawal_whitelist,out,USDT,0,2026-06-14,"],
    )
    return {
        "ledger_path": ledger_path,
        "state_path": tmp_path / "state.json",
        "cases_path": tmp_path / "cases.json",
        "deltas_dir": tmp_path / "deltas",
    }


class Recorder:
    def __init__(self, retval: Any) -> None:
        self.retval = retval
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any) -> Any:
        self.calls.append(args)
        if isinstance(self.retval, Exception):
            raise self.retval
        return self.retval


def _run(argv: list[str], paths: dict[str, Path], **overrides: Any) -> int:
    kwargs = dict(
        ledger_path=str(paths["ledger_path"]),
        state_path=paths["state_path"],
        cases_path=paths["cases_path"],
        deltas_dir=paths["deltas_dir"],
    )
    kwargs.update(overrides)
    return run.main(argv, **kwargs)


# --- CLI parsing -------------------------------------------------------------


def test_parse_args_defaults() -> None:
    args = run.parse_args([])
    assert args.chains == ["Ethereum"]
    assert args.lookback == 50000
    assert args.simulate is None
    assert args.open_issues is False
    assert args.dry_run is False


def test_parse_args_all_flags() -> None:
    args = run.parse_args(
        ["--chains", "Ethereum", "Polygon", "--lookback", "1000", "--simulate", "0xabc",
         "--open-issues", "--dry-run"]
    )
    assert args.chains == ["Ethereum", "Polygon"]
    assert args.lookback == 1000
    assert args.simulate == "0xabc"
    assert args.open_issues is True
    assert args.dry_run is True


def test_resolve_chains_all_expands_to_every_chain() -> None:
    assert set(run.resolve_chains(["all"])) == set(run.CHAINS)


def test_resolve_chains_passthrough() -> None:
    assert run.resolve_chains(["Ethereum", "Polygon"]) == ["Ethereum", "Polygon"]


# --- dry-run -------------------------------------------------------------------


def test_dry_run_writes_nothing(paths: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    change = {"chain": "Ethereum", "block": 1, "tx_hash": "0xevent", "kind": "added", "address": ADDRESS}
    fetch = Recorder([change])
    head = Recorder(100)
    confirm = Recorder(True)
    issue_opener = Recorder(999)

    code = _run(
        ["--dry-run"],
        paths,
        fetch_events=fetch,
        head_block_fn=head,
        confirm_fn=confirm,
        issue_opener=issue_opener,
    )

    assert code == 0
    assert not paths["deltas_dir"].exists() or list(paths["deltas_dir"].iterdir()) == []
    assert not paths["state_path"].exists()
    assert not paths["cases_path"].exists()
    assert issue_opener.calls == []
    out = capsys.readouterr().out
    assert "dry" in out.lower()


# --- normal run ----------------------------------------------------------------


def test_run_writes_delta_and_merges_case(paths: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    change = {"chain": "Ethereum", "block": 1, "tx_hash": "0xevent", "kind": "added", "address": ADDRESS}
    fetch = Recorder([change])
    head = Recorder(100)
    confirm = Recorder(True)
    issue_opener = Recorder(999)

    code = _run(
        [],
        paths,
        fetch_events=fetch,
        head_block_fn=head,
        confirm_fn=confirm,
        issue_opener=issue_opener,
    )

    assert code == 0
    assert list(paths["deltas_dir"].iterdir())
    assert paths["cases_path"].exists()
    stored_cases = cases._load_cases(paths["cases_path"])
    assert len(stored_cases) == 1
    assert stored_cases[0]["customer_id"] == "CUST-1"
    assert issue_opener.calls == []  # --open-issues not passed
    state = events.load_state(paths["state_path"])
    assert state["chains"]["Ethereum"]["last_block"] == 100
    out = capsys.readouterr().out
    assert "changes=1" in out
    assert "hits=1" in out
    assert "new_cases=1" in out


def test_open_issues_opens_once_and_records_issue_number(paths: dict[str, Path]) -> None:
    change = {"chain": "Ethereum", "block": 1, "tx_hash": "0xevent", "kind": "added", "address": ADDRESS}
    fetch = Recorder([change])
    head = Recorder(100)
    confirm = Recorder(True)
    issue_opener = Recorder(42)

    _run(
        ["--open-issues"],
        paths,
        fetch_events=fetch,
        head_block_fn=head,
        confirm_fn=confirm,
        issue_opener=issue_opener,
    )
    assert len(issue_opener.calls) == 1
    stored_cases = cases._load_cases(paths["cases_path"])
    assert stored_cases[0]["issue_number"] == 42

    # second run, same change -> case already known, no second issue
    fetch2 = Recorder([change])
    head2 = Recorder(100)
    confirm2 = Recorder(True)
    issue_opener2 = Recorder(999)
    _run(
        ["--open-issues"],
        paths,
        fetch_events=fetch2,
        head_block_fn=head2,
        confirm_fn=confirm2,
        issue_opener=issue_opener2,
    )
    assert issue_opener2.calls == []
    stored_cases = cases._load_cases(paths["cases_path"])
    assert len(stored_cases) == 1
    assert stored_cases[0]["issue_number"] == 42


# --- simulate --------------------------------------------------------------------


def test_simulate_injects_change_and_skips_fetch_but_still_confirms(paths: dict[str, Path]) -> None:
    fetch = Recorder([])  # must not be called for Ethereum
    head = Recorder(100)
    confirm = Recorder(True)
    issue_opener = Recorder(1)

    code = _run(
        ["--simulate", ADDRESS],
        paths,
        fetch_events=fetch,
        head_block_fn=head,
        confirm_fn=confirm,
        issue_opener=issue_opener,
    )

    assert code == 0
    assert fetch.calls == []
    assert confirm.calls == [("Ethereum", ADDRESS)]
    stored_cases = cases._load_cases(paths["cases_path"])
    assert len(stored_cases) == 1


# --- chain failures --------------------------------------------------------------


def test_one_chain_failing_does_not_abort_others(paths: dict[str, Path], capsys: pytest.CaptureFixture[str]) -> None:
    change = {"chain": "Ethereum", "block": 1, "tx_hash": "0xevent", "kind": "added", "address": ADDRESS}

    def fetch(chain: str, from_block: int, to_block: int) -> list[dict[str, Any]]:
        if chain == "Polygon":
            raise RuntimeError("polygon endpoint down")
        return [change]

    def head(chain: str) -> int:
        return 100

    confirm = Recorder(True)
    issue_opener = Recorder(1)

    code = _run(
        ["--chains", "Ethereum", "Polygon"],
        paths,
        fetch_events=fetch,
        head_block_fn=head,
        confirm_fn=confirm,
        issue_opener=issue_opener,
    )

    assert code == 0
    stored_cases = cases._load_cases(paths["cases_path"])
    assert len(stored_cases) == 1
    state = events.load_state(paths["state_path"])
    assert "Ethereum" in state["chains"]
    assert "Polygon" not in state["chains"]


def test_all_chains_failing_returns_exit_code_1(paths: dict[str, Path]) -> None:
    def fetch(chain: str, from_block: int, to_block: int) -> list[dict[str, Any]]:
        raise RuntimeError("boom")

    def head(chain: str) -> int:
        return 100

    confirm = Recorder(True)
    issue_opener = Recorder(1)

    code = _run(
        [],
        paths,
        fetch_events=fetch,
        head_block_fn=head,
        confirm_fn=confirm,
        issue_opener=issue_opener,
    )
    assert code == 1
