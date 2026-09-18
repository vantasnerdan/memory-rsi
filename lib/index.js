/**
 * memory-rsi — a DeepSeek Harness (DSH) plugin package.
 *
 * Exposes the vendored `agent-memory` CLI (progressive-disclosure memory:
 * Git-backed markdown with YAML frontmatter, ripgrep grep, BM25 search,
 * SQLite index cache, git sync) as model tools. The Python CLI is bundled
 * under `cli/`; this plugin shells out to it and returns its JSON output.
 *
 * Plugin contract mirrors first-party DSH tool plugins such as
 * `@deepseek-ai/dsh-tool-web`: named exports `name`, `inject`, `Config`,
 * `apply(ctx, config)`.
 */
import { spawn } from "node:child_process";
import z from "@deepseek-ai/schemastery";
import { defineTool } from "@deepseek-ai/dsh-tools";

/** Cordis plugin name used by loader diagnostics. */
export const name = "memory-rsi";

/** Services required to register tools and prompt guidance. */
export const inject = ["tools", "systemPrompt"];

/**
 * Plugin configuration (set from the profile's patch layer).
 * - `memoryBin`: the `memory` console script to invoke. When missing from
 *   PATH, the plugin falls back to `pythonBin -m agent_memory`.
 * - `pythonBin`: Python interpreter used for the module fallback.
 * - `base`: default `--base` directory for the memory repo; empty means the
 *   CLI's own config/env/cwd auto-detection.
 * - `timeoutMs`: cooperative per-call timeout budget.
 */
export const Config = z.object({
	memoryBin: z.string().default("memory"),
	pythonBin: z.string().default("python3"),
	base: z.string().default(""),
	// Agent identity for writes and "own" scope. Exported to the CLI as the
	// AGENT_ID env var; memory_new/memory_update also forward it as --author
	// when the model does not pass one. Empty leaves the CLI's own detection
	// (AGENT_ID from the host environment) in charge.
	agentId: z.string().default(""),
	timeoutMs: z.number().default(60_000),
});

/**
 * Run the memory CLI and collect stdout.
 * @param {string} memoryBin resolved console script
 * @param {string} pythonBin python interpreter for module fallback
 * @param {string[]} args CLI arguments (already includes --json-output when used)
 * @param {{cwd?: string, stdin?: string, timeoutMs: number, signal?: AbortSignal, env?: object}} opts
 * @returns {Promise<string>} stdout
 */
function runCli(memoryBin, pythonBin, args, opts) {
	return new Promise((resolve, reject) => {
		const launch = (cmd, cmdArgs) => {
			const child = spawn(cmd, cmdArgs, {
				cwd: opts.cwd,
				env: opts.env ?? { ...process.env, PYTHONUNBUFFERED: "1" },
			});
			let stdout = "";
			let stderr = "";
			const timer = setTimeout(() => child.kill("SIGTERM"), opts.timeoutMs);
			const onAbort = () => child.kill("SIGTERM");
			opts.signal?.addEventListener("abort", onAbort, { once: true });
			child.stdout.on("data", (chunk) => {
				stdout += chunk;
			});
			child.stderr.on("data", (chunk) => {
				stderr += chunk;
			});
			child.on("error", (error) => {
				clearTimeout(timer);
				opts.signal?.removeEventListener("abort", onAbort);
				reject(error);
			});
			child.on("close", (code) => {
				clearTimeout(timer);
				opts.signal?.removeEventListener("abort", onAbort);
				if (code === 0) resolve(stdout);
				else {
					const detail = stderr.trim() || `exit code ${code}`;
					reject(new Error(`memory ${args[0] ?? ""} failed: ${detail}`.trim()));
				}
			});
			if (opts.stdin !== undefined) {
				child.stdin.write(opts.stdin);
			}
			child.stdin.end();
		};
		launch(memoryBin, args);
	});
}

/**
 * Resolve the memory command and run it, retrying once via
 * `pythonBin -m agent_memory` when the console script is unavailable
 * (ENOENT). Other failures are surfaced as-is.
 */
