#!/usr/bin/env bash
# Round 4 of the pi customization work (details and the adoption rules: the roadmap document, section "round4").
# 8 batches in 2 stages, no input needed. Roughly 2.5-3 hours in all (a dev run takes about 0.7 minutes, a hard run about 1.2).
#   stage 1  about 1.7 h   dev tasks   r4-base  r4-v1  r4-agents  r4-ext  r4-base-b  r4-v1-b      (24 runs each)
#   stage 2  about 0.8 h   hard tasks  r4-hard-base  r4-hard-v1                                     (18 runs each)
# Run it from the arena folder (the folder that contains arena.py):
#       cd ~/pi/arena && bash ../round4.sh
# Look at the plan without running anything:   bash ../round4.sh --plan
# If it stops for any reason (closed terminal, Ctrl-C, reboot), run the same command again: finished runs are kept
# and skipped. One results file per stage is written when the stage ends (send it to me as soon as it appears, there is
# no need to wait for the whole round); at the very end a scan file r4-scan.txt is written too. The last lines say which
# files to send.
# A dead gateway stops the script after 3 runs in a row got no answer from the model (instead of wasting the hours);
# fix it, run the same command again, and the runs that got no answer are redone.
# Before a long run: switch the PC's sleep mode to "never" (a sleeping PC makes the run in progress time out).
set -u

PLAN=0
case "${1:-}" in
  "" ) ;;
  --plan ) PLAN=1 ;;
  * ) echo "usage: bash round4.sh [--plan]"; exit 2 ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f arena.py ] || { echo "arena.py not found here. cd into your arena folder first (cd ~/pi/arena)."; exit 1; }
for d in v1 variants/v1-agents variants/v1-ext; do
  [ -d "$HERE/$d" ] || { echo "$HERE/$d not found (extract the whole tgz next to the arena folder)."; exit 1; }
done
[ -f configs/setup.json ] || { echo "configs/setup.json not found: this arena folder has never run 'python3 arena.py day1'."; exit 1; }
[ -f r4_scan.py ] || { echo "r4_scan.py not found in the arena folder (extract the whole tgz)."; exit 1; }

# Knobs for testing this script itself against a mock gateway. Leave them unset for the real run.
REPS="${ROUND4_REPS:-3}"
DEV_TASKS="${ROUND4_DEV_TASKS:-dev}"
HARD_TASKS="${ROUND4_HARD_TASKS:-hard}"
HARD_TIMEOUT="${ROUND4_HARD_TIMEOUT:-1200}"
INFRA_PAUSE="${ROUND4_INFRA_PAUSE:-60}"
SKIP_PREP="${ROUND4_SKIP_PREP:-0}"

STAGE1="r4-base r4-v1 r4-agents r4-ext r4-base-b r4-v1-b"
STAGE2="r4-hard-base r4-hard-v1"
PACKED=()
CURRENT=""
FINISHED=0
T0=$(date +%s)

stamp() { date '+%F %T'; }
elapsed() { local s=$(( $(date +%s) - T0 )); printf '%dh%02dm' $((s / 3600)) $(( (s % 3600) / 60 )); }

# infra_rows <label>: how many runs of the batch have, as their newest attempt, one that got no answer from the model
# (gateway down, quota used up, the agent failing to start; the arena flags such runs I)
infra_rows() {
  python3 - "$1" <<'PY'
import glob, json, os, sys
best = {}
for p in glob.glob(os.path.join("runs", sys.argv[1], "*", "result.json")):
    try:
        r = json.load(open(p, encoding="utf-8"))
    except Exception:
        continue
    k = (r.get("agent"), r.get("task"), r.get("rep"))
    if k not in best or r.get("attempt", 1) > best[k].get("attempt", 1):
        best[k] = r
print(sum(1 for r in best.values() if r.get("infra_error")))
PY
}

