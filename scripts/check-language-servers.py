#!/usr/bin/env python3
"""Optional development evidence: AST parsing and real LSP document-symbol queries.

Requires pylsp and typescript-language-server on PATH. This is not a replacement
for tests or a claim that language-server symbol discovery proves correctness.
"""
import ast
import json
import queue
import shutil
import subprocess
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inspect_server(command, paths, language):
    if not shutil.which(command[0]):
        raise RuntimeError(f"Unavailable capability: {command[0]}")
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    responses = queue.Queue()

    def send(payload):
        data = json.dumps({"jsonrpc": "2.0", **payload}).encode()
        process.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        process.stdin.flush()

    def receive():
        try:
            while True:
                headers = {}
                while True:
                    line = process.stdout.readline()
                    if not line:
                        return
                    if line == b"\r\n":
                        break
                    key, value = line.decode().split(":", 1)
                    headers[key.lower()] = value.strip()
                responses.put(json.loads(process.stdout.read(int(headers["content-length"]))))
        finally:
            responses.put({"eof": True})

    threading.Thread(target=receive, daemon=True).start()

    def request(number, method, params):
        send({"id": number, "method": method, "params": params})
        while True:
            response = responses.get(timeout=30)
            if response.get("eof"):
                raise RuntimeError(f"{command[0]} exited before {method}")
            if "method" in response and "id" in response:
                answer = [] if response["method"] == "workspace/configuration" else None
                send({"id": response["id"], "result": answer})
            elif response.get("id") == number:
                if "error" in response:
                    raise RuntimeError(response["error"])
                return response.get("result")

    try:
        request(1, "initialize", {"processId": None, "rootUri": ROOT.as_uri(), "capabilities": {}})
        send({"method": "initialized", "params": {}})
        for number, path in enumerate(paths, 2):
            uri = path.as_uri()
            send({"method": "textDocument/didOpen", "params": {"textDocument": {"uri": uri, "languageId": language, "version": 1, "text": path.read_text()}}})
            symbols = request(number, "textDocument/documentSymbol", {"textDocument": {"uri": uri}})
            print(json.dumps({"server": command[0], "file": str(path.relative_to(ROOT)), "symbols": len(symbols or []), "sample": [s["name"] for s in (symbols or [])[:8]]}))
            if not symbols:
                raise RuntimeError(f"No symbols returned for {path}")
        request(1000, "shutdown", None)
        send({"method": "exit", "params": None})
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


if __name__ == "__main__":
    python_files = sorted((ROOT / "cli/src/agent_memory").glob("*.py"))
    for file in python_files:
        ast.parse(file.read_text(), filename=str(file))
    print(f"Python AST parsed: {len(python_files)} modules")
    inspect_server(["pylsp"], [ROOT / "cli/src/agent_memory/plans.py", ROOT / "cli/src/agent_memory/policy.py"], "python")
    inspect_server(["typescript-language-server", "--stdio"], [ROOT / "lib/contracts.js", ROOT / "lib/prompt.js"], "javascript")
