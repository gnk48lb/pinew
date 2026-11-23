// tally-skeleton.ts: the 10-line tally.ts from chapter 5 with the two handler bodies left blank.
// Copy it to tally.ts and fill in the two TODO lines (or type the whole thing from the handbook instead).
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

export default function (pi: ExtensionAPI) {
	let n = 0; // tool calls so far
	pi.on("tool_call", (e) => {
		// TODO 1: add 1 to n, then print one line to stderr: the number and e.toolName   (console.error(...))
	});
	// TODO 2: on "agent_end", print one line to stderr: how many tool calls this run used
}