# batch <label> <tasks> <timeout> <reps> [more suite options...]
# Gateway trouble is handled here, because a long unattended run must neither stop for one hiccup nor spend the
# remaining hours on a dead gateway: the arena stops the batch when 3 runs in a row get no answer from the model
# (exit code 4); runs that did get no answer are redone once the batch is complete (up to 2 more passes, 60 s apart),
# or at the next start of this script.
batch() {
  local label="$1" tasks="$2" timeout="$3" reps="$4"; shift 4
  echo
  echo "################ $(stamp)  (elapsed $(elapsed))  batch $label: tasks $tasks, $reps repetitions, timeout ${timeout}s"
  if [ "$PLAN" = 1 ]; then echo "  python3 arena.py suite --label $label --agents pi --tasks $tasks --reps $reps --timeout $timeout --skip tasks --no-pack --stop-after-infra 3 --redo-infra $*"; return 0; fi
  local pass=1 rc n redo
  while true; do
    redo=()
    n="$(infra_rows "$label")"
    if [ "${n:-0}" -gt 0 ]; then
      redo=(--redo run,report)
      echo "  $n run(s) of $label got no answer from the model earlier (gateway trouble): redoing them now"
    fi
    python3 arena.py suite --label "$label" --agents pi --tasks "$tasks" --reps "$reps" --timeout "$timeout" --skip tasks --no-pack \
        --stop-after-infra 3 --redo-infra ${redo[@]+"${redo[@]}"} "$@"
    rc=$?
    if [ $rc -ne 0 ]; then
      echo
      if grep -q 'failed (exit 4)' "suite/$label/state.json" 2>/dev/null; then
        echo "!! batch $label stopped: 3 runs in a row got no answer from the model. Is the gateway running and answering?"
        echo "   Check it, then run the same command again: the runs that got no answer are redone, nothing else is lost."
      else
        echo "!! batch $label stopped (exit code $rc). Run the same command again to continue."
      fi
      return $rc
    fi
    n="$(infra_rows "$label")"
    [ "${n:-0}" -eq 0 ] && return 0
    if [ $pass -ge 3 ]; then
      echo "  note: $n run(s) of $label still got no answer from the model after 3 passes; they stay flagged I in the results and are left out of the comparison."
      return 0
    fi
    pass=$((pass + 1))
    echo "  $n run(s) of $label got no answer from the model. Pausing ${INFRA_PAUSE}s, then redoing them (pass $pass of 3)."
    sleep "$INFRA_PAUSE"
  done
}

