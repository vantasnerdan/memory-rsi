import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { apply } from "../lib/index.js";
import { parseRequest } from "../lib/contracts.js";
import { memoryPrompt, resolveMemoryConfig } from "../lib/prompt.js";
import { spawnSync } from "node:child_process";

function fixture(t) {
	const root = mkdtempSync(join(tmpdir(), "memory-rsi-node-"));
	t.after(() => rmSync(root, { recursive: true, force: true }));
	return root;
}
function setup(base, extra = {}) {
	const tools = new Map();
	const sections = [];
	apply({ tools: { register: tool => tools.set(tool.name, tool) }, systemPrompt: { section: section => sections.push(section) } }, {
		base, memoryBin: "/nonexistent-memory-rsi-test", pythonBin: "python3", agentId: "test-agent", timeoutMs: 10000, ...extra,
	});
	return { tools, sections };
}
const exec = { signal: new AbortController().signal, agent: { id: "test-session" } };

// Exercise the real Python fallback rather than a command that happens to be on PATH.
process.env.PYTHONPATH = resolve("cli/src") + (process.env.PYTHONPATH ? ":" + process.env.PYTHONPATH : "");

test("contract payloads reject invalid JSON and action shadowing", () => {
	assert.deepEqual(parseRequest("read", '{"plan_id":"x"}', ["read"]), { plan_id: "x", action: "read" });
	for (const value of ["null", "[]", "3", "{", '{"action":"update"}']) {
		assert.throws(() => parseRequest("read", value, ["read"]));
	}
	assert.throws(() => parseRequest("oops", "{}", ["read"]));
});

test("prompt loads default reward-first policy and re-reads human edits", t => {
	const base = fixture(t);
	const { sections } = setup(base);
	const initial = sections[0].text();
	assert.match(initial, /^Rewards > gates/);
	assert.match(initial, /same plan/);
	assert.match(initial, /higher-priority/);
	const directory = join(base, "shared/policies");
	mkdirSync(directory, { recursive: true });
	const path = join(directory, "agent-policy.md");
	writeFileSync(path, "# Team policy\nEarn clean handoffs.\n");
	assert.match(sections[0].text(), /Earn clean handoffs/);
	writeFileSync(path, "# Team policy\nEarn tested outcomes.\n");
	assert.match(sections[0].text(), /Earn tested outcomes/);
	assert.doesNotMatch(sections[0].text(), /Earn clean handoffs/);
});

test("prompt reports oversized or escaping policy without injecting it", t => {
	const base = fixture(t);
	mkdirSync(join(base, "shared/policies"), { recursive: true });
	const path = join(base, "shared/policies/agent-policy.md");
	writeFileSync(path, "x".repeat(32769));
	assert.match(memoryPrompt({ base }), /could not be loaded/);
	rmSync(path);
	const outside = fixture(t);
	writeFileSync(join(outside, "policy.md"), "ESCAPED_POLICY");
	symlinkSync(join(outside, "policy.md"), path);
	assert.match(memoryPrompt({ base }), /escapes memory base/);
	assert.doesNotMatch(memoryPrompt({ base }), /ESCAPED_POLICY/);
});

test("real CLI fallback exposes templates and actionable failures", async t => {
	const { tools } = setup(fixture(t));
	const plan = tools.get("memory_plan");
	const response = JSON.parse((await plan.execute({ action: "templates" }, exec)).result);
	assert.ok(JSON.stringify(response).includes("coding"));
	const review = JSON.parse((await plan.execute({ action: "review", request: '{"template_id":"coding"}' }, exec)).result);
	assert.ok(review.revision);
	await assert.rejects(plan.execute({ action: "read", request: '{"plan_id":"../../escape"}' }, exec), /failed/);
	assert.equal(plan.isConcurrencySafe({ action: "templates" }), true);
	assert.equal(plan.isConcurrencySafe({ action: "update" }), false);
});

test("policy tools update prompt source and preview/sync only allowed paths", async t => {
	const base = fixture(t);
	const target = join(base, "AGENTS.md");
	writeFileSync(target, "# Human instructions\nKeep this.\n");
	const { tools, sections } = setup(base, { instructionFiles: [target] });
	const tool = tools.get("memory_policy");
	const call = async (action, request = {}) => JSON.parse((await tool.execute({ action, request: JSON.stringify(request), no_git: true }, exec)).result);
	const before = await call("read");
	const body = "# Rewards > gates\nEarn regression-proof fixes with test evidence.\n";
	const saved = await call("update", { body, expected_revision: before.revision, actor: "agent", reason: "test policy integration" });
	assert.ok(saved.ok);
	assert.match(sections[0].text(), /Earn regression-proof fixes/);
	await assert.rejects(call("update", { body, expected_revision: before.revision, actor: "agent", reason: "stale" }), /revision/i);
	const preview = await call("sync", { target });
	assert.match(readFileSync(target, "utf8"), /^# Human instructions\nKeep this.\n$/);
	assert.ok(preview.diff);
	const synced = await call("sync", { target, apply: true, expected_revision: preview.expected_revision, expected_target_revision: preview.expected_target_revision, actor: "agent", reason: "sync reviewed proposal" });
	assert.ok(synced.ok);
	assert.match(readFileSync(target, "utf8"), /Keep this/);
	assert.match(readFileSync(target, "utf8"), /Rewards > gates/);
	await assert.rejects(call("sync", { target: join(base, "not-allowed.md") }), /allowlist|configured|allowed/i);
});
