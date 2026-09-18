# CLAUDE.md — Sanctions Delta Watch

**Design: [A event-driven | B snapshot-diff] — set this after Stage 0 of INSTRUCTIONS.md**

## What this project is
A portfolio demo that continuously re-screens an exchange's known addresses when the
Chainalysis sanctions oracle adds or removes designations. It is an independent build on
public Chainalysis data, not Chainalysis software. All customer data is synthetic.

## Stack (zero cost)
- Python 3.12, `requests`, `pandas`, `pytest`. No web3 library: raw JSON-RPC only.
- GitHub Actions (scheduled watcher), GitHub Issues (cases), GitHub Pages (dashboard in `docs/`).
- Ollama on macOS for the local analyst copilot. Never call a hosted LLM with case data.

## Chainalysis oracle facts (source: go.chainalysis.com/chainalysis-oracle-docs.html)
- Oracle: `0x40C57923924B5c5c5455c48D93317139ADDaC8fb` on Ethereum, Polygon, BNB, Avalanche,
  Optimism, Arbitrum, Celo. **Base is different:** `0x3A91A31cB3dC49b4db9Ce721F50a9D076c8D739B`.
- `isSanctioned(address)` selector: `0xdf592f7d`. Call data = selector + address left-padded to 32 bytes.
- Event topics (keccak256 of signature):
  - `SanctionedAddressesAdded(address[])`   `0x2596d7dd6966c5673f9c06ddb0564c4f0e6d8d206ea075b83ad9ddd71a4fb927`
  - `SanctionedAddressesRemoved(address[])` `0x32aab684eee99db715515d1a9987a8fe33bb6341b0e35e60db7eab48a08f9a3a`
- Event `addrs` is NOT indexed. Decode from `data`: word 0 = offset, word at offset = length,
  then one 32-byte word per address (last 20 bytes).
- Doc test addresses: sanctioned `0x7F367cC41522cE07553e823bf3be79A889DEbe1B`,
  not sanctioned `0x7f268357A8c2552623316e2562D90e642bB538E5`.

## Rules
1. Write the failing test first, then the code. Mock all network calls in tests.
2. Always confirm a hit with a live `isSanctioned` call before opening a case.
3. Log access: free RPCs usually return 403 for `eth_getLogs`. Read logs through the Etherscan API
   first, fall back to RPC in 2,000-block chunks, and abort with a clear error after 5 consecutive
   refusals — never report zero events from a scan that did not run. Persist progress in `data/state.json`.
4. Compare addresses lowercase. Never log or commit API keys.
5. Idempotent: re-running the watcher on the same blocks must not open duplicate issues
   (dedupe on chain + address + customer_id).
6. The copilot drafts; a human approves before anything is posted.
7. Do not claim capabilities of paid Chainalysis products in code, comments or UI.
8. Keep functions small, typed, logged with `logging`, and handle RPC errors without crashing the run.

## Commands
- Tests: `pytest -q`
- Watch once: `python -m watcher.run --chains Ethereum --lookback 50000`
- Simulate a designation: `python -m watcher.run --simulate 0x7F367cC41522cE07553e823bf3be79A889DEbe1B`
- Copilot: `python -m copilot.brief --issue <number>`

## Layout
watcher/ (rpc.py, events.py, rescreen.py, cases.py, run.py) · copilot/ (brief.py) ·
data/ (ledger.csv, state.json, deltas/) · docs/ (index.html, cases.json) · tests/
