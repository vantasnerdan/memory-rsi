import { defineTool } from "@deepseek-ai/dsh-tools";
import { createHash } from "node:crypto";
import { setupFields } from "./setup-json.js";
import { withinAssessmentBudget } from "./rsi-budget.js";
import { safeAssessmentCode } from "./rsi-errors.js";
import { createLearning } from "./rsi-learning.js";
import { diagnoseAssessor } from "./rsi-diagnostics.js";
import { rsiOutput } from "./rsi-output.js";
export { rsiOutput } from "./rsi-output.js";
import { evaluateTypesafe, typesafeConfig } from "./typesafe.js";
import { policySections, preflightQuestions, evaluationQuestions, rubricIdentity, interpretPreflight, interpretEvaluation, contractState, outcomeSummary, DISCLAIMER } from "./rsi-rubric.js";

const ACTIONS = ["status", "diagnose", "preflight", "prepare", "sections", "propose", "evaluate", "reflect", "observe", "clear_signals", "corpus", "mine", "reduce", "read", "list", "promote"];
const FIELDS = {
	status: [], diagnose: [], preflight: ["plan_id"], prepare: ["plan_ids", "insight_ids"], sections: [],
	propose: ["proposal_id", "body", "edits", "reason", "expected_revision", "plan_ids", "source_artifact_ids"],
	observe: ["plan_id", "context_note"], clear_signals: [],
	corpus: ["kind", "after", "limit"],
	mine: ["kind", "source_ids", "after", "limit", "max_calls", "refresh", "cursor"],
	reduce: ["artifact_ids", "max_issues"],
	evaluate: ["proposal_id"], reflect: ["plan_id", "lesson"], read: ["id", "issue_index"], list: ["kind", "limit", "after"],
	promote: ["proposal_id", "evaluation_id", "expected_revision", "review_note", "apply"],
};
function fieldsFor(action, fields) {
	if (!ACTIONS.includes(action)) throw new Error(`Unknown RSI action: ${action}`);
	const extra = Object.keys(fields).filter(key => !FIELDS[action].includes(key));
	if (extra.length) throw new Error(`Unknown RSI fields: ${extra.join(", ")}`);
	return fields;
}
const bindingsFor = context => ({ policy_revision: context.policy.revision, plans: context.plans.map(entry => ({ plan_id: entry.plan_id, revision: entry.revision })) });
const snapshotPolicy = policy => ({ body: policy.body, revision: policy.revision });
const receipt = result => ({ id: result.artifact.id, revision: result.artifact.revision, path: result.artifact.path, bindings: result.artifact.bindings, freshness: result.artifact.freshness, persistence: result.persistence });
const RECORD_BUDGET = 128 * 1024;
const bytes = value => Buffer.byteLength(JSON.stringify(value));
function reviewFits(state, questions) {
	// Reserve room for known typed answers, interpretation, metadata and Unicode
	// headings. No paid inference is attempted if its evidence cannot be retained.
	return bytes({ state, questions }) + bytes(questions) * 2 + 16384 <= RECORD_BUDGET;
}
function retainedInput(state, questions) {
	if (reviewFits(state, questions)) return { state, questions };
	const input = JSON.stringify({ state, questions });
	return { input_retained: false, input_bytes: Buffer.byteLength(input), input_sha256: createHash("sha256").update(input).digest("hex"), limitation: "Input exceeds durable assessment budget; narrow selected contracts. No inference was performed and no evidence was truncated into a passing judgment." };
}

/** Network and persistence are separate dependencies so protocol tests need no live key. */
export function createRsi(ctx, config, { memory, evaluate = evaluateTypesafe, signals, learningFactory = createLearning }) {
	async function local(request, args, exec) {
		const argv = ["rsi", "--request", "-"];
		if (args.no_git) argv.push("--no-git");
		if (args.allow_non_main_branch) argv.push("--allow-non-main-branch");
		const callerConfig = exec.agent?.id ? { ...config, agentId: exec.agent.id } : config;
		return JSON.parse(await memory(callerConfig, argv, { stdin: JSON.stringify(request), signal: exec.signal }));
	}
	async function credential() {
		const service = ctx.get?.("credentials");
		if (service) return (await service.resolve(config.typesafeApiKeyEnv || "TYPESAFE_API_KEY"))?.value;
		return process.env[config.typesafeApiKeyEnv || "TYPESAFE_API_KEY"];
	}
	async function assess(state, questions, exec) {
		if (config.typesafeEnabled !== true) return { status: "disabled", reason: "Remote assessment is opt-in: configure typesafeEnabled after reviewing data disclosure.", disclaimer: DISCLAIMER };
		if (!reviewFits(state, questions)) return { status: "unavailable", reason: "Selected input exceeds the durable assessment budget; select smaller contracts or fewer evaluation cases. No inference performed.", error_code: "RSI_INPUT_BUDGET", disclaimer: DISCLAIMER };
		try {
			const resolved = typesafeConfig(config);
			return await withinAssessmentBudget(async (signal, remaining) => {
				const apiKey = await credential();
				signal.throwIfAborted();
				if (!apiKey) return { status: "unavailable", reason: "TypeSafe credential is not configured. Set the configured host credential or environment reference.", disclaimer: DISCLAIMER };
				// Never include credential resolution objects in artifacts or tool results.
				const response = await evaluate({ state, questions }, { ...config, typesafeTimeoutMs: remaining() }, { signal, apiKey });
				return { status: "assessed", evaluator: { ...rubricIdentity(questions), requested_model: resolved.typesafeModel, endpoint: resolved.typesafeEndpoint }, response };
			}, exec.signal, resolved.typesafeTimeoutMs);
		} catch (error) {
			if (exec.signal?.aborted) throw new Error("RSI assessment cancelled");
			const validationCode = safeAssessmentCode(error, "validation_code");
			return { status: "unavailable", reason: "TypeSafe assessment failed; inspect configuration and retry. No compliance or achievement is asserted.", error_code: safeAssessmentCode(error) ?? "assessment_failed", ...(validationCode ? { validation_code: validationCode } : {}), disclaimer: DISCLAIMER };
		}
	}
	async function preflight(planId, args, exec, expectedRevision) {
		if (config.typesafeEnabled !== true) return { status: "disabled", reason: "Remote policy preflight is opt-in (typesafeEnabled). Review the contract against policy locally before work.", permission_effect: "none" };
		const context = await local({ action: "context", plan_ids: [planId] }, args, exec);
		if (expectedRevision !== undefined && context.plans[0].revision !== expectedRevision) throw new Error("Plan changed after creation; read the latest revision before preflight");
		const sections = policySections(context.policy.body);
		const state = { policy: { ...snapshotPolicy(context.policy), sections }, contract: contractState(context.plans[0]) };
		const questions = preflightQuestions(sections);
		const assessment = await assess(state, questions, exec);
		const interpretation = assessment.status === "assessed" ? interpretPreflight(assessment.response.answers, sections) : { disposition: "not-assessed", permission_effect: "none", disclaimer: DISCLAIMER };
		const result = await local({ action: "record", kind: "preflight", bindings: bindingsFor(context), data: { ...assessment, ...retainedInput(state, questions), interpretation } }, args, exec);
		return { ...assessment, interpretation, receipt: receipt(result) };
	}
	const learning = learningFactory?.({ local, assess, config });
	async function selectedInsights(ids, args, exec) {
		if (!Array.isArray(ids) || ids.length > 32 || new Set(ids).size !== ids.length || ids.some(id => typeof id !== "string")) throw new Error("insight_ids must be at most 32 distinct artifact IDs");
		const insights = [];
		for (const id of ids) {
			const { artifact } = await local({ action: "read", id }, args, exec);
			if (artifact.kind !== "insight") throw new Error("Selected learning evidence must be an insight artifact");
			if (artifact.freshness?.stale !== false) throw new Error("Selected learning insight is stale or its source lineage is unavailable; remine changed sources and reduce against current policy before authoring");
			insights.push({ id: artifact.id, revision: artifact.revision, path: artifact.path, freshness: artifact.freshness, issues: artifact.data.issues ?? [], coverage: artifact.data.coverage ?? {}, warnings: artifact.data.warnings ?? [], provenance: artifact.provenance ?? "Unverified stored learning summary" });
		}
		return insights;
	}
	async function run(action, fields, args = {}, exec = {}) {
		fieldsFor(action, fields);
		if (action === "diagnose") return diagnoseAssessor(assess, exec);
		if (action === "mine" || action === "reduce") {
			if (!learning) throw new Error("Corpus learning is not mounted");
			return learning[action](fields, args, exec);
		}
		if (action === "read" && fields.issue_index !== undefined) {
			if (!Number.isSafeInteger(fields.issue_index) || fields.issue_index < 0) throw new Error("issue_index must be a nonnegative integer");
			const { artifact } = await local({ action: "read", id: fields.id }, args, exec);
			if (artifact.kind !== "insight" || !Array.isArray(artifact.data.issues) || fields.issue_index >= artifact.data.issues.length) throw new Error("issue_index must select an existing insight issue");
			const issue = artifact.data.issues[fields.issue_index];
			return { artifact: { id: artifact.id, kind: artifact.kind, revision: artifact.revision, path: artifact.path, bindings: artifact.bindings, freshness: artifact.freshness }, issue_index: fields.issue_index, issue, assessment: artifact.data.assessments?.find(item => item.key === issue.key), coverage: artifact.data.coverage, warnings: artifact.data.warnings, disclaimer: DISCLAIMER };
		}
		if (action === "clear_signals") {
			if (!signals || !exec.agent) throw new Error("Current-session signal collector is unavailable");
			return signals.clear(exec.agent);
		}
		if (action === "observe") {
			if (!signals || !exec.agent) throw new Error("observe requires the current invoking agent; arbitrary session IDs are not accepted");
			if (fields.context_note !== undefined && (typeof fields.context_note !== "string" || !fields.context_note.trim() || fields.context_note.length > 4096)) throw new Error("context_note must be a nonempty selected summary of at most 4096 characters");
			const context = await local({ action: "context", plan_ids: fields.plan_id ? [fields.plan_id] : [] }, args, exec);
			const data = { status: "observed", telemetry: signals.snapshot(exec.agent), context_note: fields.context_note ?? null, note_provenance: "Explicitly selected agent-authored context summary; not independently verified, not a raw session transcript. Review before remote mining.", disclosure: "Local persistence only. A later explicit mine selects this observation for TypeSafe.", disclaimer: DISCLAIMER };
			const result = await local({ action: "record", kind: "observation", bindings: bindingsFor(context), data }, args, exec);
			return { ...data, receipt: receipt(result), network_called: false };
		}
		if (action === "status") {
			const resolved = typesafeConfig(config);
			const service = ctx.get?.("credentials");
			const configured = service ? (await service.describe(resolved.typesafeApiKeyEnv)).configured : Boolean(process.env[resolved.typesafeApiKeyEnv]);
			return { enabled: config.typesafeEnabled === true, configured, protocol: "typesafe-systemone-v1", endpoint: resolved.typesafeEndpoint, model: resolved.typesafeModel, credential_reference: resolved.typesafeApiKeyEnv, timeout_ms: resolved.typesafeTimeoutMs, telemetry: signals?.status(exec.agent) ?? { enabled: false, status: "unavailable" }, network_called: false, disclaimer: DISCLAIMER };
		}
		if (action === "preflight") return preflight(fields.plan_id, args, exec);
		if (action === "prepare") {
			const context = await local({ action: "context", plan_ids: fields.plan_ids ?? [] }, args, exec);
			const issueBriefs = await selectedInsights(fields.insight_ids ?? [], args, exec);
			const sectionView = await local({ action: "sections" }, args, exec);
			if (sectionView.policy.revision !== context.policy.revision) throw new Error("Policy changed while preparing; retry against the current revision");
			return {
				policy: context.policy, sections: sectionView.sections, issues: issueBriefs, cases: context.plans.map(entry => contractState(entry, { outcomes: true })), outcomes: context.plans.map(outcomeSummary),
				brief: "As the reasoning author, draft at most one small coherent policy improvement, or recommend no change. Jev cannot generate prose. Preserve substantive requirements; prefer replacing/clarifying over appending. Use selected reviewed outcomes to identify a recurring failure or successful pattern. Explain expected benefit, counterexamples and rollback condition in reason. Put job-specific skills/rules/tool details in a separately versioned template or contract, not global policy. Never relax an active plan's pinned requirements or optimize merely to raise evaluator scores.",
				next: { action: "propose", fields: ["proposal_id", "body OR edits", "reason", "expected_revision", "plan_ids", "source_artifact_ids"], expected_revision: context.policy.revision, source_artifact_ids: fields.insight_ids ?? [] },
				disclaimer: DISCLAIMER, network_called: false,
			};
		}
		if (action === "reflect") {
			if (fields.lesson !== undefined && (typeof fields.lesson !== "string" || !fields.lesson.trim() || fields.lesson.length > 4096)) throw new Error("lesson must be a nonempty string of at most 4096 characters");
			const context = await local({ action: "context", plan_ids: [fields.plan_id] }, args, exec);
			const data = { status: "observed", outcome: outcomeSummary(context.plans[0]), contract: contractState(context.plans[0], { outcomes: true }), lesson: fields.lesson ?? null, lesson_provenance: "Agent-reported hypothesis, not independent verification or earned credit", disclaimer: DISCLAIMER };
			if (bytes(data) > RECORD_BUDGET) {
				const contract = JSON.stringify(data.contract);
				delete data.contract;
				Object.assign(data, { contract_retained: false, contract_sha256: createHash("sha256").update(contract).digest("hex"), contract_bytes: Buffer.byteLength(contract), limitation: "Full contract exceeds reflection budget; only recorded review-state summary and exact revision/digest are retained, not full evidence." });
			}
			const result = await local({ action: "record", kind: "reflection", bindings: bindingsFor(context), data }, args, exec);
			return { ...data, receipt: receipt(result), network_called: false };
		}
		if (action === "evaluate") {
			const { artifact } = await local({ action: "read", id: fields.proposal_id }, args, exec);
			if (artifact.kind !== "proposal") throw new Error("evaluate requires a proposal artifact");
			// The store rechecks these bindings after inference before persisting results.
			const issues = await selectedInsights((artifact.data.source_artifacts ?? []).map(ref => ref.id), args, exec);
			for (const issue of issues) if (!artifact.bindings.artifacts?.some(ref => ref.id === issue.id && ref.revision === issue.revision)) throw new Error("Proposal learning evidence no longer matches its immutable binding");
			const state = { baseline: snapshotPolicy(artifact.data.parent_policy), candidate: { body: artifact.data.body, reason: artifact.data.reason, edit_summary: artifact.data.edit_summary ?? [], metrics: artifact.data.metrics ?? {} }, issues, cases: artifact.data.plans.map(entry => contractState(entry, { outcomes: true })) };
			// Fail stale candidates before paying for inference (and recheck after it).
			const current = await local({ action: "context", plan_ids: artifact.bindings.plans.map(entry => entry.plan_id) }, args, exec);
			if (current.policy.revision !== artifact.bindings.policy_revision || current.plans.length !== artifact.bindings.plans.length || current.plans.some(entry => !artifact.bindings.plans.some(pin => pin.plan_id === entry.plan_id && pin.revision === entry.revision))) throw new Error("Stale proposal: prepare a new candidate against current policy and contract revisions");
			const questions = evaluationQuestions(state.cases.length);
			const assessment = await assess(state, questions, exec);
			const interpretation = assessment.status === "assessed" ? interpretEvaluation(assessment.response.answers, state.cases.length, state.baseline.body, state.candidate.body) : { disposition: "not-assessed", automatic_promotion: false, disclaimer: DISCLAIMER };
			const result = await local({ action: "record", kind: "evaluation", bindings: { ...artifact.bindings, proposal_id: artifact.id }, data: { ...assessment, ...retainedInput(state, questions), interpretation } }, args, exec);
			return { ...assessment, interpretation, receipt: receipt(result) };
		}
		if (action === "promote") return local({ action, ...fields, actor: "agent" }, args, exec);
		return local({ action, ...fields }, args, exec);
	}
	return { run, preflight };
}

