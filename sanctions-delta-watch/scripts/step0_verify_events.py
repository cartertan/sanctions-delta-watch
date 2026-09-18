"""Step 0: does the Chainalysis oracle emit designation events?

Free public RPCs usually allow eth_call but block eth_getLogs (HTTP 403).
So this script has two providers:

    # A. Etherscan log API (recommended). Free key, instant signup, handles big ranges.
    python scripts/step0_verify_events.py --provider etherscan --key YOURKEY

    # B. Raw RPC, only if the endpoint allows eth_getLogs
    python scripts/step0_verify_events.py --provider rpc --rpc https://eth.llamarpc.com

    # C. Just check which RPCs in the candidate list support eth_getLogs
    python scripts/step0_verify_events.py --probe

The isSanctioned check always runs over plain RPC and needs no key.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

import requests

ORACLE = "0x40C57923924B5c5c5455c48D93317139ADDaC8fb"
TOPIC_ADDED = "0x2596d7dd6966c5673f9c06ddb0564c4f0e6d8d206ea075b83ad9ddd71a4fb927"
TOPIC_REMOVED = "0x32aab684eee99db715515d1a9987a8fe33bb6341b0e35e60db7eab48a08f9a3a"
SELECTOR = "0xdf592f7d"
DOC_SANCTIONED = "0x7F367cC41522cE07553e823bf3be79A889DEbe1B"
DOC_CLEAN = "0x7f268357A8c2552623316e2562D90e642bB538E5"
ETHERSCAN = "https://api.etherscan.io/v2/api"
CHUNK = 2000
MAX_CONSECUTIVE_FAILURES = 5

RPC_CANDIDATES = [
    "https://ethereum-rpc.publicnode.com",
    "https://eth.llamarpc.com",
    "https://rpc.ankr.com/eth",
    "https://1rpc.io/eth",
    "https://eth.drpc.org",
    "https://rpc.flashbots.net",
]

log = logging.getLogger("step0")


class RpcBlocked(RuntimeError):
    """The endpoint refused the call (403 / method not allowed)."""


def rpc(url: str, method: str, params: list):
    r = requests.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params}, timeout=20)
    if r.status_code in (401, 403, 405, 429):
        raise RpcBlocked(f"HTTP {r.status_code}")
    r.raise_for_status()
    body = r.json()
    if "error" in body:
        msg = str(body["error"])
        if any(w in msg.lower() for w in ("not allowed", "unsupported", "unauthorized", "limit exceeded")):
            raise RpcBlocked(msg)
        raise RuntimeError(msg)
    return body["result"]


def decode_address_array(data_hex: str) -> list[str]:
    """Decode a non-indexed address[] from event data."""
    data = data_hex[2:] if data_hex.startswith("0x") else data_hex
    words = [data[i:i + 64] for i in range(0, len(data), 64)]
    if not words:
        return []
    offset_words = int(words[0], 16) // 32
    if offset_words >= len(words):
        return []
    length = int(words[offset_words], 16)
    return ["0x" + w[-40:] for w in words[offset_words + 1: offset_words + 1 + length]]


def is_sanctioned(url: str, addr: str) -> bool:
    data = SELECTOR + addr[2:].lower().rjust(64, "0")
    return int(rpc(url, "eth_call", [{"to": ORACLE, "data": data}, "latest"]), 16) == 1


def probe_rpcs() -> None:
    """Report which endpoints allow eth_call and eth_getLogs."""
    print(f"{'endpoint':<45} {'eth_call':<10} eth_getLogs")
    for url in RPC_CANDIDATES:
        try:
            head = int(rpc(url, "eth_blockNumber", []), 16)
            call_ok = "ok"
        except Exception as exc:
            print(f"{url:<45} {'fail':<10} - ({exc.__class__.__name__})")
            continue
        try:
            rpc(url, "eth_getLogs", [{"address": ORACLE, "fromBlock": hex(head - 50),
                                      "toBlock": hex(head), "topics": [[TOPIC_ADDED, TOPIC_REMOVED]]}])
            logs_ok = "ok"
        except RpcBlocked as exc:
            logs_ok = f"blocked ({exc})"
        except Exception as exc:
            logs_ok = f"error ({exc.__class__.__name__})"
        print(f"{url:<45} {call_ok:<10} {logs_ok}")


def events_via_etherscan(key: str, start: int, end: int) -> list[dict]:
    """One query per topic; Etherscan serves the whole range."""
    out: list[dict] = []
    for topic in (TOPIC_ADDED, TOPIC_REMOVED):
        page = 1
        while True:
            params = {"chainid": 1, "module": "logs", "action": "getLogs", "address": ORACLE,
                      "fromBlock": start, "toBlock": end, "topic0": topic,
                      "page": page, "offset": 1000, "apikey": key}
            r = requests.get(ETHERSCAN, params=params, timeout=30)
            r.raise_for_status()
            body = r.json()
            result = body.get("result")
            if not isinstance(result, list):
                msg = str(body.get("message") or result)
                if "No record" in msg or "No log" in msg:
                    break
                raise RuntimeError(f"Etherscan: {msg}")
            out += [{"block": int(x["blockNumber"], 16), "tx": x["transactionHash"],
                     "kind": "ADDED" if topic == TOPIC_ADDED else "REMOVED",
                     "addrs": decode_address_array(x["data"])} for x in result]
            if len(result) < 1000:
                break
            page += 1
            time.sleep(0.25)
    return sorted(out, key=lambda e: e["block"])


def events_via_rpc(url: str, start: int, end: int) -> list[dict]:
    out: list[dict] = []
    failures = 0
    for lo in range(start, end + 1, CHUNK):
        hi = min(lo + CHUNK - 1, end)
        try:
            logs = rpc(url, "eth_getLogs", [{"address": ORACLE, "fromBlock": hex(lo), "toBlock": hex(hi),
                                             "topics": [[TOPIC_ADDED, TOPIC_REMOVED]]}])
            failures = 0
        except RpcBlocked as exc:
            failures += 1
            if failures >= MAX_CONSECUTIVE_FAILURES:
                raise RpcBlocked(
                    f"{url} refused {failures} eth_getLogs calls in a row ({exc}). "
                    "This endpoint does not serve logs. Run with --probe, or use --provider etherscan."
                ) from exc
            continue
        out += [{"block": int(x["blockNumber"], 16), "tx": x["transactionHash"],
                 "kind": "ADDED" if x["topics"][0] == TOPIC_ADDED else "REMOVED",
                 "addrs": decode_address_array(x["data"])} for x in logs]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["etherscan", "rpc"], default="etherscan")
    ap.add_argument("--key", default=os.environ.get("ETHERSCAN_API_KEY"), help="Etherscan API key")
    ap.add_argument("--rpc", default="https://ethereum-rpc.publicnode.com")
    ap.add_argument("--blocks", type=int, default=2_000_000)
    ap.add_argument("--probe", action="store_true", help="test which RPCs allow eth_getLogs, then exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.probe:
        probe_rpcs()
        return 0

    print(f"Doc sanctioned address -> isSanctioned = {is_sanctioned(args.rpc, DOC_SANCTIONED)}")
    print(f"Doc clean address      -> isSanctioned = {is_sanctioned(args.rpc, DOC_CLEAN)}")

    head = int(rpc(args.rpc, "eth_blockNumber", []), 16)
    start = max(0, head - args.blocks)
    print(f"Scanning blocks {start}-{head} via {args.provider}")

    if args.provider == "etherscan":
        if not args.key:
            print("\nNeed an Etherscan API key: --key YOURKEY (free, instant at etherscan.io).")
            print("Or try --provider rpc after finding an endpoint with --probe.")
            return 2
        events = events_via_etherscan(args.key, start, head)
    else:
        try:
            events = events_via_rpc(args.rpc, start, head)
        except RpcBlocked as exc:
            print(f"\n{exc}")
            return 2

    for e in events:
        print(f"block {e['block']} {e['kind']} {len(e['addrs'])} address(es): {e['addrs'][:5]}")
    print(f"\nEvents found: {len(events)}")
    if not events:
        print("The scan worked but found nothing. Either widen --blocks, or the oracle updates without "
              "emitting these events. Check the Events tab for the contract on Etherscan, then use the "
              "snapshot-diff fallback in BUILD_PLAN.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
