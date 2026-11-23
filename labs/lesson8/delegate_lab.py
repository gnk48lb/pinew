#!/usr/bin/env python3
"""delegate_lab.py —— 第 8 课（子代理 delegate，和摘要会不会丢事实）的实验脚本。

用法（在一个空目录里运行，比如 ~/pi-practice/l8）：
  python3 ~/pi/lesson8/delegate_lab.py --dry-run                 不调模型：打印设计指纹、28 次运行的顺序、样例材料的大小和各个“针”的位置
  python3 ~/pi/lesson8/delegate_lab.py 2>&1 | tee -ia ex8.log    正式跑（约 10–15 分钟，预计 $0.04–0.15）

设计在路线图 11-19 第 8 课那一行里先写好了（臂、次数、每一句的原文、判定、预测）；这个脚本只是把它跑出来。
改了脚本里写死的设计，“设计指纹”就变。你自己的 ~/.pi/agent 一个字不动：脚本在 /tmp 下起一个私有目录，
放 models.json、auth.json 的副本和实验用的 settings.json，跑完删除。
"""
import argparse, hashlib, json, os, re, shutil, signal, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
EXT = os.path.join(HERE, "lab8_ext.ts")

# ───────────────────────────── 设计（改一个字，“设计指纹”就变） ─────────────────────────────
SEED = 8
RETRIES = 2
TIMEOUT = 420
MIN_T, HI, LO = 1500, 0.7, 0.3          # “整段重付”：T ≥ 1,500 且 U ≥ 0.7 T；“正常”：U < 0.3 T
KEEP = 300                              # keepRecentTokens：压缩时保留的最近 token（比一句填充文字小，所以只留最后一句）
TARGET_BYTES = 32000                    # incident.log 的目标大小（字节）
PAD_CHARS = 2500                        # C0、C1 里填充句的长度（字符）
COMPACT_AFTER = 3                       # C1：第 3 句跑完后强制压缩
OK_TEXT = "Reply with only the word OK."
M1_R = ("Read ./incident.log with the read tool, then write a short incident summary of at most 6 lines: "
        "what failed first, what the root cause was, and what was done about it.")
M1_D = ("You cannot read files yourself. Use the delegate tool to have a helper read ./incident.log, then write a short "
        "incident summary of at most 6 lines based on what it reports: what failed first, what the root cause was, and what was done about it.")
RECALL = ("Answer from memory only; the tools are switched off for this question. Reply on one line in exactly this format: "
          "A=<value>; B=<value>; C=<value>; D=<value>. A: the build number in the FATAL deploy-ctl line. "
          "B: the req id in the first ERROR line. C: the retry_after seconds given to tenant {tenant}. "
          "D: the commit the rollback was started to. Write UNKNOWN for any item you do not know. Do not guess.")
RECOVER = ("If any item in your last line was UNKNOWN, look it up now (the tools are back on), then reply on one line in the same format. "
           "If none was UNKNOWN, repeat your last line.")
PAD_HEAD = "Note %d (unrelated to the log, no action needed)"
ARMS = {
    "R":  {"reps": 8, "tools": "read",     "kind": "rd", "compact": False, "doc": "主会话自己读文件，8 句（任务、5 句 OK、回忆题、补救）"},
    "D":  {"reps": 8, "tools": "delegate", "kind": "rd", "compact": False, "doc": "主会话只有 delegate，让子进程读文件，8 句（同上）"},
    "C0": {"reps": 4, "tools": "read",     "kind": "c",  "compact": False, "doc": "自己读，2 句填充，不压缩，回忆题、补救，共 5 句"},
    "C1": {"reps": 8, "tools": "read",     "kind": "c",  "compact": True,  "doc": "自己读，2 句填充，第 3 句后强制压缩，回忆题、补救，共 5 句"},
}
WORDS = ("table river window garden paper silver morning engine bridge letter forest candle market ocean planet ticket pencil mirror "
         "basket violin harbor lantern pocket carpet rocket button meadow castle drawer hammer island jacket kitchen ladder magnet needle "
         "orange pillow quartz ribbon saddle thunder umbrella valley wagon yellow anchor blanket compass dolphin feather glacier horizon "
         "ivory jungle kettle lemon marble nickel orchid pepper quiver rabbit shadow timber useful velvet whistle zephyr copper dinner "
         "evening flower guitar helmet insect jewel kernel lamp mountain napkin office parcel quilt rubber sponge teapot uniform vessel "
         "walnut yogurt zipper apple bottle cheese desert elbow fabric ginger honey input jelly knife leather monkey noodle olive potato "
         "queen radio sugar tomato village winter").split()
TENANTS = ["acme-eu", "borealis", "cinder", "dovetail", "ember", "fjord", "granite", "harbor",
           "indigo", "juniper", "kestrel", "lumen", "marlin", "nimbus", "opal", "quartz"]
ROUTES = ["/v2/charge", "/v2/refund", "/v2/session", "/v2/invoice", "/v1/health", "/v2/ledger"]
TABLES = ["accounts", "ledger_entries", "invoices", "sessions", "tenants", "audit_log"]
DAY = "2026-03-14"
PRICE_KEYS = ("input", "output", "cacheRead", "cacheWrite")
NEEDLES = "ABCD"


