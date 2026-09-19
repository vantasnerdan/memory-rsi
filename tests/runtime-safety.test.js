import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, readFile, rm, symlink, link, lstat, access } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { join, dirname } from "node:path";
import { tmpdir } from "node:os";
import { backendStatus, installRuntime, runtimePaths, safeDirectory } from "../lib/runtime.js";

async function fixture(t) {
	const root = await mkdtemp(join(tmpdir(), "memory-runtime-safety-"));
	t.after(() => rm(root, { recursive: true, force: true }));
	const config = { runtimeDir: join(root, "runtime") };
	const paths = runtimePaths(config), calls = [];
	const runner = async (command, args) => {
		calls.push({ command, args });
		return { stdout: command === paths.gitnexus ? "1.6.7\n" : "available\n", stderr: "", code: 0 };
	};
	return { root, config, paths, calls, runner };
}

for (const kind of ["symlink", "fifo", "directory", "hardlink", "oversized"]) {
	test(`status refuses ${kind} installed.json without following it or blocking`, { skip: process.platform === "win32" && kind === "fifo" }, async t => {
		const f = await fixture(t);
		await mkdir(f.paths.root);
		const manifest = join(f.paths.root, "installed.json"), other = join(f.root, "other.json");
		await writeFile(other, '{"private":"must not read"}');
		if (kind === "symlink") await symlink(other, manifest);
		if (kind === "fifo") execFileSync("mkfifo", [manifest]);
		if (kind === "directory") await mkdir(manifest);
		if (kind === "hardlink") await link(other, manifest);
		if (kind === "oversized") await writeFile(manifest, "x".repeat(65537));
		const result = await backendStatus(f.config, { runner: f.runner });
		assert.match(result.installation.error, /Refusing nonregular/);
		assert.equal(result.installation.private, undefined);
		assert.equal(await readFile(other, "utf8"), '{"private":"must not read"}');
	});

	test(`install refuses ${kind} installed.json before modifying software`, { skip: process.platform === "win32" }, async t => {
		const f = await fixture(t);
		await mkdir(f.paths.root);
		const manifest = join(f.paths.root, "installed.json"), other = join(f.root, "other.json");
		await writeFile(other, "preserve external bytes");
		if (kind === "symlink") await symlink(other, manifest);
		if (kind === "fifo") execFileSync("mkfifo", [manifest]);
		if (kind === "directory") await mkdir(manifest);
		if (kind === "hardlink") await link(other, manifest);
		if (kind === "oversized") await writeFile(manifest, "x".repeat(65537));
		await assert.rejects(installRuntime(f.config, { graph: false, runner: f.runner }), /Refusing nonregular/);
		assert.equal(f.calls.some(call => call.args[0] === "-m"), false);
		assert.equal(await readFile(other, "utf8"), "preserve external bytes");
		await assert.rejects(access(join(f.paths.root, ".install-lock")));
	});
}

for (const relative of ["python/bin", "graph/node_modules", "graph/node_modules/.bin"]) {
	test(`installer refuses preexisting ${relative} directory symlink escape`, async t => {
		const f = await fixture(t), target = join(f.paths.root, relative), outside = join(f.root, "outside");
		await mkdir(dirname(target), { recursive: true });
		await mkdir(outside);
		await writeFile(join(outside, "keep"), "operator-owned");
		await symlink(outside, target, "dir");
		await assert.rejects(installRuntime(f.config, { runner: f.runner }), /contains a symlink/);
		assert.equal(f.calls.some(call => call.args[0] === "-m" || call.command === "npm"), false);
		assert.equal(await readFile(join(outside, "keep"), "utf8"), "operator-owned");
	});
}

test("installer preserves legitimate venv/bin/python symlink and writes atomic metadata", async t => {
	const f = await fixture(t);
	await mkdir(dirname(f.paths.python), { recursive: true });
	await symlink(process.execPath, f.paths.python);
	await writeFile(join(f.paths.root, "installed.json"), '{"old":true}\n');
	const result = await installRuntime(f.config, { graph: false, runner: f.runner });
	assert.equal(result.installed, true);
	assert.equal((await lstat(f.paths.python)).isSymbolicLink(), true);
	assert.equal(JSON.parse(await readFile(join(f.paths.root, "installed.json"), "utf8")).schema, 1);
	assert.equal(f.config.pythonBin, f.paths.python);
	await assert.rejects(access(join(f.paths.root, ".install-lock")));
});

test("manifest swapped to symlink during install is refused without changing target", async t => {
	const f = await fixture(t), target = join(f.root, "outside.json");
	await writeFile(target, "preserve");
	const runner = async (command, args) => {
		if (command === f.paths.memory && args[0] === "bootstrap") await symlink(target, join(f.paths.root, "installed.json"));
		return f.runner(command, args);
	};
	await assert.rejects(installRuntime(f.config, { graph: false, runner }), /Refusing nonregular/);
	assert.equal(await readFile(target, "utf8"), "preserve");
});

test("readiness on absent root creates no runtime files", async t => {
	const f = await fixture(t);
	const result = await backendStatus(f.config, { runner: f.runner });
	assert.equal(result.installation, null);
	await assert.rejects(access(f.paths.root));
});

test("safeDirectory is exported and refuses linked or special ancestors", async t => {
	const f = await fixture(t), real = join(f.root, "real"), alias = join(f.root, "alias");
	await mkdir(real);
	await symlink(real, alias, "dir");
	await assert.rejects(safeDirectory(join(alias, "child")), /contains a symlink/);
	await writeFile(join(real, "file"), "keep");
	await assert.rejects(safeDirectory(join(real, "file")), /not a directory/);
	await safeDirectory(join(real, "fresh/child"));
	assert.equal((await lstat(join(real, "fresh/child"))).isDirectory(), true);
});
