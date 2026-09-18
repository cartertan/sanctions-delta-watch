"""Design A: read SanctionedAddressesAdded/Removed events from the oracle.

Primary source is the Etherscan v2 logs API (Stage 0 proved this is the only
path that works at scale on free tiers). The raw eth_getLogs fallback exists
for when no Etherscan key is configured, and must never paper over a blocked
scan as "zero events" -- that distinction matters more than anything else
here.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from scripts.step0_verify_events import TOPIC_ADDED, TOPIC_REMOVED, decode_address_array
from watcher.rpc import CHAINS, RpcBlocked, call

logger = logging.getLogger(__name__)

__all__ = [
    "TOPIC_ADDED",
    "TOPIC_REMOVED",
    "decode_address_array",
    "fetch_designation_events",
    "load_state",
    "save_state",
    "resolve_start_block",
]

ETHERSCAN_URL = "https://api.etherscan.io/v2/api"
# Chains Etherscan's unified v2 API serves logs for. Celo is left out: its
# explorer isn't part of that unified endpoint, so we fall back to RPC for it.
ETHERSCAN_CHAIN_IDS: dict[str, int] = {
    "Ethereum": 1,
    "Polygon": 137,
    "BNB": 56,
    "Avalanche": 43114,
    "Optimism": 10,
    "Arbitrum": 42161,
    "Base": 8453,
}

CHUNK_SIZE = 2000
MAX_CONSECUTIVE_FAILURES = 5
PAGE_SIZE = 1000

STATE_PATH = Path("data/state.json")

_TOPIC_KIND: dict[str, str] = {TOPIC_ADDED: "added", TOPIC_REMOVED: "removed"}


def _is_no_records(message: str) -> bool:
    lower = message.lower()
    return "no record" in lower or "no log" in lower


def _fetch_via_etherscan(
    chain: str, from_block: int, to_block: int, api_key: str, session: Any
) -> list[dict[str, Any]]:
    oracle = CHAINS[chain]["oracle"]
    chain_id = ETHERSCAN_CHAIN_IDS[chain]
    sess = session if session is not None else requests
    out: list[dict[str, Any]] = []
    for topic, kind in _TOPIC_KIND.items():
        page = 1
        while True:
            params = {
                "chainid": chain_id,
                "module": "logs",
                "action": "getLogs",
                "address": oracle,
                "fromBlock": from_block,
                "toBlock": to_block,
                "topic0": topic,
                "page": page,
                "offset": PAGE_SIZE,
                "apikey": api_key,
            }
            response = sess.get(ETHERSCAN_URL, params=params, timeout=30)
            response.raise_for_status()
            body = response.json()
            result = body.get("result")
            if not isinstance(result, list):
                message = str(body.get("message") or result)
                if _is_no_records(message):
                    break
                raise RuntimeError(f"Etherscan error for {chain}: {message}")
            for entry in result:
                addresses = decode_address_array(entry["data"])
                block = int(entry["blockNumber"], 16)
                tx_hash = entry["transactionHash"]
                out.extend(
                    {"chain": chain, "block": block, "tx_hash": tx_hash, "kind": kind, "address": addr}
                    for addr in addresses
                )
            if len(result) < PAGE_SIZE:
                break
            page += 1
            time.sleep(0.25)
    return sorted(out, key=lambda e: e["block"])


def _fetch_via_rpc(chain: str, from_block: int, to_block: int, session: Any) -> list[dict[str, Any]]:
    oracle = CHAINS[chain]["oracle"]
    rpc_url = CHAINS[chain]["rpc"]
    out: list[dict[str, Any]] = []
    consecutive_failures = 0
    for lo in range(from_block, to_block + 1, CHUNK_SIZE):
        hi = min(lo + CHUNK_SIZE - 1, to_block)
        try:
            logs = call(
                rpc_url,
                "eth_getLogs",
                [
                    {
                        "address": oracle,
                        "fromBlock": hex(lo),
                        "toBlock": hex(hi),
                        "topics": [[TOPIC_ADDED, TOPIC_REMOVED]],
                    }
                ],
                session=session,
            )
        except RpcBlocked as exc:
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                raise RpcBlocked(
                    f"{rpc_url} refused {consecutive_failures} eth_getLogs calls in a row ({exc}) "
                    f"scanning {chain} blocks {lo}-{hi}. This endpoint does not serve logs. "
                    "Set ETHERSCAN_API_KEY or use another endpoint -- do not report zero events "
                    "from a scan that did not run."
                ) from exc
            logger.warning(
                "eth_getLogs blocked on %s (%s), %d/%d consecutive refusals",
                chain,
                exc,
                consecutive_failures,
                MAX_CONSECUTIVE_FAILURES,
            )
            continue
        consecutive_failures = 0
        for entry in logs:
            kind = _TOPIC_KIND.get(entry["topics"][0])
            if kind is None:
                continue
            addresses = decode_address_array(entry["data"])
            block = int(entry["blockNumber"], 16)
            tx_hash = entry["transactionHash"]
            out.extend(
                {"chain": chain, "block": block, "tx_hash": tx_hash, "kind": kind, "address": addr}
                for addr in addresses
            )
    return out


def fetch_designation_events(
    chain: str,
    from_block: int,
    to_block: int,
    *,
    api_key: str | None = None,
    session: Any = None,
) -> list[dict[str, Any]]:
    """One dict per address per event: {chain, block, tx_hash, kind, address}."""
    if chain not in CHAINS:
        raise ValueError(f"unknown chain: {chain}")

    key = api_key if api_key is not None else os.environ.get("ETHERSCAN_API_KEY")
    if key:
        if chain in ETHERSCAN_CHAIN_IDS:
            return _fetch_via_etherscan(chain, from_block, to_block, key, session)
        logger.warning(
            "Etherscan does not support chain %s; falling back to eth_getLogs over RPC", chain
        )
    return _fetch_via_rpc(chain, from_block, to_block, session)


# --- state -------------------------------------------------------------------


def _empty_state() -> dict[str, Any]:
    return {"chains": {}, "updated_at": None}


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    """Load data/state.json. A missing or corrupt file is logged and rebuilt,
    never allowed to crash the run."""
    try:
        raw = path.read_text()
    except FileNotFoundError:
        logger.info("no state file at %s; starting fresh", path)
        return _empty_state()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("state file %s is corrupt (%s); rebuilding", path, exc)
        return _empty_state()

    if not isinstance(data, dict) or not isinstance(data.get("chains"), dict):
        logger.error("state file %s has an unexpected shape; rebuilding", path)
        return _empty_state()

    return data


def save_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    payload = {
        "chains": state.get("chains", {}),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))


def resolve_start_block(
    chain: str, head: int, lookback: int, state: dict[str, Any] | None = None
) -> int:
    """Resume after the chain's last processed block, or start at head minus
    lookback when nothing has been recorded for it yet."""
    state = state if state is not None else load_state()
    last_block = state.get("chains", {}).get(chain, {}).get("last_block")
    if last_block is None:
        return max(0, head - lookback)
    return last_block + 1
