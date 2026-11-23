# STATE — 上一个工作段停在哪

**每个工作段结束前更新这一页**（它是下一个 session 接上的唯一入口）。只写现状和下一步，历史看 `git log` 和 `CHANGELOG.md`。

- 最后更新：2026-11-24（交接；11-23 按用户反馈修订过一次）· 更新者：上一个（教学）session
- 当前版本：**v1.0.0**（= 实测过的 v1 + pi 包外壳；tag `v1.0.0` 指向推送时的最终提交）
- 用户 WSL 上 pinew 的状态：**还没装**。他那里现在是 `install_v1.py` 复制式安装的 v1（12 个文件复制进 `~/.pi/agent/`），或者别的什么——**真实情况未知，等 `collect-state.py` 的输出**。

> **推进规则（用户 11-23 定的，别漏）**：**没有“先用一两周、攒摩擦再做”的门槛。**他不想拿半成品当主力熬着，要你先主动把 pinew 打磨到顺手、靠谱，他再重度上手。所以：按 `ROADMAP.md` 的顺序 v1.1 → v1.2 → v1.3 → v1.4 → v1.5 → v2.0 → v3 以后**连续推进**，他没报摩擦也不停下来等；他报了摩擦就记进 `FRICTION.md` 并插队；验收看可测的标准，不看“他用了多久”；他全力配合（跑命令、贴输出、二选一）。完整规则在 `ROADMAP.md` 开头。

## 已验证（沙箱：pi 1.5.0 + `dev/mock_gw.py`，2026-11-22/23；mock 不是真网关）

- `pi install <本地路径>` 后：`/preset`、5 个提示模板、2 个技能都加载，包按路径加载（不复制，改了文件无需重装）。
- 安全带在 print 模式拦下 `rm -rf old`（交互模式弹窗这里没法测）。
- 缺 `presets.json` 时 `--preset explore` **静默无效**（工具照旧、没有指令、没有报错）。
- **双装**（`~/.pi/agent/extensions/` 里有复制的 v1 + 再装包）：pi 退出码 1，`Failed to load extension … Flag "--preset" conflicts`；`pi -ne` 可进；复制的 `prompts/`、`skills/` 会静默盖住包里同名文件。
- **`--preset X` 盖掉命令行的 `--thinking`**（`--preset deep --thinking low` 发 16384=high；`--tools` 不受影响，是外层白名单）：FACTS G7，`dev/seed-check.sh` 里有检查。
- **provider 级 `compat` 对 anthropic-messages 的模型照样生效**（`x-session-affinity` 头：provider 级/模型级 compat 都有头，无 compat 无头）：FACTS F9，用嗅探服务器测的。
- 仓库根的 `CLAUDE.md`/`AGENTS.md` 会进 pi 的系统提示，`.claude/CLAUDE.md` 不会。
- `dev/mock.sh`（start/stop/health/reset）、`dev/rpc_commands.py`（RPC `get_commands`，不花钱）可用；`bash dev/seed-check.sh` 14 项全 PASS。
- 15 个基线文件与 `v1.0.0` 逐字节相同：`python3 tools/verify-baseline.py`。
- 密钥扫描干净（无 API key、无邮箱）；`.gitignore` 挡着 `models.json`/`auth.json`/`.env*`/`dev/env.sh`。

## 没验证（别当成已知没问题）

- 任何东西在用户的真 WSL 上装 pinew 的行为（一次都没装过）。
- `pi install git:github.com/gnk48lb/pinew@v1.x.y`（要仓库在线）。
- 真网关上的 token 数：mock 的 token 数是近似的；要量前缀请用 `tools/real-gateway/`（花一个请求的钱）。
- `agent_before_settle` 钩子（v1.4 验证闭环要用）：只读了 pi 1.5.0 的文档，**没跑过**（FACTS F8）。
- 官方 `plan-mode/` 退出计划模式时的 `context` 钩子会把旧消息从请求里滤掉：读了源码，**没测**对缓存的影响（ROADMAP v1.4）。
- `grep`/`find`/`ls` 三个工具在 MiniMax 上的前缀代价与收益：没量（ROADMAP v1.2；FACTS §K）。
- `experimental/edit-fidelity.ts` 在 pi 1.5.0 上（读 1.3.0 源码写的）。
- `tools/check.py`、`tools/pinew.py`、`tools/smoke.*`：**还不存在**，是 v1.1 的内容。

