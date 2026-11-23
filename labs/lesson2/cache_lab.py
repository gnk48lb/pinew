#!/usr/bin/env python3
"""cache_lab.py —— 缓存实验室：改前缀的不同位置，看账单怎么变。

用法：
  python3 cache_lab.py api       直接问网关（OpenAI 接口，和 mini_agent 同一条路）：9 个请求，约 1 分钟
  python3 cache_lab.py pi        用真 pi 跑 9 次（走 models.json 里这个模型指定的那条路），约 2–3 分钟
  python3 -u cache_lab.py ttl    挂机实验：缓存能活多久。约 65 分钟，每个时间点输出一行（挂后台用，见 README）

用的是你 ~/.pi/agent 里的网关、模型和价格；三个实验加起来最坏（一个缓存都没命中）约 3 美分，一般不到 1.5 美分。
每个实验的提示开头都带一个时间戳，所以“冷启动”一定是冷的，不会被你上一次运行的缓存污染。
"""
import argparse, json, os, shutil, signal, subprocess, sys, time, uuid

HERE = os.path.dirname(os.path.abspath(__file__))
for _d in (os.path.join(HERE, "..", "lesson1"), os.path.expanduser("~/pi/lesson1")):
    if os.path.exists(os.path.join(_d, "mini_agent.py")):
        sys.path.insert(0, _d)
        break
try:
    import mini_agent as m
except ImportError:
    sys.exit("找不到 mini_agent.py（应该在 ~/pi/lesson1/）。先运行：cd ~/pi && tar xzf pi-lesson2.tgz")

SENT = "The quick brown fox jumps over the lazy dog. "
SENT_MID = "The quick brown cat jumps over the lazy dog. "
U1 = "Reply with the single word: OK"
EFFORT = "low"


def say(*a):
    print(*a, flush=True)


# ───────────────────────────── 价格与表格 ─────────────────────────────
def prices(cfg):
    c = cfg.get("cost") or {}
    if not c:
        return None
    return {k: float(c.get(k) or 0) for k in ("input", "output", "cacheRead", "cacheWrite")}


def dollars(p, new, cached, out=0, write=0):
    return None if not p else (new * p["input"] + cached * p["cacheRead"] + out * p["output"] + write * p["cacheWrite"]) / 1e6


def money(x):
    return "      -" if x is None else "$%.6f" % x


def row_line(i, total, cached, out, cost, label):
    new = total - cached
    say("%-3s %6d %6d %6d %4.0f%%  %s  %s" % (i, total, cached, new, 100.0 * cached / max(1, total), money(cost), label))


HEAD = "#   总输入   命中 新输入 命中率    花费    场景（缓存读数按你的价格算；预期写在下一行）"


def failed(i, label, why):
    say("%-3s   （这一行没成功：%s）  %s" % (i, why, label))


def usage_parts(u):
    total = u.get("prompt_tokens", 0)
    cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("prompt_cache_hit_tokens") or 0
    return total, cached, u.get("completion_tokens", 0)


