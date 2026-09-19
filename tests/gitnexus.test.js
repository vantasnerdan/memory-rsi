import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, readFile, rm, access, symlink, link } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { registerGitNexus, buildGraphCommand, lexicalGraphQuery, graphEnvironment, executeGraph, gitnexusStatus, GITNEXUS_VERSION } from "../lib/gitnexus.js";
import { runGraphProcess, GRAPH_OUTPUT_BYTES } from "../lib/gitnexus-process.js";

async function fixture(t, body = "console.log(JSON.stringify({argv:args,cwd:process.cwd(),home:process.env.HOME}));") {
	const root = await mkdtemp(join(tmpdir(), "memory-rsi-graph-test-"));
	t.after(() => rm(root, { recursive: true, force: true }));
	const cwd = join(root, "repo space ; --embeddings"), home = join(root, "home");
	await mkdir(cwd); await mkdir(home);
	const bin = join(root, "gitnexus.mjs");
	await writeFile(bin, `const args=process.argv.slice(2);\nif(args[0]==='--version'){console.log('${GITNEXUS_VERSION}');process.exit(0);}\n${body}`);
	return { root, cwd, bin, config: { gitnexusBin: bin, gitnexusHome: home, gitnexusTimeoutMs: 5000 } };
}

test("graph tool is registered even before binary installation and uses typed actions", async () => {
	const tools = [];
	registerGitNexus({ tools: { register: (tool) => tools.push(tool) } }, { gitnexusBin: "/missing/gitnexus" });
	assert.equal(tools.length, 1); assert.equal(tools[0].name, "gitnexus");
	assert.ok(tools[0].parameters.properties.action.enum.includes("analyze"));
	assert.equal(tools[0].isConcurrencySafe(), false);
	const result = JSON.parse((await tools[0].execute({ action: "doctor" }, {})).result);
	assert.equal(result.available, false); assert.equal(result.graphReady, false);
	assert.match(result.error, /memory_setup/);
});

test("analyze arguments cannot generate or delete embeddings, instructions, or skills", async (t) => {
	const f = await fixture(t);
	for (const force of [false, true]) {
		const command = await buildGraphCommand({ action: "analyze", cwd: f.cwd, force });
		assert.deepEqual(command.argv, ["analyze", "--index-only", "--skip-git", ...(force ? ["--force"] : []), "--", f.cwd]);
		const result = JSON.parse((await executeGraph(f.config, { action: "analyze", cwd: f.cwd, force })).result);
		assert.deepEqual(result.argv, command.argv);
		assert.equal(result.cwd, f.cwd); assert.equal(result.home, f.config.gitnexusHome);
	}
});

test("repository config cannot silently enable embeddings or embedding removal", async (t) => {
	const f = await fixture(t);
	for (const config of [
		{ embeddings: true }, { embeddings: "0" }, { embeddings: 0 },
		{ analyze: { embeddings: true } }, { dropEmbeddings: true }, { analyze: { dropEmbeddings: true } },
		{ embeddings: true, analyze: { embeddings: false } },
	]) {
		const path = join(f.cwd, ".gitnexusrc"), raw = JSON.stringify(config);
		await writeFile(path, raw);
		await assert.rejects(executeGraph(f.config, { action: "analyze", cwd: f.cwd }), /Graph-only analyze refused/);
		assert.equal(await readFile(path, "utf8"), raw);
	}
	await writeFile(join(f.cwd, ".gitnexusrc"), '{"embeddings":false,"dropEmbeddings":false,"analyze":{"indexOnly":false}}');
	assert.ok((await buildGraphCommand({ action: "analyze", cwd: f.cwd })).argv.includes("--index-only"));
	await writeFile(join(f.cwd, ".gitnexusrc"), "broken");
	await assert.rejects(buildGraphCommand({ action: "analyze", cwd: f.cwd }), /Invalid/);
});

test("hybrid query is never invoked, including when an index already has embeddings", async (t) => {
	const f = await fixture(t, `if(args[0]==='query'||args.some(a=>a==='--embeddings'||a==='--drop-embeddings'))throw new Error('EMBEDDING PATH FORBIDDEN'); console.log(JSON.stringify({argv:args}));`);
	await mkdir(join(f.cwd, ".gitnexus"));
	await writeFile(join(f.cwd, ".gitnexus", "existing-embeddings"), "preserve me");
	const result = await executeGraph(f.config, { action: "query", cwd: f.cwd, query: "checkout total", limit: 7 });
	const parsed = JSON.parse(result.result.split("\n").slice(1).join("\n"));
	assert.equal(parsed.argv[0], "cypher");
	assert.equal(parsed.argv[1], `--repo=${f.cwd}`);
	assert.match(parsed.argv.at(-1), /LIMIT 7$/);
	assert.match(parsed.argv.at(-1), / AND /);
	assert.equal(await readFile(join(f.cwd, ".gitnexus", "existing-embeddings"), "utf8"), "preserve me");
});

