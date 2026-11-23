# labs/ — 八节课的实验脚本（已结业；当模板用，别当任务重做）

每个脚本都会**真的发请求**（走用户的网关，按 `models.json` 里的价格计费），各课预算大约 $0.02–0.4（第 6 课 $0.0763、第 8 课 $0.1758）。要重跑先看对应 README 里的预算和 `--dry-run`。README 里的路径沿用旧布局（`~/pi/lessonN/`），自己换成 `~/pinew/labs/lessonN/`。**结论已整理在 `docs/FACTS.md`，不用为了“确认”再跑。**

| 课 | 主题 | 脚本 | 可复用的东西 |
|---|---|---|---|
| 1 | agent 循环 | `mini_agent.py`（几十行的迷你 agent）、`pi_events.py`（把 `--mode json` 事件流整理成表）、`show_messages.py`、`hello.py` | **`pi_events.py`**：解析 pi 事件流的现成模板 |
| 2 | 前缀就是钱（缓存） | `cache_lab.py` | 缓存实验模板：同一前缀重发 / 改前缀不同位置 |
| 3 | 思考档位 | `think_lab.py` | 档位对比的模板（以及怎么把设计做坏——FACTS D、L7） |
| 4 | 规则/技能/模板怎么送达、各占多少前缀 | `rule_lab.py` | 前缀成本的量法 |
| 5 | 扩展 | `ext_lab.py`、`tally.ts`、`guard-mini.ts`、`exercises/` | 扩展对缓存的影响实验（K0–K4、S1–S8） |
| 6 | 预设与 MCP | `preset_mcp_lab.py`、`score_lesson6.py`、`lab-presets.json`、`mcp/mini_mcp.js` | **`mini_mcp.js`**：测 MCP 用的小服务器；预设切换断缓存的实验 |
| 7 | 长会话（压缩/恢复/分叉/冷热首请求） | `session_lab.py`、`score_lesson7.py` | `--continue`/`--fork`/压缩的实验写法 |
| 8 | 子代理 | `delegate_lab.py`、`lab8_ext.ts`、`score_lesson8.py`、`analysis/`、`mock/` | **`lab8_ext.ts`**：子代理（`delegate` 工具）原型，v1.5 的起点；`mock/` 是它的专用模拟网关（`MOCK_MODULE=labs/lesson8/mock/mock_lab8.py dev/mock.sh start`） |

- `score_lesson6/7/8.py` 是**冻结的评分脚本**（数据到之前就写好，之后不改）。第 2–5 课的评分脚本没有保留。
- 原始日志在 `archive/evidence/lessonN/`。
- 做新实验的纪律见 `docs/ROADMAP.md` §4：先写预期、这个 n 能分辨多大的差别、对照臂；每格 ≥5 次。
