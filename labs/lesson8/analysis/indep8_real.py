# 独立复算（第 8 课的真实日志）：不 import score_lesson8.py，只读数据行，按路线图登记的定义重新算一遍。
# 解析回忆题、提到、闸门的写法都和评分脚本不同（正则不同、循环结构不同），结果应当一致。
import json, math, re, statistics as st, sys
from fractions import Fraction

recs = [json.loads(l[3:]) for l in open(sys.argv[1], encoding="utf-8") if l.startswith("数据 {")]
ok = [r for r in recs if "err" not in r]
arm = {a: sorted([r for r in ok if r["arm"] == a], key=lambda r: r["rep"]) for a in ("R", "D", "C0", "C1")}
print("运行数", {a: len(v) for a, v in arm.items()}, "失败", len(recs) - len(ok))

def med(x):
    return st.median(x)

def mcost(m):
    return sum(q["cost"] for q in m["reqs"]) + sum((x["nested"] or 0) for x in m["results"])

# F̂
fh = []
for a in ("R", "C0", "C1"):
    for r in arm[a]:
        q = r["msgs"][0]["reqs"]
        fh.append(q[1]["T"] - q[0]["T"] - q[0]["out"])
F = med(fh)
print("F̂ =", F, "（%d 次）" % len(fh))

tR = [m["reqs"][0]["T"] for r in arm["R"] for m in r["msgs"][1:6]]
tD = [m["reqs"][0]["T"] for r in arm["D"] for m in r["msgs"][1:6]]
print("T 中位数 R=%s D=%s 差=%s  0.85F̂=%.1f" % (med(tR), med(tD), med(tR) - med(tD), 0.85 * F))

def clean(a):
    return [mcost(m) for r in arm[a] for m in r["msgs"][1:6] if len(m["reqs"]) == 1 and not m["calls"]]
cR, cD = clean("R"), clean("D")
mR, mD = med(cR), med(cD)
s = mR - mD
print("干净后续句 R %d D %d；c_R=%.8f c_D=%.8f s=%.8f  c_D/c_R=%.4f" % (len(cR), len(cD), mR, mD, s, mD / mR))
lo, hi = 0.6 * F * 0.015 / 1e6, 1.4 * F * 0.015 / 1e6
print("A2 区间 [%.8f, %.8f] -> %s" % (lo, hi, lo <= s <= hi))
P = med([mcost(r["msgs"][0]) for r in arm["D"]]) - med([mcost(r["msgs"][0]) for r in arm["R"]])
lim = 0.5 * F * 0.15 / 1e6
print("P=%.8f  A3 上限=%.8f -> %s ; P/limit=%.3f ; P/(F̂×0.15/1e6)=%.3f" % (P, lim, 0 < P <= lim, P / lim, P / (F * 0.15 / 1e6)))
print("P÷s = %.4f" % (P / s))
kobs = None
for k in range(6):
    cr = med([sum(mcost(m) for m in r["msgs"][:k + 1]) for r in arm["R"]])
    cd = med([sum(mcost(m) for m in r["msgs"][:k + 1]) for r in arm["D"]])
    print("  cum k=%d R=%.8f D=%.8f D-R=%.8f" % (k, cr, cd, cd - cr))
    if kobs is None and cd <= cr:
        kobs = k
print("k_obs =", kobs)
share = []
for r in arm["D"]:
    nest = sum((x["nested"] or 0) for m in r["msgs"] for x in m["results"])
    tot = sum(mcost(m) for m in r["msgs"])
    share.append(nest / tot)
print("子代理占比 中位数 %.4f 范围 %.3f–%.3f" % (med(share), min(share), max(share)))
for a in ("R", "D", "C0", "C1"):
    tot = [sum(mcost(m) for m in r["msgs"]) + sum(c["cost"] for c in r["comps"]) for r in arm[a]]
    print("每次运行总花费中位数 %s %.6f" % (a, med(tot)))
print("压缩摘要请求花费中位数 %.6f" % med([c["cost"] for r in arm["C1"] for c in r["comps"]]))
print("总花费 %.4f ；用时 %.1f 分钟" % (sum(sum(mcost(m) for m in r["msgs"]) + sum(c["cost"] for c in r["comps"]) for r in ok), sum(r["secs"] for r in ok) / 60))

# ── 回忆题（另一种写法：按“X=”切段，再在段里找第一个格式对的词）
FMT = {"A": r"(?<!\d)\d{4}(?!\d)", "B": r"(?<![0-9a-f])[0-9a-f]{6}(?![0-9a-f])", "C": r"(?<!\d)\d{1,3}(?!\d)", "D": r"(?<![0-9a-f])[0-9a-f]{7}(?![0-9a-f])"}  # 登记的规则：词前后不能紧挨着同类字符（第一版我写成了 \b，把 “79s” 漏掉了）
def recall(r, k_off=0):
    m = next(m for m in r["msgs"] if m["k"] == r["recall_k"] + k_off)
    txt = m["final"]
    lines = [x for x in txt.split("\n") if "A=" in x]
    out = {}
    if not lines:
        return {n: "格式不对" for n in "ABCD"}
    line = lines[-1]
    segs = dict(re.findall(r"([ABCD])\s*=\s*([^;]*)", line))
    for n in "ABCD":
        seg = segs.get(n, "")
        seg_l = seg.lower() if n in "BD" else seg
        w = re.findall(FMT[n], seg_l)
        if w:
            out[n] = "对" if w[0] == r["truth"][n] else "错"
        elif "unknown" in seg.lower():
            out[n] = "不知道"
        else:
            out[n] = "格式不对"
    return out