async function memory(config, args, opts = {}) {
	const base = [];
	if (opts.jsonOutput !== false) base.push("--json-output");
	const fullArgs = [...base, ...args];
	if (config.base) fullArgs.push("--base", config.base);
	const runOpts = {
		// Run inside the memory base so entry paths that tools return relative
		// to the memory root (e.g. from memory_ls/memory_toc) resolve the same
		// way for every subcommand.
		cwd: opts.cwd ?? config.base || undefined,
		stdin: opts.stdin,
		timeoutMs: config.timeoutMs,
		signal: opts.signal,
		env: config.agentId
			? { ...process.env, PYTHONUNBUFFERED: "1", AGENT_ID: config.agentId }
			: undefined,
	};
	try {
		return await runCli(config.memoryBin, config.pythonBin, fullArgs, runOpts);
	} catch (error) {
		if (error?.code === "ENOENT" && config.memoryBin !== "__bundled__") {
			return runCli(config.pythonBin, ["-m", "agent_memory", ...fullArgs], runOpts);
		}
		throw error;
	}
}

/** Bounded model-facing result envelope. */
const MAX_OUTPUT_CHARS = 100_000;
function clip(text) {
	return text.length > MAX_OUTPUT_CHARS
		? `${text.slice(0, MAX_OUTPUT_CHARS)}\n…(truncated at ${MAX_OUTPUT_CHARS} characters; narrow the query)`
		: text;
}

/** Shared output schema: the CLI's stdout as one text block. */
const cliOutput = {
	schema: {
		type: "object",
		additionalProperties: false,
		properties: {
			result: { type: "string", required: true },
		},
	},
	render: (_args, value) => [{ type: "text", text: value.result }],
};

/** Validate an enum-ish string against an allow-list; throws otherwise. */
function requireChoice(toolName, field, value, choices) {
	if (value !== undefined && !choices.includes(value)) {
		throw new Error(`${toolName}: ${field} must be one of ${choices.join(", ")}`);
	}
	return value;
}

/** Tools that only read memory are concurrency-safe. */
const concurrencySafe = () => true;

/**
 * Register every memory tool on the injected `tools` service.
 * @param {import("@deepseek-ai/cordis").Context} ctx cordis context
 * @param {unknown} rawConfig validated plugin config
 */
