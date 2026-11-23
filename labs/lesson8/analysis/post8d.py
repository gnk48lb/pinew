import json, re, sys
recs=[json.loads(l[3:]) for l in open(sys.argv[1],encoding='utf-8') if l.startswith('数据 {')]
FMT = {"A": r"(?<!\d)\d{4}(?!\d)", "B": r"(?<![0-9a-f])[0-9a-f]{6}(?![0-9a-f])", "C": r"(?<!\d)\d{1,3}(?!\d)", "D": r"(?<![0-9a-f])[0-9a-f]{7}(?![0-9a-f])"}
def recall(r,off=0):
    m=next(m for m in r['msgs'] if m['k']==r['recall_k']+off); lines=[x for x in m['final'].split("\n") if "A=" in x]
    if not lines: return {n:"格式不对" for n in "ABCD"}
    segs=dict(re.findall(r"([ABCD])\s*=\s*([^;]*)",lines[-1])); out={}
    for n in "ABCD":
        seg=segs.get(n,""); sl=seg.lower() if n in "BD" else seg; w=re.findall(FMT[n],sl)
        out[n]=("对" if w[0]==r['truth'][n] else "错") if w else ("不知道" if "unknown" in seg.lower() else "格式不对")
    return out
for a in ('R','D','C0','C1'):
    rs=[r for r in recs if r['arm']==a]
    x1=sum(1 for r in rs for n in 'ABCD' if recall(r)[n]=='对'); x2=sum(1 for r in rs for n in 'ABCD' if recall(r,1)[n]=='对')
    print(a,"回忆题答对 %d/%d；补救句之后答对 %d/%d"%(x1,4*len(rs),x2,4*len(rs)))
# C1 recover: how many used a tool; D recover: re-delegations
rc=[r for r in recs if r['arm']=='C1']
print("C1 补救句调用 read 的运行：",sum(1 for r in rc if any(c['tool']=='read' for c in r['msgs'][4]['calls'])),"/",len(rc))
print("D 补救句调用 delegate 的运行：",sum(1 for r in recs if r['arm']=='D' and any(c['tool']=='delegate' for c in r['msgs'][7]['calls'])),"/8")
# helper output tokens vs reply chars
for r in sorted([r for r in recs if r['arm']=='D'],key=lambda r:r['rep']):
    s1=[s for s in r['side'] if s['msg']==1][0]
    print("D#%d helper 请求数 %d 输出 token %d 回传 %d 字符 子代理 m1 花费 $%.6f"%(r['rep'],len(s1['reqs']),sum(q['output'] for q in s1['reqs']),s1['finalChars'],sum(q['cost'] for q in s1['reqs'])))
