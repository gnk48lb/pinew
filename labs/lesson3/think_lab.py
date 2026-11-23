#!/usr/bin/env python3
"""think_lab.py —— 思考档位实验室：同一道题，各档位让模型“想”多久，账单里的输出 token 差多少。

用法：
  python3 think_lab.py                          三条路径 × 4 个档位 × 3 个任务 × 3 次 = 108 次请求，约 10 分钟
  python3 think_lab.py --reps 2                 快一点（72 次）
  python3 think_lab.py --paths pi-budget,pi-adaptive      只测 pi 的两种发法
  python3 think_lab.py --paths api              只测直接问网关

三条路径：
  pi-budget    真 pi，你现在的配置：线上发 thinking 预算（budget_tokens），不发 output_config.effort
  pi-adaptive  真 pi，用一份私有的 models.json 副本，给这个模型条目加 compat.forceAdaptiveThinking：
               线上改发 thinking={type: adaptive} 加 output_config.effort。你真正的配置一个字不动。
  api          不经过 pi，直接对网关发 OpenAI 接口的请求，带 reasoning_effort

用你 ~/.pi/agent 里的网关、模型、密钥和价格。预计花 0.1–0.2 美元（取决于模型想多久），花费到 --max-cost（默认 $0.30）就自动停。
"""
import argparse, copy, json, math, os, re, shutil, statistics, subprocess, sys, tempfile, threading, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (os.path.join(HERE, "..", "lesson1"), os.path.expanduser("~/pi/lesson1")):
    if os.path.exists(os.path.join(_d, "mini_agent.py")):
        sys.path.insert(0, _d)
        break
try:
    import mini_agent as m
except ImportError:
    sys.exit("找不到 mini_agent.py（应该在 ~/pi/lesson1/）。先运行：cd ~/pi && tar xzf pi-lesson3.tgz")

SYSTEM = "You are a helpful assistant."
TASKS = {   # 名字 -> (提示, 标准答案)
    "ok": ("Reply with the single word: OK", "OK"),
    "count": ("How many integers from 1 to 1000 (inclusive) are divisible by 3 or by 5, but not by 15? "
              "(A number divisible by 15 does not count.) Reply with just the final number.", 401),
    "subsets": ("How many four-digit numbers have strictly increasing digits (each digit larger than the one before it) "
                "and a digit sum of exactly 20? Reply with just the final number.", 12),
}
PUZZLES = ("count", "subsets")
PATHS = ("pi-budget", "pi-adaptive", "api")
LEVELS = ("low", "medium", "high", "max")
FLAT = (0.67, 1.5)          # R 落在这个区间：档位没有效果
EFFECTIVE = 0.6             # R 不大于它：档位有效
OK_LONG = 50                # 对照题“想了很久”的门槛（输出 token）

LOCK = threading.Lock()
STOP = threading.Event()
PROCS = set()
STATE = {"done": 0, "spent": 0.0, "skipped": 0}


def say(*a):
    with LOCK:
        print(*a, flush=True)


# ───────────────────────────── 价格 ─────────────────────────────
def prices(cfg):
    c = cfg.get("cost") or {}
    if not c:
        return None
    return {k: float(c.get(k) or 0) for k in ("input", "output", "cacheRead", "cacheWrite")}


def dollars(p, fresh, cached, out):
    return None if not p else (fresh * p["input"] + cached * p["cacheRead"] + out * p["output"]) / 1e6


# ───────────────────────────── 配置：找到模型、做私有配置目录 ─────────────────────────────
def agent_dir():
    return os.environ.get("PI_CODING_AGENT_DIR") or os.path.expanduser("~/.pi/agent")


