# 事后分析（第 8 课，标注为事后；不改任何登记的判定）。只读 ex8.log 的数据行。
import json, statistics as st, sys, math
recs=[json.loads(l[3:]) for l in open(sys.argv[1],encoding='utf-8') if l.startswith('数据 {')]
arm={a:sorted([r for r in recs if r['arm']==a],key=lambda r:r['rep']) for a in ('R','D','C0','C1')}
def mc(m): return sum(q['cost'] for q in m['reqs'])+sum((x['nested'] or 0) for x in m['results'])
med=st.median
print("== ① 各类句子的花费中位数（每臂每次运行先取该类句子的合计，再取中位数）")
def grp(r):
    ms={m['k']:m for m in r['msgs']}
    if r['arm'] in ('R','D'):
        return {'m1':mc(ms[1]),'m2-6':sum(mc(ms[k]) for k in range(2,7)),'m7 回忆':mc(ms[7]),'m8 补救':mc(ms[8])}
    return {'m1':mc(ms[1]),'m2-3':sum(mc(ms[k]) for k in (2,3)),'压缩摘要':sum(c['cost'] for c in r['comps']),'m4 回忆':mc(ms[4]),'m5 补救':mc(ms[5])}
for a in ('R','D','C0','C1'):
    g=[grp(r) for r in arm[a]]
    keys=list(g[0].keys())
    tot=[sum(x.values()) for x in g]
    print(a, "  ".join("%s $%.6f"%(k,med([x[k] for x in g])) for k in keys), "  合计中位数 $%.6f"%med(tot))
print()
print("== ② D 臂：回传长度与后续句花费（逐次）；R 臂后续句花费作对照")
cR=[mc(m) for r in arm['R'] for m in r['msgs'][1:6]]
print("R 后续句：中位数 $%.6f 平均数 $%.6f 范围 $%.6f–$%.6f（40 句）"%(med(cR),st.mean(cR),min(cR),max(cR)))
rows=[]
for r in arm['D']:
    s1=[s for s in r['side'] if s['msg']==1][0]
    fol=[mc(m) for m in r['msgs'][1:6]]
    rows.append((s1['finalChars'],med(fol),r['rep'],r['msgs'][1]['reqs'][0]['T']))
for ch,c,rep,T in sorted(rows):
    print("  D#%d 回传 %6d 字符 → 主会话第 2 句请求 T=%6d → 后续句中位数 $%.6f（R 的 %.2f 倍）"%(rep,ch,T,c,c/med(cR)))
def rank(v):
    o=sorted(range(len(v)),key=lambda i:v[i]); rk=[0]*len(v)
    for i,j in enumerate(o): rk[j]=i+1
    return rk
x=[a for a,_,_,_ in rows]; y=[b for _,b,_,_ in rows]
rx,ry=rank(x),rank(y); n=len(x)
d2=sum((a-b)**2 for a,b in zip(rx,ry)); rho=1-6*d2/(n*(n*n-1))
print("  Spearman ρ（回传字符数 对 后续句花费，n=8）= %.2f"%rho)
print()
print("== ③ P 的拆分（中位数）：D 的 m1 = 主会话两个请求 + 子代理；R 的 m1 = 主会话两个请求（含读文件）")
mainD=[sum(q['cost'] for q in r['msgs'][0]['reqs']) for r in arm['D']]
subD=[sum((x['nested'] or 0) for x in r['msgs'][0]['results']) for r in arm['D']]
mainR=[mc(r['msgs'][0]) for r in arm['R']]
print("  D m1 主会话 $%.6f ＋ 子代理 $%.6f ＝ $%.6f ； R m1 $%.6f ； P=$%.6f"%(med(mainD),med(subD),med([a+b for a,b in zip(mainD,subD)]),med(mainR),med([mc(r['msgs'][0]) for r in arm['D']])-med(mainR)))
h1=[]; hout=[]; hre=[]
for r in arm['D']:
    s1=[s for s in r['side'] if s['msg']==1][0]
    q=s1['reqs']
    h1.append((q[0]['cost'],q[0]['cacheRead'],q[0]['input']+q[0]['cacheRead']))
    hout.append(sum(z['output'] for z in q)); hre.append(sum(z['cost'] for z in q))
print("  子代理第 1 个请求：花费中位数 $%.6f；缓存读/总输入 %s"%(med([a for a,_,_ in h1]),[(c,t) for _,c,t in h1]))
print("  子代理在 m1 里合计输出 token：%s（中位数 %s；按 $0.6/百万 约 $%.6f）"%(hout,med(hout),med(hout)*0.6/1e6))
print("  R m1 主会话输出 token（写总结）：%s"%[sum(q['out'] for q in r['msgs'][0]['reqs']) for r in arm['R']])
print("  D m1 主会话输出 token：%s"%[sum(q['out'] for q in r['msgs'][0]['reqs']) for r in arm['D']])
print()
print("== ④ D 臂的补救句（第 8 句）：是否再调 delegate、花费")
for r in arm['D']:
    m8=r['msgs'][7]; unk=[k for k,v in zip('ABCD','ABCD') if False]
    n=sum(1 for c in m8['calls'] if c['tool']=='delegate')
    print("  D#%d 再调 delegate %d 次，补救句花费 $%.6f"%(r['rep'],n,mc(m8)))
print("  R 补救句花费：",["%.6f"%mc(r['msgs'][7]) for r in arm['R']])
print("  C0 补救句(第5句)：",["%.6f"%mc(r['msgs'][4]) for r in arm['C0']],"  C1 补救句(第5句)：",["%.6f"%mc(r['msgs'][4]) for r in arm['C1']])
print("  C1 补救句是否调工具：",[[c['tool'] for c in r['msgs'][4]['calls']] for r in arm['C1']])
print()
print("== ⑤ 每臂花费合计")
for a in ('R','D','C0','C1'):
    t=[sum(mc(m) for m in r['msgs'])+sum(c['cost'] for c in r['comps']) for r in arm[a]]
    print("  %s 合计 $%.4f（%d 次）"%(a,sum(t),len(t)))
print("  全部 $%.4f"%sum(sum(mc(m) for m in r['msgs'])+sum(c['cost'] for c in r['comps']) for r in recs))
print("  D#7 单独 $%.4f；去掉 D#7 后 D 臂每次运行平均 $%.6f"%(sum(mc(m) for m in arm['D'][6]['msgs']),st.mean([sum(mc(m) for m in r['msgs']) for r in arm['D'] if r['rep']!=7])))
