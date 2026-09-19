import { createHash, randomUUID } from "node:crypto";

// Storage ceilings, not policy-length targets or achievement thresholds.
export const SIGNAL_LIMITS = Object.freeze({ sessions: 100, recent: 128, tools: 32, patterns: 64, dedup: 256 });
const OUTCOMES = ["success", "failed", "aborted", "denied", "unknown"];
// Only this fixed vocabulary may leave the observer. Arbitrary error codes can
// contain secrets too. Current tools/result has error.info.code, NOT a reason enum.
const FAILURE_CODES = new Map([
	["ABORTED", ["aborted", "cancelled_after_dispatch"]],
	["ABORTED_BEFORE_DISPATCH", ["aborted", "cancelled_before_dispatch"]],
	["FS_ABORTED", ["aborted", "filesystem_cancelled"]],
	["FS_PERMISSION_DENIED", ["denied", "filesystem_permission"]],
	["FS_SANDBOX_DENIED", ["denied", "filesystem_sandbox"]],
	["UNKNOWN_TOOL", ["failed", "unknown_tool"]],
	["INVALID_ARGS", ["failed", "invalid_arguments"]],
	["INVALID_TOOL_OUTPUT", ["failed", "invalid_output"]],
	["UNSUPPORTED_SCHEMA", ["failed", "unsupported_schema"]],
	["CODE_RUN_FAILED", ["failed", "code_execution"]],
	["TOOL_TIMEOUT", ["failed", "timeout"]],
	["SANDBOX_UNAVAILABLE", ["failed", "sandbox_unavailable"]],
	...["FS_NOT_FOUND", "FS_NOT_DIRECTORY", "FS_NOT_TEXT", "FS_NOT_REGULAR_FILE", "FS_TOO_LARGE", "FS_IO_ERROR", "FS_STALE_VERSION", "FS_NOT_OBSERVED", "FS_AMBIGUOUS_EDIT", "FS_EDIT_NOT_FOUND"].map(code => [code, ["failed", code.toLowerCase()]]),
]);
const LIMITATIONS = [
	"Counts are final root tool dispatch outcomes, not agent mistakes, earned credit, task success or independent evidence sources.",
	"Nested/composite dispatches are excluded to avoid double counting; a successful composite can hide failed child tools.",
	"Tool arguments, values, output/content, metadata, error messages and session history are never inspected. Nonzero exit text in a successful result remains dispatch success; expected probes cannot be inferred.",
	"Approval denials and policy blocks without an allowlisted structured error.info.code remain unclassified failures; no denial is inferred from free text.",
	"RSI/probe/internal tool names are excluded. Internal work using ordinary tool names is indistinguishable without reading arguments and may remain included.",
	"Tool names are retained only when the optional tools registry verifies them in this exact agent scope. UNKNOWN_TOOL and absent, unavailable or invalid registry lookups use fixed anonymous buckets; counters are still captured.",
	"Same-tool failure/recovery sequences follow result arrival order, not causal attribution; concurrent calls may finish out of order. Overflow and anonymous tool groups do not produce sequence claims.",
	"Deduplication covers only the bounded recent root call-ID window. Replayed IDs outside it can count again. No ancestor/child session joins or pre-activation history are read.",
];
const object = value => value !== null && typeof value === "object";
function leaf(value, key) {
	try { return object(value) ? value[key] : undefined; } catch { return undefined; }
}
const totals = () => ({ total: 0, success: 0, failed: 0, aborted: 0, denied: 0, unknown: 0 });
const sequences = () => ({ repeated_failures: 0, recoveries: 0, longest_failure_streak: 0 });
const coverage = () => ({ events_seen: 0, excluded_self_or_probe: 0, nested_omitted: 0, duplicate_calls: 0, malformed_execution: 0, malformed_result: 0, invalid_tool_names: 0, unknown_tool_names: 0, unverified_tool_names: 0, missing_structured_code: 0, unrecognized_structured_code: 0, samples_dropped: 0, dedup_ids_dropped: 0, tool_calls_grouped_as_other: 0, pattern_events_dropped: 0 });
const validId = value => typeof value === "string" && value.length > 0 && value.length <= 512;
const validTool = name => typeof name === "string" && /^[a-zA-Z][a-zA-Z0-9_.:/-]{0,95}$/.test(name);
function excluded(name) {
	return typeof name === "string" && name.length <= 128 && (
		/^(?:functions\.)?(?:memory_rsi(?:$|[._:/-])|rsi(?:$|[._:/-])|__)/.test(name)
		|| /(?:^|[._:/-])(?:probe|internal)(?:$|[._:/-])/.test(name)
	);
}
function classify(result) {
	const flag = leaf(result, "isError");
	if (flag === false) return { outcome: "success", failure_class: null, structured_code: null };
	if (flag !== true) return { outcome: "unknown", failure_class: null, structured_code: null, uncertainty: "malformed_result" };
	const code = leaf(leaf(leaf(result, "error"), "info"), "code");
	const known = FAILURE_CODES.get(code);
	if (known) return { outcome: known[0], failure_class: known[1], structured_code: code };
	return { outcome: "failed", failure_class: "unclassified_failure", structured_code: null, uncertainty: typeof code === "string" ? "unrecognized_structured_code" : "missing_structured_code" };
}
function projectToolName(ctx, name, agent, classification, counts) {
	// An unknown requested name is model-controlled, not registry metadata. Even
	// a known wrapper can surface UNKNOWN_TOOL; never retain that request's name.
	if (classification.structured_code === "UNKNOWN_TOOL") {
		counts.unknown_tool_names++;
		return "(unknown-tool)";
	}
	if (!validTool(name)) { counts.invalid_tool_names++; return "(unclassified-tool)"; }
	try {
		// Inspected tools.get(name, scope) resolves only this agent's visible tool.
		// Do not enumerate, copy or retain the live service/definition, or fall back
		// to the global registry when a scoped definition is absent/restricted.
		const tools = ctx.get?.("tools");
		const definition = tools?.get(name, agent);
		if (leaf(definition, "name") === name) return name;
	} catch { /* Optional unavailable/malformed service: anonymous capture only. */ }
	counts.unverified_tool_names++;
	return "(unregistered-tool)";
}
function newState(previous = 0, reset = "activation", marker = randomUUID()) {
	return {
		marker, since: Date.now(), first: null, last: null, evicted: false,
		previousDropped: previous, reset, totals: totals(), sequences: sequences(), coverage: coverage(),
		tools: new Map(), other: null, patterns: new Map(), recent: [], seen: new Set(),
	};
}
function erase(state) {
	state.tools.clear(); state.patterns.clear(); state.recent.length = 0; state.seen.clear();
	state.other = null; state.totals = totals(); state.sequences = sequences(); state.coverage = coverage();
	state.first = null; state.last = null;
}
function count(target, outcome) { target.total++; target[outcome]++; }
function toolGroup(state, name) {
	let group = state.tools.get(name);
	if (!group && state.tools.size < SIGNAL_LIMITS.tools) {
		group = { tool: name, totals: totals(), sequences: sequences(), streak: 0 };
		state.tools.set(name, group);
	}
	if (group) return group;
	state.coverage.tool_calls_grouped_as_other++;
	return state.other ??= { tool: "(other-tools)", totals: totals(), sequences: sequences(), streak: 0 };
}
function updateSequences(state, group, outcome) {
	if (!validTool(group.tool)) return { failure_streak: null, recovery: false };
	const recovery = outcome === "success" && group.streak > 0;
	if (outcome === "failed") {
		group.streak++;
		if (group.streak > 1) { group.sequences.repeated_failures++; state.sequences.repeated_failures++; }
		group.sequences.longest_failure_streak = Math.max(group.sequences.longest_failure_streak, group.streak);
		state.sequences.longest_failure_streak = Math.max(state.sequences.longest_failure_streak, group.streak);
	} else {
		if (recovery) { group.sequences.recoveries++; state.sequences.recoveries++; }
		group.streak = 0; // Abort, denial and unknown are not failed-attempt evidence.
	}
	return { failure_streak: group.streak, recovery };
}
function record(state, name, classification) {
	const now = Date.now(), { outcome, failure_class, structured_code, uncertainty } = classification;
	state.first ??= now; state.last = now;
	count(state.totals, outcome);
	if (uncertainty) state.coverage[uncertainty]++;
	const group = toolGroup(state, name);
	count(group.totals, outcome);
	const sequence = updateSequences(state, group, outcome);
	if (failure_class) {
		const key = `${group.tool}|${failure_class}`;
		let pattern = state.patterns.get(key);
		if (!pattern && state.patterns.size < SIGNAL_LIMITS.patterns) {
			pattern = { tool: group.tool, outcome, failure_class, structured_code, count: 0 };
			state.patterns.set(key, pattern);
		}
		if (pattern) pattern.count++; else state.coverage.pattern_events_dropped++;
	}
	// No call IDs or live object references enter samples or public snapshots.
	state.recent.push({ sequence: state.totals.total, at_ms: now, tool: group.tool, outcome, failure_class, structured_code, ...sequence });
	if (state.recent.length > SIGNAL_LIMITS.recent) { state.recent.shift(); state.coverage.samples_dropped++; }
}
function rates(counts) {
	return { denominator: counts.total, ...Object.fromEntries(OUTCOMES.map(key => [key, counts.total ? counts[key] / counts.total : null])) };
}

/**
 * Safe metadata-only observation of the inspected Host contracts:
 * tools/result(exec: Readonly<ToolExecution>, result: Readonly<ToolExecutionResult>)
 * agent/disposed(payload: { agent: Agent }). Both are emit events.
 *
 * Instantiate in the owning plugin Fiber. Only pass the actual exec.agent to the
 * returned API; it accepts no session ID and never looks one up. This collector
 * neither persists nor transmits anything. An explicit observe action owns local
 * persistence; only separately selected mine input may leave the machine.
 */
export function createSessionSignals(ctx, config = {}) {
	const enabled = config.rsiTelemetryEnabled === true;
	const activatedAt = Date.now();
	let active = enabled, states = new WeakMap(), disposed = new WeakSet();
	// Owned aggregates only: this bounded set holds NO Agent, execution or result.
	const retained = new Set();
	function ensure(agent) {
		let state = states.get(agent);
		if (state && !state.evicted) { retained.delete(state); retained.add(state); return state; }
		if (retained.size >= SIGNAL_LIMITS.sessions) {
			const oldest = retained.values().next().value;
			oldest.previousDropped += oldest.totals.total;
			erase(oldest); oldest.evicted = true; oldest.reset = "capacity";
			retained.delete(oldest);
		}
		state = newState(state?.previousDropped ?? 0, state?.evicted ? "capacity" : "activation", state?.marker);
		states.set(agent, state); retained.add(state);
		return state;
	}
	function onResult(exec, result) {
		if (!active) return;
		const agent = leaf(exec, "agent");
		if (!object(agent) || disposed.has(agent)) return;
		const state = ensure(agent);
		state.coverage.events_seen++;
		const name = leaf(exec, "name");
		if (excluded(name)) { state.coverage.excluded_self_or_probe++; return; }
		const callId = leaf(exec, "callId"), rootCallId = leaf(exec, "rootCallId");
		if (!validId(callId) || !validId(rootCallId)) { state.coverage.malformed_execution++; return; }
		if (callId !== rootCallId) { state.coverage.nested_omitted++; return; }
		// Bounded, non-exported digests avoid retaining raw identifier strings.
		const id = createHash("sha256").update(callId).digest("hex");
		if (state.seen.has(id)) { state.coverage.duplicate_calls++; return; }
		state.seen.add(id);
		if (state.seen.size > SIGNAL_LIMITS.dedup) { state.seen.delete(state.seen.values().next().value); state.coverage.dedup_ids_dropped++; }
		const classification = classify(result);
		const tool = projectToolName(ctx, name, agent, classification, state.coverage);
		record(state, tool, classification);
	}
	function forget(agent) {
		const state = object(agent) ? states.get(agent) : undefined;
		if (!state) return false;
		erase(state); retained.delete(state); states.delete(agent);
		return true;
	}
	if (enabled) {
		if (typeof ctx?.on !== "function" || typeof ctx?.effect !== "function") throw new Error("Session telemetry requires Fiber-owned ctx.on and ctx.effect");
		// ctx.on owns listener disposers; this effect also erases buffers on stop/HMR.
		ctx.effect(() => () => {
			active = false;
			for (const state of retained) erase(state);
			retained.clear(); states = new WeakMap(); disposed = new WeakSet();
		});
		ctx.on("tools/result", onResult);
		ctx.on("agent/disposed", payload => {
			const agent = leaf(payload, "agent");
			if (object(agent)) { forget(agent); disposed.add(agent); }
		});
	}
	function status(agent) {
		const state = object(agent) ? states.get(agent) : undefined;
		const status = !enabled ? "disabled" : !active ? "stopped" : !object(agent) ? "invalid-agent" : disposed.has(agent) ? "disposed" : !state ? "not-observed" : state.evicted ? "evicted" : "capturing";
		return { enabled, active, status, local_only: true, persistence: "explicit-observe-only", network_called: false, permission_effect: "none" };
	}
	function snapshot(agent) {
		const state = object(agent) ? states.get(agent) : undefined;
		const counts = state?.totals ?? totals();
		return {
			schema: "memory-rsi-session-signals/v1", ...status(agent),
			source: { kind: "current-session-tool-results", event: "tools/result", session_marker: state?.marker ?? null, scope: "exact-agent-object", counted_unit: "root-dispatch" },
			coverage: {
				activated_at_ms: activatedAt, window_since_ms: state?.since ?? activatedAt, snapshot_at_ms: Date.now(),
				first_counted_at_ms: state?.first ?? null, last_counted_at_ms: state?.last ?? null,
				reset_reason: state?.reset ?? "no-observations", prior_window_calls_dropped: state?.previousDropped ?? 0,
				...state?.coverage ?? coverage(), recent_retained: state?.recent.length ?? 0, dedup_retained: state?.seen.size ?? 0,
			},
			limits: { ...SIGNAL_LIMITS }, totals: { ...counts }, rates: rates(counts),
			sequences: { scope: "same-tool-result-order", ...state?.sequences ?? sequences() },
			tools: state ? [...state.tools.values(), ...state.other ? [state.other] : []].map(group => ({ tool: group.tool, totals: { ...group.totals }, rates: rates(group.totals), sequences: { ...group.sequences }, sequence_tracking: validTool(group.tool) })) : [],
			failure_patterns: state ? [...state.patterns.values()].map(pattern => ({ ...pattern })) : [],
			recent: state ? state.recent.map(sample => ({ ...sample })) : [],
			limitations: [...LIMITATIONS],
		};
	}
	function clear(agent) {
		const previous = object(agent) ? states.get(agent) : undefined;
		const cleared = forget(agent);
		if (cleared && active && !disposed.has(agent)) {
			const next = ensure(agent);
			next.reset = "clear";
			next.marker = previous.marker; // Same source, new observation window; not independent evidence.
		}
		return { ...status(agent), cleared, scope: "exact-agent-object", persisted_artifacts_removed: false };
	}
	return { snapshot, clear, status };
}