test("unknown actions, freeform flags and irrelevant options fail closed", async (t) => {
	const f = await fixture(t);
	for (const args of [
		{ action: "wiki" }, { action: "cypher", query: "RETURN 1" }, { action: "setup" },
		{ action: "analyze", cwd: f.cwd, embeddings: true }, { action: "analyze", cwd: f.cwd, flags: ["--embeddings"] },
		{ action: "query", cwd: f.cwd, query: "total", content: true }, { action: "list", cwd: f.cwd },
		{ action: "analyze", cwd: f.cwd, force: "--embeddings" },
	]) await assert.rejects(buildGraphCommand(args));
	for (const limit of [0, -1, 101, 1.5, Infinity, "--embeddings"]) {
		await assert.rejects(buildGraphCommand({ action: "query", cwd: f.cwd, query: "test", limit }));
	}
});

test("repo paths require explicit absolute directories and cannot become flags", async (t) => {
	const f = await fixture(t);
	for (const cwd of [undefined, ".", "--embeddings", "", f.bin]) {
		await assert.rejects(buildGraphCommand({ action: "analyze", cwd }));
	}
	const command = await buildGraphCommand({ action: "context", cwd: f.cwd, symbol: "--embeddings", file: "--content" });
	assert.deepEqual(command.argv, ["context", `--repo=${f.cwd}`, "--file=--content", "--", "--embeddings"]);
	assert.equal(command.cwd, f.cwd);
});

test("lexical Cypher uses only identifier words, not caller-supplied statements", () => {
	const query = lexicalGraphQuery("x'\\'); DELETE n; //", 10);
	assert.ok(!query.includes("DELETE n")); assert.ok(!query.includes("\\"));
	assert.ok(!query.includes(";")); assert.match(query, /^MATCH \(n\) WHERE/);
	assert.throws(() => lexicalGraphQuery("'';\\"));
	assert.throws(() => lexicalGraphQuery("word ".repeat(17)));
	assert.throws(() => lexicalGraphQuery("a\nb"));
});

test("impact and diff expose only bounded graph options", async (t) => {
	const f = await fixture(t);
	assert.deepEqual((await buildGraphCommand({ action: "impact", cwd: f.cwd, symbol: "total" })).argv,
		["impact", `--repo=${f.cwd}`, "--direction=upstream", "--depth=3", "--limit=20", "--", "total"]);
	assert.deepEqual((await buildGraphCommand({ action: "detect_changes", cwd: f.cwd, scope: "compare", base_ref: "feature/safe-branch" })).argv,
		["detect-changes", `--repo=${f.cwd}`, "--scope=compare", "--base-ref=feature/safe-branch"]);
	for (const base_ref of ["--help", "main;touch /tmp/oops", "$(touch bad)", "main..evil", "main^", "a b"]) {
		await assert.rejects(buildGraphCommand({ action: "detect_changes", cwd: f.cwd, scope: "compare", base_ref }));
	}
	await assert.rejects(buildGraphCommand({ action: "detect_changes", cwd: f.cwd, scope: "compare" }));
	await assert.rejects(buildGraphCommand({ action: "impact", cwd: f.cwd, symbol: "total", depth: 11 }));
});

test("environment disables downloads, global registry/storage overrides and publication", () => {
	const before = { ...process.env };
	try {
		Object.assign(process.env, { GITNEXUS_STORAGE_PATH: "/operator-index", GITNEXUS_LBUG_EXTENSION_INSTALL: "auto", GITNEXUS_EMBEDDING_API_URL: "http://model.invalid", UNDERSTAND_QUICKLY_TOKEN: "secret", NODE_OPTIONS: "--inspect" });
		const env = graphEnvironment({ gitnexusHome: join(tmpdir(), "isolated-graph-home") });
		assert.equal(env.GITNEXUS_STORAGE_PATH, undefined); assert.equal(env.GITNEXUS_EMBEDDING_API_URL, undefined);
		assert.equal(env.UNDERSTAND_QUICKLY_TOKEN, undefined); assert.equal(env.NODE_OPTIONS, undefined);
		assert.equal(env.GITNEXUS_LBUG_EXTENSION_INSTALL, "never");
		assert.equal(env.HF_HUB_OFFLINE, "1"); assert.equal(env.TRANSFORMERS_OFFLINE, "1");
		assert.equal(env.USERPROFILE, env.HOME); assert.notEqual(env.HOME, process.env.HOME);
	} finally {
		for (const key of Object.keys(process.env)) if (!(key in before)) delete process.env[key];
		Object.assign(process.env, before);
	}
});

