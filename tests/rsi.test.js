import test from "node:test";
import assert from "node:assert/strict";
import { createRsi } from "../lib/rsi.js";
import { policySections, preflightQuestions, evaluationQuestions, interpretPreflight, interpretEvaluation, outcomeSummary } from "../lib/rsi-rubric.js";
import { registerContracts } from "../lib/contracts.js";

const exec = { agent: { id: "session-test" }, signal: new AbortController().signal };
const config = { typesafeEnabled: true, typesafeApiKeyEnv: "TYPESAFE_TEST", timeoutMs: 10000 };
const plan = { plan_id: "job", template: { template_id: "coding", revision: "template-v1", content: { achievements: [{ id: "outcome", description: "Useful tested outcome" }] } }, task: { title: "Ship a fix", good: "The regression test passes" }, work_items: [{ id: "work", title: "Fix", owner: "agent", requirement_ids: ["outcome"], status: "pending", evidence: [], exceptions: [] }] };
function context() { return { policy: { body: "Reward useful verified outcomes. Preserve permissions.", revision: "policy-v1" }, plans: [{ plan_id: "job", revision: "plan-v1", plan: structuredClone(plan), validation: { complete: false, work_items: [{ work_item_id: "work", missing_requirements: ["outcome"] }] } }] }; }
function answersFor(questions) {
	return Object.fromEntries(Object.entries(questions).map(([id, q]) => {
		if (q.type === "noul") return [id, { type: q.type, noul: 0.01 }];
		if (q.type === "score") return [id, { type: q.type, score: 2, confidence: 0.95 }];
		return [id, { type: q.type, choice: id.startsWith("policy_") ? "covered" : id === "alignment" ? "aligned" : id === "preservation" ? "preserved" : id === "outcome_support" ? "insufficient" : "equivalent", confidence: 0.95 }];
	}));
}
function fixture(extra = {}) {
	const calls = [], states = [], records = new Map();
	let credentialReads = 0;
	const ctx = { get: () => ({ resolve: async () => { credentialReads++; return { value: "secret-for-test" }; }, describe: async () => ({ configured: true }) }) };
	const memory = async (_config, _argv, { stdin }) => {
		const request = JSON.parse(stdin); calls.push(request);
		if (request.action === "context") return JSON.stringify(context());
		if (request.action === "sections") return JSON.stringify({ policy: context().policy, sections: [] });
		if (request.action === "record") {
			const artifact = { id: `receipt-${records.size}`, kind: request.kind, revision: "record-v1", bindings: request.bindings, data: request.data, path: "/memory/receipt.md", freshness: { current: true } };
			records.set(artifact.id, artifact);
			return JSON.stringify({ artifact, persistence: { saved: true } });
		}
		if (request.action === "read") return JSON.stringify({ artifact: records.get(request.id) });
		return JSON.stringify({ ok: true });
	};
	const evaluate = async ({ state, questions }, _config, opts) => { assert.equal(opts.apiKey, "secret-for-test"); states.push(state); return { model: "jev-test", answers: answersFor(questions), usage: { input_tokens: 100, output_tokens: 20 }, elapsedMs: 1 }; };
	return { rsi: createRsi(ctx, { ...config, ...extra.config }, { memory: extra.memory ?? memory, evaluate: extra.evaluate ?? evaluate }), calls, states, records, get credentialReads() { return credentialReads; } };
}

test("preflight batches typed coaching, persists exact bindings and never awards achievements", async () => {
	const f = fixture();
	const result = await f.rsi.preflight("job", { no_git: true }, exec);
	assert.equal(result.status, "assessed");
	assert.equal(result.interpretation.permission_effect, "none");
	assert.equal(result.interpretation.achievement_credit, "none-before-outcomes");
	assert.equal(f.states.length, 1);
	assert.equal(f.calls.at(-1).bindings.policy_revision, "policy-v1");
	assert.equal(f.calls.at(-1).bindings.plans[0].revision, "plan-v1");
	assert.doesNotMatch(JSON.stringify(f.calls), /secret-for-test/);
	assert.ok(f.calls.at(-1).data.evaluator.sha256);
	assert.equal(f.credentialReads, 1);
});

