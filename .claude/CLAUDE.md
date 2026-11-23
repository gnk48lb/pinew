# Orientation for a Claude Code session working in this repo

This repo is `pinew`: a pi (pi.dev) customization package that is being iterated from the measured "v1" into a daily-driver coding agent.

Read, in this order, before changing anything:
1. `docs/HANDOFF.md`  (who/what/rules/pitfalls/first tasks — start here)
2. `docs/STATE.md`    (where the last session stopped; update it before you finish)
3. `git log -10`
Look things up in `docs/FACTS.md` with grep; never read `archive/` end to end (it is evidence; grep it).

Do NOT add a `CLAUDE.md` or `AGENTS.md` at the repo root: pi loads those from its working directory, which would silently change what pi sends to the model when it is run here. This file lives in `.claude/` on purpose (pi does not read that folder; tested on pi 1.5.0).
