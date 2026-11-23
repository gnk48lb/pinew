# 事后（第 8 课）：对账用户的两个数字 + 把累计账多算两步（k=6、7）。只读 ex8.log 的数据行。
import json, statistics as st, sys
recs=[json.loads(l[3:]) for l in open(sys.argv[1],encoding='utf-8') if l.startswith('数据 {')]
arm={a:sorted([r for r in recs if r['arm']==a],key=lambda r:r['rep']) for a in ('R','D','C0','C1')}
def mc(m): return sum(q['cost'] for q in m['reqs'])+sum((x['nested'] or 0) for x in m['results'])
med=st.median
print("== 对账：c_R 的各种算法（R 后续句 = 第 2–6 句，每次运行 5 句）")
cleanR=[mc(m) for r in arm['R'] for m in r['msgs'][1:6] if len(m['reqs'])==1 and not m['calls']]
cleanD=[mc(m) for r in arm['D'] for m in r['msgs'][1:6] if len(m['reqs'])==1 and not m['calls']]
pr=[med([mc(m) for m in r['msgs'][1:6]]) for r in arm['R']]
prD=[med([mc(m) for m in r['msgs'][1:6]]) for r in arm['D']]
pm=[st.mean([mc(m) for m in r['msgs'][1:6]]) for r in arm['R']]
print("  登记的算法：40 句合在一起取中位数 c_R=%.8f"%med(cleanR))
print("  每次运行先取中位数，再对 8 个取中位数 = %.8f ；再取平均 = %.8f"%(med(pr),st.mean(pr)))
print("  每次运行先取平均，再对 8 个取中位数 = %.8f"%med(pm))
print("  40 句的平均 = %.8f"%st.mean(cleanR))
print("  R 每次运行的后续句中位数（8 个）：",["%.6f"%x for x in sorted(pr)])
print("  D 每次运行的后续句中位数（8 个）：",["%.6f"%x for x in sorted(prD)])
print("== c_D 的各种算法")
print("  40 句合在一起取中位数 c_D=%.8f ；每次运行中位数再取中位数 %.8f ；40 句平均 %.8f"%(med(cleanD),med(prD),st.mean(cleanD)))
P=0.00127343
for lab,cr,cd in (("登记的算法",med(cleanR),med(cleanD)),("每次运行先取中位数",med(pr),med(prD)),("用户的 c_R=0.000257 配登记的 c_D",0.000257,med(cleanD))):
    s=cr-cd
    print("  %s：c_R=%.8f c_D=%.8f c_D/c_R=%.3f s=%.8f P÷s=%.3f"%(lab,cr,cd,cd/cr,s,P/s))
print("  F̂×$0.015/百万 = %.8f"%(13474.5*0.015/1e6))
print()
print("== 累计账多算两步（登记的窗口是 k≤5；k=6 把第 7 句“回忆题”也算进来，k=7 再把第 8 句“补救句”算进来）。各臂对每次运行累计后取中位数")
for k in range(8):
    cr=med([sum(mc(m) for m in r['msgs'][:k+1]) for r in arm['R']])
    cd=med([sum(mc(m) for m in r['msgs'][:k+1]) for r in arm['D']])
    print("  k=%d R $%.6f D $%.6f D−R $%+.6f（%+.1f%%）"%(k,cr,cd,cd-cr,100*(cd-cr)/cr))
print("  （按每次运行配对：k=6 时 D−R 的 8×8 个组合里 D≤R 的比例，仅看一眼）")
cr6=[sum(mc(m) for m in r['msgs'][:7]) for r in arm['R']]; cd6=[sum(mc(m) for m in r['msgs'][:7]) for r in arm['D']]
n=sum(1 for a in cd6 for b in cr6 if a<=b); print("   %d/64"%n, " D 最小 %.6f 最大 %.6f ；R 最小 %.6f 最大 %.6f"%(min(cd6),max(cd6),min(cr6),max(cr6)))
print()
print("== D#2 / D#5 / D#7 的 delegate 任务文本（主会话自己写的）和回传长度")
for rep in (2,5,7,3):
    r=arm['D'][rep-1]; assert r['rep']==rep
    for c in r['msgs'][0]['calls']:
        if c['tool']=='delegate':
            a=c['args']; t=a.get('task') if isinstance(a,dict) else a
            print("  D#%d 任务(%d 字符)：%s"%(rep,len(t or ''),(t or '').replace('\n',' ⏎ ')[:900]))
    print("     回传 %d 字符"%[s for s in r['side'] if s['msg']==1][0]['finalChars'])
print()
print("== 数据行字段：comps 里有什么")
print("  C1#1 comps keys:", list(arm['C1'][0]['comps'][0].keys()) if arm['C1'][0]['comps'] else None)
r=arm['D'][1]
print("  D#2 m1 results[0] keys:", list(r['msgs'][0]['results'][0].keys()))
print("  D#2 m1 results[0].g7:", r['msgs'][0]['results'][0].get('g7'), " chars:", r['msgs'][0]['results'][0].get('chars'))
