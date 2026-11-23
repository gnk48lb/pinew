#!/usr/bin/env python3
"""preset_mcp_lab.py —— “预设与 MCP 实验室”：MCP 服务器的四种露出方式让每个请求多几个 token；会话中途 /preset 切换会不会让缓存失效；模型用不用得上这几种 MCP 工具。

用法：
  python3 preset_mcp_lab.py 2>&1 | tee -ia ex6.log   三部分都做（约 25–30 分钟，预计 $0.06–0.20）
  python3 preset_mcp_lab.py --only size              只做第一部分（10 个臂 × 4 次，每次 1 句话）
  python3 preset_mcp_lab.py --only switch            只做第二部分（6 个臂 × 5–8 次，每次连问 3 句，中间夹着 /preset）
  python3 preset_mcp_lab.py --only use               只做第三部分（4 个臂 × 3–5 次，模型真的去用 MCP 工具）
  python3 preset_mcp_lab.py --mine-mcp [mcp.json]    量一个 MCP 配置：四种露出方式各让每个请求多几个 token（不带文件名 = 包里的迷你服务器，20 个工具）
  python3 preset_mcp_lab.py --demo-preset            演示：会话中途 /preset 切换前后，模型看到的工具名单（MCP 工具怎么被一起关掉）
  python3 preset_mcp_lab.py --dry-run                不调模型：核对包里的文件、迷你服务器和 node，打印设计指纹和要跑的运行

一、size（第一部分）：同一句话（“Reply with exactly the single word: OK”）、只开 read 工具，再加上每个臂该有的 MCP 入口，10 个臂各跑 4 次。
   读 pi 自己报的用量，取这句话的第 1 个请求的总输入 token。总输入是确定性的（同一个请求发两次，总输入一样），
   所以“某个臂比 E0 多几个 token”是精确的差，不是估计。
     N0 不装 MCP 的内置扩展（对照一）   E0 装上三个内置扩展但不配服务器（对照二）
     C12、C30 codemode，12 / 30 个工具   D12、D30 deferred，12 / 30 个工具
     X3、X12、X30 direct，3 / 12 / 30 个工具   H12 hidden，12 个工具
二、switch（第二部分）：每次运行是一个 pi 进程连问 3 句，第 1 句带一段约 16,000 字符的固定参考文本；第 1 句之后用 /preset 切一次预设。
   量第 2、3 句“没命中缓存的 token”U = 总输入 − 命中；U ≥ 1800 记“断崖”（整段历史按新输入重付）。
     W0 切到同一个预设（对照）   PT 只改思考档位   PL 只改工具表   PI 只改说明   PA 三样全改   PR 往返（lab-all 再切回 lab-base）
三、use（第三部分）：同一个问题，答案藏在 MCP 服务器的笔记里；模型要自己想办法拿到。记答对几次、用几个请求、花多少钱。
     U0 不接 MCP（边界对照，应当答不对）   UX direct   UD deferred   UC codemode（都是 12 个工具）

用你 ~/.pi/agent 里的网关、模型、密钥和价格，只给模型 read 工具（加上每个臂该有的 MCP 入口；第二部分的预设里写着 read 和 grep、find、ls）。
花费到 --max-cost（默认 $0.60）就自动停。运行一个一个跑，不并行（第二部分量的就是缓存）。
"""
import argparse, hashlib, json, math, os, re, shutil, signal, statistics, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))

# ───────────────────────────── 设计（路线图 11-14 那一行写的就是这一版；改一个字，下面的“设计指纹”就变） ─────────────────────────────
MSG_OK = "Reply with exactly the single word: OK"
MSG_YES = "Reply with exactly the single word: YES"
MSG_DONE = "Reply with exactly the single word: DONE"
QUESTION = "In my notes there is an unpaid invoice. Which company is it from and what is its due date? Answer in one short sentence."
JUDGE = {"company": "brightwell", "due": r"\b30(?:th)?\b"}     # 判对：最后一个回答里（不分大小写）有 brightwell，并且有独立的 30 或 30th
CLIFF = 1800                     # 第二部分：没命中的 token（U）≥ 1800 记“断崖”
ALPHA = 0.05                     # 一格和背景比：Fisher 精确检验（双侧）p < 0.05
FLOOR_GAP = 2                    # 第一部分的“底”：和最小值差 ≤ 2 的那些运行组成最低簇
SEEDS = {1: 6101, 2: 6102, 3: 6103}   # 运行顺序的种子（三部分各一个）
REF_SEED, REF_CHARS = 5103, 16000     # 参考文本的种子和长度（和第 5 课同一段：16,056 字符，sha256 缩写 692f1d8597c1）
RETRIES = 2                      # 基础设施失败（超时、网关报错、没拿到用量）最多重跑 2 次（第三部分的超时算“没答对”，不重跑）
TIMEOUTS = {1: 120, 2: 180, 3: 300}   # 一次运行最多等几秒
MAX_COST = 0.60
SERVER = "notes"
SERVER_DESC = "A small notebook: read and change notes"
MINI_SHA = "903715bd1ed58f56986f75960e41318d4fa1d3a11fbaf3c0a0597e9e0aeb95af"   # mcp/mini_mcp.js（12,454 字节）
PRESET_SHA = "7942b7f933f97bcc29024a87f8526969413ba6f87b0bec537648a2926a0a4e29"   # v1-extensions/preset.ts（和你 v1 里的逐字节相同）
MINI_TOOLS = ["list_notes", "get_note", "search_notes"]   # 迷你服务器的前三个工具（真能用的前三个）
MINE_TOOLS = 20                  # --mine-mcp 不带文件名时，迷你服务器给几个工具
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

TEXT_A = "Lab preset text A. Keep answers short and plain, and answer in the language of the question."
TEXT_B = ("Lab preset text B. FOCUS MODE: answer in one short sentence, with no list and no preamble. Do not restate the question and do not add "
          "caveats unless the user asks for them. If something is unclear, say in one sentence what is unclear. Always answer in the language of the question.")
LAB_PRESETS = {      # 第二部分的 5 个实验用预设（每个都把三个字段写全：preset.ts 对没写的字段不动，会留着上一个预设设的值；只有 instructions 跟着当前预设走）
    "lab-base": {"thinkingLevel": "low", "tools": ["read"], "instructions": TEXT_A},
    "lab-think": {"thinkingLevel": "high", "tools": ["read"], "instructions": TEXT_A},
    "lab-tools": {"thinkingLevel": "low", "tools": ["read", "grep", "find", "ls"], "instructions": TEXT_A},
    "lab-instr": {"thinkingLevel": "low", "tools": ["read"], "instructions": TEXT_B},
    "lab-all": {"thinkingLevel": "high", "tools": ["read", "grep", "find", "ls"], "instructions": TEXT_B},
}
DEMO_PRESETS = {     # --demo-preset 用的两个预设
    "demo-plain": {"thinkingLevel": "low", "tools": ["read"], "instructions": "Demo preset: plain."},
    "demo-keep": {"thinkingLevel": "low", "tools": ["read"] + ["mcp__%s__%s" % (SERVER, t) for t in MINI_TOOLS], "instructions": "Demo preset: keep the MCP tools."},
}

EXT3 = ("builtin:mcp", "builtin:codemode", "builtin:tool-search")
GREP3 = ["find", "grep", "ls"]


def _arm(part, reps, tools, ext, mcp, doc, **kw):
    return dict(part=part, reps=reps, tools=tools, ext=tuple(ext), mcp=mcp, doc=doc, **kw)


def _sw(doc, switches, names, levels, later):
    return _arm(2, None, None, ("v1:preset.ts",), None, doc, switches=switches, startup_preset="lab-base",
                expect={"names": names, "levels": levels, "later": later})


