# Changelog

## [1.0.0] - 2026-11-23

Baseline. The files in `extensions/`, `skills/`, `prompts/` and `config/` are byte-identical to the "v1" pack that was measured in rounds 2-4 and lessons 0-8 (older documents call it "v1.2"; `docs/baseline/v1-SHA256SUMS.txt` has the hashes, `python3 tools/verify-baseline.py` checks them). Only the packaging is new:

- `package.json` with a pi manifest, so the pack can be installed with `pi install <path-or-git>`; `config/` holds the three files a package cannot ship (`AGENTS.md`, `settings.json`, `presets.json`) and still needs an installer.
- Tooling around it: `dev/` (mock gateway, RPC command lister, `seed-check.sh`), `tools/` (read-only `collect-state.py`, `verify-baseline.py`, `real-gateway/` request inspectors, the legacy v1 installer), `labs/` (lessons 1-8), `bench/` (arena v0.3), `archive/` (evidence), `docs/` (handoff, facts, roadmap).

prefix: +0 tokens/request compared with the measured v1 (same files; measured with MiniMax-M3.1-Flash on pi 1.5.0 — see `docs/FACTS.md` section C for what each part costs).

Known issue: installing this package on a machine that already has v1 copied into `~/.pi/agent/` (what `tools/legacy/install_v1.py` does) makes pi exit at startup with `Flag "--preset" conflicts`. Recover with `pi -ne`. The 1.1.0 installer is meant to remove the copies first.
