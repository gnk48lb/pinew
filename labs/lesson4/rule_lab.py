#!/usr/bin/env python3
"""rule_lab.py —— “规则放哪儿”实验室：同一条规则，放进 AGENTS.md、技能、提示模板，pi 发出去的请求有什么不同，模型听不听。

用法：
  python3 rule_lab.py                    先量“每样东西占多少 token”（26 个小请求），再测六条路径 × 15 次（每次连问 3 句，约 10–15 分钟）
  python3 rule_lab.py --only anatomy     只做第一部分（约 1 分钟，几乎不花钱）
  python3 rule_lab.py --only carriers    只做第二部分

一、anatomy：用真 pi 发 13 种配置各 2 次，读 pi 自己的 JSON 输出里的“系统提示分段”，并用网关报的总输入 token 做减法，
   量出：工具、AGENTS.md、一条规则、一个技能清单、隐藏技能、提示模板各占多少 token（和缓存无关）。
二、carriers：同一条规则（commit 信息要以 [sg] 开头），用六种方式交给模型：
   none        对照：不给规则
   agents      写进 AGENTS.md（接在 v1 的 8 条后面，第 9 条）
   skill-good  放进一个技能，描述写得清楚（commit-style）：模型要自己决定去读它
   skill-vague 同一个正文，描述写得含糊（team-notes）
   skill-cmd   隐藏技能，用 /skill:commit-style 点名
   template    提示模板，用 /commit-msg 调用
   每次运行是一个 pi 进程连问 3 句：① 写一条 commit 信息（规则靠上面那种方式送到）② 一个无关的问题 ③ 再写一条 commit 信息（不再点名）。
   记下：模型有没有照规则写（第 1 句和第 3 句），有没有自己去读技能，每句的 token 和花费。

用你 ~/.pi/agent 里的网关、模型、密钥和价格，只给模型 read 工具。预计花 0.04–0.10 美元，花费到 --max-cost（默认 $0.30）就自动停。
"""
import argparse, hashlib, json, math, os, re, shutil, signal, statistics, subprocess, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))

# ───────────────────────────── 设计（路线图 11-07 那一行写的就是这一版；改一个字，下面的“设计指纹”就变） ─────────────────────────────
RULE = ("Commit messages in this project start with the tag [sg] followed by one space, then a lowercase imperative summary "
        "of at most 50 characters. Example: [sg] drop the unused feature flag")
REQ = "Write a commit message for this change (reply with only the message): "
PROBE = "Reply with the single word: OK"
CHANGES = [
    "the login page redirected to /home instead of /dashboard after sign-in; fixed the redirect target in auth/redirect.py",
    "renamed the config key max_retry to max_retries across the code base",
    "added a unit test for the leap-year case in the date parser",
    "raised the HTTP client timeout in the sync job from 10 to 30 seconds",
]
T2 = "In one sentence: what is the difference between git merge and git rebase?"
HIGH, LOW = 0.85, 0.5           # 档：照做率 ≥ 0.85 记“高”，≤ 0.5 记“低”，其间记“中”
ALPHA = 0.05                    # 两条路径“有差别”的门槛：Fisher 精确检验，双侧
N_DEFAULT, N_NONE = 15, 8

SKILLS = {   # 键 -> (目录名, name, description, 是否隐藏, 标题)
    "good": ("commit-style", "commit-style", "Use when writing a git commit message for this project. Contains the required commit message format.", False, "Commit messages"),
    "vague": ("team-notes", "team-notes", "Assorted notes for working in this repository.", False, "Team notes"),
    "hidden": ("commit-style", "commit-style", "Use when writing a git commit message for this project. Contains the required commit message format.", True, "Commit messages"),
}
TEMPLATE = ("---\ndescription: Write a commit message for a change\nargument-hint: \"<what changed>\"\n---\n"
            + RULE + "\n\n" + REQ + "$ARGUMENTS\n")

#            工具          AGENTS.md           技能        提示模板    第一句怎么发
ARM_SPEC = {
    "none":        dict(tools="read", ctx="v1",     skills=[],         template=False, first="req"),
    "agents":      dict(tools="read", ctx="v1rule", skills=[],         template=False, first="req"),
    "skill-good":  dict(tools="read", ctx="v1",     skills=["good"],   template=False, first="req"),
    "skill-vague": dict(tools="read", ctx="v1",     skills=["vague"],  template=False, first="req"),
    "skill-cmd":   dict(tools="read", ctx="v1",     skills=["hidden"], template=False, first="skillcmd"),
    "template":    dict(tools="read", ctx="v1",     skills=[],         template=True,  first="tpl"),
}
ARMS = tuple(ARM_SPEC)
ANATOMY = [   # (名字, 配置)；ctx: none = 不读 AGENTS.md（-nc）
    ("no-tools",        dict(tools="",                      ctx="none",   skills=[],                template=False, first="probe")),
    ("read",            dict(tools="read",                  ctx="none",   skills=[],                template=False, first="probe")),
    ("4tools",          dict(tools="read,bash,edit,write",  ctx="none",   skills=[],                template=False, first="probe")),
    ("agents-v1",       dict(tools="read",                  ctx="v1",     skills=[],                template=False, first="probe")),
    ("agents-v1+rule",  dict(tools="read",                  ctx="v1rule", skills=[],                template=False, first="probe")),
    ("skill-1",         dict(tools="read",                  ctx="none",   skills=["good"],          template=False, first="probe")),
    ("skill-2",         dict(tools="read",                  ctx="none",   skills=["good", "vague"], template=False, first="probe")),
    ("skill-hidden",    dict(tools="read",                  ctx="none",   skills=["hidden"],        template=False, first="probe")),
    ("template-listed", dict(tools="read",                  ctx="none",   skills=[],                template=True,  first="probe")),
    ("plain-request",   dict(tools="read",                  ctx="none",   skills=[],                template=False, first="req")),
    ("template-call",   dict(tools="read",                  ctx="none",   skills=[],                template=True,  first="tpl")),
    ("skill-call",      dict(tools="read",                  ctx="none",   skills=["hidden"],        template=False, first="skillcmd")),
    ("all-in-one",      dict(tools="read,bash,edit,write",  ctx="v1rule", skills=["good"],          template=True,  first="probe")),
]
ANATOMY_SPEC = dict(ANATOMY)
ANATOMY_REPS = 2

