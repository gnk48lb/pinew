// lab8_ext.ts —— 第 8 课实验用的扩展（一个文件，三件事）：
//   1. delegate 工具：开一个 pi 子进程去读文件，只把它的最终回答还给主会话；把子进程的用量放进工具结果的 usage 字段，交给 pi 汇总。
//   2. 回忆题关工具：LAB8_NO_TOOLS=7 表示第 7 句里任何工具调用都被拦下（模型只能凭上下文回答）。
//   3. 强制压缩：LAB8_COMPACT_AFTER=3 表示第 3 句跑完后，等压缩做完再开始下一句（不靠 token 阈值）。
// 环境变量（脚本给每个运行设）：LAB8_LOG（旁路日志文件）、LAB8_CHILD_TOOLS、LAB8_REPORT_USAGE。
import { spawn } from "node:child_process";
import { appendFileSync, existsSync } from "node:fs";
import { basename } from "node:path";
import { Type } from "typebox";
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";

const LOG = process.env.LAB8_LOG || "";
const CHILD_TOOLS = process.env.LAB8_CHILD_TOOLS || "read";
const REPORT_USAGE = process.env.LAB8_REPORT_USAGE !== "0";
const NO_TOOLS = new Set((process.env.LAB8_NO_TOOLS || "").split(",").filter(Boolean).map(Number));
const COMPACT_AFTER = Number(process.env.LAB8_COMPACT_AFTER || "0");
const mark = (o: Record<string, unknown>) => {
  if (!LOG) return;
  try { appendFileSync(LOG, JSON.stringify({ t: Date.now(), ...o }) + "\n"); } catch { /* 日志写不进去不影响实验 */ }
};

// 找到 pi 自己：和官方 subagent 示例同一个办法
function piInvocation(args: string[]): { command: string; args: string[] } {
  const script = process.argv[1];
  if (script && !script.startsWith("/$bunfs/root/") && existsSync(script)) return { command: process.execPath, args: [script, ...args] };
  if (!/^(node|bun)(\.exe)?$/.test(basename(process.execPath).toLowerCase())) return { command: process.execPath, args };
  return { command: "pi", args };
}

const zero = () => ({ input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } });

const delegate = defineTool({
  name: "delegate",
  label: "Delegate",
  description:
    "Hand a self-contained task to a helper agent that starts with an empty context and can read files. " +
    "Only the helper's final answer comes back to you; everything it reads stays out of your context. " +
    "The helper cannot see this conversation, so put everything it needs in `task`: file paths and exactly what to report.",
  parameters: Type.Object({
    task: Type.String({ description: "Complete, self-contained instructions for the helper." }),
  }),
  async execute(_id, params, signal, _onUpdate, ctx) {
    const args = ["--mode", "json", "-p", "--no-session", "--no-extensions", "-ns", "-np", "-nc"];
    if (ctx.model) args.push("--model", `${ctx.model.provider}/${ctx.model.id}`);
    if (ctx.thinkingLevel) args.push("--thinking", ctx.thinkingLevel);
    args.push("--tools", CHILD_TOOLS, params.task);

    const total = zero();
    const reqs: any[] = [];
    const calls: { tool: string; args: unknown }[] = [];
    const results: { tool: string; chars: number; isError: boolean }[] = [];
    let final = "";
    let stop = "";
    let err = "";
    let stderr = "";
    const t0 = Date.now();

    const code = await new Promise<number>((resolve) => {
      const inv = piInvocation(args);
      const proc = spawn(inv.command, inv.args, { cwd: ctx.cwd, shell: false, stdio: ["ignore", "pipe", "pipe"] });
      let buf = "";
      const onLine = (line: string) => {
        if (!line.trim()) return;
        let ev: any;
        try { ev = JSON.parse(line); } catch { return; }
        if (ev.type !== "message_end" || !ev.message) return;
        const m = ev.message;
        if (m.role === "toolResult") {
          let chars = 0;
          for (const part of m.content || []) if (part.type === "text") chars += (part.text || "").length;
          results.push({ tool: m.toolName || "", chars, isError: !!m.isError });
          return;
        }
        if (m.role !== "assistant") return;
        const u = m.usage;
        if (u) {
          total.input += u.input || 0; total.output += u.output || 0;
          total.cacheRead += u.cacheRead || 0; total.cacheWrite += u.cacheWrite || 0;
          total.totalTokens += u.totalTokens || 0;
          for (const k of ["input", "output", "cacheRead", "cacheWrite", "total"] as const) total.cost[k] += u.cost?.[k] || 0;
          reqs.push({ input: u.input || 0, cacheRead: u.cacheRead || 0, cacheWrite: u.cacheWrite || 0, output: u.output || 0, totalTokens: u.totalTokens || 0, cost: u.cost?.total || 0 });
        }
        let text = "";
        for (const part of m.content || []) {
          if (part.type === "toolCall") calls.push({ tool: part.name, args: part.arguments });
          if (part.type === "text") text = part.text;
        }
        final = text;
        if (m.stopReason) stop = m.stopReason;
        if (m.errorMessage) err = m.errorMessage;
      };
      proc.stdout.on("data", (d) => { buf += d.toString(); const ls = buf.split("\n"); buf = ls.pop() || ""; for (const l of ls) onLine(l); });
      proc.stderr.on("data", (d) => { stderr += d.toString(); });
      proc.on("close", (c) => { if (buf.trim()) onLine(buf); resolve(c ?? 0); });
      proc.on("error", () => resolve(1));
      if (signal) {
        const kill = () => { proc.kill("SIGTERM"); setTimeout(() => proc.kill("SIGKILL"), 5000); };
        if (signal.aborted) kill(); else signal.addEventListener("abort", kill, { once: true });
      }
    });

    const failed = code !== 0 || stop === "error" || stop === "aborted";
    mark({ ev: "delegate", exit: code, stop, ms: Date.now() - t0, task: params.task, calls, results, requests: reqs, finalChars: final.length, err: err || undefined, stderr: failed ? stderr.slice(0, 300) : undefined });
    const text = failed ? `Helper failed: ${err || stderr.slice(0, 500) || final || "(no output)"}` : final || "(no output)";
    const res: any = { content: [{ type: "text", text }], details: { exit: code, turns: reqs.length, calls, usage: total } };
    if (failed) res.isError = true;
    if (REPORT_USAGE) res.usage = total;
    return res;
  },
});

export default function (pi: ExtensionAPI) {
  pi.registerTool(delegate);

  let msg = 0; // 第几句（从 1 数；每个 -p 参数一句）
  pi.on("before_agent_start", async (event) => {
    if (event.messageType === "user") { msg++; mark({ ev: "msg", i: msg }); }
    return undefined;
  });

  // 回忆题：这一句里任何工具调用都拦下，模型只能凭上下文回答
  pi.on("tool_call", async (event) => {
    if (!NO_TOOLS.has(msg)) return undefined;
    mark({ ev: "blocked", i: msg, tool: event.toolName });
    return { block: true, reason: "Tools are switched off for this question. Answer from memory; write UNKNOWN for anything you do not know." };
  });

  // 强制压缩：第 N 句跑完后做一次，等它做完再开始下一句
  let settled = 0;
  pi.on("agent_settled", async (_e, ctx) => {
    settled++;
    if (!COMPACT_AFTER || settled !== COMPACT_AFTER) return;
    const t0 = Date.now();
    await new Promise<void>((resolve) => {
      ctx.compact({
        onComplete: () => { mark({ ev: "compact", ok: true, ms: Date.now() - t0 }); resolve(); },
        onError: (e: Error) => { mark({ ev: "compact", ok: false, ms: Date.now() - t0, err: String(e && e.message) }); resolve(); },
      });
    });
  });
}
