import { randomUUID } from "node:crypto";
import { typesafeConfig } from "./typesafe.js";
import { safeAssessmentCode } from "./rsi-errors.js";
import { DATA_BUDGET, MAX_ROOTS, PROJECTION_VERSION, bytes, digest, mappingKey, projectSource, artifactRef, receipt, validFields, integer, ids } from "./rsi-learning-data.js";
import { LEARNING_VERSION, LEARNING_DISCLAIMER, AUTHOR_BRIEF, learningIdentity, mapQuestions, mapFeature, reduceQuestions, groupState, interpretGroup } from "./rsi-learning-rubric.js";

const MAX_UNITS_PER_TURN = 128;
const emptyUsage = () => ({ input_tokens: 0, output_tokens: 0, elapsed_ms: 0, complete: true, unavailable_calls: 0, unknown_usage_calls: 0,
	scope: "Known successful-assessment subtotal only, not provider billing. complete covers returned token fields across logical assessment attempts; unavailable_calls have no validated response, and unknown_usage_calls are assessed responses missing token counts. calls counts logical assessor invocations (including local disabled responses), not HTTP/retry counts or proof an attempt was paid. Elapsed time includes only reported finite values." });
function addUsage(usage, assessment, attempted = true) {
	if (assessment.status === "unavailable" && attempted) { usage.complete = false; usage.unavailable_calls++; }
	if (assessment.status !== "assessed") return;
	let unknown = false;
	for (const key of ["input_tokens", "output_tokens"]) {
		const value = assessment.response.usage?.[key];
		if (Number.isSafeInteger(value) && value >= 0) usage[key] += value;
		else unknown = true;
	}
	if (unknown) { usage.complete = false; usage.unknown_usage_calls++; }
	if (Number.isFinite(assessment.response.elapsedMs) && assessment.response.elapsedMs >= 0) usage.elapsed_ms += assessment.response.elapsedMs;
}
function validAssessment(value, questions) {
	if (value?.status !== "assessed" || typeof value.response?.model !== "string") return false;
	const answers = value.response.answers;
	return answers && Object.keys(answers).length === Object.keys(questions).length && Object.entries(questions).every(([id, q]) => {
		const a = answers[id];
		return a?.type === q.type && Number.isFinite(a.confidence) && a.confidence >= 0 && a.confidence <= 1
			&& (q.type === "choice" ? Object.hasOwn(q.criteria, a.choice) : Number.isFinite(a.score) && a.score >= 0 && a.score <= q.criteria.length - 1);
	});
}
const boundPlans = context => context.plans.map(p => ({ plan_id: p.plan_id, revision: p.revision }));
function checkRevision(actual, expected, message) {
	if (actual !== expected) throw new Error(message);
}
function checkCancellation(exec) {
	if (!exec.signal?.aborted) return;
	const error = new Error("Learning operation cancelled"); error.code = "TYPESAFE_CANCELLED"; throw error;
}
function ownDataField(value, key) {
	// Never invoke accessors, including accessor/proxy traps that throw secrets.
	try {
		if (value === null || !["object", "function"].includes(typeof value)) return undefined;
		const descriptor = Object.getOwnPropertyDescriptor(value, key);
		return descriptor && Object.hasOwn(descriptor, "value") ? descriptor.value : undefined;
	} catch { return undefined; }
}
function immutableIdConflict(error) {
	const expected = "immutable RSI artifact ID already exists";
	const message = ownDataField(error, "message");
	if (message === expected) return true;
	// The real gateway wraps CLI JSON in the process-runner error. Recognize only
	// its exact immutable-ID error, never a broad EEXIST/stale/permission failure.
	const match = typeof message === "string" && message.match(/^[^\r\n]+ exited 1: (\{[^\r\n]*\})$/u);
	if (!match) return false;
	try {
		const payload = JSON.parse(match[1]);
		return payload?.ok === false && payload.code === "rsi_error" && payload.error === expected;
	} catch { return false; }
}

