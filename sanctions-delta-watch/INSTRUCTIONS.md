# Sanctions Delta Watch — Complete Build Instructions

Everything from an empty folder to a rehearsed interview demo. No Chainalysis account, no paid
services, runs on your MacBook, published on your GitHub.

**What you are building:** when the public Chainalysis sanctions oracle gains a designation,
the system re-screens every address the platform already approved, confirms each hit live,
opens a case, and drafts the compliance brief with a local model for analyst approval.

Read this once end to end before starting. Then work stage by stage.

---

# Part 1 — Before you start

## 1.1 Where the project lives

Do not work in `~/Downloads`; it gets cleaned out. Pick a permanent home:

```bash
mkdir -p ~/projects
cd ~/projects
# unzip the starter repo here, so you end up with ~/projects/sanctions-delta-watch
```

If you already started in Downloads, move it. The virtual environment survives the move:

```bash
mv ~/Downloads/sanctions-delta-watch ~/projects/
```

## 1.2 Install the tools

| Tool | Command | Check |
|---|---|---|
| Homebrew | see brew.sh | `brew --version` |
| Python 3.12 | `brew install python@3.12` | `python3.12 --version` |
| Git | `brew install git` | `git --version` |
| GitHub CLI | `brew install gh` | `gh --version` |
| Node (for Claude Code) | `brew install node` | `node --version` |
| Claude Code | `npm install -g @anthropic-ai/claude-code` | `claude --version` |
| Ollama | `brew install ollama` | `ollama --version` |
| Model | `ollama pull qwen3:8b` | `ollama list` |

Then authenticate GitHub once: `gh auth login` (choose HTTPS, log in via browser).

## 1.3 Accounts

| Account | Needed for | Cost | Wait |
|---|---|---|---|
| GitHub | code, scheduler, cases, dashboard | free (public repo) | none |
| Etherscan | reading oracle event logs | free | instant |
| Chainalysis | nothing | — | not required |

**Get the Etherscan key now:** etherscan.io → sign up → API Keys → Add. You need it in Stage 0
because free JSON-RPC endpoints refuse log queries.

## 1.4 Set up the Python environment

```bash
cd ~/projects/sanctions-delta-watch
python3.12 -m venv .venv
source .venv/bin/activate          # run this every new terminal session
pip install -r requirements.txt
```

Save your key so you don't retype it:

```bash
echo 'export ETHERSCAN_API_KEY=your_key_here' >> ~/.zshrc
source ~/.zshrc
```

## 1.5 What the starter repo already contains

```
CLAUDE.md            project rules Claude Code reads automatically
INSTRUCTIONS.md      this file
README.md            public-facing description
requirements.txt     python deps
scripts/step0_verify_events.py   working verification script
data/ledger.csv      synthetic customers, with planted matches
data/state.json      watcher progress (starts empty)
prompts/mlro_brief.md   prompt for the local model
docs/index.html      placeholder dashboard, replaced in Stage 6
.github/workflows/   tests, hourly watcher, demo trigger
watcher/ copilot/ tests/    empty; Claude Code fills these
```

---

# Part 2 — Stage 0: prove the data source (1 hour)

This stage decides the whole design. Do not skip it.

## 2.1 Understand the two questions

1. **Can I read the oracle?** `isSanctioned(address)` over any public RPC. Cheap, always allowed.
2. **Can I watch it change?** Reading the contract's event logs. Expensive, usually blocked on
   free RPC endpoints, which return `403 Forbidden`.

A run that reports "0 events" after a wall of 403 warnings has scanned nothing. That is an access
failure, not a finding.

## 2.2 Run the checks

```bash
cd ~/projects/sanctions-delta-watch
source .venv/bin/activate

# A. Which public endpoints serve logs? ~30 seconds, no key
python scripts/step0_verify_events.py --probe

# B. The real scan through Etherscan
python scripts/step0_verify_events.py --provider etherscan --blocks 2000000
```

If any endpoint in the probe shows `ok` under `eth_getLogs`, you can skip the key entirely:

```bash
python scripts/step0_verify_events.py --provider rpc --rpc <that-url> --blocks 2000000
```

## 2.3 Read the result

The first two lines must be `True` then `False` for the doc's two test addresses. That proves
oracle access and is non-negotiable; everything else depends on it.

| Outcome | What it means | Which design |
|---|---|---|
| Events listed | The oracle announces list changes | **Design A: event-driven** |
| 0 events, scan actually ran | The oracle updates silently | **Design B: snapshot-diff** |
| 403s everywhere, no scan | Access problem, not a finding | Fix access, run again |

