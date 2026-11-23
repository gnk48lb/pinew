# pinew

A measured, cache-friendly customization pack for [pi](https://pi.dev) (the terminal coding agent), being iterated from the measured "v1" into a daily-driver coding agent. It is a pi package: extensions, skills, prompt templates, plus config files that an installer places into `~/.pi/agent/`.

pinew 是给 pi（pi.dev 终端 coding agent）做的定制包：在用户自己的 WSL、网关和模型上**实测**过每样东西占多少前缀、怎么断缓存、花多少钱（`docs/FACTS.md`），再一版版往“日常主力”迭代（`docs/ROADMAP.md`）。

## 状态

- 当前版本 **v1.0.0** = 实测过的 v1（字节级原样，`docs/baseline/`）+ pi 包外壳。pi 1.5.0 上验证过包能加载。
- **v1.1.0（安装/迁移/退回 + 自检）还没做。在它做好之前，不要在已经用 `install_v1.py` 复制安装过 v1 的机器上执行 `pi install`**：pi 会因为 `--preset` 标志冲突直接起不来（`pi -ne` 可恢复）。详见 `docs/HANDOFF.md` §5。
- 下一步、已验证/未验证的东西：`docs/STATE.md`。

## v1.0.0 里有什么

| 部件 | 位置 | 作用 | 占前缀 |
|---|---|---|---|
| 工作约定 | `config/AGENTS.md` | 8 条（先读后改、改完验证、不擅自 commit/push、两次失败就停…） | +285 token/请求 |
| 安全带 | `extensions/safety-gate.ts` | 拦危险 bash（`rm -r`、sudo、`git reset --hard`…）、写 `.git/` `.env`、读密钥文件 | 0 |
| 预算护栏 | `extensions/budget-guard.ts` | 一次运行输出 token / 调用次数 / 费用超限时提醒收尾，再超则中止 | 0 |
| 预设 | `extensions/preset.ts` + `config/presets.json` | `pi --preset explore`（只读）、`deep`（高思考） | 0 |
| 提示模板 | `prompts/` | `/init` `/plan` `/review` `/commit` `/explain` | 0（用时 +40） |
| 隐藏技能 | `skills/` | `/skill:debug`、`/skill:refactor`（只能手动触发） | 0（用时 +97） |

前缀数字：MiniMax-M3.1-Flash，pi 1.5.0，用户网关（`docs/FACTS.md` §C）。

## 目录

```
docs/      HANDOFF（交接，先读）· STATE（停在哪）· ROADMAP（排期与验收）· FACTS（硬结论表）· FRICTION（用着别扭的地方）· OLD-REPO-MAP · OPENING_PROMPT · baseline/（v1 基线与哈希）
extensions/ skills/ prompts/     pi 包资源（package.json 的 pi 清单指向这里）
config/    AGENTS.md · settings.json · presets.json（不是包资源，要由安装器放进 ~/.pi/agent/）
tools/     collect-state.py（只读体检）· verify-baseline.py · real-gateway/（在真网关上看 pi 的完整请求体）· legacy/（v1 的旧安装器）
dev/       沙箱开发环境：模拟网关、RPC 列命令、seed-check.sh（12 项检查）
labs/      八节课的实验脚本与冻结评分脚本（模板，不用重做）
bench/     回归评测台 arena v0.3（重；里程碑时当回归闸）
experimental/  不在 v1 里的东西（edit-fidelity.ts）
archive/   原始证据与旧文档导出（只 grep，不通读）
```

## 开发者（给 Claude Code / 新 session）

先读 `.claude/CLAUDE.md` 指的顺序：`docs/HANDOFF.md` → `docs/STATE.md` → `git log -10`。**不要在仓库根放 `CLAUDE.md`/`AGENTS.md`**——pi 在这里运行时会把它们读进系统提示。沙箱搭建：`dev/README.md`。

## 许可

MIT（见 `LICENSE`）。`extensions/preset.ts` 来自 pi 官方示例（MIT）。
