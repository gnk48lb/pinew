#!/usr/bin/env bash
# see-sys.sh —— 看 pi 实际发出去的系统提示：7 段原文 + 工具定义 + 这一个请求的 token
#
#   bash ~/pinew/tools/real-gateway/see-sys.sh              用你现在的真实配置（扩展、工具、AGENTS.md、技能、模板全都在）
#   bash ~/pinew/tools/real-gateway/see-sys.sh --minimal    和第 4 课实验一样的最小配置（只 read 工具，不读 AGENTS.md，不加载扩展）
#   bash ~/pinew/tools/real-gateway/see-sys.sh --no-agents  只去掉 AGENTS.md，其余照旧
#
# （原先放在 Windows 侧目录里；现在随仓库）
# 用法路径：~/pinew/tools/real-gateway/see-sys.sh

set -uo pipefail

MINIMAL=0
NOAGENTS=0
for a in "$@"; do
  case "$a" in
    --minimal)   MINIMAL=1 ;;
    --no-agents) NOAGENTS=1 ;;
    *) echo "不认识的参数: $a（可用：--minimal / --no-agents）" >&2; exit 2 ;;
  esac
done

# 找 pi
PI=""
if command -v pi >/dev/null 2>&1; then
  PI="pi"
else
  for c in "$HOME/.local/bin/pi" "$HOME/node_modules/.bin/pi" "/usr/local/bin/pi" "$HOME/.bun/bin/pi"; do
    [ -x "$c" ] && { PI="$c"; break; }
  done
fi
if [ -z "$PI" ]; then
  echo "找不到 pi。先确认 'pi --version' 能跑。" >&2
  exit 1
fi

OUT=$(mktemp /tmp/pi-sys-XXXXXX.jsonl)
ERR=$(mktemp /tmp/pi-sys-XXXXXX.err)
trap 'rm -f "$OUT" "$ERR"' EXIT

PI_ARGS=(--mode json --no-session)
if [ "$MINIMAL" = 1 ]; then
  PI_ARGS+=(--no-extensions -ns -np --thinking low --tools read -nc)
elif [ "$NOAGENTS" = 1 ]; then
  PI_ARGS+=(-nc)
fi

echo "跑：$PI -p \"Reply with the single word: OK\" ${PI_ARGS[*]}" >&2
"$PI" -p "Reply with the single word: OK" "${PI_ARGS[@]}" > "$OUT" 2>"$ERR"

python3 - "$OUT" <<'PY'
import json, sys

path = sys.argv[1]
sysmsg = None
turns = []
with open(path, encoding="utf-8", errors="replace") as f:
    for line in f:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t = e.get("type")
        m = e.get("message") or {}
        if t == "message_start" and m.get("role") == "system" and sysmsg is None:
            sysmsg = m
        elif t == "turn_end":
            turns.append(m)

if not sysmsg:
    print("!! 没找到 role=system 的 message_start 事件。原始输出前 3000 字符：")
    print(open(path, encoding="utf-8", errors="replace").read()[:3000])
    sys.exit(1)

sec = sysmsg.get("sections") or {}
print("=" * 72)
print("pi 实际发出去的系统提示 —— %d 段，顺序就是请求里的顺序" % len(sec))
print("=" * 72)
total = 0
for k, v in sec.items():
    total += len(v)
    print("")
    print("-" * 72)
    print("### %s   (%d 字符)" % (k, len(v)))
    print("-" * 72)
    print(v)
print("")
print("=" * 72)
print("7 段合计 %d 字符" % total)

tools = sysmsg.get("toolsAdded")
tools_chars = len(json.dumps(tools, ensure_ascii=False)) if tools is not None else 0
if tools is not None:
    print("")
    print("toolsAdded —— 工具的完整定义，不在系统提示里，是请求里另外一块：%d 字符" % tools_chars)
    for t in tools:
        d = t.get("description") or ""
        one = " ".join(d.split())
        print("   - %-8s %5d 字符  %s" % (t.get("name"), len(json.dumps(t, ensure_ascii=False)), one[:70]))

inp = cached = out = 0
for m in turns:
    u = m.get("usage") or {}
    inp += (u.get("input", 0) or 0) + (u.get("cacheRead", 0) or 0) + (u.get("cacheWrite", 0) or 0)
    cached += u.get("cacheRead", 0) or 0
    out += u.get("output", 0) or 0

print("")
if turns:
    print("网关报的总输入 %d token（其中缓存读 %d），输出 %d，共 %d 个请求" % (inp, cached, out, len(turns)))
    syschars = total + tools_chars
    if inp:
        print("系统提示 %d + 工具定义 %d = %d 字符，对应 %d token，约 %.2f 字符/token" % (total, tools_chars, syschars, inp, syschars / inp))
else:
    print("（没抓到 turn_end，拿不到 token 数）")
PY

if [ -s "$ERR" ]; then
  echo ""
  echo "---- pi 的 stderr ----"
  head -20 "$ERR"
fi
