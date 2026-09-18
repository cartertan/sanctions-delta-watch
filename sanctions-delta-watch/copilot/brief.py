"""Local analyst copilot: draft an MLRO brief for a sanctions-hit issue.

Runs entirely against a local Ollama instance -- it must never call a hosted
model, since case data (even synthetic) shouldn't leave the analyst's
machine. The model drafts; a human approves before anything is posted back
to the issue.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

import requests

logger = logging.getLogger(__name__)

PROMPT_PATH = Path("prompts/mlro_brief.md")
OUT_DIR = Path("copilot/out")
DEFAULT_MODEL = "qwen3:8b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"

Runner = Callable[..., Any]

_FIELD_MAP: dict[str, str] = {
    "customer": "customer_id",
    "chain": "chain",
    "address": "address",
    "relationship": "relationship",
    "direction": "direction",
    "asset": "asset",
    "amount": "amount",
    "tx date": "tx_date",
    "tx hash": "tx_hash",
    "severity": "severity",
}

_REQUIRED_CASE_FIELDS = [
    "customer_id",
    "chain",
    "address",
    "relationship",
    "direction",
    "asset",
    "amount",
    "tx_date",
    "tx_hash",
    "event_block",
    "event_tx",
    "severity",
    "confirmed_at",
]

_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$", re.MULTILINE)
_EVENT_RE = re.compile(r"Designation event:\s*block\s*(\d+),\s*tx\s*(\S+)")
_CONFIRMED_RE = re.compile(r"Live oracle confirmation:\s*(\S+)")
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class OllamaUnavailable(RuntimeError):
    """Ollama isn't reachable at the configured URL."""


# --- parsing the case back out of the issue body --------------------------------


def parse_case_from_body(body: str) -> dict[str, Any]:
    """Parse the exposure table (and the event/confirmation lines) that
    watcher.cases renders into an issue body back into a case dict."""
    parsed: dict[str, Any] = {}
    for field, value in _ROW_RE.findall(body):
        if field.strip("- ") == "" or field.strip().lower() == "field":
            continue
        key = _FIELD_MAP.get(field.strip().lower())
        if key:
            parsed[key] = value.strip()

    event_match = _EVENT_RE.search(body)
    if event_match:
        parsed["event_block"] = int(event_match.group(1))
        parsed["event_tx"] = event_match.group(2)

    confirmed_match = _CONFIRMED_RE.search(body)
    if confirmed_match:
        parsed["confirmed_at"] = confirmed_match.group(1)

    missing = [f for f in _REQUIRED_CASE_FIELDS if f not in parsed]
    if missing:
        raise ValueError(
            f"issue body is not a recognised sanctions-hit case (missing: {', '.join(missing)})"
        )

    try:
        parsed["amount"] = float(parsed["amount"])
    except (TypeError, ValueError):
        pass

    return parsed


def render_prompt(case: dict[str, Any], *, prompt_path: Path = PROMPT_PATH) -> str:
    template = prompt_path.read_text()
    case_json = json.dumps(case, indent=2, default=str)
    return template.replace("{case_json}", case_json)


def strip_think(text: str) -> str:
    return _THINK_RE.sub("", text).strip()


def validate_draft(draft: str, case: dict[str, Any]) -> list[str]:
    """Facts from the case that the draft fails to mention."""
    required = [str(case["customer_id"]), str(case["tx_hash"])]
    return [fact for fact in required if fact not in draft]


# --- Ollama ------------------------------------------------------------------------


def generate(
    prompt: str,
    *,
    model: str,
    url: str,
    session: Any = None,
    timeout: int = 180,
) -> str:
    sess = session if session is not None else requests
    payload = {"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.2}}
    try:
        response = sess.post(f"{url}/api/generate", json=payload, timeout=timeout)
    except requests.exceptions.ConnectionError as exc:
        raise OllamaUnavailable(
            f'Could not reach Ollama at {url}. Run "ollama serve" and try again.'
        ) from exc
    response.raise_for_status()
    return response.json()["response"]


# --- gh ------------------------------------------------------------------------------


def fetch_issue(issue_number: int, *, runner: Runner = subprocess.run) -> dict[str, Any]:
    result = runner(
        ["gh", "issue", "view", str(issue_number), "--json", "title,body,labels"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def save_draft(issue_number: int, draft: str, *, out_dir: Path = OUT_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"issue-{issue_number}.md"
    path.write_text(draft)
    return path


def post_comment(
    issue_number: int,
    body: str,
    *,
    runner: Runner = subprocess.run,
    out_dir: Path = OUT_DIR,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    body_file = out_dir / f"issue-{issue_number}.posted.md"
    body_file.write_text(body)
    runner(
        ["gh", "issue", "comment", str(issue_number), "--body-file", str(body_file)],
        capture_output=True,
        text=True,
        check=True,
    )


def _footer(model: str) -> str:
    return (
        f"\n\n---\n*Drafted locally by Ollama ({model}); reviewed and approved "
        "by an analyst before posting.*"
    )


# --- CLI ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Draft an MLRO brief for a sanctions-hit issue")
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--post", action="store_true")
    parser.add_argument("--url", default=None)
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    fetch_issue_fn: Callable[[int], dict[str, Any]] = fetch_issue,
    generate_fn: Callable[..., str] = generate,
    comment_runner: Runner = subprocess.run,
    prompt_path: Path = PROMPT_PATH,
    out_dir: Path = OUT_DIR,
    input_fn: Callable[[str], str] = input,
) -> int:
    args = parse_args(argv)
    url = args.url or os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL)

    try:
        issue = fetch_issue_fn(args.issue)
    except subprocess.CalledProcessError as exc:
        print(f"could not fetch issue #{args.issue}: {exc}")
        return 1

    try:
        case = parse_case_from_body(issue.get("body", ""))
    except ValueError as exc:
        print(str(exc))
        return 1

    prompt = render_prompt(case, prompt_path=prompt_path)

    try:
        raw = generate_fn(prompt, model=args.model, url=url)
    except OllamaUnavailable as exc:
        print(str(exc))
        return 1

    draft = strip_think(raw)
    missing = validate_draft(draft, case)

    draft_path = save_draft(args.issue, draft, out_dir=out_dir)
    logger.info("saved draft to %s", draft_path)
    print(draft)

    if missing:
        logger.warning("draft is missing required facts: %s", ", ".join(missing))
        print(f"WARNING: draft is missing required facts: {', '.join(missing)}")

    if not args.post:
        return 0

    if missing:
        print("Refusing to post: draft is missing required facts.")
        return 1

    answer = input_fn("Type approve to post this comment: ")
    if answer.strip() != "approve":
        print("Not posting.")
        return 0

    post_comment(args.issue, draft + _footer(args.model), runner=comment_runner, out_dir=out_dir)
    print(f"posted comment on issue #{args.issue}")
    return 0


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
