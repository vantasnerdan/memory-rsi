import { openSync, readSync, closeSync, realpathSync, fstatSync, constants } from "node:fs";
import { spawnSync } from "node:child_process";
import { join, resolve, relative, isAbsolute } from "node:path";
import { fileURLToPath } from "node:url";

const MAX_POLICY_BYTES = 131072;
const MAX_POLICY_CHARS = 32768;
const DEFAULT_POLICY = fileURLToPath(new URL("../cli/src/agent_memory/default_policy.md", import.meta.url));

/** Resolve once at mount using the CLI's authoritative config precedence.
 * All tools and prompt reads share this absolute base until plugin reload.
 */
export function resolveMemoryConfig(config) {
	let base = config.base || process.env.AGENT_MEMORY_PATH;
	if (!base) {
		const args = ["--json-output", "config", "show"];
		const options = { encoding: "utf8", timeout: Math.min(config.timeoutMs ?? 60000, 10000), maxBuffer: 1024 * 1024 };
		let result = spawnSync(config.memoryBin || "memory", args, options);
		if (result.error?.code === "ENOENT") result = spawnSync(config.pythonBin || "python3", ["-m", "agent_memory", ...args], options);
		if (result.error || result.status !== 0) throw new Error(`Cannot resolve memory base: ${result.error?.message || result.stderr || result.stdout}`);
		base = JSON.parse(result.stdout).base_path?.value;
		if (typeof base !== "string" || !base.trim()) throw new Error("CLI config show did not return a memory base");
	}
	return { ...config, base: resolve(base) };
}

function boundedRead(path) {
	const fd = openSync(path, constants.O_RDONLY | constants.O_NONBLOCK | constants.O_NOFOLLOW);
	try {
		if (!fstatSync(fd).isFile()) throw new Error("policy is not a regular file");
		const buffer = Buffer.alloc(MAX_POLICY_BYTES + 1);
		let length = 0;
		while (length < buffer.length) {
			const count = readSync(fd, buffer, length, buffer.length - length, null);
			if (!count) break;
			length += count;
		}
		const text = new TextDecoder("utf-8", { fatal: true }).decode(buffer.subarray(0, length));
		// Count Unicode code points, matching the Python policy writer.
		// Never inject a truncated instruction whose meaning could change.
		if (length > MAX_POLICY_BYTES || [...text].length > MAX_POLICY_CHARS) throw new Error("policy exceeds 32768 characters or 131072 bytes");
		if (!text.trim()) throw new Error("policy is empty");
		return text;
	} finally { closeSync(fd); }
}

export function policyText(config) {
	const base = config.base || process.env.AGENT_MEMORY_PATH;
	if (base) {
		try {
			const root = realpathSync(resolve(base));
			const policy = realpathSync(join(root, "shared", "policies", "agent-policy.md"));
			const rel = relative(root, policy);
			if (rel.startsWith("..") || isAbsolute(rel)) throw new Error("policy path escapes memory base");
			return boundedRead(policy);
		} catch (error) {
			if (error.code !== "ENOENT") {
				return `Editable memory policy could not be loaded: ${error.message}. Read and repair it with memory_policy; do not assume it was applied.\n\n${boundedRead(DEFAULT_POLICY)}`;
			}
		}
	}
	return boundedRead(DEFAULT_POLICY);
}

export function memoryPrompt(config) {
	return [
		"Rewards > gates. Memory plans are shared execution contracts: make excellent behavior attractive through evidence-backed achievements, not activity counts or self-awarded completion.",
		"Use memory_plan templates → review → create for durable, nontrivial work. Read the full selected template, fill it for the actual task, and retain its revision. Parent and subagents read the same plan and pass its ID, latest revision, and assigned work-item IDs on every delegation. Re-read after revision conflicts; contribute scoped evidence, not competing plans.",
		"memory_policy reads and explicitly updates the versioned instruction source, reviews history/rollbacks, and previews/syncs managed AGENTS-like sections only to operator-configured paths. Policy is re-read at each prompt assembly; no tool rewrites prior messages. User-authored memory policy supplements rather than overrides higher-priority instructions, safety rules, permissions, or approvals. A recorded actor label is not human approval. Seek human review before weakening shared requirements.",
		"Use memory_plan for contract files rather than generic memory_update, which is intended for ordinary entries. Template improvements are explicit and never silently change an active plan's pinned requirements.",
		"Discovery over retrieval: memory_ls → memory_toc → memory_section/search. Use memory_grep for exact strings. Write focused durable knowledge with memory_new/memory_update; memory_validate checks ordinary entries, memory_plan validate checks contracts. memory_sync, memory_clone, memory_init, memory_cache and memory_log maintain the repository.",
		"## Editable memory policy\n" + policyText(config),
	].join("\n\n");
}
