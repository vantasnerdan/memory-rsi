import { spawn } from "node:child_process";

/** Bounded, abortable argv execution. No shell and no orphaned installer children. */
export function runProcess(command, args, { cwd, env = process.env, stdin, signal, timeoutMs = 60000, maxBytes = 2 * 1024 * 1024 } = {}) {
	return new Promise((resolve, reject) => {
		if (signal?.aborted) { reject(signal.reason ?? new Error("Operation cancelled")); return; }
		const child = spawn(command, args, { cwd, env, detached: process.platform !== "win32", stdio: "pipe" });
		let stdout = "", stderr = "", size = 0, failure, forceTimer;
		const kill = sig => {
			try { if (process.platform === "win32") child.kill(sig); else if (child.pid) process.kill(-child.pid, sig); } catch (error) { if (error.code !== "ESRCH") child.kill(sig); }
		};
		const stop = reason => {
			if (failure) return;
			failure = reason;
			kill("SIGTERM");
			forceTimer = setTimeout(() => kill("SIGKILL"), 500);
		};
		const timer = setTimeout(() => stop(new Error(`${command} timed out after ${timeoutMs}ms`)), timeoutMs);
		const abort = () => stop(new Error(`${command} cancelled`));
		signal?.addEventListener("abort", abort, { once: true });
		const collect = (key, chunk) => {
			size += Buffer.byteLength(chunk);
			if (size > maxBytes) { stop(new Error(`${command} exceeded output limit (${maxBytes} bytes)`)); return; }
			if (key === "stdout") stdout += chunk.toString(); else stderr += chunk.toString();
		};
		child.stdout.setEncoding("utf8");
		child.stderr.setEncoding("utf8");
		child.stdout.on("data", chunk => collect("stdout", chunk));
		child.stderr.on("data", chunk => collect("stderr", chunk));
		const cleanup = () => { clearTimeout(timer); clearTimeout(forceTimer); signal?.removeEventListener("abort", abort); };
		child.on("error", error => { cleanup(); reject(error); });
		child.stdin.on("error", error => { if (error.code !== "EPIPE") stop(error); });
		child.on("close", (code, terminationSignal) => {
			if (failure) kill("SIGKILL"); // Parent may exit while a detached descendant ignores SIGTERM.
			cleanup();
			if (failure) reject(failure);
			else if (code !== 0) reject(new Error(`${command} exited ${code ?? terminationSignal}: ${(stderr || stdout).trim().slice(-12000)}`));
			else resolve({ stdout, stderr, code });
		});
		child.stdin.end(stdin);
	});
}