def mention(r):
    t = r["msgs"][0]["final"]
    tr = r["truth"]
    return {"A": tr["A"] in t, "B": tr["B"] in t, "D": tr["D"] in t,
            "C": (tr["tenant"] in t) and bool(re.search(r"(?:^|\D)%s(?:\D|$)" % re.escape(tr["C"]), t))}

cells = {a: [(r["rep"], recall(r), mention(r), recall(r, 1)) for r in arm[a]] for a in arm}
for a in arm:
    row = []
    for n in "ABCD":
        x = sum(1 for _, c, _, _ in cells[a] if c[n] == "对")
        row.append("%s %d/%d" % (n, x, len(cells[a])))
    labs = {}
    for _, c, _, _ in cells[a]:
        for n in "ABCD":
            labs[c[n]] = labs.get(c[n], 0) + 1
    print("回忆题", a, " ".join(row), labs)
bg = [c[n] for a in ("R", "C0") for _, c, _, _ in cells[a] for n in "ABCD"]
bgx = sum(1 for x in bg if x == "对")
print("背景 R+C0: %d/%d" % (bgx, len(bg)))

def fisher(a, b, c, d):
    n1, n2, k = a + b, c + d, a + c
    n = n1 + n2
    def pr(x):
        return Fraction(math.comb(n1, x) * math.comb(n2, k - x), math.comb(n, k))
    obs = pr(a)
    return float(sum(pr(x) for x in range(max(0, k - n2), min(k, n1) + 1) if pr(x) <= obs))

for a in ("D", "C1"):
    for n in "ABCD":
        x = sum(1 for _, c, _, _ in cells[a] if c[n] == "对")
        bx = sum(1 for aa in ("R", "C0") for _, c, _, _ in cells[aa] if c[n] == "对")
        bn = sum(1 for aa in ("R", "C0") for _ in cells[aa])
        print("  Fisher %s 针%s: %d/8 对 背景 %d/%d  p=%.6f" % (a, n, x, bx, bn, fisher(x, 8 - x, bx, bn - bx)))
    x = sum(1 for _, c, _, _ in cells[a] for n in "ABCD" if c[n] == "对")
    print("  Fisher %s 全部: %d/32 对 背景 %d/%d  p=%.8f" % (a, x, bgx, len(bg), fisher(x, 32 - x, bgx, len(bg) - bgx)))

# B2/B3/B4/B5/B6、K1/K2/K3
d_cells = [(rep, n, c[n]) for rep, c, _, _ in cells["D"] for n in "ABCD"]
dx = sum(1 for _, _, v in d_cells if v == "对")
print("B2: D %d/32=%.3f ; B1 比例 %.3f ; 差 %.3f" % (dx, dx / 32, bgx / len(bg), bgx / len(bg) - dx / 32))
print("B3: 针C D=%d C1=%d" % (sum(1 for _, c, _, _ in cells["D"] if c["C"] == "对"), sum(1 for _, c, _, _ in cells["C1"] if c["C"] == "对")))
c1_cells = [(rep, n, c[n]) for rep, c, _, _ in cells["C1"] for n in "ABCD"]
nc = [v for _, _, v in d_cells + c1_cells if v in ("不知道", "错")]
print("B4: 没答对 %d，其中不知道 %d" % (len(nc), sum(1 for v in nc if v == "不知道")))
hasu = [rep for rep, c, _, _ in cells["D"] if any(v == "不知道" for v in c.values())]
rd = {r["rep"]: r for r in arm["D"]}
redel = [rep for rep in hasu if any(c["tool"] == "delegate" for m in rd[rep]["msgs"] if m["k"] == rd[rep]["recall_k"] + 1 for c in m["calls"])]
print("B5: 有不知道的 D 运行", hasu, "补救句再调 delegate 的", redel)
ax = sum(1 for _, _, _, c2 in cells["D"] for n in "ABCD" if c2[n] == "对")
print("B6: 补救后 D %d/32" % ax)
sel_no = [(rep, n) for rep, c, mn, _ in cells["C1"] for n in "BCD" if not mn[n]]
print("K1: B/C/D 没提到 %d 格，答对 %d" % (len(sel_no), sum(1 for rep, n in sel_no if next(c for rr, c, _, _ in cells["C1"] if rr == rep)[n] == "对")))
sel_yes = [(rep, n) for rep, c, mn, _ in cells["C1"] for n in "ABCD" if mn[n]]
print("K2: 提到 %d 格，答对 %d" % (len(sel_yes), sum(1 for rep, n in sel_yes if next(c for rr, c, _, _ in cells["C1"] if rr == rep)[n] == "对")))
print("K3: C1 %d/32 对" % sum(1 for _, _, v in c1_cells if v == "对"))

# 猜测 (b)：D 臂 m1 总结没提到的格子
nm = [(rep, n, c[n]) for rep, c, mn, _ in cells["D"] for n in "ABCD" if not mn[n]]
print("猜测(b)：D 臂总结没提到的格子 %d，答对 %d（%.1f%%）" % (len(nm), sum(1 for _, _, v in nm if v == "对"), 100 * sum(1 for _, _, v in nm if v == "对") / max(1, len(nm))))
print("  逐针：", {n: (sum(1 for _, nn, _ in nm if nn == n), sum(1 for _, nn, v in nm if nn == n and v == "对")) for n in "ABCD"})
print("Z：失败 %d；G6 逐次差（工具结果 usage vs 旁路日志）：" % (len(recs) - len(ok)))
for r in arm["D"]:
    dres = [x for m in r["msgs"] for x in m["results"] if x["tool"] == "delegate" and not x["blocked"]]
    sd = [sum(q["cost"] for q in sdd["reqs"]) for sdd in r["side"]]
    print("  D#%d" % r["rep"], ["%.8f" % x["nested"] for x in dres], ["%.8f" % x for x in sd])
