#!/usr/bin/env python3
"""arena_sandbox.py - run a command with some folders closed to it (Linux Landlock; no root, nothing to install).

    python3 -I arena_sandbox.py --probe                        print the Landlock version of this kernel (exit 0), or why not
    python3 -I arena_sandbox.py --check [--deny DIR ...]       self test: is the sandbox really closed and nothing else
    python3 -I arena_sandbox.py [--deny DIR ...] -- COMMAND [ARGUMENTS ...]

Why.  The agents under test have a shell, a shell reads the whole disk, and the disk holds this arena: the hidden
tests, the reference solutions, the transcripts of every earlier run.  In round 3 models looked there (13 of 18 runs of
one task), so those runs measured "who finds the answer sheet", not the harness.

How.  Landlock (a part of Linux >= 5.13) lets a process take rights away from itself and from everything it starts,
without root.  Here: the command may do everything it could do before EXCEPT read, write, execute, create or delete
anything below the denied folders.  Listing and `stat` stay possible (so `ls /home/you` and `find /` keep working and
the agent sees "Permission denied" instead of an empty or missing folder: an honest "off limits" signal).  The rule is
built the only way Landlock allows ("allow these subtrees"): for every directory on the way from / to a denied folder,
every entry except the one on the way gets full rights.  The denied folder itself gets none.

The restriction is applied to THIS process, which then replaces itself with the command (os.execvp), so the command
runs with the same pid, environment and working directory it would have had without the wrapper.  If Landlock cannot be
set up the wrapper exits with status 97 and does NOT run the command: a run is never started half-sandboxed.

Python >= 3.8, standard library only.
"""
import ctypes
import os
import stat
import subprocess
import sys
import tempfile

SYS_CREATE_RULESET, SYS_ADD_RULE, SYS_RESTRICT_SELF = 444, 445, 446        # the same number on every architecture
CREATE_RULESET_VERSION = 1 << 0
RULE_PATH_BENEATH = 1
PR_SET_NO_NEW_PRIVS = 38

EXECUTE, WRITE_FILE, READ_FILE, READ_DIR = 1 << 0, 1 << 1, 1 << 2, 1 << 3
REMOVE_DIR, REMOVE_FILE = 1 << 4, 1 << 5
MAKE_CHAR, MAKE_DIR, MAKE_REG, MAKE_SOCK, MAKE_FIFO, MAKE_BLOCK, MAKE_SYM = (1 << i for i in range(6, 13))
REFER, TRUNCATE = 1 << 13, 1 << 14                                           # Landlock ABI 2 and 3
FILE_RIGHTS = EXECUTE | WRITE_FILE | READ_FILE | TRUNCATE                    # the rights that mean something for a non-directory

MIN_ABI = 2          # ABI 1 forbids every rename/link between directories (git and npm would break); ABI 2 handles REFER
EXIT_NO_SANDBOX = 97


class SandboxError(Exception):
    pass


class _PathBeneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def _libc():
    lib = ctypes.CDLL(None, use_errno=True)
    lib.syscall.restype = ctypes.c_long
    return lib


def abi_version():
    """(abi, why_not): the Landlock ABI of this kernel (1, 2, 3 ...), or (0, reason) when there is no Landlock."""
    try:
        lib = _libc()
        r = lib.syscall(ctypes.c_long(SYS_CREATE_RULESET), ctypes.c_void_p(None), ctypes.c_size_t(0),
                        ctypes.c_uint32(CREATE_RULESET_VERSION))
    except Exception as e:  # noqa
        return 0, "cannot call the kernel: %s" % e
    if r < 0:
        err = ctypes.get_errno()
        reason = {38: "this kernel has no Landlock (ENOSYS)", 95: "Landlock is switched off in this kernel (EOPNOTSUPP)"}.get(err)
        return 0, reason or ("landlock_create_ruleset failed: %s" % os.strerror(err))
    abi = int(r)
    cap = os.environ.get("ARENA_SANDBOX_ABI_CAP", "")        # for tests: behave like an older kernel (rights above the cap are not handled)
    if cap.isdigit() and int(cap) < abi:
        abi = int(cap)
        if abi < 1:
            return 0, "Landlock capped to ABI 0 (ARENA_SANDBOX_ABI_CAP)"
    return abi, ""


def handled_rights(abi):
    """Everything a path-based rule can take away, except listing a directory (kept so that `ls` and `find` still work)."""
    h = (EXECUTE | WRITE_FILE | READ_FILE | REMOVE_DIR | REMOVE_FILE | MAKE_CHAR | MAKE_DIR | MAKE_REG | MAKE_SOCK |
         MAKE_FIFO | MAKE_BLOCK | MAKE_SYM)
    if abi >= 2:
        h |= REFER
    if abi >= 3:
        h |= TRUNCATE
    return h


def _real(p):
    return os.path.realpath(os.path.expanduser(p))