Confirm Design B before committing to it: open the oracle contract on Etherscan and check its
Events tab.

## 2.4 The two designs

**Design A — event-driven.** The watcher reads `SanctionedAddressesAdded` and
`SanctionedAddressesRemoved` logs since the last block it processed, and re-screens on each change.

**Design B — snapshot-diff.** The watcher calls `isSanctioned` on every address in the ledger,
compares with the previous run stored in `data/snapshot.json`, and treats any false → true change
as a designation. Slower and only covers addresses you know about, but it needs no log access at
all and is honest about that limitation.

**Only Stage 3 differs between them.** Everything else — cases, automation, copilot, dashboard,
the demo, the deck — is identical. Design B is also the better story for a customer with no
block-explorer budget, so do not treat it as the consolation prize.

Write your choice at the top of `CLAUDE.md` before moving on:
`Design: A (event-driven)` or `Design: B (snapshot-diff)`.

---

# Part 3 — Stage 1: publish the repo (30 minutes)

```bash
git init
git add .
git status                      # nothing secret should be listed
git commit -m "Starter scaffold and build instructions"
gh repo create sanctions-delta-watch --public --source . --push
```

Then on github.com, in your new repo:

1. **Settings → Actions → General → Workflow permissions** → select **Read and write permissions** → Save.
   Without this the watcher cannot open issues or commit results.
2. **Settings → Pages** → Source: Deploy from a branch → Branch `main`, folder `/docs` → Save.
3. **About (top right, gear icon)** → description and topics: `chainalysis`, `sanctions-screening`,
   `compliance`, `claude-code`, `ollama`.

**Done when:** the repo is public, the Pages URL loads the placeholder dashboard, and the `tests`
workflow has run (it will fail until Stage 2 adds tests; that is expected).

---

# Part 4 — Stages 2 to 7: build with Claude Code

Start Claude Code inside the project folder:

```bash
cd ~/projects/sanctions-delta-watch
claude
```

It reads `CLAUDE.md` automatically. For each stage: paste the prompt, let it work, then run the
verification command yourself. Commit after each stage:

```bash
git add . && git commit -m "Stage N: <what>" && git push
```

## Stage 2 — RPC client and chain config (1.5 h)

```
Read CLAUDE.md. Build watcher/rpc.py test-first.

call(url, method, params): POST JSON-RPC with timeout, 3 retries with backoff, and two
exception types: RpcBlocked for HTTP 401/403/405/429 or "method not allowed" style errors,
RpcError for everything else.

CHAINS: dict of chain name -> {oracle, rpc}. Oracle addresses and the Base exception are in
CLAUDE.md. Each rpc overridable by env RPC_<CHAIN_NAME_UPPER_WITH_UNDERSCORES>.

is_sanctioned(chain, address): encode selector + padded address, eth_call, return bool.
Raise on malformed addresses. Compare addresses lowercase everywhere.

Tests in tests/test_rpc.py with a fake session: encoding, true/false decoding, blocked vs
error classification, retry behaviour, env override, Base uses its own oracle address.
```

Verify: `pytest -q` passes, and:
```bash
python -c "from watcher.rpc import is_sanctioned; print(is_sanctioned('Ethereum','0x7F367cC41522cE07553e823bf3be79A889DEbe1B'))"
```
prints `True`.

## Stage 3 — Change detection (2 h)

**If you chose Design A:**
```
Build watcher/events.py test-first.

fetch_designation_events(chain, from_block, to_block) returning
[{chain, block, tx_hash, kind: added|removed, address}].
Primary source: Etherscan logs API (module=logs, action=getLogs, topic0 per event, paginated,
key from ETHERSCAN_API_KEY). Fallback: eth_getLogs in 2000-block chunks, aborting with a clear
error after 5 consecutive RpcBlocked failures rather than silently reporting zero.
Decode the non-indexed address[] from log data (see CLAUDE.md for the layout).
load_state/save_state for data/state.json: last processed block per chain; first run starts at
head minus --lookback.

Tests: decoding, pagination, topic mapping, the 5-failure abort, state round-trip.
Reuse the decoder and topic constants from scripts/step0_verify_events.py.
```

