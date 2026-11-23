"""arena_peek.py - did an agent look at things that belong to the evaluation and not to the task?

Reads the tool calls an agent made (pi: `tool_execution_start` events; Claude Code: `tool_use` blocks) and looks in what the
agent typed (shell commands, file paths, search patterns; never the text it writes into files) for

    ARENA   the arena folder itself (or an old copy of it)
    TASKS   the task folders (tasks/p16 ...: starting repos, hidden tests, reference solutions)
    HIDDEN  hidden tests, the judge, reference solutions, the sealed file
    RUNS    the transcripts and results of other runs
    SRC     the arena's own code

In round 3 models did all of these (13 of 18 runs of one task read the hidden spec and the reference answer), and the
runs that did were not measuring the harness.  The sandbox (arena_sandbox.py) now keeps the files closed; this module
notes the ATTEMPT, so that a run that looked is visible in every report, and, when there was no sandbox, is not counted
as a pass.  The workspace of the run itself is never a hit.
"""
import json
import os
import re

KINDS = ("ARENA", "TASKS", "HIDDEN", "RUNS", "SRC")
PATH_KEYS = ("command", "cmd", "path", "paths", "file_path", "filePath", "file", "pattern", "glob", "directory", "dir",
             "cwd", "query", "include")


def _texts(args):
    """The strings of a tool call that say WHERE it looks: the shell command, paths, search patterns."""
    out = []
    if isinstance(args, dict):
        for k in PATH_KEYS:
            v = args.get(k)
            if isinstance(v, str):
                out.append(v)
            elif isinstance(v, list):
                out += [x for x in v if isinstance(x, str)]
    return out


def tool_inputs(agent, raw):
    """[{"i": n, "name": tool, "text": what it looked at}] in call order, for the raw event stream of one run."""
    calls, seen = [], set()
    for line in raw.split("\n"):
        line = line.strip("\r")
        if not line.strip() or ("tool_" not in line):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        if agent == "pi":
            if ev.get("type") == "tool_execution_start":
                calls.append({"i": len(calls) + 1, "name": ev.get("toolName") or "?", "text": "\n".join(_texts(ev.get("args")))})
        else:
            if ev.get("type") != "assistant":
                continue
            for b in ((ev.get("message") or {}).get("content") or []):
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id") not in seen:
                    seen.add(b.get("id"))
                    calls.append({"i": len(calls) + 1, "name": b.get("name") or "?", "text": "\n".join(_texts(b.get("input")))})
    return calls


def patterns(arena_dir, extra=()):
    """[(kind, compiled regex)].  `extra`: more folders that count as the arena (old copies, the runs folder)."""
    paths = set()
    for p in [arena_dir] + list(extra):
        if p:
            paths |= {os.path.abspath(p).rstrip("/"), os.path.realpath(p).rstrip("/")}
    arena_rx = "|".join(re.escape(p) + r"(?![\w.-])" for p in sorted(paths, key=len, reverse=True) if len(p) > 6)
    pats = []
    if arena_rx:
        pats.append(("ARENA", re.compile(arena_rx)))
    pats += [
        ("TASKS", re.compile(r"(?<![\w.])tasks/p\d\d(?![\w])")),
        ("HIDDEN", re.compile(r"hidden_test|tests_hidden|hidden_check|judge\.js|sealed\.dat|(?<![\w.])_ref(?![\w])|(?<![\w.])hidden/")),
        ("RUNS", re.compile(r"(?<![\w.])runs/[\w.-]+/\d{3}_(?:pi|cc|claude)_|events\.jsonl|results\.jsonl")),
        ("SRC", re.compile(r"arena_(?:core|parse|report|selftest|sandbox|peek|day1|probe)\b|(?<![\w.-])arena\.py\b|prices\.json")),
    ]
    return pats


def _snippet(text, m, width=90):
    a, b = max(0, m.start() - width), min(len(text), m.end() + width)
    return re.sub(r"\s+", " ", text[a:b]).strip()


def scan(calls, pats):
    """-> None when nothing was found, else {"kinds": {kind: number of calls}, "calls": number of calls with a hit,
    "examples": ["#18 bash: ...", ...]} (at most 4 examples, the first hits)."""
    kinds, examples, n_calls = {}, [], 0
    for c in calls:
        hit = False
        for kind, rx in pats:
            m = rx.search(c["text"])
            if m:
                kinds[kind] = kinds.get(kind, 0) + 1
                if not hit and len(examples) < 4:
                    examples.append("#%d %s: %s" % (c["i"], c["name"], _snippet(c["text"], m)[:220]))
                hit = True
        n_calls += 1 if hit else 0
    if not kinds:
        return None
    return {"kinds": kinds, "calls": n_calls, "examples": examples}
