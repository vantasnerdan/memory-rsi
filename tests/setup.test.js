import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, rmSync, existsSync, mkdirSync, writeFileSync, symlinkSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { apply } from "../lib/index.js";
import { resolveMemoryConfig } from "../lib/prompt.js";
import { bootstrapRequest, syncSetupInstructions } from "../lib/setup.js";
import { setupFields } from "../lib/setup-json.js";
import { readManagedJSON, writeManagedJSON } from "../lib/runtime.js";

function isolated(t) {
	const root = mkdtempSync(join(tmpdir(), "memory-rsi-setup-test-"));
	t.after(() => rmSync(root, { recursive: true, force: true }));
	for (const key of ["HOME", "DSH_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "AGENT_MEMORY_PATH", "AGENT_ID", "PYTHONPATH", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"]) {
		const before = process.env[key];
		t.after(() => before === undefined ? delete process.env[key] : process.env[key] = before);
	}
	Object.assign(process.env, { HOME: root, DSH_HOME: join(root, ".dsh"), XDG_CONFIG_HOME: join(root, ".config"), XDG_CACHE_HOME: join(root, ".cache"), PYTHONPATH: resolve("cli/src"), GIT_CONFIG_GLOBAL: join(root, "no-gitconfig"), GIT_CONFIG_NOSYSTEM: "1" });
	delete process.env.AGENT_MEMORY_PATH; delete process.env.AGENT_ID;
	return root;
}

test("first package import without Python or memory registers all setup/graph tools without side effects", t => {
	const root = isolated(t), tools = new Map();
	const config = { memoryBin: join(root, "absent-memory"), pythonBin: join(root, "absent-python"), runtimeDir: join(root, "runtime"), timeoutMs: 1000 };
	const resolved = resolveMemoryConfig(config);
	assert.equal(resolved.base, join(root, ".dsh", "memory"));
	assert.match(resolved.setupHint, /install and initialize/);
	apply({ tools: { register: tool => tools.set(tool.name, tool) }, systemPrompt: { section: () => {} } }, config);
	assert.ok(tools.has("memory_setup")); assert.ok(tools.has("gitnexus"));
	assert.equal(existsSync(join(root, ".dsh")), false); assert.equal(existsSync(config.runtimeDir), false);
});

test("setup rejects duplicate nested keys, action shadowing and malformed JSON before side effects", () => {
	for (const input of ['{"graph":false,"graph":true}', '{"preview":{"key":1,"\\u006bey":2}}']) assert.throws(() => setupFields(input), /Duplicate JSON field/);
	for (const input of ["null", "[]", "1", '"text"', '{"action":"install"}']) assert.throws(() => setupFields(input));
	const input = { preview: { entries: [{ path: "brace } : , \\\"", data: [true, null, 1] }] }, graph: false };
	assert.deepEqual(setupFields(JSON.stringify(input)), input);
	assert.deepEqual(setupFields('{"__proto__":{"polluted":true}}'), JSON.parse('{"__proto__":{"polluted":true}}'));
	assert.equal({}.polluted, undefined);
});

test("real registered Python bootstrap supports file-only readiness and repeat initialization", async t => {
	const root = isolated(t);
	const config = { base: join(root, "memory"), runtimeDir: join(root, "runtime"), agentId: "new-agent", memoryBin: join(root, "absent-memory"), pythonBin: "python3", instructionFiles: [], timeoutMs: 10000 };
	const first = await bootstrapRequest(config, { action: "initialize" }, { noGit: true });
	assert.equal(first.ready, true); assert.equal(existsSync(join(config.base, ".git")), false);
	const status = await bootstrapRequest(config, { action: "status" }, { noGit: true });
	assert.equal(status.ready, true);
	const second = await bootstrapRequest(config, { action: "initialize" }, { noGit: true });
	assert.equal(second.changed, false);
});

test("installer instruction sync uses an accepted automated actor and preserves user text", async t => {
	const root = isolated(t), target = join(root, "AGENTS.md");
	const config = { base: join(root, "memory"), agentId: "new-agent", memoryBin: join(root, "absent-memory"), pythonBin: "python3", instructionFiles: [target], timeoutMs: 10000 };
	await bootstrapRequest(config, { action: "initialize" }, { noGit: true });
	const original = "Owner instructions\r\nPreserve exact bytes.\r\n";
	writeFileSync(target, original);
	const result = await syncSetupInstructions(config);
	assert.equal(result[0].applied, true);
	assert.ok(readFileSync(target, "utf8").startsWith(original));
	assert.match(readFileSync(target, "utf8"), /memory-rsi:policy:begin/);
	assert.equal((await syncSetupInstructions(config))[0].changed, false);
});

test("migration preview JSON IO refuses redirected files and preserves unrelated data", async t => {
	const root = isolated(t), victim = join(root, "owner.json"), preview = join(root, "preview.json");
	writeFileSync(victim, "owner content"); symlinkSync(victim, preview);
	await assert.rejects(writeManagedJSON(preview, { preview: [] }), /linked|nonregular/);
	await assert.rejects(readManagedJSON(preview), /linked|nonregular/);
	assert.equal(readFileSync(victim, "utf8"), "owner content");
	rmSync(preview); await writeManagedJSON(preview, { preview: [], revision: "fixture" });
	assert.deepEqual(await readManagedJSON(preview), { preview: [], revision: "fixture" });
	await assert.rejects(writeManagedJSON(preview, { huge: "x".repeat(100) }, 16), /exceeds/);
});