LOCK = threading.Lock()
STOP = threading.Event()
PROCS = set()
STATE = {"done": 0, "spent": 0.0, "skipped": 0}


def say(*a):
    with LOCK:
        print(*a, flush=True)


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def v1_agents():
    return open(os.path.join(HERE, "v1-AGENTS.md"), encoding="utf-8").read()


def agents_text(with_rule):
    base = v1_agents()
    return base.rstrip("\n") + "\n- " + RULE + "\n" if with_rule else base


def skill_md(key):
    _, name, desc, hidden, title = SKILLS[key]
    fm = ["---", "name: " + name, "description: " + desc] + (["disable-model-invocation: true"] if hidden else []) + ["---"]
    return "\n".join(fm) + "\n# %s\n\n%s\n" % (title, RULE)


def design_id():
    blob = json.dumps({"rule": RULE, "req": REQ, "probe": PROBE, "changes": CHANGES, "t2": T2, "v1": sha(v1_agents()),
                       "skills": {k: skill_md(k) for k in SKILLS}, "template": TEMPLATE, "arms": ARM_SPEC, "anatomy": ANATOMY,
                       "bands": [HIGH, LOW, ALPHA], "n": [N_DEFAULT, N_NONE, ANATOMY_REPS]}, sort_keys=True, ensure_ascii=False)
    return sha(blob)[:12]


def first_message(kind, change):
    return {"probe": PROBE, "req": REQ + change, "tpl": "/commit-msg " + change, "skillcmd": "/skill:commit-style " + REQ + change}[kind]


def messages_for(arm, rep):
    a, b = CHANGES[rep % len(CHANGES)], CHANGES[(rep + 2) % len(CHANGES)]
    return [first_message(ARM_SPEC[arm]["first"], a), T2, REQ + b]


# ───────────────────────────── 价格、配置 ─────────────────────────────
def dollars(p, usage):
    if not p:
        return None
    return ((usage.get("input", 0) or 0) * p["input"] + (usage.get("cacheRead", 0) or 0) * p["cacheRead"]
            + (usage.get("cacheWrite", 0) or 0) * p["cacheWrite"] + (usage.get("output", 0) or 0) * p["output"]) / 1e6


def agent_dir():
    return os.environ.get("PI_CODING_AGENT_DIR") or os.path.expanduser("~/.pi/agent")


