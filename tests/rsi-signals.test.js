import test from "node:test";
import assert from "node:assert/strict";
import { Context } from "@deepseek-ai/cordis";
import { createSessionSignals, SIGNAL_LIMITS } from "../lib/rsi-signals.js";

// Inspected Host contracts are emit(exec,result) and emit({agent}), NOT a
// serialized event log. Mock ctx.on owns its disposer just as Cordis does.
function fiber() {
	const listeners = new Map(), effects = [];
	const definitions = new Map(["bash", "read", "run_code", "memory_plan", ...Array.from({ length: 40 }, (_, i) => `tool_${i}`)].map(name => [name, Object.freeze({ name })]));
	const tools = { get(name, scope) { assert.ok(scope && typeof scope === "object"); return definitions.get(name); } };
	const ctx = {
		get(name) { return name === "tools" ? tools : undefined; },
		effect(factory) { const dispose = factory(); effects.push(dispose); return dispose; },
		on(name, listener) {
			const set = listeners.get(name) ?? new Set(); listeners.set(name, set); set.add(listener);
			return ctx.effect(() => () => set.delete(listener));
		},
	};
	return {
		ctx, listeners, definitions, tools,
		emit(name, ...args) { for (const listener of listeners.get(name) ?? []) assert.equal(listener(...args), undefined); },
		stop() { for (const dispose of effects.splice(0).reverse()) dispose(); },
	};
}
const success = () => Object.freeze({ isError: false, value: { result: "ok" }, content: [] });
function failure(code, message = "generic failure") {
	return Object.freeze({ isError: true, error: Object.freeze({ message, ...(code === undefined ? {} : { info: Object.freeze({ name: "HarnessError", code }) }) }), content: [] });
}
function fixture(config = { rsiTelemetryEnabled: true }) {
	const host = fiber(), signals = createSessionSignals(host.ctx, config);
	const agent = Object.freeze({ id: "session-current" });
	let number = 0;
	function emit(result = success(), options = {}) {
		const callId = options.callId ?? `call-${++number}`;
		const exec = Object.freeze({ name: "bash", callId, rootCallId: callId, agent, arguments: {}, signal: new AbortController().signal, ...options });
		host.emit("tools/result", exec, result);
	}
	return { ...host, signals, agent, result: emit, snapshot: () => signals.snapshot(agent) };
}

test("capture is disabled by default and only literal operator true opts in", () => {
	for (const value of [undefined, false, "true", 1, null]) {
		const f = fixture({ rsiTelemetryEnabled: value });
		assert.equal(f.listeners.size, 0);
		f.result(failure("INVALID_ARGS"));
		const snapshot = f.snapshot();
		assert.equal(snapshot.status, "disabled");
		assert.equal(snapshot.active, false);
		assert.equal(snapshot.totals.total, 0);
		assert.equal(snapshot.rates.failed, null);
		assert.equal(snapshot.network_called, false);
		assert.equal(snapshot.permission_effect, "none");
	}
	assert.throws(() => createSessionSignals({}, { rsiTelemetryEnabled: true }), /Fiber-owned/);
});

test("real result signatures distinguish structured failure, abort and denial from success", () => {
	const f = fixture();
	f.result(success());
	for (const code of ["UNKNOWN_TOOL", "INVALID_ARGS", "INVALID_TOOL_OUTPUT", "TOOL_TIMEOUT", "FS_STALE_VERSION"]) f.result(failure(code));
	for (const code of ["ABORTED", "ABORTED_BEFORE_DISPATCH", "FS_ABORTED"]) f.result(failure(code));
	for (const code of ["FS_PERMISSION_DENIED", "FS_SANDBOX_DENIED"]) f.result(failure(code));
	const s = f.snapshot();
	assert.deepEqual(s.totals, { total: 11, success: 1, failed: 5, aborted: 3, denied: 2, unknown: 0 });
	assert.equal(s.rates.denominator, 11);
	assert.equal(s.rates.aborted, 3 / 11);
	assert.equal(s.recent[6].failure_class, "cancelled_after_dispatch");
	assert.equal(s.recent[7].failure_class, "cancelled_before_dispatch");
	assert.equal(s.recent[9].structured_code, "FS_PERMISSION_DENIED");
	assert.match(s.schema, /\/v1$/);
	assert.equal(s.source.counted_unit, "root-dispatch");
});

