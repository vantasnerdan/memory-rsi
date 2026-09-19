import test from "node:test";
import assert from "node:assert/strict";
import { rsiOutput } from "../lib/rsi-output.js";

const revision = "sha256:" + "a".repeat(64);
const verbose = "detail-".repeat(20000);
const cursor = { kind: "plans", source_ids: ["work"], max_calls: 4, cursor: { source_id: "work", revision, unit: 128, projection: "c".repeat(64), selection: "d".repeat(64) } };

test("large cached mining turns preserve every work identity and continuation", () => {
	const receipts = Array.from({ length: 128 }, (_, i) => ({ id: `map-${String(i).padStart(64, "0")}`, revision, source_id: "work", unit: i, cache_hit: true, status: "assessed", path: "/memory/" + "long-path/".repeat(160), freshness: { direct: "x".repeat(1000) }, persistence: { saved: true, git: { status: "pushed", committed: true, pushed: true, commit: "b".repeat(40) } } }));
	const original = { status: "partial", receipts, resume: cursor, next_after: null, has_more: true, calls: 0, cache_hits: 128, usage: { input_tokens: 0, output_tokens: 0 }, coverage: { page_only: true, selected_page_complete: false, whole_corpus_reviewed: false, sources: [{ id: "work", revision, units_total: 200, start_unit: 0, units_completed: 128 }] }, warnings: ["Only selected units are mapped"] };
	assert.ok(JSON.stringify(original).length > 90000);
	const encoded = rsiOutput(original), result = JSON.parse(encoded);
	assert.ok(encoded.length < 90000);
	assert.deepEqual(result.receipts.map(item => item.id), receipts.map(item => item.id));
	assert.deepEqual(result.receipts.map(item => item.revision), receipts.map(item => item.revision));
	assert.deepEqual(result.resume, cursor);
	assert.equal(result.next_after, null);
	assert.equal(result.has_more, true);
	assert.equal(result.calls, 0);
	assert.equal(result.cache_hits, 128);
	assert.equal(result.coverage.sources[0].units_completed, 128);
	assert.equal(result.coverage.whole_corpus_reviewed, false);
	assert.equal(result.receipts[0].persistence.git.pushed, true);
	assert.equal(result.receipts[0].persistence.git.commit, "b".repeat(40));
	assert.deepEqual(result.warnings, original.warnings);
});

test("large reductions preserve saved receipt and deferred-group recovery", () => {
	const coverage = { whole_corpus_reviewed: false, groups_total: 9, groups_selected: 4, deferred_groups: [{ key: "other", root_indices: [2, 8, 40] }], resume_instruction: "Use root_indices into saved lineage.maps" };
	const result = JSON.parse(rsiOutput({ status: "partial", receipt: { id: "insight-example", revision, persistence: { saved: true, git: { status: "disabled" } } }, issues: [{ key: "context", operation: "rewrite", destination: "policy", witnesses: verbose }], coverage, calls: 4, warnings: ["Counterexamples remain"], author_brief: "Inspect sources" }));
	assert.equal(result.receipt.id, "insight-example");
	assert.deepEqual(result.coverage, coverage);
	assert.equal(result.issues[0].issue_index, 0);
	assert.equal(result.issues[0].operation, "rewrite");
	assert.equal(result.issues[0].witnesses, undefined);
	assert.match(result.reason, /issue_index/);
});

test("large list/corpus pages retain every identity and advancement", () => {
	for (const key of ["artifacts", "sources"]) {
		const result = JSON.parse(rsiOutput({ ok: true, action: key === "artifacts" ? "list" : "corpus", [key]: [{ id: "first", revision, freshness: verbose }], next_after: "first", has_more: true, scanned: 1 }));
		assert.equal(result[key][0].id, "first");
		assert.equal(result.next_after, "first");
		assert.equal(result.has_more, true);
	}
});

test("large preparation retains insight/section identities and small results stay exact", () => {
	const original = { status: "complete", receipts: [], resume: null };
	assert.deepEqual(JSON.parse(rsiOutput(original)), original);
	const prepared = JSON.parse(rsiOutput({ policy: { body: "Keep useful evidence", revision }, brief: "Revise a coherent section", cases: [{ plan_id: "work", revision, contract: verbose }], sections: [{ section_id: "section-a", title: "Evidence", revision, body: "Keep useful evidence" }], issues: [{ id: "insight-1", revision, issues: verbose }], next: { source_artifact_ids: ["insight-1"] } }));
	assert.equal(prepared.sections[0].section_id, "section-a");
	assert.equal(prepared.issues[0].id, "insight-1");
	assert.equal(prepared.network_called, false);
});

test("compaction preserves safe failure codes and projection/usage coverage", () => {
	const source = { id: "selected", projection_version: "projection/2", projection_modes: ["telemetry-report-units"], source_reviewed_in_full: false, projection_exclusions_present: true };
	const usage = { input_tokens: 4, output_tokens: 0, complete: false, unavailable_calls: 1, unknown_usage_calls: 1, scope: "Validated subtotals only" };
	const output = JSON.parse(rsiOutput({ status: "unavailable", error_code: "TYPESAFE_TIMEOUT", validation_code: "TYPESAFE_INVALID_SCORE_WEIGHT", receipts: [{ id: "failed-map", revision, status: "unavailable", error_code: "TYPESAFE_TIMEOUT", validation_code: "TYPESAFE_INVALID_SCORE_WEIGHT", freshness: verbose }], resume: cursor, usage, coverage: { sources: [source] } }));
	assert.equal(output.error_code, "TYPESAFE_TIMEOUT");
	assert.equal(output.receipts[0].error_code, "TYPESAFE_TIMEOUT");
	assert.equal(output.validation_code, "TYPESAFE_INVALID_SCORE_WEIGHT");
	assert.equal(output.receipts[0].validation_code, "TYPESAFE_INVALID_SCORE_WEIGHT");
	assert.deepEqual(output.coverage.sources[0], source);
	assert.deepEqual(output.usage, usage);
	assert.deepEqual(output.resume, cursor);
	const reduction = JSON.parse(rsiOutput({ status: "partial", receipt: { id: "reduction", revision }, issues: [{ key: "unknown", error_code: "TYPESAFE_HTTP", witnesses: verbose }] }));
	assert.equal(reduction.issues[0].error_code, "TYPESAFE_HTTP");
});