test("doctor checks actual version/help/native status, never fakes index readiness", async (t) => {
	const f = await fixture(t, `if(args.includes('--help'))console.log('--index-only --skip-git');else console.log('native    ✓ lbugjs.node loaded');`);
	const result = await gitnexusStatus(f.config);
	assert.equal(result.available, true); assert.equal(result.supported, true);
	assert.equal(result.version, "1.6.7"); assert.equal(result.nativeLoaded, true);
	assert.equal(result.graphReady, false); assert.equal(result.embeddings, false);
	await writeFile(f.bin, "console.log('9.9.9');");
	const unsupported = await gitnexusStatus(f.config);
	assert.equal(unsupported.available, true); assert.equal(unsupported.supported, false);
	await assert.rejects(executeGraph(f.config, { action: "list" }), /Unsupported GitNexus version/);
});

test("doctor marks unavailable native binding and failed child honestly", async (t) => {
	const f = await fixture(t, `if(args.includes('--help'))console.log('--index-only --skip-git');else console.log('native    ✗ lbugjs.node missing');`);
	assert.equal((await gitnexusStatus(f.config)).supported, false);
	await writeFile(f.bin, "console.error('native initialization failed');process.exit(5);");
	const status = await gitnexusStatus(f.config);
	assert.equal(status.available, false); assert.match(status.error, /exit 5.*native initialization failed/s);
});

test("process nonzero exits, unknown CLI flags, and error JSON cannot masquerade as success", async (t) => {
	const f = await fixture(t, `console.error('unknown option --index-only');process.exit(1);`);
	await assert.rejects(executeGraph(f.config, { action: "analyze", cwd: f.cwd }), /exit 1.*unknown option/s);
	await writeFile(f.bin, `const args=process.argv.slice(2);console.log(args[0]==='--version'?'1.6.7':JSON.stringify({error:'native graph unavailable'}));`);
	await assert.rejects(executeGraph(f.config, { action: "query", cwd: f.cwd, query: "total" }), /graph operation failed/);
	await writeFile(f.bin, `console.log(process.argv[2]==='--version'?'1.6.7':'Error: Git diff failed');`);
	await assert.rejects(executeGraph(f.config, { action: "detect_changes", cwd: f.cwd }), /graph operation failed/);
});

test("process transport bounds both stdout and stderr while draining pipes", async () => {
	const output = await runGraphProcess(process.execPath, ["-e", "process.stdout.write('x'.repeat(3000000));process.stderr.write('y'.repeat(3000000));"], { timeoutMs: 5000 });
	assert.ok(Buffer.byteLength(output.stdout) + Buffer.byteLength(output.stderr) <= GRAPH_OUTPUT_BYTES);
	assert.equal(output.truncated, true);
});

test("timeout escalates SIGTERM-ignoring child to SIGKILL and cleans descendants", { skip: process.platform === "win32" }, async (t) => {
	const f = await fixture(t);
	const marker = join(f.root, "descendant-ran");
	const descendant = `setTimeout(()=>require('node:fs').writeFileSync(${JSON.stringify(marker)},'bad'),1500);setInterval(()=>{},1000);`;
	const source = `const {spawn}=require('node:child_process');spawn(process.execPath,['-e',${JSON.stringify(descendant)}],{stdio:'ignore'});process.on('SIGTERM',()=>{});setInterval(()=>{},1000);`;
	const start = Date.now();
	await assert.rejects(runGraphProcess(process.execPath, ["-e", source], { timeoutMs: 200 }), /timed out/);
	assert.ok(Date.now() - start < 2000);
	await new Promise((resolve) => setTimeout(resolve, 1600));
	await assert.rejects(access(marker));
});

test("abort signal cancels running process and rejects already-aborted launch", async () => {
	const controller = new AbortController();
	const promise = runGraphProcess(process.execPath, ["-e", "setInterval(()=>{},1000);"], { signal: controller.signal, timeoutMs: 5000 });
	controller.abort();
	await assert.rejects(promise, /cancelled/);
	await assert.rejects(runGraphProcess("/not-started", [], { signal: controller.signal }), /before startup/);
});

const repoActions = (cwd) => [
	{ action: "analyze", cwd }, { action: "status", cwd }, { action: "query", cwd, query: "total" },
	{ action: "context", cwd, symbol: "total" }, { action: "impact", cwd, symbol: "total" }, { action: "detect_changes", cwd },
];

