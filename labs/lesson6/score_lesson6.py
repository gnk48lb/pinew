#!/usr/bin/env python3
"""score_lesson6.py —— 第 6 课的评分脚本（冻结：路线图 11-14 那一行写下的门、读法、A1–A9、B1–B9、C1–C7、D1–D2，在看到任何数据之前写成，之后一个字不改）。

用法：python3 score_lesson6.py ex6.log

读 preset_mcp_lab.py 的日志（头一行“pi … 模型 … 设计指纹 …”，末尾“== 数据”后面每次运行一行“数据 {json}”）。
日志里有不止一次运行就分别打分，不合并；设计指纹、pi 版本和登记的不一致、或没有数据标题行，就不打分。
"""
import json, math, re, statistics, sys

EXPECT_PI = "1.5.0"
EXPECT_MODEL = "MiniMax-M3.1-Flash"
EXPECT_DESIGN = "1837b8842f8c"          # 设计指纹（12 位）
CLIFF, ALPHA, FLOOR_GAP = 1800, 0.05, 2
MAX_FAILED = 8
MIN_RUNS = {"N0": 3, "E0": 3, "C12": 3, "C30": 3, "D12": 3, "D30": 3, "X3": 3, "X12": 3, "X30": 3, "H12": 3,
            "W0": 6, "PT": 6, "PL": 4, "PI": 4, "PA": 4, "PR": 4, "U0": 2, "UX": 4, "UD": 4, "UC": 4}
ARMS1 = ("N0", "E0", "C12", "C30", "D12", "D30", "X3", "X12", "X30", "H12")
ARMS2 = ("W0", "PT", "PL", "PI", "PA", "PR")
ARMS3 = ("U0", "UX", "UD", "UC")
CONF = {"A1": 85, "A2": 88, "A3": 60, "A4": 60, "A5": 55, "A6": 55, "A7": 55, "A8": 80, "A9": 55,
        "B1": 70, "B2": 88, "B3": 88, "B4": 92, "B5": 55, "B6": 90, "B7": 55, "B8": 75, "B9": 92,
        "C1": 97, "C2": 80, "C3": 60, "C4": 45, "C5": 70, "C6": 50, "C7": 45, "D1": 80, "D2": 65}
HEAD = re.compile(r"^pi (\S+)\s+模型 (\S+) / (\S+)\s+设计指纹 ([0-9a-f]{12})\s*$")


def say(*a):
    print(*a)


# ───────────────────────────── 和实验脚本里同一套规则 ─────────────────────────────
def floor_of(Ts):
    """“底”：和最小值差 ≤ 2 的那些运行（最低簇）的中位数；最低簇只有 1 次 → 不稳。-> (底, 簇大小, 稳不稳)"""
    if not Ts:
        return None, 0, False
    lo = min(Ts)
    cl = [t for t in Ts if t - lo <= FLOOR_GAP]
    return statistics.median(cl), len(cl), len(cl) >= 2


def fisher_p(x1, n1, x2, n2):
    """双侧 Fisher 精确检验（整数运算）：概率 ≤ 观察到的那张表的所有表的概率之和。"""
    K, N = x1 + x2, n1 + n2
    lo, hi = max(0, K - n2), min(K, n1)
    num = {x: math.comb(n1, x) * math.comb(n2, K - x) for x in range(lo, hi + 1)}
    return sum(v for v in num.values() if v <= num[x1]) / math.comb(N, K)


def cell_class(x, n, bc, bn):
    if n == 0 or bn == 0:
        return "—", None
    p = fisher_p(x, n, bc, bn)
    if x >= (3 * n + 3) // 4 and p < ALPHA:
        return "断崖", p
    if x <= n // 4:
        return "无断崖", p
    return "待定", p


def med(v):
    v = [x for x in v if x is not None]
    return statistics.median(v) if v else None


# ───────────────────────────── 读日志 ─────────────────────────────
def split_log(text):
    """-> [(头信息 (pi 版本, 模型 provider, 模型 id, 设计指纹) 或 None, 这一段的行列表)]。头一行之前的内容丢掉。"""
    segs, cur, head = [], None, None
    for ln in text.splitlines():
        m = HEAD.match(ln.strip())
        if m:
            if cur is not None:
                segs.append((head, cur))
            head, cur = m.groups(), []
        elif cur is not None:
            cur.append(ln)
    if cur is not None:
        segs.append((head, cur))
    return segs


def parse_segment(lines):
    """-> (有没有数据标题行, 运行记录列表, 本次花费或 None)"""
    has_head, recs, spend = False, [], None
    for ln in lines:
        s = ln.strip()
        if s.startswith("== 数据"):
            has_head = True
        elif s.startswith("数据 {"):
            try:
                r = json.loads(s[3:].strip())
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("kind") == "run":
                recs.append(r)
        else:
            m = re.match(r"^本次花费 \$([0-9.]+)\s*$", s)
            if m:
                spend = float(m.group(1))
    return has_head, recs, spend


# ───────────────────────────── 分析 ─────────────────────────────
class Seg:
    def __init__(self, recs, spend):
        self.recs, self.spend = recs, spend
        self.runs = [r for r in recs if "err" not in r]
        self.failed = [r for r in recs if "err" in r]
        self.parts = sorted({r["part"] for r in recs if r.get("part") in (1, 2, 3)})

    def good(self, arm):
        return [r for r in self.runs if r["arm"] == arm]

    def attempted(self, arm):
        return [r for r in self.recs if r["arm"] == arm]

    def enough(self, arm):
        return len(self.good(arm)) >= MIN_RUNS[arm]

    def unreadable(self, arm):
        """G2：某个臂核对失败的运行超过一半 → 这个臂不评。"""
        a = self.attempted(arm)
        chk = [r for r in a if r.get("check_failed")]
        return bool(a) and 2 * len(chk) > len(a)

    def usable(self, arm):
        return self.enough(arm) and not self.unreadable(arm)

    def T1(self, arm):
        return [r["sents"][0]["T"] for r in self.good(arm)]

    def floor(self, arm):
        return floor_of(self.T1(arm))

    def stable_floor(self, arm):
        if not self.usable(arm):
            return None
        b, k, ok = self.floor(arm)
        return b if ok else None

    def background(self):
        w = self.good("W0")
        us = [r["sents"][i]["U"] for r in w for i in (1, 2)]
        return sum(1 for u in us if u >= CLIFF), len(us)

    def cell(self, arm, si):
        rs = self.good(arm)
        us = [r["sents"][si - 1]["U"] for r in rs]
        x = sum(1 for u in us if u >= CLIFF)
        bc, bn = self.background()
        cls, p = cell_class(x, len(us), bc, bn)
        return x, len(us), cls, p, us


def gates(sg):
    """-> dict：G1…G6 的状态和说明。"""
    g = {}
    n_failed = len(sg.failed)
    short = [a for a in MIN_RUNS if sg.attempted(a) and not sg.enough(a)]
    g["G1"] = (n_failed <= MAX_FAILED, "失败（重跑后仍失败）%d 次（≤ %d）；有效运行不够的臂：%s" % (n_failed, MAX_FAILED, ",".join(short) or "无"))
    chk_arms = [a for a in MIN_RUNS if sg.attempted(a) and sg.unreadable(a)]
    g["G2"] = (not chk_arms, "核对失败超过一半的臂：%s" % (",".join(chk_arms) or "无"))
    if 1 in sg.parts:
        fl = {a: sg.floor(a) for a in ARMS1 if sg.good(a)}
        unstable = [a for a in fl if not fl[a][2]]
        wide = [a for a in fl if sg.T1(a) and max(sg.T1(a)) - min(sg.T1(a)) > 2]
        e0 = "E0" in fl and fl["E0"][2] and sg.usable("E0")
        g["G3"] = (e0, "底不稳的臂：%s%s；4 次 T 最大减最小 > 2 的臂：%s" % (",".join(unstable) or "无", "（E0 不稳或没有数据 → 第一部分整个不读）" if not e0 else "", ",".join(wide) or "无"))
    if 2 in sg.parts:
        t1 = med(sg.T1("W0"))
        g["G4"] = (t1 is not None and t1 >= 3500, "W0 第 1 句 T 的中位数 %s（≥ 3,500）" % ("—" if t1 is None else "%g" % t1))
        bc, bn = sg.background()
        g["G5"] = (bn > 0 and bc <= 7, "W0 背景 %d/%d（≤ 7 / 16）" % (bc, bn))
    if 3 in sg.parts:
        u0 = sg.good("U0")
        ok = sum(1 for r in u0 if r.get("correct"))
        g["G6"] = (bool(u0) and ok == 0, "U0 答对 %d/%d（应为 0）" % (ok, len(u0)))
    return g


# ───────────────────────────── 预测 ─────────────────────────────
def predictions(sg, g):
    """-> {编号: (结果 True/False/None, 说明)}，None = 无法评。"""
    out = {}
    g1 = g["G1"][0]
    p1_ok = g1 and g.get("G3", (False,))[0] and 1 in sg.parts
    p2_ok = g1 and g.get("G4", (False,))[0] and g.get("G5", (False,))[0] and 2 in sg.parts
    p3_ok = g1 and g.get("G6", (False,))[0] and 3 in sg.parts

    def need(arms, ok, extra=""):
        if not ok:
            return "没过有效性门（G1 或这一部分的门）"
        bad = [a for a in arms if not sg.usable(a)]
        if bad:
            return "有效运行不足或核对失败超过一半的臂：" + ",".join(bad)
        return None

    # ── 第一部分
    def base(a):
        return sg.stable_floor(a)

    def D(a):
        b, e = base(a), base("E0")
        return None if (b is None or e is None) else b - e

    def a_range(key, arm, lo, hi):
        why = need((arm, "E0"), p1_ok)
        if not why and (base(arm) is None or base("E0") is None):
            why = "底不稳（最低簇只有 1 次）：%s" % ",".join(a for a in (arm, "E0") if base(a) is None)
        if why:
            out[key] = (None, why)
            return
        d = D(arm)
        out[key] = (lo <= d <= hi, "D(%s) = %g，范围 %g–%g" % (arm, d, lo, hi))

    why = need(("N0", "E0"), p1_ok)
    if not why and (base("N0") is None or base("E0") is None):
        why = "底不稳：%s" % ",".join(a for a in ("N0", "E0") if base(a) is None)
    out["A1"] = (None, why) if why else (abs(base("E0") - base("N0")) <= 2, "E0 底 %g，N0 底 %g，差 %+g" % (base("E0"), base("N0"), base("E0") - base("N0")))
    why = need(("H12", "E0"), p1_ok)
    if not why and (base("H12") is None or base("E0") is None):
        why = "底不稳：%s" % ",".join(a for a in ("H12", "E0") if base(a) is None)
    out["A2"] = (None, why) if why else (abs(D("H12")) <= 2, "D(H12) = %+g" % D("H12"))
    a_range("A3", "C12", 450, 700)
    a_range("A4", "D12", 180, 290)
    a_range("A5", "X3", 250, 380)
    a_range("A6", "X12", 1050, 1550)
    a_range("A7", "X30", 2700, 3900)
    arms = ("C12", "C30", "D12", "D30")
    why = need(arms, p1_ok)
    if not why and any(base(a) is None for a in arms):
        why = "底不稳：%s" % ",".join(a for a in arms if base(a) is None)
    if why:
        out["A8"] = (None, why)
    else:
        dc, dd = base("C30") - base("C12"), base("D30") - base("D12")
        out["A8"] = (abs(dc) <= 3 and abs(dd) <= 3, "C30 − C12 = %+g，D30 − D12 = %+g（都要 ≤ 3）" % (dc, dd))
    arms = ("C12", "X30", "X3", "E0")
    why = need(arms, p1_ok)
    if not why and any(base(a) is None for a in arms):
        why = "底不稳：%s" % ",".join(a for a in arms if base(a) is None)
    if why:
        out["A9"] = (None, why)
    else:
        slope = (D("X30") - D("X3")) / 27.0
        if slope <= 0:
            out["A9"] = (False, "每个工具 %.2f token ≤ 0，盈亏点不存在" % slope)
        else:
            n_star = math.floor(D("C12") / slope * 10 + 0.5) / 10          # 四舍五入到一位小数
            out["A9"] = (4 <= n_star <= 8, "D(C12) = %g，每个工具 %.1f token，n* = %.1f（范围 4–8）" % (D("C12"), slope, n_star))

    # ── 第二部分
    bc, bn = sg.background()
    why = need(("W0",), p2_ok)
    out["B1"] = (None, why) if why else (bc <= 3, "W0 背景 %d/%d（≤ 3 记对）" % (bc, bn))

    def b_cell(key, arm, si, want):
        why = need((arm, "W0"), p2_ok)
        if why:
            out[key] = (None, why)
            return
        x, n, cls, p, us = sg.cell(arm, si)
        out[key] = (cls == want, "%s 第 %d 句 %d/%d，档“%s”（p=%.4f），背景 %d/%d；要的是“%s”" % (arm, si, x, n, cls, p, bc, bn, want))
    b_cell("B2", "PL", 2, "断崖")
    b_cell("B3", "PI", 2, "断崖")
    b_cell("B4", "PA", 2, "断崖")
    b_cell("B5", "PT", 2, "无断崖")
    b_cell("B6", "PR", 2, "断崖")
    b_cell("B7", "PR", 3, "无断崖")
    arms = ("PT", "PL", "PI", "PA", "W0")
    why = need(arms, p2_ok)
    if why:
        out["B8"] = (None, why)
    else:
        px = pn = 0
        for a in ("PT", "PL", "PI", "PA"):
            x, n, _, _, _ = sg.cell(a, 3)
            px, pn = px + x, pn + n
        p = fisher_p(px, pn, bc, bn)
        out["B8"] = (p >= ALPHA, "第 3 句合起来 %d/%d 对背景 %d/%d，Fisher p = %.4f（≥ 0.05 记对）" % (px, pn, bc, bn, p))
    why = need(ARMS2, p2_ok)
    if why:
        out["B9"] = (None, why)
    else:
        bad = [(r["arm"], r["rep"], s["i"], s["requests"]) for a in ARMS2 for r in sg.good(a) for s in r["sents"] if s["requests"] != 1]
        out["B9"] = (not bad, "请求数不是 1 的句子 %d 个%s" % (len(bad), "" if not bad else "，例如 " + ", ".join("%s#%d 第%d句 %d 个请求" % b for b in bad[:3])))

    # ── 第三部分
    why = need(("U0",), g1 and 3 in sg.parts)           # C1 单独评：U0 有答对的运行时，C1 就是错的（G6 不过时只影响 C2–C7）
    if why:
        out["C1"] = (None, why)
    else:
        ok = sum(1 for r in sg.good("U0") if r.get("correct"))
        out["C1"] = (ok == 0, "U0 答对 %d/%d" % (ok, len(sg.good("U0"))))

    def c_ok(key, arm):
        why = need((arm,), p3_ok)
        if why:
            out[key] = (None, why)
            return
        rs = sg.good(arm)
        k = sum(1 for r in rs if r.get("correct"))
        out[key] = (k >= 4, "%s 答对 %d/%d（≥ 4 记对）" % (arm, k, len(rs)))
    c_ok("C2", "UX")
    c_ok("C3", "UD")
    c_ok("C4", "UC")

    def reqs(arm):
        return [r["requests"] for r in sg.good(arm) if not r.get("timeout")]

    def costs(arm):
        return [r["cost"] for r in sg.good(arm) if not r.get("timeout") and r.get("cost") is not None]
    why = need(("UD", "UX"), p3_ok)
    if not why and (not reqs("UD") or not reqs("UX")):
        why = "没有可比的请求数（都超时了）"
    out["C5"] = (None, why) if why else (med(reqs("UD")) > med(reqs("UX")), "UD 请求数中位数 %g，UX %g（UD 严格大于记对）" % (med(reqs("UD")), med(reqs("UX"))))
    why = need(("UX", "UD", "UC"), p3_ok)
    if not why and any(not costs(a) for a in ("UX", "UD", "UC")):
        why = "有的臂没有花费数据"
    if why:
        out["C6"] = (None, why)
    else:
        cx, cd, cc = med(costs("UX")), med(costs("UD")), med(costs("UC"))
        out["C6"] = (cx < cd and cx < cc, "每次运行花费的中位数：UX $%.6f，UD $%.6f，UC $%.6f（UX 严格最小记对）" % (cx, cd, cc))
    why = need(("UC",), p3_ok)
    if why:
        out["C7"] = (None, why)
    else:
        k = sum(1 for r in sg.good("UC") if r.get("read_codemode_md"))
        out["C7"] = (k >= 1, "UC 里读了名字带 codemode.md 的文件的运行 %d/%d 次（≥ 1 记对）" % (k, len(sg.good("UC"))))

    # ── 运行本身
    out["D1"] = (len(sg.failed) == 0, "重跑后仍失败的运行 %d 次" % len(sg.failed))
    if sg.parts == [1, 2, 3] and sg.spend is not None:
        out["D2"] = (0.06 <= sg.spend <= 0.20, "本次花费 $%.4f（$0.06–0.20 记对）" % sg.spend)
    elif sg.parts != [1, 2, 3]:
        out["D2"] = (None, "这一段只含第 %s 部分，D2 是三部分合计" % "、".join(str(p) for p in sg.parts))
    else:
        out["D2"] = (None, "没找到“本次花费”那一行")
    return out


