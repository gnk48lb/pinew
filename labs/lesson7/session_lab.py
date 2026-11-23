#!/usr/bin/env python3
"""session_lab.py —— 第 7 课（长会话：压缩、恢复、分出去）的实验脚本。

用法（在一个空目录里运行，比如 ~/pi-practice/l7）：
  python3 ~/pi/lesson7/session_lab.py --dry-run                 不调模型：打印设计指纹、33 个 pi 进程的顺序和每个臂的设置
  python3 ~/pi/lesson7/session_lab.py 2>&1 | tee -ia ex7.log    正式跑（约 10–15 分钟，预计 $0.02–0.12）

设计在路线图 11-17 第 7 课那一行里先写好了（臂、次数、阈值、判定、预测），这个脚本只是把它跑出来；改了脚本里写死的设计，“设计指纹”就变。
你自己的 ~/.pi/agent 一个字不动：脚本在 /tmp 下起一个私有目录，放 models.json、auth.json 的副本和实验用的 settings.json，跑完删除。
"""
import argparse, hashlib, json, os, shutil, signal, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))

# ───────────────────────────── 设计（改一个字，“设计指纹”就变） ─────────────────────────────
WORDS = ("table river window garden paper silver morning engine bridge letter forest candle market ocean planet ticket pencil mirror "
         "basket violin harbor lantern pocket carpet rocket button meadow castle drawer hammer island jacket kitchen ladder magnet needle "
         "orange pillow quartz ribbon saddle thunder umbrella valley wagon yellow anchor blanket compass dolphin feather glacier horizon "
         "ivory jungle kettle lemon marble nickel orchid pepper quiver rabbit shadow timber useful velvet whistle zephyr copper dinner "
         "evening flower guitar helmet insect jewel kernel lamp mountain napkin office parcel quilt rubber sponge teapot uniform vessel "
         "walnut yogurt zipper apple bottle cheese desert elbow fabric ginger honey input jelly knife leather monkey noodle olive potato "
         "queen radio sugar tomato village winter").split()
FILLER_CHARS = 2800           # 每句里的填充文字长度（字符）
SENT_TAIL = "Reply with just the word OK."
THRESH = 4500                 # K1：上下文超过这么多 token 就压缩（脚本按你模型的 contextWindow 算出 reserveTokens）
KEEP = 1000                   # K1：压缩时保留的最近 token（keepRecentTokens）
SEED = 7
RETRIES = 2
TIMEOUT = 420
MIN_T, HI, LO = 1500, 0.7, 0.3          # “整段重付”：T ≥ 1,500 且 U ≥ 0.7 T；“正常”：U < 0.3 T
ARMS = {
    "K0": {"reps": 5, "n1": 7, "n2": 0, "mode": "none", "compaction": False, "doc": "不压缩，连问 7 句"},
    "K1": {"reps": 8, "n1": 7, "n2": 0, "mode": "none", "compaction": True, "doc": "压缩（阈值约 %d token，保留 %d），连问 7 句" % (THRESH, KEEP)},
    "R0": {"reps": 5, "n1": 3, "n2": 1, "mode": "continue", "compaction": False, "doc": "进程 1 问 3 句，进程 2 用 --continue 立刻再问 1 句"},
    "F0": {"reps": 5, "n1": 3, "n2": 1, "mode": "fork", "compaction": False, "doc": "进程 1 问 3 句，进程 2 用 --fork 立刻再问 1 句"},
}
PRICE_KEYS = ("input", "output", "cacheRead", "cacheWrite")


class Lcg:
    """自己写的伪随机数（不用 random 模块：算法在不同 Python 版本之间不保证一样；顺序和填充文字要能逐字节复现）。"""

    def __init__(self, seed):
        self.x = (int(seed) * 2862933555777941757 + 3037000493) % (1 << 64)

    def below(self, n):
        self.x = (self.x * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        return (self.x >> 33) % n

    def shuffle(self, items):
        for i in range(len(items) - 1, 0, -1):
            j = self.below(i + 1)
            items[i], items[j] = items[j], items[i]


def seed_of(text):
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:12], 16)