test("unstructured approval rejection, cancellation and blocking stay unclassified", () => {
	const f = fixture();
	for (const message of ["the user rejected tool bash", "approval for bash was cancelled", "blocked by a policy", "tool failed for an unknown reason"]) f.result(failure(undefined, message));
	f.result(failure("CUSTOM_USER_SUPPLIED_SECRET"));
	const s = f.snapshot();
	assert.equal(s.totals.failed, 5);
	assert.equal(s.totals.denied, 0);
	assert.equal(s.totals.aborted, 0);
	assert.equal(s.coverage.missing_structured_code, 4);
	assert.equal(s.coverage.unrecognized_structured_code, 1);
	assert.ok(s.recent.every(event => event.failure_class === "unclassified_failure" && event.structured_code === null));
	assert.match(s.limitations.join(" "), /no denial is inferred from free text/);
	assert.doesNotMatch(JSON.stringify(s), /CUSTOM_USER_SUPPLIED_SECRET|the user rejected tool bash/);
});

test("successful materialized command text is counterevidence, not a guessed failure", () => {
	const f = fixture();
	f.result(Object.freeze({ isError: false, value: { result: "[exit code: 1] Error: expected missing-file probe" }, content: [{ type: "text", text: "ABORTED" }] }));
	assert.equal(f.snapshot().totals.success, 1);
	assert.equal(f.snapshot().totals.failed, 0);
	assert.equal(f.snapshot().recent[0].structured_code, null);
	assert.match(f.snapshot().limitations.join(" "), /not agent mistakes/);
});

test("arguments, output, freeform errors, metadata and agent history are never read", () => {
	const f = fixture();
	let forbiddenReads = 0;
	const forbidden = { get() { forbiddenReads++; throw new Error("SECRET-command-path-token"); } };
	const agent = Object.freeze(Object.defineProperties({}, { id: forbidden, session: forbidden, parent: forbidden, history: forbidden, toJSON: forbidden }));
	const exec = Object.freeze(Object.defineProperties({ agent, name: "bash", callId: "SECRET-call-id", rootCallId: "SECRET-call-id" }, { arguments: forbidden, signal: forbidden, parent: forbidden, token: forbidden, toJSON: forbidden }));
	const error = Object.freeze(Object.defineProperties({ info: Object.freeze({ code: "INVALID_ARGS" }) }, { message: forbidden, name: forbidden }));
	const result = Object.freeze(Object.defineProperties({ isError: true, error }, { value: forbidden, content: forbidden, meta: forbidden, additionalContexts: forbidden, toJSON: forbidden }));
	f.emit("tools/result", exec, result);
	const owned = f.signals.snapshot(agent);
	assert.equal(owned.totals.failed, 1);
	assert.equal(forbiddenReads, 0);
	assert.doesNotMatch(JSON.stringify(owned), /SECRET/);
	assert.equal(forbiddenReads, 0);
	// Even a malformed success carrying an error object must not read that object.
	const successResult = Object.defineProperty({ isError: false }, "error", forbidden);
	f.result(successResult);
	assert.equal(f.snapshot().totals.success, 1);
	assert.equal(forbiddenReads, 0);
});

test("literal secret values in prohibited fields and unknown structured codes never leak", () => {
	const f = fixture();
	const secret = "sk-live-very-private-example-token", args = { command: `curl -H 'Authorization: Bearer ${secret}' /secret/path` };
	f.result({ isError: true, error: { message: secret, info: { name: secret, code: secret } }, content: [{ text: secret }], value: secret, meta: { secret } }, { arguments: args });
	f.result({ isError: false, value: args, content: [{ text: secret }], meta: { secret } }, { arguments: args });
	assert.doesNotMatch(JSON.stringify([f.snapshot(), f.signals.status(f.agent), f.signals.clear(f.agent)]), /sk-live-|secret\/path|Authorization/);
});