/** Explicit local storage and bounded authenticated assessor; no I/O or credentials here. */
export function createLearning({ local, assess, config = {} }) {
	if (typeof local !== "function" || typeof assess !== "function") throw new Error("Learning requires local and assess dependencies");
	const transport = typesafeConfig(config);
	async function assessment(state, questions, exec) {
		checkCancellation(exec);
		try {
			const result = await assess(state, questions, exec);
			if (validAssessment(result, questions)) return { ...result, evaluator: { ...result.evaluator, ...learningIdentity(questions), requested_model: transport.typesafeModel, endpoint: transport.typesafeEndpoint } };
			const code = result?.status === "unavailable" ? safeAssessmentCode(result, "error_code") ?? "LEARNING_ASSESSMENT_UNAVAILABLE" : "LEARNING_ASSESSMENT_UNAVAILABLE";
			const validationCode = safeAssessmentCode(result, "validation_code");
			return { status: result?.status === "disabled" ? "disabled" : "unavailable", error_code: code, ...(validationCode ? { validation_code: validationCode } : {}), reason: "No valid typed judgment is available. Only an allowlisted error code is retained; raw provider details and retryability are not inferred. Resume after investigating configuration or service availability." };
		} catch (error) {
			checkCancellation(exec);
			const validationCode = safeAssessmentCode(error, "validation_code");
			return { status: "unavailable", error_code: safeAssessmentCode(error) ?? "LEARNING_ASSESSMENT_FAILED", ...(validationCode ? { validation_code: validationCode } : {}), reason: "Assessment failed; raw transport errors are not retained. No retryability or failure cause beyond the safe code is asserted." };
		}
	}
	function fits(state, questions) {
		return bytes({ state, questions }) + bytes(questions) * 2 + 16384 <= DATA_BUDGET
			&& bytes({ state, questions, model: transport.typesafeModel }) <= transport.typesafeMaxRequestBytes;
	}

	async function mine(fields, args = {}, exec = {}) {
		validFields(fields, ["kind", "source_ids", "after", "limit", "max_calls", "refresh", "cursor"]);
		if (!["plans", "observations"].includes(fields.kind)) throw new Error("mine kind must be plans or observations");
		const maxCalls = integer(fields.max_calls, 4, 12, "max_calls");
		const limit = integer(fields.limit, 4, 50, "limit");
		if (fields.refresh !== undefined && typeof fields.refresh !== "boolean") throw new Error("refresh must be boolean");
		if (fields.after !== undefined) ids([fields.after], 1, "after");
		if (fields.source_ids !== undefined && fields.after !== undefined) throw new Error("source_ids and after cannot be combined");
		const selected = fields.source_ids === undefined ? null : ids(fields.source_ids, 50, "source_ids");
		if (fields.cursor !== undefined) {
			validFields(fields.cursor, ["source_id", "revision", "unit", "projection", "selection"]);
			ids([fields.cursor.source_id], 1, "cursor.source_id");
			if (typeof fields.cursor.revision !== "string" || !Number.isSafeInteger(fields.cursor.unit) || fields.cursor.unit < 0) throw new Error("Invalid source unit cursor");
			if ((fields.cursor.unit > 0 && fields.cursor.projection === undefined) || (fields.cursor.projection !== undefined && (typeof fields.cursor.projection !== "string" || !/^[a-f0-9]{64}$/u.test(fields.cursor.projection)))) throw new Error("Missing or incompatible projection cursor; restart this source at unit 0 without a cursor");
			if (fields.cursor.selection !== undefined && (typeof fields.cursor.selection !== "string" || !/^[a-f0-9]{64}$/u.test(fields.cursor.selection))) throw new Error("Invalid selection cursor; restart the selection without a cursor");
		}
		const page = selected ? { sources: selected.map(id => ({ id, kind: fields.kind === "plans" ? "plan" : "observation" })), has_more: false, next_after: null, errors: [] }
			: await local({ action: "corpus", kind: fields.kind, limit, ...(fields.after === undefined ? {} : { after: fields.after }) }, args, exec);
		const result = { status: "complete", receipts: [], calls: 0, cache_hits: 0, usage: emptyUsage(), coverage: { page_only: true, selected_page_complete: false, whole_corpus_reviewed: false, sources_selected: page.sources.length, sources: [], discovery_errors: page.errors ?? [] },
			next_after: page.next_after ?? null, has_more: page.has_more === true, resume: null,
			warnings: ["Only the selected source/page units are mapped; ID cursors are pages, not change feeds: periodically rescan from the start for revised or earlier-sorting IDs (unchanged units reuse cache). Model aliases can change: use refresh:true to force a new assessment. Unavailable assessments are never successful cache hits."], disclaimer: LEARNING_DISCLAIMER };
		let sourceIndex = fields.cursor ? page.sources.findIndex(s => s.id === fields.cursor.source_id) : 0;
		if (sourceIndex < 0) throw new Error("Cursor source is no longer in this page; rediscover and restart the page");
		const selection = digest({ kind: fields.kind, mode: selected ? "explicit" : "page", source_ids: page.sources.map(source => source.id), after: fields.after ?? null, limit: selected ? null : limit });
		if (fields.cursor) {
			if (fields.cursor.selection === undefined && (sourceIndex > 0 || fields.cursor.unit > 0)) throw new Error("Missing selection binding in cursor; restart the selection without a cursor");
			if (fields.cursor.selection !== undefined) checkRevision(selection, fields.cursor.selection, "Selection/page cursor is stale (kind, source IDs/order or page changed); restart the selection without a cursor");
		}
		const pause = (source, unit, projection, status = "partial") => {
			result.status = status;
			result.next_after = null; // A corpus-page advance must not skip unfinished source units.
			result.resume = { ...fields, cursor: { source_id: source.id, revision: source.revision, unit, projection, selection } };
		};
		for (; sourceIndex < page.sources.length; sourceIndex++) {
			checkCancellation(exec);
			const summary = page.sources[sourceIndex];
			let context, payload, source;
			try {
				if (fields.kind === "plans") {
					context = await local({ action: "context", plan_ids: [summary.id] }, args, exec);
					payload = context.plans.find(p => p.plan_id === summary.id);
					if (!payload) throw new Error("Selected plan is unavailable");
					source = { kind: "plan", id: summary.id, revision: payload.revision };
				} else {
					payload = (await local({ action: "read", id: summary.id }, args, exec)).artifact;
					if (!payload || payload.kind !== "observation") throw new Error("Selected source must be an observation artifact");
					context = await local({ action: "context", plan_ids: [] }, args, exec);
					source = { kind: "observation", id: payload.id, revision: payload.revision };
					await validateDependencies(payload, args, exec, new Map(), new Set());
				}
			} catch {
				checkCancellation(exec);
				result.status = "unavailable"; result.next_after = null; result.resume = { ...fields };
				result.coverage.sources.push({ id: summary.id, kind: summary.kind, units_total: null, units_completed: 0, error: "source_snapshot_unavailable" });
				result.warnings.push("Selected source snapshot could not be loaded/validated within local bounds. Repair missing/stale dependencies or narrow the source, then retry this selection; no units from this source were inferred. Earlier receipts remain durable and reusable.");
				return result;
			}
			if (summary.revision !== undefined) checkRevision(source.revision, summary.revision, "Source changed since corpus discovery; rediscover before mapping");
			const units = projectSource(source, payload);
			const projection = digest({ version: PROJECTION_VERSION, rubric: LEARNING_VERSION, requested_model: transport.typesafeModel, endpoint: transport.typesafeEndpoint, units: units.map(state => ({ state: digest(state), questions: learningIdentity(mapQuestions(state)).sha256 })) });
			const start = fields.cursor?.source_id === source.id ? fields.cursor.unit : 0;
			if (fields.cursor?.source_id === source.id) {
				checkRevision(source.revision, fields.cursor.revision, "Source cursor is stale; restart this source at its new revision");
				if (fields.cursor.projection !== undefined) checkRevision(projection, fields.cursor.projection, "Projection/rubric cursor is stale (projection, questions, requested model or endpoint changed); restart this source at unit 0 without a cursor");
			}
			if (start >= units.length) throw new Error("Source cursor is beyond its unit range");
			const coverage = { ...source, projection_version: PROJECTION_VERSION,
				projection_modes: [...new Set(units.map(s => s.coverage.projection_mode ?? "plan-report-units"))], source_reviewed_in_full: false,
				projection_exclusions_present: units.some(s => s.coverage.projection_exclusions?.length || s.coverage.sample_exclusions?.indices?.length),
				units_total: units.length, start_unit: start, units_completed: 0, omitted_context_units: units.filter(s => s.coverage.context_omissions.length).length };
			result.coverage.sources.push(coverage);
			for (let unit = start; unit < units.length; unit++) {
				const state = units[unit];
				const questions = mapQuestions(state);
				const rubric = learningIdentity(questions);
				const nonce = fields.refresh ? randomUUID() : null;
				const key = mappingKey({ source, state, questions, rubric, model: transport.typesafeModel, endpoint: transport.typesafeEndpoint, nonce });
				if (result.receipts.length >= MAX_UNITS_PER_TURN) { pause(source, unit, projection); return result; }
				const found = (await local({ action: "lookup", id: key }, args, exec)).artifact;
				if (found && validCached(found, key)) {
					result.cache_hits++; coverage.units_completed++;
					result.receipts.push({ ...receipt({ artifact: found }), source_id: source.id, unit, cache_hit: true, status: "assessed" });
					continue;
				}
				if (result.calls >= maxCalls) { pause(source, unit, projection); return result; }
				if (!fits(state, questions)) {
					pause(source, unit, projection, "unavailable"); result.error_code = "LEARNING_INPUT_BUDGET"; result.warnings.push("Unit exceeds configured request/durable budget. Increase the technical request allowance or narrow the source; this unit was not assessed and is resumable."); return result;
				}
				result.calls++;
				const judged = await assessment(state, questions, exec);
				addUsage(result.usage, judged);
				const data = { schema: LEARNING_VERSION, status: judged.status, cache_key: key, nonce, requested_model: transport.typesafeModel, endpoint: transport.typesafeEndpoint, rubric, state, questions, assessment: judged };
				if (bytes(data) > DATA_BUDGET) throw new Error("Assessed mapping exceeds record budget; no success receipt was recorded");
				const bindings = { policy_revision: context.policy.revision, plans: source.kind === "plan" ? boundPlans(context) : payload.bindings.plans,
					...(source.kind === "observation" ? { artifacts: [artifactRef(payload)] } : {}) };
				// Failures are recorded without a deterministic ID: an immutable error
				// must never poison the success cache. Unexpected cached error IDs also
				// get a fresh attempt ID rather than overwriting an immutable artifact.
				const request = { action: "record", kind: "mapping", bindings, data,
					...(judged.status === "assessed" && !found ? { record_id: key } : {}) };
				let saved, raceReused = false;
				try { saved = await local(request, args, exec); }
				catch (error) {
					if (request.record_id !== key || !immutableIdConflict(error)) throw error;
					const winner = (await local({ action: "lookup", id: key }, args, exec)).artifact;
					if (!winner || !validCached(winner, key)) throw error;
					saved = { artifact: winner }; raceReused = true; result.cache_hits++;
					result.warnings.push("A concurrent miner saved this exact map first. The winning immutable receipt is reused; this attempt's differing assessment was NOT persisted. Duplicate paid inference may have occurred; calls and usage still include this attempt.");
				}
				result.receipts.push({ ...receipt(saved), source_id: source.id, unit, cache_hit: raceReused, race_reused: raceReused, status: judged.status });
				if (judged.status !== "assessed") { pause(source, unit, projection, judged.status); result.error_code = judged.error_code; result.receipts.at(-1).error_code = judged.error_code; if (judged.validation_code) { result.validation_code = judged.validation_code; result.receipts.at(-1).validation_code = judged.validation_code; } return result; }
				coverage.units_completed++;
			}
		}
		result.coverage.selected_page_complete = !page.errors?.length;
		if (page.errors?.length) { result.status = "partial"; result.warnings.push("Some discovered sources were invalid and are not reviewed; repair them and rediscover this page."); }
		return result;
	}

	function validCached(artifact, key = artifact.data?.cache_key) {
		if (artifact.kind !== "mapping" || artifact.data?.schema !== LEARNING_VERSION) throw new Error("Unexpected mapping cache schema");
		const data = artifact.data;
		if (data.status !== "assessed" || !validAssessment(data.assessment, data.questions)) return false;
		const expected = mappingKey({ source: data.state.source, state: data.state, questions: data.questions, rubric: data.rubric, model: data.requested_model, endpoint: data.endpoint, nonce: data.nonce });
		if (expected !== key || data.cache_key !== key || data.rubric.version !== LEARNING_VERSION || data.rubric.sha256 !== learningIdentity(data.questions).sha256) throw new Error("Mapping cache identity mismatch; do not trust this artifact");
		return true;
	}

	async function currentPlan(pin, args, exec, snapshots) {
		if (!snapshots.has(pin.plan_id)) {
			const context = await local({ action: "context", plan_ids: [pin.plan_id] }, args, exec);
			const plan = context.plans.find(p => p.plan_id === pin.plan_id);
			if (!plan) throw new Error("Learning lineage plan is unavailable");
			snapshots.set(pin.plan_id, { plan_id: plan.plan_id, revision: plan.revision });
		}
		checkRevision(snapshots.get(pin.plan_id).revision, pin.revision, "Stale learning source lineage; remine the changed source before reducing");
	}
	async function validateDependencies(artifact, args, exec, snapshots, visited) {
		if (visited.has(artifact.id)) return;
		visited.add(artifact.id);
		if (visited.size > MAX_ROOTS * 2) throw new Error("Learning lineage traversal exceeds its bounded window; use smaller cohorts");
		for (const pin of artifact.bindings?.plans ?? []) await currentPlan(pin, args, exec, snapshots);
		for (const ref of artifact.bindings?.artifacts ?? []) {
			const child = (await local({ action: "read", id: ref.id }, args, exec)).artifact;
			if (!child) throw new Error("Learning lineage artifact is missing");
			checkRevision(child.revision, ref.revision, "Stale learning artifact lineage");
			await validateDependencies(child, args, exec, snapshots, visited);
		}
	}

	async function reduce(fields, args = {}, exec = {}) {
		validFields(fields, ["artifact_ids", "max_issues"]);
		const inputIds = ids(fields.artifact_ids, 128, "artifact_ids");
		const maxIssues = integer(fields.max_issues, 4, 8, "max_issues");
		const inputs = [];
		const roots = new Map();
		const ancestors = [];
		const rootIds = new Set();
		for (const id of inputIds) {
			const artifact = (await local({ action: "read", id }, args, exec)).artifact;
			if (!artifact || !["mapping", "insight"].includes(artifact.kind) || artifact.data?.schema !== LEARNING_VERSION) throw new Error("reduce requires compatible mapping or insight artifacts");
			inputs.push(artifactRef(artifact));
			if (artifact.kind === "mapping") { roots.set(id, artifact); rootIds.add(id); }
			else {
				if (!Array.isArray(artifact.data.lineage?.maps) || typeof artifact.data.lineage.root_revision_digest !== "string") throw new Error("Insight lacks exact root lineage; cannot reduce summaries as evidence");
				ancestors.push(artifact.data.lineage);
				for (const root of artifact.data.lineage.maps) rootIds.add(ids([root], 1, "lineage root")[0]);
			}
		}
		if (rootIds.size > MAX_ROOTS) return { status: "unavailable", calls: 0, issues: [], receipt: null, coverage: { roots_discovered: rootIds.size, root_limit: MAX_ROOTS, roots_assessed: 0, whole_corpus_reviewed: false }, warnings: ["This exact-lineage processing window exceeds 1024 roots. Keep disjoint cohorts as separately reviewable insights; narrow this reduction. No global confidence or independent-source count was inferred."], disclaimer: LEARNING_DISCLAIMER };
		const ordered = [...rootIds].sort();
		if (!ordered.length) throw new Error("No mapping roots in selected insights");
		const snapshots = new Map();
		const visited = new Set();
		const features = [];
		for (const id of ordered) {
			checkCancellation(exec);
			const root = roots.get(id) ?? (await local({ action: "read", id }, args, exec)).artifact;
			if (!root || !validCached(root)) throw new Error("Only successful assessed mapping roots can be reduced; retry failed maps");
			roots.set(id, root);
			const source = root.data.state.source;
			if (source.kind === "plan") await currentPlan({ plan_id: source.id, revision: source.revision }, args, exec, snapshots);
			else if (source.kind === "observation") {
				const observation = (await local({ action: "read", id: source.id }, args, exec)).artifact;
				if (!observation || observation.kind !== "observation") throw new Error("Learning observation source is unavailable");
				checkRevision(observation.revision, source.revision, "Stale observation learning source");
				await validateDependencies(observation, args, exec, snapshots, visited);
			} else throw new Error("Unknown mapping source kind");
			features.push(mapFeature(root));
		}
		for (const lineage of ancestors) checkRevision(digest([...new Set(lineage.maps)].sort().map(id => artifactRef(roots.get(id)))), lineage.root_revision_digest, "Stale hierarchical insight lineage digest");
		const context = await local({ action: "sections" }, args, exec);
		const sections = context.sections.map(s => ({ id: s.section_id, heading: s.title, text: s.body, revision: s.revision }));
		if (sections.length > 254) return { status: "unavailable", calls: 0, issues: [], warnings: ["Policy has too many exact section candidates for one typed choice; select a coherent policy scope before reducing. No sections were silently merged."], disclaimer: LEARNING_DISCLAIMER };
		const questions = reduceQuestions(sections);
		const groups = new Map();
		for (const feature of features) { if (!groups.has(feature.group_key)) groups.set(feature.group_key, []); groups.get(feature.group_key).push(feature); }
		const sortedGroups = [...groups].sort((a, b) => Math.max(...b[1].map(f => f.impact)) - Math.max(...a[1].map(f => f.impact)) || a[0].localeCompare(b[0]));
		const chosen = sortedGroups.slice(0, maxIssues);
		const warnings = ["Selected artifacts are not the whole growing corpus. Same tasks/revisions and normalized report copies are not independent evidence. Root lineage is checked before and after inference; the local store revalidates reachable source pins and hashes under cooperative writer locks at persistence. External edits that ignore those locks remain outside that transaction."];
		if (ancestors.length) warnings.push("Prior insight judgments were not reused as votes: exact mapping roots were re-read, deduplicated and re-reduced against current policy.");
		if (features.some(f => f.mechanism === "other")) warnings.push("Novel mechanisms are grouped by exact selected excerpt, not inferred paraphrase equivalence; the reasoning author should inspect related novel groups together.");
		if (snapshots.size > 8) warnings.push("More than eight source plans: direct bindings list eight, while the store validates the bounded reachable source DAG under cooperative writer locks. Incomplete or stale lineage is rejected, not treated as fresh.");
		const data = { schema: LEARNING_VERSION, status: "complete", policy: { body: context.policy.body, revision: context.policy.revision },
			lineage: { maps: ordered, root_revision_digest: digest(ordered.map(id => artifactRef(roots.get(id)))), direct_inputs: inputs },
			questions, rubric: learningIdentity(questions), issues: [], assessments: [],
			coverage: { whole_corpus_reviewed: false, input_artifacts: inputs.length, unique_mapping_roots: ordered.length, duplicate_input_ids: fields.artifact_ids.length - inputIds.length, groups_total: groups.size, groups_selected: chosen.length,
				deferred_groups: sortedGroups.slice(maxIssues).map(([key, list]) => ({ key, root_count: list.length, root_indices: list.map(f => ordered.indexOf(f.map.id)) })),
				resume_instruction: "Deferred groups are not semantically reduced. Read this insight's lineage.maps, select the listed zero-based root_indices, and reduce those artifact IDs in bounded cohorts." },
			warnings, author_brief: AUTHOR_BRIEF, disclaimer: LEARNING_DISCLAIMER };
		// Budget the ENTIRE durable result before paid inference. Shared questions
		// are retained once; root states remain immutable at their exact links.
		let witnessLimit = 12;
		const preview = limit => chosen.map(([, list]) => interpretGroup(groupState(list, limit), { status: "unavailable" }, sections));
		const answerReserve = bytes(questions) + chosen.length * 8192;
		while (witnessLimit > 1 && bytes({ ...data, issues: preview(witnessLimit) }) + answerReserve > DATA_BUDGET) witnessLimit--;
		if (bytes({ ...data, issues: preview(witnessLimit) }) + answerReserve > DATA_BUDGET) return { status: "unavailable", calls: 0, issues: [], receipt: null, coverage: data.coverage, warnings: [...warnings, "Exact lineage/policy/result exceeds durable budget. Narrow selected cohorts or max_issues; no evidence was silently discarded and no inference performed."], disclaimer: LEARNING_DISCLAIMER };
		let calls = 0;
		const usage = emptyUsage();
		for (const [key, list] of chosen) {
			const group = groupState(list, witnessLimit);
			// Exact sections partition the full policy: transmit each policy byte once.
			// The immutable record retains body once; questions reference these sections.
			const state = { policy: { revision: data.policy.revision, sections }, group };
			const withinBudget = fits(state, questions);
			const judged = withinBudget ? (calls++, await assessment(state, questions, exec)) : { status: "unavailable", error_code: "LEARNING_INPUT_BUDGET", reason: "Group/policy exceeds bounded assessment input. Narrow the selected cohort; no inference performed." };
			addUsage(usage, judged, withinBudget);
			data.issues.push({ key, ...interpretGroup(group, judged, sections) });
			data.assessments.push({ key, state_sha256: digest(state), assessment: judged });
			if (judged.status !== "assessed") data.status = "partial";
		}
		if (chosen.length !== groups.size) data.status = "partial";
		if (!data.assessments.some(entry => entry.assessment.status === "assessed")) data.status = data.assessments.every(entry => entry.assessment.status === "disabled") ? "disabled" : "unavailable";
		// Recheck all transitive source snapshots after network work. The store then
		// checks direct pins and bounded source ancestry under cooperative writer locks.
		const afterSnapshots = new Map();
		for (const pin of snapshots.values()) await currentPlan(pin, args, exec, afterSnapshots);
		const afterVisited = new Set();
		for (const root of roots.values()) if (root.data.state.source.kind === "observation") {
			const source = root.data.state.source;
			const observation = (await local({ action: "read", id: source.id }, args, exec)).artifact;
			checkRevision(observation?.revision, source.revision, "Observation source changed during reduction");
			await validateDependencies(observation, args, exec, afterSnapshots, afterVisited);
		}
		if (bytes(data) > DATA_BUDGET) throw new Error("Reduction response exceeds durable budget; no success is claimed");
		const bindings = { policy_revision: context.policy.revision, plans: [...afterSnapshots.values()].sort((a, b) => a.plan_id.localeCompare(b.plan_id)).slice(0, 8), artifacts: inputs };
		const saved = await local({ action: "record", kind: "insight", bindings, data }, args, exec);
		return { status: data.status, receipt: receipt(saved), issues: data.issues, coverage: data.coverage, warnings, author_brief: AUTHOR_BRIEF, calls, usage, disclaimer: LEARNING_DISCLAIMER };
	}
	return { mine, reduce };
}
