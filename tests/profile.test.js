import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, readFile, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import yaml from "js-yaml";
import { patchConfiguration, profileDirectory, readManagedSettings, configureProfile, MANAGED_BEGIN, MANAGED_END } from "../lib/profile.js";

const block = body => `${MANAGED_BEGIN}\n- id: memory-rsi\n  config:\n${body}${MANAGED_END}\n`;

test("empty YAML patch seeds one row and repeated identical settings are stable", () => {
	for (const original of ["", "[]\n", "# operator header\n[]\n"]) {
		const settings = { base: "/portable/memory", instructionFiles: ["/selected/AGENTS.md"], timeoutMs: 60000 };
		const once = patchConfiguration(original, settings);
		assert.equal(yaml.load(once).length, 1);
		assert.deepEqual(yaml.load(once)[0].config, settings);
		assert.equal(patchConfiguration(once, settings), once);
		if (original.includes("operator header")) assert.ok(once.includes("# operator header"));
	}
});

test("unmanaged memory-rsi rows with comments, quoting, flow style, or nested inserts fail closed", () => {
	for (const original of [
		"- id: memory-rsi # operator managed\n  config: {base: /original}\n",
		"- id: 'memory-rsi'\n  config: {base: /original}\n",
		"[{id: memory-rsi, config: {base: /original}}]\n",
		"- id: wrapper\n  insert:\n    - id: memory-rsi # nested\n      config: {base: /original}\n",
	]) assert.throws(() => patchConfiguration(original, { base: "/replacement" }), /unmanaged memory-rsi row/);
});

test("known-key updates retain anchors used by unrelated rows", () => {
	const original = block("    base: &memoryRoot /old\n    custom: keep\n") + "- id: unrelated\n  config:\n    path: *memoryRoot\n";
	const result = patchConfiguration(original, { base: "/new" });
	const parsed = yaml.load(result);
	assert.equal(parsed[0].config.base, "/new");
	assert.equal(parsed[1].config.path, "/new");
	assert.ok(result.includes("base: &memoryRoot"));
	assert.ok(result.endsWith("- id: unrelated\n  config:\n    path: *memoryRoot\n"));
});

test("config mapping anchors and unknown nested settings remain intact", () => {
	const original = `${MANAGED_BEGIN}\n- id: memory-rsi\n  config: &memoryConfig\n    base: /old\n    unknownSetting:\n      nested: [a, b]\n      enabled: false\n    # custom comment\n${MANAGED_END}\n- id: consumer\n  config: *memoryConfig\n`;
	const result = patchConfiguration(original, { base: "/new", timeoutMs: 12000 });
	const parsed = yaml.load(result);
	assert.equal(parsed[1].config, parsed[0].config);
	assert.deepEqual(parsed[0].config.unknownSetting, { nested: ["a", "b"], enabled: false });
	assert.ok(result.includes("    unknownSetting:\n      nested: [a, b]\n      enabled: false\n    # custom comment\n"));
});

test("undefined settings never erase an existing configured value", () => {
	const original = block("    gitnexusBin: /custom/executable\n    base: /memory\n");
	assert.equal(patchConfiguration(original, { gitnexusBin: undefined }), original);
	const result = patchConfiguration(original, { pythonBin: undefined, base: "/next" });
	assert.equal(yaml.load(result)[0].config.gitnexusBin, "/custom/executable");
	assert.equal(Object.hasOwn(yaml.load(result)[0].config, "pythonBin"), false);
	assert.ok(!result.includes(": undefined"));
});

test("surrounding bytes, comments, and unrelated CRLF rows survive managed updates", () => {
	const prefix = "# Exact operator prefix\r\n- id: before\r\n  config: {value: keep}\r\n";
	const suffix = "# Exact operator suffix\r\n- id: after\r\n  config:\r\n    enabled: true\r\n";
	const original = prefix + block("    base: /old\n    unknown: human\n") + suffix;
	const result = patchConfiguration(original, { base: "/new" });
	assert.ok(result.startsWith(prefix));
	assert.ok(result.endsWith(suffix));
	assert.ok(result.includes("    unknown: human\n"));
	assert.equal(yaml.load(result).length, 3);
});

test("multiline owned settings are replaced without consuming neighboring settings", () => {
	const original = block("    instructionFiles:\n      - /old/one\n      - /old/two\n    custom: preserved\n");
	const result = patchConfiguration(original, { instructionFiles: ["/new/AGENTS.md"] });
	assert.deepEqual(yaml.load(result)[0].config, { instructionFiles: ["/new/AGENTS.md"], custom: "preserved" });
});

