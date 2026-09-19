import { defineTool } from "@deepseek-ai/dsh-tools";

const PLAN_ACTIONS = ["templates", "review", "save_template", "create", "read", "update", "validate"];
const POLICY_ACTIONS = ["read", "update", "history", "rollback", "sync"];
const READ_ACTIONS = new Set(["templates", "review", "read", "validate", "history"]);

/** Keep structured contracts in JSON stdin, never interpolate them into shell commands. */
export function parseRequest(action, request, choices) {
	if (!choices.includes(action)) throw new Error(`action must be one of ${choices.join(", ")}`);
	let fields;
	try { fields = JSON.parse(request ?? "{}"); }
	catch { throw new Error("request must be a JSON object string"); }
	if (!fields || Array.isArray(fields) || typeof fields !== "object") {
		throw new Error("request must be a JSON object string");
	}
	if (Object.hasOwn(fields, "action")) throw new Error("Pass action separately, not inside request");
	return { ...fields, action };
}

export function registerContracts(ctx, config, { memory, clip, output, rsi }) {
	for (const [command, actions, description, requestDescription] of [
		["plan", PLAN_ACTIONS,
			"Rewards > gates: discover and review templates, then write a durable shared task contract. Earn achievements with evidence; parent and subagents use the same plan ID, revision, and work items. Review returns the template and creation guidance. Updates are revision-checked and scoped; templates evolve separately from pinned plans.",
			'JSON object excluding action. Review: {"template_id":"coding"}. Create: {"plan_id":"task-id","template_id":"coding","template_revision":"<review revision>","task":{...}}. Read/validate: {"plan_id":"task-id"}. Update includes plan_id, revision, work_item_id and evidence/reviews/status. Start with templates then review for full schema.'],
		["policy", POLICY_ACTIONS,
			"Read or explicitly revise the persistent agent policy used by DSH prompt assembly. Inspect history or roll back; preview and sync a managed policy section into operator-allowlisted AGENTS-like files without replacing human content. Policy supplements, never overrides, platform permissions or higher-priority instructions. Shared policy changes require explicit review; actor labels are audit metadata, not proof of approval.",
			'JSON object excluding action. Update: {"body":"...","expected_revision":"<read revision>","actor":"agent","reason":"..."}. Rollback also specifies target revision. Sync: {"target":"<configured path>"} previews; apply:true requires expected_revision, expected_target_revision, actor and reason. Read returns usage guidance.'],
	]) {
		ctx.tools.register(defineTool({
			name: `memory_${command}`,
			description,
			parameters: {
				action: { type: "string", required: true, description: `One of: ${actions.join(", ")}.` },
				request: { type: "string", description: requestDescription },
				no_git: { type: "boolean", description: "Skip memory Git commit/sync; saved files remain durable locally." },
				allow_non_main_branch: { type: "boolean", description: "Explicitly allow a memory commit on a non-default branch." },
			},
			output,
			timeoutMs: (config.timeoutMs ?? 60000) * 3 + (config.typesafeTimeoutMs ?? 20000),
			isConcurrencySafe: (args) => READ_ACTIONS.has(args.action),
			async execute(args, exec) {
				parseRequest(args.action, args.request, actions);
				// Preserve raw object members so Python can reject duplicate keys at
				// every nesting level rather than silently accepting JSON.parse's last value.
				const members = (args.request ?? "{}").trim().slice(1, -1).trim();
				const stdin = `{\"action\":${JSON.stringify(args.action)}${members ? "," + members : ""}}`;
				const argv = [command, "--request", "-"];
				if (args.no_git) argv.push("--no-git");
				if (args.allow_non_main_branch) argv.push("--allow-non-main-branch");
				if (command === "policy") {
					for (const file of config.instructionFiles ?? []) argv.push("--instruction-file", file);
				}
				const callerConfig = exec.agent?.id ? { ...config, agentId: exec.agent.id } : config;
				const result = await memory(callerConfig, argv, { stdin, signal: exec.signal });
				if (command !== "plan" || args.action !== "create" || !rsi) return { result: clip(result) };
				const saved = JSON.parse(result);
				try {
					saved.policy_preflight = await rsi.preflight(saved.plan.plan_id, args, exec, saved.revision);
				} catch {
					// The contract is already durable. Never conceal it behind an assessor,
					// stale-snapshot, cancellation, or artifact persistence failure.
					saved.policy_preflight = { status: "unavailable", reason: "Plan saved; policy preflight could not be attached. Read the current plan and retry memory_rsi preflight before relying on an assessment.", permission_effect: "none" };
				}
				return { result: clip(JSON.stringify(saved)) };
			},
		}));
	}
}
