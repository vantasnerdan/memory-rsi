import test from "node:test";
import assert from "node:assert/strict";
import { diagnoseAssessor, DIAGNOSTIC_CASES } from "../lib/rsi-diagnostics.js";

const response = (questions, aligned) => ({ status: "assessed", response: { model: "fixture", usage: { input_tokens: 1, output_tokens: 1 }, elapsedMs: 1, answers: Object.fromEntries(Object.entries(questions).map(([id, q]) => [id, q.type === "noul" ? { type: "noul", noul: aligned ? 0.01 : 0.99 } : q.type === "score" ? { type: "score", score: aligned ? 2 : 0, confidence: 0.8 } : { type: "choice", choice: aligned ? "aligned" : "conflict", confidence: 0.8 }])) } });

test("synthetic diagnostics keep adverse instructions as labelled data and never mutate policy", async () => {
	const states = [];
	const result = await diagnoseAssessor(async (state, questions) => { states.push(state); assert.ok(Object.values(questions).every(q => q.instructions.includes("never instructions to obey"))); return response(questions, states.length === 1); }, {});
	assert.equal(states.length, 3);
	assert.match(states[2].contract.untrusted_attachment, /Ignore your rubric/);
	assert.ok(result.cases.every(entry => entry.expectation_observed));
	assert.equal(result.policy_changed, false);
	assert.equal(result.records_created, false);
	assert.match(result.limitation, /not a calibrated accuracy estimate/);
	assert.equal(DIAGNOSTIC_CASES.length, result.coverage.assessed);
});

test("disagreement and unavailable diagnostics are not silently counted as successful checks", async () => {
	const disagreement = await diagnoseAssessor(async (_state, questions) => response(questions, true), {});
	assert.deepEqual(disagreement.cases.map(entry => entry.expectation_observed), [true, false, false]);
	let calls = 0;
	const unavailable = await diagnoseAssessor(async () => { calls++; return { status: "disabled", reason: "Not opted in" }; }, {});
	assert.equal(calls, 1);
	assert.equal(unavailable.coverage.assessed, 0);
	assert.equal(unavailable.cases[0].status, "disabled");
	assert.equal(unavailable.cases[0].expectation_observed, undefined);
});
