#!/usr/bin/env bash
# Start / stop / check the mock gateway (no real model, $0). Works from any directory.
#   dev/mock.sh start [port] [script.json]   start in background (default port 18081)
#   dev/mock.sh stop  [port]
#   dev/mock.sh health [port]
#   dev/mock.sh reset [port]                  clear the simulated cache + request log
# Env:  MOCK_MODULE  python file to run (default: dev/mock_gw.py; lesson 8 uses labs/lesson8/mock/mock_lab8.py)
#       MOCK_RUN_DIR where pid/log/dump files go (default: ${TMPDIR:-/tmp}/pinew-mock)
#       MOCK_DUMP    full request bodies (JSONL) -> default $MOCK_RUN_DIR/dump-<port>.jsonl
# The script file is re-read on every request (see the docstring in dev/mock_gw.py).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="${MOCK_RUN_DIR:-${TMPDIR:-/tmp}/pinew-mock}"; mkdir -p "$RUN"
cmd="${1:-health}"; PORT="${2:-18081}"
case "$cmd" in
  start)
    SCRIPT="${3:-}"
    [ -f "$RUN/mock-$PORT.pid" ] && kill "$(cat "$RUN/mock-$PORT.pid")" 2>/dev/null; sleep 0.3
    export MOCK_PORT="$PORT" MOCK_LOG="${MOCK_LOG:-$RUN/mock-$PORT.jsonl}" MOCK_DUMP="${MOCK_DUMP:-$RUN/dump-$PORT.jsonl}"
    [ -n "$SCRIPT" ] && export MOCK_SCRIPT="$SCRIPT"
    : > "$MOCK_LOG"; : > "$MOCK_DUMP"
    nohup python3 "${MOCK_MODULE:-$HERE/mock_gw.py}" > "$RUN/mock-$PORT.out" 2>&1 &
    echo $! > "$RUN/mock-$PORT.pid"
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do curl -s "http://127.0.0.1:$PORT/__health" && break; sleep 0.3; done; echo
    echo "dump: $MOCK_DUMP"
    ;;
  stop)   [ -f "$RUN/mock-$PORT.pid" ] && kill "$(cat "$RUN/mock-$PORT.pid")" 2>/dev/null && echo stopped || echo "not running" ;;
  reset)  curl -s -X POST "http://127.0.0.1:$PORT/__reset"; echo ;;
  health) curl -s -m 2 "http://127.0.0.1:$PORT/__health" || { echo "mock not answering on $PORT"; exit 1; }; echo ;;
  *) sed -n 2,10p "$0"; exit 2 ;;
esac
