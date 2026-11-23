"""lab8_model.py —— 第 8 课实验的"假模型"（只用来在容器里测脚本，不代表任何真实模型的行为）。

它读请求里的消息，按内容决定做什么：
  - 主会话第 1 句（R：读文件；D：委派）→ 调工具 → 写一份摘要（摘要里带哪几个"针"，由这次运行的标记行的哈希决定，所以每次不一样）
  - 子进程（第一句话以 CHILD-TASK: 开头）→ 读文件 → 回摘要，或者回 A=…; B=…; C=…; D=…
  - "OK" 句、填充句 → 回 OK
  - 回忆题 → 只从上下文里找值；找不到写 UNKNOWN（有时编一个错的、有时格式不对、有时先试着调一次工具）
  - 补救句 → 有 UNKNOWN 就再查一次（D：再委派；其余：再读），查不到就重复上一行
  - 没带任何工具的请求（压缩的摘要请求）→ 只把"转写稿里看得见的"事实写进摘要
所有"随机"都来自内容的哈希，所以同一份输入永远得到同一个输出。
"""
import hashlib
import os
import random
import re

FLAKY = float(os.environ.get("LAB8_MOCK_FLAKY", "0") or 0)   # 只用来测试重跑：这个概率下，第 1 句不调工具

# 从一段文字里找值：日志原文和摘要里的写法用同一套正则
RE_A = re.compile(r"build (\d{4}) failed the health gate")
RE_B = re.compile(r"ERROR\s+payments-gw\s+req=([0-9a-f]{6})")
RE_D = re.compile(r"rollback started to commit=([0-9a-f]{7})")
RE_THROTTLE = re.compile(r"tenant=(\S+) retry_after=(\d+)s")


def hnum(*parts):
    return int(hashlib.sha1("\x1f".join(parts).encode("utf-8", "replace")).hexdigest()[:8], 16)


def blocks_of(messages):
    flat = []
    for m in messages:
        c = m.get("content")
        role = m.get("role")
        if isinstance(c, str):
            flat.append({"role": role, "kind": "text", "text": c})
            continue
        for b in c or []:
            t = b.get("type")
            if t == "text":
                flat.append({"role": role, "kind": "text", "text": b.get("text", "")})
            elif t == "tool_use":
                flat.append({"role": "assistant", "kind": "tool_use", "name": b.get("name"), "input": b.get("input") or {}})
            elif t == "tool_result":
                cc = b.get("content")
                if isinstance(cc, list):
                    cc = "".join(x.get("text", "") for x in cc if isinstance(x, dict))
                flat.append({"role": "user", "kind": "tool_result", "text": cc or ""})
    return flat


def find_values(blob, tenant):
    v = {}
    m = RE_A.search(blob)
    if m:
        v["A"] = m.group(1)
    m = RE_B.search(blob)
    if m:
        v["B"] = m.group(1)
    for m in RE_THROTTLE.finditer(blob):
        if m.group(1) == tenant:
            v["C"] = m.group(2)
            break
    m = RE_D.search(blob)
    if m:
        v["D"] = m.group(1)
    return v


def summary_lines(blob, seed):
    """主会话/子进程读完日志后写的摘要：A 一定写；B、D、C 由 seed 决定写不写。"""
    v = find_values(blob, None)
    L = ["Root cause: ledger-sync build %s failed the health gate on 3 of 5 pods (FATAL deploy-ctl)." % v.get("A", "????")]
    if seed % 4 == 0 and "B" in v:
        L.append("First failure: ERROR payments-gw req=%s upstream timeout, circuit OPEN." % v["B"])
    else:
        L.append("First failure: payments-gw upstream timeouts, circuit OPEN.")
    L.append("Impact: edge-gw-2 throttled several tenants; billing-api saw 502s.")
    if seed % 3 != 0 and "D" in v:
        L.append("Action: rollback started to commit=%s (previous stable build)." % v["D"])
    else:
        L.append("Action: a rollback to the previous stable build was started.")
    if seed % 7 == 0:
        late = [(m.group(1), m.group(2)) for m in RE_THROTTLE.finditer(blob)][-4:]
        if late:
            L.append("Late throttling: " + "; ".join("tenant=%s retry_after=%ss" % p for p in late) + ".")
    return L


def asked_tenant(text):
    m = re.search(r"tenant (\S+?)\. D:", text or "")
    return m.group(1) if m else None


def child_recover_task(tenant):
    return ("CHILD-TASK: In ./incident.log find the values and reply on one line, nothing else: A=<value>; B=<value>; C=<value>; D=<value>. "
            "A: the build number in the FATAL deploy-ctl line. B: the req id in the first ERROR line. "
            "C: the retry_after seconds given to tenant %s. D: the commit the rollback was started to." % tenant)