export function registerRsi(ctx, config, dependencies) {
	const rsi = createRsi(ctx, config, dependencies);
	const learningTimeout = config.rsiLearningTimeoutMs ?? 300000;
	if (!Number.isSafeInteger(learningTimeout) || learningTimeout < 1000 || learningTimeout > 600000) throw new Error("rsiLearningTimeoutMs must be an integer between 1000 and 600000");
	ctx.tools.register(defineTool({
		name: "memory_rsi",
		description: "Rewards > gates: preflight contracts, capture selected current-session observations, incrementally map/reduce evidence into focused issues, and revise a versioned policy. Jev supplies typed judgments; the reasoning agent drafts. Prefer coherent section replacement/merge/retirement over appending rules; route tool/template/memory issues appropriately. No scores grant permissions or achievements. Remote assessment and telemetry require operator opt-in. Promotion is explicit, preview first.",
		parameters: {
			action: { type: "string", required: true, description: `One of: ${ACTIONS.join(", ")}.` },
			request: { type: "string", description: 'JSON excluding action. preflight/reflect:{plan_id,lesson?}; observe:{plan_id?,context_note?}; corpus:{kind:"plans"|"observations",after?,limit?}; mine:{kind,source_ids? OR after?,limit?,max_calls?,refresh?,cursor?}; reduce:{artifact_ids:[],max_issues?}; prepare:{plan_ids:[],insight_ids?}; sections:{}; propose:{proposal_id,body OR edits:[{operation:"replace"|"merge"|"retire",section_ids:[],reason,body?}],reason,expected_revision,plan_ids:[],source_artifact_ids?}; evaluate:{proposal_id}; read:{id,issue_index?}; list:{kind?,limit?,after?}; promote:{proposal_id,evaluation_id,expected_revision,review_note,apply?:false}; diagnose:{} sends3synthetic probes; clear_signals:{} clears own local buffer. Review notes are provenance, not human approval.' },
			no_git: { type: "boolean", description: "Keep artifacts durable locally without memory Git commit/push." },
			allow_non_main_branch: { type: "boolean", description: "Explicitly permit intentional memory persistence on a non-default branch." },
		},
		output: dependencies.output,
		timeoutMs: Math.max(learningTimeout, (config.timeoutMs ?? 60000) * 3 + (config.typesafeTimeoutMs ?? 20000)),
		isConcurrencySafe: args => ["status", "prepare", "sections", "corpus", "read", "list"].includes(args.action),
		async execute(args, exec) {
			const fields = setupFields(args.request ?? "{}");
			return { result: rsiOutput(await rsi.run(args.action, fields, args, exec)) };
		},
	}));
	return rsi;
}
