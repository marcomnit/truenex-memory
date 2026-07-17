# Git Bridge — Multi-PC Sync Setup Guide

Truenex Memory Pro includes **Git Bridge**, which lets you sync your memory database across multiple PCs using any Git remote (GitHub, GitLab, Gitea, or your own server).

## Prerequisites

- **Pro license** active on all PCs (`truenex-mem license status` must show `tier: pro`)
- A **private Git repository** to use as sync target (create one on GitHub, GitLab, etc.)
- On Windows: OpenSSH client (installed by default on Windows 10/11)

---

## Step 1 — Generate an SSH key on each PC

On **every PC** you want to sync, open PowerShell and run:

```powershell
ssh-keygen -t ed25519 -C "my-pc-name"
```

Press Enter three times to accept defaults.

Read the public key:

```powershell
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
```

Copy the entire line starting with `ssh-ed25519`.

---

## Step 2 — Add the key to your Git provider

Go to your Git provider's SSH key settings:

- **GitHub**: https://github.com/settings/keys
- **GitLab**: https://gitlab.com/profile/keys

Click **New SSH key**, paste the public key, give it a title (e.g. "Office PC"), and save.

Repeat for every PC.

---

## Step 3 — Initialize Git Bridge on each PC

On **each PC**:

```powershell
truenex-mem git init
truenex-mem git remote add origin git@github.com:YOUR_USER/YOUR_REPO.git
```

Replace `YOUR_USER/YOUR_REPO.git` with your actual repository SSH URL.

---

## Step 4 — Sync

### First PC (push)

```powershell
truenex-mem git push
```

### Second PC (pull)

```powershell
truenex-mem git pull
```

The first time you connect, SSH will ask:

```
Are you sure you want to continue connecting (yes/no)?
```

Type `yes` and press Enter.

---

## Step 5 — Ongoing workflow

Whenever you want to sync:

- **Push** from the PC where you made changes
- **Pull** on the other PC

```powershell
truenex-mem git push   # on PC with new data
truenex-mem git pull   # on the other PC
```

---

## Troubleshooting

### "Permission denied (publickey)"

Your SSH key is not added to the Git provider, or the remote URL is HTTPS instead of SSH. Fix:

```powershell
truenex-mem git remote remove origin
truenex-mem git remote add origin git@github.com:YOUR_USER/YOUR_REPO.git
```

### "refusing to merge unrelated histories"

If you initialized Git Bridge on two PCs independently before connecting them, run once manually:

```powershell
cd $env:USERPROFILE\.truenex-memory\sync
git pull --allow-unrelated-histories origin master
```

Then use `truenex-mem git pull` normally.

---

## Security Notes

- Always use a **private repository** for sync. Your memory data may contain sensitive project information.
- Never share your private SSH key (`id_ed25519`). Only the public key (`id_ed25519.pub`) should be uploaded to GitHub.
- The Git Bridge tokenizes and exports your memory nodes as JSON — the raw SQLite database (`*.db`) is never pushed.