test("all repository actions reject symlinked index roots before any native launch", async (t) => {
	const f = await fixture(t), outside = join(f.root, "outside"), marker = join(f.root, "native-launched");
	await mkdir(outside); await writeFile(join(outside, "sentinel"), "untouched");
	await writeFile(f.bin, `import {writeFileSync} from 'node:fs';writeFileSync(${JSON.stringify(marker)},'bad');console.log('1.6.7');`);
	await symlink(outside, join(f.cwd, ".gitnexus"), "dir");
	for (const args of repoActions(f.cwd)) await assert.rejects(executeGraph(f.config, args), /Unsafe GitNexus/);
	await assert.rejects(access(marker));
	assert.equal(await readFile(join(outside, "sentinel"), "utf8"), "untouched");
});

test("native DB, sidecar, metadata, nested-cache and config links are rejected", async (t) => {
	const f = await fixture(t), outside = join(f.root, "outside-file"), index = join(f.cwd, ".gitnexus");
	await mkdir(index); await mkdir(join(index, "parse-cache")); await writeFile(outside, '{}');
	for (const path of [join(index, "lbug"), join(index, "lbug.wal"), join(index, "meta.json"), join(index, "parse-cache", "cache.json"), join(f.cwd, ".gitnexusrc")]) {
		for (const mode of ["symlink", "hardlink"]) {
			await (mode === "symlink" ? symlink(outside, path) : link(outside, path));
			for (const args of repoActions(f.cwd)) await assert.rejects(buildGraphCommand(args), /Unsafe GitNexus/);
			assert.equal(await readFile(outside, "utf8"), '{}');
			await rm(path);
		}
	}
	await symlink(join(f.root, "missing"), join(index, "dangling-link"));
	await assert.rejects(buildGraphCommand({ action: "analyze", cwd: f.cwd }), /Unsafe GitNexus/);
});

test("index root files, DB directories, and directory symlinks fail closed", async (t) => {
	const f = await fixture(t), index = join(f.cwd, ".gitnexus");
	await writeFile(index, "not a directory");
	await assert.rejects(buildGraphCommand({ action: "analyze", cwd: f.cwd }), /Unsafe GitNexus/);
	await rm(index); await mkdir(index); await mkdir(join(index, "lbug"));
	await assert.rejects(buildGraphCommand({ action: "query", cwd: f.cwd, query: "total" }), /expected a regular file/);
	await rm(join(index, "lbug"), { recursive: true });
	await symlink(f.root, join(index, "cache"), "dir");
	await assert.rejects(buildGraphCommand({ action: "status", cwd: f.cwd }), /Unsafe GitNexus/);
});

test("FIFO index/config paths are rejected without reading or starting native code", { skip: process.platform === "win32" }, async (t) => {
	const f = await fixture(t), index = join(f.cwd, ".gitnexus");
	await mkdir(index);
	for (const path of [join(index, "lbug"), join(index, "meta.json"), join(f.cwd, ".gitnexusrc")]) {
		const made = spawnSync("mkfifo", [path], { shell: false });
		assert.equal(made.status, 0, made.stderr?.toString());
		await assert.rejects(buildGraphCommand({ action: "analyze", cwd: f.cwd }), /Unsafe GitNexus/);
		await assert.rejects(buildGraphCommand({ action: "query", cwd: f.cwd, query: "total" }), /Unsafe GitNexus/);
		await rm(path);
	}
});

test("absent indexes support analysis/status without ancestor discovery; regular indexes work", async (t) => {
	const f = await fixture(t, "throw new Error('status must not launch without valid selected-root metadata');");
	assert.ok((await buildGraphCommand({ action: "analyze", cwd: f.cwd })).argv.includes("--index-only"));
	assert.match((await executeGraph(f.config, { action: "status", cwd: f.cwd })).result, /not indexed.*Ancestor indexes were not consulted/s);
	const index = join(f.cwd, ".gitnexus"); await mkdir(index); await mkdir(join(index, "parse-cache"));
	await writeFile(join(index, "lbug"), "native-file"); await writeFile(join(index, "parse-cache", "cache.json"), '{}');
	for (const content of ["null", "[]", "invalid"]) {
		await writeFile(join(index, "meta.json"), content);
		await assert.rejects(executeGraph(f.config, { action: "status", cwd: f.cwd }), /Invalid GitNexus metadata/);
	}
	await writeFile(join(index, "meta.json"), '{}');
	for (const args of repoActions(f.cwd)) assert.ok((await buildGraphCommand(args)).argv.length);
});

test("registered tool observes configured binary updates after setup", async (t) => {
	const f = await fixture(t);
	const config = { ...f.config, gitnexusBin: "/not-installed" }, tools = [];
	registerGitNexus({ tools: { register: (tool) => tools.push(tool) } }, config);
	await assert.rejects(tools[0].execute({ action: "list" }, {}), /memory_setup/);
	config.gitnexusBin = f.bin;
	assert.deepEqual(JSON.parse((await tools[0].execute({ action: "list" }, {})).result).argv, ["list"]);
});
