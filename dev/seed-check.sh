#!/usr/bin/env bash
# Seed for tools/check.py (v1.1): the checks that were run by hand on 2026-11-22 against pi 1.5.0 + the mock gateway,
# as one script. It touches nothing outside a temp dir, needs no network and costs $0.
#
#   . dev/env.sh            # (copy of dev/env.sh.example) puts pi 1.5.0 on PATH
#   bash dev/seed-check.sh  # prints PASS / FAIL lines; exit code = number of FAILs
#
# What it checks: the package installs from a local path; /preset, 5 prompt templates and 2 skills load (and nothing from
# labs/ archive/ experimental/ bench/); the safety gate refuses `rm -rf old` in print mode and the model is told why;
# `--preset explore` does nothing without presets.json and works with it; `--preset deep --thinking low` still sends high
# (the preset beats the flag; a documented pitfall, FACTS G7 — flip that check when v1.2 fixes it); copied-v1 + package
# (double install) reproduces the `--preset` conflict and `pi -ne` still starts; baseline files are byte-identical to v1.0.0.
# Gotchas it encodes (all learned the hard way): the isolated agent dir needs settings.json with defaultProvider/defaultModel,
# otherwise pi picks a built-in provider and the request never reaches the mock (error: `Unexpected token 'r', "request bl"...`);
# print/json mode needs `< /dev/null`; the gate cannot ask anyone in print mode, so it refuses.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; REPO="$(cd "$HERE/.." && pwd)"
PI="${PI_BIN:-pi}"; PORT="${MOCK_PORT:-18081}"
command -v "$PI" >/dev/null 2>&1 || { echo "pi not found (source dev/env.sh first)"; exit 99; }
W="$(mktemp -d "${TMPDIR:-/tmp}/pinew-seed-XXXXXX")"; mkdir -p "$W/agent" "$W/cwd" "$W/run"
trap 'bash "$HERE/mock.sh" stop "$PORT" >/dev/null 2>&1; rm -rf "$W"' EXIT
export PI_CODING_AGENT_DIR="$W/agent" MOCK_RUN_DIR="$W/run"
sed "s#http://127.0.0.1:18081#http://127.0.0.1:$PORT#" "$HERE/models.mock.json" > "$W/agent/models.json"
echo '{"defaultProvider":"magpie","defaultModel":"MiniMax-M3.1-Flash","defaultThinkingLevel":"low"}' > "$W/agent/settings.json"
FAILS=0; pass() { echo "PASS  $*"; }; fail() { echo "FAIL  $*"; FAILS=$((FAILS+1)); }
JSON="--mode json --no-session --thinking low"
mock() { bash "$HERE/mock.sh" start "$PORT" "$1" >/dev/null 2>&1; }
echo '{"steps":[],"final":"OK"}' > "$W/empty.json"
echo '{"steps":[{"tool":"bash","arguments":{"command":"rm -rf old"}}],"final":"done"}' > "$W/rm.json"
echo "pi $("$PI" --version 2>&1 | tail -1) · repo $(git -C "$REPO" describe --tags --always 2>/dev/null || echo 'no git')"

# 1. package install + what loads
( cd "$W/cwd" && "$PI" install "$REPO" < /dev/null > "$W/install.out" 2>&1 )
( cd "$W/cwd" && PI_BIN="$PI" python3 "$HERE/rpc_commands.py" ) > "$W/cmds.txt" 2>&1
grep -q " /preset " "$W/cmds.txt" && pass "/preset (extension) loaded" || fail "/preset missing"
[ "$(grep -c '^prompt' "$W/cmds.txt")" = 5 ] && pass "5 prompt templates" || fail "prompt templates: $(grep -c '^prompt' "$W/cmds.txt")"
[ "$(grep -c '^skill' "$W/cmds.txt")" = 2 ] && pass "2 skills" || fail "skills: $(grep -c '^skill' "$W/cmds.txt")"
grep -qE "labs/|archive/|experimental/|bench/" "$W/cmds.txt" && fail "package picked up labs/archive/experimental/bench" || pass "nothing from labs/ archive/ experimental/ bench/"

# 2. a plain request reaches the mock
mock "$W/empty.json"
( cd "$W/cwd" && "$PI" -p "hi" $JSON < /dev/null > "$W/plain.jsonl" 2> "$W/plain.err" )
[ -s "$W/run/dump-$PORT.jsonl" ] && pass "request reached the mock gateway" || fail "mock saw no request (see the gotchas in this file's header)"

