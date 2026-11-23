# 第 0 课：把 v1 装进你的 pi

这个包里有两样东西：`v1/`（round 1–4 反复实测过的那一份配置，12 个文件 + `settings.json` 里的 2 个键），和 `install_v1.py`（把它装进你真正在用的 pi 的安装脚本）。
装之前会先让你看清楚要改什么；装完会逐个文件读回核对；想反悔，一条命令撤销。

## 安装（三步，前两步都不危险）

```bash
cd ~/pi && tar xzf pi-lesson0.tgz && cd lesson0
python3 install_v1.py --dry-run      # ① 只看，不改任何东西
python3 install_v1.py                # ② 真的装
                                     # ③ 关掉正在运行的 pi，重新打开（新配置要重启才生效）
```

① 的输出只需要看三处：

1. **`目标：`** 那一行是不是你的 pi 配置目录（里面应该有你的 `models.json`）。不是的话，设环境变量 `PI_CODING_AGENT_DIR`，或加 `--dest <目录>`；如果它说目录不存在，多半是 pi 的配置放在别处，先别加 `--create`（那只给全新机器用）。
2. **会发生的事**里，每个文件是 `新增` / `替换` / `相同`。`替换` 的文件，旧版本会先原样存进备份。
3. 有没有 `提醒：` 开头的行（最常见的是“你已经有自己的 AGENTS.md”——安装会换成包里的那份，你的旧文件在备份里，`--undo` 可以还原）。

有哪里不对劲就停在 ①，把输出贴给我。

## 装好后怎么确认（1 分钟）

在任意项目文件夹里启动 pi，启动屏应该有这四块（在真的 pi 1.3.0 上跑出来的）：

```
[Context]
  ~/.pi/agent/AGENTS.md

[Skills]
  debug, refactor

[Prompts]
  /commit, /explain, /init, /plan, /review

[Extensions]
  budget-guard.ts, preset.ts, safety-gate.ts
```

右下角的模型名后面是 `• low`（思考档位）。看不到这些，多半是没重启 pi，或 pi 读的不是这个目录：
`python3 install_v1.py --status` 会逐个文件告诉你“已装 / 没装 / 被改过”。

## 它装了什么

| 文件 | 它是什么 | 占提示前缀 | 怎么关 |
|---|---|---|---|
| `settings.json` 的 `defaultThinkingLevel: low` | 默认思考档位（v1/README.md 里记录的实测：比默认便宜约四成、快约三成五）；pi 里按 `Shift+Tab` 只改本次会话 | 0 | 删这个键；单次 `pi --thinking high` |
| `settings.json` 的 `showCacheMissNotices: true` | 缓存没命中时在界面里提示（为第 2 课准备） | 0 | 删这个键 |
| `AGENTS.md` | 8 条工作约定：先读后改、最小改动、跑测试验证、独立的调用放同一轮、危险操作先问、两次失败就停下汇报、结尾给短报告 | 约 300 token | 删文件，或 `pi --no-context-files` |
| `extensions/safety-gate.ts` | 安全带（不是沙箱）：拦危险 bash（`rm -r`、sudo、`git push`、`reset --hard`、`curl｜sh`…）、写受保护路径、读密钥文件 | 0 | `PI_SAFETY_GATE=off pi`，或删文件 |
| `extensions/budget-guard.ts` | 防失控：一条消息内超限就提醒收尾，再超限就中止 | 0 | `PI_BUDGET_GUARD=off pi`，或删文件 |
| `extensions/preset.ts` + `presets.json` | 官方示例：`pi --preset explore`（只读）、`--preset deep`（高思考）；会话里 `/preset` 切换（会打断一次缓存） | 0 | 删文件 |
| `prompts/*.md` | `/init` `/plan` `/review` `/commit` `/explain`：输入时才展开 | 0 | 删文件 |
| `skills/debug`、`skills/refactor` | 隐藏技能，不进系统提示，用 `/skill:debug <现象>` 才加载 | 0 | 删目录 |

“占提示前缀”这一列是整套设计的底线：pi 便宜是因为每次请求开头那一大段（前缀）能被缓存，所以凡是每次都要塞进前缀的东西都要精打细算。扩展是代码，不是文字，所以是 0。

## 10–20 分钟练习（全部在一个练习文件夹里，不碰你的真项目）

```bash
mkdir -p ~/pi-practice/old && cd ~/pi-practice && echo hi > old/x.txt && pi
```

**A. 让它做件小事（3 分钟）**：说“写一个 hello.py，打印 hello，然后运行它”。看它是不是先读、再写、再运行，最后给一份“改了什么 / 怎么验证的 / 还剩什么”的短报告（AGENTS.md 的最后一条）。

**B. 安全带（5 分钟）**：说“请执行 `rm -rf old`”。会弹出：

```
Safety gate: bash command

  rm -rf old

Reason: recursive delete

→ Allow once
  Allow "rm-recursive" for the rest of this session
  Deny
```

先选 `Deny`：pi 会被告知“用户拒绝了，别重试，问用户想怎么办”，`old/` 还在。再来一次选 `Allow once`：`old/` 被删掉。注意弹窗出现时命令**还没有执行**——这就是它值钱的地方。

**C. 思考档位和缓存（5 分钟）**：看底栏那一行，`↑` 是累计输入 token，`↓` 是累计输出，`R` 是缓存读取，`CH` 是最近一次请求的缓存命中率，`$` 是花费（models.json 里写了价格才有）。问同一个小问题两次，中间按 `Shift+Tab` 把档位切到 high 再问一次，对比 `↓` 的增量。你的网关如果支持缓存，从第二条消息起 `CH` 会明显升高——这就是第 2 课要讲的“前缀 = 钱”。

**D. 撤销再装（3 分钟）**：退出 pi，运行 `python3 install_v1.py --undo`，重新打开 pi，启动屏回到装之前的样子；再 `python3 install_v1.py` 装回来。

## 护栏的默认值（可能会“打扰”你的地方）

- **safety-gate**：交互模式下弹窗问你；非交互模式（`pi -p`）没人能回答，就直接拒绝并把原因告诉模型。`rm -rf build`、`rm -rf dist` 这类清理可再生目录的命令直接放行。它是安全带，不是沙箱：有心绕过的模型能绕过，真要隔离请把 pi 放进容器或虚拟机。
- **budget-guard**：“一次”指你发的一条消息引出的全部工作。软限制（输出 4 万 token / 30 次模型调用 / 1 美元）提醒模型收尾；硬限制（8 万 / 60 次 / 3 美元）中止本次，**再发一条消息就能继续**。美元只在 models.json 里写了价格时才算。长任务被它停下是正常的，不是 pi 坏了；觉得太紧就改 `~/.pi/agent/extensions/budget-guard.ts` 开头的 `LIMITS`。

## 撤销

```bash
python3 install_v1.py --undo
```

只动“上一次安装”装的东西：新增的文件删掉、被替换的还原、`settings.json` 里包加的键拿掉。装完以后**你自己改过**的文件不碰（它会告诉你哪些保留了）；如果 pi 或你后来又改过 `settings.json`，只拿掉包加的键，其余原样。备份文件夹（`~/.pi/agent/.v1-backup-<时间>/`）撤销后也留着，想删自己删。

## 常见情况

- **安装被打断**（Ctrl+C、断电）：再运行一次 `python3 install_v1.py` 会接着装完；想回到原样就 `--undo`。
- **“settings.json 不是合法的 JSON”**：你的 `settings.json` 里有注释或多余的逗号，pi 自己也读不了它。先修好再装；脚本在改任何东西之前就会停下。
- **`models-store.json`**：pi 自己生成的文件，与本包无关，不用管。
- **`v1/install.sh`**：旧安装脚本，别用（遇到格式不对的 `settings.json` 会装到一半，也没有一键撤销）。它留在包里，只是为了让 `v1/` 和实测过的版本逐字节相同；`install_v1.py` 会拒绝安装被改过的包（除非你加 `--allow-modified-pack`）。
- **`/mcp`、`/llama` 命令**：pi 1.3.0 自带，不是本包装的。

## 诚实的说明：v1 带来了什么

round 4 的结论是：整套 v1 通过了“不比空白配置差”的检验，但**没有测出提升**（开发集的任务两边都全部通过，144/144，测量噪声 15–17%，量不出差别）。所以装它的理由不是“pi 变聪明了”，而是：一条安全带，一个防失控的保险（round 4 的运行里安全带和预算护栏一次都没触发过——它们是保险，不是加速器），一个省钱的思考档位，以及——最重要的——一个可以逐件拆开、逐件自己量一遍的底座。后面 8 课就是这样一件一件讲的。