def read_json(path):
    try:
        return json.load(open(path, encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}


def pick_model(model_arg):
    """和 mini_agent 同一套选法：--model provider/模型id，否则 settings.json 里的默认模型。返回 (配置目录, models.json 内容, provider 名, 模型 id)。"""
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


def make_private(root, variant, src, models, name, mid):
    """在 root 下做一份私有的 pi 配置目录：models.json 的副本（adaptive 变体给模型条目加 compat.forceAdaptiveThinking）、
    auth.json 的副本、最小的 settings.json。你真正的 ~/.pi/agent 一个字不动。"""
    d = os.path.join(root, variant)
    os.makedirs(d, mode=0o700)
    m2 = copy.deepcopy(models)
    ent = next(x for x in m2["providers"][name]["models"] if x.get("id") == mid)
    if variant == "adaptive":
        ent["compat"] = dict(ent.get("compat") or {}, forceAdaptiveThinking=True)
    files = {"models.json": json.dumps(m2, ensure_ascii=False, indent=1), "settings.json": json.dumps({"defaultProvider": name, "defaultModel": mid})}
    if os.path.exists(os.path.join(src, "auth.json")):
        files["auth.json"] = open(os.path.join(src, "auth.json"), encoding="utf-8-sig").read()
    for fn, text in files.items():
        fd = os.open(os.path.join(d, fn), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
    return d


# ───────────────────────────── 解析回复 ─────────────────────────────
def last_int(text):
    nums = re.findall(r"\d+", re.sub(r"(?<=\d),(?=\d{3}\b)", "", text or ""))
    return int(nums[-1]) if nums else None


def split_think(content):
    """OpenAI 接口有的网关把思考放在 content 里的 <think>…</think>。返回 (思考, 正文)。"""
    c = content or ""
    mt = re.match(r"(?s)\s*<think>(.*?)</think>\s*(.*)$", c)
    if mt:
        return mt.group(1), mt.group(2)
    if c.lstrip().startswith("<think>"):
        return c.lstrip()[7:], ""
    return "", c


def parse_pi(stdout):
    """pi --mode json 的输出 -> 这次运行的用量与回复。没有 turn_end 就返回 None。"""
    r = {"out": 0, "reasoning": 0, "inp": 0, "cached": 0, "cost_pi": 0.0, "text": "", "think_chars": 0, "dur": None, "stop": None, "err": None, "turns": 0}
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") != "turn_end":
            continue
        msg = e.get("message") or {}
        u = msg.get("usage") or {}
        r["turns"] += 1
        r["out"] += u.get("output", 0) or 0
        r["reasoning"] += u.get("reasoning", 0) or 0
        r["inp"] += (u.get("input", 0) or 0) + (u.get("cacheRead", 0) or 0) + (u.get("cacheWrite", 0) or 0)
        r["cached"] += u.get("cacheRead", 0) or 0
        r["cost_pi"] += (u.get("cost") or {}).get("total", 0) or 0
        r["stop"] = msg.get("stopReason")
        if msg.get("stopReason") in ("error", "aborted"):
            r["err"] = msg.get("errorMessage") or msg.get("stopReason")
        if msg.get("durationMs"):
            r["dur"] = (r["dur"] or 0) + msg["durationMs"] / 1000.0
        r["text"] = "".join(b.get("text", "") for b in (msg.get("content") or []) if isinstance(b, dict) and b.get("type") == "text")
        r["think_chars"] += sum(len(b.get("thinking", "")) for b in (msg.get("content") or []) if isinstance(b, dict) and b.get("type") == "thinking")
    return r if r["turns"] else None


# ───────────────────────────── 发一次请求 ─────────────────────────────
def run_pi_once(pi, agentdir, work, prompt, level, timeout):
    cmd = [pi, "-p", prompt, "--mode", "json", "--no-tools", "--no-extensions", "--no-context-files", "--no-skills", "--no-prompt-templates",
           "--no-session", "--system-prompt", SYSTEM, "--thinking", level]
    env = dict(os.environ, PI_CODING_AGENT_DIR=agentdir)
    t0 = time.time()
    try:
        p = subprocess.Popen(cmd, cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
    except OSError as e:
        return None, "%s: %s" % (type(e).__name__, e)
    with LOCK:
        PROCS.add(p)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        p.communicate()
        return None, "超时（%d 秒）" % timeout
    finally:
        with LOCK:
            PROCS.discard(p)
    r = parse_pi(out)
    if not r:
        return None, "退出码 %s，没有收到用量：%s" % (p.returncode, (err or out).strip()[-200:])
    if r["err"] and not r["out"]:
        return None, "pi 报错：%s" % str(r["err"]).strip()[:200]
    r["secs"] = r["dur"] if r["dur"] else time.time() - t0
    r["wall"] = time.time() - t0
    return r, None


def run_api_once(cfg, prompt, level, timeout, max_tokens):
    body = {"model": cfg["model"], "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}], "reasoning_effort": level}
    if max_tokens:
        body["max_tokens"] = max_tokens
    headers = {"Content-Type": "application/json", "User-Agent": "think-lab", "Authorization": "Bearer " + cfg["key"], **cfg["headers"]}
    t0 = time.time()
    try:
        reply = json.load(urllib.request.urlopen(urllib.request.Request(cfg["url"], json.dumps(body).encode(), headers), timeout=timeout))
    except urllib.error.HTTPError as e:
        return None, "HTTP %d：%s" % (e.code, e.read()[:200].decode("utf-8", "replace"))
    except (OSError, ValueError) as e:
        return None, "%s: %s" % (type(e).__name__, e)
    ch = (reply.get("choices") or [None])[0]
    if not ch:
        return None, "网关返回了没有 choices 的内容：%s" % json.dumps(reply)[:200]
    msg, u = ch.get("message") or {}, reply.get("usage") or {}
    think, text = split_think(msg.get("content"))
    rc = msg.get("reasoning_content") or ""
    det = (u.get("completion_tokens_details") or {})
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("prompt_cache_hit_tokens") or 0
    return {"out": u.get("completion_tokens", 0) or 0, "reasoning": det.get("reasoning_tokens"), "inp": u.get("prompt_tokens", 0) or 0, "cached": cached,
            "text": text, "think_chars": len(rc) + len(think), "stop": ch.get("finish_reason"), "secs": time.time() - t0}, None


TRANSIENT = ("429", "500", "502", "503", "504", "overloaded", "超时", "timed out", "Connection", "连不上", "Remote end")


def safe_job(job, ctx):
    try:
        return do_job(job, ctx)
    except Exception as e:  # noqa  一个运行里的意外不该拖垮整个实验：记成失败
        path, task, level, rep = job
        with LOCK:
            STATE["done"] += 1
        say("[%3d/%d] %-11s %-7s %-6s #%d  失败：脚本内部错误 %s: %s" % (STATE["done"], ctx["total"], path, task, level, rep, type(e).__name__, e))
        return {"path": path, "task": task, "level": level, "rep": rep, "err": "脚本内部错误 %s: %s" % (type(e).__name__, e)}


def do_job(job, ctx):
    path, task, level, rep = job
    prompt, expect = TASKS[task]
    if STOP.is_set():
        return None
    with LOCK:
        if ctx["cap"] and STATE["spent"] >= ctx["cap"]:
            STATE["skipped"] += 1
            return None
    rec, err = None, None
    for attempt in (1, 2):
        if path == "api":
            rec, err = run_api_once(ctx["cfg"], prompt, level, ctx["timeout"], ctx["api_max_tokens"])
        else:
            rec, err = run_pi_once(ctx["pi"], ctx["dirs"][path.split("-", 1)[1]], ctx["work"], prompt, level, ctx["timeout"])
        retry = path != "api" or any(t in (err or "") for t in TRANSIENT)      # pi 失败一律再试一次；api 只在像是临时故障时再试
        if rec or STOP.is_set() or not retry:
            break
        if attempt == 1:
            time.sleep(3)
    base = {"path": path, "task": task, "level": level, "rep": rep}
    if not rec and STOP.is_set():
        return None                      # Ctrl+C 时被杀掉的那几次：不算失败，也不进数据
    if not rec:
        out = dict(base, err=(err or "?")[:200])
        with LOCK:
            STATE["done"] += 1
            n = STATE["done"]
        say("[%3d/%d] %-11s %-7s %-6s #%d  失败：%s" % (n, ctx["total"], path, task, level, rep, out["err"]))
        return out
    text = rec["text"] or ""
    ans = last_int(text)
    truncated = rec["stop"] in ("length", "max_tokens")      # 事先写好的规则：输出被截断算错
    correct = (not truncated) and ((ans == expect) if task != "ok" else bool(re.search(r"\bok\b", text, re.I)))
    fresh = max(0, rec["inp"] - rec["cached"])
    cost = dollars(ctx["prices"], fresh, rec["cached"], rec["out"])
    out = dict(base, out=rec["out"], reasoning=rec["reasoning"], think_chars=rec["think_chars"], inp=rec["inp"], cached=rec["cached"],
               secs=round(rec["secs"], 1), answer=(ans if task != "ok" else text.strip()[:20]), correct=correct, stop=rec["stop"],
               empty=not text.strip(), cost=None if cost is None else round(cost, 7))
    with LOCK:
        STATE["done"] += 1
        STATE["spent"] += cost or 0
        n = STATE["done"]
    th = "" if rec["reasoning"] is None else "（思考 %d）" % rec["reasoning"]
    say("[%3d/%d] %-11s %-7s %-6s #%d  输出 %5d%s  %5.1f 秒  %s  %s" % (
        n, ctx["total"], path, task, level, rep, rec["out"], th, rec["secs"],
        ("答 %s %s" % (out["answer"], "✓" if correct else "✗")) if task != "ok" else ("回 %r" % text.strip()[:12]),
        "" if cost is None else "$%.6f" % cost))
    return out


# ───────────────────────────── 汇总 ─────────────────────────────
def good(recs):
    return [r for r in recs if r and "err" not in r]


def cell(recs, path, task, level):
    return [r["out"] for r in good(recs) if r["path"] == path and r["task"] == task and r["level"] == level]


def med(recs, path, task, level):
    v = cell(recs, path, task, level)
    return statistics.median(v) if v else None


def geo_ratio(fn_num, fn_den):
    logs = []
    for t in PUZZLES:
        a, b = fn_num(t), fn_den(t)
        if a and b:
            logs.append(math.log(a / b))
    return math.exp(sum(logs) / len(logs)) if logs else None


def reading(r):
    if r is None:
        return "没有足够的数据"
    if r <= EFFECTIVE:
        return "档位有效（≤%g）" % EFFECTIVE
    if FLAT[0] <= r <= FLAT[1]:
        return "没有效果（%g–%g）" % FLAT
    return "说不清（在 %g 和 %g 之间，或高于 %g）" % (EFFECTIVE, FLAT[0], FLAT[1])


def fmt(x):
    return "—" if x is None else "%.2f" % x


def summarize(recs, paths, levels, tasks, total, ctx):
    g = good(recs)
    say("\n== 汇总一：输出 token（含思考），每格的中位数（括号里是最小–最大，n 是成功的次数）")
    say("%-11s %-7s " % ("path", "level") + " ".join("%-26s" % t for t in tasks))
    for p in paths:
        for lv in levels:
            cells = []
            for t in tasks:
                v = cell(recs, p, t, lv)
                cells.append("%-26s" % ("—" if not v else "%d (%d-%d) n=%d" % (statistics.median(v), min(v), max(v), len(v))))
            say("%-11s %-7s " % (p, lv) + " ".join(cells))
    say("\n== 汇总二：谜题答对 / 次数，谜题的中位耗时（秒），花费合计")
    say("%-11s %-7s %-8s %-8s %-10s %-10s" % ("path", "level", "count", "subsets", "secs", "cost"))
    for p in paths:
        for lv in levels:
            row = []
            for t in PUZZLES:
                rs = [r for r in g if r["path"] == p and r["task"] == t and r["level"] == lv]
                row.append("%d/%d" % (sum(1 for r in rs if r["correct"]), len(rs)) if t in tasks else "—")
            allr = [r for r in g if r["path"] == p and r["level"] == lv]
            secs = [r["secs"] for r in allr if r["task"] in PUZZLES]
            cost = sum(r["cost"] or 0 for r in allr)
            say("%-11s %-7s %-8s %-8s %-10s %-10s" % (p, lv, row[0], row[1], "%.1f" % statistics.median(secs) if secs else "—", "$%.5f" % cost if ctx["prices"] else "—"))
    say("\n== 判定（算法事先写好：每格取中位数，R = 两个谜题上 [low 的中位数 ÷ max 的中位数] 的几何平均数）")
    R = {}
    for p in paths:
        R[p] = geo_ratio(lambda t, p=p: med(recs, p, t, "low"), lambda t, p=p: med(recs, p, t, "max"))
        say("  R(%-11s) = %s   → %s" % (p, fmt(R[p]), reading(R[p])))
    if "pi-budget" in paths:
        n = geo_ratio(lambda t: med(recs, "pi-budget", t, "high"), lambda t: med(recs, "pi-budget", t, "max"))
        say("  噪声检查：pi-budget 的 high ÷ max = %s（这两档在线上发出的请求完全相同，所以这个比值只是噪声）→ %s" % (
            fmt(n), "没有数据" if n is None else ("在 %g–%g 之内，测得出 1.5 倍以上的差别" % FLAT if FLAT[0] <= n <= FLAT[1] else "超出 %g–%g，这次测不出 1.5 倍以内的差别" % FLAT)))
    if "pi-budget" in paths and "pi-adaptive" in paths:
        b2a = geo_ratio(lambda t: med(recs, "pi-budget", t, "max"), lambda t: med(recs, "pi-adaptive", t, "max"))
        say("  max 档对比：pi-budget ÷ pi-adaptive = %s → %s" % (fmt(b2a), "没有数据" if b2a is None else ("相当（%g–%g）" % FLAT if FLAT[0] <= b2a <= FLAT[1] else "不同")))
    for p in ("pi-budget", "pi-adaptive"):
        if p in paths and "ok" in tasks:
            rs = [r for r in g if r["path"] == p and r["task"] == "ok"]
            say("  对照题 ok：%s 有 %d/%d 次输出 ≥ %d token（最大 %s）" % (p, sum(1 for r in rs if r["out"] >= OK_LONG), len(rs), OK_LONG, max((r["out"] for r in rs), default="—")))
    if "max" in levels:
        parts, tot, den = [], 0, 0
        for p in paths:
            rs = [r for r in g if r["path"] == p and r["task"] in PUZZLES and r["level"] == "max"]
            parts.append("%s %d/%d" % (p, sum(1 for r in rs if r["correct"]), len(rs)))
            tot += sum(1 for r in rs if r["correct"])
            den += len(rs)
        say("  max 档谜题答对：%s，合计 %d/%d" % ("，".join(parts), tot, den))
    if "pi-adaptive" in paths and "low" in levels and "max" in levels:
        lo = [r for r in g if r["path"] == "pi-adaptive" and r["task"] in PUZZLES and r["level"] == "low"]
        mx = [r for r in g if r["path"] == "pi-adaptive" and r["task"] in PUZZLES and r["level"] == "max"]
        say("  pi-adaptive：low 档谜题答对 %d/%d，max 档 %d/%d" % (sum(1 for r in lo if r["correct"]), len(lo), sum(1 for r in mx if r["correct"]), len(mx)))
    if "api" in paths:
        vals = [r["reasoning"] for r in g if r["path"] == "api"]
        rep_ = [v for v in vals if v is not None]
        chars = [r["think_chars"] for r in g if r["path"] == "api" and r["task"] in PUZZLES]
        say("  api 的 reasoning_tokens：%s；思考文字（reasoning_content 或 <think>）的字符数中位数 %s（谜题），输出 token 中位数 %s" % (
            "网关没报这个字段" if not rep_ else ("全部为 0" if max(rep_) == 0 else "出现了非零值，最大 %d" % max(rep_)),
            "%d" % statistics.median(chars) if chars else "—",
            "%d" % statistics.median([r["out"] for r in g if r["path"] == "api" and r["task"] in PUZZLES]) if chars else "—"))
    pir = [r for r in g if r["path"].startswith("pi-") and r["task"] in PUZZLES]
    if pir:
        rep_pi = [r for r in pir if r["reasoning"]]
        say("  pi 报了思考 token（usage.reasoning > 0）的谜题运行：%d/%d" % (len(rep_pi), len(pir)))
    bad = [r for r in recs if r and "err" in r]
    empty = [r for r in g if r["empty"]]
    trunc = [r for r in g if r["stop"] in ("length", "max_tokens")]
    say("  失败 %d 次，空回复 %d 次，被截断 %d 次（共完成 %d 次请求，计划 %d 次）" % (len(bad), len(empty), len(trunc), len(recs), total))
    if bad:
        for r in bad[:5]:
            say("    失败样例：%s %s %s #%d：%s" % (r["path"], r["task"], r["level"], r["rep"], r["err"]))
    with LOCK:
        skipped = STATE["skipped"]
    if skipped:
        say("  花费到了上限（$%.4f），剩下 %d 次没有跑；上面的汇总只含已经跑的。" % (STATE["spent"], skipped))
    if ctx["prices"]:
        say("  本次花费 $%.4f" % STATE["spent"])
    say("\n怎么读：R 越小，说明低档位真的让模型少想了；R 接近 1，说明档位没有改变模型想多久。pi-budget 是你现在的配置，"
        "pi-adaptive 是只改了一行配置的版本，api 是不经过 pi 直接问网关。温度固定为 1，每格只有几个样本，别把中位数当精确值。")
    say("\n== 数据（每次运行一行，贴给我的时候别删）")
    for r in recs:
        if r:
            say("数据 " + json.dumps(r, ensure_ascii=False, sort_keys=True))


# ───────────────────────────── 主程序 ─────────────────────────────
def pi_bin():
    b = os.environ.get("PI_BIN") or shutil.which("pi")
    if not b or not (os.access(b, os.X_OK) or shutil.which(b)):
        sys.exit("找不到可以运行的 pi（%s）。可以先 export PI_BIN=/路径/pi" % (b or "PATH 里没有 pi"))
    return b


def main():
    ap = argparse.ArgumentParser(description="思考档位实验室")
    ap.add_argument("--paths", default=",".join(PATHS), help="测哪几条路径，逗号分隔：pi-budget、pi-adaptive、api（默认全部）")
    ap.add_argument("--levels", default=",".join(LEVELS), help="要测的档位，逗号分隔（默认 low,medium,high,max）")
    ap.add_argument("--tasks", default=",".join(TASKS), help="任务：ok（对照）、count、subsets")
    ap.add_argument("--reps", type=int, default=3, help="每格重复几次（默认 3）")
    ap.add_argument("--parallel", type=int, default=3, help="同时跑几个请求（默认 3；1 = 一个一个跑）")
    ap.add_argument("--max-cost", type=float, default=0.30, help="花费上限（美元，默认 0.30）：超过就不再发新请求；0 = 不设上限")
    ap.add_argument("--timeout", type=int, default=300, help="单次请求最多等几秒（默认 300）")
    ap.add_argument("--api-max-tokens", type=int, default=0, help="api 路径的 max_tokens（默认 0 = 不发这个字段）")
    ap.add_argument("--model", help="provider/模型id；默认用 pi 的 settings.json 里的默认模型")
    a = ap.parse_args()
    paths = [x.strip() for x in a.paths.split(",") if x.strip()]
    levels = [x.strip() for x in a.levels.split(",") if x.strip()]
    tasks = [x.strip() for x in a.tasks.split(",") if x.strip()]
    for x in paths:
        if x not in PATHS:
            sys.exit("--paths 里只能有 %s" % "、".join(PATHS))
    for x in tasks:
        if x not in TASKS:
            sys.exit("--tasks 里只能有 %s" % "、".join(TASKS))
    for x in levels:
        if x not in ("low", "medium", "high", "xhigh", "max"):
            sys.exit("--levels 里只能有 low、medium、high、xhigh、max")
    cfg = m.load_config(a.model)
    p = prices(cfg)
    pi = pi_bin() if any(x.startswith("pi-") for x in paths) else None
    src, models, name, mid = pick_model(a.model)
    root = tempfile.mkdtemp(prefix="think-lab-")
    work = os.path.join(root, "work")
    os.makedirs(work)
    ctx = {"cfg": cfg, "pi": pi, "dirs": {}, "work": work, "prices": p, "cap": a.max_cost, "timeout": a.timeout, "api_max_tokens": a.api_max_tokens}
    jobs = [(path, t, lv, rep) for rep in range(1, a.reps + 1) for t in tasks for lv in levels for path in paths]
    ctx["total"] = len(jobs)
    recs = []
    try:
        if pi:
            ver = subprocess.run([pi, "--version"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
            say("pi %s   模型 %s / %s" % (ver, name, mid))
            for variant in sorted({x.split("-", 1)[1] for x in paths if x.startswith("pi-")}):
                ctx["dirs"][variant] = make_private(root, variant, src, models, name, mid)
            say("私有配置目录 %s（你的 models.json、auth.json 的副本 + 最小的 settings.json，跑完删除；%s）" % (
                root, "pi-adaptive 的副本里给这个模型条目加了 compat.forceAdaptiveThinking" if "adaptive" in ctx["dirs"] else "没有改动"))
        if "api" in paths:
            say("api 路径：%s   模型 %s" % (cfg["url"], cfg["model"]))
            if cfg.get("note"):
                say(cfg["note"])
        if p:
            say("单价（每百万 token）：新输入 $%g，缓存读 $%g，输出 $%g（输出是缓存读的 %.0f 倍）" % (p["input"], p["cacheRead"], p["output"], p["output"] / p["cacheRead"] if p["cacheRead"] else 0))
        else:
            say("（models.json 里这个模型没写价格，下面不算花费，也不设花费上限）")
            ctx["cap"] = 0
        say("共 %d 次请求（%d 条路径 × %d 个档位 × %d 个任务 × %d 次），同时跑 %d 个；花费上限 %s。第一行出来之前先等几秒。\n" % (
            len(jobs), len(paths), len(levels), len(tasks), a.reps, a.parallel, "$%.2f" % a.max_cost if ctx["cap"] else "无"))
        ex = ThreadPoolExecutor(max_workers=max(1, a.parallel))
        futs = [ex.submit(safe_job, j, ctx) for j in jobs]
        try:
            for f in futs:
                recs.append(f.result())
        except KeyboardInterrupt:
            STOP.set()
            say("\n（被中断，下面是已有的结果）")
            with LOCK:
                for pr in list(PROCS):
                    try:
                        pr.kill()
                    except OSError:
                        pass
            recs = [f.result() for f in futs if f.done() and not f.cancelled()]
            ex.shutdown(wait=False, cancel_futures=True)
        recs = [r for r in recs if r]
        summarize(recs, paths, levels, tasks, len(jobs), ctx)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if STOP.is_set():
        os._exit(130)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass
