# LLM Sidecar Setup (v0.1)

Truenex Memory Desktop can optionally run a local LLM via **llama.cpp** in a Docker container. In v0.1 this sidecar is **prepared but not wired into the search path** — it serves only as a health-checkable foundation for v0.2.

## Why Download a Local Model?

Truenex Memory v0.1 prepares a **local LLM sidecar** so that in v0.2 we can wire it into the search path for query expansion and entity extraction. The model runs **entirely on your machine** inside a Docker container:

- **Zero external API calls** — your memory queries never leave your PC
- **No API key required** — once downloaded, it works offline
- **Privacy-first** — sensitive code and documents stay local

In v0.1 the sidecar is used only for health-check (`/api/health/llm`). It does not yet affect search results.

## Quick Start

```bash
# From the project root
python scripts/start_llm.py
```

The script will:
1. Check if Docker is installed and running
2. Auto-detect GPU (NVIDIA) or fall back to CPU
3. **Show you the model name, exact size, and disk path** before downloading
4. **Ask for your confirmation** (press Enter or type `y` to proceed)
5. Download the default model (`Qwen2.5-7B-Instruct-Q4_K_M`, ~4.5 GB) from HuggingFace if not present in `./models/`
6. Start the container on `http://localhost:9081`

To skip the confirmation prompt (e.g. for CI or automation):

```bash
python scripts/start_llm.py --yes
```

## Requirements

### All platforms
- [Docker](https://docs.docker.com/get-docker/) installed and running
- ~5 GB free disk space for the model

### GPU acceleration (optional)
- NVIDIA GPU with CUDA support
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) installed
- On **Windows**: Docker Desktop with WSL2 backend + NVIDIA Container Toolkit
- On **macOS**: GPU acceleration is **not available** — the script will automatically use the CPU profile

## Manual Docker Compose

If you prefer to use Docker Compose directly instead of the helper script:

```bash
# CPU-only (works everywhere)
docker compose -f docker-compose.llm.yml --profile cpu up -d

# GPU (requires NVIDIA Container Toolkit)
docker compose -f docker-compose.llm.yml --profile cuda up -d
```

## Model Selection

The default model is **Qwen 2.5 7B Instruct Q4_K_M** (~4.5 GB), optimized for coding tasks.

To use a different model, set the environment variables before running:

```bash
export TRUENEX_LLM_REPO="owner/repo-name"
export TRUENEX_LLM_FILE="model-name-q4_k_m.gguf"
python scripts/start_llm.py
```

Or pass flags directly:

```bash
python scripts/start_llm.py --repo microsoft/Phi-3-mini-4k-instruct-gguf --file Phi-3-mini-4k-instruct-q4.gguf
```

## Model Download Behavior

- The script checks `./models/` for the requested `.gguf` file.
- If the file is missing or looks incomplete (< 1 MB), the script:
  1. Fetches the remote file size via HTTP HEAD
  2. **Shows you**: model name, exact size in MB, and absolute save path
  3. **Asks for confirmation** before using bandwidth and disk space
  4. Downloads from HuggingFace with progress bar
- **Resume is supported**: if a previous download was interrupted, the script resumes from where it left off.
- Download progress is shown via `tqdm` if installed, otherwise a simple MB counter.

## Health Check

Once running, verify the container:

```bash
curl http://localhost:9081/props
```

The Truenex Memory backend exposes this via:

```bash
curl http://localhost:8000/api/health/llm
```

## Stopping

```bash
docker compose -f docker-compose.llm.yml --profile cpu down
docker compose -f docker-compose.llm.yml --profile cuda down
```

Or stop all profiles at once:

```bash
docker compose -f docker-compose.llm.yml down --remove-orphans
```

## Troubleshooting

| Issue | Solution |
|-------|----------|
| `Docker is not installed` | Install Docker Desktop (Win/Mac) or Docker Engine (Linux) |
| `nvidia-smi` not found | GPU profile unavailable — script falls back to CPU automatically |
| Download very slow | Set `HF_ENDPOINT=https://hf-mirror.com` if you're in a region with slow HuggingFace access |
| Container exits immediately | Check `docker logs <container_name>` — usually means the model file was not found in `./models/` |
| Port 9081 already in use | Stop the existing container or change the port mapping in `docker-compose.llm.yml` |