test("disabled and status operations never invoke inference or resolve secrets", async () => {
	const f = fixture({ config: { typesafeEnabled: false }, evaluate: () => { throw new Error("must not call"); } });
	assert.equal((await f.rsi.preflight("job", {}, exec)).status, "disabled");
	assert.equal((await f.rsi.run("status", {}, {}, exec)).network_called, false);
	assert.equal(f.credentialReads, 0);
	assert.equal(f.calls.length, 0);
});

test("assessor failures are not compliance and raw errors never enter artifacts", async () => {
	const f = fixture({ evaluate: async () => { throw new Error("upstream echoed secret-for-test"); } });
	const result = await f.rsi.preflight("job", {}, exec);
	assert.equal(result.status, "unavailable");
	assert.equal(result.interpretation.disposition, "not-assessed");
	assert.doesNotMatch(JSON.stringify([...f.records.values(), result]), /secret-for-test/);
});

test("assessment error codes never invoke accessors or leak malformed throws", async () => {
	for (const action of ["preflight", "evaluate"]) {
		let reads = 0;
		const changing = Object.defineProperty({}, "code", { get() { return ++reads === 1 ? "TYPESAFE_TIMEOUT" : "SECRET_CHANGED_GETTER_VALUE"; } });
		const throwing = Object.defineProperty({}, "code", { get() { throw new Error("SECRET_GETTER_SENTINEL"); } });
		const proxy = new Proxy({}, { getOwnPropertyDescriptor() { throw new Error("SECRET_PROXY_SENTINEL"); } });
		for (const failure of [changing, throwing, proxy, null, "SECRET_STRING", { code: "TYPESAFE_SECRET_UNKNOWN" }, Object.assign(new Error("SECRET_MESSAGE"), { code: "TYPESAFE_TIMEOUT" })]) {
			const f = fixture({ evaluate: async () => { throw failure; } });
			const c = context();
			f.records.set("candidate", { id: "candidate", kind: "proposal", bindings: { plans: [{ revision: "plan-v1", plan_id: "job" }], policy_revision: "policy-v1" }, data: { parent_policy: c.policy, body: c.policy.body, reason: "Test", plans: c.plans } });
			const result = action === "preflight" ? await f.rsi.preflight("job", {}, exec) : await f.rsi.run("evaluate", { proposal_id: "candidate" }, {}, exec);
			assert.equal(result.status, "unavailable");
			assert.equal(result.error_code, failure instanceof Error ? "TYPESAFE_TIMEOUT" : "assessment_failed");
			assert.doesNotMatch(JSON.stringify([...f.records.values(), result]), /SECRET/);
		}
		assert.equal(reads, 0);
	}
});

test("safe validation diagnostics survive but diagnostic accessors stay private", async () => {
	for (const accessor of [false, true]) {
		let reads = 0;
		const error = Object.assign(new Error("SECRET_PROVIDER_BODY"), { code: "TYPESAFE_RESPONSE_INVALID" });
		if (accessor) Object.defineProperty(error, "validation_code", { get() { reads++; throw new Error("SECRET_DIAGNOSTIC"); } });
		else error.validation_code = "TYPESAFE_INVALID_DISTRIBUTION_SUM";
		const f = fixture({ evaluate: async () => { throw error; } });
		const result = await f.rsi.preflight("job", {}, exec);
		assert.equal(result.error_code, "TYPESAFE_RESPONSE_INVALID");
		assert.equal(result.validation_code, accessor ? undefined : "TYPESAFE_INVALID_DISTRIBUTION_SUM");
		assert.equal(reads, 0);
		assert.doesNotMatch(JSON.stringify([...f.records.values(), result]), /SECRET/);
	}
});

test("create returns the durable plan even when coaching persistence fails", async () => {
	const tools = [];
	registerContracts({ tools: { register: tool => tools.push(tool) } }, { timeoutMs: 1000 }, { memory: async () => JSON.stringify({ revision: "saved", plan }), clip: x => x, output: { schema: { type: "string" } }, rsi: { preflight: async () => { throw new Error("stale revision"); } } });
	const result = JSON.parse((await tools[0].execute({ action: "create", request: "{}" }, exec)).result);
	assert.equal(result.revision, "saved");
	assert.equal(result.policy_preflight.status, "unavailable");
});

test("create runs coaching exactly after successful save", async () => {
	const order = [], tools = [];
	registerContracts({ tools: { register: tool => tools.push(tool) } }, { timeoutMs: 1000 }, { memory: async () => { order.push("save"); return JSON.stringify({ plan }); }, clip: x => x, output: { schema: { type: "string" } }, rsi: { preflight: async id => { assert.equal(id, "job"); order.push("coach"); return { status: "assessed" }; } } });
	assert.equal(JSON.parse((await tools[0].execute({ action: "create", request: "{}" }, exec)).result).policy_preflight.status, "assessed");
	assert.deepEqual(order, ["save", "coach"]);
});

test("prepare and reflect use selected recorded outcomes locally, not a Jev text generator", async () => {
	const f = fixture();
	const preparation = await f.rsi.run("prepare", { plan_ids: ["job"] }, {}, exec);
	assert.match(preparation.brief, /Jev cannot generate prose/);
	assert.equal(preparation.outcomes[0].accepted_evidence, 0);
	const reflection = await f.rsi.run("reflect", { plan_id: "job", lesson: "Try narrower evidence next time" }, {}, exec);
	assert.equal(reflection.status, "observed");
	assert.match(reflection.lesson_provenance, /hypothesis/);
	assert.equal(f.states.length, 0);
});

test("unknown fields cannot configure endpoints or supply fabricated assessments", async () => {
	const f = fixture();
	await assert.rejects(f.rsi.run("preflight", { plan_id: "job", endpoint: "https://evil.test" }, {}, exec), /Unknown RSI fields/);
	await assert.rejects(f.rsi.run("record", { data: { status: "assessed" } }, {}, exec), /Unknown RSI action/);
	await assert.rejects(f.rsi.run("promote", { actor: "human" }, {}, exec), /Unknown RSI fields/);
});

test("candidate evaluation separates model preference from outcome evidence and promotion", async () => {
	const f = fixture();
	const c = context();
	f.records.set("candidate", { id: "candidate", kind: "proposal", bindings: { plans: [{ revision: "plan-v1", plan_id: "job" }], policy_revision: "policy-v1" }, data: { parent_policy: c.policy, body: c.policy.body + " Keep contracts focused.", reason: "Hypothesis", plans: c.plans } });
	const result = await f.rsi.run("evaluate", { proposal_id: "candidate" }, {}, exec);
	assert.equal(result.status, "assessed");
	assert.equal(result.interpretation.disposition, "needs-outcome-evidence");
	assert.equal(result.interpretation.automatic_promotion, false);
	assert.equal(f.calls.at(-1).bindings.proposal_id, "candidate");
	assert.equal(f.calls.filter(c => c.action === "promote").length, 0);
});

test("stale candidates do not consume an API call", async () => {
	const f = fixture(); const c = context();
	f.records.set("old", { id: "old", kind: "proposal", bindings: { policy_revision: "old-policy", plans: [{ plan_id: "job", revision: "plan-v1" }] }, data: { parent_policy: c.policy, body: "body", reason: "reason", plans: c.plans } });
	await assert.rejects(f.rsi.run("evaluate", { proposal_id: "old" }, {}, exec), /Stale proposal/);
	assert.equal(f.states.length, 0);
});

