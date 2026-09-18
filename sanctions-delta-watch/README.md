# Sanctions Delta Watch

Continuous sanctions re-screening for a crypto platform, built on the public
[Chainalysis sanctions oracle](https://go.chainalysis.com/chainalysis-oracle-docs.html).

When the oracle adds a designation, the watcher re-checks every address the platform already
knows (withdrawal whitelists, counterparties, deposit sources), confirms each hit live,
opens a case as a GitHub Issue, and a local Ollama copilot drafts the compliance brief for
analyst approval.

- **Cost:** zero. Public RPCs, GitHub Actions (public repo), GitHub Pages, local Ollama.
- **Data:** oracle data is live; customers in `data/ledger.csv` are synthetic.
- **Disclaimer:** independent project using public Chainalysis data. Not Chainalysis software.

## Status
Starter scaffold. New here? Follow [START_HERE.md](START_HERE.md) one command at a time.
The full stage-by-stage plan is in [INSTRUCTIONS.md](INSTRUCTIONS.md).
Project rules for Claude Code live in [CLAUDE.md](CLAUDE.md).

## Quick start
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python scripts/step0_verify_events.py --blocks 500000
```

## Limitations
- Covers sanctions designations only.
- Chainalysis states it cannot guarantee the accuracy or timeliness of oracle data.
  Treat this as one control within a wider compliance programme.