def make_system(nonce, n, mid=False):
    s = [SENT] * n
    if mid:
        s[n // 2] = SENT_MID
    return "%s. %s" % (nonce, "".join(s))


def tail_edit(s):
    i = s.rindex("dog")
    return s[:i] + "cat" + s[i + 3:]


def history_text(lines=120):
    return "Read this log, then reply with the single word OK.\n" + "".join("log line %04d: the quick brown fox jumps over the lazy dog\n" % i for i in range(lines))


# ───────────────────────────── 实验 1：直接问网关 ─────────────────────────────
def cmd_api(a):
    cfg = m.load_config(None)
    p = prices(cfg)
    say("模型 %s   网关 %s" % (cfg["model"], cfg["url"]))
    if cfg.get("note"):
        say(cfg["note"])
    if p:
        say("单价（每百万 token）：新输入 $%g，缓存读 $%g（便宜 %.0f 倍），输出 $%g，缓存写 $%g" % (
            p["input"], p["cacheRead"], p["input"] / p["cacheRead"] if p["cacheRead"] else 0, p["output"], p["cacheWrite"]))
    else:
        say("（models.json 里这个模型没写价格，所以下面不算花费，只看 token）")
    nonce = "lab-%d" % int(time.time())
    n = a.sentences
    base = [{"role": "system", "content": make_system(nonce, n)}, {"role": "user", "content": U1}]
    aff_a, aff_b = str(uuid.uuid4()), str(uuid.uuid4())
    state = {"reply": None}

    def msgs_append():
        r = state["reply"] or {}
        keep = {"role": "assistant", "content": r.get("content") or "OK"}
        if r.get("reasoning_content"):
            keep["reasoning_content"] = r["reasoning_content"]
        return base + [keep, {"role": "user", "content": "Reply with the single word: OK again"}]

    plan = [
        ("冷启动", lambda: base, None, "开头带了时间戳，所以一定是冷的：一个也不该命中"),
        ("原样再发一次", lambda: base, None, "整段和上一个请求相同：几乎全部命中（可能差一小块）"),
        ("追加一轮（agent 循环的下一轮）", msgs_append, None, "上一个请求的整段都在前缀里：几乎全部命中，只有新追加的几十个 token 付全价"),
        ("只改最后一条消息的最后一个词", lambda: [base[0], {"role": "user", "content": U1.replace("OK", "NO")}], None,
         "前面一字不差：命中几乎全部，只丢最后一小块"),
        ("改系统提示的中间一句", lambda: [{"role": "system", "content": make_system(nonce, n, mid=True)}, base[1]], None,
         "改动之前的命中，之后全部重新计费：大约命中一半"),
        ("改系统提示的开头（换时间戳）", lambda: [{"role": "system", "content": make_system(nonce + "x", n)}, base[1]], None,
         "开头就对不上：几乎全丢"),
        ("原样重发（同第 1 行），带 x-session-affinity=甲", lambda: base, {"x-session-affinity": aff_a},
         "pi 每个会话都会发这个头；这一行看带头之后命中有没有变"),
        ("原样重发，换成 x-session-affinity=乙", lambda: base, {"x-session-affinity": aff_b},
         "换了一个“会话身份”。命中还是很高 = 缓存不看会话；掉下去 = 缓存和会话身份有关"),
        ("原样重发，回到 x-session-affinity=甲", lambda: base, {"x-session-affinity": aff_a},
         "回到甲：如果第 8 行掉了、这一行又回来，缓存就是按会话身份分开存的"),
    ]
    say("\n" + HEAD)
    raws, tot_actual, tot_cold, ok = [], 0.0, 0.0, 0
    for i, (label, build, hdr, expect) in enumerate(plan, 1):
        c2 = dict(cfg, headers=dict(cfg["headers"], **hdr)) if hdr else cfg
        try:
            reply = m.chat(c2, build(), EFFORT)
        except SystemExit as e:
            failed(i, label, str(e)[:120])
            continue
        u = reply.get("usage") or {}
        if i == 2:
            state["reply"] = reply["choices"][0]["message"]
        total, cached, out = usage_parts(u)
        cost = dollars(p, total - cached, cached, out)
        row_line(i, total, cached, out, cost, label)
        say("%s预期：%s" % (" " * 40, expect))
        raws.append((i, u))
        ok += 1
        if cost is not None:
            tot_actual += cost
            tot_cold += dollars(p, total, 0, out)
    if ok and not any(("prompt_tokens_details" in u or "prompt_cache_hit_tokens" in u) for _, u in raws):
        say("\n！网关的 usage 里没有缓存读数字段（prompt_tokens_details.cached_tokens），上面的“命中”全是 0，不能当真。")
    if tot_cold:
        say("\n这 %d 个请求如果一个缓存也没命中要 $%.6f，实际 $%.6f（省了 %.0f%%）。" % (ok, tot_cold, tot_actual, 100 * (1 - tot_actual / tot_cold)))
    say("\n== 原始 usage（贴给我的时候别删）")
    for i, u in raws:
        say("请求 %d  %s" % (i, json.dumps(u, ensure_ascii=False)))
    say("\n怎么读：第 1 行冷、第 2 行热，差的就是“缓存读”和“新输入”的价差；第 3–6 行是同一条规则：从第一个 token 起逐个比对，"
        "第一处不同之后的全部重新计费。命中的数字常常是 256 的整数倍（缓存按“块”存），所以改一个字可能丢掉一整块。")


# ───────────────────────────── 实验 2：用真 pi ─────────────────────────────
def pi_bin():
    b = os.environ.get("PI_BIN") or shutil.which("pi")
    if not b or not (os.access(b, os.X_OK) or shutil.which(b)):
        sys.exit("找不到可以运行的 pi（%s）。可以先 export PI_BIN=/路径/pi" % (b or "PATH 里没有 pi"))
    return b


def run_pi(pi, work, prompt, flags, timeout=300):
    """跑一次 `pi -p … --mode json`，把所有轮次的用量加起来。返回 (用量字典, 失败原因或 None)。"""
    try:
        r = subprocess.run([pi, "-p", prompt, "--mode", "json"] + flags, cwd=work, capture_output=True, text=True,
                           timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, "%s: %s" % (type(e).__name__, e)
    tot = {"input": 0, "cacheRead": 0, "cacheWrite": 0, "output": 0, "reasoning": 0, "cost": 0.0, "turns": 0, "raw": []}
    err = None
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") == "turn_end":
            msg = e.get("message") or {}
            if msg.get("stopReason") in ("error", "aborted"):
                err = msg.get("errorMessage") or msg.get("stopReason")
            u = msg.get("usage") or {}
            tot["turns"] += 1
            tot["raw"].append(u)
            for k in ("input", "cacheRead", "cacheWrite", "output", "reasoning"):
                tot[k] += u.get(k, 0) or 0
            tot["cost"] += (u.get("cost") or {}).get("total", 0) or 0
    if not tot["turns"]:
        return None, "退出码 %s，没有收到用量：%s" % (r.returncode, (r.stderr or r.stdout).strip()[-160:])
    if err and not (tot["input"] + tot["cacheRead"] + tot["cacheWrite"]):
        return None, "pi 报错：%s" % str(err).strip()[:160]
    return tot, None


def cmd_pi(a):
    pi = pi_bin()
    work = os.path.expanduser(a.work)
    os.makedirs(work, exist_ok=True)
    sdir = os.path.join(work, "sessions-%d" % int(time.time()))
    os.makedirs(sdir)
    ver = subprocess.run([pi, "--version"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
    say("pi %s   工作目录 %s   会话存放 %s（跑完删除）" % (ver, work, sdir))
    nonce = "lab-%d" % int(time.time())
    n = a.sentences
    hist = history_text()
    ctrl = ["--no-extensions", "--no-context-files", "--no-skills", "--no-prompt-templates", "--session-dir", sdir]
    sess_a, sess_b = str(uuid.uuid4()), str(uuid.uuid4())
    first = hist
    again = "Reply with the single word: OK again"
    plan = [
        ("新会话，冷启动（系统提示 + 约 2k token 的“历史”）", first, make_system(nonce, n), sess_a, "开头带时间戳，应当是冷的"),
        ("同一会话，再问一句（追加）", again, make_system(nonce, n), sess_a, "追加：上一个请求的整段都在前缀里，几乎全部命中"),
        ("同一会话，系统提示最后一句改了一个词", again, tail_edit(make_system(nonce, n)), sess_a,
         "系统提示改在末尾：它前面的命中；排在它后面的整段对话历史重新计费"),
        ("同一会话，系统提示中间改了一个词", again, make_system(nonce, n, mid=True), sess_a,
         "改在中间：中间之前命中，之后的系统提示和整段历史都重新计费"),
        ("同一会话，系统提示开头改了（换时间戳）", again, make_system(nonce + "x", n), sess_a, "改在最开头：几乎全丢"),
        ("换一个新会话，提示一字不差", first, make_system(nonce, n), sess_b,
         "只是换了会话。命中很高 = 缓存不看会话；很低 = 缓存和会话绑定"),
        ("不存会话（--no-session），提示一字不差", first, make_system(nonce, n), None, "再来一个样本：同上"),
    ]
    say("\n" + HEAD)
    raws = []
    try:
        for i, (label, prompt, sysm, sid, expect) in enumerate(plan, 1):
            flags = ctrl + ["--system-prompt", sysm] + (["--session-id", sid] if sid else ["--no-session"])
            tot, why = run_pi(pi, work, prompt, flags)
            if not tot:
                failed(i, label, why)
                continue
            total = tot["input"] + tot["cacheRead"] + tot["cacheWrite"]
            row_line(i, total, tot["cacheRead"], tot["output"], tot["cost"] or None, label + ("" if not tot["cacheWrite"] else "（其中缓存写 %d）" % tot["cacheWrite"]))
            say("%s预期：%s" % (" " * 40, expect))
            raws.append((i, tot["raw"]))
        say("\n== 你的真实配置：带不带 AGENTS.md（用 pi 默认的系统提示，两次都是新会话）")
        rows = []
        for tag, extra in (("带 AGENTS.md（你现在的默认）", []), ("不带（-nc）", ["-nc"])):
            tot, why = run_pi(pi, work, U1, ["--no-session"] + extra)
            if not tot:
                failed("D", tag, why)
                rows.append(None)
                continue
            total = tot["input"] + tot["cacheRead"] + tot["cacheWrite"]
            rows.append(total)
            row_line("D", total, tot["cacheRead"], tot["output"], tot["cost"] or None, tag)
            raws.append(("D " + tag, tot["raw"]))
        if rows[0] is not None and rows[1] is not None and rows[0] > rows[1]:
            d = rows[0] - rows[1]
            lm = None
            try:
                lm = prices(m.load_config(None))
            except SystemExit:
                pass
            say("\nAGENTS.md 让每个请求多了 %d 个 token（两个总输入之差，和缓存无关）。" % d)
            if lm:
                say("按你的价格：它在冷启动那一次值 $%.6f，之后每一轮（命中缓存）只值 $%.7f；一个 30 轮的会话总共约 $%.6f。" % (
                    d * lm["input"] / 1e6, d * lm["cacheRead"] / 1e6, (d * lm["input"] + 29 * d * lm["cacheRead"]) / 1e6))
        elif rows[0] is not None and rows[1] is not None:
            say("\n两次的总输入没有差别：这个目录里 pi 没有读到 AGENTS.md（先确认第 0 课装好了 v1，并且在练习目录里运行）。")
    finally:
        if not a.keep:
            shutil.rmtree(sdir, ignore_errors=True)
    say("\n== pi 报的原始用量（贴给我的时候别删）")
    for i, raw in raws:
        for u in raw:
            say("%s  %s" % (i, json.dumps({k: v for k, v in u.items() if k != "cost"}, ensure_ascii=False)))
    say("\n注意：第 1 行的“命中”如果不是 0，那是 pi 的开头（工具说明书一类）和你之前的运行共享的那一小段。")


# ───────────────────────────── 实验 3：缓存能活多久 ─────────────────────────────
def cmd_ttl(a):
    minutes = [float(x) for x in a.minutes.split(",") if x.strip()]
    paths = [x.strip() for x in a.paths.split(",") if x.strip()]
    for x in paths:
        if x not in ("api", "pi"):
            sys.exit("--paths 只能是 api、pi 或 api,pi")
    cfg = m.load_config(None) if "api" in paths else None
    pi = pi_bin() if "pi" in paths else None
    work = os.path.expanduser(a.work)
    os.makedirs(work, exist_ok=True)
    sdir = os.path.join(work, "sessions-ttl-%d" % int(time.time()))
    os.makedirs(sdir)
    t0 = time.time()
    stamp = lambda: "[%02d:%02d]" % divmod(int(time.time() - t0), 60)
    say("缓存寿命实验：间隔（分钟）%s，路径 %s。每个点用一个独立的前缀：先各发一次，再按间隔各重发一次，看还剩多少命中。" % (minutes, "+".join(paths)))
    say("预计 %.0f 分钟；中途 Ctrl+C（放在后台时用 kill 进程号）会把已经有的结果打印出来。" % (max(minutes) + 3))
    def _stop(signum, frame):            # 后台任务的 shell 会让它忽略 Ctrl+C；显式接管 SIGINT/SIGTERM，这样 kill 也能收尾
        raise KeyboardInterrupt
    for sg in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sg, _stop)
    jobs = []
    for T in minutes:
        for path in paths:
            nonce = "ttl-%s-%g-%d" % (path, T, int(time.time()))
            jobs.append({"T": T, "path": path, "nonce": nonce, "sid": str(uuid.uuid4()), "res": None})
    n = a.sentences
    try:
        # 第一阶段：每个前缀先发一次
        for j in jobs:
            j["total1"], j["t1"], j["err"] = 0, None, None
            if j["path"] == "api":
                j["msgs"] = [{"role": "system", "content": make_system(j["nonce"], n)}, {"role": "user", "content": U1}]
                try:
                    u = m.chat(cfg, j["msgs"], EFFORT).get("usage") or {}
                    j["total1"] = usage_parts(u)[0]
                except SystemExit as e:
                    j["err"] = str(e)[:120]
            else:
                j["flags"] = ["--no-extensions", "--no-context-files", "--no-skills", "--no-prompt-templates", "--session-dir", sdir,
                              "--system-prompt", make_system(j["nonce"], n), "--session-id", j["sid"]]
                tot, why = run_pi(pi, work, U1, j["flags"])
                if tot:
                    j["total1"] = tot["input"] + tot["cacheRead"] + tot["cacheWrite"]
                else:
                    j["err"] = why
            j["t1"] = time.time()
            say("%s 第一次发出：%s，间隔 %g 分钟的那一组，总输入 %s%s" % (stamp(), j["path"], j["T"], j["total1"] or "?", "  失败：" + j["err"] if j["err"] else ""))
        # 第二阶段：按到期时间重发
        for j in sorted([x for x in jobs if not x["err"]], key=lambda x: x["t1"] + x["T"] * 60):
            wait = j["t1"] + j["T"] * 60 - time.time()
            if wait > 0:
                time.sleep(wait)
            age = time.time() - j["t1"]
            if j["path"] == "api":
                try:
                    u = m.chat(cfg, j["msgs"], EFFORT).get("usage") or {}
                    tot2, cached = usage_parts(u)[0], usage_parts(u)[1]
                except SystemExit as e:
                    j["res"] = {"age": age, "err": str(e)[:120]}
                    say("%s %s 间隔 %g 分钟：失败 %s" % (stamp(), j["path"], j["T"], j["res"]["err"]))
                    continue
            else:
                tot, why = run_pi(pi, work, "Reply with the single word: OK again", j["flags"])
                if not tot:
                    j["res"] = {"age": age, "err": why}
                    say("%s %s 间隔 %g 分钟：失败 %s" % (stamp(), j["path"], j["T"], why))
                    continue
                tot2, cached = tot["input"] + tot["cacheRead"] + tot["cacheWrite"], tot["cacheRead"]
            alive = cached >= 0.5 * j["total1"]
            j["res"] = {"age": age, "total": tot2, "cached": cached, "alive": alive}
            say("%s %s 间隔 %g 分钟（实际 %d 秒）：命中 %d / 第一次的 %d（%d%%）→ %s" % (
                stamp(), j["path"], j["T"], age, cached, j["total1"], 100 * cached / max(1, j["total1"]), "还活着" if alive else "已经失效"))
    except KeyboardInterrupt:
        say("\n（被中断，下面是已有的结果）")
    finally:
        shutil.rmtree(sdir, ignore_errors=True)
    say("\n== 汇总：每格 = 重发时命中的 token / 第一次的总输入，括号里是实际隔了多少秒")
    say("（pi = 真 pi，走你 models.json 指定的接口，带会话头；api = 直接问网关的 OpenAI 接口，不带会话头）")
    say("minutes     " + "   ".join("%-34s" % x for x in paths))
    for T in minutes:
        cells = []
        for path in paths:
            j = next((x for x in jobs if x["T"] == T and x["path"] == path), None)
            r = j and j["res"]
            if not r:
                cells.append("%-34s" % "（没有结果）")
            elif r.get("err"):
                cells.append("%-34s" % "失败")
            else:
                cells.append("%-34s" % ("%d/%d（%d 秒）%s" % (r["cached"], j["total1"], r["age"], "活" if r["alive"] else "死")))
        say("%-10g  %s" % (T, "   ".join(cells)))
    for path in paths:
        done = sorted([(x["T"], x["res"]["alive"]) for x in jobs if x["path"] == path and x["res"] and "alive" in x["res"]])
        if not done:
            continue
        live = [T for T, al in done if al]
        dead = [T for T, al in done if not al]
        say("%s 路径：最长还活着的间隔 %s；最短已失效的间隔 %s。" % (path, "%g 分钟" % max(live) if live else "没有（第一个点就已失效）",
                                                           "%g 分钟" % min(dead) if dead else "没有（试过的点都还活着，寿命比最长的点还长）"))
    say("（“活”= 命中至少是第一次总输入的一半。真正的寿命在“最长还活着”和“最短已失效”之间。）")


def main():
    ap = argparse.ArgumentParser(description="缓存实验室")
    sub = ap.add_subparsers(dest="cmd")
    for name, fn in (("api", cmd_api), ("pi", cmd_pi), ("ttl", cmd_ttl)):
        sp = sub.add_parser(name)
        sp.set_defaults(fn=fn)
        sp.add_argument("--sentences", type=int, default=300, help="系统提示里重复多少句（300 句约 3300 token）")
        if name != "api":
            sp.add_argument("--work", default="~/pi-practice/l2", help="pi 的工作目录")
        if name == "pi":
            sp.add_argument("--keep", action="store_true", help="跑完不删会话文件")
        if name == "ttl":
            sp.add_argument("--minutes", default="1,3,5,10,15,30,60", help="要测的间隔（分钟，逗号分隔，可以是小数）")
            sp.add_argument("--paths", default="pi,api", help="测哪条路：pi、api 或 pi,api")
    a = ap.parse_args()
    if not a.cmd:
        ap.print_help()
        sys.exit(2)
    a.fn(a)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass
