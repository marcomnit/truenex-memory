"""Verifica end-to-end: una nuova sessione Claude (root config) vede le memorie?"""

import json
import subprocess

# Comando IDENTICO a quello della config MCP radice di Claude Code (.claude.json)
CMD = [
    r"C:\Users\marco\AppData\Roaming\Python\Python313\Scripts\truenex-mem.exe",
    "mcp",
    "--project-root",
    r"C:\Users\marco",
]

requests = [
    {
        "jsonrpc": "2.0",
        "id": "init",
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "kimi-verify", "version": "1"},
        },
    },
    {
        "jsonrpc": "2.0",
        "id": "search",
        "method": "tools/call",
        "params": {
            "name": "memory_search",
            "arguments": {
                "query": "un backup che non hai mai provato a ripristinare non è un backup è un auspicio",
                "top_k": 5,
            },
        },
    },
]

process = subprocess.run(
    CMD,
    input="\n".join(json.dumps(r) for r in requests) + "\n",
    capture_output=True,
    text=True,
    timeout=120,
)

if process.returncode != 0:
    print("STDERR:", process.stderr[-2000:])
    raise SystemExit(f"server exited with {process.returncode}")

by_id = {}
for line in process.stdout.splitlines():
    line = line.strip()
    if line.startswith("{"):
        resp = json.loads(line)
        if "id" in resp:
            by_id[resp["id"]] = resp

print("server:", by_id["init"]["result"]["serverInfo"])
content = by_id["search"]["result"]["content"]
payload = json.loads(content[0]["text"]) if isinstance(content, list) else content
results = payload.get("results", payload) if isinstance(payload, dict) else payload
print("\nTop 5 risultati memory_search (nuova sessione simulata):")
for i, h in enumerate(results, 1):
    print(
        f"  {i}. {h.get('memory_type')} | score {round(h.get('score', 0), 4)} "
        f"| {str(h.get('title'))[:65]}"
    )
first_is_memory = results and results[0].get("memory_type") not in (None, "document_chunk")
print("\nVERDETTO:", "OK - memoria in prima posizione" if first_is_memory else "KO - la memoria non e' prima")
