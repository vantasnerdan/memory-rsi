/** Audited GitNexus graph-only adapter. No semantic query, arbitrary flags, or raw Cypher. */
import { readFile, realpath, stat } from "node:fs/promises";
import { isAbsolute, join } from "node:path";
import { homedir } from "node:os";
import { defineTool } from "@deepseek-ai/dsh-tools";
import { runGraphProcess } from "./gitnexus-process.js";
import { checkGraphFile, checkGraphPaths, hasGraphMetadata } from "./gitnexus-paths.js";

export const GITNEXUS_VERSION = "1.6.7";
export const GRAPH_ACTIONS = ["doctor", "status", "analyze", "query", "context", "impact", "detect_changes", "list"];
const SETUP = `Run memory_setup to install the managed GitNexus ${GITNEXUS_VERSION} runtime, or have the operator set gitnexusBin to that version's executable.`;
const FIELDS = {
	doctor: [], list: [], status: ["cwd"], analyze: ["cwd", "force"],
	query: ["cwd", "query", "limit"], context: ["cwd", "symbol", "file"],
	impact: ["cwd", "symbol", "file", "direction", "depth", "limit"],
	detect_changes: ["cwd", "scope", "base_ref"],
};

function text(value, field, max = 4096) {
	if (typeof value !== "string" || !value.trim() || value.length > max || /[\x00-\x1f\x7f]/.test(value)) {
		throw new Error(`${field} must be a nonempty string without control characters (max ${max}).`);
	}
	return value;
}
function integer(value, field, fallback, max) {
	if (value === undefined) return fallback;
	if (!Number.isSafeInteger(value) || value < 1 || value > max) throw new Error(`${field} must be an integer between 1 and ${max}.`);
	return value;
}
function choice(value, field, allowed, fallback) {
	if (value === undefined && fallback !== undefined) return fallback;
	if (!allowed.includes(value)) throw new Error(`${field} must be one of ${allowed.join(", ")}.`);
	return value;
}

/** Do not inherit storage overrides, automatic publishing, or embedding-service settings. */
export function graphEnvironment(config) {
	const env = { ...process.env };
	for (const key of Object.keys(env)) {
		if (key.startsWith("GITNEXUS_") || key === "UNDERSTAND_QUICKLY_TOKEN" || key === "NODE_OPTIONS") delete env[key];
	}
	const home = config.gitnexusHome || join(config.runtimeDir || join(process.env.DSH_HOME || join(homedir(), ".dsh"), "plugins", "memory-rsi", "runtime"), "graph-home");
	if (!isAbsolute(home)) throw new Error("gitnexusHome/runtimeDir must be absolute operator-configured paths.");
	return {
		...env, HOME: home, USERPROFILE: home, XDG_CACHE_HOME: join(home, ".cache"),
		HF_HOME: join(home, ".cache", "huggingface"), HF_HUB_OFFLINE: "1", TRANSFORMERS_OFFLINE: "1",
		GITNEXUS_LBUG_EXTENSION_INSTALL: "never", GITNEXUS_LANG: "en",
		LANG: "C.UTF-8", LC_ALL: "C.UTF-8", NO_COLOR: "1",
	};
}
function budget(config) { return integer(config.gitnexusTimeoutMs, "gitnexusTimeoutMs", 120_000, 900_000); }
async function cli(config, argv, options = {}) {
	const bin = config.gitnexusBin || "gitnexus";
	text(bin, "gitnexusBin");
	try {
		return await runGraphProcess(/\.[cm]?js$/.test(bin) ? process.execPath : bin,
			/\.[cm]?js$/.test(bin) ? [bin, ...argv] : argv,
			{ env: graphEnvironment(config), timeoutMs: budget(config), ...options });
	} catch (error) {
		if (error.code === "ENOENT") throw new Error(`GitNexus executable unavailable: ${bin}. ${SETUP}`);
		throw error;
	}
}
async function version(config, options) {
	const result = await cli(config, ["--version"], options);
	const value = result.stdout.trim();
	if (value !== GITNEXUS_VERSION) throw new Error(`Unsupported GitNexus version ${value || "unknown"}; graph-only behavior is audited only for ${GITNEXUS_VERSION}. ${SETUP}`);
	return value;
}

/** Pure lexical graph scan, deliberately NOT GitNexus's hybrid `query` command.
 * User input is reduced to identifier words; no raw Cypher string interpolation.
 */
