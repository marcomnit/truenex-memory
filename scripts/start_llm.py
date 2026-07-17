#!/usr/bin/env python3
"""Bootstrap the llama-server Docker sidecar.

At first run, downloads the GGUF model from HuggingFace if not present locally.
Auto-detects GPU vs CPU and picks the correct Docker profile.

Usage:
    python scripts/start_llm.py
    python scripts/start_llm.py --profile cpu
    python scripts/start_llm.py --repo Qwen/Qwen2.5-7B-Instruct-GGUF --file qwen2.5-7b-instruct-q4_k_m.gguf
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow importing from src/ without installing the package
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from truenex_memory.llm_bootstrap import main

if __name__ == "__main__":
    main()
