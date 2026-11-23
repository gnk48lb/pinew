#!/usr/bin/env python3
"""ext_lab.py —— “扩展实验室”：扩展的各种写法，让每个请求多几个 token，哪几种会让缓存失效。

用法：
  python3 ext_lab.py                  两部分都做（约 8–12 分钟，预计 $0.02–0.06）
  python3 ext_lab.py --only cost      只做第一部分（9 个臂 × 4 次，每次 1 句话，约 3 分钟）
  python3 ext_lab.py --only position  只做第二部分（5 个臂 × 5 次，每次连问 3 句，约 6–9 分钟）
  python3 ext_lab.py --mine tally.ts  量你自己写的扩展：和不装扩展比，每个请求多几个 token（4 次运行，不到 $0.001）
  python3 ext_lab.py --dry-run        不调模型：核对包里的文件，打印设计指纹和要跑的运行

一、cost（第一部分）：同一句话（“Reply with exactly the single word: OK”）、同样的工具（只开 read），9 种扩展写法各跑 4 次。
   每次读 pi 自己报的用量，取这句话的第 1 个请求的总输入 token。总输入是确定性的（同一个请求发两次，总输入一样），
   所以“某个臂比不装扩展多几个 token”是精确的差，不是估计。
     S0 不装扩展（对照）          S1 观察型（订阅 13 种事件，什么都不返回）   S2 你的 v1 三个扩展一起装
     S3 注册一个工具 today        S4 S3 加 promptSnippet 和 promptGuidelines  S5 sections 里加一段 team_rules
     S6 强制替换 systemPrompt     S7 追加一条用户消息                         S8 context 钩子：最后一条用户消息末尾加一块
二、position（第二部分）：每次运行是一个 pi 进程连问 3 句；第 1 句带一段约 16,000 字符的固定参考文本（前面有随机标签，
   所以不同次运行的第 1 句从头就不同）。量第 2、3 句“没命中缓存的 token”U = 总输入 − 命中。
     K0 不装扩展   K1 每句改一次系统提示末尾（sections）   K2 追加式（每句追加一条固定消息）
     K3 context 钩子：最后一条用户消息末尾加一块（上一条消息的末尾变了）
     K4 context 钩子：第一条用户消息最前面加 [ctx N]（历史的开头变了）
   同一个 pi 进程里连问，所以 K 系列的差别只来自“第一个变了的字节在哪”。

用你 ~/.pi/agent 里的网关、模型、密钥和价格，只给模型 read 工具（S3、S4 再加 today）。花费到 --max-cost（默认 $0.30）就自动停。
运行一个一个跑，不并行（第二部分量的就是缓存）。
"""
import argparse, hashlib, json, math, os, re, shutil, signal, statistics, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))

# ───────────────────────────── 设计（路线图 11-10 那一行写的就是这一版；改一个字，下面的“设计指纹”就变） ─────────────────────────────
MSG_OK = "Reply with exactly the single word: OK"
MSG_YES = "Reply with exactly the single word: YES"
MSG_DONE = "Reply with exactly the single word: DONE"
SAFE, CLIFF = 600, 1800          # 档：没命中的 token（U）的中位数 ≤ 600 记“安全”，≥ 1800 记“断崖”，其间记“部分”
ALPHA = 0.05                     # 两个臂“差别真实”的门槛：精确置换检验，双侧
N1, N2 = 4, 5                    # 每个臂跑几次：第一部分 4 次、第二部分 5 次
SEED1, SEED2, REF_SEED = 5101, 5102, 5103   # 运行顺序的种子（第一部分、第二部分）、参考文本的种子
REF_CHARS = 16000                # 参考文本的长度（字符，约数；写到整句为止）
RETRIES = 2                      # 基础设施失败（超时、网关报错、没拿到用量）最多重跑 2 次
WORDS = ("about above after again air all along also any back began being below big book both boy call came can carry city close come "
         "could cut day does done door down each east eat end even every eye face far feet few find first food form found four from front "
         "gave girl give going good great group grow hand hard have head hear help here high home house idea just keep kind know land "
         "large last late learn leave left life light like line list live long look made make many may mean men might mile miss more most "
         "move much must name near need never new next night now off often once only open order other our own page paper part place plant "
         "play point put quick quite read real river room round run same saw say sea see seem set she show side small some soon sound "
         "stand start state still stop story study such sun take talk tell than that the their them then there these they thing think "
         "this those three time too took tree try turn under until upon use very walk want watch water way well went were what when where "
         "which while white who why will with word work world would write year young and are but did for get had her him his how its let "
         "man not one out ran two was yes yet you").split()

V1_FILES = {   # 和 v1 配置包里的三个扩展逐字节相同的副本（脚本启动时核对 sha256）
    "budget-guard.ts": "ea8486cc3d610a91c66cacc1aced8a116efd94086a418d1c98498d835f2bd756",
    "preset.ts": "7942b7f933f97bcc29024a87f8526969413ba6f87b0bec537648a2926a0a4e29",
    "safety-gate.ts": "902abdc9d5ba32ffc3a6eed82278f261213834f6793b1c2390a1676d4bfeb902",
}
V1_PRESETS = ("v1-presets.json", "5a7ef03aa594e59d193084ef171a584d701615f559b166cbb0e709f924732d5a")   # 放进私有配置目录（只有 S2 的配置目录里有）

