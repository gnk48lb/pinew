#!/usr/bin/env python3
"""pi_events.py —— 把 `pi -p "任务" --mode json` 的事件流读成一张表：每一轮（= 每次模型请求）发生了什么。

用法：  pi -p "你的任务" --mode json | python3 pi_events.py
        python3 pi_events.py 事件流.jsonl            （先 `pi -p … --mode json > 事件流.jsonl` 存下来也行）
列的意思和 mini_agent.py 对齐：新输入 = 这次请求里没命中缓存的输入 token；缓存读 = 命中缓存的；输出 = 模型生成的（含思考）。
"""
import json, sys


def short(v, n=60):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n - 1] + "…"


def result_text(ev):
    """一个工具结果：状态 + 模型实际看到的字符数（这段文字会被追加进历史，并且在之后每一轮都被重新发送）。"""
    r = ev.get("result") or {}
    sc = r.get("structuredContent")
    text = "".join(c.get("text", "") for c in r.get("content", []) if isinstance(c, dict))
    tag = "出错" if ev.get("isError") else ("退出码 %s" % sc["exit_code"] if isinstance(sc, dict) and "exit_code" in sc else "ok")
    tr = (r.get("details") or {}).get("truncation") or {}
    if tr.get("truncated"):
        tag += "，pi 截断：只给模型 %s/%s 行" % (tr.get("outputLines"), tr.get("totalLines"))
    return "%s，模型看到 %d 字符：%s" % (tag, len(text), short(text, 24))


def main():
    src = open(sys.argv[1], encoding="utf-8") if len(sys.argv) > 1 else sys.stdin
    evs = []
    for line in src:
        line = line.strip()
        if line.startswith("{"):
            try:
                evs.append(json.loads(line))
            except ValueError:
                pass
    if not evs:
        sys.exit("没有读到 JSON 事件。用法：pi -p \"任务\" --mode json | python3 pi_events.py")
    results = {e["toolCallId"]: e for e in evs if e.get("type") == "tool_execution_end"}
    sysmsg = next((e["message"] for e in evs if e.get("type") == "message_start" and e["message"].get("role") == "system"), None)
    if sysmsg:
        print("== pi 每次请求开头那一大段固定内容（系统提示 + 工具说明书），按部分分：")
        parts = [(k, len(v)) for k, v in (sysmsg.get("sections") or {}).items()]
        tools = sysmsg.get("toolsAdded") or []
        parts.append(("工具说明书(%d个)" % len(tools), sum(len(json.dumps(t, ensure_ascii=False)) for t in tools)))
        for k, n in parts:
            print("   %6d 字符  %s" % (n, k))
        total = sum(n for _, n in parts)
        print("   ------\n   %6d 字符  合计（粗估 %d token；准确的数看下表第 1 轮：新输入 + 缓存读）\n" % (total, total // 4))
    print("== 每一轮（= 每次模型请求）")
    print("轮  新输入  缓存读    输出  思考  结束原因    这一轮做了什么")
    tot = {"input": 0, "cacheRead": 0, "output": 0, "cost": 0.0}
    n = 0
    for e in evs:
        if e.get("type") != "turn_end":
            continue
        n += 1
        m = e["message"]
        u = m.get("usage") or {}
        for k in ("input", "cacheRead", "output"):
            tot[k] += u.get(k, 0)
        tot["cost"] += (u.get("cost") or {}).get("total", 0)
        acts = []
        for b in m.get("content", []):
            if b.get("type") == "toolCall":
                r = results.get(b["id"])
                acts.append("%s %s → %s" % (b["name"], short(b.get("arguments"), 50), result_text(r) if r else "?"))
            elif b.get("type") == "text" and b.get("text", "").strip():
                acts.append("说：" + short(b["text"], 60))
        print("%-3d %6d %7d %7d %5d  %-10s  %s" % (n, u.get("input", 0), u.get("cacheRead", 0), u.get("output", 0), u.get("reasoning", 0),
                                                  m.get("stopReason", "?"), " | ".join(acts) or "-"))
    seen = tot["input"] + tot["cacheRead"]
    print("\n合计：%d 次请求，新输入 %d，缓存读 %d，输出 %d；缓存命中 %.0f%%%s" % (
        n, tot["input"], tot["cacheRead"], tot["output"], 100.0 * tot["cacheRead"] / max(1, seen),
        ("；花费 $%.5f" % tot["cost"]) if tot["cost"] else "（models.json 里没写价格，所以没有花费）"))


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:             # `… | head` 提前关掉了管道：正常，不报错
        pass
