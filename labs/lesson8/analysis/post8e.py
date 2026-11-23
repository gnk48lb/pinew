# 事后核对（第 8 课）：后续句花费 与 “请求总 token × 缓存读价”的差（零头）。只读 ex8.log 的数据行。
import json, statistics as st, sys
recs=[json.loads(l[3:]) for l in open(sys.argv[1],encoding='utf-8') if l.startswith('数据 {')]
arm={a:sorted([r for r in recs if r['arm']==a],key=lambda r:r['rep']) for a in ('R','D','C0','C1')}
def mc(m): return sum(q['cost'] for q in m['reqs'])+sum((x['nested'] or 0) for x in m['results'])
print(list(arm['R'][0]['msgs'][1]['reqs'][0].keys()))
for a in ('R','D'):
    res=[];outs=[];news=[];costs=[]
    for r in arm[a]:
        for m in r['msgs'][1:6]:
            q=m['reqs'][0]
            T=q['T']; c=mc(m)
            res.append(c-T*0.015/1e6); outs.append(sum(z['out'] for z in m['reqs'])); news.append(q['in']); costs.append(c)
    print(a,"40 句：花费中位数 $%.6f；(花费 − T×$0.015/百万) 中位数 $%.6f（范围 $%.6f–$%.6f）；输出 token 中位数 %s；新输入 token 中位数 %s"%(st.median(costs),st.median(res),min(res),max(res),st.median(outs),st.median(news)))
# D per-run residual
for r in arm['D']:
    res=[mc(m)-m['reqs'][0]['T']*0.015/1e6 for m in r['msgs'][1:6]]
    print("  D#%d 零头中位数 $%.6f"%(r['rep'],st.median(res)))