def plan(deny):
    """(closed, open_paths): the real paths that stay closed, and the paths whose subtrees stay fully open.

    `open_paths` = for every directory on the way from / to a closed path: all of its entries except the entry on the
    way and the closed path itself.  Raises SandboxError when such a directory cannot be listed (we could not tell what
    to leave open)."""
    closed = []
    for d in sorted({_real(x) for x in deny if x}):
        if not os.path.lexists(d):
            continue
        if any(d == c or d.startswith(c + "/") for c in closed):            # inside a folder that is closed anyway
            continue
        closed.append(d)
    ways = set()                                                              # the directories on the way (and "/")
    for d in closed:
        parent = os.path.dirname(d)
        while True:
            ways.add(parent)
            if parent == "/":
                break
            parent = os.path.dirname(parent)
    shut = set(closed)
    opened = []
    for a in sorted(ways):
        try:
            names = sorted(os.listdir(a))
        except OSError as e:
            raise SandboxError("cannot list %s (%s): cannot tell what to leave open" % (a, e.strerror or e))
        for n in names:
            p = a.rstrip("/") + "/" + n
            if p in shut or p in ways:
                continue
            opened.append(p)
    return closed, opened


def restrict(deny):
    """Close `deny` for this process and everything it starts.  Raises SandboxError; returns the number of open subtrees."""
    abi, why = abi_version()
    if abi < MIN_ABI:
        raise SandboxError(why or "Landlock ABI %d is too old (need >= %d, i.e. Linux >= 5.19)" % (abi, MIN_ABI))
    closed, opened = plan(deny)
    lib = _libc()
    handled = handled_rights(abi)
    attr = ctypes.c_uint64(handled)
    ruleset = lib.syscall(ctypes.c_long(SYS_CREATE_RULESET), ctypes.byref(attr), ctypes.c_size_t(8), ctypes.c_uint32(0))
    if ruleset < 0:
        raise SandboxError("landlock_create_ruleset: " + os.strerror(ctypes.get_errno()))
    n_rules = 0
    try:
        for p in opened:
            try:
                fd = os.open(p, os.O_PATH | os.O_CLOEXEC)
            except OSError:
                continue                                                      # gone, or not reachable for us either
            try:
                is_dir = stat.S_ISDIR(os.fstat(fd).st_mode)
                rule = _PathBeneath(handled if is_dir else (handled & FILE_RIGHTS), fd)
                if lib.syscall(ctypes.c_long(SYS_ADD_RULE), ctypes.c_int(ruleset), ctypes.c_int(RULE_PATH_BENEATH),
                               ctypes.byref(rule), ctypes.c_uint32(0)) < 0:
                    err = ctypes.get_errno()
                    if err in (9, 77, 22) and not is_dir:                     # EBADF / EBADFD / EINVAL on an odd file (socket, ...)
                        continue
                    raise SandboxError("landlock_add_rule(%s): %s" % (p, os.strerror(err)))
                n_rules += 1
            finally:
                os.close(fd)
        if lib.prctl(ctypes.c_int(PR_SET_NO_NEW_PRIVS), ctypes.c_ulong(1), ctypes.c_ulong(0), ctypes.c_ulong(0),
                     ctypes.c_ulong(0)) != 0:
            raise SandboxError("prctl(NO_NEW_PRIVS): " + os.strerror(ctypes.get_errno()))
        if lib.syscall(ctypes.c_long(SYS_RESTRICT_SELF), ctypes.c_int(ruleset), ctypes.c_uint32(0)) < 0:
            raise SandboxError("landlock_restrict_self: " + os.strerror(ctypes.get_errno()))
    finally:
        os.close(ruleset)
    return n_rules


def wrap_argv(argv, deny, here=None):
    """The command line that starts `argv` inside the sandbox."""
    here = here or os.path.abspath(__file__)
    out = [sys.executable, "-I", here]
    for d in deny:
        out += ["--deny", d]
    return out + ["--"] + list(argv)