test("syntactically valid model-supplied secret tool names never enter snapshots", () => {
	const f = fixture(), secret = "sk-live-example-secret-sentinel";
	for (const result of [failure("UNKNOWN_TOOL"), failure(undefined), failure("INVALID_ARGS"), success()]) f.result(result, { name: secret });
	const s = f.snapshot();
	assert.deepEqual(s.totals, { total: 4, success: 1, failed: 3, aborted: 0, denied: 0, unknown: 0 });
	assert.equal(s.coverage.unknown_tool_names, 1);
	assert.equal(s.coverage.unverified_tool_names, 3);
	assert.deepEqual(s.recent.map(row => row.tool), ["(unknown-tool)", "(unregistered-tool)", "(unregistered-tool)", "(unregistered-tool)"]);
	assert.ok(s.tools.every(group => !group.sequence_tracking));
	assert.equal(s.sequences.repeated_failures, 0);
	assert.equal(s.sequences.recoveries, 0);
	assert.doesNotMatch(JSON.stringify(s), /sk-live-example-secret-sentinel/);
	// A registered wrapper reporting UNKNOWN_TOOL is anonymous too.
	f.definitions.set(secret, Object.freeze({ name: secret }));
	f.result(failure("UNKNOWN_TOOL"), { name: secret });
	assert.equal(f.snapshot().recent.at(-1).tool, "(unknown-tool)");
	assert.doesNotMatch(JSON.stringify(f.snapshot()), /sk-live-example-secret-sentinel/);
});

test("missing or malformed optional registry anonymizes names without dropping outcomes", () => {
	const secret = "sk-live-example-secret-sentinel";
	const hostile = new Proxy({}, { get() { throw new Error(secret); } });
	for (const get of [undefined, () => undefined, () => null, () => ({}), () => { throw new Error(secret); }, () => hostile, () => ({ get() { throw new Error(secret); } }), () => ({ get: () => hostile }), () => ({ get: () => ({ name: "unrelated" }) })]) {
		const f = fixture(); f.ctx.get = get;
		f.result(failure("INVALID_ARGS"), { name: secret });
		f.result(failure(undefined), { name: secret });
		f.result(success(), { name: secret });
		const s = f.snapshot();
		assert.equal(s.totals.total, 3);
		assert.equal(s.totals.failed, 2);
		assert.equal(s.totals.success, 1);
		assert.equal(s.coverage.unverified_tool_names, 3);
		assert.ok(s.recent.every(row => row.tool === "(unregistered-tool)"));
		assert.equal(s.tools[0].sequence_tracking, false);
		assert.equal(s.sequences.repeated_failures, 0);
		assert.equal(s.sequences.recoveries, 0);
		assert.doesNotMatch(JSON.stringify(s), /sk-live-example-secret-sentinel/);
	}
	const f = fixture();
	const exec = Object.defineProperty({ agent: f.agent, callId: "hostile-name", rootCallId: "hostile-name" }, "name", { get() { throw new Error(secret); } });
	assert.doesNotThrow(() => f.emit("tools/result", exec, success()));
	assert.equal(f.snapshot().recent[0].tool, "(unclassified-tool)");
	assert.doesNotMatch(JSON.stringify(f.snapshot()), /sk-live-example-secret-sentinel/);
});

test("registered tool names use exact-agent lookup with no global fallback or definition traversal", () => {
	const f = fixture(), child = { id: "child" }, lookups = [];
	let forbiddenReads = 0;
	const forbidden = { get() { forbiddenReads++; throw new Error("secret-schema"); } };
	const definition = Object.freeze(Object.defineProperties({ name: "read" }, { description: forbidden, parameters: forbidden, output: forbidden, execute: forbidden, toJSON: forbidden }));
	f.tools.get = (name, scope) => { lookups.push({ name, scope }); return scope === f.agent && name === "read" ? definition : undefined; };
	f.result(failure("INVALID_ARGS"), { name: "read" });
	f.result(success(), { name: "read" });
	f.result(success(), { name: "read", agent: child });
	assert.equal(f.snapshot().tools[0].tool, "read");
	assert.equal(f.snapshot().tools[0].sequence_tracking, true);
	assert.equal(f.snapshot().sequences.recoveries, 1);
	assert.equal(f.signals.snapshot(child).tools[0].tool, "(unregistered-tool)");
	assert.deepEqual(lookups.map(call => call.scope), [f.agent, f.agent, child]);
	assert.equal(forbiddenReads, 0);
	f.definitions.delete("read"); f.tools.get = () => undefined;
	f.result(success(), { name: "read" });
	assert.equal(f.snapshot().recent.at(-1).tool, "(unregistered-tool)");
});

test("malformed and throwing payloads do not break the emit pipeline", () => {
	const f = fixture();
	const hostile = new Proxy({}, { get() { throw new Error("private error"); } });
	for (const exec of [undefined, null, 12, "text", {}, hostile]) assert.doesNotThrow(() => f.emit("tools/result", exec, hostile));
	for (const [i, result] of [undefined, null, true, 9, "raw-output", { isError: "false" }, hostile].entries()) {
		assert.doesNotThrow(() => f.emit("tools/result", { agent: f.agent, name: "bash", callId: `malformed-${i}`, rootCallId: `malformed-${i}` }, result));
	}
	f.result({ isError: true, error: hostile });
	for (const callId of [null, 4, "", "x".repeat(513)]) f.emit("tools/result", { agent: f.agent, name: "read", callId, rootCallId: callId }, success());
	f.emit("tools/result", { agent: f.agent, name: "read", callId: "no-root" }, success());
	f.result(success(), { name: "a command /private/path\n" });
	f.result(success(), { name: "a".repeat(10000) });
	assert.equal(f.snapshot().totals.unknown, 7);
	assert.equal(f.snapshot().coverage.malformed_result, 7);
	assert.equal(f.snapshot().coverage.malformed_execution, 5);
	assert.equal(f.snapshot().coverage.invalid_tool_names, 2);
	assert.equal(f.snapshot().tools.find(group => group.tool === "(unclassified-tool)").sequence_tracking, false);
	assert.doesNotMatch(JSON.stringify(f.snapshot()), /private\/path|private error/);
	for (const payload of [undefined, null, {}, hostile]) assert.doesNotThrow(() => f.emit("agent/disposed", payload));
	assert.equal(f.signals.status("session-current").status, "invalid-agent");
});

test("RSI, explicitly named probes and internal tools are excluded before result inspection", () => {
	const f = fixture(), unreadable = new Proxy({}, { get() { throw new Error("must not read"); } });
	for (const name of ["memory_rsi", "functions.memory_rsi", "memory_rsi_preflight", "rsi_probe", "probe", "test.probe.run", "internal_check", "__rsi_test", "functions.__internal"]) f.result(unreadable, { name });
	f.result(success(), { name: "memory_plan" });
	assert.equal(f.snapshot().totals.total, 1);
	assert.equal(f.snapshot().coverage.excluded_self_or_probe, 9);
	assert.deepEqual(f.snapshot().tools.map(group => group.tool), ["memory_plan"]);
});

test("root dispatch counting avoids double counting composite tools and reports its blind spot", () => {
	const f = fixture();
	f.result(failure("INVALID_ARGS"), { name: "read", callId: "child-a", rootCallId: "root" });
	f.result(success(), { name: "bash", callId: "child-b", rootCallId: "root" });
	f.result(success(), { name: "run_code", callId: "root", rootCallId: "root" });
	const s = f.snapshot();
	assert.equal(s.totals.total, 1);
	assert.equal(s.totals.success, 1);
	assert.equal(s.coverage.nested_omitted, 2);
	assert.equal(s.tools[0].tool, "run_code");
	assert.match(s.limitations.join(" "), /successful composite can hide failed child tools/);
});

test("duplicate root call IDs are ignored even when later result fields disagree", () => {
	const f = fixture();
	f.result(failure("INVALID_ARGS"), { callId: "one" });
	f.result(success(), { callId: "one" });
	f.result(success(), { callId: "one", name: "read" });
	assert.equal(f.snapshot().totals.total, 1);
	assert.equal(f.snapshot().totals.failed, 1);
	assert.equal(f.snapshot().coverage.duplicate_calls, 2);
	assert.equal(f.snapshot().sequences.recoveries, 0);
});

test("dedup is bounded and its expired-window replay limitation is explicit", () => {
	const f = fixture();
	for (let n = 0; n <= SIGNAL_LIMITS.dedup; n++) f.result(success(), { callId: `call-${n}` });
	assert.equal(f.snapshot().coverage.dedup_retained, SIGNAL_LIMITS.dedup);
	assert.equal(f.snapshot().coverage.dedup_ids_dropped, 1);
	f.result(success(), { callId: "call-0" });
	assert.equal(f.snapshot().totals.total, SIGNAL_LIMITS.dedup + 2);
	assert.match(f.snapshot().limitations.join(" "), /Replayed IDs outside it can count again/);
});

test("same-tool failure repetitions and successful recovery are not causal mistake claims", () => {
	const f = fixture();
	f.result(failure("INVALID_ARGS"));
	f.result(success(), { name: "read" });
	f.result(failure("INVALID_ARGS"));
	f.result(failure("INVALID_ARGS"));
	f.result(success());
	f.result(failure("INVALID_ARGS"));
	f.result(failure("ABORTED"));
	f.result(success()); // Abort interrupts a failed-attempt sequence.
	f.result(failure("INVALID_ARGS"));
	f.result({});
	f.result(success()); // Unknown interrupts too.
	const s = f.snapshot();
	assert.deepEqual(s.sequences, { scope: "same-tool-result-order", repeated_failures: 2, recoveries: 1, longest_failure_streak: 3 });
	assert.equal(s.recent[4].recovery, true);
	assert.equal(s.recent[7].recovery, false);
	assert.equal(s.recent[10].recovery, false);
	assert.equal(s.totals.success, 4);
});

test("large streams retain exact totals, bounded samples/tool groups/patterns and finite rates", () => {
	const f = fixture(), codes = ["INVALID_ARGS", "UNKNOWN_TOOL", "TOOL_TIMEOUT", "FS_NOT_FOUND", "CODE_RUN_FAILED", "FS_IO_ERROR"];
	for (let n = 0; n < 4096; n++) {
		const result = n % 5 === 0 ? success() : n % 5 === 1 ? {} : failure(codes[Math.floor(n / 40) % codes.length]);
		f.result(result, { name: `tool_${n % 40}` });
	}
	const s = f.snapshot();
	assert.equal(s.totals.total, 4096);
	assert.equal(s.recent.length, SIGNAL_LIMITS.recent);
	assert.equal(s.coverage.samples_dropped, 4096 - SIGNAL_LIMITS.recent);
	assert.equal(s.tools.length, SIGNAL_LIMITS.tools + 1);
	assert.equal(s.failure_patterns.length, SIGNAL_LIMITS.patterns);
	assert.ok(s.coverage.pattern_events_dropped > 0);
	assert.ok(s.coverage.tool_calls_grouped_as_other > 0);
	assert.equal(s.tools.reduce((total, group) => total + group.totals.total, 0), 4096);
	assert.equal(s.tools.at(-1).sequence_tracking, false);
	assert.equal(s.tools.at(-1).sequences.recoveries, 0);
	for (const key of ["success", "failed", "aborted", "denied", "unknown"]) assert.ok(s.rates[key] >= 0 && s.rates[key] <= 1);
	assert.equal(["success", "failed", "aborted", "denied", "unknown"].reduce((sum, key) => sum + s.rates[key], 0), 1);
	assert.equal(s.recent[0].sequence, 4096 - SIGNAL_LIMITS.recent + 1);
	assert.ok(s.coverage.first_counted_at_ms <= s.recent[0].at_ms);
	assert.ok(Buffer.byteLength(JSON.stringify(s)) < 90000);
});

test("session identity is object-scoped, with no ID lookup or ancestor/child joins", () => {
	const f = fixture(), child = { id: "session-child", parent: f.agent }, impostor = { id: f.agent.id };
	f.result(failure("INVALID_ARGS"), { callId: "same-id" });
	f.result(success(), { agent: child, callId: "same-id" });
	f.result(success(), { agent: impostor, callId: "same-id" });
	assert.equal(f.snapshot().totals.failed, 1);
	assert.equal(f.snapshot().totals.success, 0);
	assert.equal(f.signals.snapshot(child).totals.total, 1);
	assert.equal(f.signals.snapshot(impostor).totals.success, 1);
	assert.notEqual(f.snapshot().source.session_marker, f.signals.snapshot(child).source.session_marker);
	assert.notEqual(f.snapshot().source.session_marker, f.signals.snapshot(impostor).source.session_marker);
	assert.equal(f.signals.snapshot({ id: "session-current" }).totals.total, 0);
	assert.equal(f.signals.snapshot("session-current").totals.total, 0);
	assert.equal(f.signals.snapshot(undefined).totals.total, 0);
	assert.doesNotMatch(JSON.stringify(f.snapshot()), /session-child|session-current/);
	f.signals.clear(child);
	assert.equal(f.snapshot().totals.failed, 1);
});

test("session capacity evicts aggregates, keeps only own dropped coverage and no global lookup", () => {
	const f = fixture(), agents = Array.from({ length: SIGNAL_LIMITS.sessions + 1 }, (_, i) => ({ id: `s-${i}` }));
	f.result(failure("INVALID_ARGS"), { agent: agents[0] });
	const marker = f.signals.snapshot(agents[0]).source.session_marker;
	for (const agent of agents.slice(1)) f.result(success(), { agent });
	const dropped = f.signals.snapshot(agents[0]);
	assert.equal(dropped.status, "evicted");
	assert.equal(dropped.totals.total, 0);
	assert.equal(dropped.coverage.prior_window_calls_dropped, 1);
	assert.equal(dropped.recent.length, 0);
	assert.equal(dropped.failure_patterns.length, 0);
	assert.equal(dropped.tools.length, 0);
	assert.equal(dropped.coverage.dedup_retained, 0);
	f.result(success(), { agent: agents[0] });
	const recovered = f.signals.snapshot(agents[0]);
	assert.equal(recovered.status, "capturing");
	assert.equal(recovered.totals.total, 1);
	assert.equal(recovered.totals.failed, 0);
	assert.equal(recovered.source.session_marker, marker);
	assert.equal(recovered.coverage.reset_reason, "capacity");
	assert.equal(recovered.coverage.prior_window_calls_dropped, 1);
	assert.equal(f.signals.snapshot(agents[1]).status, "evicted");
	assert.equal(f.signals.snapshot(agents.at(-1)).coverage.prior_window_calls_dropped, 0);
});

test("clear resets only the current observation window, not source identity or disk artifacts", () => {
	const f = fixture();
	f.result(failure("INVALID_ARGS"), { callId: "retry" });
	const before = f.snapshot(), result = f.signals.clear(f.agent), empty = f.snapshot();
	assert.equal(result.cleared, true);
	assert.equal(result.persisted_artifacts_removed, false);
	assert.equal(empty.totals.total, 0);
	assert.equal(empty.recent.length, 0);
	assert.equal(empty.rates.failed, null);
	assert.equal(empty.coverage.dedup_retained, 0);
	assert.equal(empty.coverage.reset_reason, "clear");
	assert.equal(empty.source.session_marker, before.source.session_marker);
	assert.ok(empty.coverage.window_since_ms >= before.coverage.window_since_ms);
	f.result(success(), { callId: "retry" });
	assert.equal(f.snapshot().totals.success, 1);
	assert.equal(f.snapshot().sequences.recoveries, 0);
	assert.equal(f.signals.clear("session-current").cleared, false);
});

test("agent disposal clears only that agent and ignores its late events", () => {
	const f = fixture(), other = { id: "other" };
	f.result(failure("INVALID_ARGS")); f.result(success(), { agent: other });
	f.emit("agent/disposed", Object.freeze({ agent: f.agent }));
	assert.equal(f.snapshot().status, "disposed");
	assert.equal(f.snapshot().totals.total, 0);
	assert.equal(f.snapshot().source.session_marker, null);
	assert.equal(f.signals.snapshot(other).totals.success, 1);
	f.result(failure("INVALID_ARGS"));
	assert.equal(f.snapshot().totals.total, 0);
	f.signals.clear(f.agent); f.result();
	assert.equal(f.snapshot().status, "disposed");
});

test("Fiber stop and hot reload erase listeners and buffers, including stale API handles", () => {
	const f = fixture();
	f.result(failure("INVALID_ARGS"));
	assert.equal(f.listeners.get("tools/result").size, 1);
	f.stop();
	assert.equal(f.listeners.get("tools/result").size, 0);
	assert.equal(f.listeners.get("agent/disposed").size, 0);
	assert.equal(f.snapshot().status, "stopped");
	assert.equal(f.snapshot().totals.total, 0);
	assert.equal(f.snapshot().source.session_marker, null);
	f.result(failure("INVALID_ARGS"));
	assert.equal(f.snapshot().totals.total, 0);
	const replacement = createSessionSignals(f.ctx, { rsiTelemetryEnabled: true });
	f.result(success());
	assert.equal(f.snapshot().totals.total, 0);
	assert.equal(replacement.snapshot(f.agent).totals.success, 1);
	assert.equal(replacement.snapshot(f.agent).coverage.prior_window_calls_dropped, 0);
	f.stop();
	assert.equal(replacement.snapshot(f.agent).status, "stopped");
});

test("real Cordis plugin Fiber owns listeners and cleans retained API state", async t => {
	const root = new Context();
	t.after(() => root.fiber.dispose());
	const definition = Object.freeze({ name: "read" });
	root.provide("tools", { get(name, scope) { assert.ok(scope && typeof scope === "object"); return name === "read" ? definition : undefined; } });
	let signals;
	const plugin = root.plugin({ name: "session-signals-test", apply(ctx) { signals = createSessionSignals(ctx, { rsiTelemetryEnabled: true }); } });
	await plugin.await();
	const agent = Object.freeze({ id: "actual-cordis-agent-fixture" });
	const exec = Object.freeze({ agent, name: "read", callId: "root", rootCallId: "root" });
	root.emit("tools/result", exec, failure("INVALID_ARGS"));
	assert.equal(signals.snapshot(agent).totals.failed, 1);
	assert.equal(signals.snapshot(agent).recent[0].tool, "read");
	root.emit("agent/disposed", Object.freeze({ agent }));
	assert.equal(signals.snapshot(agent).status, "disposed");
	const other = Object.freeze({ id: "second-agent" });
	root.emit("tools/result", Object.freeze({ agent: other, name: "read", callId: "other", rootCallId: "other" }), success());
	assert.equal(signals.snapshot(other).totals.success, 1);
	await plugin.dispose();
	assert.equal(signals.snapshot(other).status, "stopped");
	assert.equal(signals.snapshot(other).totals.total, 0);
	root.emit("tools/result", exec, failure("INVALID_ARGS"));
	assert.equal(signals.snapshot(agent).totals.total, 0);
});

test("returned JSON is owned minimal data; caller mutation cannot alter the observer", () => {
	const f = fixture(); f.result(failure("INVALID_ARGS"));
	const s = f.snapshot();
	s.totals.total = 500; s.recent[0].tool = "secret"; s.tools[0].totals.failed = 99;
	s.tools[0].sequences.repeated_failures = 99; s.failure_patterns[0].count = 9;
	s.limits.sessions = 99999; s.coverage.duplicate_calls = 2; s.limitations.length = 0;
	const actual = f.snapshot();
	assert.equal(actual.totals.total, 1);
	assert.equal(actual.recent[0].tool, "bash");
	assert.equal(actual.tools[0].totals.failed, 1);
	assert.equal(actual.tools[0].sequences.repeated_failures, 0);
	assert.equal(actual.failure_patterns[0].count, 1);
	assert.equal(actual.limits.sessions, 100);
	assert.equal(actual.coverage.duplicate_calls, 0);
	assert.ok(actual.limitations.length > 0);
	assert.deepEqual(JSON.parse(JSON.stringify(actual)), actual);
});
