# 第 3 课：思考档位（输出才是大头）

讲义（价格差在哪、pi 的档位到底往线上发什么、怎么读结果、自测题和答案）在手册文档第 3 章。这个包里只有练习要用的实验脚本。这一课要回答一个我自己没答案的问题：**在你这个模型上，pi 的 low / medium / high 到底有没有让模型少想？** 你的数据是答案的一半。

## 包里有什么

| 文件 | 干什么 |
|---|---|
| `lesson3/think_lab.py` | 思考档位实验室，只用标准库。同一道题、每个档位各问几次，看网关记的输出 token（含思考）差多少。读你 `~/.pi/agent` 里的网关、模型、密钥和价格 |

不更新 `lesson1/mini_agent.py`：用第 2 课装的那份（脚本里的 `api` 路径要用它）。

## 解压

```bash
cd ~/pi && tar xzf pi-lesson3.tgz && ls lesson3
mkdir -p ~/pi-practice/l3 && cd ~/pi-practice/l3
```

`lesson3` 里应当有 `think_lab.py` 和 `README-lesson3.md`。

## 练习（一条命令，约 10–15 分钟，多数时间在等）

**先猜两个数（30 秒）**：看输出之前，先写下：（a）pi 你现在的配置（下面叫 `pi-budget`）里，“count”那道题，max 档的输出 token 是 low 档的几倍？（b）只改一行配置的版本（`pi-adaptive`）里，“count”题 low 档比 max 档少百分之几？我的九条预测已经写进路线图文档的实验日志（11-06 那一行），在你看到数据之前就锁定了，数据到了对照着看，不改。

**先试一下连得上（约 20 秒，几乎不花钱）**：

```bash
python3 ~/pi/lesson3/think_lab.py --paths api --levels low --tasks ok --reps 1
```

看到一行“输出 … 回 'OK'”就行。如果这一步就失败，把失败那行贴给我，后面的先别跑。

**正式跑**（别改次数：我写的评分规则是按每格 3 次定的）：

```bash
python3 ~/pi/lesson3/think_lab.py 2>&1 | tee -a ex3.log
```

三条路径 × 4 个档位 × 3 个任务 × 3 次 = 108 次请求，同时跑 3 个。屏幕上每完成一次出一行；跑完会打印两张表、一段“判定”，最后是每次运行一行的原始数据。想中途看进度，别关窗口；想停，按 Ctrl+C，已有的结果会照样汇总。

三条路径：

| 路径 | 是什么 | 线上发什么 |
|---|---|---|
| `pi-budget` | 真 pi，你现在的配置 | `thinking={type: enabled, budget_tokens: 2048/8192/16384}`，不发 `output_config.effort` |
| `pi-adaptive` | 真 pi，用一份私有的 `models.json` 副本，给这个模型条目加一行 `compat.forceAdaptiveThinking` | `thinking={type: adaptive}` + `output_config.effort: low/medium/high/max` |
| `api` | 不经过 pi，直接对网关发 OpenAI 接口的请求 | `reasoning_effort: low/medium/high/max` |

三个任务：`ok`（只回一个词，对照题）、`count`（1–1000 里被 3 或 5 整除但不被 15 整除的有多少个）、`subsets`（各位严格递增、数字和为 20 的四位数有多少个）。后两道是要想一想的谜题，答案是固定的，所以顺便能看“少想有没有答错”（答案取回复里最后出现的整数，和标准答案一致才算对，输出被截断算错）。

**交作业（1 分钟）**：把 `~/pi-practice/l3/ex3.log` 发给我，加上你的两个猜测，再用一两句话说哪一点最意外。别删最后那段“数据”，我算分用的就是它。

## 费用和安全

- **花费**：用你 `models.json` 里的价格算。预计 0.05–0.2 美元（取决于模型想多久），花费到 `--max-cost`（默认 0.30 美元）脚本自己停下，并在汇总里说明。
- **不动你的配置**：跑的时候把你的 `models.json`、`auth.json` 复制一份到 `/tmp/think-lab-xxxx`（权限 600，只有你能读），`pi-adaptive` 的那份加一行 `compat.forceAdaptiveThinking`，跑完整个目录删掉。你真正的 `~/.pi/agent` 一个字不动。
- **不会动你的文件**：pi 那两条路径都带着 `--no-tools --no-extensions --no-context-files --no-skills --no-prompt-templates --no-session`，没有工具、不读 AGENTS.md、不存会话，每个请求就是一句系统提示加一道题；运行目录在上面那个临时目录里。
- **没连过你的网关**：这个脚本我在我这边用一个假网关加真的 pi 1.5.0 测过 70 项（线上字段、失败重试、超时、Ctrl+C 清理、不动你的配置、算分），但没连过你的真网关。第一次对着真网关跑的是你。

## 出了问题怎么办

- 「找不到 mini_agent.py」：先解压（上面第一段命令）。
- 「找不到可以运行的 pi」：先 `export PI_BIN=~/.local/bin/pi`，再重跑。
- 很多行显示「失败：…」：把失败的原因原样贴给我，这本身就是数据（比如网关不认某个档位名会返回 400）。失败的运行不计入中位数，汇总里会写出有几次失败。
- 判定里「噪声检查」那行说“超出 0.67–1.5”：意思是这次的随机波动大到测不出 1.5 倍以内的差别。别自己加跑，发给我，我按事先写好的规则决定要不要用 `--reps 5` 补一轮。
- 中途断网或电脑休眠：按 Ctrl+C 停掉，发已有的输出给我；不用从头再来。
