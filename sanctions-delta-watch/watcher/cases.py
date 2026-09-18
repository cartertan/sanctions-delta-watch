"""Persist rescreen deltas, merge confirmed hits into cases, and open GitHub
Issues for them through the gh CLI.

Every subprocess call goes through an injected `runner` so tests never shell
out. A case already recorded in docs/cases.json never gets a second issue --
that dedupe is what keeps the watcher idempotent across scheduled runs.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

DELTAS_DIR = Path("data/deltas")
CASES_PATH = Path("docs/cases.json")

EXPLORERS: dict[str, str] = {
    "Ethereum": "https://etherscan.io",
    "Polygon": "https://polygonscan.com",
    "BNB": "https://bscscan.com",
    "Avalanche": "https://snowtrace.io",
    "Optimism": "https://optimistic.etherscan.io",
    "Arbitrum": "https://arbiscan.io",
    "Celo": "https://celoscan.io",
    "Base": "https://basescan.org",
}

_LABEL_COLORS: dict[str, str] = {
    "sanctions-hit": "5319e7",
    "severity:Critical": "b60205",
    "severity:High": "d93f0b",
    "severity:Medium": "fbca04",
}

Runner = Callable[..., Any]


def _now_iso(now: datetime | None) -> str:
    return (now if now is not None else datetime.now(timezone.utc)).isoformat()


def case_key(hit_or_case: dict[str, Any]) -> tuple[str, str, str]:
    return (hit_or_case["chain"], hit_or_case["address"], hit_or_case["customer_id"])


# --- delta files ---------------------------------------------------------------


def write_delta(
    hits: list[dict[str, Any]],
    notices: list[dict[str, Any]],
    *,
    deltas_dir: Path = DELTAS_DIR,
    now: datetime | None = None,
) -> Path:
    """Save one run's hits and notices to data/deltas/<UTC timestamp>.json."""
    now = now if now is not None else datetime.now(timezone.utc)
    deltas_dir.mkdir(parents=True, exist_ok=True)
    filename = now.strftime("%Y%m%dT%H%M%S%fZ") + ".json"
    path = deltas_dir / filename
    payload = {"timestamp": now.isoformat(), "hits": hits, "notices": notices}
    path.write_text(json.dumps(payload, indent=2, default=str))
    logger.info("wrote delta %s (%d hits, %d notices)", path, len(hits), len(notices))
    return path


# --- cases.json ------------------------------------------------------------------


def _load_cases(cases_path: Path) -> list[dict[str, Any]]:
    try:
        raw = cases_path.read_text()
    except FileNotFoundError:
        logger.info("no cases file at %s; starting fresh", cases_path)
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("cases file %s is corrupt (%s); rebuilding", cases_path, exc)
        return []
    if not isinstance(data, list):
        logger.error("cases file %s has an unexpected shape; rebuilding", cases_path)
        return []
    return data


def _save_cases(cases_list: list[dict[str, Any]], cases_path: Path) -> None:
    cases_path.parent.mkdir(parents=True, exist_ok=True)
    cases_path.write_text(json.dumps(cases_list, indent=2, default=str))


def merge_cases(
    hits: list[dict[str, Any]],
    *,
    cases_path: Path = CASES_PATH,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Merge confirmed hits into docs/cases.json, deduped on chain+address+customer_id.

    A case already on file keeps its original `opened` timestamp, `issue_number`
    and `severity` untouched. Returns only the newly created cases.
    """
    existing = _load_cases(cases_path)
    known_keys = {case_key(case) for case in existing}

    new_cases: list[dict[str, Any]] = []
    for hit in hits:
        key = case_key(hit)
        if key in known_keys:
            continue
        known_keys.add(key)
        case = {
            "opened": _now_iso(now),
            "chain": hit["chain"],
            "address": hit["address"],
            "customer_id": hit["customer_id"],
            "relationship": hit["relationship"],
            "severity": hit["severity"],
            "status": "open",
            "issue_number": None,
        }
        existing.append(case)
        new_cases.append(case)

    _save_cases(existing, cases_path)
    return new_cases


def record_issue_number(
    chain: str,
    address: str,
    customer_id: str,
    issue_number: int,
    *,
    cases_path: Path = CASES_PATH,
) -> None:
    """Persist the GitHub issue number opened for an existing case."""
    existing = _load_cases(cases_path)
    key = (chain, address, customer_id)
    for case in existing:
        if case_key(case) == key:
            case["issue_number"] = issue_number
            break
    else:
        logger.error("no case found for %s to attach issue #%d", key, issue_number)
        return
    _save_cases(existing, cases_path)


# --- GitHub issues -----------------------------------------------------------------


def _list_labels(runner: Runner) -> set[str]:
    result = runner(["gh", "label", "list", "--json", "name", "--limit", "200"], capture_output=True, text=True, check=True)
    return {item["name"] for item in json.loads(result.stdout)}


def _ensure_labels(labels: list[str], runner: Runner) -> None:
    existing = _list_labels(runner)
    for label in labels:
        if label in existing:
            continue
        color = _LABEL_COLORS.get(label, "ededed")
        runner(["gh", "label", "create", label, "--color", color, "--force"], capture_output=True, text=True, check=True)
        logger.info("created missing label %s", label)


def _render_issue_body(hit: dict[str, Any]) -> str:
    explorer = EXPLORERS.get(hit["chain"], "")
    address_link = f"{explorer}/address/{hit['address']}" if explorer else hit["address"]
    tx_link = f"{explorer}/tx/{hit['event_tx']}" if explorer else hit["event_tx"]

    table_rows = [
        ("Customer", hit["customer_id"]),
        ("Chain", hit["chain"]),
        ("Address", hit["address"]),
        ("Relationship", hit["relationship"]),
        ("Direction", hit["direction"]),
        ("Asset", hit["asset"]),
        ("Amount", hit["amount"]),
        ("Tx date", hit["tx_date"]),
        ("Tx hash", hit["tx_hash"]),
        ("Severity", hit["severity"]),
    ]
    table = "\n".join(f"| {label} | {value} |" for label, value in table_rows)

    return (
        "| Field | Value |\n"
        "|---|---|\n"
        f"{table}\n\n"
        f"Designation event: block {hit['event_block']}, tx {hit['event_tx']}\n\n"
        f"Live oracle confirmation: {hit['confirmed_at']}\n\n"
        f"[Address on block explorer]({address_link}) · [Event tx on block explorer]({tx_link})\n\n"
        "_Customer data is synthetic; oracle data is live._"
    )


def _parse_issue_number(output: str) -> int:
    match = re.search(r"/issues/(\d+)", output.strip())
    if not match:
        raise RuntimeError(f"could not parse issue number from gh output: {output!r}")
    return int(match.group(1))


def open_issue(hit: dict[str, Any], *, runner: Runner = subprocess.run) -> int:
    """Create a GitHub Issue for a confirmed hit and return its issue number."""
    labels = ["sanctions-hit", f"severity:{hit['severity']}"]
    _ensure_labels(labels, runner)

    title = f"[Sanctions hit] {hit['customer_id']} {hit['address'][:10]} on {hit['chain']}"
    body = _render_issue_body(hit)

    args = ["gh", "issue", "create", "--title", title, "--body", body]
    for label in labels:
        args += ["--label", label]

    result = runner(args, capture_output=True, text=True, check=True)
    issue_number = _parse_issue_number(result.stdout)
    logger.info("opened issue #%d for %s on %s", issue_number, hit["customer_id"], hit["chain"])
    return issue_number
