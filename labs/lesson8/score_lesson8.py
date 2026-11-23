#!/usr/bin/env python3
"""score_lesson8.py —— 第 8 课的评分脚本（冻结：数据到之前就写好，之后不改）。
用法：python3 score_lesson8.py ex8.log
按路线图 11-19 第 8 课那一行登记的规则：先过 G1–G7，再逐条打 A1–A5、B1–B6、K1–K4、Z1–Z3，
最后印读法 Q1–Q4 用的数字（格子表、Fisher、悬崖、读法分支）。
检查日志头（pi 版本、模型、设计指纹）和登记不一致时不打分，先问用户（--ignore-header 只给测试用）。"""
import json
import math
import re
import statistics
import sys

FP, PIV, MODEL = "3ad3a7f03eab", "1.5.0", "MiniMax-M3.1-Flash"
REG_PRICE = {"input": 0.15, "cacheRead": 0.015, "output": 0.6}      # 登记时的单价（美元 / 百万 token）
MIN_T, HI, LO = 1500, 0.7, 0.3
NEED = {"R": 6, "D": 6, "C0": 3, "C1": 6}
NEEDLES = "ABCD"
CONF = {"A1": 90, "A2": 70, "A3": 65, "A4": 65, "A5": 80, "B1": 75, "B2": 80, "B3": 85, "B4": 60, "B5": 70, "B6": 60,
        "K1": 85, "K2": 65, "K3": 75, "K4": 80, "Z1": 75, "Z2": 75, "Z3": 70}
PATS = {"A": r"(?<!\d)\d{4}(?!\d)", "B": r"(?<![0-9a-f])[0-9a-f]{6}(?![0-9a-f])",
        "C": r"(?<!\d)\d{1,3}(?!\d)", "D": r"(?<![0-9a-f])[0-9a-f]{7}(?![0-9a-f])"}


# ───────────────────────────── 小工具 ─────────────────────────────
def cls(U, T):
    if T >= MIN_T and U >= HI * T:
        return "整段重付"
    if U < LO * T:
        return "正常"
    return "待定"


def med(v):
    v = sorted(v)
    n = len(v)
    return None if not n else (v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2)


def fisher(a, b, c, d):
    """2×2 表 [[a, b], [c, d]] 的 Fisher 双侧精确检验：所有概率不大于观察到的那张表的表，概率加起来。"""
    n1, n2, k = a + b, c + d, a + c
    n = n1 + n2
    if n == 0 or n1 == 0 or n2 == 0:
        return None
    lo, hi = max(0, k - n2), min(k, n1)

    def w(x):
        return math.comb(n1, x) * math.comb(n2, k - x)
    obs = w(a)
    return sum(w(x) for x in range(lo, hi + 1) if w(x) <= obs) / math.comb(n, k)


def fmt_d(x):
    return "—" if x is None else "$%.6f" % x


def load(path):
    txt = open(path, encoding="utf-8-sig", errors="replace").read()
    hdr, prices = None, None
    for ln in txt.splitlines():
        m = re.match(r"^pi (\S+)(?:\s+node \S+)?(?:\s+python \S+)?\s+模型 (\S+) / (\S+)\s+设计指纹 ([0-9a-f]{12})\s*$", ln)
        if m and hdr is None:
            hdr = m.groups()
        m = re.match(r"^单价（每百万 token）：新输入 \$([0-9.]+)，缓存读 \$([0-9.]+)，缓存写 \$([0-9.]+)，输出 \$([0-9.]+)", ln)
        if m and prices is None:
            prices = {"input": float(m.group(1)), "cacheRead": float(m.group(2)), "cacheWrite": float(m.group(3)), "output": float(m.group(4))}
    runs, seen, dups = [], set(), 0
    opts = [ln for ln in txt.splitlines() if ln.startswith("！用了会改设计的选项")]
    for ln in txt.splitlines():
        if ln.startswith("数据 {"):
            try:
                r = json.loads(ln[3:])
            except ValueError:
                continue
            key = (r.get("arm"), r.get("rep"))
            if key in seen:
                dups += 1
                continue
            seen.add(key)
            runs.append(r)
    spent = None
    for m in re.finditer(r"本次花费 \$([0-9.]+)", txt):
        spent = float(m.group(1))
    return hdr, prices, runs, dups, spent, opts


# ───────────────────────────── 每次运行的量 ─────────────────────────────
def msg_cost(m):
    xs = [q["cost"] for q in m["reqs"]] + [x["nested"] for x in m["results"] if x.get("nested") is not None]
    return None if (not xs or None in xs) else sum(xs)


