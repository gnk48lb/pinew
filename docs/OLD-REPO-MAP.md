# 旧仓库地图（`github.com/gnk48lb/pi`，固定在提交 `922884b6b6d75f61a405b05f3adb36964a8f4a7d`）

旧仓库是上一个 session 一个多月的**原始材料全集**（用户把工作区整个放了上去）：教学脚本、原始日志、各版压缩包、导出的长文档，混在一起，约 11 MB。**pinew 已经带走了有用的部分**，这一篇告诉你：旧路径 → 是什么 → 在 pinew 的哪里 → 还要不要去取。需要时只取单个文件，不要克隆来通读。

## 怎么取一个文件

```bash
# 方法 1：只取某个路径（不拉全部历史）
git clone --filter=blob:none --no-checkout https://github.com/gnk48lb/pi /tmp/pi-old
git -C /tmp/pi-old checkout 922884b6b6d75f61a405b05f3adb36964a8f4a7d -- "pi-round3/README-round3.md"

# 方法 2：原始 URL（中文文件名要 URL 编码）
curl -fsSL https://raw.githubusercontent.com/gnk48lb/pi/922884b6b6d75f61a405b05f3adb36964a8f4a7d/pi-round3/README-round3.md
```

（沙箱的网络白名单通常放行 GitHub；不行就让用户把那个文件贴/传过来。）

## 对照表

| 旧路径 | 是什么 | 在 pinew 里 | 还要取吗 |
|---|---|---|---|
| `pi-lesson0/lesson0/`（`v1/`、`install_v1.py`、`install.sh`、README） | **v1.2 配置包 = 本仓库 `v1.0.0` 基线**（哈希逐字节相同）+ 安装器 | `extensions/` `prompts/` `skills/` `config/`；安装器 → `tools/legacy/`；README → `docs/baseline/lesson0-README.md` | 不用 |
| `pi-v1/v1/`、`pi-v1/round2.sh` | **最早的 v1**（第 2 轮用的；安全带还没有豁免；budget-guard 是旧版） | 没带 | 只在考古时取 |
| `pi-round3/`（`arena/`=v0.2、`v1/`=v1.1、`variants/{v1-agents,v1-ext,v1-fid}`、`round3.sh`、`README-round3.md`） | 第 3 轮（v1 的消融：只带 AGENTS.md / 只带扩展 / 加保真闸） | `v1-fid` 里的 `edit-fidelity.ts` → `experimental/`；其余没带 | 不用 |
| `pi-round4.tgz`（只有压缩包，仓库里没解开） | **第 4 轮**：arena v0.3（含封存题库）、v1.2、variants、`round4.sh`、README | arena v0.3 → `bench/arena/`；`round4.sh`、README → `bench/` | 不用（已解开带走） |
| `arena-v0/`、`arena-v0.1/` | 评测台 v0、v0.1（被 v0.3 取代） | 没带 | 不用 |
| `arena/`（`probe2.py`、`probe2.out`、`probe2.net.out`） | 10 月对网关的探针与输出 | `archive/evidence/gateway-probes/` | 不用 |
| `pi-lesson1/` … `pi-lesson8/`（各含 `lessonN/`） | 各课的包：实验脚本、README、**用户跑出来的原始日志 `exN.log`**、猜测记录 | 脚本/README → `labs/lessonN/`；日志、猜测 → `archive/evidence/lessonN/` | 不用 |
| `pi-lesson1/lesson1/{pi1.jsonl,run1.json}`、`pi-lesson6/lesson6/req.jsonl` | 第 1、6 课的原始数据 | 没带（体积大、结论已入 FACTS） | 需要复算时取 |
| `pi-lesson8/lesson8/indep8_local.py` | 第 8 课的独立复算（发给用户在 WSL 里跑的那份，不依赖评分脚本） | 没带；`labs/lesson8/analysis/indep8_real.py` 是在沙箱里对真实日志另写的独立复算（两份结果一致，差 <1%） | 不用 |
| `score_lesson7.py`（仓库根） | 第 7 课评分脚本的**较早版本**（设计 G1–G3/P1–P8） | `labs/lesson7/score_lesson7.py` 是**冻结的最终版**（指纹 8387f1a371c4，与 `ex7.log` 一致）；两份内容不同，以 pinew 的为准 | 不用 |
| `see_body.py`、`see-sys.sh`（仓库根） | 在用户真网关上看 pi 发出的**完整请求体/系统提示**的工具 | `tools/real-gateway/`（只改了用法里的路径） | 不用 |
| `pi-lessonN.tgz`、`pi-v1.tgz`、`pi-round3.tgz`、`r3scan.tgz` | 上面各目录的压缩副本 | — | **别取**（重复） |
| `artifact/pi 定制手册：边学边练 (1).md`（289 KB） | 手册 11-21 的导出（第 0–7 章 + 第 8 章雏形） | `archive/docs-2026-11-21/handbook.md` | 要查长篇解释才 grep |
| `artifact/pi 定制与评测路线图 (15).md`（390 KB） | 路线图 11-21 的导出（实验日志、各轮预测与判据） | `archive/docs-2026-11-21/roadmap.md` | 同上 |
| `artifact/Claude-Pi编程agent定制优化-20261121-1203.md`（569 KB） | 11-21 中午的**整段对话导出** | 没带 | 不用（太长；结论已在 FACTS） |
| `Claude-Pi第5课讲解与实验复盘-20261115-2235.md`、`Claude-Pi第8课练习指导-20261121.md` | 第 5、8 课的讲解导出 | 没带 | 不用 |
| `回复作者-第6课.md`、`11.md`、`grok对话.md` | 用户的零散笔记（第 6 课的回复、一份与 pi 无关的笔记、另一个 AI 的对话） | 没带 | 不用 |

## 要注意的事

- **沙箱里补的文件不在旧仓库里**：`labs/lesson8/{score_lesson8.py, analysis/, mock/}`、`labs/lesson6/{score_lesson6.py, lab-presets.json}`、`dev/`（模拟网关、`rpc_commands.py`）、`bench/arena/` 里 v0.3 新增的 `arena_peek.py`、`arena_sandbox.py`、`r4_scan.py`、`arena_selftest_v03.py` 和各题的 `sealed.dat`（来自 `pi-round4.tgz`）。
- **最新的手册和路线图在用户的 Claude Docs 里**（含第 8 课数据回填），旧仓库和 `archive/` 里只有 11-21 的导出。第 8 课的结论已整理进 `FACTS.md` §I。
- 旧仓库里的 `.gitignore` 只有一行；**不要往旧仓库推东西**。建议用户之后把旧仓库在 GitHub 上归档（Settings → Archive，一次点击；不删，pinned 提交仍然可取）——这件事问他一次，不要替他做。