export function apply(ctx, rawConfig) {
	const config = rawConfig;

	ctx.tools.register(defineTool({
		name: "memory_ls",
		description:
			"List the agent-memory directory tree with progressive disclosure: directories first, then markdown entries with their frontmatter summaries. Start here to discover what memory exists.",
		parameters: {
			path: { type: "string", description: "Subdirectory to list, relative to the memory base. Defaults to the root." },
		},
		output: cliOutput,
		timeoutMs: config.timeoutMs,
		isConcurrencySafe: concurrencySafe,
		async execute(args, exec) {
			const argv = ["ls"];
			if (args.path) argv.push(args.path);
			return { result: clip(await memory(config, argv, { signal: exec.signal })) };
		},
	}));

	ctx.tools.register(defineTool({
		name: "memory_toc",
		description:
			"Show the table of contents of one memory entry: its frontmatter summary and section titles, without full section bodies.",
		parameters: {
			file_path: { type: "string", required: true, description: "Path of the markdown memory entry." },
		},
		output: cliOutput,
		timeoutMs: config.timeoutMs,
		isConcurrencySafe: concurrencySafe,
		async execute(args, exec) {
			return { result: clip(await memory(config, ["toc", args.file_path], { signal: exec.signal })) };
		},
	}));

	ctx.tools.register(defineTool({
		name: "memory_section",
		description:
			"Read one section of a memory entry by (partial) title. The narrowest read: prefer this over reading whole files.",
		parameters: {
			file_path: { type: "string", required: true, description: "Path of the markdown memory entry." },
			title: { type: "string", required: true, description: "Section title; partial matches are accepted." },
		},
		output: cliOutput,
		timeoutMs: config.timeoutMs,
		isConcurrencySafe: concurrencySafe,
		async execute(args, exec) {
			return { result: clip(await memory(config, ["section", args.file_path, args.title], { signal: exec.signal })) };
		},
	}));

	ctx.tools.register(defineTool({
		name: "memory_search",
		description:
			"BM25 relevance-ranked search over memory sections, with frontmatter filters (category, confidence, author, status, tag). Returns best-passage snippets. Prefer this for semantic questions over memory_grep.",
		parameters: {
			query: { type: "string", required: true, description: "Natural-language query." },
			scope: { type: "string", description: "Directory scope: all, own, shared, or agent:<id>. Default: all." },
			field: { type: "string", description: "Ranking field: description, tags, or content. Default: content." },
			category: { type: "string", description: "Filter by category." },
			tag: { type: "string", description: "Filter by tag." },
			limit: { type: "number", description: "Maximum results (default 10)." },
			no_cache: { type: "boolean", description: "Bypass the SQLite index cache and read files directly." },
			include_sources: { type: "boolean", description: "Also search configured additional sources (rules, skills)." },
		},
		output: cliOutput,
		timeoutMs: config.timeoutMs,
		isConcurrencySafe: concurrencySafe,
		async execute(args, exec) {
			requireChoice("memory_search", "scope", args.scope, ["all", "own", "shared"]);
			requireChoice("memory_search", "field", args.field, ["description", "tags", "content"]);
			const argv = ["search", args.query];
			if (args.scope) argv.push("--scope", args.scope);
			if (args.field) argv.push("--field", args.field);
			if (args.category) argv.push("--category", args.category);
			if (args.tag) argv.push("--tag", args.tag);
			if (args.limit !== undefined) argv.push("--limit", String(Math.trunc(args.limit)));
			if (args.no_cache) argv.push("--no-cache");
			if (args.include_sources) argv.push("--include-sources");
			return { result: clip(await memory(config, argv, { signal: exec.signal })) };
		},
	}));

	ctx.tools.register(defineTool({
		name: "memory_grep",
		description:
			"Exact or regex search over memory content via ripgrep. Use for identifiers, error strings, and exact phrases.",
		parameters: {
			pattern: { type: "string", required: true, description: "Regex or literal pattern." },
			fixed_strings: { type: "boolean", description: "Treat the pattern as a literal string." },
			ignore_case: { type: "boolean", description: "Case-insensitive match." },
			context: { type: "number", description: "Lines of context around each match." },
			scope: { type: "string", description: "Directory scope: all, own, shared, or agent:<id>." },
		},
		output: cliOutput,
		timeoutMs: config.timeoutMs,
		isConcurrencySafe: concurrencySafe,
		async execute(args, exec) {
			requireChoice("memory_grep", "scope", args.scope, ["all", "own", "shared"]);
			const argv = ["grep", args.pattern];
			if (args.fixed_strings) argv.push("--fixed-strings");
			if (args.ignore_case) argv.push("--ignore-case");
			if (args.context !== undefined) argv.push("--context", String(Math.trunc(args.context)));
			if (args.scope) argv.push("--scope", args.scope);
			return { result: clip(await memory(config, argv, { signal: exec.signal })) };
		},
	}));

	ctx.tools.register(defineTool({
		name: "memory_new",
		description:
			"Create a new memory entry with validated YAML frontmatter. The CLI commits and pushes to the memory repo automatically unless no_git is set.",
		parameters: {
			name: { type: "string", required: true, description: "Entry file name (slug)." },
			description: { type: "string", required: true, description: "One-line description for the frontmatter." },
			body: { type: "string", description: "Full body content of the entry." },
			author: { type: "string", description: "Agent ID override (default: AGENT_ID env var)." },
			tags: { type: "string", description: "Comma-separated tags." },
			category: { type: "string", description: "Category subdirectory: atlas, efforts, calendar, moc, or a configured custom category." },
			confidence: { type: "string", description: "Confidence level: established, working, or exploratory." },
			status: { type: "string", description: "Entry status: active, archived, or draft. Default: active." },
			shared: { type: "boolean", description: "Write to memory/shared/ instead of memory/{agent_id}/." },
			no_git: { type: "boolean", description: "Skip git add/commit/push." },
			allow_non_main_branch: { type: "boolean", description: "Allow committing while HEAD is on a non-default branch." },
		},
		output: cliOutput,
		timeoutMs: config.timeoutMs,
		isConcurrencySafe: () => false,
		async execute(args, exec) {
			requireChoice("memory_new", "confidence", args.confidence, ["established", "working", "exploratory"]);
			requireChoice("memory_new", "status", args.status, ["active", "archived", "draft"]);
			const argv = ["new", args.name, "--description", args.description];
			const author = args.author ?? config.agentId;
			if (author) argv.push("--author", author);
			if (args.tags) argv.push("--tags", args.tags);
			if (args.category) argv.push("--category", args.category);
			if (args.confidence) argv.push("--confidence", args.confidence);
			if (args.status) argv.push("--status", args.status);
			if (args.shared) argv.push("--shared");
			if (args.no_git) argv.push("--no-git");
			if (args.allow_non_main_branch) argv.push("--allow-non-main-branch");
			// Body goes over stdin ('-' mode) so multi-line content survives argv limits.
			if (args.body !== undefined && args.body !== "") {
				// Body goes over stdin ('-' mode) so multi-line content survives argv limits.
				argv.push("--body", "-");
				return {
					result: clip(await memory(config, argv, { signal: exec.signal, stdin: args.body })),
				};
			}
			return { result: clip(await memory(config, argv, { signal: exec.signal })) };
		},
	}));

	ctx.tools.register(defineTool({
		name: "memory_update",
		description:
			"Update an existing memory entry: replace the body or section, adjust tags/confidence/status. Commits and pushes unless no_git is set.",
		parameters: {
			file_path: { type: "string", required: true, description: "Path of the markdown memory entry." },
			body: { type: "string", description: "New body content (replaces the entry body)." },
			tags: { type: "string", description: "Replace tags (comma-separated)." },
			add_tags: { type: "string", description: "Append tags (comma-separated)." },
			confidence: { type: "string", description: "Confidence level: established, working, or exploratory." },
			status: { type: "string", description: "Status: active, archived, or draft." },
			no_git: { type: "boolean", description: "Skip git add/commit/push." },
			allow_non_main_branch: { type: "boolean", description: "Allow committing while HEAD is on a non-default branch." },
		},
		output: cliOutput,
		timeoutMs: config.timeoutMs,
		isConcurrencySafe: () => false,
		async execute(args, exec) {
			requireChoice("memory_update", "confidence", args.confidence, ["established", "working", "exploratory"]);
			requireChoice("memory_update", "status", args.status, ["active", "archived", "draft"]);
			const argv = ["update", args.file_path];
			if (args.tags) argv.push("--tags", args.tags);
			if (args.add_tags) argv.push("--add-tags", args.add_tags);
			if (args.confidence) argv.push("--confidence", args.confidence);
			if (args.status) argv.push("--status", args.status);
			if (args.no_git) argv.push("--no-git");
			if (args.allow_non_main_branch) argv.push("--allow-non-main-branch");
			if (args.body !== undefined && args.body !== "") {
				argv.push("--body", "-");
				return { result: clip(await memory(config, argv, { signal: exec.signal, stdin: args.body })) };
			}
			return { result: clip(await memory(config, argv, { signal: exec.signal })) };
		},
	}));

	ctx.tools.register(defineTool({
		name: "memory_validate",
		description: "Validate memory entries against the frontmatter schema (frontmatter fields, enums, cross-references).",
		parameters: {
			path: { type: "string", required: true, description: "Entry file or directory to validate." },
		},
		output: cliOutput,
		timeoutMs: config.timeoutMs,
		isConcurrencySafe: concurrencySafe,
		async execute(args, exec) {
			return { result: clip(await memory(config, ["validate", args.path], { signal: exec.signal })) };
		},
	}));

	ctx.tools.register(defineTool({
		name: "memory_init",
		description: "Initialize the directory structure for a new agent inside the memory repo.",
		parameters: {
			agent_id: { type: "string", required: true, description: "Identifier of the agent to initialize." },
		},
		output: cliOutput,
		timeoutMs: config.timeoutMs,
		isConcurrencySafe: () => false,
		async execute(args, exec) {
			return { result: clip(await memory(config, ["init", args.agent_id], { signal: exec.signal })) };
		},
	}));

	ctx.tools.register(defineTool({
		name: "memory_sync",
		description: "Sync the memory repo with its git remote (pull and push). Use after or before long offline stretches.",
		parameters: {
			pull_only: { type: "boolean", description: "Only pull." },
			push_only: { type: "boolean", description: "Only push." },
		},
		output: cliOutput,
		timeoutMs: config.timeoutMs,
		isConcurrencySafe: () => false,
		async execute(args, exec) {
			const argv = ["sync"];
			if (args.pull_only) argv.push("--pull-only");
			if (args.push_only) argv.push("--push-only");
			return { result: clip(await memory(config, argv, { signal: exec.signal })) };
		},
	}));

	ctx.tools.register(defineTool({
		name: "memory_clone",
		description: "Clone the configured memory repository locally for offline access.",
		parameters: {},
		output: cliOutput,
		timeoutMs: config.timeoutMs,
		isConcurrencySafe: () => false,
		async execute(_args, exec) {
			return { result: clip(await memory(config, ["clone"], { signal: exec.signal })) };
		},
	}));

	ctx.tools.register(defineTool({
		name: "memory_cache",
		description: "Manage the SQLite BM25 index cache: build, status, or clear it.",
		parameters: {
			action: { type: "string", required: true, description: "One of: build, status, clear." },
		},
		output: cliOutput,
		timeoutMs: config.timeoutMs,
		isConcurrencySafe: concurrencySafe,
		async execute(args, exec) {
			requireChoice("memory_cache", "action", args.action, ["build", "status", "clear"]);
			return { result: clip(await memory(config, ["cache", args.action], { signal: exec.signal })) };
		},
	}));

	ctx.tools.register(defineTool({
		name: "memory_log",
		description: "Inspect the CLI usage log: recent commands, durations, and error entries.",
		parameters: {
			tail: { type: "number", description: "Show only the last N entries." },
			since: { type: "string", description: "Only entries since this timestamp." },
			level: { type: "string", description: "Filter by log level." },
			command: { type: "string", description: "Filter by command name." },
			agent: { type: "string", description: "Filter by agent id." },
		},
		output: cliOutput,
		timeoutMs: config.timeoutMs,
		isConcurrencySafe: concurrencySafe,
		async execute(args, exec) {
			const argv = ["log"];
			if (args.tail !== undefined) argv.push("--tail", String(Math.trunc(args.tail)));
			if (args.since) argv.push("--since", args.since);
			if (args.level) argv.push("--level", args.level);
			if (args.command) argv.push("--command", args.command);
			if (args.agent) argv.push("--agent", args.agent);
			return { result: clip(await memory(config, argv, { signal: exec.signal })) };
		},
	}));

	ctx.systemPrompt.section({
		name: "tool:memory-rsi",
		// Sits after the web tools (2000/2100) and before TOOL_LSP (2200) in
		// the centrally owned SECTION_ORDERS ladder.
		order: 2150,
		text: () =>
			[
				"You have persistent, Git-backed memory tools (memory_ls, memory_toc, memory_section, memory_search, memory_grep, memory_new, memory_update, memory_validate, memory_init, memory_sync, memory_clone, memory_cache, memory_log).",
				"Discovery over retrieval: use memory_ls to see the tree, memory_toc for one entry's outline, memory_section for a narrow read, memory_search for BM25-ranked questions, memory_grep only for exact strings.",
				"Write durable knowledge with memory_new / memory_update; both validate frontmatter and sync to git. Keep bodies focused; one topic per entry.",
			].join(" "),
	});
}