def run_total(r):
    xs = [msg_cost(m) for m in r["msgs"]] + [c["cost"] for c in r["comps"]]
    return None if (not xs or None in xs) else sum(xs)


def nested_total(r):
    return sum(x["nested"] for m in r["msgs"] for x in m["results"] if x.get("nested") is not None)


def msg_by_k(r, k):
    return next((m for m in r["msgs"] if m["k"] == k), None)


def first_req(m):
    return m["reqs"][0] if m and m["reqs"] else None


def parse_recall(final, truth):
    """登记的判定：取最后一条助手消息里含“A=”的最后一行；每针取第一个符合该针格式的词；
    等于真值“对”；没有符合格式的词且含 UNKNOWN“不知道”；有词但不等于真值“错”；其余（含找不到这一行）“格式不对”。"""
    out = {k: "格式不对" for k in NEEDLES}
    lines = [x for x in (final or "").splitlines() if "A=" in x]
    if not lines:
        return out
    line = lines[-1]
    parts = {}
    for k, nxt in (("A", "B"), ("B", "C"), ("C", "D"), ("D", None)):
        m = re.search((r"%s\s*=(.*?)(?:;\s*%s\s*=|$)" % (k, nxt)) if nxt else (r"%s\s*=(.*)$" % k), line)
        parts[k] = m.group(1) if m else ""
    for k in NEEDLES:
        seg = parts[k]
        w = re.search(PATS[k], seg.lower() if k in "BD" else seg)
        if w:
            out[k] = "对" if w.group(0) == truth[k] else "错"
        elif "unknown" in seg.lower():
            out[k] = "不知道"
    return out


def mentions(text, truth):
    """“提到”：A、B、D 的值出现在文字里；C 要租户名和数同时出现（数是独立的数字）。"""
    t = text or ""
    return {"A": truth["A"] in t, "B": truth["B"] in t, "D": truth["D"] in t,
            "C": truth["tenant"] in t and re.search(r"(?<!\d)%s(?!\d)" % re.escape(truth["C"]), t) is not None}


def invalid_reasons(r):
    """G2、G3、G7：不满足的运行记无效。"""
    why = []
    arm, ms = r["arm"], r["msgs"]
    need = 8 if arm in ("R", "D") else 5
    if len(ms) < need or any(msg_by_k(r, k) is None for k in range(1, need + 1)):
        return ["句数不够（%d/%d）" % (len(ms), need)]
    m1 = ms[0]
    if arm == "D":
        dl = [c for c in m1["calls"] if c["tool"] == "delegate"]
        sd = [s for s in r.get("side", []) if s.get("msg") == 1] or r.get("side", [])[:len(dl)]
        if not dl:
            why.append("G2：m1 没有 delegate 调用")
        elif not sd:
            why.append("G2：旁路日志里没有 m1 的子进程")
        else:
            s0 = sd[0]
            if s0.get("exit") != 0:
                why.append("G2：子进程退出码 %s" % s0.get("exit"))
            if not any(x.get("tool") == "read" and not x.get("err") for x in s0.get("results") or []):
                why.append("G2：子进程里没有成功的 read 结果")
    else:
        rd = [x for x in m1["results"] if x["tool"] == "read" and x.get("marker")]
        if not rd:
            why.append("G2：m1 里没有含标记行的 read 结果")
        else:
            g = rd[0].get("g7") or {}
            if not all(g.get(k) == 1 for k in NEEDLES):
                why.append("G7：真值在文件里的次数不是各 1 次 %s" % g)
    good = [c for c in r["comps"] if not c.get("aborted") and not c.get("error")]
    if arm == "C1":
        if len(good) != 1:
            why.append("G3：成功压缩 %d 次（要正好 1 次）" % len(good))
        else:
            m3, m4 = msg_by_k(r, 3), msg_by_k(r, 4)
            a, b = (m3["reqs"][-1] if m3 and m3["reqs"] else None), first_req(m4)
            if not a or not b or not (b["T"] < 0.5 * a["T"]):
                why.append("G3：压缩后第 4 句第 1 个请求 T=%s 不小于压缩前最后一个请求 T 的一半（%s）" % (b and b["T"], a and a["T"]))
    elif r["comps"]:
        why.append("G3：不该压缩的臂里有压缩事件")
    return why


# ───────────────────────────── 主程序 ─────────────────────────────
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    ignore_hdr = "--ignore-header" in sys.argv
    if len(args) != 1:
        sys.exit(__doc__)
    hdr, lprices, runs, dups, spent, opts = load(args[0])
    if not hdr:
        sys.exit("日志里没找到第一行（pi 版本 / 模型 / 设计指纹）。")
    ver, prov, mid, fp = hdr
    print("日志头：pi %s  模型 %s / %s  设计指纹 %s" % (ver, prov, mid, fp))
    bad_hdr = []
    if fp != FP:
        bad_hdr.append("设计指纹不是登记的 %s" % FP)
    if ver != PIV:
        bad_hdr.append("pi 版本不是登记的 %s" % PIV)
    if not mid.endswith(MODEL):
        bad_hdr.append("模型不是登记的 %s" % MODEL)
    if opts:
        bad_hdr.append("跑的时候用了会改设计的选项（%s）" % opts[0].replace("！用了会改设计的选项：", "").split("（")[0])
    if bad_hdr and not ignore_hdr:
        print("！" + "；".join(bad_hdr) + "：这不是登记的设计，不打分，先问用户。")
        return
    if bad_hdr:
        print("！（--ignore-header：只给测试用）" + "；".join(bad_hdr))
    price = {k: (lprices or REG_PRICE).get(k) for k in ("input", "cacheRead", "output")} if lprices else None
    if lprices and any(abs(lprices[k] - REG_PRICE[k]) > 1e-12 for k in REG_PRICE):
        print("！日志里的单价和登记的不同（登记 %s）：A2、A3 的阈值按日志里的单价算。" % REG_PRICE)
    if dups:
        print("！日志里有 %d 行重复的 (臂, 序号)：只取第一次出现的。" % dups)
    if not price:
        print("！日志里没有单价：所有花费相关的预测（A2–A5、K4、Z2）不评。")
    pcr = (price or REG_PRICE)["cacheRead"]
    pin = (price or REG_PRICE)["input"]

    arms = list(NEED)
    rec = {a: [r for r in runs if r.get("arm") == a and "err" not in r] for a in arms}
    failed = [r for r in runs if "err" in r]
    inval = {}
    ok = {}
    for a in arms:
        ok[a] = []
        for r in rec[a]:
            w = invalid_reasons(r)
            if w:
                inval[(a, r["rep"])] = w
            else:
                ok[a].append(r)
    enough = {a: len(ok[a]) >= NEED[a] for a in arms}

    print("\n== 闸门")
    total = len(runs)
    g1 = len(failed) <= 3
    print("G1 记录下来的运行 %d 次，重跑 2 次后仍失败 %d 次（≤3 才算过）：%s" % (total, len(failed), "过" if g1 else "没过"))
    for r in failed:
        print("   失败：%s #%s  %s" % (r["arm"], r["rep"], str(r.get("err"))[:150]))
    for a in arms:
        print("   %-2s 有效运行 %d / 记录 %d（至少 %d）：%s" % (a, len(ok[a]), len(rec[a]), NEED[a], "可评" if enough[a] else "不评"))
    for (a, rep), w in sorted(inval.items()):
        print("   无效：%s #%s  %s" % (a, rep, "；".join(w)))
    print("G2/G3/G7（每次运行的核对）：无效 %d 次" % len(inval))

    # G4 底稳定、G5 一句一个请求、m1 的 T
    g4, g5 = {}, {}
    for a in ("R", "D", "C0", "C1"):
        if not ok[a]:
            continue
        msgs = [(m) for r in ok[a] for m in (r["msgs"][1:6] if a in ("R", "D") else r["msgs"][1:3])]
        fr = [first_req(m) for m in msgs if first_req(m)]
        normal = sum(1 for q in fr if cls(q["U"], q["T"]) == "正常")
        g4[a] = (normal, len(fr))
        t1 = [first_req(r["msgs"][0])["T"] for r in ok[a] if first_req(r["msgs"][0])]
        m_t = med(t1)
        dev = [x for x in t1 if m_t and abs(x - m_t) > 0.05 * m_t]
        print("G4 %-2s 后续句第 1 个请求“正常” %d/%d（≥70%% 才算过）：%s；m1 第 1 个请求 T 中位数 %s，偏离 >5%% 的 %d 个（只提示）" % (
            a, normal, len(fr), "过" if len(fr) and normal >= 0.7 * len(fr) else "没过", m_t, len(dev)))
        if a in ("R", "D"):
            clean = sum(1 for m in msgs if len(m["reqs"]) == 1 and not m["calls"])
            g5[a] = (clean, len(msgs))
            print("G5 %-2s 第 2–6 句“干净”（1 个请求、没有工具调用）%d/%d（≥70%% 才算过）：%s" % (a, clean, len(msgs), "过" if len(msgs) and clean >= 0.7 * len(msgs) else "没过"))
    g4ok = {a: bool(g4.get(a) and g4[a][1] and g4[a][0] >= 0.7 * g4[a][1]) for a in arms}
    g5ok = {a: bool(g5.get(a) and g5[a][1] and g5[a][0] >= 0.7 * g5[a][1]) for a in ("R", "D")}
    money_ok = enough["R"] and enough["D"] and g4ok["R"] and g4ok["D"] and g5ok["R"] and g5ok["D"]

    # G6 子代理的账
    g6_bad = []
    for r in ok["D"]:
        dres = [x for m in r["msgs"] for x in m["results"] if x["tool"] == "delegate" and not x.get("blocked")]   # 被拦下的调用没有开子进程
        side = r.get("side", [])
        if len(dres) != len(side):
            g6_bad.append("D #%s：delegate 工具结果 %d 个，旁路日志 %d 条" % (r["rep"], len(dres), len(side)))
            continue
        for x, s in zip(dres, side):
            sc = sum((q.get("cost") or 0) for q in (s.get("reqs") or []))
            if x.get("nested") is None or abs(x["nested"] - sc) > 1e-6:
                g6_bad.append("D #%s：工具结果的 usage 花费 %s，旁路日志里子进程各请求之和 %.8f" % (r["rep"], x.get("nested"), sc))
                break
    g6 = enough["D"] and not g6_bad
    print("G6 D 的子代理账对得上（差 ≤ $0.000001）：%s" % ("过" if g6 else ("没过：" + "；".join(g6_bad[:3]) if g6_bad else "D 有效运行不够，不评")))

    # ── 数字
    F, cR, cD, s_, P, kobs = None, None, None, None, None, None
    cumR, cumD = {}, {}
    fh = []
    for a in ("R", "C0", "C1"):
        for r in ok[a]:
            m1 = r["msgs"][0]
            if len(m1["reqs"]) >= 2:
                fh.append(m1["reqs"][1]["T"] - m1["reqs"][0]["T"] - m1["reqs"][0]["out"])
    F = med(fh)

    def clean_costs(a):
        return [msg_cost(m) for r in ok[a] for m in r["msgs"][1:6] if len(m["reqs"]) == 1 and not m["calls"]]
    cr_list, cd_list = clean_costs("R"), clean_costs("D")
    if price and cr_list and cd_list and None not in cr_list + cd_list:
        cR, cD = med(cr_list), med(cd_list)
        s_ = cR - cD
    m1R = [msg_cost(r["msgs"][0]) for r in ok["R"]]
    m1D = [msg_cost(r["msgs"][0]) for r in ok["D"]]
    if price and m1R and m1D and None not in m1R + m1D:
        P = med(m1D) - med(m1R)

    def cum(a, k):
        vs = []
        for r in ok[a]:
            xs = [msg_cost(m) for m in r["msgs"][:k + 1]]
            if None in xs or len(xs) < k + 1:
                return None
            vs.append(sum(xs))
        return med(vs)
    if price and ok["R"] and ok["D"]:
        for k in range(6):
            cumR[k], cumD[k] = cum("R", k), cum("D", k)
        kobs = next((k for k in range(6) if cumR[k] is not None and cumD[k] is not None and cumD[k] <= cumR[k]), None)
    tR = [first_req(m)["T"] for r in ok["R"] for m in r["msgs"][1:6] if first_req(m)]
    tD = [first_req(m)["T"] for r in ok["D"] for m in r["msgs"][1:6] if first_req(m)]
    dT = (med(tR) - med(tD)) if tR and tD else None
    print("\n== 数字（只用有效运行）")
    print("F̂（文件占上下文的 token 数，R、C0、C1 的 m1 里第 2 个请求 T − 第 1 个请求 T − 第 1 个请求输出）= %s（%d 次运行）" % (F, len(fh)))
    print("第 2–6 句第 1 个请求的 T 中位数：R %s，D %s，R−D = %s（0.85 × F̂ = %s）" % (med(tR), med(tD), dT, None if F is None else round(0.85 * F, 1)))
    print("c_R = %s，c_D = %s，s = c_R − c_D = %s（干净后续句 R %d 句、D %d 句；F̂ × $%g/百万 = %s）" % (
        fmt_d(cR), fmt_d(cD), fmt_d(s_), len(cr_list), len(cd_list), pcr, fmt_d(None if F is None else F * pcr / 1e6)))
    print("P = D 的 m1 花费中位数 − R 的 = %s（R %s，D %s；0.5 × F̂ × $%g/百万 = %s）" % (
        fmt_d(P), fmt_d(med(m1R) if m1R and None not in m1R else None), fmt_d(med(m1D) if m1D and None not in m1D else None), pin,
        fmt_d(None if F is None else 0.5 * F * pin / 1e6)))
    if P is not None and s_ is not None and s_ > 0:
        print("P ÷ s = %.2f 句（回本所需句数，纯算术）" % (P / s_))
    print("cum(k)（k = 0…5，各臂中位数）：")
    for k in sorted(cumR):
        print("   k=%d  R %s  D %s  D−R %s" % (k, fmt_d(cumR[k]), fmt_d(cumD[k]), fmt_d(None if cumR[k] is None or cumD[k] is None else cumD[k] - cumR[k])))
    print("交叉点 k_obs = %s" % kobs)
    tot_run = {a: [run_total(r) for r in ok[a]] for a in arms}
    for a in arms:
        xs = [x for x in tot_run[a] if x is not None]
        print("每次运行总花费中位数 %-2s %s（%d 次）" % (a, fmt_d(med(xs)), len(xs)))
    share = []
    for r in ok["D"]:
        t = run_total(r)
        if t:
            share.append(nested_total(r) / t)
    print("D 每次运行里子代理花费的占比：中位数 %s（%d 次）" % (None if not share else "%.3f" % med(share), len(share)))
    comp_costs = [c["cost"] for r in ok["C1"] for c in r["comps"] if not c.get("aborted") and not c.get("error") and c.get("cost") is not None]
    print("C1 压缩那次摘要请求的花费：中位数 %s（%d 次）" % (fmt_d(med(comp_costs)), len(comp_costs)))

    # ── 格子
    cells = {}
    after = {}
    mention = {}
    for a in arms:
        for r in ok[a]:
            m = msg_by_k(r, r["recall_k"])
            cells[(a, r["rep"])] = parse_recall(m["final"] if m else "", r["truth"])
            m2 = msg_by_k(r, r["recall_k"] + 1)
            after[(a, r["rep"])] = parse_recall(m2["final"] if m2 else "", r["truth"])
            mention[(a, r["rep"])] = mentions(r["msgs"][0]["final"], r["truth"])

    def cellset(arm_list, needles=NEEDLES, table=None):
        table = table or cells
        return [(a, r["rep"], k, table[(a, r["rep"])][k]) for a in arm_list for r in ok[a] for k in needles]

    bg = cellset(["R", "C0"])
    bg_n, bg_x = len(bg), sum(1 for x in bg if x[3] == "对")

    # ── 预测
    print("\n== 预测")
    score = []

    def put(name, hit, note):
        conf = CONF[name]
        if hit is None:
            print("%-3s (%d%%) 不评：%s" % (name, conf, note))
            return
        score.append((name, conf, bool(hit)))
        print("%-3s (%d%%) %s  %s" % (name, conf, "对" if hit else "错", note))

    # A
    if not money_ok or F is None:
        why = "R 或 D 有效运行不够，或 G4/G5 没过（只印数字）"
        for n in ("A1", "A2", "A3", "A4"):
            put(n, None, why)
    else:
        put("A1", dT is not None and dT >= 0.85 * F, "T 中位数 R−D = %s，0.85 × F̂ = %.0f" % (dT, 0.85 * F))
        if s_ is None:
            put("A2", None, "没有价格，算不出花费")
        else:
            lo, hi = 0.6 * F * pcr / 1e6, 1.4 * F * pcr / 1e6
            put("A2", lo <= s_ <= hi, "s = %s，区间 [%s, %s]" % (fmt_d(s_), fmt_d(lo), fmt_d(hi)))
        if P is None:
            put("A3", None, "没有价格，算不出花费")
        else:
            lim = 0.5 * F * pin / 1e6
            put("A3", P > 0 and P <= lim, "P = %s，上限 %s" % (fmt_d(P), fmt_d(lim)))
        if kobs is None and not cumR:
            put("A4", None, "没有价格，算不出花费")
        else:
            put("A4", kobs is not None, "k_obs = %s" % kobs)
    if not (enough["D"] and g6 and price and share):
        put("A5", None, "D 有效运行不够、G6 没过或没有价格")
    else:
        put("A5", med(share) >= 0.5, "子代理花费占比中位数 %.3f（%d 次）" % (med(share), len(share)))

    # B
    b1 = None
    if enough["R"] and enough["C0"]:
        b1 = bg_x / bg_n
        put("B1", bg_x * 48 >= 41 * bg_n, "R、C0 合并 %d 格答对 %d（登记 48 格里 ≥41；格数不是 48 时按比例 ≥41/48）" % (bg_n, bg_x))
    else:
        put("B1", None, "R 或 C0 有效运行不够")
    dc = cellset(["D"])
    c1c = cellset(["C1"])
    dn, dx = len(dc), sum(1 for x in dc if x[3] == "对")
    if enough["D"] and b1 is not None:
        pD = dx / dn
        put("B2", pD <= 0.6 and (b1 - pD) >= 0.25, "D %d 格答对 %d（%.0f%%），B1 的比例 %.0f%%，差 %.0f 个百分点" % (dn, dx, 100 * pD, 100 * b1, 100 * (b1 - pD)))
    else:
        put("B2", None, "D 或 R、C0 有效运行不够")
    if enough["D"] and enough["C1"]:
        cx_d = sum(1 for x in dc if x[2] == "C" and x[3] == "对")
        cx_c = sum(1 for x in c1c if x[2] == "C" and x[3] == "对")
        put("B3", cx_d <= 1 and cx_c <= 1, "针 C 答对：D %d/%d，C1 %d/%d" % (cx_d, len(ok["D"]), cx_c, len(ok["C1"])))
        nc = [x for x in dc + c1c if x[3] in ("不知道", "错")]
        if len(nc) >= 8:
            unk = sum(1 for x in nc if x[3] == "不知道")
            put("B4", unk >= 0.6 * len(nc), "没答对的格子 %d（不知道 %d、错 %d；格式不对的不进分母）：“不知道”占 %.0f%%" % (
                len(nc), unk, len(nc) - unk, 100 * unk / len(nc)))
        else:
            put("B4", None, "没答对的格子只有 %d（不足 8 格）" % len(nc))
    else:
        put("B3", None, "D 或 C1 有效运行不够")
        put("B4", None, "D 或 C1 有效运行不够")
    if enough["D"]:
        hasu = [r for r in ok["D"] if any(v == "不知道" for v in cells[("D", r["rep"])].values())]
        if len(hasu) >= 3:
            redel = [r for r in hasu if any(c["tool"] == "delegate" for c in (msg_by_k(r, r["recall_k"] + 1) or {"calls": []})["calls"])]
            put("B5", len(redel) >= 0.75 * len(hasu), "回忆题出现“不知道”的 D 运行 %d 次，补救句里再调了 delegate 的 %d 次" % (len(hasu), len(redel)))
        else:
            put("B5", None, "回忆题出现“不知道”的 D 运行只有 %d 次（不足 3 次）" % len(hasu))
        da = cellset(["D"], table=after)
        ax = sum(1 for x in da if x[3] == "对")
        put("B6", ax >= 0.75 * len(da), "补救句之后 D 的 %d 格答对 %d（%.0f%%）" % (len(da), ax, 100 * ax / len(da)))
    else:
        put("B5", None, "D 有效运行不够")
        put("B6", None, "D 有效运行不够")

    # K
    if enough["C1"]:
        sel_no = [x for x in c1c if x[2] in "BCD" and not mention[("C1", x[1])][x[2]]]
        sel_yes = [x for x in c1c if mention[("C1", x[1])][x[2]]]
        if len(sel_no) >= 10:
            h = sum(1 for x in sel_no if x[3] == "对")
            put("K1", h <= 0.1 * len(sel_no), "B、C、D 且 m1 总结里没提到的 %d 格，答对 %d（%.0f%%）" % (len(sel_no), h, 100 * h / len(sel_no)))
        else:
            put("K1", None, "这样的格子只有 %d（不足 10 格）" % len(sel_no))
        if len(sel_yes) >= 8:
            h = sum(1 for x in sel_yes if x[3] == "对")
            put("K2", h >= 0.5 * len(sel_yes), "m1 总结里提到了的 %d 格（四根针都算），答对 %d（%.0f%%）" % (len(sel_yes), h, 100 * h / len(sel_yes)))
        else:
            put("K2", None, "这样的格子只有 %d（不足 8 格）" % len(sel_yes))
        h = sum(1 for x in c1c if x[3] == "对")
        put("K3", h <= 0.5 * len(c1c), "C1 的 %d 格答对 %d（%.0f%%）" % (len(c1c), h, 100 * h / len(c1c)))
        if price and comp_costs:
            put("K4", 0.0004 <= med(comp_costs) <= 0.0015, "压缩摘要请求花费中位数 %s（区间 $0.0004–$0.0015）" % fmt_d(med(comp_costs)))
        else:
            put("K4", None, "没有价格或没有成功的压缩")
    else:
        for n in ("K1", "K2", "K3", "K4"):
            put(n, None, "C1 有效运行不足 6 次：K 系列只印数字、不读")

    # Z
    put("Z1", len(failed) == 0, "重跑 2 次后仍失败的运行 %d 个" % len(failed))
    comp_sum = sum((run_total(r) or 0) for r in runs if "err" not in r)
    tot = spent if spent is not None else comp_sum
    if price:
        put("Z2", 0.04 <= tot <= 0.15, "本次花费 $%.4f（%s；区间 $0.04–$0.15）%s" % (
            tot, "脚本最后那一行" if spent is not None else "按数据行重算", ("" if spent is None or abs(spent - comp_sum) <= 1e-4 else "；注意：数据行重算是 $%.4f，和那一行差 > $0.0001" % comp_sum)))
    else:
        put("Z2", None, "没有价格")
    mins = sum(r.get("secs", 0) for r in runs if "err" not in r) / 60.0
    put("Z3", 6 <= mins <= 20, "每次运行用时之和 %.1f 分钟（区间 6–20）" % mins)

    if score:
        n, k = len(score), sum(1 for s in score if s[2])
        exp = sum(s[1] for s in score) / 100.0
        print("\n对 %d / 18（无法评的不算对）；可评的 %d 条里对 %d 条；可评的这几条登记的把握加起来期望 %.1f（差 %+.1f）" % (k, n, k, exp, k - exp))

    # ── 读法用的数字
    print("\n== 读法用的数字")
    print("回忆题每针每臂答对 x/n（背景 = R、C0 合并 %d/%d）；Fisher 双侧；悬崖 = p ≤ 0.05 且比例相差 ≥ 25 个百分点" % (bg_x, bg_n))
    for k in NEEDLES:
        row = []
        for a in arms:
            n = len(ok[a])
            x = sum(1 for r in ok[a] if cells[(a, r["rep"])][k] == "对")
            t = "%s %d/%d" % (a, x, n)
            if a in ("D", "C1") and n and bg_n:
                bx = sum(1 for z in bg if z[2] == k and z[3] == "对")
                bn = sum(1 for z in bg if z[2] == k)
                p = fisher(x, n - x, bx, bn - bx)
                cliff = p is not None and p <= 0.05 and abs(x / n - bx / bn) >= 0.25
                t += "（背景 %d/%d，p=%s%s）" % (bx, bn, "—" if p is None else "%.4f" % p, "，悬崖" if cliff else "")
            row.append(t)
        print("  针 %s：%s" % (k, "；".join(row)))
    for a in ("D", "C1"):
        n = len(dc) if a == "D" else len(c1c)
        cs = dc if a == "D" else c1c
        if n:
            x = sum(1 for z in cs if z[3] == "对")
            p = fisher(x, n - x, bg_x, bg_n - bg_x) if bg_n else None
            print("  %s 全部 %d 格答对 %d（%.0f%%）；背景 %.0f%%；Fisher p=%s" % (a, n, x, 100 * x / n, 100 * bg_x / bg_n if bg_n else 0, "—" if p is None else "%.4f" % p))
    for a in arms:
        cs = cellset([a])
        if cs:
            print("  %s 的格子：对 %d、不知道 %d、错 %d、格式不对 %d" % (a, *(sum(1 for z in cs if z[3] == lab) for lab in ("对", "不知道", "错", "格式不对"))))
    blocked = {a: sum(1 for r in ok[a] if r.get("blocked")) for a in arms}
    print("回忆题里模型试着调了工具（被拦下）的运行：%s" % "，".join("%s %d/%d" % (a, blocked[a], len(ok[a])) for a in arms))
    if ok["D"]:
        mm = {k: sum(1 for r in ok["D"] if mention[("D", r["rep"])][k]) for k in NEEDLES}
        ch = {k: 0 for k in NEEDLES}
        for r in ok["D"]:
            dres = [x for x in r["msgs"][0]["results"] if x["tool"] == "delegate"]
            t = mentions((dres[0].get("text") if dres else "") or "", r["truth"])
            for k in NEEDLES:
                ch[k] += 1 if t[k] else 0
        print("D 的 m1：主会话总结里提到 %s；子代理回答里提到（只看 delegate 结果前 1,500 字符）%s" % (
            " ".join("%s %d/%d" % (k, mm[k], len(ok["D"])) for k in NEEDLES), " ".join("%s %d/%d" % (k, ch[k], len(ok["D"])) for k in NEEDLES)))
        for k in NEEDLES:
            ya = [cells[("D", r["rep"])][k] for r in ok["D"] if mention[("D", r["rep"])][k]]
            na = [cells[("D", r["rep"])][k] for r in ok["D"] if not mention[("D", r["rep"])][k]]
            print("   针 %s：总结里提到的 %d 格答对 %d；没提到的 %d 格答对 %d" % (k, len(ya), ya.count("对"), len(na), na.count("对")))
    if ok["C1"]:
        for k in NEEDLES:
            ya = [cells[("C1", r["rep"])][k] for r in ok["C1"] if mention[("C1", r["rep"])][k]]
            na = [cells[("C1", r["rep"])][k] for r in ok["C1"] if not mention[("C1", r["rep"])][k]]
            print("   C1 针 %s：m1 总结里提到的 %d 格答对 %d；没提到的 %d 格答对 %d" % (k, len(ya), ya.count("对"), len(na), na.count("对")))
        for r in ok["C1"]:
            c = [c for c in r["comps"] if not c.get("aborted") and not c.get("error")][0]
            print("   C1 #%s 压缩 %s→%s token，摘要请求 新输入 %d 缓存读 %d 输出 %d 花费 %s" % (
                r["rep"], c.get("before"), c.get("after"), c["in"] + c["cw"], c["cr"], c["out"], fmt_d(c.get("cost"))))

    print("\n== 读法分支（按路线图登记的规则机械地对出来；手册怎么写由人对着数字写）")
    gate_q1 = g1 and money_ok
    print("Q1 闸门（G1、G2、G4、G5 过）：%s" % ("过" if gate_q1 else "没过：只报数字，不读"))
    if gate_q1:
        res = {n: next((s[2] for s in score if s[0] == n), None) for n in ("A1", "A2", "A3", "A4", "A5")}
        if res["A1"] and res["A2"] and res["A4"]:
            print("   (a) A1、A2、A4 都成立")
        if res["A4"] is False and res["A2"]:
            print("   (b) A4 不成立而 A2 成立")
        if res["A2"] is False:
            print("   (c) A2 不成立")
        if res["A3"] is False:
            print("   (d) A3 不成立")
        if res["A5"] and g6:
            print("   (e) A5 成立且 G6 过")
    gate_q2 = g1 and enough["R"] and enough["C0"]
    print("Q2 闸门（G1、G2 过，对照 R、C0 有效运行够）：%s" % ("过" if gate_q2 else "没过：只报数字，不读"))
    if gate_q2:
        hit = {n: next((s[2] for s in score if s[0] == n), None) for n in ("B1", "B2", "B3", "B4", "B5", "B6")}
        print("   (a) B1 %s" % ("成立：回忆题可靠，读 D 和 C1 的数" if hit["B1"] else "不成立：回忆题本身不可靠，D、C1 的数只印不读"))
        if hit["B2"] and hit["B3"]:
            print("   (b) B2、B3 都成立")
        elif hit["B3"] is False:
            print("   (b) B3 不成立（针 C 有 ≥2 格答对）：先查被拦下的调用记录和总结里有没有这个数")
        print("   (c) B4 %s" % {True: "成立", False: "不成立", None: "不评"}[hit["B4"]])
        print("   (d) B5 %s；B6 %s" % ({True: "成立", False: "不成立", None: "不评"}[hit["B5"]], {True: "成立", False: "不成立", None: "不评"}[hit["B6"]]))
    gate_q3 = enough["C1"]
    print("Q3 闸门（C1 有效运行 ≥ 6、G3 过）：%s" % ("过" if gate_q3 else "没过：K 系列只印数字，不读"))
    if gate_q3:
        hit = {n: next((s[2] for s in score if s[0] == n), None) for n in ("K1", "K2", "K3", "K4")}
        print("   K1 %s；K2 %s；K3、K4 照实报数" % ({True: "成立", False: "不成立（有 ≥2 格答对就先看摘要文本和总结里有没有这个值，再看 pi 版本）", None: "不评"}[hit["K1"]], {True: "成立", False: "不成立", None: "不评"}[hit["K2"]]))
    print("Q4 不管读成哪一格，v1 都不动；要不要把 delegate 做成 v1 之外的可选扩展、要不要给压缩加自定义指令，留给用户。")


if __name__ == "__main__":
    main()
