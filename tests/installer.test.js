import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, delimiter } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

function fixture(t, npmExit = 0) {
	const home = mkdtempSync(join(tmpdir(), "memory-rsi-install-"));
	t.after(() => rmSync(home, { recursive: true, force: true }));
	const source = join(home, "source with spaces"), bin = join(home, "bin");
	mkdirSync(join(source, "bin"), { recursive: true }); mkdirSync(bin);
	writeFileSync(join(source, "bin/setup.js"), 'process.stdout.write(JSON.stringify(process.argv.slice(2)))');
	writeFileSync(join(bin, "npm"), `#!/bin/sh\nprintf '%s\\n' "$@" > "$HOME/npm-args"\nexit ${npmExit}\n`, { mode: 0o755 });
	const env = { ...process.env, HOME: home, DSH_HOME: join(home, ".dsh"), MRSI_SOURCE_DIR: source, PATH: bin + delimiter + process.env.PATH };
	return { home, source, env };
}

test("shell installer delegates exact flags to bundled portable setup without global installs", t => {
	const f = fixture(t);
	const args = ["--yes", "--profile", "new-user", "--base", join(f.home, 'memory with "quotes"'), "--agent-id", "new-agent"];
	const result = spawnSync("sh", [fileURLToPath(new URL("../scripts/install.sh", import.meta.url)), ...args], { env: f.env, encoding: "utf8", timeout: 10000 });
	assert.equal(result.status, 0, result.stderr);
	assert.deepEqual(JSON.parse(result.stdout), args);
	const npm = readFileSync(join(f.home, "npm-args"), "utf8");
	assert.ok(npm.includes(f.source)); assert.match(npm, /--ignore-scripts/); assert.match(npm, /--omit=dev/);
	assert.doesNotMatch(npm, /--global|^-g$/m);
	assert.equal(existsSync(join(f.home, ".dsh")), false);
});

test("failed dependency preparation propagates failure and does not invoke setup", t => {
	const f = fixture(t, 23);
	const result = spawnSync("sh", [fileURLToPath(new URL("../scripts/install.sh", import.meta.url)), "--yes"], { env: f.env, encoding: "utf8", timeout: 10000 });
	assert.equal(result.status, 23); assert.equal(result.stdout, "");
});

test("packaged setup help is usable without running bootstrap or network commands", t => {
	const f = fixture(t);
	const result = spawnSync(process.execPath, [fileURLToPath(new URL("../bin/setup.js", import.meta.url)), "--help"], { env: f.env, encoding: "utf8", timeout: 10000 });
	assert.equal(result.status, 0, result.stderr); assert.match(result.stdout, /--apply-migration/);
	assert.match(result.stdout, /never generated/); assert.equal(existsSync(join(f.home, ".dsh")), false);
});