# pack_stage <title> <labels...>: one results file for the labels that have runs
pack_stage() {
  local title="$1"; shift
  local args=() l
  for l in "$@"; do [ -d "runs/$l" ] && args+=(--label "$l"); done
  [ ${#args[@]} -gt 0 ] || return 0
  echo
  echo "== packing the results of $title =="
  python3 arena.py pack "${args[@]}" || { echo "!! packing failed"; return 1; }
  local f; f="$(ls -t arena-results-*.txt 2>/dev/null | head -1)"
  [ -n "$f" ] && PACKED+=("$title: $f")
  return 0
}

# scan_runs: the read-only scan of the transcripts (refused commands in full, looks at the evaluation's files, protected-file diffs)
scan_runs() {
  [ -n "$(ls -d runs/r4-* 2>/dev/null | head -1)" ] || return 0
  echo
  echo "== scanning the runs (read-only) =="
  if python3 r4_scan.py --prefix r4- > r4-scan.txt 2> /tmp/round4-scan.err; then
    PACKED+=("the scan of all the transcripts: r4-scan.txt")
  else
    echo "!! the scan failed (not fatal; the results files are enough to start):"; tail -5 /tmp/round4-scan.err
  fi
}

on_exit() {
  if [ "$PLAN" = 1 ] || [ "$FINISHED" = 1 ]; then return; fi
  echo
  echo "Stopped before the end (elapsed $(elapsed)). Run the same command again to continue; nothing is lost."
  if [ -n "$CURRENT" ]; then pack_stage "the unfinished stage (so far)" $CURRENT; fi
  if [ ${#PACKED[@]} -gt 0 ]; then
    echo; echo "Results files written in this session (plain text, no keys inside):"
    printf '  %s\n' "${PACKED[@]}"
  fi
}
trap on_exit EXIT
trap 'exit 130' INT TERM

echo "round 4: $(stamp)   plan: stage 1 (dev tasks, 6 batches), stage 2 (hard tasks, 2 batches)"

if [ "$PLAN" != 1 ] && [ "$SKIP_PREP" != 1 ]; then
  echo
  echo "== step 0a: remove the plain hidden-test folders that an older release left behind (the sealed files stay) =="
  python3 arena.py seal --prune || { echo "!! seal --prune failed: nothing was run. Copy the output above to me."; FINISHED=1; exit 1; }

  echo
  echo "== step 0b: can the agents be kept out of the arena folder on this machine? (a second, no tokens) =="
  if python3 arena.py sandbox; then
    echo "sandbox: ON for every run of this round."
  else
    echo
    echo "WARNING: the sandbox cannot be used on this machine (the reason is above). The round goes on without it;"
    echo "         a run that looks at the evaluation's files and solves the task is not counted as a pass, and every such run is listed."
    echo "         Please tell me this line when you send the results."
  fi

  echo
  if [ -f .round4-taskcheck-ok ]; then
    echo "== step 0c: the task check passed earlier (delete the file .round4-taskcheck-ok to redo it) =="
  else
    echo "== step 0c: do all the tasks work on this machine? (about 3-5 minutes, no tokens) =="
    python3 arena.py tasks --check --tasks all > /tmp/round4-taskcheck.log 2>&1
    if [ $? -ne 0 ]; then
      cat /tmp/round4-taskcheck.log
      echo; echo "!! the task check failed: nothing was run. Copy the output above to me."
      FINISHED=1
      exit 1
    fi
    echo "tasks OK: $(grep -c '^check .*: OK' /tmp/round4-taskcheck.log) of $(grep -c '^check ' /tmp/round4-taskcheck.log)"
    if grep -q '^check .*SKIPPED' /tmp/round4-taskcheck.log; then
      echo "note: some tasks are skipped because a program they need is not installed; they will be left out of the batches:"
      grep '^check .*SKIPPED' /tmp/round4-taskcheck.log
    fi
    touch .round4-taskcheck-ok
  fi
fi

# ---- stage 1: dev tasks, thinking low, interleaved so that drift in the gateway's speed hits every configuration alike.
#      r4-base / r4-base-b: the blank baseline twice (noise); r4-v1 / r4-v1-b: the v1 pack twice;
#      r4-agents (AGENTS.md only) and r4-ext (v1 without AGENTS.md): which half of v1 it is, if v1 is not clean.
CURRENT="$STAGE1"
batch r4-base    "$DEV_TASKS" 900 "$REPS" --pi-thinking low || exit $?
batch r4-v1      "$DEV_TASKS" 900 "$REPS" --pi-thinking low --pi-config "$HERE/v1" || exit $?
batch r4-agents  "$DEV_TASKS" 900 "$REPS" --pi-thinking low --pi-config "$HERE/variants/v1-agents" || exit $?
batch r4-ext     "$DEV_TASKS" 900 "$REPS" --pi-thinking low --pi-config "$HERE/variants/v1-ext" || exit $?
batch r4-base-b  "$DEV_TASKS" 900 "$REPS" --pi-thinking low || exit $?
batch r4-v1-b    "$DEV_TASKS" 900 "$REPS" --pi-thinking low --pi-config "$HERE/v1" || exit $?
[ "$PLAN" = 1 ] || pack_stage "stage 1 (dev tasks)" $STAGE1
CURRENT=""

# ---- stage 2: hard tasks, the blank baseline and the whole v1 pack (a guard rail: is v1 worse on harder tasks?).
CURRENT="$STAGE2"
batch r4-hard-base "$HARD_TASKS" "$HARD_TIMEOUT" "$REPS" --pi-thinking low || exit $?
batch r4-hard-v1   "$HARD_TASKS" "$HARD_TIMEOUT" "$REPS" --pi-thinking low --pi-config "$HERE/v1" || exit $?
[ "$PLAN" = 1 ] || pack_stage "stage 2 (hard tasks)" $STAGE2
CURRENT=""

[ "$PLAN" = 1 ] || scan_runs

FINISHED=1
echo
echo "=================================================================================================="
if [ "$PLAN" = 1 ]; then
  echo "That was the plan only; nothing was run."
else
  echo "Round 4 finished at $(stamp) (elapsed $(elapsed))."
  echo "Send me these files (plain text, no keys inside):"
  printf '  %s\n' "${PACKED[@]}"
fi
