import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm, access } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { runProcess } from "../lib/process.js";

async function fixture(t) {
	const root = await mkdtemp(join(tmpdir(), "memory-process-test-"));
	t.after(() => rm(root, { recursive: true, force: true }));
	return root;
}

test("process runner roundtrips argv and stdin without invoking a shell", async () => {
	const args = ["; echo injected", "$(not-a-command)", "--embeddings", "a b"], input = "日本語 🧭\n";
	const source = "let data='';process.stdin.setEncoding('utf8');process.stdin.on('data',s=>data+=s);process.stdin.on('end',()=>console.log(JSON.stringify({args:process.argv.slice(1),data})));";
	const result = await runProcess(process.execPath, ["-e", source, "--", ...args], { stdin: input, timeoutMs: 5000 });
	assert.deepEqual(JSON.parse(result.stdout), { args, data: input });
	assert.equal(result.code, 0);
});

test("split UTF-8 Buffer writes on stdout and stderr preserve every character", async () => {
	const expected = "日本語 🧭 café\n";
	const source = `const b=Buffer.from(${JSON.stringify(expected)});let i=0;const send=()=>{if(i===b.length)return;const part=b.subarray(i,i+1);process.stdout.write(part);process.stderr.write(part);i++;setTimeout(send,5)};send();`;
	const result = await runProcess(process.execPath, ["-e", source], { timeoutMs: 5000 });
	assert.equal(result.stdout, expected);
	assert.equal(result.stderr, expected);
	assert.ok(!result.stdout.includes("\ufffd"));
});

test("output limits count UTF-8 bytes rather than decoded characters", async () => {
	await assert.rejects(runProcess(process.execPath, ["-e", "process.stdout.write('界'.repeat(1000));"], { maxBytes: 1500, timeoutMs: 5000 }), /exceeded output limit/);
});

test("nonzero exits include bounded diagnostics and missing binaries reject", async () => {
	await assert.rejects(runProcess(process.execPath, ["-e", "console.error('intentional failure');process.exit(7);"], { timeoutMs: 5000 }), /exited 7: intentional failure/);
	await assert.rejects(runProcess("/nonexistent-memory-process-test-command", [], { timeoutMs: 1000 }), { code: "ENOENT" });
});

test("already-aborted requests never launch and live requests cancel", async () => {
	const aborted = new AbortController();
	aborted.abort(new Error("operator cancelled before startup"));
	await assert.rejects(runProcess("/must-not-launch", [], { signal: aborted.signal }), /before startup/);
	const active = new AbortController();
	const pending = runProcess(process.execPath, ["-e", "setInterval(()=>{},1000);"], { signal: active.signal, timeoutMs: 5000 });
	active.abort();
	await assert.rejects(pending, /cancelled/);
});

test("timeout escalates for a parent that ignores SIGTERM", { skip: process.platform === "win32" }, async () => {
	await assert.rejects(runProcess(process.execPath, ["-e", "process.on('SIGTERM',()=>{});setInterval(()=>{},1000);"], { timeoutMs: 150 }), /timed out/);
});

test("parent early exit cannot cancel SIGKILL for a surviving descendant", { skip: process.platform === "win32" }, async t => {
	const root = await fixture(t), marker = join(root, "orphan-ran");
	// Child intentionally ignores TERM and detaches its output pipes. Before the
	// fix, parent close cleared the escalation timer, so this marker appeared.
	const descendant = `process.on('SIGTERM',()=>{});setTimeout(()=>{require('node:fs').writeFileSync(${JSON.stringify(marker)},'bad');process.exit(0)},900);setInterval(()=>{},1000);`;
	const parent = `require('node:child_process').spawn(process.execPath,['-e',${JSON.stringify(descendant)}],{stdio:'ignore'});process.on('SIGTERM',()=>process.exit(0));setInterval(()=>{},1000);`;
	await assert.rejects(runProcess(process.execPath, ["-e", parent], { timeoutMs: 250 }), /timed out/);
	// A bounded fixture timer checks the descendant's delayed side effect; this
	// is not polling any harness job or waiting on live installation work.
	await new Promise(resolve => setTimeout(resolve, 1100));
	await assert.rejects(access(marker));
});
