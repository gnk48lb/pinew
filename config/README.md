# config/ — 不是 pi 包资源、必须由安装器放进 `~/.pi/agent/` 的三个文件

pi 包只能带 `extensions/ skills/ prompts/ themes/`。下面三个是**配置**，`pi install` 不会放它们；v1 时代由 `tools/legacy/install_v1.py` 复制进去，v1.1 起由 `tools/pinew.py` 负责（用户改过的不覆盖，只提示）。

| 文件 | 去哪里 | 内容 | 占前缀 |
|---|---|---|---|
| `AGENTS.md` | `~/.pi/agent/AGENTS.md`（pi 的全局上下文文件） | 8 条工作约定：用用户的语言回复、先读后改、改完要验证、独立查询同轮并发、工具间文字≤1 句、不可逆操作先问且不擅自 commit/push、两次失败就停下汇报、收尾短报告 | **+285 token/请求**，每多一条一行规则约 +41（[MM, pi 1.5.0]，FACTS C1/C2） |
| `settings.json` | 合并进 `~/.pi/agent/settings.json`（不要整个覆盖——里面还有 `packages`、默认模型等） | `defaultThinkingLevel: "low"`、`showCacheMissNotices: true` | 0 |
| `presets.json` | `~/.pi/agent/presets.json`（`preset.ts` 只从这里和 `<cwd>/.pi/presets.json` 读） | `explore`（low + 只读四件套 + READ-ONLY 指令）、`deep`（high + 全工具 + DEEP MODE 指令）；**都没写 `model`，所以不换模型** | 切换时断一次缓存（FACTS B5） |

**缺 `presets.json` 时 `pi --preset explore` 静默无效**（HANDOFF §5.2）。这三个文件与 `docs/baseline/v1-SHA256SUMS.txt` 逐字节相同（`python3 tools/verify-baseline.py`）；有意改动要写进 `CHANGELOG.md`。