ARMS = {
    "N0": _arm(1, 4, "read", (), None, "不装 MCP 的内置扩展（对照一）"),
    "E0": _arm(1, 4, "read", EXT3, None, "装上三个内置扩展、不配服务器（对照二，D 都以它为底）"),
    "C12": _arm(1, 4, "read,codemode", EXT3, ("codemode", 12), "codemode，12 个工具"),
    "C30": _arm(1, 4, "read,codemode", EXT3, ("codemode", 30), "codemode，30 个工具"),
    "D12": _arm(1, 4, "read,tool_search", EXT3, ("deferred", 12), "deferred，12 个工具"),
    "D30": _arm(1, 4, "read,tool_search", EXT3, ("deferred", 30), "deferred，30 个工具"),
    "X3": _arm(1, 4, "read,mcp__notes__*", EXT3, ("direct", 3), "direct，3 个工具"),
    "X12": _arm(1, 4, "read,mcp__notes__*", EXT3, ("direct", 12), "direct，12 个工具"),
    "X30": _arm(1, 4, "read,mcp__notes__*", EXT3, ("direct", 30), "direct，30 个工具"),
    "H12": _arm(1, 4, "read", EXT3, ("hidden", 12), "hidden，12 个工具"),
    "W0": dict(_sw("切到同一个预设 lab-base（对照）", {"1": "lab-base"}, ["lab-base"] * 3, [], []), reps=8),
    "PT": dict(_sw("只改思考档位（lab-think）", {"1": "lab-think"}, ["lab-base", "lab-think", "lab-think"], ["high"], []), reps=8),
    "PL": dict(_sw("只改工具表（lab-tools）", {"1": "lab-tools"}, ["lab-base", "lab-tools", "lab-tools"], [], [[2, GREP3, []]]), reps=5),
    "PI": dict(_sw("只改说明（lab-instr）", {"1": "lab-instr"}, ["lab-base", "lab-instr", "lab-instr"], [], []), reps=5),
    "PA": dict(_sw("三样全改（lab-all）", {"1": "lab-all"}, ["lab-base", "lab-all", "lab-all"], ["high"], [[2, GREP3, []]]), reps=5),
    "PR": dict(_sw("往返：lab-all，再切回 lab-base", {"1": "lab-all", "2": "lab-base"}, ["lab-base", "lab-all", "lab-base"], ["high", "low"],
                   [[2, GREP3, []], [3, [], GREP3]]), reps=6),
    "U0": _arm(3, 3, "read", EXT3, None, "不接 MCP（边界对照：没有办法拿到笔记，应当答不对）"),
    "UX": _arm(3, 5, "read,mcp__notes__*", EXT3, ("direct", 12), "direct，12 个工具"),
    "UD": _arm(3, 5, "read,tool_search", EXT3, ("deferred", 12), "deferred，12 个工具"),
    "UC": _arm(3, 5, "read,codemode", EXT3, ("codemode", 12), "codemode，12 个工具"),
}
for _a in ("N0", "E0", "C12", "C30", "D12", "D30", "X3", "X12", "X30", "H12"):
    ARMS[_a].setdefault("switches", {})
ARMS1 = tuple(a for a in ARMS if ARMS[a]["part"] == 1)
ARMS2 = tuple(a for a in ARMS if ARMS[a]["part"] == 2)
ARMS3 = tuple(a for a in ARMS if ARMS[a]["part"] == 3)
MCP_ARMS1 = ("C12", "C30", "D12", "D30", "X3", "X12", "X30")
PARTS = {"size": 1, "switch": 2, "use": 3}
PART_NAME = {1: "size", 2: "switch", 3: "use"}


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
    """约 16,000 字符的固定参考文本：写死的词表 + 固定种子，每句 6–14 个词（和第 5 课同一段）。"""
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


def messages_for(spec, tag):
    """一次运行发给 pi 的所有消息（-p 后面的那一串）。第二部分：第 1 句、（切换）、第 2 句、（切换）、第 3 句。"""
    part = spec["part"]
    if part in (0, 1):
        return [MSG_OK]
    if part == 3:
        return [QUESTION]
    out = []
    for i, s in enumerate([first_message(tag), MSG_YES, MSG_DONE], 1):
        out.append(s)
        if str(i) in spec["switches"]:
            out.append("/preset " + spec["switches"][str(i)])
    return out


