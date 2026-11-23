# 事后（第 8 课）：值在子代理回答里出现过/没出现过 × 回忆题；C1 摘要里留下了什么；钱花在哪（平均数拆分）。只读 ex8.log。
import json, re, statistics as st, sys
recs=[json.loads(l[3:]) for l in open(sys.argv[1],encoding='utf-8') if l.startswith('数据 {')]
arm={a:sorted([r for r in recs if r['arm']==a],key=lambda r:r['rep']) for a in ('R','D','C0','C1')}
def mc(m): return sum(q['cost'] for q in m['reqs'])+sum((x['nested'] or 0) for x in m['results'])
FMT = {"A": r"(?<!\d)\d{4}(?!\d)", "B": r"(?<![0-9a-f])[0-9a-f]{6}(?![0-9a-f])", "C": r"(?<!\d)\d{1,3}(?!\d)", "D": r"(?<![0-9a-f])[0-9a-f]{7}(?![0-9a-f])"}
def recall(r,off=0):
    m=next(m for m in r['msgs'] if m['k']==r['recall_k']+off); lines=[x for x in m['final'].split("\n") if "A=" in x]
    if not lines: return {n:"格式不对" for n in "ABCD"}
    segs=dict(re.findall(r"([ABCD])\s*=\s*([^;]*)",lines[-1])); out={}
    for n in "ABCD":
        seg=segs.get(n,""); sl=seg.lower() if n in "BD" else seg; w=re.findall(FMT[n],sl)
        out[n]=("对" if w[0]==r['truth'][n] else "错") if w else ("不知道" if "unknown" in seg.lower() else "格式不对")
    return out
def has(txt,tr,n):
    if n=='C': return tr['tenant'] in txt and bool(re.search(r"(?:^|\D)%s(?:\D|$)"%re.escape(tr['C']),txt))
    return tr[n] in txt
print("== D 臂逐次：回传字符 | 回答里 g7 计数(全文，只在回答含标记行时才有) | 前 1,500 字符里有没有 ABCD | 主会话 m1 总结里提到 ABCD | 回忆题 ABCD")
cells=[]
for r in arm['D']:
    rs=[x for x in r['msgs'][0]['results'] if x['tool']=='delegate']; x=rs[0]; tr=r['truth']
    first=''.join(n if has(x['text'],tr,n) else '·' for n in 'ABCD')
    ment=''.join(n if has(r['msgs'][0]['final'],tr,n) else '·' for n in 'ABCD')
    rc=recall(r); rcs=''.join({'对':'√','不知道':'?','错':'×','格式不对':'!'}[rc[n]] for n in 'ABCD')
    print("  D#%d 回传 %6d | marker=%s g7=%s | 前1500: %s | 总结提到: %s | 回忆: %s"%(r['rep'],x['chars'],x['marker'],x.get('g7'),first,ment,rcs))
    for n in 'ABCD':
        cells.append((r['rep'],n,x['text'] and has(x['text'],tr,n) or bool(x.get('g7') and x['g7'][n]>0),has(r['msgs'][0]['final'],tr,n),rc[n]))
print()
print("== 事后表（登记过要加的）：D 臂里主会话 m1 总结没提到的 16 格（针 B、C 各 8 次），按“值在子代理回答里确知出现过 / 没看到”分，各答对几个")
nm=[c for c in cells if not c[3]]
for lab,flag in (("确知出现过（前 1,500 字符里有，或回答全文的 g7 计数 >0）",True),("没看到（前 1,500 字符里没有，也没有 g7）",False)):
    sel=[c for c in nm if c[2]==flag]
    print("  %s：%d 格，答对 %d"%(lab,len(sel),sum(1 for c in sel if c[4]=='对')),"  明细",[(c[0],c[1],c[4]) for c in sel])
print("  总计 %d 格，答对 %d"%(len(nm),sum(1 for c in nm if c[4]=='对')))
print("  针 B：确知出现 %d 格（答对 %d）；没看到 %d 格（答对 %d）"%(sum(1 for c in nm if c[1]=='B' and c[2]),sum(1 for c in nm if c[1]=='B' and c[2] and c[4]=='对'),sum(1 for c in nm if c[1]=='B' and not c[2]),sum(1 for c in nm if c[1]=='B' and not c[2] and c[4]=='对')))
print("  针 C：确知出现 %d 格（答对 %d）；没看到 %d 格（答对 %d）"%(sum(1 for c in nm if c[1]=='C' and c[2]),sum(1 for c in nm if c[1]=='C' and c[2] and c[4]=='对'),sum(1 for c in nm if c[1]=='C' and not c[2]),sum(1 for c in nm if c[1]=='C' and not c[2] and c[4]=='对')))
print()
print("== C1 压缩摘要里留下了什么（逐次：摘要字符数；摘要里有没有 A、B、C、D 的值）")
for r in arm['C1']:
    s=r['comps'][0]['summary']; tr=r['truth']
    print("  C1#%d 摘要 %4d 字符 | %s | 压缩 %s→%s token"%(r['rep'],len(s),''.join(n if has(s,tr,n) else '·' for n in 'ABCD'),r['comps'][0]['before'],r['comps'][0]['after']))
r=arm['C1'][3]; print("  —— C1#%d 摘要全文（前 1,800 字符）："%r['rep']); print(r['comps'][0]['summary'][:1800])
print()
print("== 钱花在哪：每次运行的平均数拆分（均值；登记的账用中位数，这里只为看“哪一块贡献了差额”）")
def avg(a,f): return st.mean([f(r) for r in arm[a]])
def g(r,ks): return sum(mc(m) for m in r['msgs'] if m['k'] in ks)
for lab,ks in (("m1 任务",(1,)),("m2–6 五句 OK",(2,3,4,5,6)),("m7 回忆题",(7,)),("m8 补救句",(8,))):
    a,b=avg('R',lambda r:g(r,ks)),avg('D',lambda r:g(r,ks)); print("  %-10s R $%.6f  D $%.6f  D−R $%+.6f"%(lab,a,b,b-a))
tR,tD=avg('R',lambda r:sum(mc(m) for m in r['msgs'])),avg('D',lambda r:sum(mc(m) for m in r['msgs']))
print("  %-10s R $%.6f  D $%.6f  D−R $%+.6f（D 是 R 的 %.2f 倍；平均数）"%("合计",tR,tD,tD-tR,tD/tR))
tD7=st.mean([sum(mc(m) for m in r['msgs']) for r in arm['D'] if r['rep']!=7])
print("  去掉 D#7 后 D 平均 $%.6f（R 的 %.2f 倍）"%(tD7,tD7/tR))
print("  D#7 的 m2–6 每句：",["%.6f"%mc(m) for m in arm['D'][6]['msgs'][1:6]]," D#2：",["%.6f"%mc(m) for m in arm['D'][1]['msgs'][1:6]])
print()
print("== 补救句（m8）的问题文本与 D 的回答：D#7、D#2 的回忆题答对情况与是否再调 delegate")
for rep in (2,7):
    r=arm['D'][rep-1]; m8=r['msgs'][7]
    print("  D#%d 回忆 %s；补救句调用 %s；补救后 %s"%(rep,recall(r),[c['tool'] for c in m8['calls']],recall(r,1)))
