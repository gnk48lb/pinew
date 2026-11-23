# archive/ — 证据，**只 grep，不通读**

这里是上一个（教学）session 一个多月的原始证据。`.ignore` 让 rg / Grep 默认跳过它；要查就显式搜：`rg 关键词 archive/`。所有结论已经整理在 `docs/FACTS.md`（带出处），**先看那里**。

| 路径 | 是什么 |
|---|---|
| `evidence/lessonN/exN.log` | 用户在 WSL 里跑第 N 课实验得到的**原始日志**（每个请求一行 JSON，含 token/花费；末尾有“数据”段）。第 8 课还有 `score_out.txt`（冻结评分脚本的输出）、`indep_out.txt`（独立复算）、`post8*_out.txt`（事后分析，已标注）、`ex8-guesses.txt`（用户事先写的猜测）、`第8课-交作业.md` |
| `evidence/lesson2/ttl.log` | 缓存寿命（15 分钟热 / 30 分钟冷）的原始记录 |
| `evidence/lesson5/exercises-A-B-results.md` | 第 5 课练习 A/B 的结果 |
| `evidence/lesson6/{ex6-exercises.log, l6-guesses.txt}` | 第 6 课练习与用户的猜测 |
| `evidence/gateway-probes/` | 10 月对用户本地网关的探针（`probe2.py` + 输出）：协议、缓存健康度 |
| `docs-2026-11-21/handbook.md` | 手册《pi 定制手册：边学边练》11-21 的导出（第 0–7 章 + 第 8 章雏形；约 289 KB） |
| `docs-2026-11-21/roadmap.md` | 路线图《pi 定制与评测路线图》11-21 的导出（实验日志、每轮预测与判据；约 390 KB） |

**过期说明**：两份导出停在 11-21 中午，**没有第 8 课的数据回填**（第 8 课的结论在 `docs/FACTS.md` §I；原始数据在 `evidence/lesson8/`）。最新版在用户的 Claude Docs 里，不在本仓库。

怎么读日志：第 3–8 课的 `exN.log` 第一行是 pi 版本、模型、设计指纹，之后**每次运行一行 `数据 {…}`（JSON，含 token 与花费）**（第 3 课 108 行、第 4 课 109、第 5 课 61、第 6 课 95、第 7 课 23、第 8 课 28）；第 1、2 课是文字记录。解析模板见 `labs/lessonN/score_*.py`（只有第 6、7、8 课的冻结评分脚本保留了下来；第 2–5 课的评分规则和结果记在 `docs-2026-11-21/` 的路线图和手册里，脚本本身没有保留）和 `labs/lesson1/pi_events.py`。
