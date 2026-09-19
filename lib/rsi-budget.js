import { performance } from "node:perf_hooks";

/** Bound credential resolution as well as inference; late work is observed, not leaked. */
export async function withinAssessmentBudget(operation, callerSignal, timeoutMs) {
	const started = performance.now();
	const controller = new AbortController();
	const fail = code => {
		const error = new Error(code === "TYPESAFE_TIMEOUT" ? "RSI assessment timed out" : "RSI assessment cancelled");
		error.code = code;
		if (!controller.signal.aborted) controller.abort(error);
	};
	const cancel = () => fail("TYPESAFE_CANCELLED");
	callerSignal?.addEventListener("abort", cancel, { once: true });
	if (callerSignal?.aborted) cancel();
	const timer = setTimeout(() => fail("TYPESAFE_TIMEOUT"), timeoutMs);
	let onAbort;
	const cancelled = new Promise((_, reject) => {
		onAbort = () => reject(controller.signal.reason);
		controller.signal.addEventListener("abort", onAbort, { once: true });
		if (controller.signal.aborted) onAbort();
	});
	try {
		const task = Promise.resolve().then(() => {
			controller.signal.throwIfAborted();
			return operation(controller.signal, () => Math.max(1, Math.floor(timeoutMs - (performance.now() - started))));
		});
		return await Promise.race([task, cancelled]);
	} finally {
		clearTimeout(timer);
		callerSignal?.removeEventListener("abort", cancel);
		controller.signal.removeEventListener("abort", onAbort);
	}
}
