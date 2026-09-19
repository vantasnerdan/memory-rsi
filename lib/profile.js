import { readFile, writeFile, mkdir, rename, lstat } from "node:fs/promises";
import { join, dirname } from "node:path";
import { randomUUID } from "node:crypto";
import { dshHome, PACKAGE_ROOT, safeDirectory } from "./runtime.js";
import { runProcess } from "./process.js";
import yaml from "js-yaml";

export const MANAGED_BEGIN = "# >>> memory-rsi installer (managed; safe to delete) >>>";
export const MANAGED_END = "# <<< memory-rsi installer <<<";
// Parse DSH expression nodes as inert data when validating unrelated rows.
const patchSchema = yaml.DEFAULT_SCHEMA.extend([new yaml.Type("tag:yaml.org,2002:js", { kind: "scalar", construct: value => ({ expression: value }) })]);
function validatePatch(source) {
	const entries = yaml.load(source, { schema: patchSchema }) ?? [];
	if (!Array.isArray(entries)) throw new Error("DSH patch must be a YAML array; no changes were written.");
	return entries;
}

export function profileDirectory(profile) {
	if (typeof profile !== "string" || !/^[a-zA-Z0-9][a-zA-Z0-9_-]*$/.test(profile)) throw new Error("profile must be a simple name, not a filesystem path");
	return join(dshHome(), "profiles", profile);
}

async function readOptional(path) {
	try {
		const info = await lstat(path);
		if (!info.isFile() || info.isSymbolicLink()) throw new Error(`Refusing nonregular configuration file: ${path}`);
		return await readFile(path, "utf8");
	} catch (error) { if (error.code === "ENOENT") return ""; throw error; }
}

