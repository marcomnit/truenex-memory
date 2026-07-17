"""Bootstrap utilities for the llama-server Docker sidecar."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import httpx

# Support .env file if python-dotenv is available
try:
    from dotenv import load_dotenv
    _project_root = Path(__file__).parent.parent.parent
    _dotenv_path = _project_root / ".env"
    if _dotenv_path.exists():
        load_dotenv(_dotenv_path)
except Exception:
    pass

DEFAULT_REPO = os.environ.get("TRUENEX_LLM_REPO", "Qwen/Qwen2.5-7B-Instruct-GGUF")
DEFAULT_FILE = os.environ.get("TRUENEX_LLM_FILE", "qwen2.5-7b-instruct-q4_k_m.gguf")
HF_BASE_URL = os.environ.get("TRUENEX_HF_BASE_URL", "https://huggingface.co")
MIN_MODEL_BYTES = int(os.environ.get("TRUENEX_LLM_MIN_BYTES", "1048576"))
LLM_PORT = int(os.environ.get("TRUENEX_LLM_PORT", "9081"))
DEFAULT_GPU_LAYERS = int(os.environ.get("TRUENEX_LLM_GPU_LAYERS", "999"))


def check_docker() -> bool:
    """Return True if Docker daemon is reachable."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def has_nvidia_gpu() -> bool:
    """Return True if nvidia-smi is available and succeeds."""
    try:
        result = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def has_nvidia_docker_runtime() -> bool:
    """Return True if Docker has the nvidia runtime configured."""
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.Runtimes}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and "nvidia" in result.stdout.lower()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def pick_profile() -> str:
    """Choose docker-compose profile based on hardware capabilities."""
    if has_nvidia_gpu() and has_nvidia_docker_runtime():
        return "cuda"
    return "cpu"


def _hf_download_url(repo: str, filename: str) -> str:
    return f"{HF_BASE_URL}/{repo}/resolve/main/{filename}"


def _progress_bar(total: int | None):
    """Return a tqdm-like progress callback if available."""
    try:
        from tqdm import tqdm

        pbar = tqdm(total=total, unit="B", unit_scale=True, unit_divisor=1024)

        def update(n: int):
            pbar.update(n)

        def close():
            pbar.close()

        return update, close
    except ImportError:
        downloaded = 0
        last_report = 0

        def update(n: int):
            nonlocal downloaded, last_report
            downloaded += n
            if total and downloaded - last_report >= 50 * 1024 * 1024:
                pct = downloaded / total * 100
                print(f"  Downloaded {downloaded / 1024 / 1024:.1f} MB / {total / 1024 / 1024:.1f} MB ({pct:.1f}%)")
                last_report = downloaded
            elif not total and downloaded - last_report >= 50 * 1024 * 1024:
                print(f"  Downloaded {downloaded / 1024 / 1024:.1f} MB")
                last_report = downloaded

        def close():
            if total:
                print(f"  Download complete: {total / 1024 / 1024:.1f} MB")
            else:
                print(f"  Download complete: {downloaded / 1024 / 1024:.1f} MB")

        return update, close


def _get_remote_size(url: str) -> int | None:
    """Return Content-Length from a HEAD request, or None."""
    try:
        r = httpx.head(url, follow_redirects=True, timeout=10.0)
        r.raise_for_status()
        length = r.headers.get("content-length")
        return int(length) if length else None
    except Exception:
        return None


