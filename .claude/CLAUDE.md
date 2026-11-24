# Orientation for a Claude Code session working in this repo

This repo is `pinew`: a pi (pi.dev) customization package that is being iterated from the measured "v1" into a daily-driver coding agent.

Read, in this order, before changing anything:
1. `docs/HANDOFF.md`  (who/what/rules/pitfalls/first tasks — start here)
2. `docs/STATE.md`    (where the last session stopped; update it before you finish)
3. `git log -10`
Look things up in `docs/FACTS.md` with grep; never read `archive/` end to end (it is evidence; grep it).

Do NOT add a `CLAUDE.md` or `AGENTS.md` at the repo root: pi loads those from its working directory, which would silently change what pi sends to the model when it is run here. This file lives in `.claude/` on purpose (pi does not read that folder; tested on pi 1.5.0).

## Standing rules (from the user; full text in docs/OPENING_PROMPT.md and docs/HANDOFF.md sections 0, 3, 4)

- The user is gnk48lb (Taipei, UTC+8). Talk to him in Chinese, briefly: conclusion first, then at most one next step. Files in `docs/` are Chinese; code, comments, commit messages and CHANGELOG are English.
- "开始" = first session: follow HANDOFF section 8. "继续" = read the "下一步" list in `docs/STATE.md` and carry on; do not re-teach or recap the background.
- Advance proactively through `docs/ROADMAP.md` in order (v1.1, v1.2, ... v2.0, v3+). Never wait for him to "use it for N weeks". His friction reports go to `docs/FRICTION.md` and jump the queue. Stop only for a decision only he can make, or a command he must run in his WSL (one command at a time; say cost, time and whether it changes his machine first).
- You do all edits, commits and pushes; he never edits files or types git. Push straight to `main` when you can (`git push origin HEAD:main`; solo repo, no PRs); if you cannot push (free-plan chat without a cloud session), use attachment mode (HANDOFF section 8): at the end of each segment hand him a `pinew-YYYYMMDD-<sha>.tar.gz` of the whole repo. The cloud-session GitHub proxy refuses tag pushes and branch deletions (documented; HANDOFF section 5.17): mark a release with a commit titled `Release vX.Y.Z`, and do not create throwaway branches.
- Privacy: never write his personal email into any file, commit or outgoing request, and never print keys from `models.json` (use `tools/collect-state.py`, which redacts).
- Every number carries its model and pi version (plus n, real gateway or mock). Keep the cache prefix append-only. Real-token experiments run only on his gateway.
- Update `docs/STATE.md` and push before each work segment ends, and tell him what he will feel with this version.