test("boundary concerns cannot be compensated by excellent quality scores", () => {
	const pre = answersFor(preflightQuestions()); pre.boundary_conflict.noul = 0.99;
	assert.equal(interpretPreflight(pre).disposition, "review-conflict");
	const ev = answersFor(evaluationQuestions(1)); ev.preservation.choice = "weakened";
	assert.equal(interpretEvaluation(ev, 1, "old", "new").disposition, "human-review-required");
});

test("outcome summary distinguishes reported, reviewed, rejected and exceptions", () => {
	const c = context().plans[0];
	c.plan.work_items[0].evidence = [{ reviews: [] }, { reviews: [{ verdict: "accepted" }] }, { reviews: [{ verdict: "accepted" }, { verdict: "rejected" }] }];
	const summary = outcomeSummary(c);
	assert.equal(summary.reported_evidence, 3); assert.equal(summary.accepted_evidence, 1);
	assert.equal(summary.unreviewed_evidence, 1); assert.equal(summary.rejected_evidence, 1);
});

test("auto-create preflight refuses another plan revision before inference", async () => {
	const f = fixture();
	await assert.rejects(f.rsi.preflight("job", {}, exec, "created-earlier"), /changed after creation/);
	assert.equal(f.states.length, 0);
});

test("oversized selected evidence records compact unavailability without paid inference", async () => {
	let saved;
	const f = fixture({ memory: async (_c, _a, { stdin }) => {
		const request = JSON.parse(stdin);
		if (request.action === "context") { const c = context(); c.plans[0].plan.task.good = "x".repeat(125000); return JSON.stringify(c); }
		saved = request;
		return JSON.stringify({ artifact: { id: "oversized", revision: "v1", path: "/receipt", bindings: request.bindings }, persistence: { saved: true } });
	} });
	const result = await f.rsi.preflight("job", {}, exec);
	assert.equal(result.status, "unavailable");
	assert.equal(result.error_code, "RSI_INPUT_BUDGET");
	assert.equal(saved.data.input_retained, false);
	assert.equal(saved.data.state, undefined);
	assert.ok(saved.data.input_sha256);
	assert.equal(f.states.length, 0); assert.equal(f.credentialReads, 0);
});

test("credential resolution is time bounded before inference", async () => {
	let called = false;
	const f = fixture();
	const rsi = createRsi({ get: () => ({ resolve: () => new Promise(() => {}) }) }, { ...config, typesafeTimeoutMs: 25 }, {
		memory: async (_c, _a, { stdin }) => { const r = JSON.parse(stdin); if (r.action === "context") return JSON.stringify(context()); return JSON.stringify({ artifact: { id: "timeout", bindings: r.bindings } }); },
		evaluate: async () => { called = true; },
	});
	const result = await rsi.preflight("job", {}, exec);
	assert.equal(result.status, "unavailable"); assert.equal(result.error_code, "TYPESAFE_TIMEOUT"); assert.equal(called, false);
});

test("policy-specific gaps produce grounded section coaching; inapplicable clauses add no ritual", () => {
	const sections = policySections("# Rewards\nEarn tested outcomes.\n## Publication\nPush only when authorized.\n");
	const questions = preflightQuestions(sections), answers = answersFor(questions);
	answers.policy_0.choice = "covered"; answers.policy_1.choice = "not_applicable";
	assert.equal(interpretPreflight(answers, sections).opportunities.length, 0);
	answers.policy_1.choice = "conflict";
	const interpreted = interpretPreflight(answers, sections);
	assert.equal(interpreted.disposition, "review-conflict");
	assert.equal(interpreted.opportunities[0].section, "Publication");
	const many = policySections(Array.from({ length: 20 }, (_, i) => `## Policy ${i}\nrule-${i}`).join("\n"));
	assert.equal(many.length, 12); assert.ok(many.at(-1).text.includes("rule-19"));
});
