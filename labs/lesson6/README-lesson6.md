# 第 6 课：预设与 MCP（MCP 的四种露出方式各让每个请求多几个 token；会话中途换预设，哪些改动会让缓存失效）

讲义（预设是什么、MCP 的四种露出方式、codemode 怎么用、中途换预设的代价、预设和 MCP 碰在一起的那个坑、自测题和答案）在手册文档第 6 章。这个包里只有练习要用的东西。这一课要回答三个问题：**MCP 服务器的四种露出方式（codemode、deferred、direct、hidden），各让每个请求多几个 token？** **会话中途用 `/preset` 换预设，哪些改动会让缓存失效，下一句能不能恢复？** **模型实际用不用得上这几种露出方式的 MCP 工具？** 你的数据是答案的一半。

## 包里有什么

| 文件 | 干什么 |
|---|---|
| `lesson6/preset_mcp_lab.py` | 实验室，只用标准库。读你 `~/.pi/agent` 里的网关、模型、密钥和价格。`--mine-mcp` 可以量你自己的 MCP 配置，`--demo-preset` 演示中途换预设时模型看到的工具名单 |
| `lesson6/mcp/mini_mcp.js` | 我写的迷你 MCP 服务器：一个文件、约 12 KB、不依赖任何包，用 node 跑。有 40 个笔记工具的模板（环境变量 `MINI_MCP_TOOLS` 决定给几个），其中 `list_notes`、`get_note`、`search_notes`、`list_tags`、`note_stats` 五个真的能用（一本固定的 8 条笔记），其余的调用只回一句 “not implemented in this demo”。它只读标准输入、只写标准输出，不联网，不读你的文件（只有你自己设了 `MINI_MCP_LOG`，它才会往那个日志文件里追加一行行记录） |
| `lesson6/v1-extensions/preset.ts` | v1 的 `preset.ts` 原样副本，和你 v1 里的逐字节相同。第二部分用它提供 `--preset` 旗标和 `/preset` 命令 |

这些文件不会装进你的 pi：脚本只在每次运行时用 `-e` 和一份私有的 `mcp.json` 把它们点名交给那一个 pi 进程，你的 `~/.pi/agent` 里什么都不会多。需要 `PATH` 里有 `pi`（1.5.0）和 `node`；不需要第 1–5 课的脚本。

## 解压

```bash
cd ~/pi && tar xzf pi-lesson6.tgz && ls lesson6
mkdir -p ~/pi-practice/l6 && cd ~/pi-practice/l6
python3 ~/pi/lesson6/preset_mcp_lab.py --dry-run     # 不调模型、不花钱：核对包是否完整，起一遍迷你服务器，打印设计指纹和 95 次运行的顺序
```

`ls lesson6` 应当有 `README-lesson6.md  mcp  preset_mcp_lab.py  v1-extensions`。`--dry-run` 第一行里的“设计指纹”应当和路线图文档里第 6 课那一行末尾的交付记录一致。

## 练习（一条命令，约 25–30 分钟，多数时间在等）

**先猜两个数（30 秒）**，看输出之前写下来：（a）在 12 个工具的服务器上，codemode 比 direct 每个请求多（或少）多少 token？（b）会话中途只改思考档位切一次预设（下面第二部分的 `PT`），下一句会不会整段重付（没命中的输入在 1,800 token 以上算“整段重付”）？我的 27 条预测已经写进路线图文档的实验日志（11-14 那一行），在你看到数据之前就锁定了，数据到了对照着看，不改。

**正式跑**（别改次数：我写的评分规则是按这个设计定的）：

```bash
python3 ~/pi/lesson6/preset_mcp_lab.py 2>&1 | tee -ia ex6.log
```

分三部分，同一条命令。每次运行都是一个独立的 pi 进程，一个一个跑，不并行。命令里的 `-i` 别漏：它让 `tee` 在你按 Ctrl+C 时不跟着退出；不加的话，Ctrl+C 会把 `tee` 一起打断，脚本最后的汇总和“数据”就写不进 `ex6.log`。

**第一部分 size**：10 种配置共 40 次（各 4 次），每次只问一句“Reply with exactly the single word: OK”，读发出去的请求里总共有多少输入 token。每个臂比对照 `E0` 多出来的 token，就是那种露出方式的价钱。服务器是包里的迷你服务器，名字叫 `notes`：

| 臂 | 配置 |
|---|---|
| `N0` | 一个扩展都不装（对照一） |
| `E0` | 装上 MCP 的三个内置扩展，但不配服务器（对照二，别的臂都跟它比） |
| `C12`、`C30` | codemode：服务器给 12 / 30 个工具 |
| `D12`、`D30` | deferred：12 / 30 个工具 |
| `X3`、`X12`、`X30` | direct：3 / 12 / 30 个工具 |
| `H12` | hidden：12 个工具 |

**第二部分 switch**：6 种配置共 37 次，每次运行一个 pi 进程连问三句，第 1 句带一段约 16,000 字符（3,000 多个 token）的固定参考文本，第 1 句和第 2 句之间用 `-p` 的一条消息 `/preset 名字` 切换预设。量的是**第 2、3 句里有多少输入 token 没有命中缓存**。预设是实验用的 5 个（不是你的 v1 预设）：`lab-base`（思考 low、工具 read、说明 A）、`lab-think`（只把思考改成 high）、`lab-tools`（只把工具改成 read、grep、find、ls）、`lab-instr`（只把说明 A 换成一段 281 字符的说明 B）、`lab-all`（三样都改）：

| 臂 | 次数 | 切换 |
|---|---|---|
| `W0` | 8 | 第 1 句后切到 `lab-base`（同一个预设，对照：什么都没改） |
| `PT` | 8 | 切到 `lab-think`（只改思考档位） |
| `PL` | 5 | 切到 `lab-tools`（只改工具表） |
| `PI` | 5 | 切到 `lab-instr`（只改说明） |
| `PA` | 5 | 切到 `lab-all`（三样全改） |
| `PR` | 6 | 往返：第 1 句后切到 `lab-all`，第 2 句后切回 `lab-base`，再问第 3 句 |

**第三部分 use**：4 种配置共 18 次，每次运行一个 pi 进程问一句话，模型可以调工具，一直到它答完。问题是同一句：“In my notes there is an unpaid invoice. Which company is it from and what is its due date? Answer in one short sentence.” 答案在笔记 3 里；搜 “unpaid” 只能得到标题，所以至少还要再调一次 `get_note`。量的是：答对几次、用几个请求、花多少钱。`U0`（3 次，没有服务器）是边界对照，应当一次也答不对；`UX`（direct）、`UD`（deferred）、`UC`（codemode）各 5 次，都是 12 个工具的服务器。

**如果第一部分的 MCP 臂（C、D、X）全部核对失败，或者一个数据都没拿到，第三部分不会开始**：把屏幕上的内容贴给我就行；想强行开始，加 `--force`。

每次运行时脚本都会读 pi 实际发出去的系统提示和事件流，核对它正是这个臂该有的；不对的运行记失败，不进统计。屏幕上每完成一次出一行；跑完会打印几张表，最后是每次运行一行的原始数据。想中途看进度，别关窗口；想停，按 Ctrl+C，已有的结果会照样汇总。

**交作业（1 分钟）**：把 `~/pi-practice/l6/ex6.log` 发给我，加上你的两个猜测，再用一两句话说哪一点最意外。别删最后那段“数据”，我算分用的就是它。

## 三个小练习（选做，不计分，各几分钟；手册第 6 章有读法）

**① 手动和服务器说话（不花钱，3 分钟）**：MCP 服务器就是一个从标准输入读 JSON、往标准输出写 JSON 的程序，一行一条消息。先把四条消息存进一个文件（`initialize` 是“你是谁”，`initialized` 是客户端的“知道了”，`tools/list` 是“有哪些工具”，`tools/call` 是“调这个工具”），再把它喂给迷你服务器：

```bash
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
  '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"get_note","arguments":{"id":3}}}' > req.jsonl
MINI_MCP_TOOLS=12 node ~/pi/lesson6/mcp/mini_mcp.js < req.jsonl | cut -c1-300     # 三行回答，每行截到 300 字符
```

回答是三行（`initialized` 不需要回答）：第 1 行是服务器的自我介绍，第 2 行是工具清单，第 3 行是 `get_note` 的结果。先猜：把 `MINI_MCP_TOOLS=12` 改成 3 或 30，第 2 行会变成多少个字符？再来对答案（这条命令只数每一行有多长，不截断）：

```bash
for n in 3 12 30; do echo -n "$n 个工具："; MINI_MCP_TOOLS=$n node ~/pi/lesson6/mcp/mini_mcp.js < req.jsonl | awk '{printf "第%d行 %d 字符；", NR, length($0)} END {print ""}'; done
```

清单每多一个工具，就多几百个字符：这就是 direct 露出方式的价钱——这些字符每个请求都要重新发一遍。最后把 `req.jsonl` 里的 `get_note` 改成 `delete_note` 再喂一次，看看没实现的工具回什么（`isError` 是 `true`）。

**② 量你自己的 MCP 配置（用包里的服务器不到 0.003 美元）**：

```bash
python3 ~/pi/lesson6/preset_mcp_lab.py --mine-mcp                 # 不带文件名：量包里的迷你服务器（20 个工具）
python3 ~/pi/lesson6/preset_mcp_lab.py --mine-mcp ~/你的/mcp.json   # 量你自己的 mcp.json
```

四种露出方式各跑 2 次（再加一个不配服务器的对照），每次问一句话，打印每种方式让每个请求多几个 token，还会算一个“盈亏点”：你的服务器合起来的工具比这个数少，direct 每个请求更省；比这个数多，codemode 更省。你自己的文件要写成 `{"mcpServers": {"名字": {"command": …, "args": […]}}}`，`args` 里的路径写绝对路径（脚本在另一个临时目录里跑 pi，相对路径会找不到）；要登录（OAuth）的服务器这个脚本不支持。脚本会把每个启用的服务器的 `exposure` 都改成同一种，再各量一次；你的文件本身一个字不改，只是它的内容（包括 `env` 里写的东西）会被抄进私有目录里的 `mcp.json`（权限 600），跑完删掉。

**③ 看中途换预设时模型看到的工具名单（不到 0.002 美元）**：

```bash
python3 ~/pi/lesson6/preset_mcp_lab.py --demo-preset
```

一个 pi 进程连问 3 句，中间 `/preset` 切换两次：第 1 句模型看到 `read` 加 3 个 MCP 工具；切到只写了 `read` 的预设以后，第 2 句里 MCP 的工具都不见了；切到把完整的 `mcp__notes__…` 名字写进 `tools` 的预设以后，第 3 句里它们回来了。这就是手册第 6 章说的“预设和 MCP 碰在一起的坑”。

## 费用和安全

- **花费**：用你 `models.json` 里的价格算。三部分合计预计 0.06–0.20 美元；花费到 `--max-cost`（默认 0.60 美元）脚本自己停下，并在汇总里说明。三个小练习：① 不花钱；③ 只有 3 个请求，不到 0.002 美元；② 十次运行，用包里的服务器不到 0.003 美元；量你自己的配置，工具越多 direct 那一臂越贵（每个工具约多 100 个 token），一般不到 1 美分。
- **不动你的配置**：跑的时候把你的 `models.json`、`auth.json` 复制一份到 `/tmp/pm-lab-xxxx`（目录权限 700，文件权限 600，只有你能读），再放进实验用的 `mcp.json`、`presets.json`，跑完整个目录删掉（Ctrl+C、关终端、被 kill 也会删；被 `kill -9` 强杀的话来不及删，那种情况下手动 `rm -rf /tmp/pm-lab-*`）。你真正的 `~/.pi/agent` 一个字不动，你的 `AGENTS.md`、你装的扩展、你自己的 `mcp.json` 也不会被读到（每个 pi 都带着 `--no-session --no-extensions -ns -np -nc`；实验用的扩展是脚本自己用 `-e` 显式装上的）。
- **模型能用什么**：和以前一样只有 `read`（读文件，不能改文件、不能跑命令），再加这个臂该有的 MCP 入口：`codemode`（模型写一段 JavaScript，在 pi 自带的 QuickJS 沙箱里跑，沙箱里没有文件系统和网络）、`tool_search`，或者 `mcp__notes__…` 开头的工具。迷你服务器只有一本固定的笔记，写笔记的工具都是“没实现”的空壳，不会改任何东西。
- **没连过你的网关**：这个脚本我在我这边用一个假网关（带模拟的前缀缓存）加真的 pi 1.5.0 测过（迷你服务器和扩展真的装上了、请求里多出来的东西对得上、核对失败的处理、失败重试、超时、花费上限、Ctrl+C 和关终端的清理、不动你的配置、解压出来的包本身、用我的评分脚本打分），但没连过你的真网关，也没用过真的模型来答第三部分的问题。缓存到底怎么算、模型会不会用 codemode，是这个实验要量的东西，第一次对着真网关跑的是你。node 我这边是 22，你的是 24，没测过 24。

## 选项（一般不用）

`--only size`、`--only switch`、`--only use` 分开跑三部分之一；`--arms C12,PT` 只测几个臂；`--reps N` 改每个臂的次数；`--model provider/模型id` 换模型；`--max-cost 美元` 改花费上限；`--timeout 秒` 改一次运行最多等多久；`--dry-run` 不调模型；`--force` 见上。正式跑请不要加其中改设计的选项（`--only`、`--arms`、`--reps`、`--model`、`--timeout`）：脚本头一行的“设计指纹”只覆盖脚本里写死的设计（请求、预设、判定标准、各臂的次数），不覆盖你在命令行上加的选项，我算分时按默认设计读。