def n_sentences(spec):
    return 3 if spec["part"] == 2 else 1


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def design_id():
    blob = json.dumps({"msgs": [MSG_OK, MSG_YES, MSG_DONE], "question": QUESTION, "judge": JUDGE, "cliff": [CLIFF, ALPHA, FLOOR_GAP],
                       "seeds": SEEDS, "ref": [REF_SEED, REF_CHARS, sha(REF)], "retries": RETRIES, "timeouts": TIMEOUTS,
                       "server": [SERVER, SERVER_DESC, MINI_SHA, MINI_TOOLS], "preset_sha": PRESET_SHA, "presets": LAB_PRESETS, "arms": ARMS,
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


def mcp_json(server_cfg):
    return json.dumps({"mcpServers": server_cfg}, ensure_ascii=False, indent=1)


def server_cfg(node, mini, exposure, n):
    return {SERVER: {"command": node, "args": [mini], "env": {"MINI_MCP_TOOLS": str(n)}, "description": SERVER_DESC, "exposure": exposure}}


def make_agent_dir(root, label, src, models, name, mid, presets=None, mcp=None):
    """root 下的一份私有 pi 配置目录：models.json、auth.json 的副本 + 最小的 settings.json（+ presets.json、mcp.json）。你真正的 ~/.pi/agent 一个字不动。"""
    d = os.path.join(root, label)
    os.makedirs(d, mode=0o700)
    write_private(os.path.join(d, "models.json"), json.dumps(models, ensure_ascii=False, indent=1))
    write_private(os.path.join(d, "settings.json"), json.dumps({"defaultProvider": name, "defaultModel": mid, "defaultThinkingLevel": "low"}))
    if os.path.exists(os.path.join(src, "auth.json")):
        write_private(os.path.join(d, "auth.json"), open(os.path.join(src, "auth.json"), encoding="utf-8-sig").read())
    if presets is not None:
        write_private(os.path.join(d, "presets.json"), json.dumps(presets, ensure_ascii=False, indent=1), 0o644)
    if mcp is not None:
        write_private(os.path.join(d, "mcp.json"), mcp_json(mcp), 0o600)
    return d


def file_sha(path):
    try:
        return hashlib.sha256(open(path, "rb").read()).hexdigest()
    except OSError:
        return None


def check_files():
    """-> 问题列表。包里放的 mini_mcp.js 和 v1 的 preset.ts 必须和登记的 sha256 逐字节相同。"""
    probs = []
    got = file_sha(os.path.join(HERE, "mcp", "mini_mcp.js"))
    if got != MINI_SHA:
        probs.append("mcp/mini_mcp.js 的 sha256 是 %s，应当是 %s…" % ((got or "文件不存在")[:12], MINI_SHA[:8]))
    got = file_sha(os.path.join(HERE, "v1-extensions", "preset.ts"))
    if got != PRESET_SHA:
        probs.append("v1-extensions/preset.ts 的 sha256 是 %s，应当是 %s…" % ((got or "文件不存在")[:12], PRESET_SHA[:8]))
    return probs


def find_node():
    n = shutil.which("node")
    if not n:
        return None, "PATH 里没有 node（pi 本身要用 node，迷你服务器也是）"
    try:
        v = subprocess.run([n, "--version"], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20).stdout.strip()
    except Exception as e:     # noqa
        return None, "运行 %s --version 失败：%s" % (n, e)
    return (n, v), None


def preflight_mini(node, mini):
    """启动前把迷你服务器起一遍：问它 initialize、tools/list、两次 tools/call，数不对就停。-> 问题列表。"""
    probs = []
    req = "\n".join(json.dumps(x) for x in (
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "search_notes", "arguments": {"text": "unpaid"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "get_note", "arguments": {"id": 3}}})) + "\n"
    for n in (3, 12, 30):
        try:
            p = subprocess.run([node, mini], input=req, capture_output=True, text=True, timeout=30, env=dict(os.environ, MINI_MCP_TOOLS=str(n)))
        except Exception as e:     # noqa
            probs.append("迷你服务器（%d 个工具）起不来：%s" % (n, e))
            continue
        got = {}
        for ln in p.stdout.splitlines():
            try:
                m = json.loads(ln)
            except ValueError:
                continue
            if isinstance(m, dict) and "id" in m:
                got[m["id"]] = m
        tl = ((got.get(2) or {}).get("result") or {}).get("tools") or []
        if len(tl) != n or [t.get("name") for t in tl[:3]] != MINI_TOOLS:
            probs.append("迷你服务器（%d 个工具）的 tools/list 不对：%d 个，前三个 %s" % (n, len(tl), [t.get("name") for t in tl[:3]]))
        txt = lambda i: "".join(c.get("text", "") for c in (((got.get(i) or {}).get("result") or {}).get("content") or []))   # noqa: E731
        if txt(3) != "3: Invoice 4417" or "Brightwell" not in txt(4):
            probs.append("迷你服务器（%d 个工具）的 tools/call 不对：search_notes → %r；get_note → %r" % (n, txt(3), txt(4)[:60]))
    return probs


# ───────────────────────────── 读 pi 的输出 ─────────────────────────────
def parse_events(stdout):
    """pi --mode json 的输出 -> dict：
    sysmsgs [(第几句, 消息)]；sents 每条用户消息一项（agent_start … agent_end 之间：turn_end 是一次网关请求，tool_execution_* 是工具调用）；
    names 预设名（每个 turn_start 留一条 preset-state）；levels 思考档位变化；closed 最后一句有没有收尾。"""
    sysmsgs, sents, cur, k = [], [], None, 0
    names, levels = [], []
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
            cur = {"msgs": [], "tools": [], "tool_errors": 0}
        elif t == "turn_end" and cur is not None:
            cur["msgs"].append(e.get("message") or {})
        elif t == "tool_execution_start" and cur is not None:
            cur["tools"].append({"name": e.get("toolName"), "args": e.get("args")})
        elif t == "tool_execution_end" and cur is not None:
            if e.get("isError"):
                cur["tool_errors"] += 1
        elif t == "agent_end" and cur is not None:
            sents.append(cur)
            cur = None
        elif t == "entry_appended":
            ent = e.get("entry") or {}
            if ent.get("customType") == "preset-state":
                names.append((ent.get("data") or {}).get("name"))
        elif t == "thinking_level_changed":
            levels.append(e.get("level"))
    return {"sysmsgs": sysmsgs, "sents": sents, "names": names, "levels": levels, "closed": cur is None}


def request_numbers(m, prices):
    u = m.get("usage") or {}
    inp, cr, cw = [int(u.get(k) or 0) for k in ("input", "cacheRead", "cacheWrite")]
    T = inp + cr + cw
    return {"T": T, "C": cr, "U": T - cr, "in": inp, "cw": cw, "out": int(u.get("output") or 0), "cost": req_cost(u, prices) if T > 0 else None}


def sentence_numbers(sent, prices):
    """一句话的数字：用量只取第 1 个请求（它的内容在后面的请求发出之前就定了）；请求数、花费（这句里所有请求的 usage.cost.total 之和）另记。"""
    msgs = sent["msgs"]
    if not msgs:
        return {"err": "这一句没有任何请求（没有 turn_end 事件）"}
    for m in msgs:
        if m.get("stopReason") in ("error", "aborted"):
            return {"err": "pi 报错：%s" % str(m.get("errorMessage") or m.get("stopReason"))[:200]}
    nums = [request_numbers(m, prices) for m in msgs]
    if nums[0]["T"] <= 0:
        return {"err": "取不到第 1 个请求的用量（usage 缺失或总输入为 0）"}
    costs = [n["cost"] for n in nums]
    last = msgs[-1]
    text = "".join(b.get("text", "") for b in (last.get("content") or []) if isinstance(b, dict) and b.get("type") == "text")
    n0 = nums[0]
    return {"requests": len(msgs), "T": n0["T"], "C": n0["C"], "U": n0["U"], "in": n0["in"], "cw": n0["cw"], "out": n0["out"],
            "cost": None if (not prices or None in costs) else round(sum(costs), 8), "cost1": None if costs[0] is None else round(costs[0], 8),
            "text": text.strip()[:60], "stop": last.get("stopReason"), "tools": [t.get("name") for t in sent["tools"]]}


def use_numbers(sent, prices):
    """第三部分一次运行（一句话、可能很多个请求）的数字：每个请求的 T、C、U；总没命中量 ΣU；花费；调了哪些工具；答对没有。"""
    msgs = sent["msgs"]
    if not msgs:
        return {"err": "没有任何请求（没有 turn_end 事件）"}
    for m in msgs:
        if m.get("stopReason") in ("error", "aborted"):
            return {"err": "pi 报错：%s" % str(m.get("errorMessage") or m.get("stopReason"))[:200]}
    nums = [request_numbers(m, prices) for m in msgs]
    if any(n["T"] <= 0 for n in nums):
        return {"err": "有请求取不到用量（usage 缺失或总输入为 0）"}
    last = msgs[-1]
    text = "".join(b.get("text", "") for b in (last.get("content") or []) if isinstance(b, dict) and b.get("type") == "text").strip()
    costs = [n["cost"] for n in nums]
    paths = []
    for t in sent["tools"]:
        a = t.get("args") if isinstance(t.get("args"), dict) else {}
        if t.get("name") == "read":
            paths.append(str(a.get("path") or a.get("file_path") or ""))
    return {"requests": len(msgs), "reqs": [{"T": n["T"], "C": n["C"], "U": n["U"]} for n in nums], "sumU": sum(n["U"] for n in nums),
            "cost": None if (not prices or None in costs) else round(sum(costs), 8), "answer": text[:300], "stop": last.get("stopReason"),
            "tools_seq": [t.get("name") for t in sent["tools"]], "tool_errors": sent["tool_errors"], "read_paths": paths,
            "read_codemode_md": any("codemode.md" in p for p in paths), "correct": judge(text)}


def judge(text):
    t = (text or "").lower()
    return bool(JUDGE["company"] in t and re.search(JUDGE["due"], t))


def tool_names(msg):
    return sorted(x.get("name") for x in msg.get("toolsAdded") or [])


def mcp_section(msg):
    return (msg.get("sections") or {}).get("mcp_servers")


def verify_tools(spec, first):
    """第一、三部分：第一条系统消息里的工具名单和 mcp_servers 段，是不是这个臂该有的。-> 问题列表。"""
    probs = []
    if "toolsAdded" not in first:
        return ["系统提示事件里没有工具名单（toolsAdded），没法核对工具"]
    names = tool_names(first)
    sec = mcp_section(first)
    mcp = spec["mcp"]
    kind = mcp[0] if mcp else None
    if kind == "direct":
        pre = "mcp__%s__" % SERVER
        direct = [n for n in names if n.startswith(pre)]
        if sorted(n for n in names if not n.startswith(pre)) != ["read"] or len(direct) != mcp[1]:
            probs.append("direct 臂应当是 read 加 %d 个 %s 开头的工具，实际是 %s" % (mcp[1], pre, names or "无"))
        if sec is not None:
            probs.append("direct 臂不该有 mcp_servers 段，但系统提示里有")
        return probs
    want = {"codemode": ["codemode", "read"], "deferred": ["read", "tool_search"]}.get(kind, ["read"])
    if names != want:
        probs.append("工具是 %s，应当是 %s" % (names or "无", want))
    label = {"codemode": "(codemode)", "deferred": "(tool_search)"}.get(kind)    # pi 在段里把 deferred 服务器标成 tool_search
    if label:
        if not (sec and ("mcp__%s %s" % (SERVER, label)) in sec):
            probs.append("系统提示里没有 mcp_servers 段，或段里没有 mcp__%s %s" % (SERVER, label))
    elif sec is not None:
        probs.append("这个臂不该有 mcp_servers 段，但系统提示里有")
    return probs


def verify(arm, spec, ev, nsent, nreq):
    """核对：真正发出去的请求，是不是这个臂该有的。-> 问题列表（空 = 对）。"""
    sysmsgs = ev["sysmsgs"]
    if not sysmsgs:
        return ["pi 的 JSON 输出里没有系统提示事件（role=system 的 message_start）"]
    _, first = sysmsgs[0]
    probs = verify_tools(spec, first)
    if spec["part"] in (1, 3):
        return probs
    exp = spec["expect"]
    if ev["names"] != exp["names"]:
        probs.append("preset-state 名字是 %s，应当是 %s" % (ev["names"], exp["names"]))
    if ev["levels"] != exp["levels"]:
        probs.append("thinking_level_changed 的档位是 %s，应当是 %s" % (ev["levels"], exp["levels"]))
    later = sysmsgs[1:]
    want = exp["later"]
    if len(later) != len(want):
        probs.append("第一条之后的系统消息有 %d 条，应当 %d 条" % (len(later), len(want)))
    else:
        for (k, m), (wk, wadd, wrem) in zip(later, want):
            got_add = sorted(x.get("name") for x in m.get("toolsAdded") or [])
            got_rem = sorted(x.get("name") if isinstance(x, dict) else x for x in m.get("toolsRemoved") or [])
            if k != wk or got_add != sorted(wadd) or got_rem != sorted(wrem):
                probs.append("第 %d 句前的系统消息不对：toolsAdded %s、toolsRemoved %s；应当 toolsAdded %s、toolsRemoved %s（第 %d 句）" % (
                    wk, got_add, got_rem, sorted(wadd), sorted(wrem), k))
    return probs


# ───────────────────────────── 跑 pi ─────────────────────────────
def pi_argv(ctx, spec, messages, ext_paths):
    a = ["-p"] + list(messages) + ["--mode", "json", "--no-session", "--no-extensions", "-ns", "-np", "-nc", "--thinking", "low"]
    if spec["tools"]:
        a += ["--tools", spec["tools"]]
    for p in ext_paths:
        a += ["-e", p]
    if spec.get("startup_preset"):
        a += ["--preset", spec["startup_preset"]]
    return a


def ext_path(key):
    return os.path.join(HERE, "v1-extensions", key[3:]) if key.startswith("v1:") else key


def kill_tree(p):
    """杀掉 pi 和它可能起的子进程（迷你服务器）：每个 pi 单独一个进程组，按组杀。"""
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        try:
            p.kill()
        except OSError:
            pass


def run_pi(ctx, arm, spec, messages, timeout):
    """-> ((stdout, stderr, 退出码), 错误说明)；一个 pi 进程，一路问完 messages。"""
    exts = [ext_path(k) for k in spec["ext"]]
    env = dict(os.environ, PI_CODING_AGENT_DIR=ctx["dirs"][arm])
    try:
        p = subprocess.Popen([ctx["pi"]] + pi_argv(ctx, spec, messages, exts), cwd=ctx["work"], env=env, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        return None, "%s: %s" % (type(e).__name__, e)
    PROCS.add(p)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(p)
        p.communicate()
        return None, "超时（%d 秒）" % timeout
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


def attempt(ctx, arm, spec, tries):
    """跑一次 -> (结果 dict 或 None, 基础设施错误说明)。"""
    tag = os.urandom(4).hex()          # 随机标签：不同次运行的第 1 句从头就不同，缓存不能跨运行替你出答案
    messages = messages_for(spec, tag)
    timeout = ctx["timeout"] or TIMEOUTS.get(spec["part"], TIMEOUTS[1])
    t0 = time.time()
    res, err = run_pi(ctx, arm, spec, messages, timeout)
    if err:
        return None, err
    out, errtxt, rc = res
    ev = parse_events(out)
    ns = n_sentences(spec)
    if len(ev["sents"]) < ns or not ev["closed"]:
        return None, "退出码 %s，只完成了 %d/%d 句：%s" % (rc, len(ev["sents"]), ns, (errtxt or out).strip()[-200:])
    if spec["part"] == 3:
        nums = [use_numbers(ev["sents"][0], ctx["prices"])]
    else:
        nums = [sentence_numbers(s, ctx["prices"]) for s in ev["sents"]]
    bad = [n["err"] for n in nums if "err" in n]
    if bad:
        return None, bad[0]
    return {"ev": ev, "nums": nums, "rc": rc, "stderr": errtxt, "secs": round(time.time() - t0, 1), "tag": tag}, None


def over_cap(ctx):
    if ctx["cap"] and STATE["spent"] >= ctx["cap"]:
        STATE["skipped"] += 1
        return True
    return False


def sys_info(ev):
    first = ev["sysmsgs"][0][1] if ev["sysmsgs"] else {}
    later = []
    for k, m in ev["sysmsgs"][1:]:
        later.append({"k": k, "added": sorted(x.get("name") for x in m.get("toolsAdded") or []),
                      "removed": sorted(x.get("name") if isinstance(x, dict) else x for x in m.get("toolsRemoved") or [])})
    names = tool_names(first)
    return {"tools": names if len(names) <= 8 else names[:4] + ["…"], "n_tools": len(names), "n_mcp": len([x for x in names if x.startswith("mcp__")]),
            "sections": {k: len(v) for k, v in (first.get("sections") or {}).items() if isinstance(v, str)},
            "mcp_section": mcp_section(first), "n_sysmsgs": len(ev["sysmsgs"]), "later": later}


def do_run(job, ctx):
    part, arm, rep = job
    spec = ctx["arms"][arm]
    if STOP["flag"] or over_cap(ctx):
        return None
    got = err = None
    tries = 0
    for tries in range(1, RETRIES + 2):          # 事先写好的规则：基础设施失败最多重跑 RETRIES 次
        got, err = attempt(ctx, arm, spec, tries)
        if got or STOP["flag"]:
            break
        if part == 3 and err and err.startswith("超时"):
            break                                # 第三部分的超时算“没答对”，不重跑
        if tries < RETRIES + 1:
            time.sleep(3)
    base = {"kind": "run", "part": part, "arm": arm, "rep": rep}
    if not got and STOP["flag"]:
        return None                              # Ctrl+C 时被杀掉的运行不算失败，也不进数据
    STATE["done"] += 1
    n = STATE["done"]
    if not got and part == 3 and err and err.startswith("超时"):
        say("[%3d/%d] %-4s #%-2d 超时（%d 秒）：记“没答对”，不重跑" % (n, ctx["total"], arm, rep, ctx["timeout"] or TIMEOUTS[3]))
        return dict(base, tries=tries, timeout=True, correct=False, secs=float(ctx["timeout"] or TIMEOUTS[3]), requests=0, tools_seq=[], tool_errors=0,
                    read_codemode_md=False, read_paths=[], reqs=[], sumU=0, cost=None, answer="")
    probs = []
    if got:
        nreq = sum(x["requests"] for x in got["nums"])
        probs = ctx["verify"](arm, spec, got["ev"], len(got["nums"]), nreq)
    if not got or probs:
        why = ("核对没过：" + "；".join(probs)) if probs else (err or "?")
        say("[%3d/%d] %-4s #%-2d 失败：%s" % (n, ctx["total"], arm, rep, why[:300]))
        return dict(base, err=why[:400], tries=tries, check_failed=bool(probs))
    nums = got["nums"]
    stderr = got["stderr"] or ""
    rec = dict(base, tries=tries, rc=got["rc"], ext_error="Extension error" in stderr, secs=got["secs"], sys=sys_info(got["ev"]),
               stderr=stderr.strip()[:300], tag=got["tag"])
    if part == 3:
        rec.update(nums[0])
        cost = nums[0]["cost"] or 0
        STATE["spent"] += cost
        say("[%3d/%d] %-4s #%-2d %s  %d 个请求  ΣU=%d  %4.1f 秒  %s  工具：%s%s" % (
            n, ctx["total"], arm, rep, "答对" if rec["correct"] else "没答对", rec["requests"], rec["sumU"], rec["secs"],
            "" if not ctx["prices"] else "$%.6f" % cost, " → ".join(t.replace("mcp__%s__" % SERVER, "") for t in rec["tools_seq"]) or "无",
            "  ⚠ 工具报错 %d 次" % rec["tool_errors"] if rec["tool_errors"] else ""))
        return rec
    rec.update(sents=[dict(n_, i=i) for i, n_ in enumerate(nums, 1)])
    if part == 2:
        rec.update(names=got["ev"]["names"], levels=got["ev"]["levels"])
    cost = sum(n_["cost"] or 0 for n_ in nums)
    STATE["spent"] += cost
    multi = [i for i, n_ in enumerate(nums, 1) if n_["requests"] != 1]
    if part == 2:
        body = "  ".join("第%d句 %s=%d" % (i, "T" if i == 1 else "U", n_["T"] if i == 1 else n_["U"]) for i, n_ in enumerate(nums, 1))
    else:
        body = "总输入 T=%d（命中 %d）" % (nums[0]["T"], nums[0]["C"])
    say("[%3d/%d] %-4s #%-2d %s  %4.1f 秒  %s%s%s" % (n, ctx["total"], arm, rep, body, rec["secs"], "" if not ctx["prices"] else "$%.6f" % cost,
                                                    "  ⚠ 第 %s 句不止 1 个请求（模型调了工具？），数字照取第 1 个请求" % ",".join(str(i) for i in multi) if multi else "",
                                                    "  ⚠ stderr 里有 Extension error" if rec["ext_error"] else ""))
    return rec


# ───────────────────────────── 统计 ─────────────────────────────
def med(v):
    v = [x for x in v if x is not None]
    return statistics.median(v) if v else None


def fmt(x, spec="%g"):
    return "—" if x is None else spec % x


def good_runs(recs, part=None, arm=None):
    return [r for r in recs if r and r.get("kind") == "run" and "err" not in r and (part is None or r["part"] == part) and (arm is None or r["arm"] == arm)]


def floor_of(Ts):
    """第一部分一个臂的“底”：和最小值差 ≤ FLOOR_GAP 的那些运行组成最低簇，底 = 最低簇的中位数；最低簇只有 1 次 → 不稳。-> (底, 簇大小, 稳不稳)"""
    if not Ts:
        return None, 0, False
    lo = min(Ts)
    cl = [t for t in Ts if t - lo <= FLOOR_GAP]
    return statistics.median(cl), len(cl), len(cl) >= 2


def fisher_p(x1, n1, x2, n2):
    """2×2 表 [[x1, n1-x1], [x2, n2-x2]] 的双侧 Fisher 精确检验 p：概率 ≤ 观察到的那张表的所有表的概率之和。整数运算，没有浮点容差问题。"""
    K, N = x1 + x2, n1 + n2
    lo, hi = max(0, K - n2), min(K, n1)
    num = {x: math.comb(n1, x) * math.comb(n2, K - x) for x in range(lo, hi + 1)}
    return sum(v for v in num.values() if v <= num[x1]) / math.comb(N, K)


def cell_class(x, n, bc, bn):
    """一格（n 次有效运行里 x 次断崖）和背景（bn 格里 bc 个断崖）比的档：x ≥ ⌈0.75n⌉ 且 Fisher p < 0.05 → 断崖；x ≤ ⌊0.25n⌋ → 无断崖；其余 → 待定。-> (档, p)"""
    if n == 0 or bn == 0:
        return "—", None
    p = fisher_p(x, n, bc, bn)
    if x >= (3 * n + 3) // 4 and p < ALPHA:
        return "断崖", p
    if x <= n // 4:
        return "无断崖", p
    return "待定", p


def cliffs(recs, arm, si):
    """某个臂第 si 句（2 或 3）的各次运行的 U。"""
    return [r["sents"][si - 1]["U"] for r in good_runs(recs, 2, arm)]


def background(recs):
    w = good_runs(recs, 2, "W0")
    us = [r["sents"][i]["U"] for r in w for i in (1, 2)]
    return sum(1 for u in us if u >= CLIFF), len(us)


# ───────────────────────────── 汇总 ─────────────────────────────
def size_summary(recs, ctx):
    g = good_runs(recs, 1)
    if not g:
        say("\n== 第一部分没有拿到数据")
        return
    by = {a: [r for r in g if r["arm"] == a] for a in ARMS1}
    fl = {a: floor_of([r["sents"][0]["T"] for r in by[a]]) for a in ARMS1}
    base = fl["E0"][0] if by["E0"] and fl["E0"][2] else None
    p = ctx["prices"]
    say("\n== 汇总一：每种露出方式让每个请求多几个 token（总输入 T = input + cacheRead + cacheWrite，取每次运行那句话的第 1 个请求；")
    say("   底 = 和最小值差 ≤ %d 的那些运行（最低簇）的中位数；D = 该臂的底 − E0 的底）" % FLOOR_GAP)
    say("%-4s %-36s %-7s %-22s %-14s %-8s" % ("臂", "配置", "有效/计划", "各次的 T", "底（簇大小）", "D"))
    for a in ARMS1:
        if ctx["planned"].get(a, 0) == 0:
            continue
        Ts = [r["sents"][0]["T"] for r in by[a]]
        b, k, stable = fl[a]
        d = None if (b is None or base is None) else b - base
        flag = "" if (not Ts or max(Ts) - min(Ts) <= 2) else "  ← 4 次 T 最大减最小 > 2"
        if Ts and not stable:
            flag += "  ← 底不稳（最低簇只有 1 次）"
        say("%-4s %-36s %-7s %-22s %-14s %-8s%s" % (a, ARMS[a]["doc"], "%d/%d" % (len(Ts), ctx["planned"][a]), "/".join(str(x) for x in Ts) or "—",
                                                    "—" if b is None else "%s（%d）" % (fmt(b), k),
                                                    "（对照）" if a == "E0" else ("—" if d is None else "%+g" % d), flag))
    if by["N0"] and by["E0"] and fl["N0"][0] is not None and fl["E0"][0] is not None:
        say("  E0 − N0 = %+g（只装 MCP 的三个内置扩展、不配服务器，每个请求多几个 token）" % (fl["E0"][0] - fl["N0"][0]))

    def D(a):
        return None if (a not in fl or fl[a][0] is None or base is None) else fl[a][0] - base
    for a, b in (("C12", "C30"), ("D12", "D30")):
        if fl[a][0] is not None and fl[b][0] is not None:
            say("  %s 和 %s 的底相差 %+g（服务器有 12 个还是 30 个工具，codemode / deferred 的税变不变）" % (b, a, fl[b][0] - fl[a][0]))
    if D("X30") is not None and D("X3") is not None:
        slope = (D("X30") - D("X3")) / 27.0
        say("  direct 每个工具多 (D(X30) − D(X3)) ÷ 27 = %.1f token；X12 的 D 是 %s（≈ 12 × %.1f = %.0f）" % (slope, fmt(D("X12")), slope, 12 * slope))
        if D("C12") is not None and slope > 0:
            say("  盈亏点 n* = D(C12) ÷ 每个工具的 token = %.1f 个工具：服务器的工具比这少，direct 每个请求更省；比这多，codemode 更省（只算每个请求的固定税，不算模型用起来要多花的请求）" % (D("C12") / slope))
    if p and base is not None:
        say("  换成钱：每个请求多 D 个 token，命中缓存时按缓存读价（$%g/百万）多 D × $%g/百万，没命中（冷启动那一次）按新输入价（$%g/百万）多 D × $%g/百万。" % (
            p["cacheRead"], p["cacheRead"], p["input"], p["input"]))
    multi = {}
    for r in g:
        for s in r["sents"]:
            if s["requests"] != 1:
                multi[r["arm"]] = multi.get(r["arm"], 0) + 1
    if multi:
        say("  ⚠ 请求数不是 1 的句子：%s（数字照取第 1 个请求，不影响上表）" % ", ".join("%s %d 句" % kv for kv in sorted(multi.items())))


def switch_summary(recs, ctx):
    g = good_runs(recs, 2)
    if not g:
        say("\n== 第二部分没有拿到数据")
        return
    bc, bn = background(recs)
    say("\n== 汇总二：切换预设之后，第 2、3 句没命中缓存的 token（U = 总输入 − 命中；U ≥ %d 记“断崖”；每格是 x/n = 断崖次数/有效运行数，背景 = W0 的第 2、3 句合起来 %d/%d）" % (CLIFF, bc, bn))
    say("%-4s %-30s %-7s %-8s %-40s %-40s" % ("臂", "切换", "有效/计划", "第1句 T", "第 2 句 U（x/n，档，p）", "第 3 句 U（x/n，档，p）"))
    cells = {}
    for a in ARMS2:
        rs = good_runs(recs, 2, a)
        if ctx["planned"].get(a, 0) == 0:
            continue
        cols = []
        for si in (2, 3):
            us = [r["sents"][si - 1]["U"] for r in rs]
            x = sum(1 for u in us if u >= CLIFF)
            cls, pv = cell_class(x, len(us), bc, bn)
            cells[(a, si)] = (x, len(us), cls, pv)
            cols.append("%s（%d/%d，%s%s）" % ("/".join(str(u) for u in us) or "—", x, len(us), cls, "" if pv is None else "，p=%.4f" % pv))
        say("%-4s %-30s %-7s %-8s %-40s %-40s" % (a, ARMS[a]["doc"], "%d/%d" % (len(rs), ctx["planned"][a]), fmt(med([r["sents"][0]["T"] for r in rs])), cols[0], cols[1]))
    w0 = good_runs(recs, 2, "W0")
    if w0:
        t1 = med([r["sents"][0]["T"] for r in w0])
        say("  W0 第 1 句 T 的中位数 = %s（门 G4：≥ 3,500）；W0 背景 %d/%d（门 G5：≤ 7 / 16）" % (fmt(t1), bc, bn))
    pooled = [(cells[(a, 3)][0], cells[(a, 3)][1]) for a in ("PT", "PL", "PI", "PA") if (a, 3) in cells]
    if pooled and bn:
        px, pn = sum(x for x, _ in pooled), sum(n for _, n in pooled)
        pv = fisher_p(px, pn, bc, bn)
        say("  恢复（B8）：PT、PL、PI、PA 的第 3 句合起来断崖 %d/%d，和 W0 背景 %d/%d 比 Fisher p = %.4f → %s" % (
            px, pn, bc, bn, pv, "p ≥ 0.05：切换那一句之后，下一句和背景没有差别" if pv >= ALPHA else "p < 0.05：下一句和背景有差别（%s）" % ("更高" if px / pn > bc / bn else "更低")))
    if ("PR", 2) in cells and ("PR", 3) in cells:
        say("  往返（PR）：第 2 句（切到 lab-all）%s；第 3 句（切回 lab-base）%s" % (cells[("PR", 2)][2], cells[("PR", 3)][2]))
    p = ctx["prices"]
    if p:
        base_cost = med([r["sents"][1]["cost"] for r in good_runs(recs, 2, "W0") if r["sents"][1]["cost"] is not None])
        parts = []
        for a in ("PT", "PL", "PI", "PA", "PR"):
            c = med([r["sents"][1]["cost"] for r in good_runs(recs, 2, a) if r["sents"][1]["cost"] is not None])
            if c is not None and base_cost is not None:
                parts.append("%s %+.6f" % (a, c - base_cost))
        if parts:
            say("  一次切换多花多少（第 2 句花费的中位数 − W0 第 2 句的，美元）：" + "，".join(parts))
        say("  纯算术：一段 4 万 token 的会话中途整段按新输入价重付一次，比命中缓存多花 40000 × ($%g − $%g) ÷ 百万 = $%.4f" % (
            p["input"], p["cacheRead"], 40000 * (p["input"] - p["cacheRead"]) / 1e6))
    multi = {}
    for r in g:
        for s in r["sents"]:
            if s["requests"] != 1:
                multi[r["arm"]] = multi.get(r["arm"], 0) + 1
    if multi:
        say("  ⚠ 请求数不是 1 的句子：%s（数字照取第 1 个请求）" % ", ".join("%s %d 句" % kv for kv in sorted(multi.items())))


def use_summary(recs, ctx):
    g = good_runs(recs, 3)
    if not g:
        say("\n== 第三部分没有拿到数据")
        return
    say("\n== 汇总三：模型用不用得上 MCP 工具（答对 k/n；请求数、总没命中量 ΣU、花费是各次运行的中位数）")
    say("%-4s %-34s %-7s %-7s %-12s %-9s %-10s %-24s" % ("臂", "配置", "有效/计划", "答对", "请求数（范围）", "ΣU", "花费", "调工具（出现次数最多的顺序）"))
    for a in ARMS3:
        rs = good_runs(recs, 3, a)
        if ctx["planned"].get(a, 0) == 0:
            continue
        reqs = [r.get("requests", 0) for r in rs if not r.get("timeout")]
        done = [r for r in rs if not r.get("timeout")]       # 超时的运行没有请求数和花费，不进中位数（评分脚本同样）
        seqs = {}
        for r in rs:
            key = " → ".join(t.replace("mcp__%s__" % SERVER, "") for t in r.get("tools_seq", [])) or "无"
            seqs[key] = seqs.get(key, 0) + 1
        top = sorted(seqs.items(), key=lambda kv: (-kv[1], kv[0]))[0] if seqs else ("—", 0)
        say("%-4s %-34s %-7s %-7s %-12s %-9s %-10s %s ×%d" % (
            a, ARMS[a]["doc"], "%d/%d" % (len(rs), ctx["planned"][a]), "%d/%d" % (sum(1 for r in rs if r.get("correct")), len(rs)),
            "%s（%s–%s）" % (fmt(med(reqs)), min(reqs) if reqs else "—", max(reqs) if reqs else "—"), fmt(med([r.get("sumU") for r in done])),
            "—" if not ctx["prices"] else fmt(med([r.get("cost") for r in done]), "$%.6f"), top[0], top[1]))
    for a in ARMS3:
        rs = good_runs(recs, 3, a)
        extra = []
        te = sum(r.get("tool_errors", 0) for r in rs)
        if te:
            extra.append("工具报错共 %d 次" % te)
        to = sum(1 for r in rs if r.get("timeout"))
        if to:
            extra.append("超时 %d 次" % to)
        if a == "UC":
            extra.append("读了名字里带 codemode.md 的文件的运行 %d/%d 次" % (sum(1 for r in rs if r.get("read_codemode_md")), len(rs)))
        if a == "UD":
            extra.append("tool_search 调了不止一次的运行 %d/%d 次" % (sum(1 for r in rs if r.get("tools_seq", []).count("tool_search") > 1), len(rs)))
        if extra and rs:
            say("  %s：%s" % (a, "；".join(extra)))
    u0 = good_runs(recs, 3, "U0")
    if u0 and any(r.get("correct") for r in u0):
        say("  ⚠ 门 G6：U0（不接 MCP）有答对的运行：题目不靠 MCP 也能答对，或答案泄露了，第三部分不读")
    wrong = [r for a in ("UX", "UD", "UC") for r in good_runs(recs, 3, a) if not r.get("correct")]
    for r in wrong[:4]:
        say("  没答对的样例：%s #%d：请求 %d 个，工具 %s；回答 %r" % (r["arm"], r["rep"], r.get("requests", 0), " → ".join(r.get("tools_seq", [])) or "无", (r.get("answer") or "")[:100]))


def check_summary(recs, ctx):
    runs = [r for r in recs if r and r.get("kind") == "run"]
    bad = [r for r in runs if "err" in r]
    chk = [r for r in bad if r.get("check_failed")]
    infra = [r for r in bad if not r.get("check_failed")]
    say("\n== 核对")
    say("  计划 %d 次运行，完成 %d 次；失败 %d 次（核对没过 %d 次，不重跑；基础设施失败 %d 次，已各重试最多 %d 次）" % (ctx["total"], len(runs) - len(bad), len(bad), len(chk), len(infra), RETRIES))
    for r in bad[:6]:
        say("    失败样例：%s #%d：%s" % (r["arm"], r["rep"], r["err"]))
    retried = sum(1 for r in runs if r.get("tries", 1) > 1)
    if retried:
        say("  重跑过的运行 %d 次（基础设施失败后重跑）" % retried)
    ext_err = [r for r in good_runs(recs) if r.get("ext_error")]
    if ext_err:
        say("  ⚠ 这些运行的 stderr 里有 Extension error：%s" % ", ".join("%s#%d" % (r["arm"], r["rep"]) for r in ext_err[:8]))
        say("    第一条：%s" % ext_err[0]["stderr"][:200].replace("\n", " | "))
    return not chk


def gates_summary(recs, ctx):
    """门（路线图 11-14 那一行写的 G1–G6）。只在三部分都做的时候有意义；评分脚本按同一套规则再算一遍。"""
    runs = [r for r in recs if r and r.get("kind") == "run"]
    if not runs:
        return
    failed = [r for r in runs if "err" in r]
    need = {}
    for a, s in ARMS.items():
        need[a] = {1: 3, 2: (6 if a in ("W0", "PT") else 4), 3: (2 if a == "U0" else 4)}[s["part"]]
    short = [a for a in ARMS if ctx["planned"].get(a) and len(good_runs(recs, arm=a)) < need[a]]
    say("\n== 门（评分前先过的有效性门；评分脚本按同一套规则再算一遍）")
    say("  G1 有效：失败（重跑后仍失败）%d 次（≤ 8）；有效运行数不够的臂：%s" % (len(failed), ",".join(short) or "无"))
    chk = [r for r in failed if r.get("check_failed")]
    say("  G2 装上了：核对没过的运行 %d 次（这些运行记失败、不进统计）" % len(chk))
    if ctx["planned"].get("E0"):
        fl = {a: floor_of([r["sents"][0]["T"] for r in good_runs(recs, 1, a)]) for a in ARMS1}
        unstable = [a for a in ARMS1 if ctx["planned"].get(a) and good_runs(recs, 1, a) and not fl[a][2]]
        say("  G3 底稳定：底不稳的臂：%s%s" % (",".join(unstable) or "无", "（E0 不稳 → 第一部分整个不读）" if "E0" in unstable else ""))
    if ctx["planned"].get("W0"):
        w0 = good_runs(recs, 2, "W0")
        t1 = med([r["sents"][0]["T"] for r in w0])
        bc, bn = background(recs)
        say("  G4 参考文本到了：W0 第 1 句 T 的中位数 %s（≥ 3,500）→ %s" % (fmt(t1), "过" if (t1 is not None and t1 >= 3500) else "不过（第二部分只印数字、不读）"))
        say("  G5 背景：W0 背景 %d/%d（≤ 7 / 16）→ %s" % (bc, bn, "过" if bc <= 7 else "不过（第二部分只印数字、不读）"))
    if ctx["planned"].get("U0"):
        u0 = good_runs(recs, 3, "U0")
        ok = sum(1 for r in u0 if r.get("correct"))
        say("  G6 题目有效：U0 答对 %d/%d（应为 0）→ %s" % (ok, len(u0), "过" if (u0 and ok == 0) else "不过（第三部分只印数字、不读）"))


# ───────────────────────────── --mine-mcp：量一个 MCP 配置 ─────────────────────────────
def load_user_servers(path):
    """读一个 mcp.json（{"mcpServers": {...}}）-> 服务器字典。"""
    try:
        data = json.load(open(path, encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        sys.exit("读不了 %s：%s" % (path, e))
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict) or not servers:
        sys.exit("%s 里没有 mcpServers（格式：{\"mcpServers\": {\"名字\": {\"command\": …}}}）" % path)
    return servers


def sanitize(name):
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def mine_cfg(servers, exposure):
    """每个启用的服务器都改成同一种 exposure，去掉 toolExposure（让四种方式可比）。"""
    out = {}
    for name, cfg in servers.items():
        c = dict(cfg)
        if c.get("enabled") is False:
            out[name] = c
            continue
        c["exposure"] = exposure
        c.pop("toolExposure", None)
        out[name] = c
    return out


def mine_arms(servers):
    live = [n for n, c in servers.items() if c.get("enabled") is not False]
    xtools = "read," + ",".join("mcp__%s__*" % sanitize(n) for n in live)
    arms = {"E0": dict(part=0, reps=2, tools="read", ext=EXT3, mcp=None, doc="不配服务器", switches={}),
            "C": dict(part=0, reps=2, tools="read,codemode", ext=EXT3, mcp=("codemode", 0), doc="codemode", switches={}),
            "D": dict(part=0, reps=2, tools="read,tool_search", ext=EXT3, mcp=("deferred", 0), doc="deferred", switches={}),
            "X": dict(part=0, reps=2, tools=xtools, ext=EXT3, mcp=("direct", 0), doc="direct", switches={}),
            "H": dict(part=0, reps=2, tools="read", ext=EXT3, mcp=("hidden", 0), doc="hidden", switches={})}
    return arms, live


def verify_mine(arm, spec, ev, nsent, nreq):
    if not ev["sysmsgs"]:
        return ["pi 的 JSON 输出里没有系统提示事件（role=system 的 message_start）"]
    first = ev["sysmsgs"][0][1]
    if "toolsAdded" not in first:
        return ["系统提示事件里没有工具名单（toolsAdded），没法核对工具"]
    names, sec, kind = tool_names(first), mcp_section(first), (spec["mcp"] or (None,))[0]
    if kind == "direct":
        n = len([x for x in names if x.startswith("mcp__")])
        return [] if n >= 1 and "read" in names else ["direct 臂里一个 mcp__ 开头的工具都没有（服务器没连上、要登录，或 10 秒内没连上）"]
    want = {"codemode": ["codemode", "read"], "deferred": ["read", "tool_search"]}.get(kind, ["read"])
    probs = [] if names == want else ["工具是 %s，应当是 %s（服务器没连上，或还没连上就发了第 1 个请求）" % (names or "无", want)]
    if kind in ("codemode", "deferred") and not sec:
        probs.append("系统提示里没有 mcp_servers 段")
    if kind in (None, "hidden") and sec:
        probs.append("这个臂不该有 mcp_servers 段，但系统提示里有")
    return probs


def mine_summary(recs, ctx):
    g = good_runs(recs, 0)
    by = {a: [r for r in g if r["arm"] == a] for a in ctx["arms"]}
    if not by.get("E0") or not any(by[a] for a in ("C", "D", "X", "H")):
        say("\n== 没有拿到足够的数据")
        for r in recs:
            if r and "err" in r:
                say("   失败：%s #%d：%s" % (r["arm"], r["rep"], r["err"]))
        return
    fl = {a: floor_of([r["sents"][0]["T"] for r in by[a]]) for a in by}
    base = fl["E0"][0]
    say("\n== 你的 MCP 配置：四种露出方式各让每个请求多几个 token（总输入 T，每个臂 2 次；D = 该臂的底 − E0 的底）")
    say("%-4s %-10s %-12s %-10s" % ("臂", "有效/计划", "各次的 T", "D"))
    D = {}
    for a in ("E0", "C", "D", "X", "H"):
        if a not in by:
            continue
        Ts = [r["sents"][0]["T"] for r in by[a]]
        D[a] = None if (fl[a][0] is None) else fl[a][0] - base
        say("%-4s %-10s %-12s %-10s" % (a, "%d/%d" % (len(Ts), ctx["planned"][a]), "/".join(map(str, Ts)) or "—",
                                        "（对照）" if a == "E0" else ("—" if D[a] is None else "%+g" % D[a])))
    xs = by.get("X") or []
    ntools = xs[0]["sys"]["n_mcp"] if xs else 0
    if ntools:
        slope = D["X"] / ntools if D.get("X") is not None else None
        say("  direct 露出了 %d 个工具%s" % (ntools, "" if slope is None else "，平均每个工具 %.1f token" % slope))
        if slope and D.get("C") is not None and slope > 0:
            say("  盈亏点 n* = D(codemode) ÷ 每个工具的 token = %.1f 个工具：你的服务器合起来的工具比这少，direct 每个请求更省；比这多，codemode 更省（只算每个请求的固定税）" % (D["C"] / slope))
    else:
        say("  ⚠ direct 臂里没有 MCP 工具：服务器没连上、要登录（OAuth 在这个脚本里不支持），或 10 秒内没连上")
    p = ctx["prices"]
    if p:
        for a in ("C", "D", "X"):
            if D.get(a):
                say("  %s：每个请求多 %+g token → 命中缓存时多花 $%.7f，没命中时多花 $%.7f" % (a, D[a], D[a] * p["cacheRead"] / 1e6, D[a] * p["input"] / 1e6))
    if D.get("H") not in (None, 0):
        say("  ⚠ hidden 臂和 E0 不一样（D = %+g）：hidden 应当一个 token 都不占，查一下" % D["H"])


# ───────────────────────────── --demo-preset：会话中途 /preset 切换前后的工具名单 ─────────────────────────────
def run_demo(ctx, node, mini):
    spec = dict(part=0, reps=1, tools="read,mcp__%s__*" % SERVER, ext=EXT3 + ("v1:preset.ts",), mcp=("direct", 3), doc="demo", switches={})
    msgs = [MSG_OK, "/preset demo-plain", MSG_YES, "/preset demo-keep", MSG_DONE]
    d = make_agent_dir(ctx["root"], "agent-demo", ctx["src"], ctx["models"], ctx["name"], ctx["mid"], presets=DEMO_PRESETS, mcp=server_cfg(node, mini, "direct", 3))
    ctx["dirs"]["demo"] = d
    say("演示：一个 pi 进程，连问 3 句，中间用 /preset 切换两次（demo-plain 的 tools 只有 read；demo-keep 的 tools 是 read 加三个 MCP 工具的完整名字）。")
    say("启动时 --tools read,mcp__%s__*，所以第 1 句里模型能看到 read 加 3 个 MCP 工具。\n" % SERVER)
    res, err = run_pi(ctx, "demo", spec, msgs, ctx["timeout"] or 180)
    if err:
        say("失败：%s" % err)
        return
    out, errtxt, rc = res
    ev = parse_events(out)
    if len(ev["sents"]) < 3 or not ev["sysmsgs"]:
        say("失败：只完成了 %d/3 句；%s" % (len(ev["sents"]), (errtxt or out).strip()[-300:]))
        return
    active = set(tool_names(ev["sysmsgs"][0][1]))
    later = {k: m for k, m in ev["sysmsgs"][1:]}
    rows, prev = [], None
    for i in (1, 2, 3):
        if i in later:
            m = later[i]
            added = sorted(x.get("name") for x in m.get("toolsAdded") or [])
            removed = sorted(x.get("name") if isinstance(x, dict) else x for x in m.get("toolsRemoved") or [])
            active |= set(added)
            active -= set(removed)
        else:
            added, removed = [], []
        nums = sentence_numbers(ev["sents"][i - 1], ctx["prices"])
        rows.append((i, sorted(active), added, removed, nums))
        STATE["spent"] += (nums.get("cost") or 0)
    short = lambda xs: ", ".join(x.replace("mcp__%s__" % SERVER, "mcp:") for x in xs) or "（无）"   # noqa: E731
    titles = {1: "第 1 句（启动状态）", 2: "第 2 句（/preset demo-plain 之后）", 3: "第 3 句（/preset demo-keep 之后）"}
    for i, act, added, removed, nums in rows:
        say("%s：模型看到的工具 = %s" % (titles[i], short(act)))
        if added:
            say("    这一步新增：%s" % short(added))
        if removed:
            say("    这一步关掉：%s" % short(removed))
        if "T" in nums:
            say("    这句的第 1 个请求：总输入 T=%d，命中 %d，没命中 %d；请求数 %d" % (nums["T"], nums["C"], nums["U"], nums["requests"]))
    ok = (len(rows[0][1]) == 4 and rows[1][1] == ["read"] and len(rows[2][1]) == 4)
    say("\n读法：第 2 句里 MCP 的工具都不见了——预设里的 tools 是完整的白名单，没写的工具（包括 MCP 的工具、codemode、tool_search）一起被关掉；")
    say("      第 3 句里它们回来了——把完整的 mcp__ 名字写进预设的 tools，它们就留下来。%s" % ("" if ok else "\n⚠ 这次的结果和上面不一样（看每一句的工具名单），把整段输出贴给我。"))
    if prices_ok(ctx):
        say("本次花费 $%.4f" % STATE["spent"])


def prices_ok(ctx):
    return bool(ctx["prices"])


# ───────────────────────────── 主程序 ─────────────────────────────
def pi_bin():
    b = os.environ.get("PI_BIN") or shutil.which("pi")
    if not b or not (os.access(b, os.X_OK) or shutil.which(b)):
        sys.exit("找不到可以运行的 pi（%s）。可以先 export PI_BIN=/路径/pi" % (b or "PATH 里没有 pi"))
    return b


def build_jobs(part, reps, seed, arms_sel):
    """运行顺序：按“轮”分块（每轮每个臂各 1 次，次数少的臂后面的轮里不出现），每一轮内用固定种子打乱；这样各臂在时间上交错，不会有哪个臂集中在某一段时间里。"""
    arms = [a for a in ARMS if ARMS[a]["part"] == part and a in arms_sel]
    nrep = {a: (reps or ARMS[a]["reps"]) for a in arms}
    g = Lcg(seed)
    jobs = []
    for rep in range(1, max(nrep.values(), default=0) + 1):
        block = [(part, a, rep) for a in arms if nrep[a] >= rep]
        g.shuffle(block)
        jobs += block
    return jobs, nrep


def gate3(recs):
    """第三部分开不开始（事先写好）：第一部分的 MCP 臂（C、D、X）全部核对失败、或一个数据都没拿到 → 不开始（--force 除外）。-> 原因或 None"""
    if not good_runs(recs, 1):
        return "第一部分一个数据都没拿到"
    mcp = [r for r in recs if r and r.get("kind") == "run" and r.get("part") == 1 and r.get("arm") in MCP_ARMS1]
    if mcp and all(r.get("check_failed") for r in mcp):
        return "第一部分的 MCP 臂（C、D、X）全部核对失败"
    return None


def main():
    ap = argparse.ArgumentParser(description="预设与 MCP 实验室")
    ap.add_argument("--only", choices=("all", "size", "switch", "use"), default="all", help="只做哪一部分（默认三部分都做）")
    ap.add_argument("--arms", help="只测这几个臂，逗号分隔（默认全部）")
    ap.add_argument("--reps", type=int, help="每个臂跑几次（默认按设计：第一部分 4 次，第二部分 8/8/5/5/5/6 次，第三部分 3/5/5/5 次；别改，打分规则是按设计定的）")
    ap.add_argument("--max-cost", type=float, default=MAX_COST, help="花费上限（美元，默认 %.2f）：超过就不再发新请求；0 = 不设上限" % MAX_COST)
    ap.add_argument("--timeout", type=int, help="一次运行最多等几秒（默认：第一部分 120、第二部分 180、第三部分 300）")
    ap.add_argument("--model", help="provider/模型id；默认用 pi 的 settings.json 里的默认模型")
    ap.add_argument("--force", action="store_true", help="第一部分的 MCP 臂全部核对失败或没拿到数据时，也开始第三部分")
    ap.add_argument("--mine-mcp", nargs="?", const="__mini__", metavar="mcp.json", help="量一个 MCP 配置（不带文件名 = 包里的迷你服务器，%d 个工具）：四种露出方式各让每个请求多几个 token（10 次运行）" % MINE_TOOLS)
    ap.add_argument("--demo-preset", action="store_true", help="演示：会话中途 /preset 切换前后，模型看到的工具名单（1 个 pi 进程，3 个请求）")
    ap.add_argument("--dry-run", action="store_true", help="不调模型：核对包里的文件、迷你服务器和 node，打印设计指纹和要跑的运行")
    a = ap.parse_args()
    arms_sel = [x.strip() for x in a.arms.split(",") if x.strip()] if a.arms else list(ARMS)
    for x in arms_sel:
        if x not in ARMS:
            sys.exit("--arms 里只能有 %s" % "、".join(ARMS))
    if a.mine_mcp and a.demo_preset:
        sys.exit("--mine-mcp 和 --demo-preset 一次只能用一个")
    src, models, name, mid = pick_model(a.model)
    prices = model_prices(models, name, mid)
    pi = pi_bin()
    node_info, nerr = find_node()
    if nerr:
        sys.exit(nerr)
    node, node_ver = node_info
    probs = check_files()
    if probs:
        sys.exit("包里的文件和登记的不一致（这一课要的是原文件）：\n  " + "\n  ".join(probs))
    mini = os.path.join(HERE, "mcp", "mini_mcp.js")
    probs = preflight_mini(node, mini)
    if probs:
        sys.exit("迷你服务器自检没过（node %s）：\n  " % node_ver + "\n  ".join(probs))
    root = tempfile.mkdtemp(prefix="pm-lab-")
    work = os.path.join(root, "work")
    os.makedirs(work)

    def _stop_signal(signum, frame):      # 关终端（SIGHUP）或 kill（SIGTERM）也走和 Ctrl+C 一样的清理
        raise KeyboardInterrupt
    for _sig_name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, _sig_name):
            try:
                signal.signal(getattr(signal, _sig_name), _stop_signal)
            except (ValueError, OSError):
                pass
    recs = []
    ctx = {"pi": pi, "root": root, "work": work, "prices": prices, "cap": a.max_cost, "timeout": a.timeout, "arms": dict(ARMS), "dirs": {},
           "verify": verify, "src": src, "models": models, "name": name, "mid": mid}
    try:
        ver = subprocess.run([pi, "--version"], capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout.strip()
        say("pi %s   模型 %s / %s   设计指纹 %s" % (ver, name, mid, design_id()))
        say("node %s；迷你服务器 mini_mcp.js sha256 %s…（自检通过：3 / 12 / 30 个工具，两次 tools/call 对）；v1 preset.ts sha256 %s…；参考文本 %d 字符（sha256 %s…）" % (
            node_ver, MINI_SHA[:8], PRESET_SHA[:8], len(REF), sha(REF)[:12]))
        say("私有目录 %s（你的 models.json、auth.json 的副本、实验用的 mcp.json 和 presets.json；跑完删除）。" % root)
        if prices:
            say("单价（每百万 token）：新输入 $%g，缓存读 $%g，输出 $%g" % (prices["input"], prices["cacheRead"], prices["output"]))
        else:
            say("（models.json 里这个模型没写价格，下面不算花费，也不设花费上限）")
            ctx["cap"] = 0
        if a.demo_preset:
            ctx["dirs"] = {}
            if a.dry_run:
                say("== --dry-run：演示 --demo-preset 会跑 1 个 pi 进程，连问 3 句（%s、/preset demo-plain、%s、/preset demo-keep、%s）。" % (MSG_OK, MSG_YES, MSG_DONE))
                return
            run_demo(ctx, node, mini)
            return
        if a.mine_mcp:
            if a.mine_mcp == "__mini__":
                servers, label = server_cfg(node, mini, "codemode", MINE_TOOLS), "包里的迷你服务器（%d 个工具）" % MINE_TOOLS
            else:
                path = os.path.abspath(os.path.expanduser(a.mine_mcp))
                servers, label = load_user_servers(path), path
            arms, live = mine_arms(servers)
            ctx["arms"] = arms
            ctx["verify"] = verify_mine
            ctx["free"] = True
            ctx["planned"] = {x: 2 for x in arms}
            for arm in arms:
                mcp = None if arm == "E0" else mine_cfg(servers, {"C": "codemode", "D": "deferred", "X": "direct", "H": "hidden"}[arm])
                ctx["dirs"][arm] = make_agent_dir(root, "agent-" + arm, src, models, name, mid, mcp=mcp)
            jobs = []
            g = Lcg(SEEDS[1] + 7)
            for rep in (1, 2):
                block = [(0, arm, rep) for arm in arms]
                g.shuffle(block)
                jobs += block
            ctx["total"] = len(jobs)
            say("量 MCP 配置：%s；启用的服务器：%s。E0（不配服务器）、C（codemode）、D（deferred）、X（direct）、H（hidden）各 2 次，每次问 1 句。" % (label, ", ".join(live) or "无"))
            if a.dry_run:
                for part, arm, rep in jobs:
                    say("  %-3s #%d  --tools %s" % (arm, rep, arms[arm]["tools"]))
                return
            try:
                for job in jobs:
                    r = do_run(job, ctx)
                    if r:
                        recs.append(r)
            except KeyboardInterrupt:
                STOP["flag"] = True
                say("\n（被中断，下面是已有的结果）")
                for pr in list(PROCS):
                    kill_tree(pr)
            mine_summary(recs, ctx)
            if prices:
                say("本次花费 $%.4f" % STATE["spent"])
            return
        # ── 正式实验 ──
        parts_sel = [p for p in (1, 2, 3) if a.only in ("all", PART_NAME[p])]
        jobs_by, planned = {}, {}
        for p in parts_sel:
            jobs, nrep = build_jobs(p, a.reps, SEEDS[p], arms_sel)
            if jobs:
                jobs_by[p] = jobs
                planned.update(nrep)
        ctx["planned"] = planned
        ctx["free"] = False
        ctx["total"] = sum(len(j) for j in jobs_by.values())
        for arm in ARMS:
            spec = ARMS[arm]
            if arm not in planned:
                continue
            mcp = server_cfg(node, mini, spec["mcp"][0], spec["mcp"][1]) if spec["mcp"] else None
            ctx["dirs"][arm] = make_agent_dir(root, "agent-" + arm, src, models, name, mid, presets=LAB_PRESETS if spec["part"] == 2 else None, mcp=mcp)
        odd = ([] if not a.reps else ["每个臂 %d 次（默认按设计）" % a.reps]) + ([] if not a.arms else ["只测 %s" % ",".join(arms_sel)]) \
            + ([] if a.only == "all" else ["只做 %s" % a.only]) + ([] if not a.model else ["模型 %s/%s" % (name, mid)])
        if odd:
            say("（注意：这次用了非默认的设置：%s。评分规则是按默认设计定的，这份日志不一定能完整打分。）" % "；".join(odd))
        say("共 %d 次运行：%s；一个一个跑，不并行；花费上限 %s。第一行出来之前先等几秒。\n" % (
            ctx["total"], "、".join("第%s部分 %d 次" % ("一二三"[p - 1], len(j)) for p, j in jobs_by.items()), "$%g" % a.max_cost if ctx["cap"] else "无"))
        if a.dry_run:
            say("== --dry-run：不调模型。运行顺序（固定种子打乱）：")
            for p, jobs in jobs_by.items():
                for part, arm, rep in jobs:
                    sp = ARMS[arm]
                    say("  第%d部分 %-4s #%d  --tools %-20s 扩展 %s%s%s" % (part, arm, rep, sp["tools"] or "（预设决定）", ",".join(sp["ext"]) or "无",
                                                                       "  mcp %s×%d" % sp["mcp"] if sp["mcp"] else "", "  切换 %s" % sp["switches"] if sp.get("switches") else ""))
            ex = ARMS["C12"] if "C12" in ctx["planned"] else ARMS[next(iter(ctx["planned"]))]
            exts = [ext_path(k) if k.startswith("v1:") else k for k in ex["ext"]]
            say("\n  例：命令行：%s" % " ".join([os.path.basename(pi)] + [x if len(x) < 60 else x[:57] + "…" for x in pi_argv(ctx, ex, messages_for(ex, "00000000"), exts)]))
            say("  第二部分第 1 句 %d 字符（含 %d 字符的参考文本）；第三部分的问题：%s" % (len(first_message("00000000")), len(REF), QUESTION))
            say("\n  文件核对：通过（mini_mcp.js、preset.ts 的 sha256 和登记的一致）；迷你服务器自检：通过；node %s。" % node_ver)
            return
        skipped = set()
        printed = set()
        summaries = {1: size_summary, 2: switch_summary, 3: use_summary}
        try:
            for p in sorted(jobs_by):
                if p == 3 and 1 in jobs_by and not a.force:
                    why = gate3(recs)
                    if why:
                        say("\n第三部分没有开始：%s（上面有说明）。把屏幕上的内容贴给我；想强行开始，加 --force。" % why)
                        skipped.add(3)
                        continue
                for job in jobs_by[p]:
                    r = do_run(job, ctx)
                    if r:
                        recs.append(r)
                    if STOP["flag"]:
                        break
                summaries[p](recs, ctx)
                printed.add(p)
        except KeyboardInterrupt:
            STOP["flag"] = True
            say("\n（被中断，下面是已有的结果）")
            for pr in list(PROCS):
                kill_tree(pr)
        for p in jobs_by:
            if p not in printed and p not in skipped:
                summaries[p](recs, ctx)
        check_summary(recs, ctx)
        gates_summary(recs, ctx)
        if STATE["skipped"]:
            say("\n花费到了上限（$%.4f），剩下 %d 个没有跑；上面的汇总只含已经跑的。" % (STATE["spent"], STATE["skipped"]))
        if prices:
            say("本次花费 $%.4f" % STATE["spent"])
        say("\n== 数据（每次运行一行，贴给我的时候别删）")
        for r in recs:
            say("数据 " + json.dumps(r, ensure_ascii=False, sort_keys=True))
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