# ───────────────────────────── 打印数字（读法用） ─────────────────────────────
def print_numbers(sg, g):
    if 1 in sg.parts:
        say("\n  第一部分（底 / D）：")
        e0 = sg.floor("E0")[0] if sg.good("E0") else None
        for a in ARMS1:
            if not sg.good(a):
                continue
            b, k, ok = sg.floor(a)
            say("    %-4s T=%s  底 %g（簇 %d%s）  D=%s" % (a, "/".join(str(t) for t in sg.T1(a)), b, k, "" if ok else "，不稳", "—" if (e0 is None or a == "E0") else "%+g" % (b - e0)))
    if 2 in sg.parts:
        bc, bn = sg.background()
        say("\n  第二部分（背景 %d/%d）：" % (bc, bn))
        for a in ARMS2:
            if not sg.good(a):
                continue
            c2, c3 = sg.cell(a, 2), sg.cell(a, 3)
            say("    %-3s 第 2 句 %d/%d %s%s；第 3 句 %d/%d %s%s" % (a, c2[0], c2[1], c2[2], "" if c2[3] is None else "（p=%.4f）" % c2[3],
                                                           c3[0], c3[1], c3[2], "" if c3[3] is None else "（p=%.4f）" % c3[3]))
    if 3 in sg.parts:
        say("\n  第三部分：")
        for a in ARMS3:
            rs = sg.good(a)
            if not rs:
                continue
            say("    %-3s 答对 %d/%d；请求数中位数 %s；ΣU 中位数 %s；花费中位数 %s" % (
                a, sum(1 for r in rs if r.get("correct")), len(rs), med([r["requests"] for r in rs if not r.get("timeout")]),
                med([r.get("sumU") for r in rs if not r.get("timeout")]), med([r.get("cost") for r in rs if not r.get("timeout")])))


