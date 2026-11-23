// try-judge.ts: ask guard-mini.ts what it would decide after each model call. No pi, no model, no money.
//   run:  node try-judge.ts          (Node 22.18+ or 24 runs TypeScript directly)
// It plays 5 model calls in a row (each one "used" 500 output tokens and $0.01) and prints the verdict
// the guard would give after each call, using the numbers in guard-mini.ts's LIMITS.
import { judge, LIMITS } from "./guard-mini.ts";

console.log("LIMITS =", JSON.stringify(LIMITS));
let softSent = false; // budget-guard.ts remembers that it already nudged the model once
for (let call = 1; call <= 5; call++) {
	const { verdict, why } = judge({ output: call * 500, turns: call, usd: call * 0.01, softSent });
	if (verdict === "soft") softSent = true;
	console.log(`after call ${call}: ${verdict.padEnd(4)}  (${why})`);
}