# 扩展的写法。每个臂装哪几个：见下面的 ARMS。EXT_LAB_MARK 是脚本给每次运行的标记文件路径（只有标 mark 的臂会写）。
MARK_HEAD = '''import { appendFileSync } from "node:fs";
const MARK = process.env.EXT_LAB_MARK || "";
const mark = (s: string) => { if (MARK) appendFileSync(MARK, s + "\\n"); };
'''
EXT_SRC = {
    "observer": '''import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
''' + MARK_HEAD + '''export default function (pi: ExtensionAPI) {
  for (const ev of ["session_start", "before_agent_start", "agent_start", "turn_start", "context", "before_provider_request", "message_end", "tool_call", "tool_result", "turn_end", "agent_end", "agent_settled", "session_shutdown"] as const) {
    pi.on(ev as any, async () => { mark(ev); });
  }
}
''',
    "tool": '''import { Type } from "typebox";
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
const today = defineTool({
  name: "today", label: "Today", description: "Return today's date as YYYY-MM-DD.",
  parameters: Type.Object({}),
  async execute() { return { content: [{ type: "text", text: new Date().toISOString().slice(0, 10) }], details: {} }; },
});
export default function (pi: ExtensionAPI) { pi.registerTool(today); }
''',
    "tool_snippet": '''import { Type } from "typebox";
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
const today = defineTool({
  name: "today", label: "Today", description: "Return today's date as YYYY-MM-DD.",
  promptSnippet: "Return today's date",
  promptGuidelines: ["Use today when the user asks for the current date."],
  parameters: Type.Object({}),
  async execute() { return { content: [{ type: "text", text: new Date().toISOString().slice(0, 10) }], details: {} }; },
});
export default function (pi: ExtensionAPI) { pi.registerTool(today); }
''',
    "sections_const": '''import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
export default function (pi: ExtensionAPI) {
  pi.on("before_agent_start", (event) => { event.systemPromptOptions.sections.team_rules = "Always answer in one word."; });
}
''',
    "force_const": '''import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
export default function (pi: ExtensionAPI) {
  pi.on("before_agent_start", (event) => ({ systemPrompt: event.systemPrompt + "\\n\\nAlways answer in one word." }));
}
''',
    "message": '''import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
''' + MARK_HEAD + '''export default function (pi: ExtensionAPI) {
  pi.on("before_agent_start", (event) => {
    if (event.messageType !== "user") return;
    mark("before_agent_start");
    return { message: { customType: "probe-note", content: "Note: always answer in one word.", display: true } };
  });
}
''',
    "ctx_tail": '''import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
''' + MARK_HEAD + '''export default function (pi: ExtensionAPI) {
  pi.on("context", (event) => {
    mark("context");
    const msgs = event.messages.map((m: any) => ({ ...m }));
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === "user") {
        const c = msgs[i].content;
        msgs[i].content = (typeof c === "string" ? [{ type: "text", text: c }] : c.slice()).concat([{ type: "text", text: "(reminder: one word)" }]);
        break;
      }
    }
    return { messages: msgs };
  });
}
''',
    "sections_vary": '''import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
''' + MARK_HEAD + '''export default function (pi: ExtensionAPI) {
  let n = 0;
  pi.on("before_agent_start", (event) => {
    n++;
    mark("before_agent_start " + n);
    event.systemPromptOptions.sections.team_rules = "Run number " + n + ". Always answer in one word.";
  });
}
''',
    "ctx_head": '''import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
''' + MARK_HEAD + '''export default function (pi: ExtensionAPI) {
  let n = 0;
  pi.on("context", (event) => {
    n++;
    mark("context " + n);
    const msgs = event.messages.map((m: any) => ({ ...m }));
    for (let i = 0; i < msgs.length; i++) {
      if (msgs[i].role === "user") {
        const c = msgs[i].content;
        msgs[i].content = [{ type: "text", text: "[ctx " + n + "]" }].concat(typeof c === "string" ? [{ type: "text", text: c }] : c.slice());
        break;
      }
    }
    return { messages: msgs };
  });
}
''',
}

# 臂：part 1 = cost，part 2 = position；tools = --tools 的值；exts = 装哪几个扩展（v1:… 是 v1 的三个原文件）；
# mark = (事件, 规则)：标记文件里该事件的行数 atleast1（至少 1 行）/ per_sentence（每句 1 行）/ per_request（每个请求 1 行）
ARMS = {
    "S0": dict(part=1, tools="read", exts=(), v1=False, mark=None, doc="不装扩展（对照）"),
    "S1": dict(part=1, tools="read", exts=("observer",), v1=False, mark=("before_agent_start", "atleast1"), doc="观察型：订阅 13 种事件，什么都不返回"),
    "S2": dict(part=1, tools="read", exts=("v1:budget-guard.ts", "v1:preset.ts", "v1:safety-gate.ts"), v1=True, mark=None, doc="你的 v1 三个扩展一起装"),
    "S3": dict(part=1, tools="read,today", exts=("tool",), v1=False, mark=None, doc="注册工具 today（名字、描述、空参数）"),
    "S4": dict(part=1, tools="read,today", exts=("tool_snippet",), v1=False, mark=None, doc="S3 加 promptSnippet 和 promptGuidelines"),
    "S5": dict(part=1, tools="read", exts=("sections_const",), v1=False, mark=None, doc="sections 加 team_rules"),
    "S6": dict(part=1, tools="read", exts=("force_const",), v1=False, mark=None, doc="强制替换 systemPrompt（后面加一句）"),
    "S7": dict(part=1, tools="read", exts=("message",), v1=False, mark=("before_agent_start", "per_sentence"), doc="追加一条用户消息"),
    "S8": dict(part=1, tools="read", exts=("ctx_tail",), v1=False, mark=("context", "per_request"), doc="context：最后一条用户消息末尾加一块"),
    "K0": dict(part=2, tools="read", exts=(), v1=False, mark=None, doc="不装扩展"),
    "K1": dict(part=2, tools="read", exts=("sections_vary",), v1=False, mark=("before_agent_start", "per_sentence"), doc="每句改一次系统提示末尾（sections）"),
    "K2": dict(part=2, tools="read", exts=("message",), v1=False, mark=("before_agent_start", "per_sentence"), doc="追加式：每句追加一条固定消息"),
    "K3": dict(part=2, tools="read", exts=("ctx_tail",), v1=False, mark=("context", "per_request"), doc="context：最后一条用户消息末尾加一块"),
    "K4": dict(part=2, tools="read", exts=("ctx_head",), v1=False, mark=("context", "per_request"), doc="context：第一条用户消息最前面加 [ctx N]"),
}
ARMS1 = tuple(a for a in ARMS if ARMS[a]["part"] == 1)
ARMS2 = tuple(a for a in ARMS if ARMS[a]["part"] == 2)
BANDS = ("安全", "部分", "断崖")


