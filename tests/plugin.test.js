import test from "node:test";
import assert from "node:assert/strict";
import * as plugin from "../lib/index.js";

test("plugin exports the DSH bundle contract", () => {
	assert.equal(typeof plugin.name, "string");
	assert.ok(plugin.name.length > 0);
	assert.deepEqual(plugin.inject, ["tools", "systemPrompt"]);
	assert.equal(typeof plugin.apply, "function");
	assert.ok(["function", "object"].includes(typeof plugin.Config));
});

test("every memory tool is registered with a valid definition", () => {
	const EXPECTED = [
		"memory_ls",
		"memory_toc",
		"memory_section",
		"memory_search",
		"memory_grep",
		"memory_new",
		"memory_update",
		"memory_validate",
		"memory_init",
		"memory_sync",
		"memory_clone",
		"memory_cache",
		"memory_log",
		"memory_plan",
		"memory_policy",
	];
	const registered = [];
	const promptSections = [];
	const ctx = {
		tools: { register: (t) => registered.push(t) },
		systemPrompt: { section: (s) => promptSections.push(s) },
	};
	plugin.apply(ctx, {
		memoryBin: "memory",
		pythonBin: "python3",
		base: "",
		timeoutMs: 1000,
	});
	const names = registered.map((t) => t.name);
	assert.deepEqual(names.sort(), [...EXPECTED].sort());
	for (const tool of registered) {
		assert.equal(typeof tool.execute, "function", `${tool.name}.execute`);
		assert.equal(typeof tool.description, "string");
		assert.ok(tool.description.length > 10, `${tool.name}.description`);
		assert.ok(tool.output?.schema, `${tool.name}.output.schema`);
		assert.equal(typeof tool.timeoutMs, "number");
	}
	assert.equal(promptSections.length, 1);
});
