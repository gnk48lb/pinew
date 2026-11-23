#!/usr/bin/env python3
"""mini_agent.py —— 一个 agent 到底是什么：一个 while 循环。只用标准库，约 120 行。

用法：  python3 mini_agent.py "你的任务" [--effort low] [--cap 8000] [--max-turns 20] [--save 记录.json]
它读你的 ~/.pi/agent/models.json 和 settings.json，所以用的是和 pi 同一个网关、同一个模型。
故意什么都不加：没有安全带、没有预算护栏、没有 AGENTS.md、没有压缩。看清循环本身。
注意：它会用你的身份真的执行 bash 命令。只在练习文件夹里用。
"""
import argparse, json, os, re, subprocess, sys, urllib.error, urllib.request

SYSTEM = "You are a coding agent. Use the tools to do the user's task, then reply with a short report."

# ── 1. 工具 = 给模型看的“说明书”（JSON schema）+ 一个真正干活的 Python 函数 ──────────────────────────
def spec(name, desc, **props):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "required": list(props),
                "properties": {
                    k: {"type": "string", "description": v} 
                    for k, v in props.items()
                }
            }
        }
    }
TOOLS = [spec("bash", "Run a bash command in the current directory. Returns stdout, stderr and the exit code.", command="the command"),
         spec("read", "Read a text file.", path="file path"),
         spec("write", "Create or overwrite a file.", path="file path", content="the whole new content")]

