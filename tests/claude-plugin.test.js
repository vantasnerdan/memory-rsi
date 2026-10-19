import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { accessSync, constants, existsSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(fileURLToPath(new URL("..", import.meta.url)));
const RUN_SH = join(ROOT, "claude", "hooks", "run.sh");
const readJSON = (path) => JSON.parse(readFileSync(join(ROOT, path), "utf8"));
const plugin = readJSON(".claude-plugin/plugin.json");
const marketplace = readJSON(".claude-plugin/marketplace.json");
const hooks = readJSON("claude/hooks/hooks.json");
const HOOK_KEYS = ["restore", "precompact", "subagent_load", "subagent_save", "policy"];

function hookCommands() {
	return Object.values(hooks.hooks).flatMap((groups) => groups.flatMap((group) => group.hooks));
}

function frontmatterDescription(path) {
	const match = /^---\n([\s\S]*?)\n---\n/.exec(readFileSync(path, "utf8"));
	assert.ok(match, `${path} has frontmatter`);
	const line = match[1].split("\n").find((l) => l.startsWith("description:"));
	assert.ok(line && line.slice("description:".length).trim().length > 10, `${path} has a description`);
}

/** Run run.sh with a minimal environment; logs and HOME stay inside a temp directory. */
function runHook(args, env = {}, input = "") {
	const sandbox = mkdtempSync(join(tmpdir(), "memory-rsi-claude-"));
	const result = spawnSync("bash", [RUN_SH, ...args], {
		input,
		encoding: "utf8",
		env: {
			PATH: process.env.PATH,
			HOME: join(sandbox, "home"),
			AGENT_MEMORY_LOG_PATH: join(sandbox, "logs"),
			CLAUDE_PLUGIN_ROOT: ROOT,
			...env,
		},
	});
	return { ...result, sandbox };
}

test("Claude manifests parse and agree on name and version", () => {
	const pkg = readJSON("package.json");
	const lock = readJSON("package-lock.json");
	assert.equal(plugin.name, "memory-rsi");
	assert.equal(plugin.version, pkg.version);
	assert.equal(lock.version, pkg.version);
	assert.equal(lock.packages[""].version, pkg.version);
	assert.equal(marketplace.plugins.length, 1);
	assert.equal(marketplace.plugins[0].name, plugin.name);
	assert.equal(marketplace.plugins[0].source, "./");
});

test("every manifest path is relative and exists", () => {
	for (const key of ["skills", "commands", "hooks"]) {
		assert.ok(plugin[key].startsWith("./"), `${key} starts with ./`);
		assert.ok(existsSync(join(ROOT, plugin[key])), `${key} exists`);
	}
});

test("hooks.json commands use the plugin root, existing executable targets and timeouts in seconds", () => {
	const commands = hookCommands();
	assert.equal(commands.length, HOOK_KEYS.length);
	const keys = [];
	for (const hook of commands) {
		assert.equal(hook.type, "command");
		assert.ok(hook.timeout > 0 && hook.timeout <= 600, `timeout ${hook.timeout} is in seconds`);
		const match = /^bash "\$\{CLAUDE_PLUGIN_ROOT\}\/(claude\/hooks\/run\.sh)" ([a-z_]+) ([\w.-]+)$/.exec(hook.command);
		assert.ok(match, `command shape: ${hook.command}`);
		const [, runner, key, script] = match;
		keys.push(key);
		const target = [join(ROOT, "claude", "hooks", script), join(ROOT, "cli", "hooks", script)].find(existsSync);
		assert.ok(target, `${script} exists`);
		for (const path of [join(ROOT, runner), target]) accessSync(path, constants.X_OK);
	}
	assert.deepEqual(keys.sort(), [...HOOK_KEYS].sort());
});

test("every hook option exists and defaults to off", () => {
	for (const key of HOOK_KEYS) {
		const option = plugin.userConfig[`hook_${key}`];
		assert.ok(option, `hook_${key} option`);
		assert.equal(option.type, "boolean");
		assert.equal(option.default, false);
		assert.ok(option.description.length > 40, `hook_${key} says what it does`);
	}
	for (const key of ["memory_base", "agent_id"]) {
		assert.equal(plugin.userConfig[key].type, "string");
		assert.equal(plugin.userConfig[key].default, "");
	}
});

test("run.sh is silent and writes nothing unless its option is on and a base resolves", () => {
	const base = mkdtempSync(join(tmpdir(), "memory-rsi-base-"));
	const cases = [
		{ MEMORY_BASE: base },
		{ MEMORY_BASE: base, CLAUDE_PLUGIN_OPTION_HOOK_PRECOMPACT: "false" },
		{ MEMORY_BASE: base, CLAUDE_PLUGIN_OPTION_HOOK_PRECOMPACT: "" },
		{ CLAUDE_PLUGIN_OPTION_HOOK_PRECOMPACT: "true" },
		{ CLAUDE_PLUGIN_OPTION_HOOK_PRECOMPACT: "true", CLAUDE_PLUGIN_OPTION_MEMORY_BASE: join(base, "missing") },
	];
	for (const env of cases) {
		const result = runHook(["precompact", "pre-compact-save.sh"], env, '{"session_id":"t"}');
		assert.equal(result.status, 0, JSON.stringify(env));
		assert.equal(result.stdout, "", JSON.stringify(env));
		assert.deepEqual(readdirSync(base), [], `nothing written for ${JSON.stringify(env)}`);
		const on = env.CLAUDE_PLUGIN_OPTION_HOOK_PRECOMPACT === "true";
		assert.equal(existsSync(join(result.sandbox, "logs")), on, `skip logged only when the hook is on: ${JSON.stringify(env)}`);
	}
});

test("run.sh passes stdin to the selected script and returns its output", () => {
	const root = mkdtempSync(join(tmpdir(), "memory-rsi-root-"));
	const base = mkdtempSync(join(tmpdir(), "memory-rsi-base-"));
	mkdirSync(join(root, "cli", "hooks"), { recursive: true });
	writeFileSync(
		join(root, "cli", "hooks", "stub.sh"),
		'#!/usr/bin/env bash\nprintf "%s|%s|%s" "$(cat)" "$MEMORY_BASE" "${AGENT_ID:-}"\n',
		{ mode: 0o755 },
	);
	for (const value of ["true", "TRUE", "1", "yes", "On"]) {
		const result = runHook(
			["restore", "stub.sh"],
			{
				CLAUDE_PLUGIN_ROOT: root,
				CLAUDE_PLUGIN_OPTION_HOOK_RESTORE: value,
				CLAUDE_PLUGIN_OPTION_MEMORY_BASE: base,
				CLAUDE_PLUGIN_OPTION_AGENT_ID: "tester",
			},
			'{"source":"compact"}',
		);
		assert.equal(result.status, 0, result.stderr);
		assert.equal(result.stdout, `{"source":"compact"}|${base}|tester`);
	}
	const fallback = runHook(["restore", "stub.sh"], { CLAUDE_PLUGIN_ROOT: root, CLAUDE_PLUGIN_OPTION_HOOK_RESTORE: "true", AGENT_MEMORY_PATH: base }, "x");
	assert.equal(fallback.stdout, `x|${base}|`);
});

test("run.sh rejects script names that leave the hook directories", () => {
	for (const script of ["../x.sh", "a/b.sh", ".."]) {
		const result = runHook(["restore", script], { CLAUDE_PLUGIN_OPTION_HOOK_RESTORE: "true", MEMORY_BASE: tmpdir() });
		assert.notEqual(result.status, 0, script);
		assert.equal(result.stdout, "");
		assert.match(result.stderr, /invalid script name/);
	}
});

test("every command and the skill have frontmatter descriptions", () => {
	const commandsDir = join(ROOT, plugin.commands);
	const commands = readdirSync(commandsDir).filter((name) => name.endsWith(".md"));
	assert.deepEqual(commands.sort(), ["recall.md", "save.md", "setup.md"]);
	for (const name of commands) frontmatterDescription(join(commandsDir, name));
	frontmatterDescription(join(ROOT, plugin.skills, "shared_agent-memory-usage", "SKILL.md"));
});
