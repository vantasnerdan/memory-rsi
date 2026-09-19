#!/usr/bin/env node
/** Explicit portable installer; plugin import itself remains side-effect free. */
import { parseArgs } from "node:util";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";
import { dshHome, defaultMemoryBase, runtimeConfig, localAgentId, installRuntime, safeDirectory, readManagedJSON, writeManagedJSON } from "../lib/runtime.js";
import { readManagedSettings, configureProfile, profileDirectory, preflightProfile } from "../lib/profile.js";
import { bootstrapRequest, setupStatus } from "../lib/setup.js";

const help = `memory-rsi-setup — portable, graph-only agent bootstrap

Run after adding the plugin: memory-rsi-setup --profile web --yes

Options:
  --profile NAME          DSH profile (default web)
  --base PATH             Memory root (existing managed setting or DSH_HOME/memory)
  --agent-id ID           Local lowercase identity (existing setting or current username)
  --python COMMAND        Python 3.10+ with venv (default python3)
  --runtime-dir PATH      Private dependencies (default DSH_HOME/plugins/memory-rsi/runtime)
  --instruction-file PATH Managed policy target; repeat (default DSH_HOME/AGENTS.md)
  --no-instructions       Do not sync instruction files in this invocation
  --no-gitnexus           Explicit memory-only setup; report graph not provisioned
  --no-git                File-only initialization, without a memory Git repository
  --no-profile            Do not modify/install a DSH profile (backend-only deployment)
  --status                Read-only readiness report; no installations or initialization
  --codex-home PATH       Prepare a migration preview, never silently import it
  --memory-dir PATH       Intentional Codex memory source; repeat (Markdown only)
  --apply-migration FILE  Apply a previously reviewed preview JSON; does not reinstall
  --yes                  Unattended setup; explicit consent to install local dependencies
  --help                 Show this help

Package import never installs software. GitNexus is pinned and graph-only; embeddings
are never generated. Migration never copies credentials, sessions or provider config.
`;

export async function main(argv = process.argv.slice(2)) {
	const { values } = parseArgs({ args: argv, options: {
		profile: { type: "string", default: "web" }, base: { type: "string" }, "agent-id": { type: "string" }, python: { type: "string" },
		"runtime-dir": { type: "string" }, "instruction-file": { type: "string", multiple: true },
		"no-instructions": { type: "boolean" }, "no-gitnexus": { type: "boolean" }, "no-git": { type: "boolean" }, "no-profile": { type: "boolean" },
		status: { type: "boolean" }, "codex-home": { type: "string" }, "memory-dir": { type: "string", multiple: true },
		"apply-migration": { type: "string" }, yes: { type: "boolean", short: "y" }, help: { type: "boolean", short: "h" },
	} });
	if (values.help) { console.log(help); return; }
	profileDirectory(values.profile); // Validate before provisioning anything.
	const previous = values["no-profile"] ? {} : await readManagedSettings(values.profile);
	const selected = {
		...previous,
		base: resolve(values.base || previous.base || process.env.AGENT_MEMORY_PATH || defaultMemoryBase()),
		agentId: values["agent-id"] || previous.agentId || localAgentId(),
		runtimeDir: values["runtime-dir"] ? resolve(values["runtime-dir"]) : previous.runtimeDir,
		bootstrapPython: values.python || previous.bootstrapPython || "python3",
		instructionFiles: values["instruction-file"]?.map(path => resolve(path)) ?? previous.instructionFiles ?? [join(dshHome(), "AGENTS.md")],
	};
	const config = runtimeConfig(selected);
	if (values.status) { console.log(JSON.stringify(await setupStatus(config, { noGit: values["no-git"] }), null, 2)); return; }
	if (values["apply-migration"]) {
		const saved = await readManagedJSON(resolve(values["apply-migration"]), 2 * 1024 * 1024);
		const applied = await bootstrapRequest(config, { action: "apply_migration", preview: saved.preview, expected_revision: saved.revision }, { noGit: values["no-git"] });
		console.log(JSON.stringify(applied, null, 2)); return;
	}
	if (!values.yes) throw new Error("Review --help and rerun with --yes to explicitly install private dependencies and initialize selected memory. Use --status for a read-only report.");
	if (values["memory-dir"]?.length && !values["codex-home"]) throw new Error("--memory-dir requires an explicit --codex-home; no files were imported.");
	if (!values["no-profile"]) await preflightProfile(values.profile);
	console.error("Installing private runtime dependencies (no global pip/npm changes; no embeddings)…");
	const installed = await installRuntime(config, { graph: !values["no-gitnexus"] });
	const initialized = await bootstrapRequest(config, { action: "initialize" }, { noGit: values["no-git"] });
	if (!initialized.ready) throw new Error(`Memory initialization is incomplete; existing content was preserved. ${JSON.stringify(initialized)}`);
	const instructions = [];
	if (!values["no-instructions"]) {
		for (const target of config.instructionFiles) {
			await safeDirectory(resolve(target, ".."));
			const preview = await bootstrapRequest(config, { action: "sync_instructions", target });
			// --yes authorizes synchronization of this known canonical policy, not
			// activation of any imported Codex instructions or arbitrary source files.
			console.error(preview.diff || `Managed instructions unchanged: ${target}`);
			instructions.push(await bootstrapRequest(config, {
				action: "sync_instructions", target, apply: true,
				expected_revision: preview.expected_revision, expected_target_revision: preview.expected_target_revision,
				actor: "installer", reason: "Explicit setup --yes requested synchronization of the existing canonical policy, preserving unmanaged instructions; this actor label is provenance, not proof of human approval.",
			}));
		}
	}
	const profile = values["no-profile"] ? null : await configureProfile(values.profile, config);
	let migration;
	if (values["codex-home"]) {
		migration = await bootstrapRequest(config, { action: "preview_migration", source_home: resolve(values["codex-home"]), memory_dirs: (values["memory-dir"] || []).map(path => resolve(path)) });
		const previewPath = join(config.runtimeDir, "codex-migration-preview.json");
		await writeManagedJSON(previewPath, migration, 2 * 1024 * 1024);
		migration = { previewPath, can_apply: migration.can_apply, instruction: "Inspect this preview, then rerun with --apply-migration FILE and the same --base. Nothing has been imported." };
	}
	const status = await setupStatus(config, { noGit: values["no-git"] });
	const result = { installed, initialized, instructionTargets: instructions.map(item => ({ target: item.target, changed: item.changed })), profile, migration, status };
	console.log(JSON.stringify(result, null, 2));
	if (!values["no-gitnexus"] && !status.ready) throw new Error("Setup completed some steps but readiness is incomplete; inspect the status remedies above. No claim of ready was made.");
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
	main().catch(error => { console.error(`memory-rsi setup failed: ${error.message}`); process.exitCode = 1; });
}
