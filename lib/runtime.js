import { constants, existsSync } from "node:fs";
import { mkdir, open, writeFile, rm, lstat, rename } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { homedir, userInfo } from "node:os";
import { join, resolve, dirname, isAbsolute, delimiter } from "node:path";
import { fileURLToPath } from "node:url";
import { runProcess } from "./process.js";
import { GITNEXUS_VERSION, graphEnvironment as auditedGraphEnvironment } from "./gitnexus.js";
export { GITNEXUS_VERSION };
export const CLI_SOURCE = fileURLToPath(new URL("../cli", import.meta.url));
export const PACKAGE_ROOT = fileURLToPath(new URL("..", import.meta.url));
export const dshHome = () => resolve(process.env.DSH_HOME || join(homedir(), ".dsh"));
export const defaultMemoryBase = () => join(dshHome(), "memory");

export function runtimePaths(config = {}) {
	const root = resolve(config.runtimeDir || join(dshHome(), "plugins", "memory-rsi", "runtime"));
	const venv = join(root, "python");
	return {
		root, venv,
		python: join(venv, "bin", "python"),
		memory: join(venv, "bin", "memory"),
		ripgrep: join(venv, "bin", "rg"),
		graphRoot: join(root, "graph"),
		gitnexus: join(root, "graph", "node_modules", ".bin", "gitnexus"),
		graphHome: join(root, "graph-home"),
	};
}

export function runtimeConfig(config = {}) {
	const paths = runtimePaths(config);
	return {
		...config,
		runtimeDir: paths.root,
		memoryBin: (!config.memoryBin || config.memoryBin === "memory") && existsSync(paths.memory) ? paths.memory : (config.memoryBin || "memory"),
		pythonBin: (!config.pythonBin || config.pythonBin === "python3") && existsSync(paths.python) ? paths.python : (config.pythonBin || "python3"),
		gitnexusBin: (!config.gitnexusBin || config.gitnexusBin === "gitnexus") && existsSync(paths.gitnexus) ? paths.gitnexus : (config.gitnexusBin || "gitnexus"),
		gitnexusHome: config.gitnexusHome || paths.graphHome,
		instructionFiles: config.instructionFiles ?? [],
		timeoutMs: config.timeoutMs ?? 60000,
	};
}

export function localAgentId(config = {}) {
	if (config.agentId) return config.agentId;
	let name = process.env.AGENT_ID || process.env.USER;
	if (!name) { try { name = userInfo().username; } catch { name = "agent"; } }
	const id = name.toLowerCase().replace(/[^a-z0-9_-]/g, "-").replace(/^[^a-z0-9]+/, "").slice(0, 80);
	return !id || ["shared", "imports", "templates", "plans", "policies"].includes(id) ? "agent" : id;
}

async function checkDirectory(path) {
	// Validate ancestors without creating them; status must remain read-only.
	for (let part = resolve(path); ; part = dirname(part)) {
		try {
			const info = await lstat(part);
			if (info.isSymbolicLink()) throw new Error(`Managed runtime path contains a symlink: ${part}`);
			if (!info.isDirectory()) throw new Error(`Managed runtime path is not a directory: ${part}`);
		} catch (error) { if (error.code !== "ENOENT") throw error; }
		if (dirname(part) === part) break;
	}
}

/** Create a selected directory only after refusing links and special ancestors. */
export async function safeDirectory(path) {
	await checkDirectory(path);
	await mkdir(path, { recursive: true });
}

const MANIFEST_MAX_BYTES = 64 * 1024;
async function checkRegularFile(path, maxBytes = Infinity) {
	await checkDirectory(dirname(path));
	try {
		const info = await lstat(path);
		if (!info.isFile() || info.isSymbolicLink() || info.nlink !== 1 || info.size > maxBytes) {
			throw new Error(`Refusing nonregular, linked, or oversized managed runtime file: ${path}`);
		}
		return info;
	} catch (error) { if (error.code === "ENOENT") return null; throw error; }
}

export async function readManagedJSON(path, maxBytes = MANIFEST_MAX_BYTES) {
	await checkRegularFile(path, maxBytes);
	// O_NONBLOCK also protects the lstat/open gap from a swapped-in FIFO.
	const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
	try {
		const info = await handle.stat();
		if (!info.isFile() || info.nlink !== 1 || info.size > maxBytes) throw new Error(`Unsafe managed JSON file: ${path}`);
		const buffer = Buffer.alloc(maxBytes + 1);
		let size = 0;
		while (size < buffer.length) {
			const { bytesRead } = await handle.read(buffer, size, buffer.length - size, size);
			if (!bytesRead) break;
			size += bytesRead;
		}
		if (size > maxBytes) throw new Error(`Managed JSON file exceeds ${maxBytes} bytes: ${path}`);
		return JSON.parse(buffer.subarray(0, size).toString("utf8"));
	} finally { await handle.close(); }
}

export async function writeManagedJSON(path, manifest, maxBytes = MANIFEST_MAX_BYTES) {
	const text = JSON.stringify(manifest, null, 2) + "\n";
	if (Buffer.byteLength(text) > maxBytes) throw new Error(`Managed JSON exceeds ${maxBytes} bytes; narrow migration selections.`);
	await checkRegularFile(path, maxBytes);
	const temporary = join(dirname(path), `.installed-${randomUUID()}.tmp`);
	try {
		await writeFile(temporary, text, { flag: "wx", mode: 0o600 });
		await checkRegularFile(path, maxBytes);
		await rename(temporary, path);
	} finally { await rm(temporary, { force: true }); }
}

async function installLock(root) {
	const lock = join(root, ".install-lock");
	try { await mkdir(lock); }
	catch (error) { if (error.code === "EEXIST") throw new Error(`Another setup may be running: ${lock}. If it crashed, inspect owner.json and remove only this stale lock before retrying.`); throw error; }
	await writeFile(join(lock, "owner.json"), JSON.stringify({ pid: process.pid, started: new Date().toISOString() }) + "\n");
	return () => rm(lock, { recursive: true, force: true });
}

/** Explicit setup only: imports/registering tools never install software or access the network. */
export async function installRuntime(config, { graph = true, signal, runner = runProcess } = {}) {
	if (process.platform === "win32") throw new Error("This release supports POSIX systems; use WSL on Windows (Python flock/venv/bin are required).");
	const paths = runtimePaths(config);
	const timeoutMs = config.setupTimeoutMs ?? 600000;
	const python = config.bootstrapPython || "python3";
	const opts = { signal, timeoutMs, maxBytes: 4 * 1024 * 1024 };
	await runner(python, ["-c", "import sys; assert sys.version_info >= (3,10), 'Python 3.10+ required'"], opts);
	await runner("git", ["--version"], opts);
	await safeDirectory(paths.root);
	const unlock = await installLock(paths.root);
	try {
		await checkRegularFile(join(paths.root, "installed.json"), MANIFEST_MAX_BYTES);
		await safeDirectory(paths.venv);
		// Directory links can redirect venv/npm writes outside the managed tree.
		// Do not reject bin/python itself: venv legitimately links the interpreter.
		await safeDirectory(dirname(paths.python));
		await checkRegularFile(paths.memory);
		await checkRegularFile(paths.ripgrep);
		if (graph) {
			await safeDirectory(paths.graphRoot);
			await safeDirectory(paths.graphHome);
			await safeDirectory(dirname(paths.gitnexus));
		}
		await runner(python, ["-m", "venv", paths.venv], opts);
		await runner(paths.python, ["-m", "pip", "install", "--disable-pip-version-check", "--upgrade", CLI_SOURCE], opts);
		await runner(paths.python, ["-m", "pip", "install", "--disable-pip-version-check", "--only-binary=:all:", "ripgrep==14.1.0"], opts);
		await runner(paths.ripgrep, ["--version"], opts);
		const cli = await runner(paths.memory, ["--version"], opts);
		await runner(paths.memory, ["bootstrap", "--help"], opts);
		let graphVersion = null;
		if (graph) {
			await safeDirectory(paths.graphRoot);
			await safeDirectory(paths.graphHome);
			// This private prefix never touches global npm state or project dependencies.
			await runner(config.npmBin || "npm", ["install", "--prefix", paths.graphRoot, "--no-audit", "--no-fund", "--save-exact", `gitnexus@${GITNEXUS_VERSION}`], opts);
			const version = await runner(paths.gitnexus, ["--version"], { ...opts, env: graphEnvironment(paths.graphHome) });
			graphVersion = version.stdout.trim();
			if (graphVersion !== GITNEXUS_VERSION) throw new Error(`Expected graph-only audited GitNexus ${GITNEXUS_VERSION}, got ${graphVersion}`);
		}
		const manifest = { schema: 1, cliVersion: cli.stdout.trim(), graphVersion, installedAt: new Date().toISOString() };
		await writeManagedJSON(join(paths.root, "installed.json"), manifest);
		// The owning plugin's tools close over this config, so successful installation
		// activates backend commands in the current process without a module reload.
		Object.assign(config, { memoryBin: paths.memory, pythonBin: paths.python, ...(graph ? { gitnexusBin: paths.gitnexus, gitnexusHome: paths.graphHome } : {}) });
		return { installed: true, ...manifest, memoryBin: paths.memory, ...(graph ? { gitnexusBin: paths.gitnexus } : {}), next: "initialize memory, then verify status; import/migration is never automatic" };
	} finally { await unlock(); }
}

export function graphEnvironment(home) {
	return auditedGraphEnvironment({ gitnexusHome: home });
}

export async function backendStatus(config, { signal, runner = runProcess } = {}) {
	const checks = [];
	for (const [name, command, args] of [
		["python", config.pythonBin || config.bootstrapPython || "python3", ["--version"]],
		["git", "git", ["--version"]],
		["ripgrep", "rg", ["--version"]],
		["memory", config.memoryBin || "memory", ["bootstrap", "--help"]],
	]) {
		try {
			const env = { ...process.env, PATH: isAbsolute(config.memoryBin || "") ? `${dirname(config.memoryBin)}${delimiter}${process.env.PATH || ""}` : process.env.PATH };
			const options = { signal, timeoutMs: 10000, env };
			let result;
			try { result = await runner(command, args, options); }
			catch (error) {
				if (name !== "memory" || error.code !== "ENOENT") throw error;
				result = await runner(config.pythonBin || "python3", ["-m", "agent_memory", ...args], options);
			}
			checks.push({ name, available: true, detail: result.stdout.trim().split("\n")[0] });
		} catch (error) {
			if (signal?.aborted) throw error;
			checks.push({ name, available: false, detail: error.message, remedy: name === "memory" ? "Run memory_setup action install, then initialize." : `Install ${name} and ensure it is on PATH.` });
		}
	}
	let installation;
	try { installation = await readManagedJSON(join(runtimePaths(config).root, "installed.json")); }
	catch (error) { if (error.code !== "ENOENT") installation = { error: error.message }; }
	return { checks, installation: installation ?? null, backendReady: checks.find(check => check.name === "memory").available };
}
