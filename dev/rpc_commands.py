#!/usr/bin/env python3
"""List what pi has actually loaded: extension commands, prompt templates, skills.

Starts `pi --mode rpc --no-session`, sends one `get_commands` request, prints a table, exits.
No model request is made (nothing is sent to any gateway), so it is free and works offline.

usage:  python3 dev/rpc_commands.py [--json] [-- extra pi args, e.g. --no-extensions -e ./x.ts]
env:    PI_BIN (default: pi), PI_CODING_AGENT_DIR (which agent dir to inspect)

Use it after every install/migration step: it shows each command's `source` (extension / prompt / skill)
and `sourceInfo.path` (where it was loaded from), so a duplicate load or a missing file is visible at once.
"""
import json, os, signal, subprocess, sys, time

def main():
    argv = sys.argv[1:]
    as_json = False
    if argv and argv[0] == "--json":
        as_json, argv = True, argv[1:]
    if argv and argv[0] == "--":
        argv = argv[1:]
    cmd = [os.environ.get("PI_BIN", "pi"), "--mode", "rpc", "--no-session"] + argv
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, cwd=os.getcwd())
    p.stdin.write(json.dumps({"id": "c1", "type": "get_commands"}) + "\n")
    p.stdin.flush()
    out, deadline = None, time.time() + 60
    while time.time() < deadline:
        line = p.stdout.readline()
        if not line:
            break
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "response" and ev.get("command") == "get_commands":
            out = ev
            break
    p.kill()
    if out is None:
        sys.stderr.write("no get_commands response (pi stderr follows)\n" + p.stderr.read()[-2000:])
        return 1
    cmds = out["data"]["commands"]
    if as_json:
        print(json.dumps(cmds, ensure_ascii=False, indent=1))
        return 0
    cmds.sort(key=lambda c: (c.get("source", ""), c["name"]))
    for c in cmds:
        si = c.get("sourceInfo") or {}
        print("%-10s /%-22s %-8s %s" % (c.get("source", "?"), c["name"], si.get("scope", ""), si.get("path", "")))
    print("(%d commands)" % len(cmds))
    return 0

if __name__ == "__main__":
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)  # `| head` must not raise BrokenPipeError
    sys.exit(main())