def ensure_model(
    models_dir: Path,
    repo: str = DEFAULT_REPO,
    filename: str = DEFAULT_FILE,
    yes: bool = False,
) -> Path:
    """Ensure the GGUF model exists locally, downloading if necessary.

    Supports resume for partially downloaded files.
    Asks for user confirmation before downloading unless *yes* is True.
    """
    models_dir.mkdir(parents=True, exist_ok=True)
    model_path = models_dir / filename

    # If file looks complete (>1MB), skip download
    if model_path.exists() and model_path.stat().st_size > MIN_MODEL_BYTES:
        print(f"Model already present: {model_path}")
        return model_path

    url = _hf_download_url(repo, filename)
    remote_size = _get_remote_size(url)
    size_mb = remote_size / 1024 / 1024 if remote_size else None

    print("\n" + "=" * 60)
    print("  LOCAL LLM MODEL DOWNLOAD")
    print("=" * 60)
    print(f"  Model:    {repo}/{filename}")
    if size_mb:
        print(f"  Size:     ~{size_mb:.1f} MB ({remote_size:,} bytes)")
    else:
        print("  Size:     unknown (will show progress during download)")
    print(f"  Save to:  {model_path.resolve()}")
    print("-" * 60)
    print("  Why:      This model powers local LLM inference inside")
    print("            a Docker container on your own machine.")
    print("            It is used ONLY for v0.1 health-check and")
    print("            will be wired into the search path in v0.2.")
    print("  Privacy:  No query data leaves your PC.")
    print("            No external LLM API key is required.")
    print("=" * 60)

    if not yes:
        confirm = input("\nProceed with download? [Y/n]: ").strip().lower()
        if confirm and confirm not in ("y", "yes"):
            print("Download cancelled by user.")
            sys.exit(0)

    headers: dict[str, str] = {}
    mode = "wb"
    resume_pos = 0

    if model_path.exists():
        resume_pos = model_path.stat().st_size
        headers["Range"] = f"bytes={resume_pos}-"
        mode = "ab"
        print(f"\nResuming download from {resume_pos:,} bytes...")
    else:
        print(f"\nDownloading {filename} from HuggingFace...")
        print(f"URL: {url}")

    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=60.0) as response:
        response.raise_for_status()
        total = response.headers.get("content-length")
        total_bytes = int(total) + resume_pos if total else None

        update, close = _progress_bar(total_bytes)

        with open(model_path, mode) as f:
            for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                f.write(chunk)
                update(len(chunk))

        close()

    print(f"\nModel ready: {model_path}")
    return model_path


def start_container(
    profile: str,
    models_dir: Path,
    compose_file: Path | None = None,
) -> None:
    """Start the llama-server container with the chosen profile."""
    if compose_file is None:
        compose_file = Path(__file__).parent.parent.parent / "docker-compose.llm.yml"

    if not compose_file.exists():
        print(f"Docker compose file not found: {compose_file}", file=sys.stderr)
        sys.exit(1)

    env = os.environ.copy()
    env["MODEL_FILE"] = DEFAULT_FILE
    env["MODELS_DIR"] = str(models_dir.resolve())
    env["N_GPU_LAYERS"] = env.get("N_GPU_LAYERS", str(DEFAULT_GPU_LAYERS) if profile == "cuda" else "0")

    cmd = [
        "docker", "compose",
        "-f", str(compose_file),
        "--profile", profile,
        "up", "-d", "--remove-orphans",
    ]

    print(f"\nStarting llama-server with profile: {profile}")
    print(f"  Compose file: {compose_file}")
    print(f"  Models dir:   {models_dir}")
    subprocess.run(cmd, env=env, check=True)
    print(f"llama-server started on http://localhost:{LLM_PORT}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Bootstrap llama-server sidecar")
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=Path(__file__).parent.parent.parent / "models",
        help="Directory to store/download GGUF models",
    )
    parser.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help="HuggingFace repo ID (e.g. Qwen/Qwen2.5-7B-Instruct-GGUF)",
    )
    parser.add_argument(
        "--file",
        default=DEFAULT_FILE,
        help="GGUF filename inside the repo",
    )
    parser.add_argument(
        "--profile",
        choices=["cpu", "cuda", "auto"],
        default="auto",
        help="Docker profile: cpu, cuda, or auto-detect",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip download confirmation prompt",
    )
    args = parser.parse_args()

    if not check_docker():
        print("ERROR: Docker is not installed or not running.", file=sys.stderr)
        sys.exit(1)

    profile = args.profile if args.profile != "auto" else pick_profile()
    print(f"Selected profile: {profile}")

    ensure_model(args.models_dir, args.repo, args.file, yes=args.yes)
    start_container(profile, args.models_dir)


if __name__ == "__main__":
    main()