test("DSH !!js expressions remain inert and byte-preserved", () => {
	const unrelated = "- id: unrelated\n  config:\n    value: !!js 'globalThis.__memoryProfileExpressionRan = true'\n";
	const result = patchConfiguration(unrelated, { base: "/memory" });
	assert.ok(result.includes(unrelated));
	assert.equal(globalThis.__memoryProfileExpressionRan, undefined);
});

test("malformed marker pairs, duplicate markers and invalid YAML fail before returning edits", () => {
	for (const original of [
		`${MANAGED_BEGIN}\n- id: memory-rsi\n  config: {}\n`,
		`${MANAGED_END}\n${MANAGED_BEGIN}\n`,
		block("    base: /old\n") + block("    base: /other\n"),
		`${MANAGED_BEGIN}\n${MANAGED_BEGIN}\n${MANAGED_END}\n`,
		"not: a-list\n",
		"- id: [broken\n",
		block("    base: /old\n") + "- id: alias\n  config: *missingAnchor\n",
	]) assert.throws(() => patchConfiguration(original, { base: "/new" }));
});

test("settings are encoded as values rather than YAML structure", () => {
	const value = "C:\\folder\\quoted \"# : newline\n日本語";
	const result = patchConfiguration("", { base: value });
	assert.equal(yaml.load(result)[0].config.base, value);
	assert.throws(() => patchConfiguration("", { "bad-key: injection": true }), /Invalid configuration key/);
});

test("profile names refuse traversal and filesystem paths", () => {
	for (const value of ["../escape", "/absolute", "has/slash", "", ".hidden", "a\\b", null]) {
		assert.throws(() => profileDirectory(value), /simple name/);
	}
});

test("managed settings resolve an external anchor from an earlier unrelated row", async t => {
	const home = await mkdtemp(join(tmpdir(), "memory-profile-anchor-test-"));
	const beforeHome = process.env.DSH_HOME;
	process.env.DSH_HOME = home;
	t.after(async () => { if (beforeHome === undefined) delete process.env.DSH_HOME; else process.env.DSH_HOME = beforeHome; await rm(home, { recursive: true, force: true }); });
	const directory = profileDirectory("anchor-profile");
	await mkdir(directory, { recursive: true });
	const patchPath = join(directory, "cordis.patch.yml");
	const selected = join(home, "operator-memory");
	const prefix = `# Operator-owned anchor\n- id: unrelated\n  config:\n    path: &sharedbase ${JSON.stringify(selected)}\n`;
	const original = prefix + block("    base: *sharedbase\n    custom: retained\n");
	await writeFile(patchPath, original);
	assert.equal(yaml.load(original)[1].config.base, selected);
	const settings = await readManagedSettings("anchor-profile");
	assert.equal(settings.base, selected);
	assert.equal(settings.custom, "retained");
	assert.equal(await readFile(patchPath, "utf8"), original);
});

test("temporary profile roundtrip preserves manifest bundles and unknown settings", async t => {
	const home = await mkdtemp(join(tmpdir(), "memory-profile-test-"));
	const beforeHome = process.env.DSH_HOME;
	process.env.DSH_HOME = home;
	t.after(async () => { if (beforeHome === undefined) delete process.env.DSH_HOME; else process.env.DSH_HOME = beforeHome; await rm(home, { recursive: true, force: true }); });
	const directory = profileDirectory("test-profile");
	await mkdir(directory, { recursive: true });
	const manifestPath = join(directory, "package.json"), patchPath = join(directory, "cordis.patch.yml");
	await writeFile(manifestPath, JSON.stringify({ name: "operator-profile", custom: { keep: true }, dsh: { profile: { bundles: ["custom-bundle"] } } }));
	await writeFile(patchPath, block("    base: /old\n    unknown: custom\n"));
	await configureProfile("test-profile", { base: "/new", timeoutMs: 12345 }, { add: false });
	const settings = await readManagedSettings("test-profile");
	assert.equal(settings.base, "/new");
	assert.equal(settings.unknown, "custom");
	const manifest = JSON.parse(await readFile(manifestPath, "utf8"));
	assert.deepEqual(manifest.custom, { keep: true });
	assert.deepEqual(manifest.dsh.profile.bundles, ["custom-bundle", "memory-rsi"]);
	const firstPatch = await readFile(patchPath, "utf8");
	await configureProfile("test-profile", { base: "/new", timeoutMs: 12345 }, { add: false });
	assert.equal(await readFile(patchPath, "utf8"), firstPatch);
	assert.deepEqual(JSON.parse(await readFile(manifestPath, "utf8")).dsh.profile.bundles, ["custom-bundle", "memory-rsi"]);
});