export async function readManagedSettings(profile) {
	const source = await readOptional(join(profileDirectory(profile), "cordis.patch.yml"));
	if (!source.includes(MANAGED_BEGIN)) return {};
	const begin = source.indexOf(MANAGED_BEGIN), end = source.indexOf(MANAGED_END);
	if (end < begin || source.split(MANAGED_BEGIN).length !== 2 || source.split(MANAGED_END).length !== 2) throw new Error("Malformed installer markers; repair before setup.");
	const entries = validatePatch(source);
	const matching = entries.filter(entry => entry?.id === "memory-rsi");
	if (matching.length !== 1 || !/^-\s+id:\s*(?:memory-rsi|"memory-rsi"|'memory-rsi')\s*(?:#.*)?$/m.test(source.slice(begin, end))) throw new Error("Expected exactly one top-level managed memory-rsi row");
	const config = matching[0]?.config;
	if (!config || typeof config !== "object" || Array.isArray(config)) throw new Error("Managed memory-rsi config is not a mapping");
	return config;
}

/** Preserve unknown settings, surrounding comments, and anchors used by other rows. */
export function patchConfiguration(original, settings) {
	const count = marker => original.split(marker).length - 1;
	const begins = count(MANAGED_BEGIN), ends = count(MANAGED_END);
	if (begins !== ends || begins > 1) throw new Error("Malformed or duplicate memory-rsi installer markers; repair explicitly before setup.");
	let block;
	if (begins) {
		const start = original.indexOf(MANAGED_BEGIN), end = original.indexOf(MANAGED_END);
		if (end < start) throw new Error("Out-of-order memory-rsi installer markers");
		block = original.slice(start, end + MANAGED_END.length);
		if (!/^  config:(?:\s*&[A-Za-z0-9_-]+)?\s*$/m.test(block)) throw new Error("Managed config must be a block mapping; preserve/customize complex config manually.");
	} else {
		const hasRow = entries => entries.some(entry => entry?.id === "memory-rsi" || (Array.isArray(entry?.insert) && hasRow(entry.insert)));
		if (hasRow(validatePatch(original))) throw new Error("An unmanaged memory-rsi row already exists; configure it manually or explicitly adopt it into an installer-managed block.");
		block = `${MANAGED_BEGIN}\n- id: memory-rsi\n  config:\n${MANAGED_END}`;
	}
	for (const [key, value] of Object.entries(settings)) {
		if (value === undefined) continue;
		if (!/^[a-zA-Z][a-zA-Z0-9]*$/.test(key)) throw new Error("Invalid configuration key");
		const pattern = new RegExp(`^    ${key}:.*(?:\\r?\\n(?:[ \\t]{5,}[^\\n]*|[ \\t]*))*(?=\\r?\\n|$)`, "m");
		const anchor = block.match(new RegExp(`^    ${key}:\\s*(&[A-Za-z0-9_-]+)\\s`, "m"))?.[1];
		const line = `    ${key}: ${anchor ? anchor + " " : ""}${JSON.stringify(value)}`;
		if (pattern.test(block)) block = block.replace(pattern, () => line);
		else block = block.replace(MANAGED_END, `${line}\n${MANAGED_END}`);
	}
	const start = original.indexOf(MANAGED_BEGIN), end = original.indexOf(MANAGED_END) + MANAGED_END.length;
	const result = !begins ? original.replace(/^\s*\[\]\s*$/m, "").trimEnd() + "\n\n" + block + "\n" : original.slice(0, start) + block + original.slice(end);
	validatePatch(result);
	return result;
}

async function atomicConfig(path, before, next) {
	if (next === before) return;
	await mkdir(dirname(path), { recursive: true });
	if (await readOptional(path) !== before) throw new Error(`Configuration changed during setup: ${path}; retry after reviewing it.`);
	const temp = join(dirname(path), `.memory-rsi-${randomUUID()}.tmp`);
	await writeFile(temp, next, { flag: "wx", mode: 0o600 });
	await rename(temp, path);
}

export async function preflightProfile(profile) {
	const directory = profileDirectory(profile);
	await safeDirectory(directory);
	const preexistingManifest = await readOptional(join(directory, "package.json"));
	if (preexistingManifest) JSON.parse(preexistingManifest);
	patchConfiguration(await readOptional(join(directory, "cordis.patch.yml")), {});
	return directory;
}

/** Called only by explicit installer CLI, never plugin import or a model request. */
export async function configureProfile(profile, config, { signal, add = true } = {}) {
	const directory = await preflightProfile(profile);
	if (add) await runProcess("dsh", ["plugin", "--profile", profile, "add", PACKAGE_ROOT], { signal, timeoutMs: config.setupTimeoutMs ?? 600000 });
	const manifestPath = join(directory, "package.json");
	const before = await readOptional(manifestPath);
	if (!before) throw new Error(`DSH did not create profile manifest: ${manifestPath}`);
	const manifest = JSON.parse(before);
	manifest.dsh ??= {};
	manifest.dsh.profile ??= {};
	const bundles = manifest.dsh.profile.bundles ??= ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app"];
	if (!Array.isArray(bundles) || bundles.some(bundle => typeof bundle !== "string")) throw new Error("Invalid profile bundle list; repair it before installation.");
	if (!bundles.includes("memory-rsi")) bundles.push("memory-rsi");
	const patchPath = join(directory, "cordis.patch.yml");
	const oldPatch = await readOptional(patchPath);
	const settings = {
		memoryBin: config.memoryBin, pythonBin: config.pythonBin, bootstrapPython: config.bootstrapPython,
		base: config.base, agentId: config.agentId, runtimeDir: config.runtimeDir,
		gitnexusBin: config.gitnexusBin, gitnexusHome: config.gitnexusHome,
		instructionFiles: config.instructionFiles, timeoutMs: config.timeoutMs,
	};
	const patch = patchConfiguration(oldPatch, settings);
	// Validate both edits before touching either file; each replacement is atomic.
	await atomicConfig(manifestPath, before, JSON.stringify(manifest, null, 2) + "\n");
	await atomicConfig(patchPath, oldPatch, patch);
	return { profile, manifest: manifestPath, patch: patchPath, activation: "Reload/restart this profile if it was already running; package code is not automatically hot-reloaded." };
}
