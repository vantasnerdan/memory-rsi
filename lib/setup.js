import { defineTool } from "@deepseek-ai/dsh-tools";
import { dirname, delimiter } from "node:path";
import { isAbsolute } from "node:path";
import { backendStatus, installRuntime, localAgentId, safeDirectory } from "./runtime.js";
import { runProcess } from "./process.js";
import { gitnexusStatus } from "./gitnexus.js";
import { setupFields } from "./setup-json.js";

const ACTIONS = ["status", "install", "initialize", "preview_migration", "apply_migration", "sync_instructions"];

export function memoryEnvironment(config) {
	return {
		...process.env,
		PYTHONUNBUFFERED: "1",
		AGENT_ID: localAgentId(config),
		PATH: isAbsolute(config.memoryBin) ? `${dirname(config.memoryBin)}${delimiter}${process.env.PATH || ""}` : process.env.PATH,
	};
}

export async function bootstrapRequest(config, request, { signal, noGit = false } = {}) {
	const argv = ["bootstrap", "--request", "-", "--base", config.base, "--agent-id", localAgentId(config)];
	for (const file of config.instructionFiles ?? []) argv.push("--instruction-file", file);
	if (noGit) argv.push("--no-git");
	const options = { stdin: JSON.stringify(request), env: memoryEnvironment(config), signal, timeoutMs: config.timeoutMs ?? 60000 };
	let result;
	try { result = await runProcess(config.memoryBin || "memory", argv, options); }
	catch (error) {
		if (error.code !== "ENOENT") throw error;
		result = await runProcess(config.pythonBin || "python3", ["-m", "agent_memory", ...argv], options);
	}
	return JSON.parse(result.stdout);
}

export async function setupStatus(config, { signal, noGit = false } = {}) {
	const backend = await backendStatus(config, { signal });
	const graph = await gitnexusStatus(config, { signal });
	let memory = null;
	if (backend.backendReady) {
		try { memory = await bootstrapRequest(config, { action: "status" }, { signal, noGit }); }
		catch (error) { memory = { ready: false, error: error.message }; }
	}
	return {
		base: config.base, runtimeDir: config.runtimeDir, instructionFiles: config.instructionFiles ?? [],
		backend, memory, graph,
		ready: Boolean(backend.backendReady && !backend.installation?.error && backend.checks.every(check => check.available) && memory?.ready && graph.supported),
		guidance: "Readiness covers installed tooling and memory initialization. Analyze each chosen project separately using the graph-only gitnexus tool. Codex migration is opt-in: preview, inspect, then explicitly apply. No embeddings are ever generated.",
	};
}

/** Explicit installer synchronization; imported instructions are never promoted. */
export async function syncSetupInstructions(config, { signal, report = () => {} } = {}) {
	const results = [];
	for (const target of config.instructionFiles ?? []) {
		await safeDirectory(dirname(target));
		const preview = await bootstrapRequest(config, { action: "sync_instructions", target }, { signal });
		report(preview.diff || `Managed instructions unchanged: ${target}`);
		results.push(await bootstrapRequest(config, {
			action: "sync_instructions", target, apply: true,
			expected_revision: preview.expected_revision, expected_target_revision: preview.expected_target_revision,
			actor: "agent", reason: "Explicit setup requested automated synchronization of the existing canonical policy, preserving unmanaged instructions; this actor label is provenance, not proof of human approval.",
		}, { signal }));
	}
	return results;
}

export function registerSetup(ctx, config) {
	ctx.tools.register(defineTool({
		name: "memory_setup",
		description: "Bootstrap new or Codex-migrating agents. Status is read-only. Install explicitly provisions a private Python CLI/ripgrep and pinned graph-only GitNexus runtime; initialize seeds missing templates/policy without overwriting user content. Migration previews selected Markdown only, then applies a revision-checked copy; credentials/history are never imported. Sync instructions only to configured targets.",
		parameters: {
			action: { type: "string", required: true, description: `One of: ${ACTIONS.join(", ")}. Start with status.` },
			request: { type: "string", description: "JSON object excluding action. Install: {graph:true} (default). Initialize: {}. preview_migration: {source_home:'/chosen/codex',memory_dirs:[],import_id:'codex'}. apply_migration: {preview:<full preview>,expected_revision:'<revision>'}. sync_instructions mirrors memory_policy sync fields. No implicit source discovery or credential import." },
			no_git: { type: "boolean", description: "Initialize files without Git; existing repositories are never auto-pushed." },
		},
		output: { schema: { type: "object", additionalProperties: false, properties: { result: { type: "string", required: true } } }, render: (_args, value) => [{ type: "text", text: value.result }] },
		timeoutMs: config.setupTimeoutMs ?? 600000,
		isConcurrencySafe: args => args.action === "status",
		async execute(args, exec) {
			if (!ACTIONS.includes(args.action)) throw new Error(`Unknown setup action: ${args.action}`);
			const fields = setupFields(args.request || "{}");
			let result;
			if (args.action === "status") {
				if (Object.keys(fields).length) throw new Error("status takes no request fields");
				result = await setupStatus(config, { signal: exec.signal, noGit: args.no_git === true });
			} else if (args.action === "install") {
				if (Object.keys(fields).some(key => key !== "graph") || (fields.graph !== undefined && typeof fields.graph !== "boolean")) throw new Error("install accepts only optional graph:boolean");
				result = await installRuntime(config, { graph: fields.graph !== false, signal: exec.signal });
			} else {
				result = await bootstrapRequest(config, { ...fields, action: args.action }, { signal: exec.signal, noGit: args.no_git === true });
			}
			return { result: JSON.stringify(result) };
		},
	}));
}
