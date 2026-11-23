# dev/ — 沙箱里的开发环境（无网络、不花钱）

目标：在云沙箱里装真 pi（1.5.0），接一个**模拟网关**，能验证：包加载、扩展行为、安全带、预设、前缀逐轮追加、安装器场景。**模拟网关不是用户的真网关**：它按“前缀完全相同才命中”模拟缓存，没有随机整段重付、没有冷热两态；token 数按 4 字符≈1 token 估算。真实 token/命中/花费只能在用户的 WSL 里量（`tools/real-gateway/`，花一个请求的钱）。

## 1. 一次性搭建

```bash
mkdir -p ~/rt && cd ~/rt && npm i @earendil-works/pi-coding-agent@1.5.0     # ≈160 MB；钉住实测过的版本
cd <本仓库> && cp dev/env.sh.example dev/env.sh && . dev/env.sh              # RT、PATH、PI_BIN、隔离的 PI_CODING_AGENT_DIR
mkdir -p "$PI_CODING_AGENT_DIR" && cp dev/models.mock.json "$PI_CODING_AGENT_DIR/models.json"
echo '{"defaultProvider":"magpie","defaultModel":"MiniMax-M3.1-Flash","defaultThinkingLevel":"low"}' > "$PI_CODING_AGENT_DIR/settings.json"
bash dev/mock.sh start                 # 127.0.0.1:18081；stop / health / reset 同理
pi install "$PWD" && python3 dev/rpc_commands.py       # 应列出 /preset、5 个提示模板、2 个技能（共 10 条，含 pi 自带的 /llama /mcp）
```

- **`settings.json` 里的 `defaultProvider/defaultModel` 不能省**：没有它 pi 会选内置的提供商，请求根本到不了模拟网关，报 `Unexpected token 'r', "request bl"... is not valid JSON`（被沙箱代理拦了）。上一个 session 在这里踩过，还差点把“请求没发出去”当成“安全带拦住了”。**每个检查都要先确认请求真的到了模拟网关**（`dev/run` 里的转储非空）。
- `PI_CODING_AGENT_DIR` 必须指到一个隔离目录，别指到真的 `~/.pi/agent`。`pi install` 会把包路径写进它的 `settings.json`（相对路径）。
- 沙箱是 Node 22，用户的 WSL 是 Node 24.15.0；到目前没发现差别。

## 2. 里面有什么

| 文件 | 做什么 |
|---|---|
| `mock.sh` | 起/停/查模拟网关。`start [端口] [脚本.json]`；环境变量 `MOCK_RUN_DIR`（pid/日志/转储放哪，默认 `/tmp/pinew-mock`）、`MOCK_MODULE`（换成别的模拟，如 `labs/lesson8/mock/mock_lab8.py`）。每次 `start` 清空日志与转储 |
| `mock_gw.py` | 模拟网关本体（OpenAI 与 Anthropic 两种协议，脚本化的“模型”，模拟前缀缓存）。脚本格式见文件头注释；例子：`example-script-empty.json`（直接回 OK）、`example-script-mcp.json`（按工具表分支走 MCP） |
| `models.mock.json` | 隔离 agent 目录里的 `models.json`：提供商 `magpie`，模型 `MiniMax-M3.1-Flash`，价格 0.15/0.6/0.015 |
| `rpc_commands.py` | 用 RPC `get_commands` 列出 pi 实际加载的命令/模板/技能及其来源路径。**不发模型请求，免费**；每次装/迁移后跑一次，重复加载或缺文件一眼可见 |
| `seed-check.sh` | 2026-11-22 手工做过的 12 项检查的脚本化：包加载、安全带、预设、双装崩溃复现、基线哈希。**是 `tools/check.py`（v1.1）的种子**，已在 pi 1.5.0 上全绿 |
| `env.sh.example` | 沙箱环境变量模板（`dev/env.sh` 已被 `.gitignore`） |

## 3. 复现“双装崩溃”（HANDOFF §5.1）

```bash
mkdir -p "$PI_CODING_AGENT_DIR/extensions" && cp extensions/*.ts "$PI_CODING_AGENT_DIR/extensions/"   # 模拟 install_v1.py 的复制式安装
pi -p hi --no-session < /dev/null      # → exit 1：Failed to load extension ".../preset.ts": Flag "--preset" conflicts with ...
pi -ne -p hi --no-session < /dev/null  # -ne = --no-extensions，能进，用来恢复
```

安装器的迁移就是要让这件事不发生：先按文件名精确删掉复制的 v1 扩展/提示模板/技能（只删哈希与基线一致的），再 `pi install <路径>`。复制的 `prompts/`、`skills/` 还会**静默盖住**包里同名文件。

## 4. 常用手法

- 一次性的、不写会话的打印模式：`pi -p "…" --mode json --no-session --thinking low --tools read,bash,edit,write < /dev/null`。`< /dev/null` 不能省（否则等 stdin）；这个模式下没人能点“允许”，安全带会直接拒绝并告诉模型原因。
- `--mode json` 的事件流里 `message_start`（role=system）带 `sections`（pi 拼出的系统提示分段），`agent_end` 带全部消息，`message_end` 带 `usage`。`labs/lesson1/pi_events.py` 把它整理成表。
- 看模拟网关收到的完整请求体：`MOCK_DUMP`（`mock.sh start` 会设）里每行一个请求，含 `tools`、`system`、`messages`——检查“前缀逐轮严格追加”就靠它。
- 带 MCP 的实验：`labs/lesson6/mcp/mini_mcp.js`（一个小 MCP 服务器）+ `labs/lesson6/lab-presets.json`。
