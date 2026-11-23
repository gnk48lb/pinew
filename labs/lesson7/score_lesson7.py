#!/usr/bin/env python3
"""score_lesson7.py —— 第 7 课的评分脚本（冻结：数据到之前就写好，之后不改）。
用法：python3 score_lesson7.py ex7.log
按路线图 11-17 第 7 课那一行登记的规则：先过 G1–G4，再逐条打 A1–A7、B1–B2、C1–C2，最后印读法 Q1–Q3 对应的数字。"""
import json, re, sys, math

FP, PIV = "8387f1a371c4", "1.5.0"
MIN_T, HI, LO = 1500, 0.7, 0.3
NEED = {"K0": 4, "K1": 6, "R0": 4, "F0": 4}


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


def frac_ok(hits, n, num=7, den=8):
    """登记的“≥7/8”按比例：命中率 ≥ 7/8。n=0 不判。"""
    return n > 0 and hits * den >= num * n


def load(path):
    txt = open(path, encoding="utf-8-sig", errors="replace").read()
    hdr = None
    for ln in txt.splitlines():
        m = re.match(r"^pi (\S+)\s+模型 (\S+) / (\S+)\s+设计指纹 ([0-9a-f]{12})\s*$", ln)
        if m:
            hdr = m.groups()
            break
    runs, seen = [], set()
    for ln in txt.splitlines():
        if ln.startswith("数据 {"):
            try:
                r = json.loads(ln[3:])
            except ValueError:
                continue
            key = (r.get("arm"), r.get("rep"))
            if key in seen:
                continue
            seen.add(key)
            runs.append(r)
    cost = None
    for m in re.finditer(r"本次花费 \$([0-9.]+)", txt):
        cost = float(m.group(1))
    return hdr, runs, cost


