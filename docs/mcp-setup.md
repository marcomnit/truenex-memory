# MCP Setup Guide

Connect your AI agent (Claude Code, Kimi, Cursor, Codex) to Truenex Memory so it can search your project automatically.

> **You only need to do this once.** After setup, the agent launches `truenex-mem mcp` on its own whenever it needs to recall something.

---

## 1. Find your `truenex-mem` path

The exact path depends on your OS and how you installed it (`pipx` or `pip`).

**Windows:**
```powershell
where truenex-mem
# Example output:
# C:\Users\<you>\AppData\Roaming\Python\Python313\Scripts\truenex-mem.exe
```

**macOS / Linux:**
```bash
which truenex-mem
# Example output:
# /Users/<you>/.local/bin/truenex-mem
```

If the command is not found, make sure the install location is in your `PATH`:

```bash
# After pipx install, you may need:
pipx ensurepath
```

---

## 2. Choose your agent

### Claude Code

**File:** `~/.claude/settings.json`

```json
{
  "mcpServers": {
    "truenex-memory": {
      "command": "C:\\Users\\<you>\\AppData\\Roaming\\Python\\Python313\\Scripts\\truenex-mem.exe",
      "args": [
        "mcp",
        "--project-root",
        "C:\\Users\\<you>"
      ],
      "env": {
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

Replace `<you>` with your Windows username. On macOS/Linux use the Unix path from `which truenex-mem`.

---

### Kimi

**File:** `~/.kimi/mcp.json`

```json
{
  "truenex-memory": {
    "command": "C:\\Users\\<you>\\AppData\\Roaming\\Python\\Python313\\Scripts\\truenex-mem.exe",
    "args": [
      "mcp",
      "--project-root",
      "C:\\Users\\<you>"
    ],
    "env": {
      "PYTHONUTF8": "1"
    }
  }
}
```

---

### Cursor

**File:** `~/.cursor/mcp.json`

```json
{
  "mcpServers": {
    "truenex-memory": {
      "command": "C:\\Users\\<you>\\AppData\\Roaming\\Python\\Python313\\Scripts\\truenex-mem.exe",
      "args": [
        "mcp",
        "--project-root",
        "C:\\Users\\<you>"
      ],
      "env": {
        "PYTHONUTF8": "1"
      }
    }
  }
}
```

---

### Codex (CLI)

**File:** `~/.codex/config.toml`

```toml
[mcp_servers.truenex-memory]
command = 'C:\Users\<you>\AppData\Roaming\Python\Python313\Scripts\truenex-mem.exe'
args = ["mcp", "--project-root", 'C:\Users\<you>']

[mcp_servers.truenex-memory.env]
PYTHONUTF8 = "1"
```

---

## 3. Restart your agent

After saving the file, **fully restart** the agent client (Claude Code, Kimi, Cursor, or Codex). It will now automatically call `truenex-mem mcp` whenever it needs project memory.

---

## Tips

- **`--project-root`**: This tells Truenex Memory which folder to treat as the root project. Set it to your home directory (`C:\Users\<you>` or `/Users/<you>`) if you work across multiple repos, or to a specific project folder if you only want memory for that one.
- **`PYTHONUTF8=1`**: Prevents encoding issues on Windows. Recommended but usually optional on macOS/Linux.
- **No background process needed**: You do **not** need to run `truenex-mem mcp` manually. The agent starts and stops it automatically.
