#!/usr/bin/env python3
"""show_messages.py —— 把 mini_agent.py --save 存下的对话一条一条打印出来：这就是 agent 的全部“记忆”。

用法：  python3 show_messages.py run1.json
每次请求发给模型的，就是这里从第 1 条到最新一条的全部内容（再加上工具说明书）。
"""
import json, sys


def short(s, n=64):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[:n - 1] + "…"


def main():
    if len(sys.argv) < 2:
        sys.exit("用法：python3 show_messages.py 记录.json   （记录文件由 mini_agent.py --save 记录.json 生成）")
    try:
        d = json.load(open(sys.argv[1], encoding="utf-8"))
        msgs = d["messages"]
    except (OSError, ValueError, KeyError) as e:
        sys.exit("读不了 %s：%s" % (sys.argv[1], e))
    print("条  角色        字符  内容")
    total = 0
    for i, m in enumerate(msgs, 1):
        text = m.get("content") or ""
        for c in m.get("tool_calls") or []:
            text += " [调用 %s %s]" % (c["function"]["name"], c["function"]["arguments"])
        n = len(json.dumps(m, ensure_ascii=False))
        total += n
        think = "  （另有 %d 字符的思考内容）" % len(m["reasoning_content"]) if m.get("reasoning_content") else ""
        print("%-3d %-10s %6d  %s%s" % (i, m.get("role", "?"), n, short(text), think))
    tools = len(json.dumps(d.get("tools") or [], ensure_ascii=False))
    print("\n共 %d 条消息，约 %d 字符；每次请求另外还带着 %d 字符的工具说明书。" % (len(msgs), total, tools))
    print("第 N 次请求发出的 = 前 N 轮累积的全部消息：这就是“每次都带着全部历史”。")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        pass
