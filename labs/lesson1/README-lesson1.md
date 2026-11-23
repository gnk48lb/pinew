# 第 1 课：agent 就是一个循环

讲义（循环图、你的真实数据、自测题和答案）在手册文档里。这个包里是练习要用的三个脚本，外加第 0 课 README 的订正版。

## 包里有什么

| 文件 | 干什么 |
|---|---|
| `lesson1/mini_agent.py` | 约 120 行的迷你 agent，只用标准库；读你 `~/.pi/agent` 里的网关、模型和密钥设置，所以和 pi 用同一个模型 |
| `lesson1/pi_events.py` | 把 `pi -p "任务" --mode json` 的事件流整理成“每一轮一行”的表，开头列出系统提示的各部分 |
| `lesson1/show_messages.py` | 把 `mini_agent.py --save` 存下的对话一条一条打印出来（agent 的全部“记忆”） |
| `lesson0/README-lesson0.md` | 第 0 课 README 的订正版：只改了练习 B。解压时会覆盖旧的那一份，别的文件（包括 `install_v1.py` 和 `v1/`）一个字节都不动 |

## 解压

```bash
cd ~/pi && tar xzf pi-lesson1.tgz && ls lesson1
mkdir -p ~/pi-practice/l1 && cd ~/pi-practice/l1
```

## 练习（10–20 分钟）

**安全提醒**：`mini_agent.py` 会用你的身份真的执行模型写出来的 bash 命令，没有安全带，也没有预算护栏——这是故意的，为了看清循环本身。只在 `~/pi-practice/l1` 里用，不要给它删东西或碰真项目的任务。

**① 读代码（5 分钟，不运行）**：`less ~/pi/lesson1/mini_agent.py`，按 q 退出。找到这四处，各用一句话回答：

- `TOOLS` 和 `run_tool`：模型怎么知道有哪些工具？谁真正执行它们？
- `chat`：一次请求的 body 里发了哪几个字段？
- `main` 里的 `for turn in …`：哪一行让 agent“记住”前面做过的事？循环什么时候结束？
- `run_tool` 里的 `except`：工具出错时程序会崩吗？

**② 跑迷你 agent（3 分钟）**：

```bash
python3 -u ~/pi/lesson1/mini_agent.py "在当前目录写 hello.py，内容是打印 hello；然后运行它确认输出" --save run1.json 2>&1 | tee -a ex1.log
python3 ~/pi/lesson1/show_messages.py run1.json 2>&1 | tee -a ex1.log
```

**③ 同一个任务交给 pi（3 分钟）**：

```bash
rm -f hello.py
pi -p "在当前目录写 hello.py，内容是打印 hello；然后运行它确认输出" --mode json > pi1.jsonl
python3 ~/pi/lesson1/pi_events.py pi1.jsonl 2>&1 | tee -a ex1.log
```

比较两个第 1 轮的“新输入 + 缓存读”：pi 多出来的是哪几部分？

**④ 一个很大的工具结果（5 分钟）**：先猜——三次运行里，“大输出之后那一轮”的新输入哪次最大，大约差几倍？

```bash
T='用 bash 原样运行 seq 1 10000（不要加 head、tail 之类），然后告诉我你看到的最后一行是什么'
python3 -u ~/pi/lesson1/mini_agent.py "$T" --cap 0 2>&1 | tee -a ex1.log
python3 -u ~/pi/lesson1/mini_agent.py "$T" 2>&1 | tee -a ex1.log
pi -p "$T" --mode json | python3 ~/pi/lesson1/pi_events.py 2>&1 | tee -a ex1.log
```

三次依次是：不截断；mini_agent 默认只留前 8000 字符；pi 自己的截断。看两件事：大输出之后那一轮的新输入各是多少；三个版本各自回答的最后一行对不对，为什么（提示：mini_agent 的截断保留开头，pi 的 bash 截断保留末尾）。如果模型自作聪明加了 `| tail`，就在任务里再强调一遍“原样”。

**⑤ 交作业（1 分钟）**：把 `~/pi-practice/l1/ex1.log` 发给我，再用一两句话说你猜得准不准、哪一点最意外。

## 出了问题

- **“读不到密钥”**：你的 `models.json` 里的 `apiKey` 是 `!命令` 形式，`mini_agent.py` 不执行命令。先 `export MINI_API_KEY=你的密钥` 再运行。
- **“连不上网关”**：网关没开，或地址不对。`mini_agent.py` 用的地址就是 `~/.pi/agent/models.json` 里该 provider 的 `baseUrl`，和 pi 一样。
- **“HTTP 4xx”**：网关拒绝了请求，错误正文会跟在后面。常见原因是模型 id 或密钥不对；可以用 `--model provider/模型id` 指定，provider 和模型 id 取自 `models.json`。如果正文里提到 reasoning 或 effort，说明网关不认 `reasoning_effort` 这个字段，加上 `--effort none`（不发这个字段）再运行。
- **“没有读到 JSON 事件”**（`pi_events.py`）：管道里不是 pi 的 JSON 输出。确认命令里有 `-p` 和 `--mode json`。

## 做完发给我什么

`~/pi-practice/l1/ex1.log`，加上一两句话。不用整理，不用写总结。


第 1 课练习跑完了，`ex1.log` 已上传。

workbuddy的deepseek模型免费过期了, 我现在用的是 `minimax-code-global/MiniMax-M3.1-Flash`, 不过如果你觉得还是用deepseek比较好的话我也可以换回来deepseek

关于练习 ④：
我只猜了pi可能对工具结果进行过处理所以新输入可能会少一点, 我不知道bash：保留末尾 2000 行或 50KB(问Gemini他告诉我的)
以下是Gemini的回答:
1. 不截断（`--cap 0`）第 2 轮新输入直接飙到了 29053 token；而 `pi` 截断保留末尾 2000 行（8001~10000），第 2 轮新输入只有 6113 token（差不多 1/5），而且一次就能看到 10000，2 轮就结束了。
2. `mini_agent` 默认截断只留前 8000 字符，模型看不到最后一行，结果它在第 2 轮自己补调了一次 `seq 1 10000 | wc -l; seq 1 10000 | tail` 才拿到 10000（多了 1 轮）；而且最意外的是，它第 2 轮的新输入只有 19、缓存读却有 5122——是不是因为前 8000 字符刚好和上一次 `--cap 0` 运行的前缀撞上了，直接命中了上一次的缓存？

关于升级 pi：
我注意到一个情况，现在 pi 最新版本已经是 1.5.0 了（不只是 1.4.0）。下面是你让我跑的安装方式检查输出

gnk48@LB-Studio:~/pi-practice/l1$ command -v pi; ls -l "$(command -v pi)"; file -L "$(command -v pi)"; readlink -f "$(command -v pi)"; npm ls -g --depth=0 2>/dev/null | grep -i pi-coding-agent || echo "(npm -g 里没有 pi)"
/home/gnk48/.local/bin/pi
lrwxrwxrwx 1 gnk48 gnk48 22 Oct 13 10:35 /home/gnk48/.local/bin/pi -> ../../.pi/agent/bin/pi
/home/gnk48/.local/bin/pi: POSIX shell script, ASCII text executable
/home/gnk48/.pi/agent/bin/pi
(npm -g 里没有 pi)