def run_tool(name, args, cap):
    try:                                    # 错误也只是“结果”：交还给模型，让它自己改正，而不是让程序崩掉
        if name == "bash":
            p = subprocess.run(["bash", "-c", args["command"]], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
            out = p.stdout + p.stderr + "\n[exit code %d]" % p.returncode
        elif name == "read":
            out = open(args["path"], encoding="utf-8", errors="replace").read()
        elif name == "write":
            os.makedirs(os.path.dirname(os.path.abspath(args["path"])), exist_ok=True)
            open(args["path"], "w", encoding="utf-8").write(args["content"])
            out = "wrote %d characters to %s" % (len(args["content"]), args["path"])
        else:
            out = "Error: unknown tool " + name
    except Exception as e:
        out = "Error: %s: %s" % (type(e).__name__, e)
    return out if not cap or len(out) <= cap else out[:cap] + "\n[... cut: %d more characters]" % (len(out) - cap)

# ── 2. 读 pi 的配置：网关地址、模型、密钥（规则和 pi 一致：--api-key > auth.json > models.json 的 apiKey）──────────
def resolve(v):                         # models.json 里的 $NAME / ${NAME} = 读环境变量；!命令 形式这里不支持
    if v.startswith("!"):
        sys.exit("models.json 里有 !命令 形式的值，mini_agent 不执行它。请先 export MINI_API_KEY=你的密钥")
    return re.sub(r"\$\{?(\w+)\}?", lambda m: os.environ.get(m.group(1), ""), v)

def load_config(model_arg):
    d = os.environ.get("PI_CODING_AGENT_DIR") or os.path.expanduser("~/.pi/agent")
    load = lambda f: json.load(open(os.path.join(d, f), encoding="utf-8-sig")) if os.path.exists(os.path.join(d, f)) else {}
    providers, settings = load("models.json").get("providers", {}), load("settings.json")
    name, _, mid = (model_arg or "").partition("/") if model_arg else (settings.get("defaultProvider"), "", settings.get("defaultModel"))
    name = name or next(iter(providers), None)
    p = providers.get(name) or {}
    mid = mid or (p.get("models") or [{}])[0].get("id")
    if not (p.get("baseUrl") and mid):
        sys.exit("在 %s/models.json 里找不到可用的 provider 或模型。可以用 --model provider/模型id 指定" % d)
    if p.get("api", "openai-completions") != "openai-completions":
        sys.exit("mini_agent 只会说 openai-completions 这一种接口；这个 provider 用的是 %s" % p["api"])
    auth = load("auth.json").get(name)
    key = os.environ.get("MINI_API_KEY") or (auth.get("key") if isinstance(auth, dict) else auth) or resolve(p.get("apiKey", ""))
    if not key:
        sys.exit("读不到密钥。请先 export MINI_API_KEY=你的密钥")
    return {"url": p["baseUrl"].rstrip("/") + "/chat/completions", "model": mid, "key": key,
            "headers": {k: resolve(str(v)) for k, v in (p.get("headers") or {}).items()}}

def chat(cfg, messages, effort):        # ── 3. 一次请求 = 把“到目前为止的全部消息”整个发过去 ───────────────
    body = {"model": cfg["model"], "messages": messages, "tools": TOOLS}
    if effort != "none":
        body["reasoning_effort"] = effort
    headers = {"Content-Type": "application/json", "User-Agent": "mini-agent", "Authorization": "Bearer " + cfg["key"], **cfg["headers"]}
    try:
        reply = json.load(urllib.request.urlopen(urllib.request.Request(cfg["url"], json.dumps(body).encode(), headers), timeout=300))
    except urllib.error.HTTPError as e:
        sys.exit("HTTP %d：%s" % (e.code, e.read()[:600].decode("utf-8", "replace")))
    except OSError as e:
        sys.exit("连不上网关 %s：%s" % (cfg["url"], e))
    return reply if reply.get("choices") else sys.exit("网关返回了没有 choices 的内容：%s" % json.dumps(reply)[:600])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--model", help="provider/模型id；默认用 pi 的 settings.json 里的默认模型")
    ap.add_argument("--effort", default="low", help="reasoning_effort：low/medium/high；none = 不发这个字段")
    ap.add_argument("--cap", type=int, default=8000, help="工具结果最多保留多少字符；0 = 不截断")
    ap.add_argument("--max-turns", type=int, default=20)
    ap.add_argument("--save", help="把完整对话写进这个 json 文件")
    a = ap.parse_args()
    cfg = load_config(a.model)
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": a.task}]
    print("模型 %s   网关 %s" % (cfg["model"], cfg["url"]))
    print("轮  新输入  缓存读    输出  这一轮做了什么")
    totals = [0, 0, 0]
    for turn in range(1, a.max_turns + 1):          # ── 4. 循环：问模型 → 执行工具 → 把结果追加回去 → 再问 ───────
        reply = chat(cfg, messages, a.effort)
        m, u = reply["choices"][0]["message"], reply.get("usage") or {}
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or u.get("prompt_cache_hit_tokens") or 0
        fresh, out = u.get("prompt_tokens", 0) - cached, u.get("completion_tokens", 0)
        totals = [totals[0] + fresh, totals[1] + cached, totals[2] + out]
        calls = m.get("tool_calls") or []
        keep = {"role": "assistant", "content": m.get("content")}       # 模型的回复原样追加：历史只增不改（这就是缓存能命中的原因）
        for k in ("tool_calls", "reasoning_content"):
            if m.get(k):
                keep[k] = m[k]
        messages.append(keep)
        doing = ", ".join("%s(%s)" % (c["function"]["name"], c["function"]["arguments"][:50]) for c in calls) or "给出最终回答"
        print("%-3d %6d %7d %7d  %s" % (turn, fresh, cached, out, doing))
        if not calls:
            print("\n" + (m.get("content") or "(空回答)"))
            break
        for c in calls:                                  # 每个工具调用都要有一条对应的 tool 消息，用 tool_call_id 对上号
            try:
                args = json.loads(c["function"]["arguments"] or "{}")
                result = run_tool(c["function"]["name"], args, a.cap)
            except ValueError as e:
                result = "Error: 参数不是合法 JSON: %s" % e
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": result})
    else:
        print("\n（到了 --max-turns 上限，停下）")
    print("\n合计：%d 次请求，新输入 %d，缓存读 %d，输出 %d；缓存命中 %.0f%%" % (turn, *totals, 100.0 * totals[1] / max(1, totals[0] + totals[1])))
    if a.save:
        json.dump({"messages": messages, "tools": TOOLS}, open(a.save, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("完整对话已写入", a.save)

if __name__ == "__main__":
    main()
