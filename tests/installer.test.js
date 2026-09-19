import test from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, symlinkSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";

const source = readFileSync(new URL("../scripts/install.sh", import.meta.url), "utf8");
const install = source.slice(source.indexOf("install_cli_venv()"), source.indexOf("command -v memory >/dev/null 2>&1 || warn"));

for (const mode of ["venv", "fallback"]) {
	test(`installer upgrades existing CLI and supports ${mode}`, t => {
		const root = mkdtempSync(join(tmpdir(), "mrsi-install-"));
		t.after(() => rmSync(root, { recursive: true, force: true }));
		const bin = join(root, "bin");
		mkdirSync(bin);
		for (const cmd of ["mkdir", "ln"]) symlinkSync(`/bin/${cmd}`, join(bin, cmd));
		writeFileSync(join(bin, "memory"), "#!/bin/sh\nexit 0\n", { mode: 0o755 });
		writeFileSync(join(bin, "python3"), `#!/bin/sh
printf '%s\\n' "$*" >> "$HOME/calls"
if [ "$1" = "-c" ]; then [ "$TEST_MODE" = "venv" ]; exit $?; fi
if [ "$2" = "pip" ]; then
  if [ "$3" = "--version" ]; then exit 0; fi
  [ "$TEST_MODE" = "venv" ]; exit $?
fi
if [ "$2" = "venv" ]; then
  /bin/mkdir -p "$3/bin"
  printf '#!/bin/sh\\nprintf "fallback pip\\\\n" >> "$HOME/calls"\\n' > "$3/bin/pip"
  /bin/chmod +x "$3/bin/pip"
  exit 0
fi
exit 1
`, { mode: 0o755 });
		const result = spawnSync("/bin/sh", ["-c", `set -eu\nrepo_root=/source\nsay() { :; }\nwarn() { :; }\n${install}`], {
			encoding: "utf8", env: { ...process.env, HOME: root, PATH: bin, TEST_MODE: mode }, timeout: 10000,
		});
		assert.equal(result.status, 0, result.stderr);
		const calls = readFileSync(join(root, "calls"), "utf8");
		if (mode === "venv") {
			assert.match(calls, /-m pip install --upgrade \/source\/cli/);
			assert.doesNotMatch(calls, /--user/);
		} else {
			assert.match(calls, /--user --upgrade/);
			assert.match(calls, /fallback pip/);
		}
	});
}

test("installer refresh preserves instruction allowlist and quotes config values", t => {
	const root = mkdtempSync(join(tmpdir(), "mrsi-install-config-"));
	t.after(() => rmSync(root, { recursive: true, force: true }));
	const path = join(root, "patch.yml");
	const begin = "# >>> memory-rsi installer (managed; safe to delete) >>>";
	const end = "# <<< memory-rsi installer <<<";
	writeFileSync(path, `${begin}\n- id: memory-rsi\n  config:\n    base: old\n    agentId: old\n    instructionFiles: [\"/project/AGENTS.md\"]\n${end}\n`);
	const python = source.split("<<'PY'\n").map(s => s.split("\nPY")[0]).find(s => s.startsWith("import json, re, sys"));
	assert.ok(python);
	const result = spawnSync("python3", ["-", path, begin, end, 'team"quoted', "/new/base"], { input: python, encoding: "utf8" });
	assert.equal(result.status, 0, result.stderr);
	const updated = readFileSync(path, "utf8");
	assert.match(updated, /instructionFiles: \["\/project\/AGENTS.md"\]/);
	assert.ok(updated.includes('agentId: "team\\"quoted"'));
	assert.ok(updated.includes('base: "/new/base"'));
});
