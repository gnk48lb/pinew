# 交接文档（给新 session）

写于 2026-11-22/23（11-23 按用户反馈修订：推进规则、`compat`、preset 与 `--thinking`、跨账号），上一个 session 的最后一件事。读完这一篇就能开工；要查某个数的来历去 `FACTS.md`（用 grep 找条目，别通读）；上一个 session 的流水账（`archive/`）不用读。

## 0. 一屏版

- **谁、在做什么**：用户 gnk48lb（说中文，时区台北 UTC+8）用 **pi**（pi.dev 的终端 coding agent，当前 **1.5.0**）。过去一个多月，上一个 session 带他把 pi 调成了“v1”，并在他的 WSL、他的网关和他现在的模型上实测了：钱花在哪、缓存怎么断、每样东西占多少前缀（第 0–8 课，教学已经结束）。
- **现在的目标变了**：不再教学，而是把 **pinew**（本仓库）从 v1 一版版迭代成**他每天真用的主力 coding agent**。先补两块短板：①开局多档位（现在只有 explore / deep 两个 preset）；②日常主力缺的功能（撤得回改动、计划/待办、验证闭环，其次是子代理、逐轮花费）。顺序和验收见 `ROADMAP.md`。
- **分工**：**你做全部改动、提交、推送**。用户只配合三件事：在他的 WSL 里跑你给的**一条**命令并整段贴回输出；回答是/否或二选一；（有的话）说用着哪里别扭。别让他手动改文件、别让他打一串 git 命令。
- **推进规则（用户 11-23 定的，最重要的一条）**：**没有“先用一两周、攒出摩擦再做”的门槛。**他是个懒人（原话），不想拿半成品硬当主力熬着，要你先主动把 pinew 打磨到顺手、靠谱，他再重度上手。所以：按 `ROADMAP.md` 的顺序 v1.1 → v1.2 → v1.3 → v1.4 → v1.5 → v2.0 → v3 以后**连续推进**，他没报摩擦也**不停下来等**；他报了摩擦就记进 `FRICTION.md` 并**插队**；验收看可测的标准（`check.py`、mock、真网关量一次、真机一条冒烟命令），**不看“他用了多久”**；他全力配合（跑命令、贴输出、二选一），需要就直接要，把要他做的事合并成一条。只有两种情况停下来等他：要他拍板的事，或要他在 WSL 里跑一条命令才能继续的验证——结束这一轮前先把不依赖他的活做完。完整规则见 `ROADMAP.md` 开头。
- **起点**：本仓库 `v1.0.0` = 实测过的 v1 **字节级原样**（旧文档里叫“v1.2”，哈希见 `docs/baseline/v1-SHA256SUMS.txt`）+ 一层 pi 包的外壳。v1 在 DeepSeek 时代的评测里“没测出伤害，也没测出收益”（FACTS §J），所以后面的改进靠**每版的可测验收 + 真机冒烟 + 他随时报的摩擦（插队）**，不靠比赛。
- **节奏**：轻快务实。小步、能装、能回滚；不再冻结 v1，不再默认写预测登记。只守四条（§4）：缓存前缀只追加、数字带标签、小步提交+CHANGELOG+STATE、基本验证。
- **第一件事**：`ROADMAP.md` 的 v1.1（安装器/迁移器 + 自检）。**在它做好之前，不要让用户在已装旧版 v1 的 WSL 上 `pi install`——pi 会直接起不来**（§5 第 1 条，已实测）。

## 1. 你接手的东西：v1 里有什么

用户 WSL 上现在装的是它，经 `install_v1.py` **复制**进 `~/.pi/agent/`（所以才有上面那个双装问题）。仓库里对应位置：

| 部件 | 仓库位置 | 做什么 | 占前缀（实测：MiniMax-M3.1-Flash，pi 1.5.0，用户网关） |
|---|---|---|---|
| 工作约定 | `config/AGENTS.md` | 8 条：用用户的语言回复、先读后改、改完要验证、独立查询同轮并发、工具间文字≤1 句、不可逆操作先问且不擅自 commit/push、两次失败就停下汇报、收尾短报告 | **+285 token/请求**（每多一条一行规则约 +41） |
| 安全带 | `extensions/safety-gate.ts` | `tool_call` 钩子：危险 bash（rm -r、sudo、git push/reset --hard…）、写 `.git/` `.env` 项目外路径、读密钥文件时拦截；交互模式弹窗 Allow once / Allow session / Deny；print/json 模式没人能点，直接拒绝并给模型一个理由。v1.2 放行三类无害清理（`rm -rf build dist` 类、/tmp 下 mktemp 变量、heredoc 文本里提到 rm）。`PI_SAFETY_GATE=off` 关 | 0 |
| 预算护栏 | `extensions/budget-guard.ts` | 每次“用户发一条消息”算一个 run：软限 4 万输出 token / 30 次调用 / $1 → 提醒模型收尾一次；硬限 8 万 / 60 / $3 → 中止。防“一直想不收敛”（DeepSeek 时代出现过 13.4 万输出 token、17× 中位数）。`PI_BUDGET_GUARD=off` 关 | 0（只在触发时才发一句话） |
| 预设 | `extensions/preset.ts` | 官方示例原样。`--preset NAME`、`/preset`、Ctrl+Shift+U 轮换；预设字段 provider/model/thinkingLevel/tools/instructions；**只从 `<agentDir>/presets.json` 和 `<cwd>/.pi/presets.json` 读**（项目级覆盖全局）；**`--preset` 会盖掉命令行的 `--thinking`**（§5.16） | 0 |
| 预设内容 | `config/presets.json` | `explore`：low + 只读四件套（read/grep/find/ls）+ READ-ONLY 指令；`deep`：high + 全工具 + DEEP MODE 指令。**两个都没写 model**，所以不换模型 | 切换时见 §5 |
| 设置 | `config/settings.json` | `defaultThinkingLevel: "low"`、`showCacheMissNotices: true` | — |
| 提示模板 | `prompts/` | `/init`（生成项目 AGENTS.md）、`/plan`（只出方案）、`/review`、`/commit`（只提交不推送）、`/explain` | 0；用时 +40 |
| 技能（隐藏） | `skills/debug` `skills/refactor` | `disable-model-invocation: true`，只能 `/skill:名字` 手动触发 | 0；用时 +97 |
| 实验性 | `experimental/edit-fidelity.ts` | **不在 v1 里**。拦住会悄悄弄坏文件的 `edit`/`write`（非 UTF-8、混合换行、CRLF 被 write 改成 LF）。读 pi **1.3.0** 源码写的，**没在 1.5.0 上复核**；0 前缀 | 0 |

v1 明显的短板（用户的原话：“底座稳，但东西精简、配置简单，离日常主力还有一段”）：开局档位只有两个；改坏了没法一键撤；没有计划模式/待办；没有验证闭环；没有逐轮花费/命中率（pi 的 footer 已显示**累计** token、花费、上下文占用，缺的只是“这一轮”）；子代理只在实验室里（`labs/lesson8/lab8_ext.ts`）；没有 MCP（文档/搜索）；升级靠人记命令。

## 2. 两个世界：你的沙箱 vs 用户的 WSL

| | 你的沙箱（云容器） | 用户的 WSL |
|---|---|---|
| 真模型/网关 | **够不到**。只有 `dev/mock_gw.py` 模拟网关（会按前缀模拟缓存，但它不是真实网关的证据） | 本地网关 **magpie**（`http://127.0.0.1:3425/v1`，10-19 的探测值，现在可能已变），当前模型 `MiniMax-M3.1-Flash`（日志里的路由名有 `minimax-code-global/…`（第 2–5 课）和 `minimax-code/…`（第 6–8 课）两种，以 `collect-state.py` 打出的 models.json 为准） |
| 能做 | 装真 pi（`npm i @earendil-works/pi-coding-agent@1.5.0`）、跑 RPC/print/json、验证包加载、安全带、预设、前缀逐轮追加、写脚本、推 GitHub | 真实 token/命中/花费、真实使用手感、安装迁移的最后一公里 |
| Node | 22 | 24.15.0（nvm） |
| 其它 | 没有他的 `~/.pi/agent` | Ubuntu on WSL2（约 2 CPU / 7.8 GB 内存 / 8 GB swap，宿主 16 GB）；pi 是 **pi.dev 托管安装**（`~/.local/bin/pi → ~/.pi/agent/bin/pi`）；**没有 jq**（用 python）；HTTP 代理 `127.0.0.1:7897`，`NO_PROXY` 含 127.*；Docker 已装但 WSL 集成没开；WSL 里还有锁了版本的 Claude Code 2.1.302（对比已搁置，不要提） |

要点：

- **`~/.pi/agent/` 既是配置目录，也是托管安装的根**（里面有 `bin/`、`releases/<版本>/`、`current-version`、`managed-install.json`）。**任何迁移/卸载都只能按文件名精确动手，绝不能整目录删。**
- 升级：`pi update` 在 `releases/<新版本>/` 装好新版再改写 `current-version`，旧版目录保留到下次更新；**退回 = 把 `current-version` 改回旧版本号**；配置文件不受影响（11-04 在仿真环境演练过，31 项）。升级前先读随包 CHANGELOG 的 Breaking Changes / Caching / Compaction 三段，在隔离的 `PI_CODING_AGENT_DIR` 里装新版先跑自检。1.5.0 的唯一破坏性变更是“工具参数校验改用 pi-schema”。
- 涉及真实模型的数（token、命中、钱）只能让用户跑。每次要他跑东西前先说：大概花多少钱、多久、会不会改他机器上的东西。

## 3. 怎么和用户配合

- **语言**：对话用中文；`docs/` 里的 HANDOFF/FACTS/ROADMAP/STATE 用中文；代码、标识符、注释、commit message、CHANGELOG 用英文。
- **回复要短**：先结论，再一条下一步。长内容写进文件。用户不爱读长篇，常常只回一个“continue”。
- **给他的命令**：一次一条、能整段复制、只读的先给；会改东西的先给 `--dry-run`，让他看一眼再正式跑；告诉他输出整段贴回来。需要他拍板的只给二选一并说你的推荐。
- **不要**：重新教学；默认写预测登记；让他手改文件；把他能自己看到的东西再问他一遍；替他做不可逆的决定（删仓库、改 key、换网关）。
- **隐私**：**他的个人邮箱地址不得出现在任何文件、提交、外发请求里**（你也不会被告知；万一在 `git config` 或日志里看到，别抄进仓库）；提交作者用 GitHub noreply；不要打印 `models.json` 里的 key（`tools/collect-state.py` 已自动打码，用它）。
- **第一次对他说话**：一句话确认收到交接 + 他要做的**一件**事（见 §8 第 4 步）。

## 4. 铁律（只有这四条）

1. **缓存护城河：前缀只追加。** 每个请求 = 系统提示 + 工具表 + 历史消息；缓存只认**完全相同的前缀**，在他的网关上任何位置的非追加改动都会让后面（甚至整段）重新按全价算。所以：每轮不许改系统提示/工具表/早期消息（每轮提醒一律用“追加一条消息”）；会话中途切换 thinking 档位、工具白名单、预设指令**各断一次缓存**（实测 8/8；5k token 历史约 $0.0006，40k 约 $0.0054）→ **档位在开局定；不要做自动的、反复的中途切换**（有意的、一次性的切换可以，例如“计划→实现”换一次，成本是上面这个数；官方示例 `jev-router.ts` 也是“一个会话只切一次，认一次缓存未命中”）。新增任何会进请求的东西（工具、技能、规则、MCP），必须在 CHANGELOG 里声明 `prefix: +N tokens/request`（用 mock dump 量，必要时让用户在真网关上量一次）。纯扩展代码（观察/拦截/通知）实测 0 前缀。
2. **数字带标签。** 任何成本/命中/质量的数字写明 `模型 + pi 版本`（+ n、是真网关还是 mock）。DeepSeek 时代的数标 `[DS, pi 1.3.0]`，当前基线是 `MiniMax-M3.1-Flash, pi 1.5.0`。价格：输入 $0.15 / 缓存读 $0.015 / 输出 $0.60（每百万 token）。
3. **小步提交 + CHANGELOG + STATE.md。** 每个可用增量一次提交；用户可见的变化写进 `CHANGELOG.md`（pi 在包更新时会显示它，格式 `## [x.y.z] - 日期`）并 bump `package.json` 版本；每个里程碑发版（提交标题 `Release vX.Y.Z` + `package.json` 版本 + CHANGELOG 条目；云端会话的代理不让推 tag，见 §5.17）；每个工作段结束前更新 `docs/STATE.md`（下一个 session 靠它接上）。
4. **基本验证。** 推送前 `tools/check.py` 全绿（v1.1 要建；见 ROADMAP）：包加载无重复、安全带仍拦 `rm -rf old`、`--preset` 生效、三轮会话前缀逐轮严格追加、发给模型的 system+tools 与基线字节一致（有意改动要声明）。有风险的改动（新工具、改请求的钩子、动安全带/预算护栏）让用户在真机上做一次冒烟（一条命令）。

另有几条“默认不做”：不装没读过源码的第三方 pi 包（扩展拥有用户的全部权限；要装就读源码、锁版本）；不改 `v1.0.0` 基线文件而不写 CHANGELOG；不在仓库根放 `CLAUDE.md`/`AGENTS.md`（pi 在这里运行时会把它读进系统提示；`.claude/CLAUDE.md` 是安全的，已放）。

## 5. 踩坑清单（都是实测过的；括号里是范围/证据）

1. **双装会让 pi 起不来**：`~/.pi/agent/extensions/` 里有复制的 v1 扩展，再 `pi install` pinew，启动报 `Failed to load extension … Flag "--preset" conflicts`，退出码 1；`pi -ne`（不加载扩展）可进。迁移必须先清掉复制的文件再装包。另外复制的 `prompts/`、`skills/` 会**静默盖住**包里同名文件（用户目录优先），改了包里的提示模板却不生效。（pi 1.5.0，沙箱 11-22）
2. **缺 `presets.json` 时 `--preset explore` 静默无效**：工具照旧四件套、没有 READ-ONLY 指令，也没有任何报错。`preset.ts` 不会读包内的 `config/presets.json`，所以要么安装器把它放进 `~/.pi/agent/`，要么改 `preset.ts` 多读一层包内默认。（pi 1.5.0，沙箱 11-22）
3. **`presets.json` 里省略的字段 = 保持上一个预设的值**（只有 `instructions` 跟随当前预设）。例：explore 没写 model，切到它不会换模型。（第 6 课）
4. **预设的 `tools` 白名单会把 MCP 工具一并关掉**（`--tools` 白名单同时管内置、扩展、MCP 工具）；开局 `--preset` 时 MCP 工具还在（原因没查），会话中途 `/preset` 才关，系统提示里的 `mcp_servers` 段也跟着消失。办法三选一：在预设的 `tools` 里点名入口（`codemode` 或 `tool_search`；direct 方式要把工具名写全）；开局就选好预设；让 `preset.ts` 的 `tools` 支持 `+名字`/`-名字`（pi 的 `--tools` 文档里有这种相对写法，没在预设里试过）。读 `preset.ts` 源码 [src]：它用 `pi.setActiveTools(validTools)` 整体替换工具表，所以 MCP 入口会被一起关掉。这是旧路线图里唯一的“预设”候选，在 `ROADMAP.md` 里归 v1.2.0；动手前先写下验收。（第 6 课，n=5–8）
5. **根目录的 `CLAUDE.md`/`AGENTS.md` 会被 pi 读进系统提示，`.claude/CLAUDE.md` 不会**（pi 1.5.0，沙箱 11-22）。所以别在仓库根放，也别把实验的 cwd 设在仓库根。
6. **print/json/rpc 模式**：一律 `< /dev/null`（否则等 stdin）、加 `--no-session`；这些模式下**没人能点“允许”**，安全带会直接拒绝匹配的命令（`rm -rf old` 在沙箱被拦，已验证）。写自检别用危险命令当“正常步骤”。
7. **`--thinking off` 关不掉思考**，会被抬到 low（这个模型，第 3 课）。thinking 档位在这个模型上**没测出**输出 token 的差别（R=low/max 0.70–0.88，只能排除“减半以上”）。
8. **不要拿新会话的第一个请求当成本基线**：冷/热两种状态（热 10/23，冷贵 2.7×，没找到控制办法，第 7 课）。单次读数也不可靠：长请求（≈4.4k token 以上）约 20% 概率莫名全价重算（第 5 课）→ 比较要 ≥5 次重复 + 一个对照臂。
9. **models.json 的 `api` 与 `compat`**：provider 级 `api: openai-completions`，但模型条目自己写了 `api: anthropic-messages` → pi 以模型条目为准，走 `/v1/messages`、用 `budget_tokens` 的思考（第 2 课）。**provider 级的 `compat` 照样合并进模型条目**：pi 文档（`docs/models-configuration.md` “Compatibility”）写明“模型值逐字段覆盖 provider 值，适用哪些字段取决于模型的 `api`”。用户的 `compat.sendSessionAffinityHeaders: true` 就写在 provider 级，对这个 anthropic-messages 模型照样发出 `x-session-affinity`（第 2 课；11-23 在沙箱用同款结构复测：provider 级 `openai-completions` + 模型级 `anthropic-messages` + provider 级 compat → 头发出，无 compat → 不发 [mock 嗅探器，pi 1.5.0]）。真正不起作用的是**属于另一种 `api` 的字段**（例如写给 `openai-completions` 的 `supportsDeveloperRole`、`thinkingFormat`，对 anthropic-messages 的模型无效）。`tools/collect-state.py` 会同时打出 provider 级和模型级的 `compat`；动 `models.json` 之前先看它。
10. **会话/MCP/技能的“看不见的前缀”**：可见技能 +132/+59 token；MCP 直连每个工具约 +101 token（12 个工具 +1,174，30 个 +3,007）；codemode +556、deferred +292 固定。扩展里 `registerTool` +42（带 promptSnippet/Guidelines 约 +95）。（第 5–6 课）
11. **子代理工具的 `usage`**：工具结果里的顶层 `usage` 会计入 pi 的合计（页脚、/session、RPC）但**不会发给模型**；子代理回的文字越长，后面每一轮越贵（回整份日志＝等于自己读）。（第 8 课）
12. **mock 不是网关**：沙箱里的模拟缓存是“前缀完全相同才命中”的理想版，没有随机全价重算、没有冷热两态。它能证明“前缀逐轮追加”，不能证明“真实命中率”。
13. **Windows 痕迹**：从 Windows 下载进 WSL 的文件会带 `*.Zone.Identifier`（已在 `.gitignore`）；WSL 里没有 jq。
14. **升级前先隔离试装**：独立 `PI_CODING_AGENT_DIR` + 自检；记下退回命令（改 `current-version`）。pi 近 30 天发过 15 个版本，别在没跑自检时 `pi update`。
15. **别回头追的小异常**（每次 <$0.00004，花时间不值）：约 6% 请求多出 66/67 个输入 token；缓存读数偶尔不是 128/256 的倍数；文档写的 512 token 起算门槛与实测（几百 token 也能读到缓存）对不上。
16. **`--preset` 会盖掉命令行的 `--thinking`**：`preset.ts` 文件头第 38 行写着 “CLI flags always override preset values”，但 `--preset` 是在 `session_start` 里应用的，比 pi 应用 `--thinking`（`main.js` 里作为会话创建参数）晚。实测（pi 1.5.0，模拟网关，请求体里的 `budget_tokens`）：`pi --preset deep --thinking low` → 16384（high）；`pi --preset explore --thinking high` → 2048（low）；没有预设时 `--thinking low` → 2048、`high` → 16384；预设里**没写** `thinkingLevel` 就不盖（`--preset t --thinking high` → 16384）。`--tools` 没这个问题：它是外层白名单，预设的 `tools` 只能在里面取交集（`--preset deep --tools read` → 只剩 read，`--no-tools` → 空）。v1 的两个预设都写了 `thinkingLevel`，所以现在就会被咬到；v1.2 要修（ROADMAP v1.2；FACTS G7；第 6 章 11-15 首测，11-23 复测）。
17. **云端会话的 GitHub 代理不让推 tag、不让删分支，推分支随便**——官方文档写明（`code.claude.com/docs/en/cloud-environments.md` 的 “GitHub proxy → Push restrictions”：*the proxy rejects branch deletions and pushes of anything other than a branch, such as a tag. It doesn't limit which branches a push can update.*），11-24 在沙箱里实测一致（git 2.43.0，仓库 `gnk48lb/pinew`，Claude GitHub App 装好之后；FACTS F11）：`git push origin main` 成功；`git push origin v1.0.0`（带注解的 tag）的 `GET info/refs?service=git-receive-pack` 返回 200，紧接着的 `POST git-receive-pack` 返回 **403 Forbidden**，客户端报 `RPC failed; HTTP 403 …`，连试 4 次一样，`git ls-remote --tags origin` 看不到 tag。**所以这不是授权没配好，换账号、换会话也一样；别试推 tag，也别换花样（轻量 tag 之类）去绕，更别建推上去就删不掉的临时分支。**同一页还写了：GraphQL 一律 403（`gh pr`、`gh issue` 不能用，REST 的 `gh api repos/{owner}/{repo}/…` 可以）；代理只服务挂到会话上的仓库。对策：①别推 tag；②发版标记改用“提交标题 `Release vX.Y.Z` + `package.json` 版本 + `CHANGELOG.md` 条目”，`pinew.py` 用 `git log --grep '^Release v'` 找版本；本地路径安装（计划里的主路径）的 `update` 只是 `git pull` + 同步、退回只是 `git checkout <提交>`，**不需要 tag**；③要用 `git:` 方式锁版本，用完整提交号：`pi install git:github.com/gnk48lb/pinew@<提交号>`——**已实测**（pi 1.5.0，沙箱，隔离的 HOME 和 `PI_CODING_AGENT_DIR`，11-24，FACTS F10）：带提交号、`@main`、不带 ref 三种都装得上，`pi list` 列得出；pi 的文档说 git 的 tag 和提交号是 pinned、分支跟着走；带斜杠的发版分支（`release/v1.1.0`）没测。仓库是公开的，WSL 里 `git clone https://github.com/gnk48lb/pinew` 不用登录；④真要有 tag（装不装是**用户的决定**，不是你的）：(a) 用户在 GitHub 网页上点一次（Releases → Draft a new release → Choose a tag 输入 `v1.x.y` → Create new tag → Target 选 `main` → Publish release），每版都点太烦；(b) 一次性装 `tools/github/auto-tag.yml`：用户把全文粘进网页上新建的 `.github/workflows/auto-tag.yml` 并提交一次，之后每次推送 `main`，GitHub 自己按 `package.json` 的版本在第一个带该版本的提交上打 tag（`v1.0.0` 会落在基线提交 `8e9114f`）；它的 shell 逻辑在本地仓库里测过 4 种情形（首次、幂等、版本在较早的提交里、JSON 格式不同），**没在真 GitHub Actions 上跑过**；别自己往 `.github/workflows/` 推（App 多半没有 workflows 权限，也不该替用户决定装不装）。两条都不是默认路径（FACTS F11）。

## 6. 十二条硬结论（摘要；证据、范围、n 在 `FACTS.md`）

范围默认 = MiniMax-M3.1-Flash，pi 1.5.0，用户网关。

1. 缓存只认完全相同的前缀；在他的网关上，系统提示/工具表/早期消息里任何位置的非追加改动，都让整段历史重新按全价算（K1/K4 各 10/10）。纯追加安全。
2. 会话中途换 thinking / 工具白名单 / 预设指令，各断一次缓存（thinking 8/8、tools 5/5、instructions 5/5）；断一次就够，后面恢复；切回去有 2/6 再断一次。
3. 缓存读价是输入价的 1/10，输出是输入的 4×（缓存读的 40×）。输出和“没命中的输入”才是大头；命中的前缀几乎免费（3.6k 前缀命中 $0.000054 / 未命中 $0.00054）。
4. 内置四个工具约占 1,013 token/请求（相当于 3.5 个 AGENTS.md）；AGENTS.md 8 条 +285；扩展代码本身 0。
5. 规则送达：AGENTS.md 一条规则 15/15 被遵守；`/模板`、`/skill:名` 15/15；可见技能描述写得模糊时只有 8/15 会被用上（静默失败）。（只测了 1 条规则、1 个模型）
6. thinking low 的默认值保留；在这个模型上低 vs 高没测出差别；`off` 无效（§5.7）。[DS, pi 1.3.0] 时 low 比默认便宜约 40%（成本 ×0.61），别拿它当 MiniMax 的数。
7. MCP 暴露方式有“入场税”：hidden 0；codemode +556；deferred +292；direct ≈ 101×工具数，约 5.5 个工具以内 direct 最省；用起来 direct 也最省（12 工具实测：direct 1×，deferred 2×，codemode 4×）；deferred 加载工具会再断一次缓存。
8. 长会话：自动压缩阈值 = 上下文窗口 − 16,384 ≈ 1,032,192 token，1M 窗口的模型基本碰不到；压缩摘要请求约 $0.0005–0.0008，压缩后第一次请求要重付；`--continue`/`--fork` 立刻接上不额外断缓存（5/5），超过 ~30 分钟冷。
9. 子代理：固定开销约 $0.00127（回传+辅助读取），每次追问便宜约 5×（c_D ≈ 0.20×c_R），要 ≥6.3 次追问才回本（32 KB 日志、n=4–8/臂）；子代理回传超过 ~12k 字符就失去优势；“回复里没有的细节”主会话 0/7 答得出（但都老实说不知道，没编造）。
10. 安全带/预算护栏：v1.2 在 [DS, pi 1.3.0] 的评测里 144/144 通过；v1 整包相对空白配置：轮数 ×1.08、成本 ×1.05（区间跨 1）：**没测出伤害，也没测出收益**。
11. 评测噪声：相同配置两批之间波动 15–17%（round 4）→ 做质量比较要 ≥2 个基线批次；难题已饱和，只有 p16（README 的新规则与旧测试冲突：[DS, pi 1.3.0] n=3/臂里 1 次按规格做对并如实报告，1 次沿用旧测试，1 次改 `package.json` 的 test 脚本让 `npm test` 变绿）暴露了行为差距。候选 AGENTS.md 规则（FACTS J4）：**规格与测试矛盾时按规格实现、测试不动、如实报告；不要为了变绿去改测试配置或跳过测试**——**不要写成“停下问”**，非交互评测里没人回答，等于认输。
12. pi 1.5.0 的行为：`--tools` 白名单管所有工具；扩展 `tool_call` 可拦截；`before_agent_start` 在 triggerTurn 时也触发；压缩摘要只看每个工具结果的前 2,000 字符（读源码所得，**未被数据独立验证**）；`agent_before_settle` 是最后一个可操作的边界，可追加条目并请求一次续跑，`agent_settled` 只通知（读 pi 文档所得，**没跑过**；v1.4 的验证闭环要用，见 FACTS F1）。

## 7. 优先级（详细验收见 `ROADMAP.md`；**按这个顺序连续推进，不设“先用满 N 周”的门槛**，见 §0 的推进规则）

- **P0 机制（v1.1）**：包 + `tools/pinew.py`（status / dry-run / install / migrate / update / undo）+ `tools/check.py` + 用户 WSL 上一次真实迁移与冒烟。不做完，后面每个改动都送不到用户手里。
- **P1 开局多档位（v1.2）**：在开局选档（`pi --preset X` 或 shell 别名），不是中途切；修 preset 的 tools-vs-MCP 坑，以及“预设盖掉 `--thinking`”（§5.16）；评估编码档要不要带 `grep/find/ls`；先向用户要 `pi --list-models` 看有哪些模型/价格，再设计档位（quick / explore / deep / review，需要的话 cheap；**不单列 plan**——计划归 v1.4 的官方 `plan-mode/`，一条路径）。
- **P2 日常主力短板（v1.3–v1.5，都要做，按序，不等摩擦）**：撤得回（git 检查点按**用户消息**而不是每轮、带时限和体积保护 + `/undo`；同版附带“每轮花费/命中”一行，纯 UI、0 前缀）→ 不迷路（官方 `plan-mode/` 作为唯一计划入口、待办、改完自动验证用 `agent_before_settle` 续跑、AGENTS.md 加“规格与测试矛盾按规格做”一行）→ 子代理产品化（回复上限、usage 回传、便宜的辅助模型；**默认关**）。**先看官方示例**：`examples/extensions/` 里有 `plan-mode/`、`todo.ts`、`git-checkpoint.ts`、`status-line.ts`、`model-status.ts`、`subagent/`、`handoff.ts`、`custom-compaction.ts`、`dirty-repo-guard.ts`、`protected-paths.ts`… 能改就不要重写；改之前先量前缀成本。
- **P3 打磨（v2.0 起）**：v2.0 验收（回归闸、升级演练自动化、README）→ MCP（文档/搜索，按工具数选暴露方式）、压缩/交接策略、按任务自动选档位（仅在开局）、edit-fidelity 在 1.5.0 上复核、重测套件；顺序见 ROADMAP v3+，**同样不等摩擦**。
- 用户说“这里别扭”就记进 `docs/FRICTION.md`（日期 + 场景 + 现象，一行）并**插队**；同类出现第二次，升为高优先级。**他没报摩擦时不要停下来等，也不要去催他“先用用看”**——照上面的顺序继续做。

## 8. 开工第一小时怎么走

1. 读 `STATE.md`、`git log -10`。仓库：在云端会话（claude.ai/code）里用户已在仓库选择器选了 `gnk48lb/pinew`，它就在你的工作目录里；没有的话 `add_repo(owner="gnk48lb", repo="pinew", access="push")`。确认能 `git push origin HEAD:main`（直接推 main，不走 PR）；推不了就一句话告诉用户缺什么，别绕（普通聊天/普通 Project 里没有挂载和推送，要请用户改到云端会话，见 `OPENING_PROMPT.md`）。
2. 搭沙箱：`mkdir -p ~/rt && cd ~/rt && npm i @earendil-works/pi-coding-agent@1.5.0`；`cp dev/env.sh.example dev/env.sh` 并按需改；建隔离的 `PI_CODING_AGENT_DIR`，把 `dev/models.mock.json` 放成它的 `models.json`；`dev/mock.sh start`；`python3 dev/rpc_commands.py` 应列出 `/preset`、5 个提示模板、2 个技能（`dev/README.md` 有全套）。
3. 官方文档和示例就在 npm 包里：`$RT/node_modules/@earendil-works/pi-coding-agent/{docs,examples/extensions}`。
4. **让用户做一件事**：在 WSL 里 `git clone https://github.com/gnk48lb/pinew ~/pinew && python3 ~/pinew/tools/collect-state.py`，整段贴回来（只读、无网络、密钥打码）。它会告诉你：pi 怎么装的、`~/.pi/agent` 里 v1 的 12 个文件哪些一致/被改过/缺失、他有没有自己加的扩展、models.json 里有哪些模型和价格、有没有已装的包。**安装器按这份输出设计，不要凭想象。**
5. 写 `tools/pinew.py` 与 `tools/check.py`。**别从零写安装器**：`tools/legacy/install_v1.py`（25 KB，只用标准库）已经是演练过的成熟品——先检查再动手、`--dry-run`、先写 MANIFEST 再动手、原子写入、装完读回核对、`--undo` 只撤自己装的、从不碰 `models.json`/`auth.json`/`sessions/`（当时 95 项假家目录场景 + 201 个“被强杀”断点 + 真 pi 42 项全过）。在它的骨架上加“迁移”：对 12 个 v1 文件逐个比对哈希（一致才删；被改过的搬到备份并提示；用户自己加的文件不碰）→ `pi install <本地路径>` → 合并 settings.json / 放置 `AGENTS.md` 与 `presets.json`。用“复制式 v1 + 包”的场景在沙箱全测一遍（`dev/README.md` §双装复现）；再让用户跑 `python3 ~/pinew/tools/pinew.py --dry-run`，看计划，正式迁移，冒烟。
6. 打 `v1.1.0`，更新 `STATE.md` 与 `CHANGELOG.md`，推送；**然后直接开 v1.2**（不等用户“先用几天”，§0 的推进规则）。等他在 WSL 上跑 v1.1 的那几条命令时，沙箱里 v1.2 的设计、原型、mock 验证可以先做起来。

