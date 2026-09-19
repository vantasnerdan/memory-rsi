#!/usr/bin/env node
/** Package-only integration smoke. Installs dependencies ONLY in a fresh temp tree.
 * Full graph-only verification is the default; --skip-graph is an explicit fast mode.
 * Keeps report/logs and isolated state on failure (and success) for review.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, mkdir, writeFile, readFile, readdir, lstat, access, realpath } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, dirname, resolve, delimiter } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { parseArgs } from "node:util";

const { values } = parseArgs({ options: { "skip-graph": { type: "boolean" }, python: { type: "string", default: "python3" } } });
const checkout = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const root = await mkdtemp(join(tmpdir(), "memory-rsi-package-smoke-"));
const home = join(root, "home"), dshHome = join(home, ".dsh"), work = join(root, "work"), app = join(root, "app");
const reportPath = join(root, "report.json"), logs = join(root, "logs");
const report = { ok: false, root, reportPath, fullGraph: !values["skip-graph"], startedAt: new Date().toISOString(), steps: [] };
for (const path of [home, dshHome, work, app, logs, join(root, "tmp"), join(root, "npm-cache"), join(root, "pip-cache")]) await mkdir(path, { recursive: true });
// A minimal environment prevents Python/npm/Git/user configuration from leaking
// into the test. Retain only the executable search path and network TLS/proxy setup.
const env = {};
for (const key of ["PATH", "SystemRoot", "COMSPEC", "PATHEXT", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE"])
	if (process.env[key] !== undefined) env[key] = process.env[key];
Object.assign(env, {
	HOME: home, USERPROFILE: home, USER: "smoke-agent", LOGNAME: "smoke-agent", DSH_HOME: dshHome,
	XDG_CONFIG_HOME: join(home, ".config"), XDG_CACHE_HOME: join(home, ".cache"), XDG_DATA_HOME: join(home, ".local/share"),
	TMPDIR: join(root, "tmp"), TMP: join(root, "tmp"), TEMP: join(root, "tmp"),
	GIT_CONFIG_NOSYSTEM: "1", GIT_CONFIG_GLOBAL: join(root, "empty-gitconfig"),
	NPM_CONFIG_CACHE: join(root, "npm-cache"), NPM_CONFIG_USERCONFIG: join(root, "empty-npmrc"), NPM_CONFIG_GLOBALCONFIG: join(root, "empty-global-npmrc"), NPM_CONFIG_PREFIX: join(root, "npm-prefix"),
	PIP_CACHE_DIR: join(root, "pip-cache"), PIP_CONFIG_FILE: "/dev/null", PIP_DISABLE_PIP_VERSION_CHECK: "1",
	PYTHONNOUSERSITE: "1", PYTHONUNBUFFERED: "1", LANG: "C.UTF-8", LC_ALL: "C.UTF-8",
});
for (const key of Object.keys(process.env)) delete process.env[key];
Object.assign(process.env, env);
process.chdir(work);
let commandId = 0;
const persist = () => writeFile(reportPath, JSON.stringify(report, null, 2) + "\n");
await persist();
console.error(`Package smoke state/report: ${reportPath}`);

async function run(command, args, { cwd = work, timeoutMs = 600000, input, commandEnv = env } = {}) {
	const id = String(++commandId).padStart(3, "0");
	const record = { command, args, cwd, startedAt: new Date().toISOString() };
	const output = await new Promise((resolvePromise, reject) => {
		const child = spawn(command, args, { cwd, env: commandEnv, detached: process.platform !== "win32", stdio: "pipe" });
		let stdout = "", stderr = "", failure;
		const kill = signal => { try { if (process.platform !== "win32" && child.pid) process.kill(-child.pid, signal); else child.kill(signal); } catch (error) { if (error.code !== "ESRCH") throw error; } };
		const timer = setTimeout(() => { failure = new Error(`${command} timed out after ${timeoutMs}ms`); kill("SIGKILL"); }, timeoutMs);
		child.stdout.setEncoding("utf8"); child.stderr.setEncoding("utf8");
		child.stdout.on("data", chunk => { stdout += chunk; }); child.stderr.on("data", chunk => { stderr += chunk; });
		child.on("error", error => { clearTimeout(timer); reject(error); });
		child.stdin.on("error", error => { if (error.code !== "EPIPE") { failure = error; kill("SIGKILL"); } });
		child.on("close", (code, signal) => { clearTimeout(timer); resolvePromise({ stdout, stderr, code, signal, failure }); });
		child.stdin.end(input);
	});
	await writeFile(join(logs, `${id}.json`), JSON.stringify({ ...record, ...output, failure: output.failure?.message }, null, 2) + "\n");
	if (output.failure || output.code !== 0) throw new Error(`${output.failure?.message || `${command} exited ${output.code ?? output.signal}`}\n${output.stderr.slice(-16000)}\n${output.stdout.slice(-16000)}\nLog: ${join(logs, `${id}.json`)}`);
	return output;
}
async function step(name, operation) {
	const row = { name, state: "running", startedAt: new Date().toISOString() };
	report.steps.push(row); await persist(); console.error(`smoke: ${name}`);
	try { row.evidence = await operation(); row.state = "passed"; }
	catch (error) { row.state = "failed"; row.error = error.stack || String(error); throw error; }
	finally { row.finishedAt = new Date().toISOString(); await persist(); }
}
async function absent(path) { try { await access(path); return false; } catch (error) { if (error.code === "ENOENT") return true; throw error; } }
async function files(path) {
	if (await absent(path)) return [];
	const result = [];
	for (const entry of await readdir(path, { withFileTypes: true })) {
		const child = join(path, entry.name);
		if (entry.isDirectory()) result.push(...await files(child)); else result.push(child);
	}
	return result;
}
const json = result => JSON.parse(result.stdout);
const outsideManaged = text => text.replace(/<!-- memory-rsi:policy:begin -->[\s\S]*?<!-- memory-rsi:policy:end -->/, "");

try {
	let archive, packed, plugin, runtime, setup, graph, config, tools;
	const base = join(dshHome, "memory"), instruction = join(dshHome, "AGENTS.md");
	await writeFile(instruction, "# Operator-owned preface\r\nPreserve these exact bytes.\r\n");
	await step("npm-pack-and-install-artifact", async () => {
		const pack = json(await run("npm", ["pack", "--json", "--pack-destination", root], { cwd: checkout }))[0];
		archive = join(root, pack.filename);
		await run("npm", ["install", "--prefix", app, "--ignore-scripts", "--omit=dev", "--no-audit", "--no-fund", archive]);
		packed = join(app, "node_modules", "memory-rsi");
		assert.ok((await realpath(packed)).startsWith(app + "/"), "npm must extract a real package, not link the checkout");
		for (const required of ["bin/setup.js", "cli/src/agent_memory/bootstrap.py", "cli/src/agent_memory/default_policy.md", "lib/gitnexus.js"]) await access(join(packed, required));
		return { archive, integrity: pack.integrity, package: packed, version: JSON.parse(await readFile(join(packed, "package.json"), "utf8")).version };
	});
	await step("import-and-register-with-no-backends", async () => {
		plugin = await import(pathToFileURL(join(packed, "lib/index.js")).href);
		runtime = await import(pathToFileURL(join(packed, "lib/runtime.js")).href);
		setup = await import(pathToFileURL(join(packed, "lib/setup.js")).href);
		graph = await import(pathToFileURL(join(packed, "lib/gitnexus.js")).href);
		const registered = [];
		plugin.apply({ tools: { register: tool => registered.push(tool) }, systemPrompt: { section() {} } }, {
			base, agentId: "smoke-agent", memoryBin: join(root, "absent-memory"), pythonBin: join(root, "absent-python"), gitnexusBin: join(root, "absent-gitnexus"), timeoutMs: 1000,
		});
		for (const name of ["memory_setup", "gitnexus", "memory_new", "memory_plan", "memory_policy"]) assert.ok(registered.some(tool => tool.name === name));
		assert.ok(await absent(base)); assert.ok(await absent(runtime.runtimePaths().root));
		return { registered: registered.length, initialized: false, installed: false };
	});
	const setupArgs = [join(packed, "bin/setup.js"), "--yes", "--no-profile", "--agent-id", "smoke-agent", "--python", values.python, ...(values["skip-graph"] ? ["--no-gitnexus"] : [])];
	await step("full-private-runtime-first-run", async () => {
		const first = json(await run(process.execPath, setupArgs, { timeoutMs: 900000 }));
		assert.ok(first.initialized.ready); assert.equal(first.profile, null);
		if (!values["skip-graph"]) assert.ok(first.status.ready, JSON.stringify(first.status));
		config = runtime.runtimeConfig({ base, agentId: "smoke-agent", instructionFiles: [instruction], gitnexusTimeoutMs: 300000 });
		for (const key of ["memoryBin", "pythonBin", ...(values["skip-graph"] ? [] : ["gitnexusBin"])]) assert.ok(config[key].startsWith(runtime.runtimePaths(config).root + "/"), `${key} escaped the private runtime`);
		const interpreter = json(await run(config.pythonBin, ["-c", "import json,sys,agent_memory; print(json.dumps({'prefix':sys.prefix,'base_prefix':sys.base_prefix,'module':agent_memory.__file__}))"]));
		assert.equal(interpreter.prefix, runtime.runtimePaths(config).venv);
		assert.notEqual(interpreter.prefix, interpreter.base_prefix);
		assert.ok(interpreter.module.startsWith(interpreter.prefix + "/"), "Python CLI must be installed inside the isolated venv");
		assert.ok(await absent(join(dshHome, "profiles")), "--no-profile must not write DSH profiles");
		const policy = await readFile(join(base, "shared/policies/agent-policy.md"), "utf8");
		assert.ok(policy.includes("Rewards > gates")); assert.ok(!policy.includes(checkout)); assert.ok(!policy.includes("session-"));
		for (const name of ["coding", "general"]) assert.ok((await readFile(join(base, "shared/templates", `${name}.md`), "utf8")).includes("template_id:"));
		tools = new Map();
		plugin.apply({ tools: { register: tool => tools.set(tool.name, tool) }, systemPrompt: { section() {} } }, config);
		return { ready: first.status.ready, memoryReady: first.initialized.ready, versions: first.installed, managedExecutables: { memory: config.memoryBin, python: config.pythonBin, gitnexus: config.gitnexusBin }, noProfiles: true };
	});
	const invoke = async (name, args) => {
		assert.ok(tools.has(name), `missing tool ${name}`);
		const response = await tools.get(name).execute(args, { signal: AbortSignal.timeout(360000) });
		await writeFile(join(logs, `tool-${String(++commandId).padStart(3, "0")}-${name}.json`), JSON.stringify({ args, response }, null, 2) + "\n");
		return response.result;
	};
	await step("actual-memory-new-search-grep-plans-policy", async () => {
		const created = await invoke("memory_new", { name: "smoke-entry", description: "Portable integration witness", body: "## Portable witness\n\nPortabilityWitness9753 exists only in isolated smoke state.\n", category: "atlas", no_git: true });
		assert.ok(created.includes("smoke-entry"));
		const search = await invoke("memory_search", { query: "PortabilityWitness9753", no_cache: true });
		assert.ok(search.includes("smoke-entry"), search);
		const grep = await invoke("memory_grep", { pattern: "PortabilityWitness9753", fixed_strings: true });
		assert.ok(grep.includes("smoke-entry"), grep);
		const templates = JSON.parse(await invoke("memory_plan", { action: "templates" }));
		assert.ok(templates.templates.some(item => item.template_id === "coding"));
		const review = JSON.parse(await invoke("memory_plan", { action: "review", request: JSON.stringify({ template_id: "coding" }) }));
		const { action, ...create } = review.create_example;
		create.plan_id = "smoke-plan";
		const plan = JSON.parse(await invoke("memory_plan", { action, request: JSON.stringify(create), no_git: true }));
		const reread = JSON.parse(await invoke("memory_plan", { action: "read", request: JSON.stringify({ plan_id: "smoke-plan" }) }));
		assert.equal(reread.revision, plan.revision);
		const policy = JSON.parse(await invoke("memory_policy", { action: "read" }));
		const body = policy.body + "\n\n## Operator smoke policy\nPreserve this explicit user-authored addition.\n";
		const changed = JSON.parse(await invoke("memory_policy", { action: "update", request: JSON.stringify({ body, expected_revision: policy.revision, actor: "human", reason: "Isolated smoke preservation fixture" }), no_git: true }));
		assert.ok(changed.write_success);
		return { created: "smoke-entry", search: true, grep: true, planRevision: plan.revision, policyRevision: changed.revision };
	});
	await step("rerun-preserves-user-policy-and-unmanaged-instructions", async () => {
		const policyPath = join(base, "shared/policies/agent-policy.md"), beforePolicy = await readFile(policyPath);
		await writeFile(instruction, (await readFile(instruction, "utf8")) + "\r\n# Operator-owned footer\r\nPreserve this too.\r\n");
		const beforeOutside = outsideManaged(await readFile(instruction, "utf8"));
		const again = json(await run(process.execPath, setupArgs, { timeoutMs: 900000 }));
		assert.deepEqual(await readFile(policyPath), beforePolicy);
		assert.equal(outsideManaged(await readFile(instruction, "utf8")), beforeOutside);
		assert.deepEqual(again.initialized.created, []);
		const status = await setup.setupStatus(config);
		assert.ok(status.memory.ready); if (!values["skip-graph"]) assert.ok(status.ready);
		return { policyExactBytes: true, unmanagedInstructionsExactBytes: true, created: again.initialized.created, ready: status.ready };
	});
	await step("codex-preview-apply-repeat-preserves-sources-and-excludes-secrets", async () => {
		const codex = join(root, "codex"), memories = join(codex, "memories"), skills = join(codex, "skills/example");
		await mkdir(memories, { recursive: true }); await mkdir(skills, { recursive: true }); await mkdir(join(codex, "sessions"));
		for (const [path, body] of [
			[join(codex, "AGENTS.md"), "# Untrusted Codex instructions\r\nNever auto-promote.\r\n"], [join(skills, "SKILL.md"), "# Imported skill\r\n"], [join(memories, "intentional.md"), "# Intentional memory\r\nExact bytes.\r\n"],
			[join(codex, "auth.json"), '{"token":"SMOKE_SECRET_EXCLUDED"}'], [join(codex, "config.toml"), 'command="SMOKE_CONFIG_NOT_EXECUTED"'], [join(codex, "sessions/private.md"), "SMOKE_HISTORY_EXCLUDED"], [join(memories, "credentials.md"), "SMOKE_SECRET_EXCLUDED"],
		]) await writeFile(path, body);
		const originals = new Map(await Promise.all((await files(codex)).map(async path => [path, await readFile(path)])));
		const policyBefore = await readFile(join(base, "shared/policies/agent-policy.md"));
		const preview = await setup.bootstrapRequest(config, { action: "preview_migration", source_home: codex, memory_dirs: [memories] });
		assert.ok(preview.can_apply); assert.equal(preview.preview.files.length, 3);
		assert.ok(await absent(join(base, "shared/imports/codex")));
		const request = { action: "apply_migration", preview: preview.preview, expected_revision: preview.revision };
		const applied = await setup.bootstrapRequest(config, request), repeated = await setup.bootstrapRequest(config, request);
		assert.equal(applied.created.length, 3); assert.deepEqual(repeated.created, []); assert.equal(applied.policy_promoted, false);
		for (const [path, bytes] of originals) assert.deepEqual(await readFile(path), bytes);
		for (const entry of preview.preview.files) assert.deepEqual(await readFile(join(base, entry.destination)), await readFile(entry.source));
		for (const path of await files(join(base, "shared/imports"))) assert.ok(!(await readFile(path, "utf8")).includes("SMOKE_SECRET_EXCLUDED"));
		assert.deepEqual(await readFile(join(base, "shared/policies/agent-policy.md")), policyBefore);
		return { selectedMarkdown: 3, imported: applied.created, repeatCreated: repeated.created, sourcesExactBytes: true, secretsExcluded: true, policyUnchanged: true };
	});
	if (!values["skip-graph"]) await step("actual-graph-only-analyze-and-lexical-query", async () => {
		const project = join(root, "tiny-project"); await mkdir(project);
		await writeFile(join(project, "package.json"), '{"name":"smoke-tiny-project","type":"module"}\n');
		await writeFile(join(project, "math.js"), "export function smokePortableTotal(items) { return items.reduce((sum, value) => sum + value, 0); }\nexport function smokeCheckout() { return smokePortableTotal([1, 2]); }\n");
		await writeFile(join(project, "AGENTS.md"), "# Project instructions stay untouched\r\n");
		const before = await readFile(join(project, "AGENTS.md"));
		const analyzed = await graph.executeGraph(config, { action: "analyze", cwd: project }, { signal: AbortSignal.timeout(360000) });
		const queried = await graph.executeGraph(config, { action: "query", cwd: project, query: "smokePortableTotal", limit: 5 }, { signal: AbortSignal.timeout(120000) });
		assert.ok(queried.result.includes("smokePortableTotal"), queried.result);
		assert.deepEqual(await readFile(join(project, "AGENTS.md")), before);
		const metadataPath = join(project, ".gitnexus/meta.json"), metadata = JSON.parse(await readFile(metadataPath, "utf8"));
		assert.equal(metadata.stats?.embeddings, 0, `Expected exact zero embeddings: ${JSON.stringify(metadata)}`);
		const cacheFiles = [...await files(join(config.gitnexusHome, ".cache/huggingface")), ...await files(join(home, ".cache/huggingface"))];
		assert.deepEqual(cacheFiles, [], "No model cache files may be created");
		await writeFile(join(logs, "graph-analyze-query.json"), JSON.stringify({ analyzed, queried, metadata }, null, 2) + "\n");
		return { index: metadataPath, embeddings: 0, lexicalSymbolFound: true, projectInstructionsExactBytes: true, modelCacheFiles: 0 };
	});
	report.ok = true; report.finishedAt = new Date().toISOString(); await persist();
	console.log(JSON.stringify(report, null, 2));
} catch (error) {
	report.error = error.stack || String(error); report.finishedAt = new Date().toISOString(); await persist();
	console.error(JSON.stringify({ ok: false, reportPath, error: report.error }, null, 2));
	process.exitCode = 1;
}
