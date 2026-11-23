# experimental/ — 不在 v1 里、不会被 `pi install` 加载的东西

`package.json` 里 `pi.extensions` 只指向 `./extensions`，所以这里的文件**不会**被包加载（`dev/seed-check.sh` 里有检查）。要试就 `pi -e experimental/<文件>.ts`，别复制进 `extensions/` 而不量前缀、不写 CHANGELOG。

| 文件 | 做什么 | 状态 |
|---|---|---|
| `edit-fidelity.ts` | 拦住会**悄悄弄坏文件**的 `edit`/`write`：非 UTF-8 文件被改成 U+FFFD、混合换行被统一、对 CRLF 文件 `write` 变成 LF。动机：[DS, pi 1.3.0] suite1 里 pi 的 edit 失败约 12%（5/42，Claude Code 1/86）（FACTS J6） | 读 **pi 1.3.0** 源码写的；**没在 1.5.0 上复核**；MiniMax 上的失败率没测。装进 `extensions/` 之前：先在 1.5.0 + 模拟网关上跑通，再让用户用真任务量一下失败率（路线图“其它”一行） |
