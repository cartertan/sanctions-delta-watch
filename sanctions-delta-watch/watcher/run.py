"""CLI entry point: fetch designation changes, re-screen the ledger, and
optionally open cases as GitHub Issues.

A chain that fails is logged and skipped -- the run only exits non-zero when
every requested chain failed. --simulate skips log scanning for Ethereum
entirely (so the demo doesn't depend on network log access) but the injected
change still goes through a real, live isSanctioned confirmation.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Callable

from watcher.cases import CASES_PATH, DELTAS_DIR, case_key, merge_cases, open_issue, record_issue_number, write_delta
from watcher.events import STATE_PATH, fetch_designation_events, load_state, resolve_start_block, save_state
from watcher.rescreen import load_ledger, rescreen
from watcher.rpc import CHAINS, call, is_sanctioned

logger = logging.getLogger(__name__)

FetchFn = Callable[[str, int, int], list[dict[str, Any]]]
HeadBlockFn = Callable[[str], int]
ConfirmFn = Callable[[str, str], bool]
IssueOpenerFn = Callable[[dict[str, Any]], int]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sanctions delta watcher")
    parser.add_argument("--chains", nargs="+", default=["Ethereum"], help="chain names, or 'all'")
    parser.add_argument("--lookback", type=int, default=50000)
    parser.add_argument("--simulate", metavar="ADDRESS", default=None, help="inject a synthetic designation on Ethereum")
    parser.add_argument("--open-issues", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def resolve_chains(chains: list[str]) -> list[str]:
    if chains == ["all"]:
        return list(CHAINS)
    return chains


def _default_head_block(chain: str) -> int:
    return int(call(CHAINS[chain]["rpc"], "eth_blockNumber", []), 16)


def main(
    argv: list[str] | None = None,
    *,
    fetch_events: FetchFn = fetch_designation_events,
    confirm_fn: ConfirmFn = is_sanctioned,
    head_block_fn: HeadBlockFn | None = None,
    issue_opener: IssueOpenerFn = open_issue,
    ledger_path: str = "data/ledger.csv",
    state_path: Path = STATE_PATH,
    cases_path: Path = CASES_PATH,
    deltas_dir: Path = DELTAS_DIR,
) -> int:
    args = parse_args(argv)
    head_block_fn = head_block_fn if head_block_fn is not None else _default_head_block
    chains = resolve_chains(args.chains)

    state = load_state(state_path)
    ledger_df = load_ledger(ledger_path)

    all_changes: list[dict[str, Any]] = []
    scanned_chains: dict[str, int] = {}
    failed_chains: list[str] = []

    for chain in chains:
        try:
            if chain not in CHAINS:
                raise ValueError(f"unknown chain: {chain}")

            if args.simulate and chain == "Ethereum":
                logger.info("simulate: injecting synthetic designation for %s on Ethereum", args.simulate)
                changes = [
                    {
                        "chain": "Ethereum",
                        "block": 0,
                        "tx_hash": "SIMULATED",
                        "kind": "added",
                        "address": args.simulate.lower(),
                    }
                ]
            else:
                head = head_block_fn(chain)
                from_block = resolve_start_block(chain, head, args.lookback, state)
                changes = fetch_events(chain, from_block, head)
                scanned_chains[chain] = head

            all_changes.extend(changes)
        except Exception as exc:
            logger.error("chain %s failed: %s", chain, exc)
            failed_chains.append(chain)

    if failed_chains and len(failed_chains) == len(chains):
        logger.error("every chain failed: %s", failed_chains)
        return 1

    hits, notices = rescreen(all_changes, ledger_df, confirm_fn)

    if args.dry_run:
        print(
            f"[dry-run] changes={len(all_changes)} hits={len(hits)} "
            f"notices={len(notices)}. Nothing written."
        )
        return 0

    for chain, head in scanned_chains.items():
        state.setdefault("chains", {})[chain] = {"last_block": head}
    save_state(state, state_path)

    write_delta(hits, notices, deltas_dir=deltas_dir)
    new_cases = merge_cases(hits, cases_path=cases_path)

    issues_opened = 0
    if args.open_issues:
        hits_by_key = {case_key(hit): hit for hit in hits}
        for case in new_cases:
            hit = hits_by_key.get(case_key(case))
            if hit is None:
                continue
            try:
                issue_number = issue_opener(hit)
            except Exception as exc:
                logger.error("failed to open issue for %s: %s", case_key(case), exc)
                continue
            record_issue_number(case["chain"], case["address"], case["customer_id"], issue_number, cases_path=cases_path)
            issues_opened += 1

    print(
        f"changes={len(all_changes)} hits={len(hits)} new_cases={len(new_cases)} "
        f"issues_opened={issues_opened}"
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