def filler(tag, i):
    g = Lcg(seed_of("%s/%d" % (tag, i)))
    out, n = [], 0
    while n < FILLER_CHARS:
        k = 6 + g.below(9)
        s = " ".join(WORDS[g.below(len(WORDS))] for _ in range(k))
        s = s[0].upper() + s[1:] + "."
        out.append(s)
        n += len(s) + 1
    return " ".join(out)


def message(tag, i):
    return "Note %d (session tag %s)\n\n%s\n\n%s" % (i, tag, filler(tag, i), SENT_TAIL)


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def design_id():
    blob = json.dumps({"arms": ARMS, "filler": [FILLER_CHARS, sha(filler("T", 1))[:12]], "tail": SENT_TAIL, "thresh": [THRESH, KEEP],
                       "seed": SEED, "retries": RETRIES, "timeout": TIMEOUT, "cls": [MIN_T, HI, LO], "words": sha(" ".join(WORDS))[:12]},
                      sort_keys=True, ensure_ascii=False)
    return sha(blob)[:12]


STOP = {"flag": False}
PROCS = set()
STATE = {"done": 0, "spent": 0.0, "skipped": 0}


def say(*a):
    print(*a, flush=True)


# ───────────────────────────── 价格、配置 ─────────────────────────────
def dollars(p, usage):
    if not p:
        return None
    return sum((usage.get(k, 0) or 0) * p[k] for k in PRICE_KEYS) / 1e6


def req_cost(usage, prices):
    c = (usage.get("cost") or {}).get("total")
    if isinstance(c, (int, float)) and not isinstance(c, bool) and (c > 0 or not prices):
        return float(c)
    return dollars(prices, usage)


def agent_dir():
    return os.environ.get("PI_CODING_AGENT_DIR") or os.path.expanduser("~/.pi/agent")