**If you chose Design B:**
```
Build watcher/snapshot.py test-first.

current_status(addresses, chain) -> {address: bool} using watcher.rpc.is_sanctioned,
5 threads maximum, with per-address error capture that never aborts the run.
diff(previous, current) -> [{chain, address, kind: added|removed, detected_at}] where
added = false/missing -> true, removed = true -> false.
load_snapshot/save_snapshot for data/snapshot.json, including a run timestamp.
First run establishes the baseline and emits nothing; say so in the log.

Tests: diff cases including first run, addresses that error out, threading determinism.
```

Verify (both designs): `pytest -q` passes, and a dry run prints either the events found or the
baseline message.

## Stage 4 — Re-screening engine (1.5 h)

```
Build watcher/rescreen.py test-first.

rescreen(changes, ledger_df, confirm_fn) -> list of hits.
For each 'added' change, match ledger rows on lowercase address. For each match, call
confirm_fn(chain, address) for a live isSanctioned confirmation, and keep only confirmed hits.

Hit fields: chain, address, customer_id, relationship, direction, asset, amount, tx_date,
tx_hash, detected_at (UTC ISO), confirmed_at, severity.
Severity: Critical for withdrawal_whitelist, or counterparty with amount > 0 within 90 days;
High for deposit_source; Medium otherwise.
'removed' changes produce delisting notices, not hits.

Tests: matching, case-insensitivity, confirmation filtering, each severity branch, delisting.
```

Verify: with the doc's sanctioned address as the change, you get hits for CUST-1002 (twice) and
CUST-1004, and nothing for CUST-1001, CUST-1003 or CUST-1005.

## Stage 5 — Cases, CLI and automation (2 h)

```
Build watcher/cases.py and watcher/run.py test-first.

cases.py: write each run's hits to data/deltas/<UTC timestamp>.json; merge into docs/cases.json
deduping on chain+address+customer_id; open one GitHub Issue per new case through the gh CLI
(subprocess, mockable): title "[Sanctions hit] <customer> <short address> on <chain>",
labels sanctions-hit and severity:<level>, body with a markdown table of the exposure plus
explorer links and the oracle confirmation timestamp. Create labels if missing.

run.py: argparse CLI with --chains (names or 'all'), --lookback, --simulate <address>,
--open-issues, --dry-run. --simulate injects a synthetic 'added' change on Ethereum but still
performs the live confirmation. Never crash on one chain failing: log, continue, print a summary,
exit 0 unless every chain failed.

Tests: dedupe across runs, issue body rendering, CLI parsing, simulate path with mocks.
```

Verify locally, then push and run **Actions → simulate-designation → Run workflow**:
```bash
python -m watcher.run --simulate 0x7F367cC41522cE07553e823bf3be79A889DEbe1B --dry-run
python -m watcher.run --simulate 0x7F367cC41522cE07553e823bf3be79A889DEbe1B --open-issues
```
Three issues appear. Running it again opens none.

## Stage 6 — Local Ollama copilot (1.5 h)

```bash
ollama serve      # separate terminal, if not already running
```

```
Build copilot/brief.py test-first.

CLI: python -m copilot.brief --issue N [--model qwen3:8b] [--post]
Fetch the issue with gh issue view --json title,body,labels; parse the exposure table back into
JSON; fill prompts/mlro_brief.md; POST to OLLAMA_URL (default http://localhost:11434) /api/generate
with stream false and temperature 0.2; strip any <think> blocks.
Save to copilot/out/issue-N.md and print it.
Validate the draft mentions every tx hash from the case; if not, warn and refuse to post.
With --post, require the user to type 'approve' interactively, then gh issue comment with a
footer naming the model and stating an analyst reviewed it.

Tests mock both gh and Ollama. No network calls in tests.
```

Verify: a draft appears in under a minute, and after you type `approve` the comment shows on the
issue.

## Stage 7 — Dashboard and polish (2 h)

```
1. Replace docs/index.html with a single-file dashboard, no build step and no external scripts,
reading docs/cases.json: headline counts (open cases, customers affected, chains watched), a
timeline of detected changes, a filterable table linking to each GitHub issue and block explorer,
and a banner stating that customers are synthetic while oracle data is live. Responsive,
accessible, light and dark mode.

2. Add ruff config plus a lint step in tests.yml, type hints throughout, structured logging, and
a Makefile with setup, test, watch, simulate, brief targets.

3. Rewrite README.md: what it does, architecture diagram, quick start, demo steps, the design
chosen in Stage 0 and why, limitations (sanctions designations only; Chainalysis does not
guarantee timeliness; Design B only covers known addresses), and a disclaimer that this is an
independent project using public Chainalysis data.
```

