import { defineTool } from "@deepseek-ai/dsh-tools";
import { createHash } from "node:crypto";
import { setupFields } from "./setup-json.js";
import { withinAssessmentBudget } from "./rsi-budget.js";
import { safeAssessmentCode } from "./rsi-errors.js";
import { createLearning } from "./rsi-learning.js";
import { diagnoseAssessor } from "./rsi-diagnostics.js";
import { rsiOutput } from "./rsi-output.js";
import { createInstructionAudit } from "./rsi-instructions.js";
import { createInstructionSourceDiscovery } from "./rsi-source-discovery.js";
import { createTrialReview } from "./rsi-trials.js";
import { RECORD_BUDGET, bytes, reviewFits, retainedInput } from "./rsi-assessment-data.js";
export { rsiOutput } from "./rsi-output.js";
import { evaluateTypesafe, typesafeConfig } from "./typesafe.js";
import { policySections, preflightQuestions, evaluationQuestions, rubricIdentity, interpretPreflight, interpretEvaluation, contractState, outcomeSummary, DISCLAIMER } from "./rsi-rubric.js";

const ACTIONS = ["status", "diagnose", "audit", "trial_spec", "trial_results", "trial_review", "preflight", "prepare", "sections", "propose", "evaluate", "reflect", "observe", "clear_signals", "corpus", "mine", "reduce", "read", "list", "promote"];
const FIELDS = {
	status: [], diagnose: [], audit: ["sources", "plan_id", "feedback_ids"],
	trial_spec: ["trial_id", "proposal_id", "hypothesis", "procedure", "stopping_rule", "metrics", "cases", "environment"],
	trial_results: ["trial_id", "trial_revision", "results", "review_note"], trial_review: ["results_id"],
	preflight: ["plan_id"], prepare: ["plan_ids", "insight_ids"], sections: [],
	propose: ["proposal_id", "body", "edits", "reason", "expected_revision", "plan_ids", "source_artifact_ids"],
	observe: ["plan_id", "context_note", "lesson"], clear_signals: [],
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
function selectedLesson(value) {
	if (value === undefined) return null;
	if (!value || typeof value !== "object" || Array.isArray(value)
		|| Object.keys(value).sort().join(",") !== "applicability,failure,fix"
		|| Object.values(value).some(part => typeof part !== "string" || !part.trim() || part.length > 2048)) {
		throw new Error("lesson must contain nonempty failure, fix and applicability strings");
	}
	return { failure: value.failure, fix: value.fix, applicability: value.applicability };
}
const bindingsFor = context => ({ policy_revision: context.policy.revision, plans: context.plans.map(entry => ({ plan_id: entry.plan_id, revision: entry.revision })) });
const snapshotPolicy = policy => ({ body: policy.body, revision: policy.revision });
const receipt = result => ({ id: result.artifact.id, revision: result.artifact.revision, path: result.artifact.path, bindings: result.artifact.bindings, freshness: result.artifact.freshness, persistence: result.persistence });

/** Network and persistence are separate dependencies so protocol tests need no live key. */
export function createRsi(ctx, config, { memory, evaluate = evaluateTypesafe, signals, learningFactory = createLearning, discover }) {
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
	const audit = createInstructionAudit({ local, assess, discover });
	const reviewTrial = createTrialReview({ local, assess });
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
		if (action === "audit") return audit(fields, args, exec);
		if (action === "trial_review") return reviewTrial(fields, args, exec);
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
			const data = { status: "observed", telemetry: signals.snapshot(exec.agent), context_note: fields.context_note ?? null, ...(fields.lesson !== undefined ? { lesson: selectedLesson(fields.lesson) } : {}), note_provenance: "Explicitly selected agent-authored context summary; not independently verified, not a raw session transcript. Review before remote mining.", disclosure: "Local persistence only. A later explicit mine selects this observation for TypeSafe.", disclaimer: DISCLAIMER };
			const result = await local({ action: "record", kind: "observation", bindings: bindingsFor(context), data }, args, exec);
			return { ...data, receipt: receipt(result), network_called: false };
		}
		if (action === "status") {
			const resolved = typesafeConfig(config);
			const service = ctx.get?.("credentials");
			const configured = service ? (await service.describe(resolved.typesafeApiKeyEnv)).configured : Boolean(process.env[resolved.typesafeApiKeyEnv]);
			return { enabled: config.typesafeEnabled === true, configured, protocol: "typesafe-systemone-v1", endpoint: resolved.typesafeEndpoint, model: resolved.typesafeModel, credential_reference: resolved.typesafeApiKeyEnv, timeout_ms: resolved.typesafeTimeoutMs, instruction_discovery: { enabled: config.rsiInstructionDiscoveryEnabled === true, classes: ["system", "agents", "skill"], coverage: "full-members-or-complete-chunks", feedback_selection: "explicit-insight-or-trial-artifact-ids", requires_current_agent: true, automatic_apply: false }, telemetry: signals?.status(exec.agent) ?? { enabled: false, status: "unavailable" }, network_called: false, disclaimer: DISCLAIMER };
		}
		if (action === "preflight") return preflight(fields.plan_id, args, exec);
		if (action === "prepare") {
			const context = await local({ action: "context", plan_ids: fields.plan_ids ?? [] }, args, exec);
			const issueBriefs = await selectedInsights(fields.insight_ids ?? [], args, exec);
			const sectionView = await local({ action: "sections" }, args, exec);
			if (sectionView.policy.revision !== context.policy.revision) throw new Error("Policy changed while preparing; retry against the current revision");
			return {
				policy: context.policy, sections: sectionView.sections, issues: issueBriefs, cases: context.plans.map(entry => contractState(entry, { outcomes: true })), outcomes: context.plans.map(outcomeSummary),
				brief: "As the reasoning author, draft at most one small coherent policy improvement, or recommend no change. Jev cannot generate prose. Preserve actual user outcomes and higher-priority safety and permission boundaries; process rules may be added, revised, relaxed or retired. Use available concrete outcomes and lessons without inventing corroboration. Explain expected benefit, known limitations and any applicable rollback condition. Put job-specific skills/rules/tool details in a separately versioned template or contract, not global policy. Respect current user direction and never optimize merely to raise evaluator scores.",
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
	const discover = createInstructionSourceDiscovery(ctx, config);
	const rsi = createRsi(ctx, config, { ...dependencies, discover });
	const learningTimeout = config.rsiLearningTimeoutMs ?? 300000;
	if (!Number.isSafeInteger(learningTimeout) || learningTimeout < 1000 || learningTimeout > 600000) throw new Error("rsiLearningTimeoutMs must be an integer between 1000 and 600000");
	ctx.tools.register(defineTool({
		name: "memory_rsi",
		description: "Rewards > gates: when separately operator-enabled, audit without sources captures every admitted current system-prompt, configured AGENTS-like and active model-invocable skill member in full local-only snapshots, maps complete members/chunks, and optionally relates them to explicitly selected source-linked insight/trial feedback. Model labels do not establish causality or authorization. Selected concrete evidence can inform a proposal; report uncertainties honestly. No raw transcript, tool argument/output or chain-of-thought is captured. Preflight, evaluation and paired trials are optional; promotion uses the authorized revision-checked writer.",
		parameters: {
			action: { type: "string", required: true, description: `One of: ${ACTIONS.join(", ")}.` },
			request: { type: "string", description: 'JSON excluding action. audit:{plan_id?,feedback_ids?:[]} auto-discovers full admitted current system-prompt, configured AGENTS-like and active-skill members when rsiInstructionDiscoveryEnabled, stores exact local-only chunks before inference, and uses only explicitly selected insight/trial_review feedback; optional sources:[{id,kind:system|agents|skill|template|memory,scope,body}] keeps legacy explicit 1..5-source mode and cannot take feedback_ids. Automatic audits force local-only no_git persistence; incomplete mapping is reported; missing corroboration remains unknown. trial_spec:{trial_id,proposal_id,hypothesis,procedure,stopping_rule,environment:{agent_model,tool_environment,max_tokens,max_steps},metrics:{outcome,safety,cost_unit},cases:[{case_id,revision:"sha256:<digest>",split:development|holdout|control,family}]}; trial_results:{trial_id,trial_revision,results:[{case_id,baseline,candidate}],review_note}, each arm:{status:observed|missing|invalid,outcome:boolean|null,safety_violations:int|null,cost:number|null,environment:object|null,evidence_refs:[],note}; trial_review:{results_id}. preflight/reflect:{plan_id,lesson?}; observe:{plan_id?,context_note?,lesson?:{failure,fix,applicability}}; corpus:{kind:"plans"|"observations",after?,limit?}; mine:{kind,source_ids? OR after?,limit?,max_calls?,refresh?,cursor?}; reduce:{artifact_ids:[],max_issues?}; prepare:{plan_ids:[],insight_ids?}; sections:{}; propose:{proposal_id,body OR edits:[{operation:"add"|"replace"|"merge"|"retire",section_ids:[],reason,body?}],reason,expected_revision,plan_ids:[],source_artifact_ids?}; evaluate:{proposal_id}; read:{id,issue_index?}; list:{kind?,limit?,after?}; promote:{proposal_id,evaluation_id?,expected_revision,review_note,apply?:false}; diagnose:{} sends3synthetic probes; clear_signals:{} clears own local buffer. Review notes are provenance, not human approval.' },
			no_git: { type: "boolean", description: "Keep artifacts durable locally without memory Git commit/push; automatic discovery audits force this true." },
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
