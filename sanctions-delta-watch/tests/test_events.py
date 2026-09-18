"""Tests for watcher.events. No real network calls: everything goes through fake sessions."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from watcher import events
from watcher.rpc import RpcBlocked

TOPIC_ADDED = events.TOPIC_ADDED
TOPIC_REMOVED = events.TOPIC_REMOVED

ADDR_1 = "7f367cc41522ce07553e823bf3be79a889debe1b"
ADDR_2 = "7f268357a8c2552623316e2562d90e642bb538e5"


def _address_array_data(*addrs: str) -> str:
    """Build the non-indexed address[] ABI encoding step0's decoder expects."""
    words = [
        hex(32)[2:].rjust(64, "0"),  # offset = 32
        hex(len(addrs))[2:].rjust(64, "0"),  # length
    ]
    words.extend(a.rjust(64, "0") for a in addrs)
    return "0x" + "".join(words)


# --- decode_address_array (re-exported from scripts.step0_verify_events) ---


def test_decode_single_address() -> None:
    data = _address_array_data(ADDR_1)
    assert events.decode_address_array(data) == ["0x" + ADDR_1]


def test_decode_multiple_addresses() -> None:
    data = _address_array_data(ADDR_1, ADDR_2)
    assert events.decode_address_array(data) == ["0x" + ADDR_1, "0x" + ADDR_2]


def test_decode_empty_data() -> None:
    assert events.decode_address_array("0x") == []


# --- Etherscan fakes --------------------------------------------------------


class FakeEtherscanResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self._body


class FakeEtherscanSession:
    def __init__(self, responses: list[FakeEtherscanResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any], timeout: int) -> FakeEtherscanResponse:
        self.requests.append({"url": url, "params": params, "timeout": timeout})
        return self.responses.pop(0)


def _log_entry(block: int, tx_hash: str, topic: str, *addrs: str) -> dict[str, Any]:
    return {
        "blockNumber": hex(block),
        "transactionHash": tx_hash,
        "topics": [topic],
        "data": _address_array_data(*addrs),
    }


def _ok_body(result: list[dict[str, Any]]) -> FakeEtherscanResponse:
    return FakeEtherscanResponse({"status": "1", "message": "OK", "result": result})


def _no_records_body() -> FakeEtherscanResponse:
    return FakeEtherscanResponse(
        {"status": "0", "message": "No records found", "result": "No records found"}
    )


# --- fetch_designation_events: Etherscan path -------------------------------


def test_fetch_via_etherscan_one_dict_per_address() -> None:
    added_page = [_log_entry(100, "0xabc", TOPIC_ADDED, ADDR_1, ADDR_2)]
    session = FakeEtherscanSession([_ok_body(added_page), _no_records_body()])
    result = events.fetch_designation_events(
        "Ethereum", 0, 200, api_key="KEY", session=session
    )
    assert len(result) == 2
    assert {r["address"] for r in result} == {"0x" + ADDR_1, "0x" + ADDR_2}
    for r in result:
        assert r["chain"] == "Ethereum"
        assert r["block"] == 100
        assert r["tx_hash"] == "0xabc"
        assert r["kind"] == "added"


def test_fetch_via_etherscan_topic_to_kind_mapping() -> None:
    added = [_log_entry(1, "0xa1", TOPIC_ADDED, ADDR_1)]
    removed = [_log_entry(2, "0xa2", TOPIC_REMOVED, ADDR_2)]
    session = FakeEtherscanSession([_ok_body(added), _ok_body(removed)])
    result = events.fetch_designation_events("Ethereum", 0, 200, api_key="KEY", session=session)
    kinds = {r["address"]: r["kind"] for r in result}
    assert kinds["0x" + ADDR_1] == "added"
    assert kinds["0x" + ADDR_2] == "removed"


def test_fetch_via_etherscan_paginates_past_1000_records() -> None:
    page1 = [_log_entry(i, f"0x{i}", TOPIC_ADDED, ADDR_1) for i in range(1000)]
    page2 = [_log_entry(1000, "0xlast", TOPIC_ADDED, ADDR_2)]
    session = FakeEtherscanSession([_ok_body(page1), _ok_body(page2), _no_records_body()])
    result = events.fetch_designation_events("Ethereum", 0, 5000, api_key="KEY", session=session)
    assert len(result) == 1001
    # page param incremented between the two ADDED requests
    added_requests = [r for r in session.requests if r["params"]["topic0"] == TOPIC_ADDED]
    assert [r["params"]["page"] for r in added_requests] == [1, 2]


def test_fetch_via_etherscan_no_records_found_is_empty_not_error() -> None:
    session = FakeEtherscanSession([_no_records_body(), _no_records_body()])
    result = events.fetch_designation_events("Ethereum", 0, 200, api_key="KEY", session=session)
    assert result == []


def test_fetch_via_etherscan_raises_on_real_error() -> None:
    error_body = {"status": "0", "message": "NOTOK", "result": "Invalid API Key"}
    session = FakeEtherscanSession([FakeEtherscanResponse(error_body)])
    with pytest.raises(RuntimeError):
        events.fetch_designation_events("Ethereum", 0, 200, api_key="BAD", session=session)


