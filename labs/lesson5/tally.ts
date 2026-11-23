// tally.ts: the 10-line extension from chapter 5 — count the tool calls in one run. Observes only; adds 0 bytes to the request.
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {
	let n = 0; // tool calls so far
	pi.on("tool_call", (e) => {
		n++;
		console.error(`[tally] 第 ${n} 次工具调用：${e.toolName}`);
	});
	pi.on("agent_end", () => console.error(`[tally] 这一轮共 ${n} 次工具调用`));
}
