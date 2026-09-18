"""Tests for copilot.brief. gh and Ollama are always injected; nothing hits the network."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import requests

from copilot import brief
from watcher.cases import _render_issue_body

SAMPLE_HIT = {
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

MALFORMED_BODY = "This issue has no exposure table at all, just prose."


# --- parse_case_from_body ------------------------------------------------------


def test_parse_case_from_body_round_trips_a_real_rendered_issue() -> None:
    body = _render_issue_body(SAMPLE_HIT)
    case = brief.parse_case_from_body(body)
    assert case["customer_id"] == "CUST-2011"
    assert case["chain"] == "Ethereum"
    assert case["address"] == "0xcb74874f1e06fcf80a306e06e5379a44b488ba2d"
    assert case["relationship"] == "counterparty"
    assert case["direction"] == "out"
    assert case["asset"] == "USDT"
    assert case["amount"] == pytest.approx(18400)
    assert case["tx_date"] == "2026-08-22"
    assert case["tx_hash"] == "0xSYNTHETIC0101"
    assert case["event_block"] == 24687198
    assert case["event_tx"] == "0x5446ac44bc6aa4558911d71b95627a7dedf97d7ebc248bdd9644a617e15cd72f"
    assert case["severity"] == "Critical"
    assert case["confirmed_at"] == "2026-09-18T07:30:01+00:00"


def test_parse_case_from_body_raises_on_malformed_body() -> None:
    with pytest.raises(ValueError):
        brief.parse_case_from_body(MALFORMED_BODY)


def test_parse_case_from_body_raises_when_table_present_but_event_missing() -> None:
    body = (
        "| Field | Value |\n"
        "|---|---|\n"
        "| Customer | CUST-1 |\n"
        "| Chain | Ethereum |\n"
        "| Address | 0xaaaa |\n"
        "| Relationship | counterparty |\n"
        "| Direction | out |\n"
        "| Asset | USDT |\n"
        "| Amount | 100 |\n"
        "| Tx date | 2026-01-01 |\n"
        "| Tx hash | 0xtx |\n"
        "| Severity | Medium |\n"
    )
    with pytest.raises(ValueError):
        brief.parse_case_from_body(body)


# --- render_prompt ------------------------------------------------------------


def test_render_prompt_substitutes_case_json(tmp_path: Path) -> None:
    prompt_path = tmp_path / "mlro_brief.md"
    prompt_path.write_text("Rules apply.\n\nCASE DATA:\n{case_json}\n")
    case = {"customer_id": "CUST-1", "chain": "Ethereum"}
    rendered = brief.render_prompt(case, prompt_path=prompt_path)
    assert "Rules apply." in rendered
    assert '"customer_id": "CUST-1"' in rendered
    assert "{case_json}" not in rendered


# --- strip_think ---------------------------------------------------------------


def test_strip_think_removes_single_block() -> None:
    text = "<think>internal reasoning</think>Final answer."
    assert brief.strip_think(text) == "Final answer."


def test_strip_think_removes_multiple_blocks() -> None:
    text = "<think>one</think>Part A<think>two</think>Part B"
    assert brief.strip_think(text) == "Part APart B"


def test_strip_think_leaves_text_without_think_blocks_untouched() -> None:
    assert brief.strip_think("Just a plain draft.") == "Just a plain draft."


# --- generate (Ollama) ----------------------------------------------------------


class FakeOllamaSession:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, json: dict[str, Any], timeout: int) -> Any:
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeOllamaResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self._body


def test_generate_posts_expected_payload_and_returns_response_text() -> None:
    session = FakeOllamaSession(FakeOllamaResponse({"response": "the draft"}))
    result = brief.generate("a prompt", model="qwen3:8b", url="http://localhost:11434", session=session)
    assert result == "the draft"
    call = session.calls[0]
    assert call["url"] == "http://localhost:11434/api/generate"
    assert call["json"]["model"] == "qwen3:8b"
    assert call["json"]["prompt"] == "a prompt"
    assert call["json"]["stream"] is False
    assert call["json"]["options"]["temperature"] == 0.2


def test_generate_connection_refused_gives_plain_message_not_traceback() -> None:
    session = FakeOllamaSession(requests.exceptions.ConnectionError("refused"))
    with pytest.raises(brief.OllamaUnavailable) as excinfo:
        brief.generate("a prompt", model="qwen3:8b", url="http://localhost:11434", session=session)
    assert "ollama serve" in str(excinfo.value)


# --- validate_draft --------------------------------------------------------------


def test_validate_draft_passes_when_customer_and_tx_hash_present() -> None:
    case = {"customer_id": "CUST-2011", "tx_hash": "0xSYNTHETIC0101"}
    draft = "CUST-2011 sent funds, tx 0xSYNTHETIC0101, to a designated address."
    assert brief.validate_draft(draft, case) == []


def test_validate_draft_flags_missing_customer_id() -> None:
    case = {"customer_id": "CUST-2011", "tx_hash": "0xSYNTHETIC0101"}
    draft = "A customer sent funds, tx 0xSYNTHETIC0101."
    missing = brief.validate_draft(draft, case)
    assert "CUST-2011" in missing


def test_validate_draft_flags_missing_tx_hash() -> None:
    case = {"customer_id": "CUST-2011", "tx_hash": "0xSYNTHETIC0101"}
    draft = "CUST-2011 sent funds to a designated address."
    missing = brief.validate_draft(draft, case)
    assert "0xSYNTHETIC0101" in missing


# --- fetch_issue -----------------------------------------------------------------


class FakeGhRunner:
    def __init__(self, responses: dict[tuple, Any]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> Any:
        self.calls.append(args)
        key = tuple(args)
        for pattern, response in self.responses.items():
            if key[: len(pattern)] == pattern:
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"unexpected gh call: {args}")


def _stdout(text: str) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(stdout=text, returncode=0)


def test_fetch_issue_parses_gh_json_output() -> None:
    body = _render_issue_body(SAMPLE_HIT)
    payload = {"title": "[Sanctions hit] CUST-2011 ...", "body": body, "labels": [{"name": "sanctions-hit"}]}
    runner = FakeGhRunner({("gh", "issue", "view", "5"): _stdout(json.dumps(payload))})
    issue = brief.fetch_issue(5, runner=runner)
    assert issue["title"].startswith("[Sanctions hit]")
    assert issue["body"] == body
    call = runner.calls[0]
    assert call == ["gh", "issue", "view", "5", "--json", "title,body,labels"]


# --- save_draft / post_comment ---------------------------------------------------


def test_save_draft_writes_file(tmp_path: Path) -> None:
    path = brief.save_draft(7, "the draft text", out_dir=tmp_path)
    assert path.name == "issue-7.md"
    assert path.read_text() == "the draft text"


def test_post_comment_writes_body_file_and_calls_gh(tmp_path: Path) -> None:
    runner = FakeGhRunner({("gh", "issue", "comment"): _stdout("")})
    brief.post_comment(7, "the draft with footer", runner=runner, out_dir=tmp_path)
    call = runner.calls[0]
    assert call[:4] == ["gh", "issue", "comment", "7"]
    assert "--body-file" in call
    body_file = Path(call[call.index("--body-file") + 1])
    assert body_file.read_text() == "the draft with footer"


# --- main (CLI) -------------------------------------------------------------------


def _fetch_issue_stub(body: str) -> Any:
    def fetch(issue_number: int) -> dict[str, Any]:
        return {"title": "t", "body": body, "labels": []}

    return fetch


def _generate_stub(text: str) -> Any:
    def generate(prompt: str, *, model: str, url: str) -> str:
        return text

    return generate


class RecordingRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], **kwargs: Any) -> Any:
        self.calls.append(args)
        from types import SimpleNamespace

        return SimpleNamespace(stdout="", returncode=0)


def test_main_saves_draft_and_does_not_post_without_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    body = _render_issue_body(SAMPLE_HIT)
    comment_runner = RecordingRunner()
    called = {"input": False}

    def input_fn(_prompt: str) -> str:
        called["input"] = True
        return "approve"

    code = brief.main(
        ["--issue", "7"],
        fetch_issue_fn=_fetch_issue_stub(body),
        generate_fn=_generate_stub("CUST-2011 ... 0xSYNTHETIC0101 ..."),
        comment_runner=comment_runner,
        out_dir=tmp_path,
        input_fn=input_fn,
    )
    assert code == 0
    assert (tmp_path / "issue-7.md").exists()
    assert comment_runner.calls == []
    assert called["input"] is False


def test_main_posts_on_approve(tmp_path: Path) -> None:
    body = _render_issue_body(SAMPLE_HIT)
    comment_runner = RecordingRunner()

    code = brief.main(
        ["--issue", "7", "--post"],
        fetch_issue_fn=_fetch_issue_stub(body),
        generate_fn=_generate_stub("CUST-2011 ... 0xSYNTHETIC0101 ..."),
        comment_runner=comment_runner,
        out_dir=tmp_path,
        input_fn=lambda _prompt: "approve",
    )
    assert code == 0
    assert len(comment_runner.calls) == 1
    body_file = Path(comment_runner.calls[0][comment_runner.calls[0].index("--body-file") + 1])
    posted = body_file.read_text()
    assert "CUST-2011" in posted
    assert "qwen3:8b" in posted  # footer names the model
    assert "analyst" in posted.lower()


def test_main_does_not_post_on_non_approve_answer(tmp_path: Path) -> None:
    body = _render_issue_body(SAMPLE_HIT)
    comment_runner = RecordingRunner()

    code = brief.main(
        ["--issue", "7", "--post"],
        fetch_issue_fn=_fetch_issue_stub(body),
        generate_fn=_generate_stub("CUST-2011 ... 0xSYNTHETIC0101 ..."),
        comment_runner=comment_runner,
        out_dir=tmp_path,
        input_fn=lambda _prompt: "nope",
    )
    assert code == 0
    assert comment_runner.calls == []


def test_main_refuses_to_post_when_draft_missing_facts(tmp_path: Path) -> None:
    body = _render_issue_body(SAMPLE_HIT)
    comment_runner = RecordingRunner()
    called = {"input": False}

    def input_fn(_prompt: str) -> str:
        called["input"] = True
        return "approve"

    code = brief.main(
        ["--issue", "7", "--post"],
        fetch_issue_fn=_fetch_issue_stub(body),
        generate_fn=_generate_stub("A vague draft mentioning nothing specific."),
        comment_runner=comment_runner,
        out_dir=tmp_path,
        input_fn=input_fn,
    )
    assert code == 1
    assert comment_runner.calls == []
    assert called["input"] is False  # never even prompted for approval


def test_main_returns_error_on_malformed_issue_body(tmp_path: Path) -> None:
    comment_runner = RecordingRunner()
    generate_calls: list[str] = []

    def generate_fn(prompt: str, *, model: str, url: str) -> str:
        generate_calls.append(prompt)
        return "should not get here"

    code = brief.main(
        ["--issue", "7"],
        fetch_issue_fn=_fetch_issue_stub(MALFORMED_BODY),
        generate_fn=generate_fn,
        comment_runner=comment_runner,
        out_dir=tmp_path,
        input_fn=lambda _prompt: "approve",
    )
    assert code == 1
    assert generate_calls == []


def test_main_handles_ollama_connection_refused(tmp_path: Path) -> None:
    body = _render_issue_body(SAMPLE_HIT)

    def generate_fn(prompt: str, *, model: str, url: str) -> str:
        raise brief.OllamaUnavailable('Could not reach Ollama at http://x. Run "ollama serve" and try again.')

    code = brief.main(
        ["--issue", "7"],
        fetch_issue_fn=_fetch_issue_stub(body),
        generate_fn=generate_fn,
        comment_runner=RecordingRunner(),
        out_dir=tmp_path,
        input_fn=lambda _prompt: "approve",
    )
    assert code == 1
    assert not (tmp_path / "issue-7.md").exists()