def plan(messages, tool_defs, map_args):
    """-> (text, call|None, step|None)；call = {"name", "args"}"""
    flat = blocks_of(messages)
    names = [n for n, _ in tool_defs]

    def call_of(want, args):
        real, props = want, {}
        for n, p in tool_defs:
            if n.lower() == want.lower():
                real, props = n, p
                break
        return {"name": real, "args": map_args(props, args)}

    # ---- 没带工具：压缩的摘要请求
    if not names:
        blob = "\n".join(x.get("text", "") for x in flat)
        found = []
        v = find_values(blob, None)
        if "A" in v:
            found.append("build %s failed the health gate (FATAL deploy-ctl)" % v["A"])
        if "B" in v:
            found.append("ERROR payments-gw req=%s was the first error" % v["B"])
        if "D" in v:
            found.append("rollback started to commit=%s" % v["D"])
        thr = [(m.group(1), m.group(2)) for m in RE_THROTTLE.finditer(blob)]
        if thr:
            found.append("; ".join("tenant=%s retry_after=%ss" % p for p in thr[-4:]))
        out = ["## Goal", "Summarise ./incident.log for the user.", "", "## Constraints & Preferences", "- (none)", "", "## Progress", "### Done",
               "- [x] Read incident.log and wrote a short summary.", "", "### In Progress", "- [ ] Answer follow-up questions.", "", "### Blocked", "- (none)", "",
               "## Key Decisions", "- **Summary first**: keep it short.", "", "## Next Steps", "1. Answer follow-up questions.", "", "## Critical Context"]
        out += ["- " + x for x in found] or ["- (none)"]
        return "\n".join(out), None, None

    first_user = next((x["text"] for x in flat if x["kind"] == "text" and x["role"] == "user"), "")
    cur_i = max((i for i, x in enumerate(flat) if x["kind"] == "text" and x["role"] == "user"), default=0)
    cur = flat[cur_i]["text"] if flat else ""
    before = "\n".join(x.get("text", "") for x in flat[:cur_i])
    after = flat[cur_i + 1:]
    got = [x for x in after if x["kind"] == "tool_result"]
    mk = re.search(r"# incident export run=([0-9a-f]{8})", "\n".join(x.get("text", "") for x in flat))
    seed = int(mk.group(1), 16) if mk else hnum(before[:4000])

    # ---- 子进程
    if first_user.startswith("CHILD-TASK:"):
        if not got:
            return None, call_of("read", {"path": "incident.log"}), None
        txt = got[-1]["text"]
        if "A=<value>" in first_user:
            ten = asked_tenant(first_user)
            v = find_values(txt, ten)
            return "A=%s; B=%s; C=%s; D=%s" % tuple(v.get(k, "UNKNOWN") for k in "ABCD"), None, None
        return "\n".join(summary_lines(txt, seed)), None, None

    # ---- 主会话
    if cur.startswith("Read ./incident.log with the read tool"):
        if not got:
            if FLAKY and random.random() < FLAKY:
                return "I could not open the file.", None, None
            return None, call_of("read", {"path": "incident.log"}), None
        return "\n".join(summary_lines(got[-1]["text"], seed)), None, None
    if cur.startswith("You cannot read files yourself"):
        if not got:
            if FLAKY and random.random() < FLAKY:
                return "I will not delegate.", None, None
            return None, call_of("delegate", {"task": "CHILD-TASK: Read ./incident.log and write a short incident summary of at most 6 lines: what failed first, what the root cause was, and what was done about it."}), None
        return got[-1]["text"], None, None
    if cur.startswith("Use the delegate tool with this task"):
        if not got:
            return None, call_of("delegate", {"task": "CHILD-TASK: Read ./incident.log and reply with only its first line."}), None
        return "done", None, None
    if cur.startswith("Reply with only the word OK") or cur.startswith("Note "):
        return "OK", None, None
    if cur.startswith("Answer from memory only"):
        ten = asked_tenant(cur)
        v = find_values(before, ten)
        miss = [k for k in "ABCD" if k not in v]
        if miss and not got and hnum(before[-3000:], "try") % 3 == 0:     # 有的运行会先试着调一次工具（会被扩展拦下）
            if "read" in [n.lower() for n in names]:
                return None, call_of("read", {"path": "incident.log"}), None
            return None, call_of("delegate", {"task": "CHILD-TASK: look it up"}), None
        out = {}
        for k in "ABCD":
            if k in v:
                out[k] = v[k]
                if hnum(before[-2000:], k, "typo") % 23 == 0:               # 偶尔抄错
                    out[k] = {"A": "1111", "B": "aaaaaa", "C": "99", "D": "aaaaaaa"}[k]
            elif hnum(before[-2000:], k, "guess") % 6 == 0:                  # 偶尔编一个
                out[k] = {"A": "2222", "B": "bbbbbb", "C": "98", "D": "bbbbbbb"}[k]
            else:
                out[k] = "UNKNOWN"
        if hnum(before[-3000:], "fmt") % 17 == 0:
            return "I believe the values are as in the log.", None, None
        return "Here you go.\nA=%s; B=%s; C=%s; D=%s" % tuple(out[k] for k in "ABCD"), None, None
    if cur.startswith("If any item in your last line was UNKNOWN"):
        prev = [x["text"] for x in flat[:cur_i] if x["kind"] == "text" and x["role"] == "assistant" and "A=" in x["text"]]
        last_line = next((ln for ln in reversed(prev[-1].splitlines()) if "A=" in ln), "") if prev else ""
        if "UNKNOWN" not in last_line:
            return last_line or "A=UNKNOWN; B=UNKNOWN; C=UNKNOWN; D=UNKNOWN", None, None
        ten = None
        for x in flat[:cur_i]:
            if x["kind"] == "text" and x["role"] == "user" and "Answer from memory only" in x["text"]:
                ten = asked_tenant(x["text"])
        lazy = hnum(before[-3000:], "lazy") % 6 == 0                          # 有的运行不去查，只重复上一行
        if not got:
            if lazy:
                return last_line, None, None
            if "delegate" in names:
                return None, call_of("delegate", {"task": child_recover_task(ten)}), None
            return None, call_of("read", {"path": "incident.log"}), None
        txt = got[-1]["text"]
        if "delegate" in names:
            ln = next((x for x in reversed(txt.splitlines()) if "A=" in x), "")
            return ln or last_line, None, None
        v = find_values(txt, ten)
        old = {}
        for part in last_line.split(";"):
            if "=" in part:
                kk, vv = part.split("=", 1)
                old[kk.strip()] = vv.strip()
        return "A=%s; B=%s; C=%s; D=%s" % tuple(v.get(k) or old.get(k, "UNKNOWN") for k in "ABCD"), None, None
    return "OK", None, None
