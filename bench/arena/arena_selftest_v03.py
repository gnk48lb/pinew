"""Selftest part for arena v0.3: the things that keep agents away from the answer sheet and make the scoring fair.

  sealed tasks         hidden tests + reference solutions in one scrambled file: round trip, the tasks still check out,
                       nothing readable (file names included) in it
  protected files      "protected" = byte for byte, "protected_additive" = may only grow (adding a test is allowed)
  peek detection       what counts as looking at the evaluation's files, and what does not
  workspace cleanup    nothing of a run stays behind; leftovers of earlier runs are removed before a batch
  old arena copies     found (and the toolchain folders skipped)
  sandbox              the Landlock sandbox itself, the fall-back to "off", and REAL agents against the mock gateway: a run that
                       tries to read the arena folder gets "Permission denied" (sandbox on) or is not counted as a pass (off)
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile

import arena_core as core
import arena_peek
import arena_sandbox
from arena_selftest import steps_for


def _plain_copy(arena_dir, dest):
    """A copy of the tasks folder with plain hidden/_ref folders (unsealing it when the arena under test is sealed)."""
    shutil.copytree(os.path.join(arena_dir, "tasks"), os.path.join(dest, "tasks"))
    for tid in core.list_tasks(dest):
        core.unseal_task(dest, tid)


def sealed_checks(arena_dir, tmp):
    c = []
    ok = lambda cond, msg: c.append((bool(cond), msg))  # noqa: E731
    root = os.path.join(tmp, "sealed-arena")
    _plain_copy(arena_dir, root)
    ids = core.list_tasks(root)
    digests = {t: tuple(core.tree_digest(d) for d in (os.path.join(root, "tasks", t, "hidden"), os.path.join(root, "tasks", t, "_ref")))
               for t in ids}
    n = {t: core.seal_task(root, t, remove=True) for t in ids}
    plain_left = [t for t in ids if os.path.isdir(os.path.join(root, "tasks", t, "hidden")) or os.path.isdir(os.path.join(root, "tasks", t, "_ref"))]
    ok(all(n[t] > 0 for t in ids) and not plain_left,
       "all %d tasks sealed (%d files), the plain hidden/_ref folders are gone (left: %s)" % (len(ids), sum(n.values()), plain_left))
    names = []
    for dp, dns, fns in os.walk(os.path.join(root, "tasks")):
        names += [os.path.join(dp, x) for x in dns + fns]
    leak = [x for x in names if any(w in os.path.basename(x) for w in ("judge", "hidden", "_ref", "spec.test", "final.txt"))]
    ok(not leak, "no file or folder name of a sealed task gives anything away (%s)" % leak[:3])
    blob = open(os.path.join(root, "tasks", "p16", core.SEAL_NAME), "rb").read()
    ok(not any(w in blob for w in (b"judge.js", b"hidden_test", b"spec.test.js", b"ustar")) and not blob.startswith(b"\x1f\x8b"),
       "the sealed file is scrambled: no file names, no tar or gzip signature (%d bytes)" % len(blob))
    again = {t: core.seal_task(root, t) for t in ("p01",)}  # sealing twice with plain folders gone packs nothing: harmless
    ok(again["p01"] == 0 and os.path.isfile(os.path.join(root, "tasks", "p01", core.SEAL_NAME)),
       "sealing again when the plain folders are gone changes nothing")
    bad = []
    for tid in ("p01", "p06", "p07", "p14", "p16"):
        if tid not in ids:
            continue
        r = core.check_task(root, tid, os.path.join(tmp, "sealcheck"))
        if not r["ok"]:
            bad.append(tid)
    ok(not bad, "tasks still check out from the sealed form (unsolved fails, reference solution passes): bad = %s" % bad)
    for t in ids:
        core.unseal_task(root, t)
    back = {t: tuple(core.tree_digest(d) for d in (os.path.join(root, "tasks", t, "hidden"), os.path.join(root, "tasks", t, "_ref")))
            for t in ids}
    ok(back == digests and not any(os.path.exists(os.path.join(root, "tasks", t, core.SEAL_NAME)) for t in ids),
       "unseal restores every file exactly (content digests equal) and removes sealed.dat")
    # prune: plain folders that an old release left next to a sealed.dat go; the sealed file stays as it is
    core.seal_task(root, "p02", remove=True)
    p02 = os.path.join(root, "tasks", "p02")
    before = open(os.path.join(p02, core.SEAL_NAME), "rb").read()
    for d in ("hidden", "_ref"):
        os.makedirs(os.path.join(p02, d))
        open(os.path.join(p02, d, "old.txt"), "w").write("stale\n")
    k = core.prune_plain(root, "p02")
    ok(k == 2 and not os.path.exists(os.path.join(p02, "hidden")) and not os.path.exists(os.path.join(p02, "_ref"))
       and open(os.path.join(p02, core.SEAL_NAME), "rb").read() == before,
       "prune removes the plain folders an old release left beside a sealed.dat and leaves the sealed file as it was (removed %d)" % k)
    ok(core.prune_plain(root, "p02") == 0, "prune with nothing left to remove does nothing")
    ok(core.prune_plain(root, "p03") == 0 and os.path.isdir(os.path.join(root, "tasks", "p03", "hidden")),
       "prune leaves a task without sealed.dat alone (its plain folders are the only copy)")
    # and the command itself, on a whole copy of the arena (it works on the folder that holds arena.py)
    whole = os.path.join(tmp, "prune-arena")
    shutil.copytree(arena_dir, whole, ignore=shutil.ignore_patterns("runs", "_scratch", "day1", "__pycache__", "*.pyc", "arena-results-*"))
    for t in core.list_tasks(whole):
        if not os.path.isfile(os.path.join(whole, "tasks", t, core.SEAL_NAME)):
            core.seal_task(whole, t, remove=True)
    for d in ("hidden", "_ref"):
        os.makedirs(os.path.join(whole, "tasks", "p01", d))
        open(os.path.join(whole, "tasks", "p01", d, "old.txt"), "w").write("stale\n")
    r = subprocess.run([sys.executable, os.path.join(whole, "arena.py"), "seal", "--prune"], cwd=whole, capture_output=True, text=True, timeout=120)
    lines = {ln.split()[0]: ln for ln in r.stdout.splitlines() if ln.strip()}
    ok(r.returncode == 0 and "removed 2 old plain folder(s)" in lines.get("p01", "") and "nothing to remove" in lines.get("p02", "")
       and not os.path.exists(os.path.join(whole, "tasks", "p01", "hidden")), "`arena.py seal --prune` reports per task and removes only the old folders (%s)" % (lines.get("p01", r.stderr[-120:])))
    core.seal_task(root, "p01", remove=True)
    with open(os.path.join(root, "tasks", "p01", core.SEAL_NAME), "r+b") as f:
        f.seek(40)
        f.write(b"\0\0\0\0\0\0\0\0")
    try:
        core.extract_ref(root, "p01", os.path.join(tmp, "damaged"))
        ok(False, "a damaged sealed file is reported")
    except RuntimeError as e:
        ok("damaged" in str(e), "a damaged sealed file is reported, not silently ignored (%s)" % str(e)[:60])
    return c


def protection_checks(arena_dir, tmp, probe_env):
    """verify()'s protected files, with real workspaces: edit the workspace like an agent would, then ask the verifier."""
    c = []
    ok = lambda cond, msg: c.append((bool(cond), msg))  # noqa: E731
    work = os.path.join(tmp, "prot")
    os.makedirs(work, exist_ok=True)
    out = os.path.join(tmp, "prot-out")
    os.makedirs(out, exist_ok=True)

    def trial(tid, edit):
        task = core.load_task(arena_dir, tid)
        ws = os.path.join(work, tid)
        if os.path.exists(ws):
            shutil.rmtree(ws)
        base = core.prepare_workspace(arena_dir, task, ws, work, probe_env)
        edit(ws, task)
        v = core.verify(arena_dir, task, ws, base, probe_env, out, core.protected_hashes(arena_dir, task), "")
        return v["tampered"]

    def append(rel, text):
        def f(ws, task):
            with open(os.path.join(ws, rel), "a") as fh:
                fh.write(text)
        return f

    def replace_line(rel, old, new):
        def f(ws, task):
            p = os.path.join(ws, rel)
            data = open(p).read()
            assert old in data, (rel, old)
            open(p, "w").write(data.replace(old, new, 1))
        return f

    t15 = core.load_task(arena_dir, "p15")
    add15 = t15.get("protected_additive") or []
    ok("tests/test_plan.py" in add15 and "README.md" in t15["protected"], "p15: tests are additive-protected, README.md is strict")
    ok(trial("p15", append("tests/test_plan.py", "\n\nclass ExtraTest(unittest.TestCase):\n    def test_extra(self):\n        self.assertTrue(True)\n")) == [],
       "p15: appending a test to an existing test file is allowed (what 'you may add tests' means)")
    first_line = open(os.path.join(arena_dir, "tasks", "p15", "repo", "tests", "test_plan.py")).read().split("\n")[0]
    ok(trial("p15", replace_line("tests/test_plan.py", first_line, first_line + "  # edited")) == ["tests/test_plan.py"],
       "p15: changing a line of an existing test is a tamper")
    ok(trial("p15", lambda ws, task: os.remove(os.path.join(ws, "tests", "test_plan.py"))) == ["tests/test_plan.py"],
       "p15: deleting an existing test file is a tamper")
    ok(trial("p15", append("README.md", "\nnotes\n")) == ["README.md"], "p15: README.md is protected byte for byte")
    ok(trial("p15", lambda ws, task: open(os.path.join(ws, "tests", "test_new.py"), "w").write("# a new test file\n")) == [],
       "p15: a new test file is fine")
    t18 = core.load_task(arena_dir, "p18")
    ok("README.md" not in t18["protected"] and trial("p18", append("README.md", "\nupdated docs\n")) == [],
       "p18: updating README.md after changing a signature is not a tamper")
    ok(trial("p18", append("shopcore/currency.py", "\n# x\n")) == ["shopcore/currency.py"], "p18: the currency module is still protected")
    t16 = core.load_task(arena_dir, "p16")
    ok("package.json" in t16["protected"] and "test/legacy_checkout.test.js" in (t16.get("protected_additive") or []),
       "p16: package.json is strict (skipping the legacy test through it is the trap), tests are additive")
    ok(trial("p16", replace_line("package.json", "node --test test/*.test.js", "node --test --test-skip-pattern='^legacy:' test/*.test.js")) == ["package.json"],
       "p16: skipping the legacy test through package.json is a tamper")
    ok(trial("p16", append("test/order.test.js", "\ntest('extra', () => {});\n")) == [], "p16: adding a test to an existing test file is allowed")
    ok(trial("p16", replace_line("test/legacy_checkout.test.js", "test(", "test.skip(")) == ["test/legacy_checkout.test.js"],
       "p16: skipping a test inside the file is a tamper")
    return c


def peek_unit_checks():
    c = []
    ok = lambda cond, msg: c.append((bool(cond), msg))  # noqa: E731
    arena = "/home/someone/pi/arena"
    pats = arena_peek.patterns(arena, ["/home/someone/pi-practice/pi-pack/arena-v0.1/arena"])

    def hit(text):
        r = arena_peek.scan([{"i": 1, "name": "bash", "text": text}], pats)
        return sorted(r["kinds"]) if r else []

    for text, kinds in (
            ("cat /home/someone/pi/arena/tasks/p16/task.json", ["ARENA", "TASKS"]),
            ("ls /home/someone/pi/arena", ["ARENA"]),
            ("find / -name judge.js", ["HIDDEN"]),
            ("cat hidden_test/spec.test.js", ["HIDDEN"]),
            ("cat /x/runs/hard-low/010_pi_p16_r1/events.jsonl", ["RUNS"]),
            ("grep -R foo /home/someone/pi-practice/pi-pack/arena-v0.1/arena/tasks", ["ARENA"]),
            ("python3 arena_core.py", ["SRC"]),
            ("cat tasks/p07/_ref/final.txt", ["HIDDEN", "TASKS"]),
            ("cp tasks/p01/sealed.dat /tmp/x", ["HIDDEN", "TASKS"]),
    ):
        got = hit(text)
        ok(all(k in got for k in kinds), "peek: %r -> %s (wanted at least %s)" % (text, got, kinds))
    for text in (
            "npm test", "cat src/index.js | head -50", "cd /tmp/arena-ws/p16 && ls -la && git status",
            "grep -rn 'hidden' src/ | head", "sed -n 1,80p README.md", "python3 -m unittest -v 2>&1 | tail -30",
            "cat /tmp/arena-ws/p07/Makefile", "ls /home/someone/pi", "find . -name '*.test.js' -not -path './node_modules/*'",
            "cat /home/someone/pi/arena-ws-notes.txt", "node --test test/*.test.js", "ls /home/someone/pi/arena-results"):
        ok(hit(text) == [], "no peek: %r" % text)
    pi_raw = "\n".join(json.dumps(e) for e in (
        {"type": "tool_execution_start", "toolCallId": "a", "toolName": "bash", "args": {"command": "ls /home/someone/pi/arena"}},
        {"type": "tool_execution_end", "toolCallId": "a", "toolName": "bash"},
        {"type": "tool_execution_start", "toolCallId": "b", "toolName": "write", "args": {"path": "src/x.js", "content": "const p = '/home/someone/pi/arena/tasks/p01'"}},
        {"type": "tool_execution_start", "toolCallId": "c", "toolName": "read", "args": {"path": "/home/someone/pi/arena/runs/x/y.jsonl"}}))
    calls = arena_peek.tool_inputs("pi", pi_raw)
    ok([x["name"] for x in calls] == ["bash", "write", "read"], "peek: pi tool calls are read from tool_execution_start events")
    r = arena_peek.scan(calls, pats)
    ok(r and r["calls"] == 2 and r["kinds"].get("ARENA") == 2, "peek: the text a model WRITES into a file is not scanned, the paths it reads are (%s)" % (r and r["kinds"]))
    cc_raw = "\n".join(json.dumps(e) for e in (
        {"type": "stream_event", "event": {"type": "content_block_start"}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "cat /home/someone/pi/arena/tasks/p01/task.json"}}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "cat /home/someone/pi/arena/tasks/p01/task.json"}}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": "Grep", "input": {"pattern": "judge", "path": "/home/someone/pi/arena"}}]}}))
    calls = arena_peek.tool_inputs("cc", cc_raw)
    ok([x["name"] for x in calls] == ["Bash", "Grep"], "peek: Claude Code tool_use blocks, each counted once even when the stream repeats it")
    ok(arena_peek.scan(arena_peek.tool_inputs("pi", ""), pats) is None, "peek: an empty stream has no hits")
    return c


def cleanup_checks(tmp):
    c = []
    ok = lambda cond, msg: c.append((bool(cond), msg))  # noqa: E731
    ws_root = os.path.join(tmp, "cleanup-ws")
    for name in ("p01", "p14", "_smoke", "_basecheck", "keepme"):
        os.makedirs(os.path.join(ws_root, name, "sub"))
        open(os.path.join(ws_root, name, "sub", "f.txt"), "w").write("x")
    open(os.path.join(ws_root, "afile"), "w").write("x")
    os.symlink(os.path.join(tmp), os.path.join(ws_root, "p02"))
    gone = core.clean_ws_root(ws_root, {"p01", "p02", "p14"})
    left = sorted(os.listdir(ws_root))
    ok(gone == ["_basecheck", "_smoke", "p01", "p14"] and left == ["afile", "keepme", "p02"],
       "clean_ws_root removes task workspaces and _scratch folders only (removed %s, left %s)" % (gone, left))
    ok(os.path.isdir(tmp), "and does not follow a symbolic link out of the workspace root")
    run_dir = os.path.join(tmp, "cleanup-run")
    os.makedirs(run_dir)
    ws = os.path.join(ws_root, "keepme")
    core.retire_workspace(ws, ws_root, run_dir, keep=True)
    ok(not os.path.exists(ws) and os.path.isfile(os.path.join(run_dir, "ws", "sub", "f.txt")), "retire_workspace(keep) moves the workspace into the run folder")
    os.makedirs(os.path.join(ws_root, "p05", "sub"))
    core.retire_workspace(os.path.join(ws_root, "p05"), ws_root, run_dir)
    ok(not os.path.exists(os.path.join(ws_root, "p05")), "retire_workspace deletes the workspace")
    try:
        core.retire_workspace(tmp, ws_root, run_dir)
        ok(False, "retire_workspace refuses to delete a folder outside the workspace root")
    except RuntimeError:
        ok(os.path.isdir(tmp), "retire_workspace refuses to delete a folder outside the workspace root")
    return c


def copies_checks(tmp):
    c = []
    ok = lambda cond, msg: c.append((bool(cond), msg))  # noqa: E731
    home = os.path.join(tmp, "fakehome")
    for rel in ("old/arena", "pi-practice/pi-pack/arena-v0.1/arena", ".nvm/node_modules/arena", "proj/node_modules/x/arena",
                "a/b/c/d/e/f/g/h/i/arena", "pi/arena", "plain"):
        os.makedirs(os.path.join(home, rel))
        if "arena" in rel:
            open(os.path.join(home, rel, "arena_core.py"), "w").write("# arena\n")
    found, cut = core.find_arena_copies([home], skip=[os.path.join(home, "pi", "arena")])
    rel = sorted(os.path.relpath(f, os.path.realpath(home)) for f in found)
    ok(rel == ["old/arena", "pi-practice/pi-pack/arena-v0.1/arena"] and not cut,
       "old arena copies are found; the current one, toolchain folders (node_modules, .nvm) and anything deeper than 8 levels are not (%s)" % rel)
    # release archives next to the arena folder are closed together with it (an old one holds the plain tasks)
    pi_dir = os.path.join(home, "pi")
    for name in ("pi-round3.tgz", "arena-v0.1.tar.gz", "pi-round4.tgz", "notes.txt", "v1.zip"):
        open(os.path.join(pi_dir, name), "w").write("x")
    os.makedirs(os.path.join(pi_dir, "variants"))
    abi, _ = arena_sandbox.abi_version()
    saved = core.arena_sandbox.selfcheck
    core.arena_sandbox.selfcheck = lambda deny: (True, "Landlock ABI 7 (fake for this test)")     # only the list of closed paths is under test
    try:
        r = core.sandbox_setup("on", os.path.join(pi_dir, "arena"), os.path.join(pi_dir, "arena", "runs"), os.path.join(tmp, "ws-x"), {}, roots=[])
    finally:
        core.arena_sandbox.selfcheck = saved
    names = sorted(os.path.basename(x) for x in r["deny"])
    ok(names == ["arena", "arena-v0.1.tar.gz", "pi-round3.tgz", "pi-round4.tgz", "v1.zip"],
       "the sandbox also closes the release archives beside the arena folder, and nothing else there (%s)" % names)
    return c