def read_json(path):
    try:
        return json.load(open(path, encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}


def pick_model(model_arg):
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


def model_entry(models, name, mid):
    return next(x for x in models["providers"][name]["models"] if x.get("id") == mid)


def model_prices(models, name, mid):
    c = model_entry(models, name, mid).get("cost") or {}
    return {k: float(c.get(k) or 0) for k in PRICE_KEYS} if c else None


def write_private(path, text, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def make_agent_dir(root, label, src, models, name, mid, compaction):
    """root 下的一份私有 pi 配置目录：models.json、auth.json 的副本 + 实验用的 settings.json。你真正的 ~/.pi/agent 一个字不动。"""
    d = os.path.join(root, label)
    os.makedirs(d, mode=0o700)
    write_private(os.path.join(d, "models.json"), json.dumps(models, ensure_ascii=False, indent=1))
    st = {"defaultProvider": name, "defaultModel": mid, "defaultThinkingLevel": "low", "cacheWarming": "off", "compaction": compaction}
    write_private(os.path.join(d, "settings.json"), json.dumps(st))
    if os.path.exists(os.path.join(src, "auth.json")):
        write_private(os.path.join(d, "auth.json"), open(os.path.join(src, "auth.json"), encoding="utf-8-sig").read())
    return d


def compaction_settings(spec, window):
    if not spec["compaction"]:
        return {"enabled": False}
    return {"enabled": True, "reserveTokens": max(0, int(window) - THRESH), "keepRecentTokens": KEEP}


# ───────────────────────────── 事件流 ─────────────────────────────
def request_numbers(m, prices):
    u = m.get("usage") or {}
    inp, cr, cw = [int(u.get(k) or 0) for k in ("input", "cacheRead", "cacheWrite")]
    T = inp + cr + cw
    return {"T": T, "C": cr, "U": T - cr, "in": inp, "cw": cw, "out": int(u.get("output") or 0), "cost": req_cost(u, prices) if T > 0 else None}


def parse_events(stdout, prices):
    """pi --mode json 的输出 -> 按出现顺序的 seq：{"t":"req", "k":第几句, ...数字} 和 {"t":"comp", ...}；session 的 id 和 parentSession；是否收尾。"""
    seq, k, closed, sid, parent, nend, errs = [], 0, True, None, None, 0, []
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t = e.get("type")
        if t == "session" and sid is None:
            sid, parent = e.get("id"), e.get("parentSession")
        elif t == "agent_start":
            k += 1
            closed = False
        elif t == "agent_end":
            nend += 1
            closed = True
        elif t == "turn_end":
            m = e.get("message") or {}
            if m.get("stopReason") in ("error", "aborted"):
                errs.append(str(m.get("errorMessage") or m.get("stopReason"))[:200])
            n = request_numbers(m, prices)
            n.update({"t": "req", "k": k, "stop": m.get("stopReason")})
            seq.append(n)
        elif t == "compaction_start":
            seq.append({"t": "start", "reason": e.get("reason")})
        elif t == "compaction_end":
            r = e.get("result") or {}
            u = r.get("usage") or {}
            c = {"t": "comp", "reason": e.get("reason"), "aborted": bool(e.get("aborted")), "error": e.get("errorMessage"),
                 "before": r.get("tokensBefore"), "after": r.get("estimatedTokensAfter"),
                 "in": int(u.get("input") or 0), "cr": int(u.get("cacheRead") or 0), "cw": int(u.get("cacheWrite") or 0),
                 "out": int(u.get("output") or 0), "cost": req_cost(u, prices) if u else None}
            seq.append(c)
    return {"seq": seq, "sid": sid, "parent": parent, "sentences": nend, "closed": closed, "errs": errs}


def cls(U, T):
    if T >= MIN_T and U >= HI * T:
        return "整段重付"
    if U < LO * T:
        return "正常"
    return "待定"


# ───────────────────────────── 跑一次 ─────────────────────────────
def pi_argv(sdir, messages, extra):
    return ["-p"] + list(messages) + ["--mode", "json", "--session-dir", sdir, "--no-extensions", "-ns", "-np", "-nc", "--thinking", "low"] + extra


def kill_tree(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        try:
            p.kill()
        except OSError:
            pass


def run_pi(ctx, arm, argv, timeout):
    env = dict(os.environ, PI_CODING_AGENT_DIR=ctx["dirs"][arm])
    try:
        p = subprocess.Popen([ctx["pi"]] + argv, cwd=ctx["work"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        return None, "%s: %s" % (type(e).__name__, e)
    PROCS.add(p)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(p)
        p.communicate()
        return None, "超时（%d 秒）" % timeout
    except BaseException:
        kill_tree(p)
        try:
            p.wait(timeout=3)
        except Exception:
            pass
        raise
    finally:
        PROCS.discard(p)
    return (out, err, p.returncode), None


def attempt(ctx, arm, spec):
    tag = os.urandom(4).hex()
    sdir = tempfile.mkdtemp(prefix="s-", dir=ctx["root"])
    t0 = time.time()
    timeout = ctx["timeout"]
    msgs1 = [message(tag, i) for i in range(1, spec["n1"] + 1)]
    res, err = run_pi(ctx, arm, pi_argv(sdir, msgs1, []), timeout)
    if err:
        return None, err
    out1, err1, rc1 = res
    ev1 = parse_events(out1, ctx["prices"])
    if ev1["sentences"] < spec["n1"] or not ev1["closed"] or ev1["errs"]:
        return None, "进程 1 退出码 %s，只完成了 %d/%d 句：%s" % (rc1, ev1["sentences"], spec["n1"], (ev1["errs"] or [(err1 or out1).strip()[-200:]])[0])
    ev2, err2 = None, ""
    if spec["mode"] != "none":
        extra = ["--continue"] if spec["mode"] == "continue" else ["--fork", ev1["sid"] or ""]
        msgs2 = [message(tag, spec["n1"] + j) for j in range(1, spec["n2"] + 1)]
        res2, err = run_pi(ctx, arm, pi_argv(sdir, msgs2, extra), timeout)
        if err:
            return None, "进程 2：" + err
        out2, err2, rc2 = res2
        ev2 = parse_events(out2, ctx["prices"])
        if ev2["sentences"] < spec["n2"] or not ev2["closed"] or ev2["errs"]:
            return None, "进程 2 退出码 %s，只完成了 %d/%d 句：%s" % (rc2, ev2["sentences"], spec["n2"], (ev2["errs"] or [(err2 or out2).strip()[-200:]])[0])
    shutil.rmtree(sdir, ignore_errors=True)
    return {"ev1": ev1, "ev2": ev2, "secs": round(time.time() - t0, 1), "tag": tag, "stderr": ((err1 or "") + (err2 or ""))}, None


def reqs_of(ev):
    return [x for x in ev["seq"] if x["t"] == "req"]


def verify(arm, spec, got):
    """每次运行的核对：没过的运行记失败、不进统计。"""
    probs = []
    ev1, ev2 = got["ev1"], got["ev2"]
    r1 = reqs_of(ev1)
    if any(x["T"] <= 0 for x in r1):
        probs.append("有请求取不到用量")
    comps = [x for x in ev1["seq"] + (ev2["seq"] if ev2 else []) if x["t"] in ("start", "comp")]
    if not spec["compaction"] and comps:
        probs.append("不该压缩的臂里出现了压缩事件")
    if spec["mode"] != "none":
        r2 = reqs_of(ev2)
        if not r2 or r2[0]["T"] <= 0:
            probs.append("进程 2 取不到用量")
        elif r2[0]["T"] <= r1[-1]["T"]:
            probs.append("进程 2 的第 1 个请求（T=%d）不比进程 1 的最后一个（T=%d）大：没有带上历史" % (r2[0]["T"], r1[-1]["T"]))
        if spec["mode"] == "fork" and (not ev2["sid"] or ev2["sid"] == ev1["sid"] or not ev2["parent"]):
            probs.append("--fork 没有产生带 parentSession 的新会话")
        if spec["mode"] == "continue" and ev2["sid"] != ev1["sid"]:
            probs.append("--continue 没有接在同一个会话上")
    return probs


def record(arm, rep, spec, got, tries, prices):
    def pack(ev):
        reqs = [{k: x[k] for k in ("k", "T", "C", "U", "out", "cost")} for x in reqs_of(ev)]
        pos, comps, n = [], [], 0
        for x in ev["seq"]:
            if x["t"] == "req":
                n += 1
            elif x["t"] == "comp":
                comps.append({"after_req": n, **{k: x[k] for k in ("reason", "aborted", "error", "before", "after", "in", "cr", "cw", "out", "cost")}})
        return reqs, comps
    r1, c1 = pack(got["ev1"])
    rec = {"kind": "run", "arm": arm, "rep": rep, "tries": tries, "secs": got["secs"], "tag": got["tag"], "r1": r1, "c1": c1,
           "stderr": got["stderr"].strip()[:300]}
    if got["ev2"]:
        r2, c2 = pack(got["ev2"])
        rec.update(r2=r2, c2=c2)
    return rec


def run_cost(rec):
    xs = [x["cost"] for x in rec["r1"] + rec.get("r2", [])] + [c["cost"] for c in rec["c1"] + rec.get("c2", [])]
    return None if None in xs else sum(xs)


def do_run(job, ctx):
    arm, rep = job
    spec = ARMS[arm]
    if STOP["flag"] or (ctx["cap"] and STATE["spent"] >= ctx["cap"]):
        STATE["skipped"] += 1
        return None
    got = err = None
    tries = 0
    for tries in range(1, RETRIES + 2):
        got, err = attempt(ctx, arm, spec)
        if got or STOP["flag"]:
            break
        if tries < RETRIES + 1:
            time.sleep(3)
    if not got and STOP["flag"]:
        return None
    STATE["done"] += 1
    n = STATE["done"]
    probs = verify(arm, spec, got) if got else []
    if not got or probs:
        why = ("核对没过：" + "；".join(probs)) if probs else (err or "?")
        say("[%2d/%d] %-3s #%-2d 失败：%s" % (n, ctx["total"], arm, rep, why[:300]))
        return {"kind": "run", "arm": arm, "rep": rep, "err": why[:400], "tries": tries, "check_failed": bool(probs)}
    rec = record(arm, rep, spec, got, tries, ctx["prices"])
    cost = run_cost(rec) or 0
    STATE["spent"] += cost
    us = [x["U"] for x in rec["r1"]]
    extra = ""
    if rec["c1"]:
        extra = "  压缩 %d 次（%s）" % (len(rec["c1"]), "；".join("%s→%s" % (c["before"], c["after"]) for c in rec["c1"]))
    if "r2" in rec:
        x = rec["r2"][0]
        extra = "  进程 2 第 1 个请求 T=%d U=%d（%s）" % (x["T"], x["U"], cls(x["U"], x["T"]))
    say("[%2d/%d] %-3s #%-2d U=%s%s  %4.1f 秒  %s" % (n, ctx["total"], arm, rep, "/".join(str(u) for u in us), extra, rec["secs"],
                                                   "" if not ctx["prices"] else "$%.6f" % cost))
    return rec


# ───────────────────────────── 汇总（只印数字，预测由评分脚本按登记的规则打） ─────────────────────────────
def med(v):
    v = sorted(v)
    n = len(v)
    return None if not n else (v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2)


def good(recs, arm):
    return [r for r in recs if r.get("arm") == arm and "err" not in r]


def first_comp_neighbors(r):
    """K1 的一次运行：第一次压缩前最后一个请求、之后第 1、第 2 个请求。"""
    if not r["c1"]:
        return None
    p = r["c1"][0]["after_req"]
    rq = r["r1"]
    return (rq[p - 1] if p >= 1 else None, rq[p] if p < len(rq) else None, rq[p + 1] if p + 1 < len(rq) else None)


def summarize(recs, ctx):
    pr = ctx["prices"]
    say("\n== 核对")
    bad = [r for r in recs if "err" in r]
    say("  计划 %d 次运行，完成 %d 次；失败 %d 次（其中核对没过 %d 次；基础设施失败已各重试最多 %d 次）" % (
        ctx["total"], len(recs) - len(bad), len(bad), sum(1 for r in bad if r.get("check_failed")), RETRIES))
    k0, k1, r0, f0 = (good(recs, a) for a in ("K0", "K1", "R0", "F0"))
    say("\n== K0（不压缩）：每句第 1 个请求的 U 和分类")
    bg = 0
    for r in k0:
        row = []
        for x in r["r1"]:
            c = cls(x["U"], x["T"])
            row.append("%d%s" % (x["U"], "*" if c == "整段重付" else ""))
        say("  #%d  T=%s  U=%s" % (r["rep"], "/".join(str(x["T"]) for x in r["r1"]), " ".join(row)))
        bg += sum(1 for x in r["r1"][1:] if cls(x["U"], x["T"]) == "整段重付")
    say("  背景：第 2–7 句的 %d 个请求里“整段重付”（带 * 的）%d 个" % (sum(len(r["r1"]) - 1 for r in k0), bg))
    say("\n== K1（压缩）：每次运行的压缩事件和压缩前后的请求")
    be = []
    for r in k1:
        nb = first_comp_neighbors(r)
        cs = r["c1"]
        s = "  #%d  压缩 %d 次" % (r["rep"], len(cs))
        if cs:
            c = cs[0]
            s += "；第 1 次：tokensBefore %s → after %s；摘要请求 input %d cacheRead %d 输出 %d 花费 %s" % (
                c["before"], c["after"], c["in"] + c["cw"], c["cr"], c["out"], "—" if c["cost"] is None else "$%.6f" % c["cost"])
            if nb and nb[0] and nb[1]:
                s += "；压缩前最后一个请求 T=%d U=%d；之后第 1 个 T=%d U=%d（%s）" % (nb[0]["T"], nb[0]["U"], nb[1]["T"], nb[1]["U"], cls(nb[1]["U"], nb[1]["T"]))
                if nb[2]:
                    s += "；第 2 个 T=%d U=%d（%s）" % (nb[2]["T"], nb[2]["U"], cls(nb[2]["U"], nb[2]["T"]))
                if pr and c["cost"] is not None and nb[1]["cost"] is not None and nb[0]["cost"] is not None and nb[2]:
                    save = (nb[0]["C"] - nb[2]["C"]) * pr["cacheRead"] / 1e6
                    extra = c["cost"] + (nb[1]["cost"] - nb[0]["cost"])
                    if save > 0:
                        be.append(extra / save)
        say(s)
    if be:
        say("  回本估算（只是算术）：摘要请求花费 + 压缩后第 1 个请求比压缩前最后一个请求多花的，除以压缩后每个请求少付的缓存读，中位数约 %.0f 个请求（%d 次运行算得出）" % (med(be), len(be)))
    say("\n== R0（--continue）和 F0（--fork）：进程 2 第 1 个请求")
    for name, rs in (("R0", r0), ("F0", f0)):
        cl = [cls(r["r2"][0]["U"], r["r2"][0]["T"]) for r in rs]
        say("  %s：%s  （U = %s）" % (name, "、".join("%s %d" % (c, cl.count(c)) for c in ("正常", "待定", "整段重付")), "/".join(str(r["r2"][0]["U"]) for r in rs)))
    say("\n== 每次运行总花费的中位数（含摘要请求）")
    if pr:
        for a, rs in (("K0", k0), ("K1", k1), ("R0", r0), ("F0", f0)):
            cs = [run_cost(r) for r in rs if run_cost(r) is not None]
            say("  %s  $%.6f（%d 次）" % (a, med(cs) or 0, len(cs)) if cs else "  %s  —" % a)
    say("本次花费 $%.4f" % STATE["spent"])


def print_data(recs):
    say("\n== 数据（每次运行一行，贴给我的时候别删）")
    for r in recs:
        say("数据 " + json.dumps(r, ensure_ascii=False, sort_keys=True))


# ───────────────────────────── 主程序 ─────────────────────────────
def pi_bin():
    b = os.environ.get("PI_BIN") or shutil.which("pi")
    if not b or not (os.access(b, os.X_OK) or shutil.which(b)):
        sys.exit("找不到可以运行的 pi（%s）。可以先 export PI_BIN=/路径/pi" % (b or "PATH 里没有 pi"))
    return b


def build_jobs(arms_sel, reps_override):
    jobs = []
    for a in ARMS:
        if arms_sel and a not in arms_sel:
            continue
        for i in range(1, (reps_override or ARMS[a]["reps"]) + 1):
            jobs.append((a, i))
    Lcg(SEED).shuffle(jobs)
    return jobs


def main():
    ap = argparse.ArgumentParser(description="第 7 课：长会话（压缩、恢复、分出去）")
    ap.add_argument("--dry-run", action="store_true", help="不调模型：打印设计指纹和运行顺序")
    ap.add_argument("--arms", help="只测几个臂，逗号分隔，例如 K0,K1（正式跑请不要加）")
    ap.add_argument("--reps", type=int, help="每个臂的次数（正式跑请不要加）")
    ap.add_argument("--model", help="provider/模型id，默认用 settings.json 里的默认模型（正式跑请不要加）")
    ap.add_argument("--max-cost", type=float, default=0.40, help="花费上限（美元），到了就不再开始新的运行；默认 0.40")
    ap.add_argument("--timeout", type=int, default=TIMEOUT, help="一个 pi 进程最多等多少秒；默认 %d" % TIMEOUT)
    a = ap.parse_args()
    arms_sel = [x for x in (a.arms or "").split(",") if x]
    for x in arms_sel:
        if x not in ARMS:
            sys.exit("没有这个臂：%s（可选 %s）" % (x, ",".join(ARMS)))
    src, models, name, mid = pick_model(a.model)
    prices = model_prices(models, name, mid)
    window = model_entry(models, name, mid).get("contextWindow") or 200000
    pi = pi_bin()
    ver = subprocess.run([pi, "--version"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
    say("pi %s   模型 %s / %s   设计指纹 %s" % (ver, name, mid, design_id()))
    say("设计：K0 %d 次、K1 %d 次、R0 %d 次、F0 %d 次；每句约 %d 字符的填充文字；压缩阈值约 %d token（contextWindow %s → reserveTokens %d），保留 %d token；cacheWarming 在实验里关掉。" % (
        ARMS["K0"]["reps"], ARMS["K1"]["reps"], ARMS["R0"]["reps"], ARMS["F0"]["reps"], FILLER_CHARS, THRESH, window, max(0, int(window) - THRESH), KEEP))
    if prices:
        say("单价（每百万 token）：新输入 $%g，缓存读 $%g，缓存写 $%g，输出 $%g" % (prices["input"], prices["cacheRead"], prices["cacheWrite"], prices["output"]))
    else:
        say("（models.json 里这个模型没写价格，下面不算花费，也不设花费上限）")
    jobs = build_jobs(arms_sel, a.reps)
    nproc = sum(1 + (1 if ARMS[x]["mode"] != "none" else 0) for x, _ in jobs)
    say("共 %d 次运行（%d 个 pi 进程）：一个一个跑，不并行；花费上限 %s。第一行出来之前先等几秒。\n" % (
        len(jobs), nproc, ("$%g" % a.max_cost) if prices and a.max_cost else "无"))
    if a.dry_run:
        say("== --dry-run：不调模型。运行顺序（固定种子打乱）：")
        for arm, rep in jobs:
            say("  %-3s #%d  %s" % (arm, rep, ARMS[arm]["doc"]))
        say("\n  例：第 1 句的开头：%s…（共 %d 字符）" % (message("00000000", 1)[:60].replace("\n", " "), len(message("00000000", 1))))
        say("  压缩设置：K1 %s；其余臂 %s" % (json.dumps(compaction_settings(ARMS["K1"], window)), json.dumps(compaction_settings(ARMS["K0"], window))))
        return
    root = tempfile.mkdtemp(prefix="sl-lab-")
    work = os.path.join(root, "work")
    os.makedirs(work)

    def _stop_signal(signum, frame):
        raise KeyboardInterrupt
    for sn in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, sn):
            try:
                signal.signal(getattr(signal, sn), _stop_signal)
            except (ValueError, OSError):
                pass
    recs = []
    try:
        say("私有目录 %s（你的 models.json、auth.json 的副本和实验用的 settings.json；跑完删除）。" % root)
        dirs = {x: make_agent_dir(root, x, src, models, name, mid, compaction_settings(ARMS[x], window)) for x in ARMS}
        ctx = {"pi": pi, "root": root, "work": work, "prices": prices, "cap": a.max_cost if prices else 0, "timeout": a.timeout, "dirs": dirs, "total": len(jobs)}
        for job in jobs:
            r = do_run(job, ctx)
            if r is not None:
                recs.append(r)
            if STOP["flag"]:
                break
    except KeyboardInterrupt:
        STOP["flag"] = True
        say("\n（被中断：已经跑完的运行照常汇总）")
        for p in list(PROCS):
            kill_tree(p)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if STATE["skipped"]:
        say("（花费上限 $%g 到了：%d 次运行没有开始）" % (a.max_cost, STATE["skipped"]))
    summarize(recs, {"prices": prices, "total": len(jobs)})
    print_data(recs)


if __name__ == "__main__":
    main()