class Lcg:
    """自己写的伪随机数（不用 random 模块：它的算法在不同 Python 版本之间不保证一样；参考文本和运行顺序必须逐字节可复现）。"""

    def __init__(self, seed):
        self.x = (seed * 2862933555777941757 + 3037000493) % (1 << 64)

    def below(self, n):
        self.x = (self.x * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        return (self.x >> 33) % n

    def shuffle(self, items):
        for i in range(len(items) - 1, 0, -1):
            j = self.below(i + 1)
            items[i], items[j] = items[j], items[i]


def reference_text():
    """约 16,000 字符的固定参考文本：写死的词表 + 固定种子，每句 6–14 个词。"""
    g = Lcg(REF_SEED)
    out, n = [], 0
    while n < REF_CHARS:
        k = 6 + g.below(9)
        s = " ".join(WORDS[g.below(len(WORDS))] for _ in range(k))
        s = s[0].upper() + s[1:] + "."
        out.append(s)
        n += len(s) + 1
    return " ".join(out)


REF = reference_text()


def first_message(tag):
    return "Session tag " + tag + "\n\n" + REF + "\n\n" + MSG_OK


def messages_for(part, tag):
    return [first_message(tag), MSG_YES, MSG_DONE] if part == 2 else [MSG_OK]      # part 0 = --mine：和第一部分一样只问 1 句


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def design_id():
    blob = json.dumps({"msgs": [MSG_OK, MSG_YES, MSG_DONE], "bands": [SAFE, CLIFF, ALPHA], "n": [N1, N2], "seeds": [SEED1, SEED2, REF_SEED],
                       "ref": [REF_CHARS, sha(REF)], "retries": RETRIES, "ext": EXT_SRC, "arms": ARMS, "v1": [V1_FILES, V1_PRESETS[1]],
                       "first": first_message("<tag>")[:20]}, sort_keys=True, ensure_ascii=False)
    return sha(blob)[:12]


STOP = {"flag": False}
PROCS = set()
STATE = {"done": 0, "spent": 0.0, "skipped": 0}


def say(*a):
    print(*a, flush=True)


# ───────────────────────────── 价格、配置 ─────────────────────────────
def dollars(p, usage):
    if not p:
        return None
    return ((usage.get("input", 0) or 0) * p["input"] + (usage.get("cacheRead", 0) or 0) * p["cacheRead"]
            + (usage.get("cacheWrite", 0) or 0) * p["cacheWrite"] + (usage.get("output", 0) or 0) * p["output"]) / 1e6


def req_cost(usage, prices):
    """一个请求的花费：pi 在 usage.cost.total 里按你 models.json 的价格算好的；没有就自己按价格算。"""
    c = (usage.get("cost") or {}).get("total")
    if isinstance(c, (int, float)) and not isinstance(c, bool) and (c > 0 or not prices):
        return float(c)
    return dollars(prices, usage)


def agent_dir():
    return os.environ.get("PI_CODING_AGENT_DIR") or os.path.expanduser("~/.pi/agent")


def read_json(path):
    try:
        return json.load(open(path, encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}


def pick_model(model_arg):
    """--model provider/模型id，否则 settings.json 里的默认模型。返回 (配置目录, models.json 内容, provider 名, 模型 id)。"""
    d = agent_dir()
    models, settings = read_json(os.path.join(d, "models.json")), read_json(os.path.join(d, "settings.json"))
    providers = models.get("providers", {})
    if model_arg:
        name, _, mid = model_arg.partition("/")
    else:
        name, mid = settings.get("defaultProvider"), settings.get("defaultModel")
    name = name or next(iter(providers), None)
    p = providers.get(name) or {}
    mid = mid or (p.get("models") or [{}])[0].get("id")
    if not any(x.get("id") == mid for x in p.get("models") or []):
        sys.exit("在 %s/models.json 里找不到 provider %s 下的模型 %s。可以用 --model provider/模型id 指定" % (d, name, mid))
    return d, models, name, mid


def model_prices(models, name, mid):
    ent = next(x for x in models["providers"][name]["models"] if x.get("id") == mid)
    c = ent.get("cost") or {}
    return {k: float(c.get(k) or 0) for k in ("input", "output", "cacheRead", "cacheWrite")} if c else None


def write_private(path, text, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def make_agent_dir(root, label, src, models, name, mid, presets=None):
    """root 下的一份私有 pi 配置目录：models.json、auth.json 的副本 + 最小的 settings.json（+ presets.json）。你真正的 ~/.pi/agent 一个字不动。"""
    d = os.path.join(root, label)
    os.makedirs(d, mode=0o700)
    write_private(os.path.join(d, "models.json"), json.dumps(models, ensure_ascii=False, indent=1))
    write_private(os.path.join(d, "settings.json"), json.dumps({"defaultProvider": name, "defaultModel": mid, "defaultThinkingLevel": "low"}))
    if os.path.exists(os.path.join(src, "auth.json")):
        write_private(os.path.join(d, "auth.json"), open(os.path.join(src, "auth.json"), encoding="utf-8-sig").read())
    if presets is not None:
        write_private(os.path.join(d, "presets.json"), presets, 0o644)
    return d


def file_sha(path):
    try:
        return hashlib.sha256(open(path, "rb").read()).hexdigest()
    except OSError:
        return None


def check_v1_files():
    """-> 问题列表。包里放的 v1 副本必须和登记的 sha256 逐字节相同。"""
    probs = []
    for name, want in V1_FILES.items():
        got = file_sha(os.path.join(HERE, "v1-extensions", name))
        if got != want:
            probs.append("v1-extensions/%s 的 sha256 是 %s，应当是 %s…" % (name, (got or "文件不存在")[:12], want[:8]))
    got = file_sha(os.path.join(HERE, V1_PRESETS[0]))
    if got != V1_PRESETS[1]:
        probs.append("%s 的 sha256 是 %s，应当是 %s…" % (V1_PRESETS[0], (got or "文件不存在")[:12], V1_PRESETS[1][:8]))
    return probs


def make_ext_files(root):
    """把每个扩展写成 root/ext/<名字>.ts，返回 {名字: 路径}；v1: 开头的指向包里的原文件。"""
    d = os.path.join(root, "ext")
    os.makedirs(d)
    paths = {}
    for key, src in EXT_SRC.items():
        paths[key] = os.path.join(d, key + ".ts")
        write_private(paths[key], src, 0o644)
    for name in V1_FILES:
        paths["v1:" + name] = os.path.join(HERE, "v1-extensions", name)
    return paths


# ───────────────────────────── 读 pi 的输出 ─────────────────────────────
def parse_events(stdout):
    """pi --mode json 的输出 -> (系统消息列表 [(第几句, 消息)], 每句一项的列表, 最后一句有没有收尾)。
    每条用户消息的运行框在 agent_start … agent_end 之间；里面每个 turn_end 是一次网关请求。"""
    sysmsgs, sents, cur, k = [], [], None, 0
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t = e.get("type")
        if t == "message_start" and (e.get("message") or {}).get("role") == "system":
            sysmsgs.append((max(1, k), e["message"]))
        elif t == "agent_start":
            k += 1
            cur = {"msgs": []}
        elif t == "turn_end" and cur is not None:
            cur["msgs"].append(e.get("message") or {})
        elif t == "agent_end" and cur is not None:
            sents.append(cur)
            cur = None
    return sysmsgs, sents, cur is None


def sentence_numbers(sent, prices):
    """一句话的数字：用量只取第 1 个请求（它的内容在后面的请求发出之前就定了）；请求数、花费（这句里所有请求的 usage.cost.total 之和）另记。"""
    msgs = sent["msgs"]
    if not msgs:
        return {"err": "这一句没有任何请求（没有 turn_end 事件）"}
    for m in msgs:
        if m.get("stopReason") in ("error", "aborted"):
            return {"err": "pi 报错：%s" % str(m.get("errorMessage") or m.get("stopReason"))[:200]}
    u = msgs[0].get("usage") or {}
    inp, cr, cw = [int(u.get(k) or 0) for k in ("input", "cacheRead", "cacheWrite")]
    T = inp + cr + cw
    if T <= 0:
        return {"err": "取不到第 1 个请求的用量（usage 缺失或总输入为 0）"}
    costs = [req_cost(m.get("usage") or {}, prices) for m in msgs]
    last = msgs[-1]
    text = "".join(b.get("text", "") for b in (last.get("content") or []) if isinstance(b, dict) and b.get("type") == "text")
    return {"requests": len(msgs), "T": T, "C": cr, "U": T - cr, "in": inp, "cw": cw, "out": int(u.get("output") or 0),
            "cost": None if (not prices or None in costs) else round(sum(costs), 8), "cost1": None if costs[0] is None else round(costs[0], 8),
            "text": text.strip()[:60], "stop": last.get("stopReason")}


def read_marks(path):
    """标记文件 -> {事件名: 行数}。"""
    marks = {}
    try:
        for ln in open(path, encoding="utf-8"):
            ln = ln.strip()
            if ln:
                ev = ln.split()[0]
                marks[ev] = marks.get(ev, 0) + 1
    except OSError:
        pass
    return marks


def check_marks(spec, marks, nsent, nreq):
    rule = spec.get("mark")
    if not rule:
        return []
    ev, kind = rule
    got = marks.get(ev, 0)
    want = {"atleast1": None, "per_sentence": nsent, "per_request": nreq}[kind]
    if kind == "atleast1":
        return [] if got >= 1 else ["标记文件里没有 %s 这一行（扩展可能没装上）" % ev]
    return [] if got == want else ["标记文件里 %s 有 %d 行，应当是 %d 行（每%s 1 行）" % (ev, got, want, "句" if kind == "per_sentence" else "个请求")]


def verify(arm, spec, sysmsgs, nsent, nreq, marks):
    """核对：真正发出去的系统提示，是不是这个臂该有的。-> 问题列表（空 = 对）。"""
    if not sysmsgs:
        return ["pi 的 JSON 输出里没有系统提示事件（role=system 的 message_start）"]
    probs = []
    _, first = sysmsgs[0]
    if "toolsAdded" not in first:
        probs.append("系统提示事件里没有工具名单（toolsAdded），没法核对工具")
    else:
        names = sorted(t.get("name") for t in first.get("toolsAdded") or [])
        want = sorted(x for x in spec["tools"].split(",") if x)
        if names != want:
            probs.append("工具是 %s，应当是 %s" % (names or "无", want))
    tr = (first.get("sections") or {}).get("team_rules")
    if arm == "S5":
        if not (tr and "Always answer in one word." in tr):
            probs.append("第 1 句的系统提示分段里没有 team_rules（或内容不对）")
    elif arm == "K1":
        if not (tr and "Run number 1." in tr):
            probs.append("第 1 句的系统提示分段里没有 team_rules“Run number 1.”")
    elif tr is not None:
        probs.append("这个臂不该有 team_rules，但第 1 句的系统提示里有")
    later = sysmsgs[1:]
    if arm == "K1":
        if len(later) != nsent - 1:
            probs.append("K1 第 2 句起每句前应当各有一条只含 team_rules 的系统消息，共 %d 条，实际 %d 条" % (nsent - 1, len(later)))
        else:
            for i, (k, m) in enumerate(later):
                want_n = i + 2
                sec = m.get("sections") or {}
                if k != want_n or list(sec) != ["team_rules"] or ("Run number %d." % want_n) not in sec["team_rules"]:
                    probs.append("第 %d 句前的系统消息不对（应当只含 team_rules“Run number %d.”）：第 %s 句、分段 %s" % (want_n, want_n, k, list(sec)))
    elif later:
        probs.append("多出 %d 条后来的系统消息（这个臂不该有）" % len(later))
    probs += check_marks(spec, marks, nsent, nreq)
    return probs


# ───────────────────────────── 跑 pi ─────────────────────────────
def pi_argv(ctx, spec, messages, ext_paths):
    a = ["-p"] + list(messages) + ["--mode", "json", "--no-session", "--no-extensions", "-ns", "-np", "-nc", "--thinking", "low"]
    a += ["--tools", spec["tools"]] if spec["tools"] else ["--no-tools"]
    for p in ext_paths:
        a += ["-e", p]
    return a


def kill_tree(p):
    """杀掉 pi 和它可能起的子进程：每个 pi 单独一个进程组，按组杀。"""
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        try:
            p.kill()
        except OSError:
            pass


def run_pi(ctx, spec, messages, mark_path):
    """-> ((stdout, stderr, 退出码), 错误说明)；一个 pi 进程，一路问完 messages。"""
    ext_paths = [ctx["ext"][k] for k in spec["exts"]]
    env = dict(os.environ, PI_CODING_AGENT_DIR=ctx["dirs"]["v1" if spec.get("v1") else "base"], EXT_LAB_MARK=mark_path)
    try:
        p = subprocess.Popen([ctx["pi"]] + pi_argv(ctx, spec, messages, ext_paths), cwd=ctx["work"], env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        return None, "%s: %s" % (type(e).__name__, e)
    PROCS.add(p)
    try:
        out, err = p.communicate(timeout=ctx["timeout"])
    except subprocess.TimeoutExpired:
        kill_tree(p)
        p.communicate()
        return None, "超时（%d 秒）" % ctx["timeout"]
    except BaseException:                       # Ctrl+C 等：先把这个 pi 杀掉（它在自己的进程组里，终端的 Ctrl+C 到不了它），再往上抛
        kill_tree(p)
        try:
            p.wait(timeout=3)
        except Exception:
            pass
        raise
    finally:
        PROCS.discard(p)
    return (out, err, p.returncode), None


def attempt(ctx, arm, spec, part, tries, rep):
    """跑一次 -> (结果 dict 或 None, 基础设施错误说明)。"""
    tag = os.urandom(4).hex()          # 随机标签：不同次运行的第 1 句从头就不同，缓存不能跨运行替你出答案
    messages = messages_for(part, tag)
    mark_path = os.path.join(ctx["root"], "marks", "%s-%d-%d.log" % (arm, rep, tries))
    t0 = time.time()
    res, err = run_pi(ctx, spec, messages, mark_path)
    if err:
        return None, err
    out, errtxt, rc = res
    sysmsgs, sents, closed = parse_events(out)
    if len(sents) < len(messages) or not closed:
        return None, "退出码 %s，只完成了 %d/%d 句：%s" % (rc, len(sents), len(messages), (errtxt or out).strip()[-200:])
    nums = [sentence_numbers(s, ctx["prices"]) for s in sents]
    bad = [n["err"] for n in nums if "err" in n]
    if bad:
        return None, bad[0]
    return {"sysmsgs": sysmsgs, "nums": nums, "rc": rc, "stderr": errtxt, "marks": read_marks(mark_path), "secs": round(time.time() - t0, 1), "tag": tag}, None


def over_cap(ctx):
    if ctx["cap"] and STATE["spent"] >= ctx["cap"]:
        STATE["skipped"] += 1
        return True
    return False


def do_run(job, ctx):
    part, arm, rep = job
    spec = ctx["arms"][arm]
    if STOP["flag"] or over_cap(ctx):
        return None
    got = err = None
    tries = 0
    for tries in range(1, RETRIES + 2):          # 事先写好的规则：基础设施失败最多重跑 RETRIES 次
        got, err = attempt(ctx, arm, spec, part, tries, rep)
        if got or STOP["flag"]:
            break
        if tries < RETRIES + 1:
            time.sleep(3)
    base = {"kind": "run", "part": part, "arm": arm, "rep": rep}
    if not got and STOP["flag"]:
        return None                              # Ctrl+C 时被杀掉的运行不算失败，也不进数据
    probs = []
    if got and not ctx["mine"]:
        probs = verify(arm, spec, got["sysmsgs"], len(got["nums"]), sum(n["requests"] for n in got["nums"]), got["marks"])
    elif got and ctx["mine"] and not got["sysmsgs"]:
        probs = ["pi 的 JSON 输出里没有系统提示事件（role=system 的 message_start）"]
    STATE["done"] += 1
    n = STATE["done"]
    if not got or probs:
        why = ("核对没过：" + "；".join(probs)) if probs else (err or "?")
        say("[%3d/%d] %-5s #%-2d 失败：%s" % (n, ctx["total"], arm, rep, why[:300]))
        return dict(base, err=why[:400], tries=tries, check_failed=bool(probs))
    nums = got["nums"]
    first = got["sysmsgs"][0][1]
    sysinfo = {"tools": sorted(t.get("name") for t in first.get("toolsAdded") or []),
               "sections": {k: len(v) for k, v in (first.get("sections") or {}).items() if isinstance(v, str)},
               "n_sysmsgs": len(got["sysmsgs"])}
    stderr = got["stderr"] or ""
    rec = dict(base, tries=tries, rc=got["rc"], ext_error="Extension error" in stderr, marks=got["marks"], secs=got["secs"], sents=[dict(n_, i=i) for i, n_ in enumerate(nums, 1)],
               sys=sysinfo, stderr=stderr.strip()[:300], tag=got["tag"])
    cost = sum(n_["cost"] or 0 for n_ in nums)
    STATE["spent"] += cost
    multi = [i for i, n_ in enumerate(nums, 1) if n_["requests"] != 1]
    if part == 2:
        body = "  ".join("第%d句 %s=%d" % (i, "T" if i == 1 else "U", n_["T"] if i == 1 else n_["U"]) for i, n_ in enumerate(nums, 1))
    else:
        body = "总输入 T=%d（命中 %d）" % (nums[0]["T"], nums[0]["C"])
    say("[%3d/%d] %-5s #%-2d %s  %4.1f 秒  %s%s%s" % (n, ctx["total"], arm, rep, body, rec["secs"], "" if not ctx["prices"] else "$%.6f" % cost,
                                                     "  ⚠ 第 %s 句不止 1 个请求（模型调了工具？），数字照取第 1 个请求" % multi if multi else "",
                                                     "  ⚠ stderr 里有 Extension error" if rec["ext_error"] else ""))
    return rec


# ───────────────────────────── 统计 ─────────────────────────────
def med(v):
    v = [x for x in v if x is not None]
    return statistics.median(v) if v else None


def band(u):
    if u is None:
        return "—"
    return "安全" if u <= SAFE else ("断崖" if u >= CLIFF else "部分")


def sep_p(n1, n2):
    """两组完全分开（一组的最小值 > 另一组的最大值）时，精确置换检验的双侧 p 值：只有“观察到的”和“镜像的”两种分法一样极端。"""
    return 2.0 / math.comb(n1 + n2, n1)


def separated(a, b):
    return bool(a) and bool(b) and (max(a) < min(b) or max(b) < min(a))


def good_runs(recs, part=None):
    return [r for r in recs if r and r.get("kind") == "run" and "err" not in r and (part is None or r["part"] == part)]


def fmt(x, spec="%g"):
    return "—" if x is None else spec % x


# ───────────────────────────── 汇总 ─────────────────────────────
def cost_summary(recs, ctx):
    g = good_runs(recs, 1)
    if not g:
        say("\n== 第一部分没有拿到数据")
        return False
    by = {a: [r for r in g if r["arm"] == a] for a in ARMS1}
    base_runs = by.get("S0") or []
    base = med([r["sents"][0]["T"] for r in base_runs])
    p = ctx["prices"]
    say("\n== 汇总一：每种写法让每个请求多几个 token（总输入 T = input + cacheRead + cacheWrite，取每次运行那句话的第 1 个请求；D = 该臂 T 的中位数 − S0 的中位数）")
    say("%-4s %-38s %-7s %-26s %-8s %-8s" % ("臂", "写法", "有效/计划", "各次的 T", "T 中位数", "D"))
    med_by = {}
    for a in ARMS1:
        rs = by[a]
        if ctx["planned"].get(a, 0) == 0:
            continue
        Ts = [r["sents"][0]["T"] for r in rs]
        m = med(Ts)
        med_by[a] = m
        d = None if (m is None or base is None) else m - base
        say("%-4s %-38s %-7s %-26s %-8s %-8s%s" % (a, ARMS[a]["doc"], "%d/%d" % (len(rs), ctx["planned"][a]), "/".join(str(x) for x in Ts) or "—", fmt(m),
                                                  "—" if d is None else ("%+g" % d) if a != "S0" else "（对照）", "  ← 各次不同" if len(set(Ts)) > 1 else ""))
    if "S3" in med_by and "S4" in med_by and med_by["S3"] is not None and med_by["S4"] is not None:
        say("  S4 比 S3 多 %+g（promptSnippet 和 promptGuidelines 的那一份）" % (med_by["S4"] - med_by["S3"]))
    if len(base_runs) >= 2:
        Ts = [r["sents"][0]["T"] for r in base_runs]
        say("  S0 的几次 T 最大减最小 = %d（底稳定：≤ 2）" % (max(Ts) - min(Ts)))
    if p and base is not None:
        say("  换成钱：多 D 个 token，命中缓存的请求按缓存读价（$%g/百万）每个请求多 D × $%g/百万，冷启动那一次按新输入价（$%g/百万）多 D × $%g/百万。" % (
            p["cacheRead"], p["cacheRead"], p["input"], p["input"]))
    multi = {}
    for r in g:
        for s in r["sents"]:
            if s["requests"] != 1:
                multi[r["arm"]] = multi.get(r["arm"], 0) + 1
    if multi:
        say("  ⚠ 请求数不是 1 的句子：%s（数字照取第 1 个请求，不影响上表）" % ", ".join("%s %d 句" % kv for kv in sorted(multi.items())))
    return True


def position_summary(recs, ctx):
    g = good_runs(recs, 2)
    if not g:
        say("\n== 第二部分没有拿到数据")
        return
    by = {a: [r for r in g if r["arm"] == a] for a in ARMS2}
    say("\n== 汇总二：第 2、3 句没命中缓存的 token（U = 总输入 − 命中；≤ %d 记“安全”，≥ %d 记“断崖”，其间“部分”；每格是各次运行的 U，括号里是中位数和档）" % (SAFE, CLIFF))
    say("%-4s %-34s %-7s %-9s %-34s %-34s" % ("臂", "写法", "有效/计划", "第1句 T", "第 2 句 U", "第 3 句 U"))
    cells = {}
    for a in ARMS2:
        rs = by[a]
        if ctx["planned"].get(a, 0) == 0:
            continue
        t1 = med([r["sents"][0]["T"] for r in rs])
        cols = []
        for si in (1, 2):
            us = [r["sents"][si]["U"] for r in rs]
            cells[(a, si)] = us
            cols.append("%s（%s，%s）" % ("/".join(str(u) for u in us) or "—", fmt(med(us)), band(med(us))))
        say("%-4s %-34s %-7s %-9s %-34s %-34s" % (a, ARMS[a]["doc"], "%d/%d" % (len(rs), ctx["planned"][a]), fmt(t1), cols[0], cols[1]))
    s0 = good_runs(recs, 1)
    s0T = med([r["sents"][0]["T"] for r in s0 if r["arm"] == "S0"])
    k0T = med([r["sents"][0]["T"] for r in by["K0"]]) if by.get("K0") else None
    if s0T is not None and k0T is not None:
        say("  参考文本约 %d token（K0 第 1 句的 T 的中位数 %g − S0 的 T 的中位数 %g）" % (k0T - s0T, k0T, s0T))
    else:
        say("  参考文本有多少 token：需要 S0 和 K0 都有数据（--only position 时没有 S0，可以用第一部分的 S0）")
    lines = []
    for si, label in ((1, "第 2 句"), (2, "第 3 句")):
        for i, a in enumerate(ARMS2):
            for b in ARMS2[i + 1:]:
                x, y = cells.get((a, si)), cells.get((b, si))
                if not x or not y or not separated(x, y):
                    continue
                ba, bb = BANDS.index(band(med(x))), BANDS.index(band(med(y)))
                p = sep_p(len(x), len(y))
                if abs(ba - bb) <= 1 and p < ALPHA:
                    lines.append("  %s：%s 对 %s 的 U 完全分开（%s 在 %d–%d，%s 在 %d–%d；精确置换检验 p=%.4f），档 %s / %s，不是“一样”" % (
                        label, a, b, a, min(x), max(x), b, min(y), max(y), p, band(med(x)), band(med(y))))
    if lines:
        say("  档相同或相邻、但完全分开的臂（记“差别真实”）：")
        for ln in lines:
            say(ln)
    costs = {}
    for a in ARMS2:
        v = [s["cost"] for r in by[a] for s in r["sents"][1:] if s["cost"] is not None]
        costs[a] = med(v)
    if ctx["prices"] and costs.get("K0") and costs.get("K1"):
        say("  每条消息的花费（第 2、3 句合并后的中位数）：" + "，".join("%s $%.6f" % (a, c) for a, c in costs.items() if c is not None)
            + "；K1 / K0 = %.1f 倍" % (costs["K1"] / costs["K0"]))
    elif ctx["prices"]:
        say("  每条消息的花费（第 2、3 句合并后的中位数）：" + "，".join("%s $%.6f" % (a, c) for a, c in costs.items() if c is not None))
    multi = {}
    for r in g:
        for s in r["sents"]:
            if s["requests"] != 1:
                multi[r["arm"]] = multi.get(r["arm"], 0) + 1
    if multi:
        say("  ⚠ 请求数不是 1 的句子：%s（数字照取第 1 个请求）" % ", ".join("%s %d 句" % kv for kv in sorted(multi.items())))


def check_summary(recs, ctx):
    runs = [r for r in recs if r and r.get("kind") == "run"]
    bad = [r for r in runs if "err" in r]
    chk = [r for r in bad if r.get("check_failed")]
    infra = [r for r in bad if not r.get("check_failed")]
    say("\n== 核对")
    say("  计划 %d 次运行，完成 %d 次；失败 %d 次（核对没过 %d 次，不重跑；基础设施失败 %d 次，已各重试最多 %d 次）" % (ctx["total"], len(runs) - len(bad), len(bad), len(chk), len(infra), RETRIES))
    for r in bad[:6]:
        say("    失败样例：%s #%d：%s" % (r["arm"], r["rep"], r["err"]))
    s2 = [r for r in good_runs(recs, 1) if r["arm"] == "S2"]
    if s2:
        say("  S2（v1 三个扩展）：退出码都是 0 的 %d/%d 次；stderr 里出现 Extension error 的 %d 次" % (sum(1 for r in s2 if r["rc"] == 0), len(s2), sum(1 for r in s2 if r["ext_error"])))
    ext_err = [r for r in good_runs(recs) if r["ext_error"] and r["arm"] != "S2"]
    if ext_err:
        say("  ⚠ 这些运行的 stderr 里有 Extension error：%s" % ", ".join("%s#%d" % (r["arm"], r["rep"]) for r in ext_err[:8]))
        say("    第一条：%s" % ext_err[0]["stderr"][:200].replace("\n", " | "))
    return not chk


def mine_summary(recs, ctx):
    g = good_runs(recs, 0)
    base = [r for r in g if r["arm"] == "S0"]
    mine = [r for r in g if r["arm"] == "MINE"]
    if not base or not mine:
        say("\n== 没有拿到足够的数据（不装扩展 %d 次、装你的扩展 %d 次）" % (len(base), len(mine)))
        for r in recs:
            if r and "err" in r:
                say("   失败：%s #%d：%s" % (r["arm"], r["rep"], r["err"]))
        return
    tb, tm = [r["sents"][0]["T"] for r in base], [r["sents"][0]["T"] for r in mine]
    d = med(tm) - med(tb)
    say("\n== 你的扩展让每个请求多几个 token")
    say("  不装扩展：总输入 T = %s（%s）" % ("/".join(map(str, tb)), "两次相同" if len(set(tb)) == 1 else "两次不同，取中位数"))
    say("  装你的扩展：总输入 T = %s（%s）" % ("/".join(map(str, tm)), "两次相同" if len(set(tm)) == 1 else "两次不同，取中位数"))
    say("  差 D = %+g token（每个请求；命中缓存时多花 $%s，没命中时多花 $%s）" % (
        d, "—" if not ctx["prices"] else "%.7f" % (d * ctx["prices"]["cacheRead"] / 1e6), "—" if not ctx["prices"] else "%.7f" % (d * ctx["prices"]["input"] / 1e6)))
    tools_b, tools_m = base[0]["sys"]["tools"], mine[0]["sys"]["tools"]
    say("  工具名单：%s → %s%s" % (",".join(tools_b), ",".join(tools_m), "" if tools_b == tools_m else "（多出来的是你的扩展注册的；它们能进请求，是因为你用了 --mine-tools）"))
    sb, sm = base[0]["sys"]["sections"], mine[0]["sys"]["sections"]
    diffs = ["%s %+d 字符" % (k, sm.get(k, 0) - sb.get(k, 0)) for k in sorted(set(sb) | set(sm)) if sm.get(k, 0) != sb.get(k, 0)]
    say("  系统提示分段（字符数）的变化：%s" % ("；".join(diffs) if diffs else "没有"))
    if mine[0]["sys"]["n_sysmsgs"] != 1:
        say("  ⚠ 这次运行里系统消息有 %d 条（不装扩展时是 1 条）" % mine[0]["sys"]["n_sysmsgs"])
    errs = [r for r in mine if r["ext_error"]]
    if errs:
        say("  ⚠ 你的扩展报错了（stderr）：%s" % errs[0]["stderr"][:300].replace("\n", " | "))
    elif mine[0]["stderr"]:
        say("  你的扩展在 stderr 里说了：%s" % mine[0]["stderr"][:300].replace("\n", " | "))
    if d == 0 and not mine[0]["stderr"] and tools_b == tools_m and not diffs:
        say("  D = 0 又没有任何输出：要么你的扩展确实没往请求里加东西，要么它没装上。让它在 stderr 打一行（console.error），就能区分。")
    if any(s["requests"] != 1 for r in mine + base for s in r["sents"]):
        say("  ⚠ 有运行的请求数不是 1，总输入不是单个请求的")


# ───────────────────────────── 主程序 ─────────────────────────────
def pi_bin():
    b = os.environ.get("PI_BIN") or shutil.which("pi")
    if not b or not (os.access(b, os.X_OK) or shutil.which(b)):
        sys.exit("找不到可以运行的 pi（%s）。可以先 export PI_BIN=/路径/pi" % (b or "PATH 里没有 pi"))
    return b


def build_jobs(part, arms, reps, seed):
    """运行顺序：按“轮”分块（每轮每个臂各 1 次），每一轮内用固定种子打乱；这样各臂在时间上交错，不会有哪个臂集中在某一段时间里。"""
    g = Lcg(seed)
    jobs = []
    for rep in range(1, reps + 1):
        block = [(part, arm, rep) for arm in arms]
        g.shuffle(block)
        jobs += block
    return jobs


def main():
    ap = argparse.ArgumentParser(description="扩展实验室")
    ap.add_argument("--only", choices=("all", "cost", "position"), default="all", help="只做哪一部分（默认两部分都做）")
    ap.add_argument("--arms", help="只测这几个臂，逗号分隔（默认全部）")
    ap.add_argument("--reps1", type=int, default=N1, help="第一部分每个臂跑几次（默认 %d；别改，打分规则是按这个定的）" % N1)
    ap.add_argument("--reps2", type=int, default=N2, help="第二部分每个臂跑几次（默认 %d；别改）" % N2)
    ap.add_argument("--max-cost", type=float, default=0.30, help="花费上限（美元，默认 0.30）：超过就不再发新请求；0 = 不设上限")
    ap.add_argument("--timeout", type=int, default=300, help="一次运行最多等几秒（默认 300）")
    ap.add_argument("--model", help="provider/模型id；默认用 pi 的 settings.json 里的默认模型")
    ap.add_argument("--force", action="store_true", help="第一部分核对没过或没拿到数据时也继续第二部分")
    ap.add_argument("--mine", metavar="文件.ts", help="量你自己的扩展：和不装扩展各跑 2 次，打印每个请求多几个 token（不跑上面的实验）")
    ap.add_argument("--mine-tools", default="", help="--mine 时额外放开哪些工具名（你的扩展注册了工具才需要，逗号分隔）")
    ap.add_argument("--dry-run", action="store_true", help="不调模型：核对包里的文件，打印设计指纹和要跑的运行")
    a = ap.parse_args()
    arms_sel = [x.strip() for x in a.arms.split(",") if x.strip()] if a.arms else list(ARMS)
    for x in arms_sel:
        if x not in ARMS:
            sys.exit("--arms 里只能有 %s" % "、".join(ARMS))
    mine = None
    if a.mine:
        mine = os.path.abspath(os.path.expanduser(a.mine))
        if not os.path.isfile(mine):
            sys.exit("找不到你的扩展文件 %s" % a.mine)
    src, models, name, mid = pick_model(a.model)
    prices = model_prices(models, name, mid)
    pi = pi_bin()
    need_v1 = (not mine) and "S2" in arms_sel and a.only in ("all", "cost")
    probs = check_v1_files() if (need_v1 or a.dry_run) else []
    if probs:
        sys.exit("包里的 v1 副本和登记的不一致（这一课要的是 v1 的原文件）：\n  " + "\n  ".join(probs))
    root = tempfile.mkdtemp(prefix="ext-lab-")
    work = os.path.join(root, "work")
    os.makedirs(work)
    os.makedirs(os.path.join(root, "marks"))
    recs = []
    ctx = {"pi": pi, "root": root, "work": work, "prices": prices, "cap": a.max_cost, "timeout": a.timeout, "mine": bool(mine), "arms": dict(ARMS)}
    try:
        ver = subprocess.run([pi, "--version"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
        presets = open(os.path.join(HERE, V1_PRESETS[0]), encoding="utf-8").read() if os.path.exists(os.path.join(HERE, V1_PRESETS[0])) else None
        ctx["dirs"] = {"base": make_agent_dir(root, "agent-base", src, models, name, mid),
                       "v1": make_agent_dir(root, "agent-v1", src, models, name, mid, presets)}
        ctx["ext"] = make_ext_files(root)
        say("pi %s   模型 %s / %s   设计指纹 %s" % (ver, name, mid, design_id()))
        say("参考文本 %d 字符（sha256 %s…）；v1 三个扩展 sha256 %s；presets.json %s…" % (
            len(REF), sha(REF)[:12], " ".join(V1_FILES[n][:8] + "…" for n in V1_FILES), V1_PRESETS[1][:8]))
        say("私有目录 %s（你的 models.json、auth.json 的副本、实验用的扩展；跑完删除）。只给模型 read 工具（S3、S4 再加 today）。" % root)
        if prices:
            say("单价（每百万 token）：新输入 $%g，缓存读 $%g，输出 $%g" % (prices["input"], prices["cacheRead"], prices["output"]))
        else:
            say("（models.json 里这个模型没写价格，下面不算花费，也不设花费上限）")
            ctx["cap"] = 0
        jobs1 = jobs2 = []
        if mine:
            ctx["arms"]["S0"] = dict(ARMS["S0"], part=0)
            ctx["arms"]["MINE"] = dict(part=0, tools=",".join(["read"] + [x for x in a.mine_tools.split(",") if x]), exts=("mine",), v1=False, mark=None, doc="你的扩展")
            ctx["ext"]["mine"] = mine
            jobs = [(0, arm, rep) for rep in (1, 2) for arm in ("S0", "MINE")]
            ctx["planned"] = {"S0": 2, "MINE": 2}
            say("量你的扩展：%s（工具：%s）；不装扩展和装你的扩展各 2 次，每次问 1 句。" % (mine, ctx["arms"]["MINE"]["tools"]))
        else:
            a1 = [x for x in ARMS1 if x in arms_sel] if a.only in ("all", "cost") else []
            a2 = [x for x in ARMS2 if x in arms_sel] if a.only in ("all", "position") else []
            jobs1 = build_jobs(1, a1, a.reps1, SEED1)
            jobs2 = build_jobs(2, a2, a.reps2, SEED2)
            ctx["planned"] = dict([(x, a.reps1) for x in a1] + [(x, a.reps2) for x in a2])
            jobs = jobs1 + jobs2
            odd = ([] if (a.reps1 == N1 or not a1) else ["第一部分每个臂 %d 次（默认 %d）" % (a.reps1, N1)]) + ([] if (a.reps2 == N2 or not a2) else ["第二部分每个臂 %d 次（默认 %d）" % (a.reps2, N2)]) \
                + ([] if set(arms_sel) == set(ARMS) else ["只测 %s" % ",".join(arms_sel)])
            if odd:
                say("（注意：这次用了非默认的设置：%s。评分规则是按默认设计定的，这份日志不一定能打分。）" % "；".join(odd))
        ctx["total"] = len(jobs)
        if mine:
            say("共 %d 次运行，一个一个跑。\n" % ctx["total"])
        else:
            say("共 %d 次运行：第一部分 %d 次（每次 1 句），第二部分 %d 次（每次连问 3 句）；一个一个跑，不并行；花费上限 %s。第一行出来之前先等几秒。\n" % (
                ctx["total"], len(jobs1), len(jobs2), "$%.2f" % a.max_cost if ctx["cap"] else "无"))
        if a.dry_run:
            say("== --dry-run：不调模型。运行顺序（固定种子打乱）：")
            for part, arm, rep in jobs:
                say("  第%d部分 %-5s #%d  工具 %-10s 扩展 %s%s" % (part, arm, rep, ctx["arms"][arm]["tools"], ",".join(ctx["arms"][arm]["exts"]) or "无",
                                                              "  配置目录带 presets.json" if ctx["arms"][arm].get("v1") else ""))
            ex = ARMS["S3"] if "S3" in ctx["arms"] else ARMS["S0"]
            say("\n  例：S3 的命令行：%s" % " ".join([os.path.basename(pi)] + [x if len(x) < 60 else x[:57] + "…" for x in pi_argv(ctx, ex, [MSG_OK], ["<root>/ext/tool.ts"])]))
            say("  第二部分第 1 句 %d 字符（含 %d 字符的参考文本），第 2、3 句是 “%s” 和 “%s”。" % (len(first_message("00000000")), len(REF), MSG_YES, MSG_DONE))
            say("\n  v1 副本核对：通过（3 个扩展 + presets.json 的 sha256 和登记的一致）。")
            return
        printed1 = skipped2 = False
        try:
            for job in jobs:
                if jobs2 and job == jobs2[0] and jobs1:
                    # 第一部分跑完了：先汇总，再决定第二部分开不开始（事先写好：有核对没过的运行、或一个数据都没拿到 → 不开始，除非 --force）
                    cost_summary(recs, ctx)
                    printed1 = True
                    p1_bad = any(r.get("part") == 1 and r.get("check_failed") for r in recs)
                    p1_data = bool(good_runs(recs, 1))
                    if (p1_bad or not p1_data) and not a.force:
                        say("\n第二部分没有开始：第一部分%s（上面有说明）。把屏幕上的内容贴给我；想强行开始，加 --force。" % (
                            "有核对没过的运行" if p1_bad else "一个数据都没拿到"))
                        skipped2 = True
                        break
                r = do_run(job, ctx)
                if r:
                    recs.append(r)
        except KeyboardInterrupt:
            STOP["flag"] = True
            say("\n（被中断，下面是已有的结果）")
            for pr in list(PROCS):
                kill_tree(pr)
        if mine:
            mine_summary(recs, ctx)
        else:
            if jobs1 and not printed1:
                cost_summary(recs, ctx)
            if jobs2 and not skipped2:
                position_summary(recs, ctx)
            check_summary(recs, ctx)
        if STATE["skipped"]:
            say("\n花费到了上限（$%.4f），剩下 %d 个没有跑；上面的汇总只含已经跑的。" % (STATE["spent"], STATE["skipped"]))
        if prices:
            say("本次花费 $%.4f" % STATE["spent"])
        say("\n== 数据（每次运行一行，贴给我的时候别删）")
        for r in recs:
            say("数据 " + json.dumps(r, ensure_ascii=False, sort_keys=True))
    finally:
        shutil.rmtree(root, ignore_errors=True)
    if STOP["flag"]:
        os._exit(130)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass
