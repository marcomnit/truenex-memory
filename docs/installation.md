# Installation

> ⚠️ **Public Alpha — source install only.** PyPI release is planned for v0.2.0. See [ROADMAP.md](../ROADMAP.md).

## Quick Install (from source)

```bash
# 1. Clone
git clone https://github.com/marcomnit/truenex-memory.git
cd truenex-memory

# 2. Create virtual environment
python -m venv .venv

# 3. Install in editable mode
# Windows:
.venv\Scripts\pip install -e ".[dev]"
# Linux / macOS:
.venv/bin/pip install -e ".[dev]"

# 4. Verify
truenex-mem --help
```

## Future User Install

Once published to PyPI, the install path will be:

```bash
pipx install truenex-memory
```

Until then, use the editable install above.

## Repositories

- [`marcomnit/truenex-memory`](https://github.com/marcomnit/truenex-memory): source repository for the open-core local memory layer.
- [`marcomnit/truenex-memory-dev`](https://github.com/marcomnit/truenex-memory-dev): private development workspace (not public).

## Local-First Update Policy

Truenex Memory does not auto-update silently. Update checks are manual:

```bash
truenex-mem update check
```

The update check downloads only the public manifest. It does not send project
paths, indexed files, memory content, machine identifiers, or telemetry.

## Auto-Refresh Scheduler (Planned / Opt-In)

Truenex Memory indexes agent sessions from all clients (Claude Code, Codex, Cursor,
opencode, VS Code extensions, etc.) by reading their JSONL session files. For this
to work continuously, a background refresh should eventually run independently
of any specific client — not as a hook inside one tool, but as an OS-level
scheduled job.

During the current Phase 3 validation work, scheduling is intentionally
opt-in/manual. Without a scheduler, new sessions are indexed only when the user
manually runs `truenex-mem global refresh` or `truenex-mem global auto run`.

Background jobs must run only conservative indexing commands such as
`truenex-mem global refresh`. They must not run
`truenex-mem global auto run --auto-memory`, because generated-memory creation
requires explicit review controls and should not happen silently in the
background.

The future installer-managed scheduler must be reversible and explicit: status,
install, disable, clear log path, no secrets, no global client hook mutation
unless the user asks for it, and no visible console windows on Windows.

### Windows — Task Scheduler

Prefer the future `truenex-mem scheduler install/status/disable` commands once
they exist. Until then, use manual refresh during development. If a local test
scheduler is needed, run `global refresh` directly, not through a batch file and
not through `cmd.exe`.

```powershell
$action  = New-ScheduledTaskAction `
    -Execute "truenex-mem" `
    -Argument "global refresh"
$trigger = New-ScheduledTaskTrigger `
    -RepetitionInterval (New-TimeSpan -Hours 1) `
    -Once -At (Get-Date)
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal `
    -UserId $env:USERNAME `
    -LogonType Interactive
Register-ScheduledTask `
    -TaskName "TruenexMemoryRefresh" `
    -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "Hourly index of all agent sessions into truenex-memory" `
    -Force
```

Or via `schtasks` (no admin required):

```cmd
schtasks /create /tn "TruenexMemoryRefresh" /tr "truenex-mem global refresh" /sc hourly /f
```

### Linux — cron

```bash
# Add to user crontab (crontab -e):
0 * * * * /home/$USER/.local/bin/truenex-mem global refresh >> ~/.truenex-memory/refresh.log 2>&1
```

Or as a systemd user timer (`~/.config/systemd/user/truenex-refresh.timer`):

```ini
[Unit]
Description=Truenex Memory hourly refresh

[Timer]
OnBootSec=5min
OnUnitActiveSec=1h

[Install]
WantedBy=timers.target
```

With companion service (`~/.config/systemd/user/truenex-refresh.service`):

```ini
[Unit]
Description=Truenex Memory refresh

[Service]
Type=oneshot
ExecStart=%h/.local/bin/truenex-mem global refresh
```

Enable: `systemctl --user enable --now truenex-refresh.timer`

### macOS — launchd

Save to `~/Library/LaunchAgents/com.truenex.memory.refresh.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.truenex.memory.refresh</string>
  <key>ProgramArguments</key>
  <array>
    <string>truenex-mem</string>
    <string>global</string>
    <string>refresh</string>
  </array>
  <key>StartInterval</key>
  <integer>3600</integer>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/Users/USERNAME/.truenex-memory/refresh.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/USERNAME/.truenex-memory/refresh.log</string>
</dict>
</plist>
```

Load: `launchctl load ~/Library/LaunchAgents/com.truenex.memory.refresh.plist`

### Design Rationale

Client-specific hooks (e.g. Claude Code `Stop`, Codex post-session hooks) may be
added as **supplements** to provide more frequent indexing during active sessions,
but they must never be the sole refresh mechanism. Hooks must be short,
fire-and-forget, bounded by a strict timeout, and must run only conservative
refresh commands.

The OS scheduler remains the intended client-agnostic trigger, but it must be
enabled only after the refresh path is fast enough for real data, concurrent-run
handling is verified, logging has rotation, and Windows execution is silent.

| Layer | Scope | Purpose |
|---|---|---|
| OS scheduler (hourly) | All clients | Future primary trigger |
| Client hook (per-response) | One client only | Optional supplement after timeout/fire-and-forget hardening |

## Optional Local Qdrant

The default vector backend is SQLite-persisted local fallback vectors. To try
Qdrant locally:

```bash
python -m pip install -e ".[qdrant]"
docker compose up -d qdrant

# Windows (cmd)
set TRUENEX_MEMORY_VECTOR_BACKEND=qdrant
set TRUENEX_MEMORY_QDRANT_URL=http://localhost:6333
set TRUENEX_MEMORY_QDRANT_COLLECTION=truenex_memory

# Linux / macOS
export TRUENEX_MEMORY_VECTOR_BACKEND=qdrant
export TRUENEX_MEMORY_QDRANT_URL=http://localhost:6333
export TRUENEX_MEMORY_QDRANT_COLLECTION=truenex_memory

truenex-mem doctor --privacy
```

If Qdrant or `qdrant-client` is unavailable, Truenex Memory falls back to SQLite
vectors and reports the fallback in `doctor --privacy`.
