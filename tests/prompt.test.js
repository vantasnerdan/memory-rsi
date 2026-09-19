import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { memoryPrompt, resolveMemoryConfig } from "../lib/prompt.js";
import { apply } from "../lib/index.js";

function fixture(t) {
	const root = mkdtempSync(join(tmpdir(), "mrsi-prompt-"));
	t.after(() => rmSync(root, { recursive: true, force: true }));
	return root;
}
function environment(t, root) {
	for (const key of ["HOME", "AGENT_MEMORY_PATH", "PYTHONPATH"]) {
		const value = process.env[key];
		t.after(() => { if (value === undefined) delete process.env[key]; else process.env[key] = value; });
	}
	process.env.HOME = root;
	delete process.env.AGENT_MEMORY_PATH;
	process.env.PYTHONPATH = resolve("cli/src");
}
const config = { base: "", memoryBin: "/nonexistent-memory-rsi-test", pythonBin: "python3", timeoutMs: 10000 };

test("empty base uses authoritative CLI config for tools and prompt", async t => {
	const root = fixture(t);
	environment(t, root);
	const base = join(root, "configured-memory");
	mkdirSync(join(root, ".config/agent-memory"), { recursive: true });
	writeFileSync(join(root, ".config/agent-memory/config.yaml"), JSON.stringify({ base_path: base }));
	assert.equal(resolveMemoryConfig(config).base, base);
	const tools = new Map();
	let section;
	apply({ tools: { register: tool => tools.set(tool.name, tool) }, systemPrompt: { section: s => { section = s; } } }, config);
	const call = async (action, fields) => JSON.parse((await tools.get("memory_policy").execute({ action, request: JSON.stringify(fields), no_git: true }, { signal: new AbortController().signal })).result);
	const before = await call("read", {});
	await call("update", { body: "# Config-resolved custom policy\nEarn useful results.", expected_revision: before.revision, actor: "human", reason: "test" });
	assert.match(section.text(), /Config-resolved custom policy/);
});

test("empty base uses cwd memory fallback", t => {
	const root = fixture(t);
	environment(t, root);
	const cwd = process.cwd();
	try {
		process.chdir(root);
		assert.equal(resolveMemoryConfig(config).base, join(root, "memory"));
	} finally { process.chdir(cwd); }
});

test("prompt Unicode limits match Python codepoints", t => {
	const base = fixture(t);
	mkdirSync(join(base, "shared/policies"), { recursive: true });
	writeFileSync(join(base, "shared/policies/agent-policy.md"), "😀".repeat(20000));
	const prompt = memoryPrompt({ base });
	assert.doesNotMatch(prompt, /could not be loaded/);
	assert.ok(prompt.includes("😀".repeat(20000)));
});

test("nonregular FIFO cannot block prompt assembly", t => {
	const base = fixture(t);
	mkdirSync(join(base, "shared/policies"), { recursive: true });
	const made = spawnSync("mkfifo", [join(base, "shared/policies/agent-policy.md")]);
	assert.equal(made.status, 0);
	const module = new URL("../lib/prompt.js", import.meta.url).href;
	const result = spawnSync(process.execPath, ["--input-type=module", "-e", `import {memoryPrompt} from ${JSON.stringify(module)}; console.log(memoryPrompt({base:${JSON.stringify(base)}}));`], { encoding: "utf8", timeout: 3000 });
	assert.equal(result.status, 0, result.error?.message);
	assert.match(result.stdout, /not a regular file/);
});

test("DSH forwarding preserves duplicate JSON fields for strict rejection", async t => {
	const root = fixture(t);
	environment(t, root);
	const tools = new Map();
	apply({ tools: { register: tool => tools.set(tool.name, tool) }, systemPrompt: { section: () => {} } }, { ...config, base: root });
	await assert.rejects(tools.get("memory_policy").execute({ action: "update", request: '{"body":"reviewed","body":"different"}', no_git: true }, { signal: new AbortController().signal }), /Duplicate JSON field/);
	await assert.rejects(tools.get("memory_plan").execute({ action: "read", request: '{"plan_id":"one","plan_id":"two"}' }, { signal: new AbortController().signal }), /duplicate JSON key/);
});