## 9. 资料在哪

- 本仓库：`FACTS.md`（硬结论表）、`ROADMAP.md`、`STATE.md`、`FRICTION.md`、`OLD-REPO-MAP.md`（旧仓库地图）、`tools/`（`collect-state.py` 只读体检；`verify-baseline.py` 基线哈希；`real-gateway/see_body.py` 和 `see-sys.sh` 在**用户的真网关**上看 pi 实际发出去的完整请求体/系统提示，花一个请求的钱，量前缀用；`legacy/` 旧安装器）、`labs/`（8 课的实验脚本，可当模板：`pi_events.py` 把 `--mode json` 事件流整理成表；`cache_lab.py`/`ext_lab.py` 是缓存实验模板；`lesson8/lab8_ext.ts` 是子代理原型）、`bench/arena/`（回归评测台 v0.3，重，只在里程碑时用）、`archive/`（证据：实验日志、旧手册/路线图导出；`.ignore` 让 rg 默认跳过，**只 grep 不通读**）。
- 旧仓库 `github.com/gnk48lb/pi`，固定在提交 `922884b6b6d75f61a405b05f3adb36964a8f4a7d`：原始材料全集，混着教学脚本和日志。需要某个文件时用 `OLD-REPO-MAP.md` 里的路径取，不要克隆来通读。
- **你读不到旧账号的 Claude Docs（用户用另一个账号开了这个 session），一切以本仓库为准。** `archive/docs-2026-11-21/` 是手册和路线图 11-21 中午的导出，缺第 8 课手册章节的定稿和路线图里第 8 课的数据回填；这些内容的去处：结论在 `FACTS.md` §I，原始数据与评分输出在 `archive/evidence/lesson8/`，实验脚本在 `labs/lesson8/`。除此之外没有缺口；真发现缺什么，问用户一句，他可以从旧账号复制给你。

## 10. 还没解决 / 没测过的（别重复发现，也别当成“已知没问题”）

- thinking 档位在 MiniMax 上对输出 token 的影响：**没测出**≠没有；网关是否认 `budget_tokens`、MiniMax 文档里的 `output_config.effort`（默认 max）pi 是否会发：都没验证。
- 改“最后一条用户消息尾部”的缓存语义（K3）：5/10，**未定**，别用。
- 缓存 TTL 15–30 分钟之间没测；`/compact` 手动、`/tree` 没测；`--resume` 超过 30 分钟的成本没测。
- v1 在 MiniMax 上的**任务成功率**没测（评测是 DeepSeek + pi 1.3.0）；安全带在日常命令上的误拦率没测。
- 子代理在真实任务（不是 32 KB 日志）上的回本点没测。
- WSL/Windows 路径边角（`/mnt/c`、CRLF 项目）没测；`edit-fidelity.ts` 在 1.5.0 上没复核。
- `git:` 方式安装（pi 1.5.0，沙箱，11-24，FACTS F10）：`pi install git:github.com/gnk48lb/pinew`、`…@main`、`…@<完整提交号>` 都装得上且 `pi list` 列得出；`@v1.x.y` 要等远端有 tag（§5.17）；带斜杠的分支名没测；用户的 WSL 上没装过。本地路径安装已验证，且**包资源（`extensions/ skills/ prompts/`）改了无需重装**（包按路径加载，不复制），用户 `git pull` 后下次启动 pi 即生效。**但 `AGENTS.md`/`settings.json` 不是包资源**，由安装器放置，`git pull` 同步不到——靠 `pinew.py status`/`update` 的哈希记录与漂移报告（v1.1 要做，约定见 ROADMAP v1.1；`presets.json` 若让 `preset.ts` 读包内默认，也随 `git pull` 生效）。