## 下一步（按顺序；**做完一版就开下一版，不等用户“先用几天”**）

1. 读 `docs/HANDOFF.md`（§0 一屏版和推进规则、§4 铁律、§5 踩坑、§8 第一小时）和 `docs/ROADMAP.md`（开头的推进规则 + v1.1.0）。
2. 确认能推送：`add_repo(owner="gnk48lb", repo="pinew", access="push")`。
3. 搭沙箱：`mkdir -p ~/rt && cd ~/rt && npm i @earendil-works/pi-coding-agent@1.5.0`；`cp dev/env.sh.example dev/env.sh`；起 mock；`python3 dev/rpc_commands.py` 应列出 `/preset`、5 个提示模板、2 个技能；`bash dev/seed-check.sh` 应 14 项全 PASS。（`dev/README.md`）
4. **让用户做一件事**：`git clone https://github.com/gnk48lb/pinew ~/pinew && python3 ~/pinew/tools/collect-state.py`，整段贴回（只读、无网络、密钥打码；会打出 provider 级和模型级的 `compat`）。**等他贴回输出时不要空等**：沙箱里先把 `tools/pinew.py` / `tools/check.py` 的骨架和假 HOME 场景写起来。
5. 按输出设计并写 `tools/pinew.py`（status / install --dry-run / install / update / undo；非包文件的同步约定见 ROADMAP v1.1）与 `tools/check.py`，在假 HOME 里测完所有场景；再让用户 `--dry-run` → 正式迁移 → 烟测。
6. 打 `v1.1.0`，更新本页和 `CHANGELOG.md`，推送；**然后直接开 v1.2**（档位设计要的两个信息——他日常做什么、`pi --list-models`——和 v1.1 的真机步骤合并成同一条消息问他）。

## 等用户的

- 在新账号里让它能访问 `gnk48lb/pinew`（连 GitHub、授权读写；第一次推送时弹授权他点一下）。
- 在他的 WSL 里跑一条 `collect-state.py`（见上面第 4 步）。

## 开放问题（只有用户能答；一次一个，二选一；问完就做，他没回就先做不依赖答案的部分）

- 他日常主要拿 pi 做什么？（改自己的项目 / 写脚本运维 / 读别人的代码 / 混合）→ 决定 v1.2.0 的档位。
- `pi --list-models` 里有没有更便宜或更强的备选模型？→ 档位要不要带 `model`（默认不带）。
- 要不要在 `~/.local/bin/` 放几个启动脚本（`pq`/`pe`/`pd`/`pr`，计划入口 v1.4 起是 `pi --plan`）还是自己写 alias？→ v1.2.0。
- 旧仓库 `gnk48lb/pi` 要不要在 GitHub 上归档（一次点击，不删）？

## 工作段日志

| 日期 | 谁 | 做了什么 | 停在哪 |
|---|---|---|---|
| 2026-11-22/23 | 教学 session（交接） | 搭 `pinew`：包外壳 + 基线 + 实验室脚本 + 评测台 v0.3 + 模拟网关 + 证据；写 HANDOFF / FACTS / ROADMAP / STATE / OLD-REPO-MAP；沙箱验证包加载、安全带、预设；发现并记录双装崩溃与 `--preset` 静默无效 | v1.0.0；v1.1 一行没写 |
| 2026-11-23/24 | 教学 session（按用户反馈修订） | **去掉“先用一两周再攒摩擦”的门槛**（推进规则，ROADMAP/HANDOFF/OPENING_PROMPT/STATE 统一）；p16 口径统一成“按规格实现、测试不动、如实报告”；新记 `--preset` 盖 `--thinking`（G7）、`compat` 照样生效（F9，更正了 HANDOFF §5.9）、`agent_before_settle`（F8）；FACTS 的 suite1/think-low 标签按日期改准、I4 补逐句累计；跨账号说明；ROADMAP：v1.1 非包文件同步约定、v1.2 不单列 `plan` 档并评估 `grep/find/ls`、v1.3 检查点改按用户消息 + 每轮花费一行、v1.4 采用 `plan-mode` 并以 `agent_before_settle` 做验证闭环；`collect-state.py` 补打模型级 `compat`；`seed-check.sh` 加 G7 检查 | v1.0.0；v1.1 一行没写 |