# ------------------------------------------------------------------------------------------ self test
_CHECK_CHILD = r'''
import os, sys, subprocess, shutil, tempfile
secret_dir, secret_file, work = sys.argv[1:4]
bad = []
def expect_denied(what, fn):
    try:
        fn()
    except PermissionError:
        return
    except OSError as e:
        bad.append("%s: expected PermissionError, got %r" % (what, e)); return
    bad.append("%s: was allowed" % what)
def expect_ok(what, fn):
    try:
        fn()
    except Exception as e:
        bad.append("%s: %r" % (what, e))
expect_denied("read a denied file", lambda: open(secret_file).read())
expect_denied("write a denied file", lambda: open(secret_file, "a").write("x"))
expect_denied("create in a denied folder", lambda: open(os.path.join(secret_dir, "new.txt"), "w").write("x"))
expect_denied("delete in a denied folder", lambda: os.remove(secret_file))
expect_denied("rename out of a denied folder", lambda: os.rename(secret_file, os.path.join(work, "taken.txt")))
expect_denied("read through a symlink", lambda: (os.symlink(secret_file, os.path.join(work, "ln")), open(os.path.join(work, "ln")).read()))
expect_ok("list a denied folder (allowed on purpose)", lambda: os.listdir(secret_dir))
expect_ok("list / and the parent of the denied folder", lambda: (os.listdir("/"), os.listdir(os.path.dirname(secret_dir))))
expect_ok("read /etc/passwd", lambda: open("/etc/passwd").read())
def work_ok():
    p = os.path.join(work, "a"); os.makedirs(p); q = os.path.join(work, "b"); os.makedirs(q)
    f = os.path.join(p, "f.txt"); open(f, "w").write("hello"); os.rename(f, os.path.join(q, "g.txt"))
    os.link(os.path.join(q, "g.txt"), os.path.join(p, "h.txt")); os.truncate(os.path.join(p, "h.txt"), 2)
    shutil.copytree(q, os.path.join(work, "c")); shutil.rmtree(os.path.join(work, "c"))
    os.chmod(os.path.join(p, "h.txt"), 0o755); os.symlink("g.txt", os.path.join(q, "s"))
expect_ok("create, rename between folders, link, truncate, copy, delete, chmod in the work folder", work_ok)
def git_ok():
    g = shutil.which("git")
    if not g:
        return
    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", GIT_AUTHOR_NAME="a", GIT_AUTHOR_EMAIL="a@a",
               GIT_COMMITTER_NAME="a", GIT_COMMITTER_EMAIL="a@a")
    d = os.path.join(work, "repo"); os.makedirs(d)
    for a in (["init", "-q"], ["add", "-A"], ["commit", "-q", "--allow-empty", "-m", "x"]):
        r = subprocess.run([g, "-C", d] + a, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if r.returncode != 0:
            raise RuntimeError("git %s: %s" % (a[0], r.stdout.strip()[:200]))
expect_ok("git init / add / commit in the work folder", git_ok)
def exec_ok():
    r = subprocess.run([sys.executable, "-c", "print(1)"], stdout=subprocess.PIPE)
    if r.stdout.strip() != b"1":
        raise RuntimeError("could not run a python child")
expect_ok("start a child process", exec_ok)
print("\n".join(bad) if bad else "ALL-GOOD")
'''


def selfcheck(extra_deny=(), say=None):
    """Does the sandbox close what it should and keep everything else working?  (ok, message).  Starts real child processes."""
    abi, why = abi_version()
    if abi < MIN_ABI:
        return False, why or "Landlock ABI %d is too old (need >= %d)" % (abi, MIN_ABI)
    base = tempfile.mkdtemp(prefix="arena-sbx-")
    try:
        secret_dir = os.path.join(base, "secret")
        work = os.path.join(base, "work")
        os.makedirs(secret_dir)
        os.makedirs(work)
        secret_file = os.path.join(secret_dir, "answer.txt")
        with open(secret_file, "w") as f:
            f.write("the hidden test\n")
        cmd = wrap_argv([sys.executable, "-c", _CHECK_CHILD, secret_dir, secret_file, work], [secret_dir] + list(extra_deny))
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        out = (r.stdout or "").strip()
        if r.returncode == EXIT_NO_SANDBOX:
            return False, out.splitlines()[-1] if out else "the sandbox could not be set up"
        if r.returncode != 0 or out != "ALL-GOOD":
            return False, "self test failed: " + (out[-600:] or "exit %d" % r.returncode)
        return True, "Landlock ABI %d" % abi
    except Exception as e:  # noqa
        return False, "self test could not run: %s" % e
    finally:
        subprocess.run(["chmod", "-R", "u+rwX", base], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import shutil
        shutil.rmtree(base, ignore_errors=True)


def main(argv):
    deny, cmd, i, probe, check = [], [], 0, False, False
    while i < len(argv):
        a = argv[i]
        if a == "--":
            cmd = argv[i + 1:]
            break
        if a == "--deny" and i + 1 < len(argv):
            deny.append(argv[i + 1])
            i += 2
            continue
        if a == "--probe":
            probe = True
        elif a == "--check":
            check = True
        else:
            print("arena_sandbox: unknown argument %r" % a, file=sys.stderr)
            return 2
        i += 1
    if probe:
        abi, why = abi_version()
        print(("Landlock ABI %d" % abi) if abi else why)
        return 0 if abi >= MIN_ABI else 1
    if check:
        ok, msg = selfcheck(deny)
        print(("sandbox OK: " if ok else "sandbox NOT usable: ") + msg)
        return 0 if ok else 1
    if not cmd:
        print("usage: arena_sandbox.py [--deny DIR ...] -- COMMAND [ARGS ...]", file=sys.stderr)
        return 2
    try:
        restrict(deny)
    except SandboxError as e:
        print("arena_sandbox: cannot set up the sandbox: %s" % e, file=sys.stderr)
        return EXIT_NO_SANDBOX
    try:
        os.execvp(cmd[0], cmd)
    except OSError as e:
        print("arena_sandbox: cannot start %s: %s" % (cmd[0], e), file=sys.stderr)
        return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
