# bench/ — 回归评测台（重；只在里程碑当“回归闸”用）

`arena/` 是评测台 v0.3（纯标准库 Python ≥3.9）：让 agent 在隔离环境里做同一批编程题，由**封存的隐藏测试**判卷，记录通过率、成本、没走缓存的输入 token、轮数、工具失败、被安全带拦的命令、预算护栏事件、有没有偷看评测文件。题库 18 道（8 开发 + 4 留出 + 6 难题），各题 `sealed.dat` = 隐藏测试 + 参考解（加扰）；`.ignore` 让 rg 跳过 `tasks/`。完整说明：`arena/README.md`（30 KB，查用法时 grep）。

## 它能回答什么、不能回答什么

- **能**回答“这次改动有没有让 agent 变差”（开发题 8×3；同配置两批之间波动 15–17%，所以要先量噪声：基线 ×2 + 候选 ×1）。
- **不能**回答“有没有变好”：题目已饱和（[DS, pi 1.3.0] 开发题 144/144 通过；难题只有 p16 暴露行为差距）。
- 数字全是 [DS, pi 1.3.0]（DeepSeek V4.1 Flash）。**换了模型（MiniMax-M3.1-Flash）阈值要重量**。
- 要在**用户的 WSL** 里跑（要真网关）；开发题每次不到 1 分钟、难题 1–2 分钟；全程不用输入，可断点续跑（同一条命令再跑一次）。先让用户把电脑睡眠设成“从不”。

## 当回归闸怎么用（v2.0；v1.1–v1.4 不需要）

```bash
cd ~/pinew/bench/arena
python3 arena.py setup                      # 一次：读用户的 pi 网关配置，写空白 configs/（含网关占位，不要上传）
python3 arena.py tasks --check --tasks all  # 查题（约 3–5 分钟）
python3 arena.py sandbox                    # 沙箱（Landlock）可用吗？WSL 内核 6.6.87.2 预计可用
# 候选配置 = 一个 v1 布局的目录（settings.json、AGENTS.md、extensions/、prompts/、skills/、presets.json），叠在空白基线之上：
python3 arena.py suite --label base-a --agents pi --tasks dev --reps 3 --pi-thinking low --no-pack
python3 arena.py suite --label cand   --agents pi --tasks dev --reps 3 --pi-thinking low --pi-config <候选目录> --no-pack
python3 arena.py report --label cand
```

`--pi-config` 需要 v1 布局的目录：pinew 里 `config/*` 在 `config/`、其余在仓库根，**需要一个小脚本把它们拼成一个临时目录**（v2.0 的活；`tools/verify-baseline.py` 里有 v1 布局与本仓库布局的对应表）。`round4.sh` 是第 4 轮的完整批处理（路径沿用旧布局 `~/pi/v1`、`~/pi/variants/*`，**不能原样跑**，只当“长批处理怎么写”的参考；`README-round4.md` 同）。

自测（模拟网关，不花钱）：`python3 arena.py selftest`（`arena_selftest.py` + `arena_selftest_v03.py`，387 项）。2026-11-22 在沙箱（pi 1.5.0）里从 pinew 的副本跑过：`387 checks, 0 failed`；`python3 arena.py tasks` 列出 18 道题。
