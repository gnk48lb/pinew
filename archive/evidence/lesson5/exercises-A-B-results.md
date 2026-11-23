# 第 5 课练习 A、B 的结果（2026-11-14）

环境：pi 1.5.0，模型 magpie / minimax-code-global/MiniMax-M3.1-Flash，设计指纹 2874359bba68。
练习目录 `~/pi-practice/l5`（WSL Ubuntu）。`try-judge.ts` 用的是包内原件（sha256 642f146e…4788cc）。

---

## 练习 A · 看守卫怎么判 —— 做完，与手册一致

### 1) 离线跑 judge()（不花钱）

`guard-mini.ts` = `v1-extensions/budget-guard.ts` 的副本，只改了 `LIMITS` 两行：
`softTurns: 30 → 2`、`hardTurns: 60 → 4`（sha256 aaf2c41b…dbc8e；文件留在本目录）。

```
$ cd ~/pi-practice/l5 && node try-judge.ts
LIMITS = {"softOutputTokens":40000,"hardOutputTokens":80000,"softTurns":2,"hardTurns":4,"softUsd":1,"hardUsd":3}
after call 1: ok    (500 output tokens, 1 model calls, $0.01)
after call 2: soft  (1,000 output tokens, 2 model calls, $0.02)
after call 3: ok    (1,500 output tokens, 3 model calls, $0.03)
after call 4: hard  (2,000 output tokens, 4 model calls, $0.04)
after call 5: hard  (2,500 output tokens, 5 model calls, $0.05)
```

与手册第 5 章第 9 节给出的容器输出**逐字一致**。第 3 次是 ok 不是 soft：软提醒只发一次
（`softSent`）；第 5 次的 hard 在真实运行里看不到，因为第 4 次就停了。

### 2) 让 pi 真的沿 chain/ 的 6 个文件走（跑了两次，两次同一个结局）

第一次：

```
[budget-guard] soft limit reached (88 output tokens, 2 model calls, $0.00)
[budget-guard] stopped this run (372 output tokens, 4 model calls, $0.00)
This operation was aborted
```

第二次（网关修好之后重跑）：

```
[budget-guard] soft limit reached (59 output tokens, 2 model calls, $0.00)
[budget-guard] stopped this run (121 output tokens, 4 model calls, $0.00)
This operation was aborted
```

两次都落在手册的**情形（一）：没理提醒，一路读下去**（与容器里假模型的表现相同）。
- 第 2 次回答之后触发 soft，提醒作为一条 steer 消息追加进历史；
- 模型没照做，第 4 次回答之后触发 hard，`ctx.abort()` 叫停；
- `This operation was aborted` 是 `-p` 模式下被叫停的样子（退出码 1）；
- **钱付了 4 次请求**：第 N 次调用的检查发生在第 N 个回答付完钱之后（手册第 7 节第 1 点）。

结论：这个模型**不听**软提醒，两次独立运行一致；`budget-guard.ts` 的 soft 对它是"多一条消息"，不是"叫停信号"，真正起作用的是 hard + `ctx.abort()`。

---

## 练习 B · 数工具调用 —— 做完，与手册一致

### 1) tally.ts

第一轮没做成：`cp tally-skeleton.ts tally.ts` 之后两处 TODO 没补（sha256 5ec129f4…2ecf54，
与骨架逐字节相同），所以 pi 只打 `FINISHED`、`--mine` 只报"没有任何输出"的警告。
后来又误跑了一次 `cp tally-skeleton.ts tally.ts`，把补好的文件覆盖回骨架。

补好之后的 `tally.ts`（本目录，sha256 2a4ec2a9…6b11）：

```ts
export default function (pi: ExtensionAPI) {
	let n = 0; // tool calls so far
	pi.on("tool_call", (e) => {
		n++;
		console.error(`[tally] 第 ${n} 次工具调用：${e.toolName}`);
	});
	pi.on("agent_end", () => console.error(`[tally] 这一轮共 ${n} 次工具调用`));
}
```

### 2) 让 pi 沿 chain/ 走（退出码 0）

```
$ pi -ne -e ./tally.ts --thinking low -p "Read chain/start.txt and do what it says, one file at a time. Stop when a file says DONE."
[tally] 第 1 次工具调用：read
[tally] 第 2 次工具调用：read
[tally] 第 3 次工具调用：read
[tally] 第 4 次工具调用：read
[tally] 第 5 次工具调用：read
[tally] 第 6 次工具调用：read
[tally] 这一轮共 6 次工具调用
FINISHED
```

6 个文件、6 次 `read`，与手册容器里数出的 6 一致。注意与练习 A 的对照：同一条命令、同一句话，
装上守卫时模型读到第 4 次就被叫停，装上 `tally` 时一路读完 6 个——差别完全来自扩展，
不是模型自己改了主意。

### 3) 量它让每个请求多几个 token（$0.0002）

```
[  1/4] S0    #1  总输入 T=971（命中 512）   2.0 秒  $0.000078
[  2/4] MINE  #1  总输入 T=971（命中 768）   1.4 秒  $0.000044
[  3/4] S0    #2  总输入 T=971（命中 768）   1.6 秒  $0.000044
[  4/4] MINE  #2  总输入 T=971（命中 768）   1.4 秒  $0.000048

== 你的扩展让每个请求多几个 token
  不装扩展：总输入 T = 971/971（两次相同）
  装你的扩展：总输入 T = 971/971（两次相同）
  差 D = +0 token（每个请求；命中缓存时多花 $0.0000000，没命中时多花 $0.0000000）
  工具名单：read → read
  系统提示分段（字符数）的变化：没有
  你的扩展在 stderr 里说了：[tally] 这一轮共 0 次工具调用
本次花费 $0.0002
```

D = +0 且 stderr 有输出 —— 这次是**有效**的对照：扩展真的装上了（脚本截到了它打的行），
而它一个字节都没往请求里加。`0 次工具调用` 是对的：这一句只让模型回一个词，没调工具。

与主实验呼应：S1（订阅 13 种事件、什么都不返回）在两部分的 D 也是 0，tally 属于同一类。

---

## 本目录新增/更新的文件

| 文件 | 说明 |
|---|---|
| `ex5.log` | 主实验日志（61 次运行、0 失败、$0.0376），sha256 b5e44c97…d6e8c，与 `~/pi-practice/l5/ex5.log` 一致 |
| `guard-mini.ts` | 练习 A 用的守卫副本（softTurns=2、hardTurns=4），sha256 aaf2c41b…dbc8e |
| `tally.ts` | 练习 B 补好的扩展，sha256 2a4ec2a9…6b11 |
| `exercises-A-B-results.md` | 本文件 |
