/** Cooperative preflight for native index I/O, not a filesystem sandbox.
 * Reject existing links and special files without following them. Paths can still
 * change after validation; the owner must not concurrently replace index/config files.
 */
import { lstat, opendir, readFile } from "node:fs/promises";
import { join } from "node:path";

async function optionalInfo(path) {
	try { return await lstat(path); }
	catch (error) { if (error.code === "ENOENT") return undefined; throw error; }
}

export async function checkGraphFile(path) {
	const info = await optionalInfo(path);
	if (!info) return undefined;
	if (info.isSymbolicLink() || !info.isFile() || info.nlink !== 1) {
		throw new Error(`Unsafe GitNexus path ${path}: expected a regular file with no symlink or hardlink.`);
	}
	return info;
}

/** Native status walks to ancestor indexes when metadata is absent/invalid. */
export async function hasGraphMetadata(cwd) {
	const path = join(cwd, ".gitnexus", "meta.json");
	const info = await checkGraphFile(path);
	if (!info) return false;
	if (info.size > 16 * 1024 * 1024) throw new Error("GitNexus metadata exceeds the safety read limit (16 MiB).");
	let metadata;
	try { metadata = JSON.parse(await readFile(path, "utf8")); }
	catch { throw new Error("Invalid GitNexus metadata; refusing ancestor-index discovery. Reanalyze this repository."); }
	if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
		throw new Error("Invalid GitNexus metadata object; refusing ancestor-index discovery. Reanalyze this repository.");
	}
	return true;
}

export async function checkGraphPaths(cwd) {
	await checkGraphFile(join(cwd, ".gitnexusrc"));
	const root = join(cwd, ".gitnexus");
	const info = await optionalInfo(root);
	if (!info) return;
	if (info.isSymbolicLink() || !info.isDirectory()) {
		throw new Error(`Unsafe GitNexus path ${root}: expected a directory, not a symlink or special file.`);
	}
	const pending = [{ path: root, depth: 0 }];
	let seen = 0;
	while (pending.length) {
		const { path, depth } = pending.pop();
		if (depth > 64) throw new Error("GitNexus index path nesting exceeds the safety limit (64).");
		// lstat every entry, rather than trusting directory-entry type hints.
		for await (const entry of await opendir(path)) {
			if (++seen > 100_000) throw new Error("GitNexus index exceeds the safety scan limit (100000 paths).");
			const child = join(path, entry.name);
			const childInfo = await lstat(child);
			if (childInfo.isSymbolicLink()) throw new Error(`Unsafe GitNexus symlink: ${child}`);
			if (childInfo.isDirectory()) {
				// LadybugDB is a single native file; sidecars and metadata are files too.
				if (depth === 0 && (/^lbug(?:[.-].*)?$/.test(entry.name) || ["meta.json", "meta.json.tmp", "run.cjs", ".gitignore"].includes(entry.name))) {
					throw new Error(`Unsafe GitNexus path ${child}: expected a regular file, not a directory.`);
				}
				pending.push({ path: child, depth: depth + 1 });
			} else if (!childInfo.isFile() || childInfo.nlink !== 1) {
				throw new Error(`Unsafe GitNexus path ${child}: expected a regular file with no hardlink or special file.`);
			}
		}
	}
}
