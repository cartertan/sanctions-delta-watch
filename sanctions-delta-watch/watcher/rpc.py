"""JSON-RPC client and chain config for the Chainalysis sanctions oracle.

Public RPCs usually allow eth_call but block eth_getLogs and similar heavier
calls (HTTP 403, or a JSON-RPC error naming the reason). RpcBlocked marks
that class of refusal so callers can fall back to another provider instead
of treating it as a transient failure worth retrying.
"""
from __future__ import annotations

import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

logger = logging.getLogger(__name__)

SELECTOR = "0xdf592f7d"  # isSanctioned(address)
SHARED_ORACLE = "0x40C57923924B5c5c5455c48D93317139ADDaC8fb"
BASE_ORACLE = "0x3A91A31cB3dC49b4db9Ce721F50a9D076c8D739B"

_DEFAULT_RPCS: dict[str, str] = {
    "Ethereum": "https://ethereum-rpc.publicnode.com",
    "Polygon": "https://polygon-rpc.com",
    "BNB": "https://bsc-dataseed.binance.org",
    "Avalanche": "https://api.avax.network/ext/bc/C/rpc",
    "Optimism": "https://mainnet.optimism.io",
    "Arbitrum": "https://arb1.arbitrum.io/rpc",
    "Celo": "https://forno.celo.org",
    "Base": "https://mainnet.base.org",
}

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_BLOCKED_STATUS_CODES = {401, 403, 405, 429}
_BLOCKED_KEYWORDS = ("not allowed", "unsupported", "unauthorized", "limit exceeded")
_BLOCK_RANGE_WORDS = ("exceed", "limit", "too large", "too big", "too wide")


class RpcError(Exception):
    """A JSON-RPC or network call failed."""


class RpcBlocked(RpcError):
    """The endpoint refused the call outright (HTTP 401/403/405/429, or a JSON-RPC
    error indicating the method or range isn't allowed). Never worth retrying against
    the same endpoint."""


def _env_var_name(chain: str) -> str:
    return "RPC_" + re.sub(r"[^A-Z0-9]+", "_", chain.upper()).strip("_")


def _build_chains() -> dict[str, dict[str, str]]:
    chains: dict[str, dict[str, str]] = {}
    for name, default_rpc in _DEFAULT_RPCS.items():
        oracle = BASE_ORACLE if name == "Base" else SHARED_ORACLE
        rpc_url = os.environ.get(_env_var_name(name), default_rpc)
        chains[name] = {"oracle": oracle, "rpc": rpc_url}
    return chains


CHAINS: dict[str, dict[str, str]] = _build_chains()


def _looks_blocked(message: str) -> bool:
    lower = message.lower()
    if any(keyword in lower for keyword in _BLOCKED_KEYWORDS):
        return True
    if "block range" in lower and any(word in lower for word in _BLOCK_RANGE_WORDS):
        return True
    return False


def call(
    url: str,
    method: str,
    params: list[Any],
    *,
    session: Any = None,
    timeout: int = 20,
    max_retries: int = 3,
) -> Any:
    """POST a JSON-RPC 2.0 request. Retries network errors with exponential
    backoff; never retries RpcBlocked, since the endpoint has already told us
    the call itself is refused."""
    sess = session if session is not None else requests
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = sess.post(url, json=payload, timeout=timeout)
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            wait = 2**attempt
            logger.warning(
                "network error calling %s (%s), retrying in %ss: %s", url, method, wait, exc
            )
            time.sleep(wait)
            continue

        if response.status_code in _BLOCKED_STATUS_CODES:
            raise RpcBlocked(f"HTTP {response.status_code} from {url}")
        if response.status_code >= 400:
            raise RpcError(f"HTTP {response.status_code} from {url}")

        body = response.json()
        if "error" in body:
            message = str(body["error"])
            if _looks_blocked(message):
                raise RpcBlocked(message)
            raise RpcError(message)
        return body["result"]

    logger.error(
        "network error calling %s (%s) after %d attempts: %s",
        url,
        method,
        max_retries + 1,
        last_exc,
    )
    raise RpcError(f"network error calling {url}: {last_exc}") from last_exc


def is_sanctioned(chain: str, address: str, *, session: Any = None) -> bool:
    """Live isSanctioned(address) check against the given chain's oracle."""
    if chain not in CHAINS:
        raise ValueError(f"unknown chain: {chain}")
    if not _ADDRESS_RE.match(address):
        raise ValueError(f"malformed address: {address!r}")

    oracle = CHAINS[chain]["oracle"]
    rpc_url = CHAINS[chain]["rpc"]
    data = SELECTOR + address.lower()[2:].rjust(64, "0")
    result = call(rpc_url, "eth_call", [{"to": oracle, "data": data}, "latest"], session=session)
    return int(result, 16) == 1


def screen_all_chains(address: str, *, session: Any = None) -> dict[str, dict[str, Any]]:
    """Check `address` against every chain's oracle in parallel. A chain that
    fails never aborts the others; its result carries an "error" key instead."""

    def screen_one(chain: str) -> dict[str, Any]:
        try:
            return {"sanctioned": is_sanctioned(chain, address, session=session)}
        except Exception as exc:
            logger.error("screening %s on %s failed: %s", address, chain, exc)
            return {"error": str(exc)}

    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=len(CHAINS)) as executor:
        future_to_chain = {executor.submit(screen_one, chain): chain for chain in CHAINS}
        for future in as_completed(future_to_chain):
            results[future_to_chain[future]] = future.result()
    return results