class Lcg:
    """自己写的伪随机数（不用 random 模块：算法在不同 Python 版本之间不保证一样；材料和顺序要能逐字节复现）。"""

    def __init__(self, seed):
        self.x = (int(seed) * 2862933555777941757 + 3037000493) % (1 << 64)

    def below(self, n):
        self.x = (self.x * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        return (self.x >> 33) % n

    def shuffle(self, items):
        for i in range(len(items) - 1, 0, -1):
            j = self.below(i + 1)
            items[i], items[j] = items[j], items[i]


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def seed_of(text):
    return int(sha(text)[:12], 16)


# ───────────────────────────── 材料：incident.log 和填充句 ─────────────────────────────
def hexs(g, n):
    return "".join("0123456789abcdef"[g.below(16)] for _ in range(n))


def stamp(ms):
    s, m = divmod(ms, 1000)
    h, r = divmod(s, 3600)
    mi, se = divmod(r, 60)
    return "%sT%02d:%02d:%02d.%03dZ" % (DAY, 3 + h, mi, se, m)


def noise(g, phase):
    """一行普通的日志：phase 0 = 第一条 ERROR 之前（只有 INFO 和 WARN，文字里没有 error 这个词）。"""
    r = g.below(100)
    lvl = ("WARN" if r < 14 else "INFO") if phase == 0 else ("ERROR" if r < 14 else ("WARN" if r < 40 else "INFO"))
    if lvl == "ERROR":
        k = g.below(4)
        if k == 0:
            return lvl, "payments-gw", "req=%s upstream timeout after 30000ms circuit=OPEN" % hexs(g, 6)
        if k == 1:
            return lvl, "billing-api", "req=%s 502 from payments-gw" % hexs(g, 6)
        if k == 2:
            return lvl, "edge-gw-1", "req=%s 504 gateway timeout route=%s" % (hexs(g, 6), ROUTES[g.below(len(ROUTES))])
        return lvl, "ledger-sync", "sync stalled lag=%dms" % (2000 + g.below(9000))
    if lvl == "WARN":
        k = g.below(4)
        if k == 0:
            return lvl, "billing-api", "slow query %dms table=%s" % (400 + g.below(1800), TABLES[g.below(len(TABLES))])
        if k == 1:
            return lvl, "edge-gw-%d" % (1 + g.below(2)), "req=%s retry attempt=2 route=%s" % (hexs(g, 6), ROUTES[g.below(len(ROUTES))])
        if k == 2:
            return lvl, "search-idx", "gc pause %dms" % (80 + g.below(400))
        return lvl, "queue-worker", "disk usage %d%% on /var/spool" % (60 + g.below(30))
    k = g.below(7)
    if k == 0:
        return lvl, "edge-gw-%d" % (1 + g.below(2)), "req=%s route=%s status=200 ms=%d" % (hexs(g, 6), ROUTES[g.below(len(ROUTES))], 20 + g.below(180))
    if k == 1:
        return lvl, "auth-svc", "req=%s token refreshed ms=%d" % (hexs(g, 6), 5 + g.below(60))
    if k == 2:
        return lvl, "cache-3", "hit_ratio=0.%02d evictions=%d" % (70 + g.below(29), g.below(900))
    if k == 3:
        return lvl, "queue-worker", "depth=%d lag=%dms workers=8" % (g.below(400), 10 + g.below(300))
    if k == 4:
        return lvl, "search-idx", "segment merge done docs=%d took=%dms" % (1000 + g.below(90000), 50 + g.below(700))
    if k == 5:
        return lvl, "notify-svc", "batch sent n=%d failed=0" % (10 + g.below(500))
    return lvl, "ledger-sync", "sync tick lag=%dms rows=%d" % (5 + g.below(120), 100 + g.below(9000))


def fmt_line(ms, lvl, svc, text):
    return "%s %-5s %-12s %s" % (stamp(ms), lvl, svc, text)


def build_log(seed, n):
    g = Lcg(seed)
    run = hexs(g, 8)
    a = str(1000 + g.below(9000))
    prev = a
    while prev == a:
        prev = str(1000 + g.below(9000))
    b, d = hexs(g, 6), hexs(g, 7)
    decoys = [hexs(g, 7) for _ in range(3)]
    ten = TENANTS[:]
    g.shuffle(ten)
    ten = ten[:12]
    retry = []
    while len(retry) < 12:
        v = 2 + g.below(118)
        if v not in retry:
            retry.append(v)
    third = int(0.67 * n)
    early, late = set(), set()
    while len(early) < 8:
        early.add(int(0.42 * n) + g.below(third - int(0.42 * n)))
    while len(late) < 4:
        late.add(third + g.below(int(0.92 * n) - third))
    pos = sorted(early | late)
    asked_i = sorted(i for i, p in enumerate(pos) if p in late)[g.below(4)]
    fixed = {}

    def put(idx, item):
        while idx in fixed or idx in pos:
            idx += 1
        fixed[idx] = item
    put(3, ("INFO", "deploy-ctl", "rollout started: ledger-sync canary 1 of 5 pods"))
    put(5, ("INFO", "deploy-ctl", "artifact fetched commit=%s" % decoys[0]))
    put(int(0.35 * n), ("INFO", "deploy-ctl", "canary healthy commit=%s" % decoys[1]))
    put(int(0.60 * n), ("WARN", "deploy-ctl", "promotion deferred commit=%s" % decoys[2]))
    d_idx = n - 14
    forbidden = [a, b, d, prev] + decoys
    lines = ["# incident export run=%s host=ops-7 window=03:00-04:10" % run]
    off = len(lines[0]) + 1
    t = 0
    a_done = b_done = False
    b_target = 8200 + g.below(1600)
    offs = {}
    phase = 0
    for i in range(1, n):
        t += 300 + g.below(5500)
        if i in fixed:
            lvl, svc, text = fixed[i]
        elif i == d_idx:
            lvl, svc, text = "INFO", "deploy-ctl", "rollback started to commit=%s (build %s, previous stable)" % (d, prev)
            offs["D"] = off
        elif i in pos:
            j = pos.index(i)
            lvl, svc, text = "WARN", "edge-gw-2", "req=%s throttling tenant=%s retry_after=%ds" % (hexs(g, 6), ten[j], retry[j])
            if j == asked_i:
                offs["C"] = off
        elif not a_done and off >= 1100:
            lvl, svc, text = "FATAL", "deploy-ctl", "rollout aborted: ledger-sync build %s failed the health gate on 3 of 5 pods" % a
            a_done = True
            offs["A"] = off
        elif a_done and not b_done and off >= b_target:
            lvl, svc, text = "ERROR", "payments-gw", "req=%s upstream timeout after 30000ms circuit=OPEN" % b
            b_done = True
            phase = 1
            offs["B"] = off
        else:
            for _ in range(20):
                lvl, svc, text = noise(g, phase)
                if not any(f in text for f in forbidden):
                    break
        line = fmt_line(t, lvl, svc, text)
        lines.append(line)
        off += len(line) + 1
    text = "\n".join(lines) + "\n"
    truth = {"A": a, "B": b, "C": str(retry[asked_i]), "D": d, "tenant": ten[asked_i], "prev": prev}
    return {"text": text, "run": run, "truth": truth, "offs": offs, "lines": n, "bytes": len(text.encode("utf-8"))}


def check_log(m):
    t, tr, o = m["text"], m["truth"], m["offs"]
    probs = []
    if set(o) != set(NEEDLES):
        probs.append("缺针 %s" % sorted(set(NEEDLES) - set(o)))
        return probs
    if t.count(tr["A"]) != 1 or t.count("build %s" % tr["A"]) != 1:
        probs.append("A 不唯一")
    if t.count(tr["B"]) != 1:
        probs.append("B 不唯一")
    if t.count(tr["D"]) != 1:
        probs.append("D 不唯一")
    if t.count("tenant=%s " % tr["tenant"]) != 1:
        probs.append("C 的租户不唯一")
    if t.count("retry_after=%ss" % tr["C"]) != 1:
        probs.append("C 的数不唯一")
    if t.count("throttling tenant=") != 12:
        probs.append("限流行不是 12 行")
    head = t[:2000]
    if not (o["A"] + len(next(x for x in t.split("\n") if tr["A"] in x)) < 2000):
        probs.append("A 不在前 2,000 字符之内")
    if any(o[k] < 2000 for k in "BCD"):
        probs.append("B、C、D 里有在前 2,000 字符之内的")
    first_err = t.find(" ERROR ")
    if first_err < 0 or t.find("req=%s" % tr["B"]) < first_err or t[first_err:first_err + 200].find("req=%s" % tr["B"]) < 0:
        probs.append("B 不是第一条 ERROR")
    if "error" in t[:first_err].lower():
        probs.append("第一条 ERROR 之前出现了 error 这个词")
    if not (28000 <= m["bytes"] <= 36000):
        probs.append("大小 %d 不在 28,000–36,000 字节" % m["bytes"])
    if len(head) < 2000:
        probs.append("文件太短")
    return probs


def make_log(seed):
    """同一个种子永远得到同一份材料；万一检查没过（极少），换一个确定的盐再试。"""
    for salt in range(60):
        s = seed + salt * 7919
        m = build_log(s, 380)
        n2 = max(300, round(380 * TARGET_BYTES / m["bytes"]))
        m = build_log(s, n2)
        if not check_log(m):
            m["salt"] = salt
            m["seed"] = s
            m["sha"] = sha(m["text"])
            return m
    raise RuntimeError("材料生成没过检查（种子 %d）" % seed)


def filler(tag, i):
    g = Lcg(seed_of("%s/%d" % (tag, i)))
    out, n = [], 0
    while n < PAD_CHARS:
        k = 6 + g.below(9)
        s = " ".join(WORDS[g.below(len(WORDS))] for _ in range(k))
        s = s[0].upper() + s[1:] + "."
        out.append(s)
        n += len(s) + 1
    return " ".join(out)


def pad_message(tag, i):
    return "%s\n\n%s\n\n%s" % (PAD_HEAD % i, filler(tag, i), OK_TEXT)


def messages_for(arm, tenant, tag):
    """-> (按顺序的 -p 参数, 回忆题是第几句)"""
    recall = RECALL.replace("{tenant}", tenant)
    if ARMS[arm]["kind"] == "rd":
        return [M1_D if arm == "D" else M1_R] + [OK_TEXT] * 5 + [recall, RECOVER], 7
    return [M1_R, pad_message(tag, 2), pad_message(tag, 3), recall, RECOVER], 4


def design_id():
    probe = make_log(seed_of("probe"))
    blob = json.dumps({"arms": ARMS, "m1": [M1_R, M1_D], "ok": OK_TEXT, "recall": RECALL, "recover": RECOVER, "pad": [PAD_HEAD, PAD_CHARS, sha(filler("T", 2))[:12]],
                       "log": [TARGET_BYTES, sha(probe["text"])[:12], probe["bytes"]], "keep": KEEP, "compact_after": COMPACT_AFTER,
                       "seed": SEED, "retries": RETRIES, "timeout": TIMEOUT, "cls": [MIN_T, HI, LO], "words": sha(" ".join(WORDS))[:12],
                       "ext": sha(open(EXT, encoding="utf-8").read())[:12]}, sort_keys=True, ensure_ascii=False)
    return sha(blob)[:12]


STOP = {"flag": False}
PROCS = set()
STATE = {"done": 0, "spent": 0.0, "spent_all": 0.0, "skipped": 0, "secs": 0.0}


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


def make_agent_dir(root, src, models, name, mid):
    """root 下的一份私有 pi 配置目录：models.json、auth.json 的副本 + 实验用的 settings.json。你真正的 ~/.pi/agent 一个字不动。"""
    d = os.path.join(root, "agent")
    os.makedirs(d, mode=0o700)
    write_private(os.path.join(d, "models.json"), json.dumps(models, ensure_ascii=False, indent=1))
    st = {"defaultProvider": name, "defaultModel": mid, "defaultThinkingLevel": "low", "cacheWarming": "off",
          "compaction": {"enabled": True, "keepRecentTokens": KEEP}}
    write_private(os.path.join(d, "settings.json"), json.dumps(st))
    if os.path.exists(os.path.join(src, "auth.json")):
        write_private(os.path.join(d, "auth.json"), open(os.path.join(src, "auth.json"), encoding="utf-8-sig").read())
    return d


# ───────────────────────────── 事件流 ─────────────────────────────
def request_numbers(m, prices):
    u = m.get("usage") or {}
    inp, cr, cw = [int(u.get(k) or 0) for k in ("input", "cacheRead", "cacheWrite")]
    T = inp + cr + cw
    return {"T": T, "C": cr, "U": T - cr, "in": inp, "cw": cw, "out": int(u.get("output") or 0), "cost": req_cost(u, prices) if T > 0 else None}


def cls(U, T):
    if T >= MIN_T and U >= HI * T:
        return "整段重付"
    if U < LO * T:
        return "正常"
    return "待定"


def text_of(content):
    return "".join(p.get("text", "") for p in (content or []) if isinstance(p, dict) and p.get("type") == "text")


def brief_args(name, args):
    a = args if isinstance(args, dict) else {}
    if name == "delegate":
        return {"task": str(a.get("task", ""))[:600]}
    return {k: (str(v)[:80]) for k, v in a.items()}


def parse_events(stdout, prices, truth, marker):
    """pi --mode json 的输出 -> 每句一项：请求（T、C、U、输出、花费）、工具调用、工具结果、最后一条助手消息的文字；压缩事件。"""
    msgs, comps, errs, cur = [], [], [], None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t = e.get("type")
        if t == "agent_start":
            cur = {"k": len(msgs) + 1, "reqs": [], "calls": [], "results": [], "final": "", "closed": False}
            msgs.append(cur)
        elif t == "agent_end" and cur is not None:
            cur["closed"] = True
        elif t == "message_end" and cur is not None:
            m = e.get("message") or {}
            role = m.get("role")
            if role == "assistant":
                n = request_numbers(m, prices)
                n["stop"] = m.get("stopReason")
                cur["reqs"].append(n)
                if m.get("stopReason") in ("error", "aborted"):
                    errs.append(str(m.get("errorMessage") or m.get("stopReason"))[:200])
                content = m.get("content") or []
                for p in content:
                    if isinstance(p, dict) and p.get("type") == "toolCall":
                        cur["calls"].append({"tool": p.get("name"), "args": brief_args(p.get("name"), p.get("arguments"))})
                txt = text_of(content)
                if txt.strip():
                    cur["final"] = txt
            elif role == "toolResult":
                txt = text_of(m.get("content"))
                u = m.get("usage")
                r = {"tool": m.get("toolName"), "chars": len(txt), "err": bool(m.get("isError")), "marker": bool(marker and marker in txt),
                     "blocked": txt.startswith("Tools are switched off"), "nested": (req_cost(u, prices) if u else None)}
                if marker and marker in txt:
                    r["g7"] = {"A": txt.count("build %s" % truth["A"]), "B": txt.count("req=%s" % truth["B"]),
                               "C": txt.count("tenant=%s retry_after=%ss" % (truth["tenant"], truth["C"])), "D": txt.count("commit=%s" % truth["D"])}
                if m.get("toolName") == "delegate":
                    r["text"] = txt[:1500]
                cur["results"].append(r)
        elif t == "compaction_end":
            r = e.get("result") or {}
            u = r.get("usage") or {}
            comps.append({"aborted": bool(e.get("aborted")), "error": e.get("errorMessage"), "before": r.get("tokensBefore"), "after": r.get("estimatedTokensAfter"),
                          "in": int(u.get("input") or 0), "cr": int(u.get("cacheRead") or 0), "cw": int(u.get("cacheWrite") or 0), "out": int(u.get("output") or 0),
                          "cost": req_cost(u, prices) if u else None, "k": len(msgs), "summary": str(r.get("summary") or "")[:3000]})
    return {"msgs": msgs, "comps": comps, "errs": errs}


def read_side(path):
    out = []
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line.startswith("{"):
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


# ───────────────────────────── 跑一次 ─────────────────────────────
def kill_tree(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        try:
            p.kill()
        except OSError:
            pass


def run_pi(ctx, argv, env_extra, timeout):
    env = dict(os.environ, PI_CODING_AGENT_DIR=ctx["agent"], **env_extra)
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


def pi_argv(messages, tools):
    return ["-p"] + list(messages) + ["--mode", "json", "--no-session", "--no-extensions", "-ns", "-np", "-nc", "--thinking", "low",
                                      "--tools", tools, "-e", EXT]


def attempt(ctx, arm, rep, tries):
    spec = ARMS[arm]
    seed = seed_of("lesson8/%s/%d/%d" % (arm, rep, tries))
    log = make_log(seed)
    tr = log["truth"]
    tag = "%s%d.%d" % (arm, rep, tries)
    msgs, recall_k = messages_for(arm, tr["tenant"], tag)
    with open(os.path.join(ctx["work"], "incident.log"), "w", encoding="utf-8") as f:
        f.write(log["text"])
    side = os.path.join(ctx["root"], "side-%s.jsonl" % os.urandom(4).hex())
    env = {"LAB8_LOG": side, "LAB8_NO_TOOLS": str(recall_k), "LAB8_COMPACT_AFTER": str(COMPACT_AFTER) if spec["compact"] else "",
           "LAB8_CHILD_TOOLS": "read", "LAB8_REPORT_USAGE": "1"}
    t0 = time.time()
    res, err = run_pi(ctx, pi_argv(msgs, spec["tools"]), env, ctx["timeout"])
    secs = round(time.time() - t0, 1)
    if err:
        return None, err, None
    out, errtxt, rc = res
    ev = parse_events(out, ctx["prices"], tr, log["run"])
    ev["side"] = read_side(side)
    try:
        os.remove(side)
    except OSError:
        pass
    got = {"ev": ev, "secs": secs, "log": log, "recall_k": recall_k, "nmsgs": len(msgs), "stderr": (errtxt or ""), "rc": rc, "seed": log["seed"]}
    return got, None, None


def verify(arm, got):
    """每次运行的核对：没过的运行记失败、不进统计，重跑（用新种子）。"""
    spec = ARMS[arm]
    ev = got["ev"]
    probs = []
    ms = ev["msgs"]
    if got["rc"] not in (0, None) and len(ms) < got["nmsgs"]:
        probs.append("pi 退出码 %s" % got["rc"])
    if len(ms) < got["nmsgs"] or not all(m["closed"] for m in ms):
        probs.append("只完成了 %d/%d 句" % (sum(1 for m in ms if m["closed"]), got["nmsgs"]))
    if ev["errs"]:
        probs.append("有请求出错：%s" % ev["errs"][0])
    for m in ms:
        if any(r["T"] <= 0 for r in m["reqs"]):
            probs.append("第 %d 句有请求取不到用量" % m["k"])
            break
    if ms:
        m1 = ms[0]
        if spec["kind"] == "c" or arm == "R":
            if not any(r["tool"] == "read" and r["marker"] for r in m1["results"]):
                probs.append("第 1 句没有读到这次运行的文件（模型没调 read，或结果里没有标记行）")
        else:
            dl = [r for r in m1["results"] if r["tool"] == "delegate"]
            if not dl:
                probs.append("第 1 句没有调 delegate")
            elif any(r["err"] for r in dl):
                probs.append("delegate 失败")
            side = [s for s in ev["side"] if s.get("ev") == "delegate"]
            if side and not any(any(x.get("tool") == "read" and not x.get("isError") for x in (s.get("results") or [])) or s.get("results") for s in side):
                probs.append("子进程没有读任何东西")
    ncomp = sum(1 for c in ev["comps"] if not c["aborted"] and not c["error"])
    if spec["compact"]:
        if ncomp != 1 or len(ev["comps"]) != 1:
            probs.append("C1 应该正好压缩 1 次，实际 %d 次成功（共 %d 个压缩事件）" % (ncomp, len(ev["comps"])))
    elif ev["comps"]:
        probs.append("不该压缩的臂里出现了压缩事件")
    return probs


def record(arm, rep, tries, got, prices):
    ev = got["ev"]
    log = got["log"]
    rec = {"kind": "run", "arm": arm, "rep": rep, "tries": tries, "secs": got["secs"], "seed": got["seed"], "marker": log["run"],
           "file": {"bytes": log["bytes"], "lines": log["lines"], "sha": log["sha"][:16], "offs": log["offs"], "salt": log["salt"]},
           "truth": log["truth"], "recall_k": got["recall_k"], "msgs": [], "comps": ev["comps"], "side": [], "stderr": got["stderr"].strip()[:300]}
    for m in ev["msgs"]:
        keep = 2000 if m["k"] == 1 else (1500 if m["k"] >= got["recall_k"] else 80)
        rec["msgs"].append({"k": m["k"], "reqs": m["reqs"], "calls": m["calls"], "results": m["results"], "final": m["final"][:keep]})
    cur_msg = None
    for s in ev["side"]:
        if s.get("ev") == "msg":
            cur_msg = s.get("i")
        elif s.get("ev") == "delegate":
            rec["side"].append({"msg": cur_msg, "exit": s.get("exit"), "stop": s.get("stop"), "ms": s.get("ms"), "task": str(s.get("task", ""))[:600],
                                "calls": [c.get("tool") for c in s.get("calls") or []],
                                "results": [{"tool": x.get("tool"), "chars": x.get("chars"), "err": x.get("isError")} for x in s.get("results") or []],
                                "reqs": s.get("requests"), "finalChars": s.get("finalChars"), "err": s.get("err")})
        elif s.get("ev") == "blocked":
            rec.setdefault("blocked", []).append({"msg": s.get("i"), "tool": s.get("tool")})
        elif s.get("ev") == "compact":
            rec["compact_ev"] = {"ok": s.get("ok"), "ms": s.get("ms"), "err": s.get("err")}
    return rec


def msg_cost(m):
    xs = [r["cost"] for r in m["reqs"]] + [x["nested"] for x in m["results"] if x.get("nested") is not None]
    return None if None in xs or not xs else sum(xs)


def run_cost(rec):
    xs = [msg_cost(m) for m in rec["msgs"]] + [c["cost"] for c in rec["comps"]]
    return None if None in xs else sum(xs)


def parse_recall(final, truth):
    """回忆题的判定（和路线图里登记的一样）：-> {A: 对/不知道/错/格式不对, ...}"""
    out = {k: "格式不对" for k in NEEDLES}
    lines = [x for x in (final or "").splitlines() if "A=" in x]
    if not lines:
        return out
    line = lines[-1]
    pats = {"A": r"(?<!\d)\d{4}(?!\d)", "B": r"(?<![0-9a-f])[0-9a-f]{6}(?![0-9a-f])", "C": r"(?<!\d)\d{1,3}(?!\d)", "D": r"(?<![0-9a-f])[0-9a-f]{7}(?![0-9a-f])"}
    parts = {}
    for k, nxt in (("A", "B"), ("B", "C"), ("C", "D"), ("D", None)):
        m = re.search(r"%s\s*=(.*?)(?:;\s*%s\s*=|$)" % (k, nxt) if nxt else r"%s\s*=(.*)$" % k, line)
        parts[k] = m.group(1) if m else ""
    for k in NEEDLES:
        seg = parts[k]
        w = re.search(pats[k], seg.lower() if k in "BD" else seg)
        if w:
            out[k] = "对" if w.group(0) == truth[k] else "错"
        elif "unknown" in seg.lower():
            out[k] = "不知道"
    return out


def do_run(job, ctx):
    arm, rep = job
    if STOP["flag"] or (ctx["cap"] and STATE["spent_all"] >= ctx["cap"]):
        STATE["skipped"] += 1
        return None
    got = err = None
    tries = 0
    why = ""
    for tries in range(1, RETRIES + 2):
        got, err, _ = attempt(ctx, arm, rep, tries)
        probs = verify(arm, got) if got else []
        if got:
            c = run_cost(record(arm, rep, tries, got, ctx["prices"])) or 0
            STATE["spent_all"] += c
        if got and not probs:
            break
        why = ("核对没过：" + "；".join(probs)) if probs else (err or "?")
        if STOP["flag"]:
            break
        if tries < RETRIES + 1:
            say("    （%s #%d 第 %d 次没成：%s；用新种子重跑）" % (arm, rep, tries, why[:160]))
            time.sleep(3)
    if STOP["flag"] and (not got or probs):
        return None
    STATE["done"] += 1
    n = STATE["done"]
    if not got or probs:
        say("[%2d/%d] %-2s #%-2d 失败：%s" % (n, ctx["total"], arm, rep, why[:300]))
        return {"kind": "run", "arm": arm, "rep": rep, "err": why[:400], "tries": tries, "check_failed": bool(probs)}
    rec = record(arm, rep, tries, got, ctx["prices"])
    cost = run_cost(rec) or 0
    STATE["spent"] += cost
    STATE["secs"] += rec["secs"]
    rk = rec["recall_k"]
    rm = next((m for m in rec["msgs"] if m["k"] == rk), None)
    rc = parse_recall(rm["final"], rec["truth"]) if rm else {}
    fu = [cls(m["reqs"][0]["U"], m["reqs"][0]["T"]) for m in rec["msgs"][1:rk - 1] if m["reqs"]] if ARMS[arm]["kind"] == "rd" else []
    extra = ""
    if fu:
        extra += "  后续句 U=%s" % "/".join(str(m["reqs"][0]["U"]) for m in rec["msgs"][1:rk - 1] if m["reqs"])
    if rec["comps"]:
        extra += "  压缩 %s→%s" % (rec["comps"][0]["before"], rec["comps"][0]["after"])
    if arm == "D":
        extra += "  子代理 %d 次" % len(rec["side"])
    say("[%2d/%d] %-2s #%-2d 回忆 %s%s  %4.1f 秒  %s" % (n, ctx["total"], arm, rep, " ".join("%s%s" % (k, rc.get(k, "?")) for k in NEEDLES), extra, rec["secs"],
                                                       "" if not ctx["prices"] else "$%.6f" % cost))
    return rec


# ───────────────────────────── 预检 ─────────────────────────────
def preflight(ctx):
    """正式跑之前：用 D 臂的配置跑一句最简单的话，看子进程能不能起来、用量有没有交出来。"""
    log = make_log(seed_of("preflight"))
    with open(os.path.join(ctx["work"], "incident.log"), "w", encoding="utf-8") as f:
        f.write(log["text"])
    side = os.path.join(ctx["root"], "side-preflight.jsonl")
    msg = "Use the delegate tool with this task: Read ./incident.log and reply with only its first line."
    res, err = run_pi(ctx, pi_argv([msg], "delegate"), {"LAB8_LOG": side, "LAB8_NO_TOOLS": "", "LAB8_COMPACT_AFTER": "", "LAB8_CHILD_TOOLS": "read",
                                                       "LAB8_REPORT_USAGE": "1"}, min(ctx["timeout"], 180))
    if err:
        return False, "预检：%s" % err
    out, errtxt, rc = res
    ev = parse_events(out, ctx["prices"], log["truth"], log["run"])
    s = [x for x in read_side(side) if x.get("ev") == "delegate"]
    try:
        os.remove(side)
    except OSError:
        pass
    if rc != 0 and not ev["msgs"]:
        return False, "预检：pi 退出码 %s：%s" % (rc, (errtxt or out).strip()[-300:])
    if not s:
        return True, "预检：模型这次没有调 delegate（pi 正常退出）。D 臂的运行如果也这样，会记失败；先继续。"
    if s[0].get("exit") != 0 or s[0].get("stop") in ("error", "aborted"):
        return False, "预检：子进程没跑成（退出码 %s，停止原因 %s）：%s" % (s[0].get("exit"), s[0].get("stop"), (s[0].get("stderr") or s[0].get("err") or "")[:300])
    nested = [r["nested"] for m in ev["msgs"] for r in m["results"] if r["tool"] == "delegate"]
    return True, "预检：子进程跑通（%s 个请求，%.1f 秒），子代理的用量%s" % (len(s[0].get("requests") or []), (s[0].get("ms") or 0) / 1000.0,
                                                               "已交给 pi 汇总" if nested and nested[0] else "没有出现在工具结果里（A5、G6 会无法评）")


# ───────────────────────────── 汇总（只印数字，预测由评分脚本按登记的规则打） ─────────────────────────────
def med(v):
    v = sorted(v)
    n = len(v)
    return None if not n else (v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2)


def good(recs, arm):
    return [r for r in recs if r.get("arm") == arm and "err" not in r]


def summarize(recs, ctx):
    say("\n== 汇总（只印数字；预测对不对由评分脚本按路线图里登记的规则打）")
    ok = [r for r in recs if "err" not in r]
    say("运行 %d 次，成功 %d 次，失败 %d 次；跳过 %d 次" % (len(recs), len(ok), len(recs) - len(ok), STATE["skipped"]))
    pr = ctx["prices"]
    for arm in ARMS:
        rs = good(recs, arm)
        if not rs:
            say("  %-2s  —" % arm)
            continue
        cells = {k: {"对": 0, "不知道": 0, "错": 0, "格式不对": 0} for k in NEEDLES}
        for r in rs:
            m = next((x for x in r["msgs"] if x["k"] == r["recall_k"]), None)
            rc = parse_recall(m["final"], r["truth"]) if m else {}
            for k in NEEDLES:
                if k in rc:
                    cells[k][rc[k]] += 1
        s = "  %-2s %d 次  回忆题答对：%s" % (arm, len(rs), "  ".join("%s %d/%d" % (k, cells[k]["对"], len(rs)) for k in NEEDLES))
        if pr:
            cs = [run_cost(r) for r in rs if run_cost(r) is not None]
            s += "   每次运行花费中位数 $%.6f" % (med(cs) or 0)
        say(s)
    say("本次花费 $%.4f（记录下来的运行）；用时之和 %.1f 分钟；含失败的尝试共 $%.4f" % (STATE["spent"], STATE["secs"] / 60.0, STATE["spent_all"]))


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


def tool_version(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20).stdout.strip()
    except Exception:
        return "?"


def main():
    ap = argparse.ArgumentParser(description="第 8 课：子代理 delegate，和摘要会不会丢事实")
    ap.add_argument("--dry-run", action="store_true", help="不调模型：打印设计指纹、运行顺序、样例材料")
    ap.add_argument("--arms", help="只测几个臂，逗号分隔，例如 R,D（正式跑请不要加）")
    ap.add_argument("--reps", type=int, help="每个臂的次数（正式跑请不要加）")
    ap.add_argument("--model", help="provider/模型id，默认用 settings.json 里的默认模型（正式跑请不要加）")
    ap.add_argument("--max-cost", type=float, default=0.40, help="花费上限（美元），到了就不再开始新的运行；默认 0.40")
    ap.add_argument("--timeout", type=int, default=TIMEOUT, help="一个 pi 进程最多等多少秒；默认 %d" % TIMEOUT)
    ap.add_argument("--no-preflight", action="store_true", help="跳过预检（正式跑请不要加）")
    a = ap.parse_args()
    arms_sel = [x for x in (a.arms or "").split(",") if x]
    for x in arms_sel:
        if x not in ARMS:
            sys.exit("没有这个臂：%s（可选 %s）" % (x, ",".join(ARMS)))
    if not os.path.exists(EXT):
        sys.exit("找不到扩展文件 %s（要和这个脚本放在同一个目录）" % EXT)
    src, models, name, mid = pick_model(a.model)
    prices = model_prices(models, name, mid)
    pi = pi_bin()
    ver = tool_version([pi, "--version"])
    say("pi %s   node %s   python %s   模型 %s / %s   设计指纹 %s" % (ver, tool_version(["node", "--version"]), sys.version.split()[0], name, mid, design_id()))
    changed = [x for x, on in (("--arms %s" % a.arms, bool(a.arms)), ("--reps %s" % a.reps, a.reps is not None), ("--model %s" % a.model, bool(a.model)),
                               ("--timeout %s" % a.timeout, a.timeout != TIMEOUT), ("--no-preflight", a.no_preflight)) if on]
    if changed:
        say("！用了会改设计的选项：%s（不是登记的设计，评分脚本不会打分）" % " ".join(changed))
    say("设计：R %d 次、D %d 次、C0 %d 次、C1 %d 次；材料每次运行新生成，约 %d 字节；压缩保留 %d token（keepRecentTokens），C1 在第 %d 句后强制压缩；cacheWarming 在实验里关掉。" % (
        ARMS["R"]["reps"], ARMS["D"]["reps"], ARMS["C0"]["reps"], ARMS["C1"]["reps"], TARGET_BYTES, KEEP, COMPACT_AFTER))
    if prices:
        say("单价（每百万 token）：新输入 $%g，缓存读 $%g，缓存写 $%g，输出 $%g" % (prices["input"], prices["cacheRead"], prices["cacheWrite"], prices["output"]))
    else:
        say("（models.json 里这个模型没写价格，下面不算花费，也不设花费上限）")
    jobs = build_jobs(arms_sel, a.reps)
    say("共 %d 次运行：一个一个跑，不并行；花费上限 %s。第一行出来之前先等十几秒。\n" % (len(jobs), ("$%g" % a.max_cost) if prices and a.max_cost else "无"))
    if a.dry_run:
        say("== --dry-run：不调模型。运行顺序（固定种子打乱）：")
        for arm, rep in jobs:
            say("  %-2s #%d  %s" % (arm, rep, ARMS[arm]["doc"]))
        for i in (1, 2, 3):
            m = make_log(seed_of("lesson8/R/%d/1" % i))
            o, tr = m["offs"], m["truth"]
            say("\n  样例材料 %d：%d 字节、%d 行、标记 run=%s；针的位置（字符偏移）A %d、B %d、C %d、D %d；真值 A=%s B=%s C=%s（租户 %s）D=%s" % (
                i, m["bytes"], m["lines"], m["run"], o["A"], o["B"], o["C"], o["D"], tr["A"], tr["B"], tr["C"], tr["tenant"], tr["D"]))
        msgs, rk = messages_for("C1", "borealis", "demo")
        say("\n  C1 的 5 句（第 %d 句是回忆题）：" % rk)
        for i, mtxt in enumerate(msgs, 1):
            say("    第 %d 句（%d 字符）：%s…" % (i, len(mtxt), mtxt[:90].replace("\n", " ")))
        say("  设置：compaction.keepRecentTokens = %d，全部臂一样；C1 才会在第 %d 句后调 ctx.compact()；回忆题那一句扩展拦下所有工具调用。" % (KEEP, COMPACT_AFTER))
        return
    root = tempfile.mkdtemp(prefix="dl-lab-")
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
        agent = make_agent_dir(root, src, models, name, mid)
        ctx = {"pi": pi, "root": root, "work": work, "prices": prices, "cap": a.max_cost if prices else 0, "timeout": a.timeout, "agent": agent, "total": len(jobs)}
        if not a.no_preflight:
            ok, msg = preflight(ctx)
            say(msg + "\n")
            if not ok:
                sys.exit("预检没过，先不跑正式的。把上面这几行发给我。")
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
