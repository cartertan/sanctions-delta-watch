"""Tests for watcher.rpc. No real network calls: everything goes through a fake session."""
from __future__ import annotations

import importlib
import os
from typing import Any

import pytest
import requests

from watcher import rpc

SANCTIONED_RESULT = "0x" + "0" * 63 + "1"
CLEAN_RESULT = "0x" + "0" * 64
DOC_SANCTIONED = "0x7F367cC41522cE07553e823bf3be79A889DEbe1B"


class FakeResponse:
    def __init__(self, status_code: int = 200, json_data: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self) -> dict[str, Any]:
        return self._json_data


class FakeSession:
    """Records every POST and plays back a scripted list of responses/exceptions."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, json: dict[str, Any], timeout: int) -> FakeResponse:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rpc.time, "sleep", lambda *_a, **_k: None)


# --- call() ---------------------------------------------------------------


def test_call_posts_json_rpc_2_0_with_20s_timeout() -> None:
    session = FakeSession([FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": "0x1"})])
    result = rpc.call("http://x", "eth_blockNumber", [], session=session)
    assert result == "0x1"
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["timeout"] == 20
    assert call["json"] == {"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}


@pytest.mark.parametrize("status", [401, 403, 405, 429])
def test_call_raises_rpc_blocked_on_blocked_http_status(status: int) -> None:
    session = FakeSession([FakeResponse(status, {})])
    with pytest.raises(rpc.RpcBlocked):
        rpc.call("http://x", "eth_call", [], session=session)
    assert len(session.calls) == 1  # never retried


def test_call_raises_rpc_error_on_other_bad_http_status() -> None:
    session = FakeSession([FakeResponse(500, {})])
    with pytest.raises(rpc.RpcError):
        rpc.call("http://x", "eth_call", [], session=session)
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "message",
    [
        "method not allowed",
        "unsupported method",
        "unauthorized",
        "limit exceeded",
        "query returned more than 10000 results, block range too large",
        "eth_getLogs is limited to a 2000 block range",
    ],
)
def test_call_classifies_blocked_json_rpc_messages(message: str) -> None:
    session = FakeSession([FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "error": {"message": message}})])
    with pytest.raises(rpc.RpcBlocked):
        rpc.call("http://x", "eth_getLogs", [], session=session)


def test_call_raises_rpc_error_on_generic_json_rpc_error() -> None:
    session = FakeSession(
        [FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "error": {"message": "execution reverted"}})]
    )
    with pytest.raises(rpc.RpcError) as excinfo:
        rpc.call("http://x", "eth_call", [], session=session)
    assert not isinstance(excinfo.value, rpc.RpcBlocked)


def test_call_retries_network_errors_and_eventually_succeeds() -> None:
    session = FakeSession(
        [
            requests.exceptions.ConnectionError("boom"),
            requests.exceptions.Timeout("boom again"),
            FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": "0x2a"}),
        ]
    )
    result = rpc.call("http://x", "eth_blockNumber", [], session=session)
    assert result == "0x2a"
    assert len(session.calls) == 3


def test_call_raises_rpc_error_after_exhausting_retries() -> None:
    session = FakeSession([requests.exceptions.ConnectionError("boom")] * 10)
    with pytest.raises(rpc.RpcError):
        rpc.call("http://x", "eth_blockNumber", [], session=session)
    # 1 initial attempt + 3 retries = 4 total POSTs
    assert len(session.calls) == 4


def test_call_never_retries_rpc_blocked() -> None:
    session = FakeSession([FakeResponse(403, {})])
    with pytest.raises(rpc.RpcBlocked):
        rpc.call("http://x", "eth_call", [], session=session)
    assert len(session.calls) == 1


# --- is_sanctioned() -------------------------------------------------------


def test_is_sanctioned_encodes_call_data_correctly() -> None:
    session = FakeSession([FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": CLEAN_RESULT})])
    rpc.is_sanctioned("Ethereum", DOC_SANCTIONED, session=session)
    sent = session.calls[0]["json"]["params"][0]
    expected_data = "0xdf592f7d" + DOC_SANCTIONED.lower()[2:].rjust(64, "0")
    assert sent["data"] == expected_data
    assert sent["to"] == rpc.CHAINS["Ethereum"]["oracle"]


def test_is_sanctioned_true_when_result_is_one() -> None:
    session = FakeSession([FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": SANCTIONED_RESULT})])
    assert rpc.is_sanctioned("Ethereum", DOC_SANCTIONED, session=session) is True


def test_is_sanctioned_false_when_result_is_zero() -> None:
    session = FakeSession([FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": CLEAN_RESULT})])
    assert rpc.is_sanctioned("Ethereum", DOC_SANCTIONED, session=session) is False


def test_is_sanctioned_lowercases_address() -> None:
    session = FakeSession([FakeResponse(200, {"jsonrpc": "2.0", "id": 1, "result": CLEAN_RESULT})])
    rpc.is_sanctioned("Ethereum", DOC_SANCTIONED.upper().replace("0X", "0x"), session=session)
    sent = session.calls[0]["json"]["params"][0]
    assert sent["data"] == "0xdf592f7d" + DOC_SANCTIONED.lower()[2:].rjust(64, "0")


@pytest.mark.parametrize(
    "bad_address",
    [
        "not-an-address",
        "0x123",
        "7F367cC41522cE07553e823bf3be79A889DEbe1B",  # missing 0x
        "0x7F367cC41522cE07553e823bf3be79A889DEbe1BFF",  # too long
        "0xZZZ67cC41522cE07553e823bf3be79A889DEbe1B",  # non-hex chars
    ],
)
def test_is_sanctioned_raises_value_error_on_malformed_address(bad_address: str) -> None:
    with pytest.raises(ValueError):
        rpc.is_sanctioned("Ethereum", bad_address, session=FakeSession([]))


def test_is_sanctioned_raises_value_error_on_unknown_chain() -> None:
    with pytest.raises(ValueError):
        rpc.is_sanctioned("Moonchain", DOC_SANCTIONED, session=FakeSession([]))


# --- CHAINS -----------------------------------------------------------------


def test_chains_covers_all_eight() -> None:
    assert set(rpc.CHAINS) == {
        "Ethereum",
        "Polygon",
        "BNB",
        "Avalanche",
        "Optimism",
        "Arbitrum",
        "Celo",
        "Base",
    }


def test_base_uses_its_own_oracle_address() -> None:
    assert rpc.CHAINS["Base"]["oracle"] == "0x3A91A31cB3dC49b4db9Ce721F50a9D076c8D739B"
    assert rpc.CHAINS["Ethereum"]["oracle"] == "0x40C57923924B5c5c5455c48D93317139ADDaC8fb"
    others = [c for c in rpc.CHAINS if c not in ("Base",)]
    oracles = {rpc.CHAINS[c]["oracle"] for c in others}
    assert oracles == {"0x40C57923924B5c5c5455c48D93317139ADDaC8fb"}


def test_rpc_url_overridable_by_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RPC_ETHEREUM", "http://custom-ethereum-rpc.example")
    reloaded = importlib.reload(rpc)
    try:
        assert reloaded.CHAINS["Ethereum"]["rpc"] == "http://custom-ethereum-rpc.example"
    finally:
        monkeypatch.delenv("RPC_ETHEREUM", raising=False)
        importlib.reload(rpc)


# --- screen_all_chains() ----------------------------------------------------


def test_screen_all_chains_runs_all_chains_and_isolates_one_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_is_sanctioned(chain: str, address: str, *, session: Any = None) -> bool:
        if chain == "Polygon":
            raise rpc.RpcBlocked("polygon endpoint refuses eth_call")
        return chain == "Ethereum"

    monkeypatch.setattr(rpc, "is_sanctioned", fake_is_sanctioned)

    results = rpc.screen_all_chains(DOC_SANCTIONED)

    assert set(results) == set(rpc.CHAINS)
    assert results["Ethereum"] == {"sanctioned": True}
    assert results["Base"] == {"sanctioned": False}
    assert "error" in results["Polygon"]
    assert "polygon" in results["Polygon"]["error"].lower()
