#!/usr/bin/env python3
"""Read-only snapshot of the pi setup on THIS machine, for pasting into a chat. No network, no writes.

    python3 tools/collect-state.py            # or: python3 ~/pinew/tools/collect-state.py

What it prints (all of it is safe to paste; API keys are replaced by "<redacted N chars>"):
  1. machine   OS, python, node, which pi and its version, how pi was installed
  2. agent dir  every v1 file: identical to the measured baseline / MODIFIED / missing; extra files you added
  3. settings.json and models.json (providers, model ids, prices, api overrides; no keys)
  4. `pi list` (installed packages) and any leftover installer backups (.v1-*)
  5. whether ~/pinew exists and which commit it is on

Use $PI_CODING_AGENT_DIR to inspect another agent dir (default ~/.pi/agent).
"""
import hashlib, json, os, platform, re, shutil, subprocess, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
AGENT = Path(os.environ.get("PI_CODING_AGENT_DIR") or (Path.home() / ".pi" / "agent")).expanduser()
SECRET = re.compile(r"(api[_-]?key|token|secret|password|authorization)", re.I)


def sh(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa: BLE001
        return "(failed: %s)" % e


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def redact(o):
    if isinstance(o, dict):
        return {k: ("<redacted %d chars>" % len(str(v)) if SECRET.search(k) and isinstance(v, str) else redact(v))
                for k, v in o.items()}
    if isinstance(o, list):
        return [redact(x) for x in o]
    return o


def load_json(p):
    try:
        return json.loads(Path(p).read_text())
    except FileNotFoundError:
        return None
    except Exception as e:  # noqa: BLE001
        return {"__error__": str(e)}


def main():
    print("== 1. machine")
    print("os      :", platform.platform())
    print("python  :", sys.version.split()[0])
    node = shutil.which("node")
    print("node    :", sh([node, "--version"]) if node else "(not on PATH)")
    pi = shutil.which("pi")
    print("pi path :", pi or "(not on PATH)")
    if pi:
        print("pi real :", os.path.realpath(pi))
        print("pi ver  :", sh([pi, "--version"]))
    print("agent   :", AGENT, "(exists)" if AGENT.is_dir() else "(MISSING)")
    print("proxy   :", {k: re.sub(r"//[^/@\s]*@", "//<redacted>@", os.environ[k])  # drop user:pass@ from proxy URLs
                        for k in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy") if k in os.environ} or "(none)")

    print("\n== 2. v1 files vs the measured baseline (docs/baseline/v1-SHA256SUMS.txt)")
    sums = REPO / "docs" / "baseline" / "v1-SHA256SUMS.txt"
    base = {}
    if sums.is_file():
        for line in sums.read_text().splitlines():
            h, _, rel = line.partition("  ")
            base[rel.lstrip("./")] = h
    installed = ["AGENTS.md", "presets.json"] + sorted(r for r in base if r.split("/")[0] in ("extensions", "prompts", "skills"))
    for rel in installed:
        p = AGENT / rel
        if not p.is_file():
            st = "missing"
        elif p.is_symlink():
            st = "symlink -> %s" % os.readlink(p)
        else:
            st = "identical" if sha(p) == base.get(rel) else "MODIFIED"
        print("%-28s %s" % (rel, st))
    extra = []
    for d in ("extensions", "prompts", "skills"):
        for p in sorted((AGENT / d).rglob("*")) if (AGENT / d).is_dir() else []:
            if p.is_file() and str(p.relative_to(AGENT)) not in base:
                extra.append(str(p.relative_to(AGENT)))
    print("extra files you added (not part of v1):", extra or "none")
    others = sorted(x.name for x in AGENT.iterdir()) if AGENT.is_dir() else []
    print("top level of agent dir:", others)

    print("\n== 3. settings.json / models.json (secrets redacted)")
    st = load_json(AGENT / "settings.json")
    print("settings.json:", json.dumps(redact(st), ensure_ascii=False, indent=1) if st is not None else "(none)")
    mj = load_json(AGENT / "models.json")
    if mj is None:
        print("models.json: (none)")
    else:
        for pname, prov in (mj.get("providers") or {}).items():
            print("provider %s: baseUrl=%s api=%s compat=%s apiKey=%s" % (
                pname, prov.get("baseUrl"), prov.get("api"), json.dumps(prov.get("compat")),
                "<redacted %d chars>" % len(str(prov.get("apiKey", ""))) if prov.get("apiKey") else "(none)"))
            for m in prov.get("models") or []:
                print("   %-46s api=%-18s ctx=%s max=%s reasoning=%s cost=%s levels=%s" % (
                    m.get("id"), m.get("api", "(provider's)"), m.get("contextWindow"), m.get("maxTokens"),
                    m.get("reasoning"), json.dumps(m.get("cost")),
                    ",".join(k for k, v in (m.get("thinkingLevelMap") or {}).items() if v) or "-"))

    print("\n== 4. packages and leftovers")
    if pi:
        print(sh([pi, "list"]))
    print("installer leftovers:", sorted(x.name for x in AGENT.glob(".v1-*")) if AGENT.is_dir() else [])

    print("\n== 5. ~/pinew")
    here = Path.home() / "pinew"
    print("exists:", here.is_dir())
    if (here / ".git").exists():
        print("commit:", sh(["git", "-C", str(here), "log", "-1", "--format=%h %s"]))
        print("dirty :", sh(["git", "-C", str(here), "status", "--porcelain"]) or "clean")


if __name__ == "__main__":
    main()