def read_json(path):
    try:
        return json.load(open(path, encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}


def pick_model(model_arg):
    """--model provider/模型id，否则 settings.json 里的默认模型。返回 (配置目录, models.json 内容, provider 名, 模型 id)。"""
    d = agent_dir()
    models, settings = read_json(os.path.join(d, "models.json")), read_json(os.path.join(d, "settings.json"))
    providers = models.get("providers", {})
    if model_arg:
        name, _, mid = model_arg.partition("/")
    else:
        name, mid = settings.get("defaultProvider"), settings.get("defaultModel")
    name = name or next(iter(providers), None)
    p = providers.get(name) or {}
    mid = mid or (p.get("models") or [{}])[0].get("id")
    if not any(x.get("id") == mid for x in p.get("models") or []):
        sys.exit("在 %s/models.json 里找不到 provider %s 下的模型 %s。可以用 --model provider/模型id 指定" % (d, name, mid))
    return d, models, name, mid


def model_prices(models, name, mid):
    ent = next(x for x in models["providers"][name]["models"] if x.get("id") == mid)
    c = ent.get("cost") or {}
    return {k: float(c.get(k) or 0) for k in ("input", "output", "cacheRead", "cacheWrite")} if c else None


def write_private(path, text, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def make_agent_dir(root, label, src, models, name, mid, agents):
    """root 下的一份私有 pi 配置目录：models.json、auth.json 的副本 + 最小的 settings.json（+ AGENTS.md）。你真正的 ~/.pi/agent 一个字不动。"""
    d = os.path.join(root, label)
    os.makedirs(d, mode=0o700)
    write_private(os.path.join(d, "models.json"), json.dumps(models, ensure_ascii=False, indent=1))
    write_private(os.path.join(d, "settings.json"), json.dumps({"defaultProvider": name, "defaultModel": mid, "defaultThinkingLevel": "low"}))
    if os.path.exists(os.path.join(src, "auth.json")):
        write_private(os.path.join(d, "auth.json"), open(os.path.join(src, "auth.json"), encoding="utf-8-sig").read())
    if agents is not None:
        write_private(os.path.join(d, "AGENTS.md"), agents, 0o644)
    return d


def make_resources(root):
    res = {}
    for key, (dirname, *_rest) in SKILLS.items():
        d = os.path.join(root, "res", key, dirname)
        os.makedirs(d)
        write_private(os.path.join(d, "SKILL.md"), skill_md(key), 0o644)
        res[key] = d
    os.makedirs(os.path.join(root, "res", "prompts"))
    res["template"] = os.path.join(root, "res", "prompts", "commit-msg.md")
    write_private(res["template"], TEMPLATE, 0o644)
    return res


# ───────────────────────────── 读 pi 的输出 ─────────────────────────────
def parse_events(stdout):
    """pi --mode json 的输出 -> (系统提示事件, 每句话一项的列表, 最后一句有没有收尾)。
    pi 把每条用户消息的运行框在 agent_start … agent_end 之间；里面每个 turn_end 是一次网关请求；tool_execution_start 是工具调用。"""
    sysmsg, turns, cur = None, [], None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t = e.get("type")
        if t == "message_start" and sysmsg is None and (e.get("message") or {}).get("role") == "system":
            sysmsg = e["message"]
        elif t == "agent_start":
            cur = {"msgs": [], "reads": []}
        elif t == "tool_execution_start" and cur is not None and e.get("toolName") == "read":
            cur["reads"].append(str((e.get("args") or {}).get("path") or ""))
        elif t == "turn_end" and cur is not None:
            cur["msgs"].append(e.get("message") or {})
        elif t == "agent_end" and cur is not None:
            turns.append(cur)
            cur = None
    return sysmsg, turns, cur is None


def turn_numbers(turn, prices):
    """一句话的用量：这句里所有请求加起来。"""
    inp = cached = out = 0
    cost = 0.0
    for m in turn["msgs"]:
        u = m.get("usage") or {}
        cr = u.get("cacheRead", 0) or 0
        inp += (u.get("input", 0) or 0) + cr + (u.get("cacheWrite", 0) or 0)
        cached += cr
        out += u.get("output", 0) or 0
        cost += dollars(prices, u) or 0.0
    last = turn["msgs"][-1] if turn["msgs"] else {}
    text = "".join(b.get("text", "") for b in (last.get("content") or []) if isinstance(b, dict) and b.get("type") == "text")
    return {"requests": len(turn["msgs"]), "inp": inp, "cached": cached, "out": out, "cost": cost if prices else None,
            "text": text, "stop": last.get("stopReason"), "err": (last.get("errorMessage") or last.get("stopReason")) if last.get("stopReason") in ("error", "aborted") else None}


FENCE = re.compile(r"^\s*```[\w+-]*\s*$")


def first_line(text):
    for ln in (text or "").splitlines():
        if ln.strip() and not FENCE.match(ln):
            return ln
    return ""


LEAD = re.compile("^[\\s`\"'\u201c\u201d\u2018\u2019>*\\-]+")      # 空白、反引号、引号（直的和弯的）、> * -


def comply(text):
    """事先写好的定义：回复的第一行（跳过空行和 ``` 围栏行），去掉开头的空白、反引号、引号、> * - 之后，以“[sg] ”加一个非空字符开头。"""
    return bool(re.match(r"\[sg\] \S", LEAD.sub("", first_line(text))))


def skill_read(paths):
    return any(os.path.basename(p) == "SKILL.md" for p in paths)


def short_path(p):
    return "/".join(p.replace("\\", "/").split("/")[-2:]) if os.path.basename(p) == "SKILL.md" else p


# ───────────────────────────── 核对：真正发出去的系统提示，是不是这条路径该有的 ─────────────────────────────
def expected_skill_names(spec):
    return sorted(SKILLS[s][1] for s in spec["skills"] if not SKILLS[s][3])


def verify(spec, sysmsg, root):
    if not sysmsg:
        return ["pi 的 JSON 输出里没有系统提示事件（role=system 的 message_start）"]
    probs = []
    sec = sysmsg.get("sections") or {}
    pc = sec.get("project_context") or ""
    if spec["ctx"] == "none":
        if pc.strip():
            probs.append("这条路径不该读 AGENTS.md，但系统提示里有 project_context 段")
    else:
        n = pc.count("<project_instructions")
        if n != 1:
            probs.append("project_context 里应当正好 1 份 AGENTS.md，实际 %d 份（别处有 AGENTS.md 被读进来了？）" % n)
        elif "Working agreements" not in pc:
            probs.append("读进来的 AGENTS.md 不是实验用的那份")
        elif root and root not in pc:
            probs.append("读进来的 AGENTS.md 不在实验的私有目录里")
        if ("[sg]" in pc) != (spec["ctx"] == "v1rule"):
            probs.append("AGENTS.md 里%s规则，但这条路径应当%s" % ("有" if "[sg]" in pc else "没有", "有" if spec["ctx"] == "v1rule" else "没有"))
    names = sorted(re.findall(r"<name>(.*?)</name>", sec.get("skills") or ""))
    if names != expected_skill_names(spec):
        probs.append("技能清单是 %s，应当是 %s" % (names or "空", expected_skill_names(spec) or "空"))
    if "toolsAdded" in sysmsg:
        tools = sorted(t.get("name") for t in sysmsg.get("toolsAdded") or [])
        want = sorted(x for x in spec["tools"].split(",") if x)
        if tools != want:
            probs.append("工具是 %s，应当是 %s" % (tools or "无", want or "无"))
    for k, v in sec.items():
        if k != "project_context" and "[sg]" in (v or ""):
            probs.append("规则出现在系统提示的 %s 段里" % k)
    return probs


# ───────────────────────────── 跑 pi ─────────────────────────────
def pi_argv(ctx, spec, messages):
    a = ["-p"] + list(messages) + ["--mode", "json", "--no-session", "--no-extensions", "-ns", "-np", "--thinking", "low"]
    a += ["--tools", spec["tools"]] if spec["tools"] else ["--no-tools"]
    if spec["ctx"] == "none":
        a += ["-nc"]
    for s in spec["skills"]:
        a += ["--skill", ctx["res"][s]]
    if spec["template"]:
        a += ["--prompt-template", ctx["res"]["template"]]
    return a


def kill_tree(p):
    """杀掉 pi 和它可能起的子进程：每个 pi 单独一个进程组，按组杀（只杀 pi 本身的话，子进程还攥着管道，会一直等下去）。"""
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        try:
            p.kill()
        except OSError:
            pass


def run_pi(ctx, spec, messages):
    """-> (stdout, 错误说明)；一个 pi 进程，一路问完 messages。"""
    env = dict(os.environ, PI_CODING_AGENT_DIR=ctx["dirs"]["rule" if spec["ctx"] == "v1rule" else "base"])
    try:
        p = subprocess.Popen([ctx["pi"]] + pi_argv(ctx, spec, messages), cwd=ctx["work"], env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        return None, "%s: %s" % (type(e).__name__, e)
    with LOCK:
        PROCS.add(p)
    try:
        out, err = p.communicate(timeout=ctx["timeout"])
    except subprocess.TimeoutExpired:
        kill_tree(p)
        p.communicate()
        return None, "超时（%d 秒）" % ctx["timeout"]
    finally:
        with LOCK:
            PROCS.discard(p)
    return (out, err, p.returncode), None


def attempt(ctx, spec, messages):
    """-> (系统提示事件, 每句数字的列表, 错误说明)"""
    res, err = run_pi(ctx, spec, messages)
    if err:
        return None, None, err
    out, errtxt, rc = res
    sysmsg, turns, closed = parse_events(out)
    nums = [turn_numbers(t, ctx["prices"]) for t in turns]
    if len(turns) < len(messages) or not closed:
        return sysmsg, None, "退出码 %s，只完成了 %d/%d 句：%s" % (rc, len(turns), len(messages), (errtxt or out).strip()[-200:])
    bad = [n["err"] for n in nums if n["err"]]
    if bad:
        return sysmsg, None, "pi 报错：%s" % str(bad[0]).strip()[:200]
    return sysmsg, (turns, nums), None


def over_cap(ctx):
    with LOCK:
        if ctx["cap"] and STATE["spent"] >= ctx["cap"]:
            STATE["skipped"] += 1
            return True
    return False


def safe(fn, job, ctx):
    try:
        return fn(job, ctx)
    except Exception as e:  # noqa  一次运行里的意外不该拖垮整个实验：记成失败
        with LOCK:
            STATE["done"] += 1
        say("[%3d/%d] %-16s 失败：脚本内部错误 %s: %s" % (STATE["done"], ctx["total"], " ".join(map(str, job)), type(e).__name__, e))
        return {"kind": "run" if job[0] in ARMS else "anatomy", ("arm" if job[0] in ARMS else "config"): job[0], "rep": job[1], "err": "脚本内部错误 %s: %s" % (type(e).__name__, e)}


def do_anatomy(job, ctx):
    name, rep = job
    spec = ANATOMY_SPEC[name]
    if STOP.is_set() or over_cap(ctx):
        return None
    sysmsg = nums = err = None
    for att in (1, 2, 3):
        sysmsg, got, err = attempt(ctx, spec, [first_message(spec["first"], CHANGES[0])])
        if got or STOP.is_set():
            nums = got[1] if got else None
            break
        if att < 3:
            time.sleep(3)
    base = {"kind": "anatomy", "config": name, "rep": rep}
    if STOP.is_set() and not nums:
        return None
    if not nums:
        with LOCK:
            STATE["done"] += 1
            n = STATE["done"]
        say("[%3d/%d] anatomy %-16s #%d  失败：%s" % (n, ctx["total"], name, rep, err))
        return dict(base, err=(err or "?")[:200])
    nu = nums[0]
    probs = verify(spec, sysmsg, ctx["root"])
    sec = {k: len(v) for k, v in ((sysmsg or {}).get("sections") or {}).items() if isinstance(v, str)}
    rec = dict(base, inp=nu["inp"], cached=nu["cached"], out=nu["out"], requests=nu["requests"], cost=None if nu["cost"] is None else round(nu["cost"], 7),
               sections=sec, tools_chars=len(json.dumps((sysmsg or {}).get("toolsAdded") or [])), problems=probs, reply=nu["text"].strip()[:40])
    with LOCK:
        STATE["done"] += 1
        STATE["spent"] += nu["cost"] or 0
        n = STATE["done"]
    say("[%3d/%d] anatomy %-16s #%d  总输入 %5d token（命中 %d）%s%s" % (n, ctx["total"], name, rep, nu["inp"], nu["cached"],
                                                                      "  ⚠ 这一句用了 %d 个请求（模型调了工具？），总输入不是单个请求的" % nu["requests"] if nu["requests"] != 1 else "",
                                                                      "  ✗ " + "；".join(probs) if probs else ""))
    return rec


def do_run(job, ctx):
    arm, rep = job
    spec = ARM_SPEC[arm]
    msgs = messages_for(arm, rep)
    if STOP.is_set() or over_cap(ctx):
        return None
    t0 = time.time()
    sysmsg = got = err = None
    tries = 0
    for tries in (1, 2, 3):                     # 事先写好的规则：基础设施失败最多重跑 2 次
        sysmsg, got, err = attempt(ctx, spec, msgs)
        if got or STOP.is_set():
            break
        if sysmsg and verify(spec, sysmsg, ctx["root"]):
            break                                # 系统提示不对是确定性的，重跑没用
        if tries < 3:
            time.sleep(3)
    base = {"kind": "run", "arm": arm, "rep": rep}
    if not got and STOP.is_set():
        return None                              # Ctrl+C 时被杀掉的运行不算失败，也不进数据
    if sysmsg:
        probs = verify(spec, sysmsg, ctx["root"])
        if probs:
            got, err = None, "系统提示和这条路径该有的不一样：" + "；".join(probs)
    elif got:
        got, err = None, "pi 的 JSON 输出里没有系统提示事件（role=system 的 message_start），没法核对这次运行"
    if not got:
        with LOCK:
            STATE["done"] += 1
            n = STATE["done"]
        say("[%3d/%d] %-12s #%-2d 失败：%s" % (n, ctx["total"], arm, rep, err))
        return dict(base, err=(err or "?")[:300], tries=tries)
    turns, nums = got
    rows = []
    for i, (t, nu) in enumerate(zip(turns, nums), 1):
        rows.append({"i": i, "requests": nu["requests"], "inp": nu["inp"], "cached": nu["cached"], "out": nu["out"],
                     "cost": None if nu["cost"] is None else round(nu["cost"], 7), "comply": comply(nu["text"]) if i != 2 else None,
                     "leak": "[sg]" in nu["text"], "skill_read": skill_read(t["reads"]), "reads": [short_path(p) for p in t["reads"]],
                     "first_line": first_line(nu["text"])[:120], "text": nu["text"].strip()[:400], "stop": nu["stop"]})
    cost = sum(r["cost"] or 0 for r in rows)
    rec = dict(base, turns=rows, cost=None if not ctx["prices"] else round(cost, 7), secs=round(time.time() - t0, 1), tries=tries)
    with LOCK:
        STATE["done"] += 1
        STATE["spent"] += cost
        n = STATE["done"]
    say("[%3d/%d] %-12s #%-2d 第1句 %s%s  第3句 %s%s  %4.0f 秒  %s" % (
        n, ctx["total"], arm, rep, "照做" if rows[0]["comply"] else "没照做", "（读了技能）" if rows[0]["skill_read"] else "",
        "照做" if rows[2]["comply"] else "没照做", "（读了技能）" if rows[2]["skill_read"] else "", rec["secs"],
        "" if not ctx["prices"] else "$%.6f" % cost))
    return rec


# ───────────────────────────── 统计 ─────────────────────────────
def band(x, n):
    if n <= 0:
        return "—"
    return "高" if x / n >= HIGH - 1e-9 else ("低" if x / n <= LOW + 1e-9 else "中")


def fisher_two_sided(a, n1, b, n2):
    """a/n1 对 b/n2 的双侧 Fisher 精确检验 p 值。"""
    k, N = a + b, n1 + n2

    def h(x):
        return math.comb(n1, x) * math.comb(n2, k - x) / math.comb(N, k)
    obs = h(a)
    return min(1.0, sum(h(x) for x in range(max(0, k - n2), min(n1, k) + 1) if h(x) <= obs * (1 + 1e-9)))


def fmt_p(p):
    return "p<0.001" if p < 0.001 else "p=%.3f" % p


def med(v):
    return statistics.median(v) if v else None


def good_runs(recs):
    return [r for r in recs if r and r.get("kind") == "run" and "err" not in r]


def count(rs, turn, key):
    vals = [r["turns"][turn][key] for r in rs]
    return sum(1 for v in vals if v), len(vals)


# ───────────────────────────── 汇总 ─────────────────────────────
def anatomy_summary(recs, ctx):
    g = [r for r in recs if r and r.get("kind") == "anatomy" and "err" not in r]
    if not g:
        say("\n== 第一部分没有拿到数据")
        return False
    bycfg = {}
    for r in g:
        bycfg.setdefault(r["config"], []).append(r)
    ok = True
    full = bycfg.get("all-in-one")
    if full:
        r = full[0]
        say("\n== 汇总一：你这个 pi 实际发出去的系统提示，分成这几段（字符数；顺序就是请求里的顺序）")
        tot = 0
        for k, v in r["sections"].items():
            say("  %-16s %6d 字符" % (k, v))
            tot += v
        say("  %-16s %6d 字符（各段之间还有空行）；工具定义约 %d 字符（在系统提示之外，是请求里单独的一块）" % ("合计", tot, r["tools_chars"]))
        say("  这个配置（4 个工具 + AGENTS.md + 1 条规则 + 1 个可见技能）的总输入是 %d token，平均 %.1f 字符/token" % (
            r["inp"], (tot + r["tools_chars"]) / max(1, r["inp"])))
    say("\n== 汇总二：每样东西让每个请求多几个 token（两个配置的总输入之差，和缓存无关；每个配置发了 %d 次）" % ANATOMY_REPS)
    say("%-34s %-9s %-16s %-14s %-14s" % ("多出来的东西（比哪个多）", "多 token", "两次分别是", "命中缓存的请求", "冷启动那一次"))
    p = ctx["prices"]
    rows = [("工具：只开 read（比不开工具）", "read", "no-tools"),
            ("工具：再加 bash/edit/write（比只开 read）", "4tools", "read"),
            ("AGENTS.md：v1 的 8 条（比不读）", "agents-v1", "read"),
            ("再加 1 条规则（比 v1 的 8 条）", "agents-v1+rule", "agents-v1"),
            ("1 个可见技能的清单（比没有）", "skill-1", "read"),
            ("再多 1 个可见技能（比 1 个）", "skill-2", "skill-1"),
            ("1 个隐藏技能（比没有）", "skill-hidden", "read"),
            ("放着 1 个提示模板、没调用（比没有）", "template-listed", "read"),
            ("用 /commit-msg 调用模板（比普通请求）", "template-call", "plain-request"),
            ("用 /skill:commit-style 点名（比普通请求）", "skill-call", "plain-request")]
    for label, a, b in rows:
        ra, rb = bycfg.get(a), bycfg.get(b)
        if not ra or not rb:
            say("%-34s 没有数据（%s）" % (label, a if not ra else b))
            continue
        ds = [x["inp"] - y["inp"] for x, y in zip(sorted(ra, key=lambda r: r["rep"]), sorted(rb, key=lambda r: r["rep"]))]
        same = len(set(ds)) == 1
        d = ds[0]
        say("%-34s %-9s %-16s %-14s %-14s" % (label, "%+d" % d, "/".join("%+d" % x for x in ds) + ("" if same else "  ← 两次不同"),
                                              "—" if not p else "$%.7f" % (d * p["cacheRead"] / 1e6), "—" if not p else "$%.6f" % (d * p["input"] / 1e6)))
    for name, rs in bycfg.items():
        tots = [x["inp"] for x in rs]
        if len(set(tots)) > 1:
            say("  注意：配置 %s 的两次总输入不同：%s（只标出来，不影响第二部分；打分时取较小的差）" % (name, tots))
        multi = [x["requests"] for x in rs if x.get("requests", 1) != 1]
        if multi:
            say("  ⚠ 配置 %s 有 %d 次请求数不是 1（%s）：这一行的差不是纯粹的提示长度之差，只读不算" % (name, len(multi), multi))
    bad = [(r["config"], r["rep"], r["problems"]) for r in g if r["problems"]]
    if bad:
        say("\n  ✗ 有 %d 次请求发出去的系统提示和该配置应有的不一样（下面的第二部分不会开始，除非加 --force）：" % len(bad))
        for c, rep, pr in bad[:6]:
            say("    %s #%d：%s" % (c, rep, "；".join(pr)))
        ok = False
    say("\n  怎么读：“比较”都是同一个模型的同一个分词器量出来的总输入之差，所以是精确的，不是估计。命中缓存的请求只按缓存读价收这些 token（你的价格：$%s/百万），"
        "冷启动那一次按新输入价收（$%s/百万）。" % (("%g" % p["cacheRead"]) if p else "?", ("%g" % p["input"]) if p else "?"))
    return ok and not bad


def carriers_summary(recs, ctx, total):
    g = good_runs(recs)
    bad = [r for r in recs if r and r.get("kind") == "run" and "err" in r]
    say("\n== 汇总三：六条路径，每条的照做率（x/n 和档；高 = ≥%.2f，低 = ≤%.2f，中 = 其间）" % (HIGH, LOW))
    say("%-12s %-5s %-12s %-12s %-12s %-12s %-9s %-9s %-10s" % ("路径", "完成", "第1句照做", "第3句照做", "第1句读技能", "第3句读技能", "第2句漏用", "请求/次", "花费/次"))
    by = {}
    for a in ARMS:
        rs = [r for r in g if r["arm"] == a]
        by[a] = rs
        if not rs:
            say("%-12s %-5s 没有数据" % (a, "0"))
            continue
        c1, n = count(rs, 0, "comply")
        c3, _ = count(rs, 2, "comply")
        r1, _ = count(rs, 0, "skill_read")
        r3, _ = count(rs, 2, "skill_read")
        lk, _ = count(rs, 1, "leak")
        rq = med([sum(t["requests"] for t in r["turns"]) for r in rs])
        cs = med([r["cost"] for r in rs if r["cost"] is not None])
        skillish = a in ("skill-good", "skill-vague")
        bd = (lambda x, k: "") if a == "none" else band
        say("%-12s %-5d %-12s %-12s %-12s %-12s %-9s %-9s %-10s" % (
            a, n, ("%d/%d %s" % (c1, n, bd(c1, n))).strip(), ("%d/%d %s" % (c3, n, bd(c3, n))).strip(),
            ("%d/%d" % (r1, n)) if skillish else "—", ("%d/%d" % (r3, n)) if skillish else "—", "%d/%d" % (lk, n), "%g" % rq,
            "—" if cs is None else "$%.6f" % cs))
    say("  （对照 none 只跑了 %d 次，不分档，只看有没有。第2句漏用 = 无关的问题的回答里也出现了 [sg]。）" % N_NONE)
    skill_arms = [a for a in ("skill-good", "skill-vague") if by.get(a)]
    if skill_arms:
        say("\n== 汇总四：自己加载技能的两条路径：读没读技能 × 第1句照不照做")
        for a in skill_arms:
            rs = by[a]
            rd = [r for r in rs if r["turns"][0]["skill_read"]]
            nr = [r for r in rs if not r["turns"][0]["skill_read"]]
            say("  %-12s 第1句读了技能的 %d 次里照做 %d 次；没读的 %d 次里照做 %d 次" % (
                a, len(rd), sum(1 for r in rd if r["turns"][0]["comply"]), len(nr), sum(1 for r in nr if r["turns"][0]["comply"])))
            r3 = [r for r in rs if r["turns"][0]["skill_read"]]
            if r3:
                say("  %-12s 第1句读了技能的 %d 次里，第3句又读一次的 %d 次，第3句照做的 %d 次" % (
                    "", len(r3), sum(1 for r in r3 if r["turns"][2]["skill_read"]), sum(1 for r in r3 if r["turns"][2]["comply"])))
    say("\n== 汇总五：两条路径之间有没有差别（Fisher 精确检验，双侧；p < %g 记作“有差别”）" % ALPHA)
    pairs = [("第1句照做", 0, "comply", ("agents", "template")), ("第1句照做", 0, "comply", ("agents", "skill-cmd")),
             ("第1句照做", 0, "comply", ("template", "skill-cmd")), ("第1句读技能", 0, "skill_read", ("skill-good", "skill-vague")),
             ("第1句照做", 0, "comply", ("skill-good", "skill-vague")), ("第3句照做", 2, "comply", ("agents", "template")),
             ("第3句照做", 2, "comply", ("agents", "skill-cmd"))]
    for label, ti, key, (a, b) in pairs:
        if by.get(a) and by.get(b):
            x, n1 = count(by[a], ti, key)
            y, n2 = count(by[b], ti, key)
            p = fisher_two_sided(x, n1, y, n2)
            say("  %-10s %-11s %2d/%-2d  对  %-11s %2d/%-2d   %s  %s" % (label, a, x, n1, b, y, n2, fmt_p(p), "有差别" if p < ALPHA else "看不出差别"))
    say("\n  失败 %d 次（基础设施失败已各重试最多 2 次）；完成 %d 次运行，计划 %d 次" % (len(bad), len(g), total))
    for r in bad[:5]:
        say("    失败样例：%s #%d：%s" % (r["arm"], r["rep"], r["err"]))
    allc = sum(r["cost"] or 0 for r in g)
    if ctx["prices"]:
        say("  这部分的花费合计 $%.4f" % allc)
    say("\n  怎么读：每格只有 15 次，温度固定为 1。差几次不算差别；这个设计只分得开“基本都照做”（≥13/15）和“一半都不到”（≤7/15）。"
        "“读技能”指模型自己用 read 打开了那个技能的 SKILL.md；第3句不再点名、也不重复规则。")


# ───────────────────────────── 主程序 ─────────────────────────────
def pi_bin():
    b = os.environ.get("PI_BIN") or shutil.which("pi")
    if not b or not (os.access(b, os.X_OK) or shutil.which(b)):
        sys.exit("找不到可以运行的 pi（%s）。可以先 export PI_BIN=/路径/pi" % (b or "PATH 里没有 pi"))
    return b


def main():
    ap = argparse.ArgumentParser(description="规则放哪儿实验室")
    ap.add_argument("--only", choices=("all", "anatomy", "carriers"), default="all", help="只做哪一部分（默认两部分都做）")
    ap.add_argument("--arms", default=",".join(ARMS), help="第二部分测哪几条路径，逗号分隔（默认全部）")
    ap.add_argument("--reps", type=int, default=N_DEFAULT, help="每条路径跑几次（默认 %d；别改，打分规则是按这个定的）" % N_DEFAULT)
    ap.add_argument("--parallel", type=int, default=3, help="同时跑几个（默认 3；1 = 一个一个跑）")
    ap.add_argument("--max-cost", type=float, default=0.30, help="花费上限（美元，默认 0.30）：超过就不再发新请求；0 = 不设上限")
    ap.add_argument("--timeout", type=int, default=300, help="一次运行（连问 3 句）最多等几秒（默认 300）")
    ap.add_argument("--model", help="provider/模型id；默认用 pi 的 settings.json 里的默认模型")
    ap.add_argument("--force", action="store_true", help="第一部分核对出系统提示不对时也继续第二部分")
    a = ap.parse_args()
    arms = [x.strip() for x in a.arms.split(",") if x.strip()]
    for x in arms:
        if x not in ARMS:
            sys.exit("--arms 里只能有 %s" % "、".join(ARMS))
    src, models, name, mid = pick_model(a.model)
    prices = model_prices(models, name, mid)
    pi = pi_bin()
    root = tempfile.mkdtemp(prefix="rule-lab-")
    work = os.path.join(root, "work")
    os.makedirs(work)
    recs = []
    ctx = {"pi": pi, "root": root, "work": work, "prices": prices, "cap": a.max_cost, "timeout": a.timeout}
    try:
        ver = subprocess.run([pi, "--version"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
        ctx["dirs"] = {"base": make_agent_dir(root, "agent-base", src, models, name, mid, agents_text(False)),
                       "rule": make_agent_dir(root, "agent-rule", src, models, name, mid, agents_text(True))}
        ctx["res"] = make_resources(root)
        say("pi %s   模型 %s / %s   设计指纹 %s" % (ver, name, mid, design_id()))
        say("v1 的 AGENTS.md（实验里的第 1–8 条）sha256 %s…；规则：%s" % (sha(v1_agents())[:12], RULE))
        mine = os.path.join(src, "AGENTS.md")
        if os.path.exists(mine) and sha(open(mine, encoding="utf-8-sig").read()) != sha(v1_agents()):
            say("（提示：你 %s 里的 AGENTS.md 和实验用的 v1 那份不一样；实验用的是包里的那份，你的文件一个字不动。）" % src)
        say("私有目录 %s（你的 models.json、auth.json 的副本、实验用的 AGENTS.md / 技能 / 模板；跑完删除）。只给模型 read 工具。" % root)
        if prices:
            say("单价（每百万 token）：新输入 $%g，缓存读 $%g，输出 $%g" % (prices["input"], prices["cacheRead"], prices["output"]))
        else:
            say("（models.json 里这个模型没写价格，下面不算花费，也不设花费上限）")
            ctx["cap"] = 0
        n_an = len(ANATOMY) * ANATOMY_REPS if a.only in ("all", "anatomy") else 0
        jobs_c = [(arm, rep) for rep in range(1, a.reps + 1) for arm in arms if arm != "none" or rep <= N_NONE] if a.only in ("all", "carriers") else []
        ctx["total"] = n_an + len(jobs_c)
        say("共 %d 个请求/运行：第一部分 %d 个小请求，第二部分 %d 次运行（每次连问 3 句），同时跑 %d 个；花费上限 %s。第一行出来之前先等几秒。\n" % (
            ctx["total"], n_an, len(jobs_c), a.parallel, "$%.2f" % a.max_cost if ctx["cap"] else "无"))
        ex = ThreadPoolExecutor(max_workers=max(1, a.parallel))
        proceed = True
        try:
            if n_an:
                futs = [ex.submit(safe, do_anatomy, (n, rep), ctx) for rep in range(1, ANATOMY_REPS + 1) for n, _ in ANATOMY]
                for f in futs:
                    recs.append(f.result())
                recs = [r for r in recs if r]
                proceed = anatomy_summary(recs, ctx) or a.force
            if jobs_c and proceed and not STOP.is_set():
                futs = [ex.submit(safe, do_run, j, ctx) for j in jobs_c]
                for f in futs:
                    recs.append(f.result())
        except KeyboardInterrupt:
            STOP.set()
            say("\n（被中断，下面是已有的结果）")
            with LOCK:
                for pr in list(PROCS):
                    kill_tree(pr)
            ex.shutdown(wait=False, cancel_futures=True)
        recs = [r for r in recs if r]
        if jobs_c and any(r.get("kind") == "run" for r in recs):
            carriers_summary(recs, ctx, len(jobs_c))
        elif jobs_c and not proceed:
            say("\n第二部分没有开始：第一部分没有拿到数据，或者发现发出去的系统提示不对（上面有 ✗）。把上面的内容贴给我。")
        with LOCK:
            skipped = STATE["skipped"]
        if skipped:
            say("\n花费到了上限（$%.4f），剩下 %d 个没有跑；上面的汇总只含已经跑的。" % (STATE["spent"], skipped))
        if prices:
            say("本次花费 $%.4f" % STATE["spent"])
        say("\n== 数据（每个请求/每次运行一行，贴给我的时候别删）")
        for r in recs:
            say("数据 " + json.dumps(r, ensure_ascii=False, sort_keys=True))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if STOP.is_set():
        os._exit(130)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass
