import { createHash } from "node:crypto";

// These are storage/request safety bounds, not desirable policy length targets.
export const DATA_BUDGET = 128 * 1024;
export const UNIT_BUDGET = 16 * 1024;
export const MAX_ROOTS = 1024;
export const PROJECTION_VERSION = "memory-rsi-projection/2";
export const bytes = value => Buffer.byteLength(JSON.stringify(value));
export function canonical(value) {
	if (Array.isArray(value)) return value.map(canonical);
	if (value && typeof value === "object") return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
	return value;
}
export const digest = value => createHash("sha256").update(JSON.stringify(canonical(value))).digest("hex");
const normalize = value => typeof value === "string" ? value.normalize("NFKC").replace(/\s+/gu, " ").trim() : Array.isArray(value) ? value.map(normalize) : value && typeof value === "object" ? Object.fromEntries(Object.entries(value).map(([k, v]) => [k, normalize(v)])) : value;
const pick = (value, keys) => Object.fromEntries(keys.filter(key => value?.[key] !== undefined).map(key => [key, value[key]]));

/** Every string leaf becomes code-owned verbatim spans; paths are references, never reads. */
export function evidenceRows(value, root = "report") {
	const rows = [];
	function visit(item, path) {
		if (typeof item === "string") {
			// JS UTF-16 offsets allow exact source.slice(start,end) verification, including Unicode.
			for (let start = 0; start < item.length;) {
				let end = Math.min(item.length, start + 512);
				if (end < item.length && /[\uD800-\uDBFF]/u.test(item[end - 1])) end--;
				rows.push({ id: `e${rows.length}`, path, start, end, quote: item.slice(start, end) });
				start = end;
			}
		} else if (typeof item === "boolean" || typeof item === "number" || item === null) {
			rows.push({ id: `e${rows.length}`, path, quote: JSON.stringify(item), literal: true });
		} else if (Array.isArray(item)) item.forEach((child, i) => visit(child, `${path}[${i}]`));
		else if (item && typeof item === "object") Object.keys(item).sort().forEach(key => visit(item[key], `${path}.${key}`));
	}
	visit(value, root);
	return rows;
}

function boundedContext(value, omissions, label) {
	if (bytes(value) <= 2048) return value;
	// Context is not silently clipped into apparent completeness. Full evidence rows
	// are still mapped; oversized descriptive context is explicitly hashed/omitted.
	omissions.push({ path: label, bytes: bytes(value), sha256: digest(value), reason: "Descriptive context exceeds per-unit bound; inspect the exact source before authoring." });
	return { context_omitted: true, sha256: digest(value) };
}

function reportContent(report, type) {
	if (type === "evidence") return pick(report, ["summary"]); // Copies with renamed reference paths are still one report.
	if (type === "exception") return pick(report, ["reason", "alternative"]);
	return report;
}

const TELEMETRY_KEYS = new Set(["schema", "enabled", "active", "status", "local_only", "persistence", "network_called", "permission_effect", "source", "coverage", "limits", "totals", "rates", "sequences", "tools", "failure_patterns", "recent", "limitations"]);
const OBSERVATION_KEYS = new Set(["status", "telemetry", "context_note", "note_provenance", "disclosure", "disclaimer"]);
const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
const OUTCOMES = ["success", "failed", "aborted", "denied", "unknown"];
const SEQUENCE_FIELDS = ["repeated_failures", "recoveries", "longest_failure_streak"];
const COVERAGE_TIMES = ["activated_at_ms", "window_since_ms", "snapshot_at_ms", "first_counted_at_ms", "last_counted_at_ms"];
const COVERAGE_COUNTS = ["prior_window_calls_dropped", "events_seen", "excluded_self_or_probe", "nested_omitted", "duplicate_calls", "malformed_execution", "malformed_result", "invalid_tool_names", "unknown_tool_names", "unverified_tool_names", "missing_structured_code", "unrecognized_structured_code", "samples_dropped", "dedup_ids_dropped", "tool_calls_grouped_as_other", "pattern_events_dropped", "recent_retained", "dedup_retained"];
const exactShape = (value, keys) => object(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const count = value => Number.isSafeInteger(value) && value >= 0;
const text = value => typeof value === "string";
const nullableText = value => value === null || text(value);
const countsShape = (value, keys) => exactShape(value, keys) && keys.every(key => count(value[key]));
const totalsShape = value => countsShape(value, ["total", ...OUTCOMES]);
const ratesShape = value => exactShape(value, ["denominator", ...OUTCOMES]) && count(value.denominator) && OUTCOMES.every(key => value[key] === null || Number.isFinite(value[key]) && value[key] >= 0 && value[key] <= 1);
function telemetryV1Shape(data) {
	const t = data.telemetry;
	if (!object(t) || t.schema !== "memory-rsi-session-signals/v1" || Object.keys(data).some(key => !OBSERVATION_KEYS.has(key)) || !exactShape(t, [...TELEMETRY_KEYS])
		|| !["status", "note_provenance", "disclosure", "disclaimer"].every(key => data[key] === undefined || text(data[key]))
		|| !(data.context_note === undefined || nullableText(data.context_note))
		|| typeof t.enabled !== "boolean" || typeof t.active !== "boolean" || !["disabled", "stopped", "invalid-agent", "disposed", "not-observed", "evicted", "capturing"].includes(t.status)
		|| t.local_only !== true || t.network_called !== false || t.persistence !== "explicit-observe-only" || t.permission_effect !== "none"
		|| !exactShape(t.source, ["kind", "event", "session_marker", "scope", "counted_unit"]) || t.source.kind !== "current-session-tool-results" || t.source.event !== "tools/result" || t.source.scope !== "exact-agent-object" || t.source.counted_unit !== "root-dispatch" || !nullableText(t.source.session_marker)
		|| !exactShape(t.coverage, [...COVERAGE_TIMES, ...COVERAGE_COUNTS, "reset_reason"]) || !["activation", "clear", "capacity", "no-observations"].includes(t.coverage.reset_reason)
		|| !COVERAGE_TIMES.every(key => t.coverage[key] === null || count(t.coverage[key])) || !COVERAGE_COUNTS.every(key => count(t.coverage[key]))
		|| !countsShape(t.limits, ["sessions", "recent", "tools", "patterns", "dedup"])
		|| !totalsShape(t.totals) || !ratesShape(t.rates) || !exactShape(t.sequences, ["scope", ...SEQUENCE_FIELDS]) || t.sequences.scope !== "same-tool-result-order" || !SEQUENCE_FIELDS.every(key => count(t.sequences[key]))
		|| ![t.tools, t.failure_patterns, t.recent, t.limitations].every(Array.isArray) || !t.limitations.every(text)) return false;
	if (!t.tools.every(group => exactShape(group, ["tool", "totals", "rates", "sequences", "sequence_tracking"]) && text(group.tool) && totalsShape(group.totals) && ratesShape(group.rates) && countsShape(group.sequences, SEQUENCE_FIELDS) && typeof group.sequence_tracking === "boolean")
		|| new Set(t.tools.map(group => group.tool)).size !== t.tools.length) return false;
	if (!t.failure_patterns.every(pattern => exactShape(pattern, ["tool", "outcome", "failure_class", "structured_code", "count"]) && text(pattern.tool) && OUTCOMES.includes(pattern.outcome) && nullableText(pattern.failure_class) && nullableText(pattern.structured_code) && count(pattern.count))) return false;
	return t.recent.every(sample => exactShape(sample, ["tool", "sequence", "at_ms", "outcome", "failure_class", "structured_code", "failure_streak", "recovery"])
		&& text(sample.tool) && count(sample.sequence) && count(sample.at_ms) && OUTCOMES.includes(sample.outcome) && nullableText(sample.failure_class) && nullableText(sample.structured_code)
		&& (sample.failure_streak === null || count(sample.failure_streak)) && typeof sample.recovery === "boolean");
}
function literalRows(value, path) {
	const quote = JSON.stringify(value);
	return Buffer.byteLength(quote) <= 3000 ? [{ path, quote, literal: true, citation_kind: "json-literal" }] : evidenceRows(value, path);
}

/** Known runtime metadata becomes coherent reports, not paid batches of scalar zeros. */
function projectTelemetry(payload, add) {
	const data = payload.data, t = data.telemetry;
	// Unknown fields at ANY nested path may contain novel facts. Only the exact
	// v1 metadata schema permits compression; all extensions use lossless rows.
	if (!telemetryV1Shape(data)) return false;
	const metadata = { capture_state: { enabled: t.enabled, active: t.active, telemetry_status: t.status, observation_status: data.status ?? null },
		source: t.source, totals: t.totals, rates: t.rates, sequences: t.sequences, coverage: t.coverage, limits: t.limits };
	// Unexpectedly large/extended metadata uses the explicit lossless fallback.
	if (bytes(metadata) > 4096 || bytes(data.note_provenance ?? "") > 512) return false;
	const omissions = [];
	const context = { session_marker: t.source.session_marker ?? null, observation_metadata: metadata,
		note_provenance: data.note_provenance ?? "Selected author note; unreviewed, not independent verification.",
		bindings: boundedContext(payload.bindings, omissions, "observation.bindings"),
		meaning: "Totals, rates and samples are observed root-dispatch metadata only, not underlying task outcomes, agent misuse, causality or independent evidence. Selected author notes remain unreviewed claims. capture_state preserves enabled/active/status: disabled or not-observed capture is not observed zero activity. JSON-literal rows serialize exact owned source values; they are not verbatim prose spans." };
	const exclusions = [
		{ path: "observation.data", fields: ["disclaimer", "disclosure"], reason: "Disclaimer bookkeeping remains in the exact source; it is not an outcome report. Wrapper status is retained in capture_state." },
		{ path: "observation.data.telemetry", fields: ["schema", "local_only", "persistence", "network_called", "permission_effect", "limitations"], reason: "Repeated transport/safety bookkeeping is not separately classified. Its scope is summarized in context; complete original values remain in the source. Enabled/active/status remain in capture_state." },
	];
	const coverage = { projection_mode: "telemetry-report-units", source_reviewed_in_full: false, projection_exclusions: exclusions,
		contextual_aggregates: ["telemetry.source", "telemetry.totals", "telemetry.rates", "telemetry.sequences", "telemetry.coverage", "telemetry.limits"] };
	const hasNote = typeof data.context_note === "string" && data.context_note.trim().length > 0;
	if (hasNote) add(data.context_note, { type: "observation-note", latest_review: "unreviewed", review_count: 0 }, context, omissions, "observation.data.context_note", { coverage });

	const reports = t.tools.map((group, index) => ({ group, path: `observation.data.telemetry.tools[${index}]`, tool: group.tool }));
	const knownTools = new Set(t.tools.map(group => group.tool));
	if (!reports.length && !hasNote || [...t.failure_patterns, ...t.recent].some(value => !knownTools.has(value.tool))) reports.push({ group: { totals: t.totals, rates: t.rates, sequences: t.sequences }, path: "observation.data.telemetry", tool: null });
	for (const entry of reports) {
		const matches = value => entry.tool === null ? !knownTools.has(value.tool) : value.tool === entry.tool;
		const patterns = t.failure_patterns.map((value, index) => ({ value, index })).filter(item => matches(item.value));
		const samples = t.recent.map((value, index) => ({ value, index })).filter(item => matches(item.value));
		// Keep the latest real sample for each outcome/failure/recovery stratum.
		// Repetitions remain exactly represented by owned totals/patterns/sequences.
		const selected = new Map();
		for (const sample of samples) selected.set(JSON.stringify([sample.value.tool, sample.value.outcome, sample.value.failure_class, sample.value.structured_code, sample.value.recovery]), sample);
		const retained = [...selected.values()].sort((a, b) => a.index - b.index);
		const selectedIndices = new Set(retained.map(item => item.index));
		const rows = entry.tool === null
			? ["totals", "rates", "sequences"].flatMap(key => literalRows(t[key], `observation.data.telemetry.${key}`))
			: literalRows(entry.group, entry.path);
		for (const pattern of patterns) rows.push(...literalRows(pattern.value, `observation.data.telemetry.failure_patterns[${pattern.index}]`));
		for (const sample of retained) rows.push(...literalRows(sample.value, `observation.data.telemetry.recent[${sample.index}]`));
		const report = { aggregate: entry.group, patterns: patterns.map(item => item.value), samples: retained.map(item => item.value) };
		add(report, { type: "observation-telemetry", tool: entry.tool, latest_review: "unreviewed", review_count: 0 }, context, omissions, entry.path, {
			rows, coverage: { ...coverage, patterns_in_report: patterns.length, samples_in_source_group: samples.length, samples_selected: retained.length,
				sample_exclusions: { path: "observation.data.telemetry.recent", indices: samples.filter(item => !selectedIndices.has(item.index)).map(item => item.index), reason: "Repeated same-stratum dispatch samples are represented by totals/sequences/patterns plus the latest actual sample, not independent outcome reports." } },
		});
	}
	return true;
}

/** Policy-independent source units, excluding repeated common template boilerplate. */
export function projectSource(source, payload) {
	const units = [];
	const add = (report, descriptor, context, omissions, reportRoot, selection = {}) => {
		const rows = (selection.rows ?? evidenceRows(report, reportRoot)).map((row, index) => ({ ...row, id: `e${index}` }));
		const report_digest = digest(normalize(reportContent(report, descriptor.type)));
		const batches = [];
		let batch = [];
		for (const row of rows) {
			if (batch.length && (batch.length >= 16 || bytes([...batch, row]) > 7000)) { batches.push(batch); batch = []; }
			batch.push(row);
		}
		if (batch.length || !batches.length) batches.push(batch);
		batches.forEach((evidence_rows, chunk) => {
			const state = { projection_version: PROJECTION_VERSION, source: pick(source, ["kind", "id", "revision"]), context, report: descriptor, report_digest,
				evidence_rows, coverage: { chunk, chunks: batches.length, rows_in_chunk: evidence_rows.length, rows_in_report: rows.length, context_omissions: omissions, common_template_boilerplate_excluded: true, referenced_files_read: false, ...selection.coverage } };
			if (bytes(state) > UNIT_BUDGET) throw new Error("Learning unit exceeds bounded projection; source context must be narrowed");
			units.push(state);
		});
	};
	if (source.kind === "plan") {
		const plan = payload.plan;
		if (!plan || plan.plan_id !== source.id || !Array.isArray(plan.work_items)) throw new Error("Invalid plan learning source");
		for (const [itemIndex, item] of plan.work_items.entries()) {
			const omissions = [];
			const requirements = (item.requirement_ids ?? []).map(id => {
				// Only requirements assigned to this item, not the full generic template.
				const entry = [...(plan.task?.achievements ?? []), ...(plan.task?.validation ?? []), ...(plan.template?.content?.achievements ?? []), ...(plan.template?.content?.validation ?? [])].find(r => r.id === id);
				return entry ? pick(entry, ["id", "description", "evidence"]) : { id };
			});
			const context = { task: boundedContext(pick(plan.task, ["title", "purpose", "good", "boundaries"]), omissions, "task"),
				work_item: boundedContext(pick(item, ["id", "title", "status", "requirement_ids"]), omissions, "work_item"),
				applicable_requirements: boundedContext(requirements, omissions, "applicable_requirements"),
				template_pin: pick(plan.template, ["template_id", "revision", "content_sha256"]),
				complete: payload.validation?.complete === true };
			let count = 0;
			for (const [key, type] of [["evidence", "evidence"], ["exceptions", "exception"]]) for (const [index, report] of (item[key] ?? []).entries()) {
				count++;
				add(report, { type, id: report.id, requirement_id: report.requirement_id, work_item_id: item.id,
					latest_review: report.reviews?.at(-1)?.verdict ?? "unreviewed", review_count: report.reviews?.length ?? 0 }, context, omissions, `plan.work_items[${itemIndex}].${key}[${index}]`);
			}
			if (!count) add(pick(item, ["id", "title", "status", "requirement_ids"]), { type: "planned", work_item_id: item.id, latest_review: "unreviewed", review_count: 0 }, context, omissions, `plan.work_items[${itemIndex}]`);
		}
		if (!plan.work_items.length) add(plan.task ?? {}, { type: "planned", latest_review: "unreviewed", review_count: 0 }, { template_pin: pick(plan.template, ["template_id", "revision"]) }, [], "plan.task");
	} else {
		if (payload.kind !== "observation") throw new Error("Expected observation artifact");
		if (projectTelemetry(payload, add)) return units.map((state, unit) => ({ ...state, unit, source_units: units.length }));
		const omissions = [];
		// The explicit observation artifact is already selected/caller-scoped upstream.
		// Preserve every supplied data leaf (metadata numbers are retained in context
		// when bounded), not arbitrary session logs or referenced tool output.
		const context = { session_marker: payload.data.source?.session_marker ?? payload.data.snapshot?.source?.session_marker ?? payload.data.signals?.source?.session_marker ?? payload.data.telemetry?.source?.session_marker ?? null,
			observation_metadata: boundedContext(payload.data, omissions, "observation.data"),
			bindings: boundedContext(payload.bindings, omissions, "observation.bindings"), meaning: "Selected observations, not independent verification; transport errors do not establish agent misuse or underlying tool failure." };
		add(payload.data, { type: "observation", latest_review: "unreviewed", review_count: 0 }, context, omissions, "observation.data", { coverage: { projection_mode: "unknown-observation-lossless-fallback", source_reviewed_in_full: false, projection_exclusions: [] } });
	}
	return units.map((state, unit) => ({ ...state, unit, source_units: units.length }));
}

/** Connected components conservatively collapse same task/session or copied report content. */
export function independentSources(features) {
	const parent = new Map();
	const find = key => { if (!parent.has(key)) parent.set(key, key); if (parent.get(key) !== key) parent.set(key, find(parent.get(key))); return parent.get(key); };
	for (const feature of features) {
		const source = `source:${feature.source_key}`;
		const report = `report:${feature.report_digest}`;
		parent.set(find(source), find(report));
	}
	return new Set(features.map(feature => find(`source:${feature.source_key}`))).size;
}

export const mappingKey = ({ source, state, questions, rubric, model, endpoint, nonce = null }) => `map-${digest({ source, state, questions, rubric, model, endpoint, nonce })}`;
export const artifactRef = artifact => ({ id: artifact.id, revision: artifact.revision });
export const receipt = result => ({ ...artifactRef(result.artifact), path: result.artifact.path, freshness: result.artifact.freshness, persistence: result.persistence });

export function validFields(fields, allowed) {
	if (!fields || typeof fields !== "object" || Array.isArray(fields)) throw new Error("Learning fields must be an object");
	const unknown = Object.keys(fields).filter(key => !allowed.includes(key));
	if (unknown.length) throw new Error(`Unknown learning fields: ${unknown.join(", ")}`);
}
export function integer(value, fallback, max, name) {
	value ??= fallback;
	if (!Number.isSafeInteger(value) || value < 1 || value > max) throw new Error(`${name} must be an integer from 1 to ${max}`);
	return value;
}
export function ids(values, max, name) {
	if (!Array.isArray(values) || !values.length || values.length > max || values.some(id => typeof id !== "string" || !/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$/.test(id))) throw new Error(`${name} requires 1..${max} source IDs`);
	return [...new Set(values)];
}