def test_fetch_via_etherscan_unsupported_chain_falls_back_to_rpc(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Celo is treated as unsupported by the Etherscan v2 mapping.
    session = FakeRpcSession([FakeRpcResponse(200, {"jsonrpc": "2.0", "id": 1, "result": []})])
    with caplog.at_level("WARNING"):
        result = events.fetch_designation_events("Celo", 0, 1999, api_key="KEY", session=session)
    assert result == []
    assert any("does not support" in message for message in caplog.messages)


# --- RPC fallback fakes ------------------------------------------------------


class FakeRpcResponse:
    def __init__(self, status_code: int = 200, json_data: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self) -> dict[str, Any]:
        return self._json_data


class FakeRpcSession:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, json: dict[str, Any], timeout: int) -> FakeRpcResponse:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _rpc_ok(logs: list[dict[str, Any]]) -> FakeRpcResponse:
    return FakeRpcResponse(200, {"jsonrpc": "2.0", "id": 1, "result": logs})


def _rpc_blocked() -> FakeRpcResponse:
    return FakeRpcResponse(403, {})


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(events.time, "sleep", lambda *_a, **_k: None)
    import watcher.rpc as rpc_module

    monkeypatch.setattr(rpc_module.time, "sleep", lambda *_a, **_k: None)


@pytest.fixture(autouse=True)
def no_ambient_etherscan_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # The real environment may have a key set; tests control this explicitly
    # via the api_key= argument instead.
    monkeypatch.delenv("ETHERSCAN_API_KEY", raising=False)


# --- fetch_designation_events: RPC fallback path ----------------------------


def test_fetch_via_rpc_used_when_no_etherscan_key() -> None:
    logs = [
        {
            "blockNumber": hex(50),
            "transactionHash": "0xdead",
            "topics": [TOPIC_ADDED],
            "data": _address_array_data(ADDR_1),
        }
    ]
    session = FakeRpcSession([_rpc_ok(logs)])
    result = events.fetch_designation_events("Ethereum", 0, 1999, api_key=None, session=session)
    assert result == [
        {
            "chain": "Ethereum",
            "block": 50,
            "tx_hash": "0xdead",
            "kind": "added",
            "address": "0x" + ADDR_1,
        }
    ]


def test_fetch_via_rpc_chunks_in_2000_block_ranges() -> None:
    session = FakeRpcSession([_rpc_ok([]) for _ in range(3)])
    events.fetch_designation_events("Ethereum", 0, 5999, api_key=None, session=session)
    assert len(session.calls) == 3
    first_params = session.calls[0]["json"]["params"][0]
    assert first_params["fromBlock"] == hex(0)
    assert first_params["toBlock"] == hex(1999)
    third_params = session.calls[2]["json"]["params"][0]
    assert third_params["fromBlock"] == hex(4000)
    assert third_params["toBlock"] == hex(5999)


def test_fetch_via_rpc_aborts_after_5_consecutive_refusals() -> None:
    session = FakeRpcSession([_rpc_blocked() for _ in range(5)])
    with pytest.raises(RpcBlocked):
        events.fetch_designation_events("Ethereum", 0, 9999, api_key=None, session=session)
    assert len(session.calls) == 5


def test_fetch_via_rpc_never_reports_zero_from_a_scan_that_did_not_run() -> None:
    # All chunks blocked -> must raise, never silently return [].
    session = FakeRpcSession([_rpc_blocked() for _ in range(5)])
    with pytest.raises(RpcBlocked):
        events.fetch_designation_events("Ethereum", 0, 9999, api_key=None, session=session)


def test_fetch_via_rpc_resets_consecutive_failure_count_on_success() -> None:
    responses = (
        [_rpc_blocked() for _ in range(4)]
        + [_rpc_ok([])]
        + [_rpc_blocked() for _ in range(5)]
    )
    session = FakeRpcSession(responses)
    with pytest.raises(RpcBlocked):
        events.fetch_designation_events("Ethereum", 0, 10 * 2000 - 1, api_key=None, session=session)
    assert len(session.calls) == 10


# --- load_state / save_state -------------------------------------------------


def test_state_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    state = {"chains": {"Ethereum": {"last_block": 12345}}}
    events.save_state(state, path=path)
    loaded = events.load_state(path=path)
    assert loaded["chains"]["Ethereum"]["last_block"] == 12345
    assert "updated_at" in loaded


def test_load_state_missing_file_returns_empty_state(tmp_path: Path) -> None:
    path = tmp_path / "does-not-exist.json"
    state = events.load_state(path=path)
    assert state == {"chains": {}, "updated_at": None}


def test_load_state_corrupt_file_logs_and_rebuilds(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not valid json")
    with caplog.at_level("ERROR"):
        state = events.load_state(path=path)
    assert state == {"chains": {}, "updated_at": None}
    assert any("corrupt" in message.lower() for message in caplog.messages)


def test_load_state_unexpected_shape_logs_and_rebuilds(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "state.json"
    path.write_text(json.dumps(["not", "a", "dict"]))
    with caplog.at_level("ERROR"):
        state = events.load_state(path=path)
    assert state == {"chains": {}, "updated_at": None}


def test_resolve_start_block_uses_lookback_when_no_state() -> None:
    state = {"chains": {}, "updated_at": None}
    assert events.resolve_start_block("Ethereum", head=100_000, lookback=50_000, state=state) == 50_000


def test_resolve_start_block_resumes_after_last_block() -> None:
    state = {"chains": {"Ethereum": {"last_block": 90_000}}, "updated_at": "2026-01-01T00:00:00+00:00"}
    assert events.resolve_start_block("Ethereum", head=100_000, lookback=50_000, state=state) == 90_001