def run_cost(r):
    xs = [x["cost"] for x in r["r1"] + r.get("r2", [])] + [c["cost"] for c in r["c1"] + r.get("c2", [])]
    return None if (not xs or None in xs) else sum(xs)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    hdr, runs, spent = load(sys.argv[1])
    if not hdr:
        sys.exit("日志里没找到第一行（pi 版本 / 模型 / 设计指纹）。")
    ver, prov, mid, fp = hdr
    print("日志头：pi %s  模型 %s / %s  设计指纹 %s" % (ver, prov, mid, fp))
    if fp != FP:
        print("！设计指纹不是登记的 %s：这不是登记的设计，只能当探索性数据，不打分。" % FP)
        return
    if ver != PIV:
        print("！pi 版本不是 %s：照打，但标注“版本不同”。" % PIV)
    if not mid.endswith("MiniMax-M3.1-Flash"):
        print("！模型不是 MiniMax-M3.1-Flash：照打，但标注“模型不同”。")
    ok = {a: [r for r in runs if r["arm"] == a and "err" not in r] for a in NEED}
    bad = [r for r in runs if "err" in r]
    nproc = sum(len(ok[a]) * (2 if a in ("R0", "F0") else 1) for a in ok) + sum(
        (2 if r["arm"] in ("R0", "F0") else 1) for r in bad)
    print("\n== 闸门")
    g1 = len(bad) <= 3
    print("G1 失败的运行 %d 个（≤3 才算过）：%s" % (len(bad), "过" if g1 else "没过"))
    valid = {a: len(ok[a]) >= NEED[a] for a in NEED}
    for a in NEED:
        print("   %s 有效运行 %d（至少 %d）：%s" % (a, len(ok[a]), NEED[a], "可打分" if valid[a] else "不打分"))
    k1c = sum(1 for r in ok["K1"] if any(not c["aborted"] and not c["error"] for c in r["c1"]))
    g2 = len(ok["K1"]) > 0 and k1c >= 6
    print("G2 K1 里成功压缩的运行 %d/%d（≥6）：%s" % (k1c, len(ok["K1"]), "过" if g2 else "没过：只印第 A 部分的数字"))
    bgn = sum(1 for r in ok["K0"] for x in r["r1"][1:] if cls(x["U"], x["T"]) == "整段重付")
    g3 = bgn <= 6
    print("G3 K0 背景里“整段重付” %d 个（≤6）：%s" % (bgn, "过" if g3 else "没过：恢复/分出去只印数字不打分"))
    t1 = [r["r1"][0]["T"] for a in ok for r in ok[a] if r["r1"]]
    m = med(t1)
    dev = [x for x in t1 if m and abs(x - m) > 0.05 * m]
    print("G4 第 1 句 T 中位数 %s，偏离 >5%% 的运行 %d 个（只提示）" % (m, len(dev)))

    print("\n== 预测")
    score = []

    def put(name, conf, hit, note, gated=False):
        if hit is None:
            print("%-3s (%d%%) 不打分：%s" % (name, conf, note))
            return
        score.append((name, conf, hit))
        print("%-3s (%d%%) %s  %s" % (name, conf, "对" if hit else "错", note))

    k1 = ok["K1"] if valid["K1"] else []
    partA = g1 and valid["K1"] and g2
    fc = []
    for r in k1:
        cs = [c for c in r["c1"] if not c["aborted"] and not c["error"]]
        if cs:
            fc.append((r, cs[0]))
    if not partA:
        for n in ("A1", "A2", "A3", "A4", "A5"):
            put(n, 0, None, "K1 闸门没过或有效运行不够")
    else:
        h = sum(1 for r in k1 if 1 <= len([c for c in r["c1"] if not c["aborted"] and not c["error"]]) <= 2)
        put("A1", 80, h == len(k1), "每次运行 1–2 次成功压缩：%d/%d" % (h, len(k1)))
        h = sum(1 for r, c in fc if (c["in"] + c["cr"] + c["cw"]) and c["cr"] / (c["in"] + c["cr"] + c["cw"]) <= 0.2)
        put("A2", 65, frac_ok(h, len(fc)), "摘要请求缓存读占比 ≤0.2：%d/%d" % (h, len(fc)))
        h = sum(1 for r, c in fc if c["before"] and c["after"] is not None and c["after"] <= 0.7 * c["before"])
        put("A3", 70, frac_ok(h, len(fc)), "压缩后估算 ≤0.7×压缩前：%d/%d" % (h, len(fc)))
        n4 = h4 = n5 = h5 = 0
        for r, c in fc:
            p = c["after_req"]
            rq = r["r1"]
            if p < len(rq):
                n4 += 1
                h4 += cls(rq[p]["U"], rq[p]["T"]) == "整段重付"
            if p + 1 < len(rq):
                n5 += 1
                h5 += cls(rq[p + 1]["U"], rq[p + 1]["T"]) == "正常"
        put("A4", 80, frac_ok(h4, n4), "压缩后第 1 个请求整段重付：%d/%d" % (h4, n4))
        put("A5", 75, frac_ok(h5, n5), "压缩后第 2 个请求正常：%d/%d" % (h5, n5))
    if valid["K1"] and valid["K0"] and g1:
        c1 = [run_cost(r) for r in ok["K1"] if run_cost(r) is not None]
        c0 = [run_cost(r) for r in ok["K0"] if run_cost(r) is not None]
        if c1 and c0:
            put("A6", 75, med(c1) > med(c0), "每次运行总花费中位数 K1 $%.6f，K0 $%.6f" % (med(c1), med(c0)))
        else:
            put("A6", 75, None, "没有价格，算不出花费")
    else:
        put("A6", 75, None, "K0/K1 有效运行不够或 G1 没过")
    if valid["K0"] and g3 and g1:
        put("A7", 70, bgn <= 3, "K0 第 2–7 句 %d 个请求里整段重付 %d 个" % (sum(len(r["r1"]) - 1 for r in ok["K0"]), bgn))
    else:
        put("A7", 70, None, "K0 不够或闸门没过")
    for name, a, conf in (("B1", "R0", 75), ("B2", "F0", 70)):
        if valid[a] and g3 and g1:
            cl = [cls(r["r2"][0]["U"], r["r2"][0]["T"]) for r in ok[a]]
            n = len(cl)
            put(name, conf, cl.count("正常") * 5 >= 4 * n, "%s 进程 2 第 1 个请求：正常 %d、待定 %d、整段重付 %d（共 %d；登记是 ≥4/5，按比例）" % (
                a, cl.count("正常"), cl.count("待定"), cl.count("整段重付"), n))
        else:
            put(name, conf, None, "%s 不够或闸门没过" % a)
    put("C1", 80, len(bad) == 0, "失败的运行 %d 个" % len(bad))
    tot = spent if spent is not None else sum(run_cost(r) or 0 for r in runs if "err" not in r)
    put("C2", 60, 0.02 <= tot <= 0.12, "总花费 $%.4f（登记区间 $0.02–0.12）" % tot)

    if score:
        n = len(score)
        k = sum(1 for s in score if s[2])
        exp = sum(s[1] for s in score) / 100.0
        print("\n对 %d/%d；登记的置信度加起来期望 %.1f（差 %+.1f）" % (k, n, exp, k - exp))
    print("\n== 读法用的数字")
    for r in ok["K1"]:
        for c in r["c1"][:1]:
            print("K1 #%d 压缩 %s→%s 摘要请求 新输入 %d 缓存读 %d 输出 %d" % (r["rep"], c["before"], c["after"], c["in"] + c["cw"], c["cr"], c["out"]))
    for a in ("R0", "F0"):
        print("%s 进程 2 第 1 个请求 U/T：%s" % (a, "  ".join("%d/%d" % (r["r2"][0]["U"], r["r2"][0]["T"]) for r in ok[a])))
    print("（读法 Q1–Q3 的分类按路线图那一行手工对照这些数字写。）")


if __name__ == "__main__":
    main()