export function lexicalGraphQuery(query, limit = 20) {
	const words = text(query, "query", 512).toLowerCase().match(/[\p{L}\p{N}_]+/gu);
	if (!words?.length || words.length > 16) throw new Error("query must contain 1–16 identifier words.");
	const predicates = [...new Set(words)].map((word) => `(lower(n.name) CONTAINS '${word}' OR lower(n.filePath) CONTAINS '${word}')`);
	return `MATCH (n) WHERE ${predicates.join(" AND ")} RETURN n.id AS id, n.name AS name, n.filePath AS filePath ORDER BY n.name, n.id LIMIT ${integer(limit, "limit", 20, 100)}`;
}

/** Fail closed: omission of --embeddings is NOT sufficient when .gitnexusrc enables it. */
async function checkAnalyzeConfig(cwd) {
	const path = join(cwd, ".gitnexusrc");
	let raw;
	try {
		const info = await checkGraphFile(path);
		if (!info) return;
		if (info.size > 65536) throw new Error(".gitnexusrc must be a regular file no larger than 64 KiB to validate safely.");
		raw = await readFile(path, "utf8");
	} catch (error) { if (error.code === "ENOENT") return; throw error; }
	let value;
	try { value = JSON.parse(raw); } catch { throw new Error("Invalid .gitnexusrc JSON; refusing analyze."); }
	if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(".gitnexusrc must be an object.");
	for (const level of [value, value.analyze]) {
		if (level === undefined) continue;
		if (!level || typeof level !== "object" || Array.isArray(level)) throw new Error(".gitnexusrc analyze must be an object.");
		for (const key of ["embeddings", "dropEmbeddings"]) {
			if (Object.hasOwn(level, key) && level[key] !== false) {
				throw new Error(`Graph-only analyze refused: .gitnexusrc ${key} is not false. Have the owner explicitly disable that setting; this tool never generates or deletes embeddings and does not rewrite user config.`);
			}
		}
	}
}

/** Strict runtime validation supplements the model-facing schema; unknown options never disappear. */
export async function buildGraphCommand(args) {
	choice(args?.action, "action", GRAPH_ACTIONS);
	for (const key of Object.keys(args)) {
		if (key !== "action" && !FIELDS[args.action].includes(key)) throw new Error(`Unknown option ${key} for GitNexus ${args.action}.`);
	}
	if (["doctor", "list"].includes(args.action)) return { argv: [args.action] };
	text(args.cwd, "cwd");
	if (!isAbsolute(args.cwd)) throw new Error("cwd must be the absolute path of the owner-selected repository root.");
	const cwd = await realpath(args.cwd);
	if (!(await stat(cwd)).isDirectory()) throw new Error("cwd must be a directory.");
	await checkGraphPaths(cwd);
	const repo = `--repo=${cwd}`;
	switch (args.action) {
		case "status": return { cwd, argv: ["status"] };
		case "analyze": {
			if (args.force !== undefined && typeof args.force !== "boolean") throw new Error("force must be boolean.");
			await checkAnalyzeConfig(cwd);
			return { cwd, argv: ["analyze", "--index-only", "--skip-git", ...(args.force ? ["--force"] : []), "--", cwd] };
		}
		case "query": return { cwd, argv: ["cypher", repo, "--", lexicalGraphQuery(args.query, args.limit)] };
		case "context":
		case "impact": {
			const argv = [args.action, repo];
			if (args.file !== undefined) argv.push(`--file=${text(args.file, "file")}`);
			if (args.action === "impact") argv.push(
				`--direction=${choice(args.direction, "direction", ["upstream", "downstream"], "upstream")}`,
				`--depth=${integer(args.depth, "depth", 3, 10)}`, `--limit=${integer(args.limit, "limit", 20, 100)}`,
			);
			argv.push("--", text(args.symbol, "symbol", 512));
			return { cwd, argv };
		}
		case "detect_changes": {
			const scope = choice(args.scope, "scope", ["unstaged", "staged", "all", "compare"], "unstaged");
			const argv = ["detect-changes", repo, `--scope=${scope}`];
			if (scope === "compare") {
				const ref = text(args.base_ref, "base_ref", 255);
				if (!/^[A-Za-z0-9_][A-Za-z0-9_.\/-]*$/.test(ref) || ref.includes("..")) throw new Error("base_ref must be a simple branch name or commit, not options or a revision expression.");
				argv.push(`--base-ref=${ref}`);
			} else if (args.base_ref !== undefined) throw new Error("base_ref is only valid with scope compare.");
			return { cwd, argv };
		}
	}
}

/** Installation/capability evidence, never a claim that a repository index works. */
export async function gitnexusStatus(config, { signal } = {}) {
	const status = { available: false, version: null, supportedVersion: false, indexOnly: false,
		nativeLoaded: false, graphReady: false, graphReadiness: "unverified: run analyze then query for an explicit repository",
		embeddings: false, semanticQuery: false, queryMode: "lexical-cypher", extensionDownloads: false };
	try {
		const result = await cli(config, ["--version"], { signal });
		status.available = true;
		status.version = result.stdout.trim();
		status.supportedVersion = status.version === GITNEXUS_VERSION;
		if (!status.supportedVersion) throw new Error(`Graph-only support requires audited GitNexus ${GITNEXUS_VERSION}. ${SETUP}`);
		const help = await cli(config, ["analyze", "--help"], { signal });
		status.indexOnly = help.stdout.includes("--index-only") && help.stdout.includes("--skip-git");
		const doctor = await cli(config, ["doctor"], { signal });
		status.nativeLoaded = /native\s+✓ lbugjs\.node loaded/.test(doctor.stdout);
		status.supported = status.indexOnly && status.nativeLoaded;
		status.diagnostic = doctor.stdout + doctor.stderr;
		if (!status.supported) status.error = "GitNexus is installed but required graph flags/native bindings are unavailable. " + SETUP;
	} catch (error) {
		if (signal?.aborted) throw error;
		status.supported = false;
		status.error = error.message;
	}
	return status;
}

export async function executeGraph(config, args, { signal } = {}) {
	const command = await buildGraphCommand(args);
	if (args.action === "doctor") return { result: JSON.stringify(await gitnexusStatus(config, { signal }), null, 2) };
	await version(config, { signal });
	if (args.action === "status" && !(await hasGraphMetadata(command.cwd))) {
		return { result: `Selected repository is not indexed: ${command.cwd}. Run gitnexus action=analyze with this cwd. Ancestor indexes were not consulted.` };
	}
	const output = await cli(config, command.argv, { cwd: command.cwd, signal });
	// detect_changes formats backend errors as English text while still exiting zero.
	if (args.action === "detect_changes" && /^\s*Error:/.test(output.stdout)) {
		throw new Error(`GitNexus graph operation failed: ${output.stdout.slice(0, 4096)}`);
	}
	// Several backend failures are JSON error envelopes despite a CLI exit code of zero.
	try {
		const parsed = JSON.parse(output.stdout);
		if (parsed?.error) throw new Error(`GitNexus graph operation failed: ${String(parsed.error).slice(0, 4096)}`);
	} catch (error) { if (!(error instanceof SyntaxError)) throw error; }
	return { result: `${args.action === "query" ? "Graph-only lexical search (not semantic search).\n" : ""}${output.stdout}${output.stderr ? `\nDiagnostics:\n${output.stderr}` : ""}${output.truncated ? "\n[Output truncated; narrow the query.]" : ""}` };
}

export function registerGitNexus(ctx, config) {
	ctx.tools.register(defineTool({
		name: "gitnexus",
		description: "Graph-only GitNexus: doctor, status, analyze, query, context, impact, detect_changes, list. Analyze explicitly indexes only the owner-selected cwd (no instruction/skill injection); query is lexical graph search, never embeddings. No arbitrary flags, raw Cypher, wiki/LLM, model download, or embedding removal. Available before installation; doctor reports setup needs. Repo actions require an absolute cwd root. Existing .gitnexusrc embedding settings fail closed.",
		parameters: {
			action: { type: "string", enum: GRAPH_ACTIONS, required: true, description: "Allowlisted graph action." },
			cwd: { type: "string", description: "Absolute owner-selected repository root; required except doctor/list. Analyze writes its .gitnexus index and the managed runtime registry." },
			query: { type: "string", description: "query only: 1–16 lexical identifier/path words (AND match)." },
			symbol: { type: "string", description: "context/impact only: exact symbol name." },
			file: { type: "string", description: "context/impact only: disambiguating source path." },
			limit: { type: "integer", description: "query/impact only: 1–100 results, default 20." },
			depth: { type: "integer", description: "impact only: relationship depth 1–10, default 3." },
			direction: { type: "string", enum: ["upstream", "downstream"], description: "impact only; default upstream." },
			scope: { type: "string", enum: ["unstaged", "staged", "all", "compare"], description: "detect_changes only; default unstaged." },
			base_ref: { type: "string", description: "Required only for compare scope: branch or commit." },
			force: { type: "boolean", description: "analyze only: rebuild even if commit unchanged; preserves existing embeddings." },
		},
		output: {
			schema: { type: "object", additionalProperties: false, properties: { result: { type: "string", required: true } } },
			render: (_args, value) => [{ type: "text", text: value.result }],
		},
		timeoutMs: budget(config) * 3 + 2000,
		isConcurrencySafe: () => false,
		execute: (args, exec) => executeGraph(config, args, { signal: exec.signal }),
	}));
}