def main():
    if len(sys.argv) != 2:
        sys.exit("用法：python3 score_lesson6.py ex6.log")
    text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
    segs = split_log(text)
    if not segs:
        sys.exit("日志里没有“pi … 模型 … 设计指纹 …”那一行：不打分，先问你。")
    n_scored = 0
    for i, (head, lines) in enumerate(segs, 1):
        ver, prov, mid, fp = head
        say("=" * 78)
        say("第 %d 段：pi %s，模型 %s / %s，设计指纹 %s" % (i, ver, prov, mid, fp))
        has_head, recs, spend = parse_segment(lines)
        if not has_head:
            say("  这一段没有“== 数据”标题行（练习 --mine-mcp / --demo-preset 的输出，或者被中断得太早）：不打分。")
            continue
        if ver != EXPECT_PI or not (mid == EXPECT_MODEL or mid.endswith("/" + EXPECT_MODEL)) or fp != EXPECT_DESIGN:
            say("  和登记的不一致（登记：pi %s，模型 %s，设计指纹 %s）：不打分，先问你。" % (EXPECT_PI, EXPECT_MODEL, EXPECT_DESIGN))
            continue
        sg = Seg(recs, spend)
        say("  运行记录 %d 条（有效 %d，失败 %d）；包含第 %s 部分；本次花费 %s" % (
            len(recs), len(sg.runs), len(sg.failed), "、".join(str(p) for p in sg.parts) or "—", "—" if spend is None else "$%.4f" % spend))
        g = gates(sg)
        say("\n  有效性门：")
        for k in sorted(g):
            say("    %s %s  %s" % (k, "过" if g[k][0] else "不过", g[k][1]))
        print_numbers(sg, g)
        res = predictions(sg, g)
        say("\n  预测（27 条）：")
        right = judged = 0
        exp = 0.0
        for key in sorted(CONF, key=lambda k: (k[0], int(k[1:]))):
            ok, why = res[key]
            if ok is None:
                mark = "无法评"
            else:
                judged += 1
                exp += CONF[key] / 100.0
                mark = "对" if ok else "错"
                right += 1 if ok else 0
            say("    %-3s %-5s（把握 %d%%）%s" % (key, mark, CONF[key], why))
        say("\n  对 %d / 27（无法评的不算对）；可评的 %d 条里对 %d 条；可评的那些预测的把握合计预期对 %.1f 条" % (right, judged, right, exp))
        n_scored += 1
    if not n_scored:
        say("\n没有可以打分的段。")


if __name__ == "__main__":
    main()