Verify: `python -m http.server -d docs 8000` shows your cases locally, the Pages URL shows them
publicly, and a fresh `git clone` plus `make setup test` works.

---

# Part 5 — Stage 8: rehearse (1 hour)

## 5.1 Demo script, 6 minutes

| Time | Show | Say |
|---|---|---|
| 0:00 | Dashboard | "Platforms screen at onboarding. Designations keep changing after that." |
| 0:45 | Actions → simulate-designation | "A designation lands in the Chainalysis oracle." |
| 1:30 | Issues tab | "Three cases. Each confirmed live against the oracle before opening." |
| 2:30 | Terminal: copilot with --post | "Brief drafted locally, no customer data leaves the laptop. I approve it." |
| 4:00 | Dashboard refresh, git log | "Audit trail: what we knew, and when." |
| 5:00 | Limits slide | "Sanctions only. Indirect exposure and scams need Address Screening and KYT." |

## 5.2 Backups, in order

1. Live: the deployed Actions run.
2. If Actions is slow: run `python -m watcher.run --simulate ... --open-issues` locally.
3. If the network fails: a screen recording, linked in the README.
4. Last resort: the dashboard is static and loads from cache.

## 5.3 Pre-interview checklist

- [ ] Repo public, README accurate, tests badge green
- [ ] Pages dashboard loads, showing at least one open and one closed case
- [ ] `git log -p | grep -i "api_key\|secret"` returns nothing
- [ ] Recording made and linked
- [ ] Repo pinned on your GitHub profile, link in your CV and LinkedIn Featured
- [ ] Deck placeholders filled: date, GitHub username, email, value baselines

## 5.4 The deck

The pitch deck covering the use case and the customer conversation is separate:
https://claude.ai/artifact/FebDBnWq4GeWRCRaC6A3J1

Update the architecture, demo and closing slides with your real URLs, and adjust the
architecture slide if you built Design B.

---

# Part 6 — Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `403 Forbidden` on log calls | Free RPC blocks `eth_getLogs` | Use `--provider etherscan`, or an endpoint `--probe` marks ok |
| "0 events" after many warnings | Nothing was scanned | Same as above; re-run before concluding anything |
| `command not found: python3.12` | Homebrew Python not linked | `brew link python@3.12` or use `python3` |
| `gh: not authenticated` | CLI not logged in | `gh auth login` |
| Workflow can't open issues | Actions permissions | Settings → Actions → Read and write permissions |
| Workflow can't push | Same as above | Same fix; re-run the job |
| Pages shows 404 | Wrong source folder | Settings → Pages → branch `main`, folder `/docs` |
| Ollama connection refused | Server not running | `ollama serve` in another terminal |
| Ollama very slow | Model too large | `ollama pull qwen3:8b` and pass `--model qwen3:8b` |
| Scheduled runs stop | GitHub disables schedules in inactive repos | Push a commit, or re-enable in Actions |
| Rate-limited by Etherscan | Free tier limits | Add a short sleep between calls; cache results |

---

# Part 7 — Ground rules

These hold for the whole build and for anything you say about it:

1. Customer data is synthetic. Never load real customer data into this repo.
2. No case opens without a live `isSanctioned` confirmation.
3. The model drafts; a human approves. The analyst owns the output.
4. Secrets never enter the repo. Workflows use the built-in `GITHUB_TOKEN` only.
5. Describe Chainalysis paid products only at the level Chainalysis describes them publicly.
6. Always say which parts are live and which are simulated, in the README, the deck and the room.
7. This is an independent project built on public Chainalysis data. It is not Chainalysis software,
   and it is not affiliated with or endorsed by Chainalysis.

---

# Appendix — Time budget

| Stage | Hours | Output |
|---|---|---|
| 0 Prove the data source | 1.0 | Design A or B chosen |
| 1 Publish the repo | 0.5 | Public repo, Actions, Pages |
| 2 RPC client | 1.5 | Live oracle checks, tested |
| 3 Change detection | 2.0 | Events or snapshot diff |
| 4 Re-screening | 1.5 | Confirmed hits with severity |
| 5 Cases and automation | 2.0 | Issues, CLI, scheduled watcher |
| 6 Ollama copilot | 1.5 | Local brief with approval |
| 7 Dashboard and polish | 2.0 | Pages dashboard, README, lint |
| 8 Rehearse | 1.0 | Timed run, recording, checklist |
| **Total** | **13.0** | Portfolio project and demo |