# 3. safety gate in print mode
mkdir -p "$W/cwd/old" && echo keep > "$W/cwd/old/f.txt"; mock "$W/rm.json"
( cd "$W/cwd" && "$PI" -p "delete the old dir" $JSON --tools read,bash,edit,write < /dev/null > "$W/sg.jsonl" 2> "$W/sg.err" )
[ -f "$W/cwd/old/f.txt" ] && pass "safety-gate: rm -rf old blocked, directory kept" || fail "old/ was deleted"
grep -q "Blocked by safety-gate (rm-recursive" "$W/sg.jsonl" && pass "the model got the block reason" || fail "no block reason in the tool result"

# 4. --preset explore
req() { python3 - "$W/run/dump-$PORT.jsonl" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
b = rows[-1].get("body", rows[-1])
print(sorted(t.get("name") for t in (b.get("tools") or [])), "READ-ONLY" in json.dumps(b.get("system"), ensure_ascii=False))
PY
}
mock "$W/empty.json"; ( cd "$W/cwd" && "$PI" -p "hi" --preset explore $JSON < /dev/null > /dev/null 2>&1 )
[ "$(req)" = "['bash', 'edit', 'read', 'write'] False" ] && pass "--preset without presets.json is a silent no-op (known pitfall)" || fail "unexpected without presets.json: $(req)"
cp "$REPO/config/presets.json" "$W/agent/presets.json"
mock "$W/empty.json"; ( cd "$W/cwd" && "$PI" -p "hi" --preset explore $JSON < /dev/null > /dev/null 2>&1 )
[ "$(req)" = "['find', 'grep', 'ls', 'read'] True" ] && pass "--preset explore: 4 read-only tools + READ-ONLY instruction" || fail "explore not applied: $(req)"

# 4b. --preset vs an explicit --thinking (FACTS G7). Known pitfall: the preset is applied in session_start, after pi has applied
#     --thinking, so the preset wins although preset.ts's header says CLI flags win. Control first, then the pitfall.
budget() { python3 - "$W/run/dump-$PORT.jsonl" <<'PY'
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
b = rows[-1].get("body", rows[-1])
print((b.get("thinking") or {}).get("budget_tokens"))
PY
}
mock "$W/empty.json"; ( cd "$W/cwd" && "$PI" -p "hi" --mode json --no-session --thinking low < /dev/null > /dev/null 2>&1 )
[ "$(budget)" = 2048 ] && pass "no preset: --thinking low sends budget_tokens 2048" || fail "control failed: budget=$(budget)"
mock "$W/empty.json"; ( cd "$W/cwd" && "$PI" -p "hi" --mode json --no-session --preset deep --thinking low < /dev/null > /dev/null 2>&1 )
[ "$(budget)" = 16384 ] && pass "--preset deep --thinking low sends 16384 (high): the preset beats the flag (known pitfall, FACTS G7)" || fail "preset/--thinking behaviour changed: budget=$(budget) (if v1.2 fixed it, flip this check to expect 2048)"

# 5. double install
mkdir -p "$W/agent/extensions" && cp "$REPO"/extensions/*.ts "$W/agent/extensions/"
( cd "$W/cwd" && "$PI" -p "hi" $JSON < /dev/null > "$W/dbl.out" 2> "$W/dbl.err" ); RC=$?
{ [ "$RC" = 1 ] && grep -qh 'Flag "--preset" conflicts' "$W/dbl.out" "$W/dbl.err"; } && pass "copied v1 + package: pi exits 1 with the --preset conflict (known pitfall)" || fail "double install: exit=$RC"
( cd "$W/cwd" && "$PI" -ne -p "hi" $JSON < /dev/null > /dev/null 2>&1 ) && pass "pi -ne still starts" || fail "pi -ne failed"

# 6. baseline
python3 "$REPO/tools/verify-baseline.py" > "$W/base.out" 2>&1 && pass "baseline files byte-identical to v1.0.0" || { fail "baseline differs (fine if declared in CHANGELOG):"; grep -E "^(CHANGED|MISSING)" "$W/base.out"; }

echo "FAILS=$FAILS"; exit "$FAILS"
