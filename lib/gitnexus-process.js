/** Bounded, cancellable subprocess transport; no shell or automatic installation. */
import { spawn } from "node:child_process";

export const GRAPH_OUTPUT_BYTES = 64 * 1024;

export function runGraphProcess(command, argv, { cwd, env, signal, timeoutMs = 120_000 } = {}) {
	if (signal?.aborted) return Promise.reject(new Error("GitNexus cancelled before startup"));
	return new Promise((resolve, reject) => {
		const child = spawn(command, argv, {
			cwd, env, shell: false, windowsHide: true,
			detached: process.platform !== "win32", stdio: ["ignore", "pipe", "pipe"],
		});
		let stdout = Buffer.alloc(0), stderr = Buffer.alloc(0), truncated = false;
		let reason, killTimer;
		const append = (current, chunk, cap) => {
			const room = Math.max(0, cap - current.length);
			if (chunk.length > room) truncated = true;
			return room ? Buffer.concat([current, chunk.subarray(0, room)]) : current;
		};
		child.stdout.on("data", (chunk) => { stdout = append(stdout, chunk, GRAPH_OUTPUT_BYTES * 3 / 4); });
		child.stderr.on("data", (chunk) => { stderr = append(stderr, chunk, GRAPH_OUTPUT_BYTES / 4); });
		const kill = (sig) => {
			try {
				if (process.platform !== "win32" && child.pid) process.kill(-child.pid, sig);
				else child.kill(sig);
			} catch (error) { if (error.code !== "ESRCH") child.kill(sig); }
		};
		const stop = (message) => {
			if (reason) return;
			reason = message;
			kill("SIGTERM");
			killTimer = setTimeout(() => kill("SIGKILL"), 250);
		};
		const timer = setTimeout(() => stop(`GitNexus timed out after ${timeoutMs}ms`), timeoutMs);
		const abort = () => stop("GitNexus cancelled");
		signal?.addEventListener("abort", abort, { once: true });
		if (signal?.aborted) abort();
		const cleanup = () => {
			clearTimeout(timer);
			clearTimeout(killTimer);
			signal?.removeEventListener("abort", abort);
		};
		child.on("error", (error) => { cleanup(); reject(error); });
		child.on("close", (code, exitSignal) => {
			// Kill surviving descendants on cancellation even if their parent exited first.
			if (reason) kill("SIGKILL");
			cleanup();
			const result = { stdout: stdout.toString("utf8"), stderr: stderr.toString("utf8"), truncated };
			if (reason) return reject(new Error(reason));
			if (code !== 0) return reject(new Error(`GitNexus failed (${exitSignal || `exit ${code}`}): ${result.stderr || result.stdout}`));
			resolve(result);
		});
	});
}