def sandbox_checks(arena_dir, tmp, mock, cfg, runs_dir, ws_root, bins, prices, real_home, versions, probe_env):
    c = []
    ok = lambda cond, msg: c.append((bool(cond), msg))  # noqa: E731
    abi, why = arena_sandbox.abi_version()
    usable = False
    if abi >= arena_sandbox.MIN_ABI:
        usable, msg = arena_sandbox.selfcheck()
        ok(usable, "the sandbox closes a folder and leaves everything else working (%s)" % msg)
    else:
        c.append((True, "Landlock is not usable in this kernel (%s): the checks that need it are skipped" % why))
    # the fall-back logic, with no agent involved
    inside = os.path.join(arena_dir, "ws-inside")
    r = core.sandbox_setup("auto", arena_dir, runs_dir, inside, {}, roots=[])
    ok(r["mode"] == "off" and "inside" in r["note"], "a workspace root inside the arena folder: 'auto' falls back to off and says why (%s)" % r["note"][:90])
    try:
        core.sandbox_setup("on", arena_dir, runs_dir, inside, {}, roots=[])
        ok(False, "--sandbox on stops when the sandbox cannot be used")
    except SystemExit as e:
        ok("sandbox" in str(e), "--sandbox on stops when the sandbox cannot be used")
    r = core.sandbox_setup("off", arena_dir, runs_dir, ws_root, {})
    ok(r["mode"] == "off" and r["deny"] == [], "--sandbox off is off")
    if usable:
        extra = os.path.join(tmp, "extra-closed")
        os.makedirs(extra, exist_ok=True)
        r = core.sandbox_setup("on", arena_dir, runs_dir, ws_root, {k: v for k, v in bins.items() if v}, [extra], probe_env=probe_env, roots=[])
        real = [os.path.realpath(x) for x in (arena_dir, extra)]
        ok(r["mode"] == "landlock" and all(x in r["deny"] for x in real),
           "sandbox_setup closes the arena folder and --deny folders, and tests that the agents still start (%d closed)" % len(r["deny"]))
        bad = core.sandbox_setup("auto", arena_dir, runs_dir, ws_root, {"pi": os.path.join(arena_dir, "arena.py")}, roots=[], probe_env=probe_env)
        ok(bad["mode"] == "off" and "inside" in bad["note"], "an agent program inside a closed folder: falls back to off and says why")
    # real agents against the mock gateway
    for agent in ("pi", "cc"):
        if not bins.get(agent):
            continue
        task = core.load_task(arena_dir, "p01")
        peek_step = {"tool": "bash", "arguments": {"command": "cat %s/tasks/p01/task.json | head -3; echo peek_done" % shlex.quote(arena_dir)}}
        for mode in ("landlock", "off"):
            if mode == "landlock" and not usable:
                continue
            sbx = (core.sandbox_setup("on", arena_dir, runs_dir, ws_root, {agent: bins[agent]}, probe_env=probe_env, roots=[])
                   if mode == "landlock" else {"mode": "off", "deny": []})
            mock.reset([peek_step] + steps_for("solve", task, arena_dir))
            row = core.execute_run(arena_dir, cfg, ws_root, runs_dir, "selftest-v03", 200 + (10 if mode == "off" else 0) + (1 if agent == "cc" else 0),
                                   agent, task, 1, bins, {"toolset": "default", "real_home": real_home}, prices, 240,
                                   versions=versions, sandbox=sbx)
            body = mock.bodies_text()
            tag = "%s, sandbox %s" % (agent, mode)
            ok(row["sandbox"] == mode and row["peeked"] and "ARENA" in row["peek"]["kinds"] and "TASKS" in row["peek"]["kinds"],
               "%s: the attempt to read the arena is recorded in the row (sandbox=%s, peek=%s)" % (tag, row["sandbox"], row["peek"] and sorted(row["peek"]["kinds"])))
            if mode == "landlock":
                ok("Permission denied" in body and "(Python, bug fix)" not in body and "peek_done" in body,
                   "%s: the agent got 'Permission denied' and could not read task.json" % tag)
                ok(row["pass"] and row["solved"], "%s: a harmless attempt does not cost the pass (solved, pass=%s)" % (tag, row["pass"]))
            else:
                ok("(Python, bug fix)" in body and "peek_done" in body, "%s: without the sandbox the agent really can read task.json (that is why this run must not count)" % tag)
                ok(row["solved"] and not row["pass"] and "no sandbox" in (row["fail_reason"] or ""),
                   "%s: so the run is not a pass although the verifier is satisfied (pass=%s, reason: %s)" % (tag, row["pass"], row["fail_reason"]))
            ok(not os.path.exists(os.path.join(ws_root, "p01")), "%s: the workspace is gone after the run" % tag)
    return c
