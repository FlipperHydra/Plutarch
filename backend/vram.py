"""VRAM/RAM heuristic estimator.

Rules of thumb, not measurements:
  * Recommended models have a starter table at 4k / 16k context.
  * Unknown models use disk_size_bytes * 1.3 with an "estimate only" flag.
  * Available memory = max(free GPU VRAM via nvidia-smi, free RAM / 2).
"""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass

import psutil

# GB values.
_TABLE_4K: dict[str, float] = {
    "gemma3:270m": 0.4,
    "qwen2.5:0.5b": 0.6,
    "llama3.2:1b": 1.1,
    "gemma3:1b": 1.2,
    "phi3:mini": 3.0,
    "gemma3:4b": 3.5,
}
_TABLE_16K: dict[str, float] = {
    "gemma3:270m": 0.7,
    "qwen2.5:0.5b": 1.0,
    "llama3.2:1b": 1.6,
    "gemma3:1b": 1.7,
    "phi3:mini": 4.0,
    "gemma3:4b": 4.5,
}


@dataclass
class VramEstimate:
    estimate_gb: float
    available_gb: float
    level: str          # ok | warn | block
    is_heuristic: bool  # True when derived from disk size, not the table
    note: str


async def _nvidia_free_gb() -> float | None:
    if shutil.which("nvidia-smi") is None:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "nvidia-smi",
            "--query-gpu=memory.free",
            "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        lines = [l for l in stdout.decode().splitlines() if l.strip()]
        if not lines:
            return None
        # Sum across GPUs (Ollama can shard).
        mib = sum(int(x.strip()) for x in lines)
        return mib / 1024.0
    except Exception:
        return None


async def estimate(model: str, ctx: int, disk_size_bytes: int | None) -> VramEstimate:
    ctx_bucket = 16 if ctx >= 16000 else 4
    table = _TABLE_16K if ctx_bucket == 16 else _TABLE_4K

    if model in table:
        estimate_gb = table[model]
        heuristic = False
        note = f"Table estimate for {model} at ~{ctx_bucket}k context."
    elif disk_size_bytes:
        estimate_gb = round((disk_size_bytes / 1024 ** 3) * 1.3, 2)
        heuristic = True
        note = "Heuristic (disk size x 1.3) - estimate only."
    else:
        estimate_gb = 4.0
        heuristic = True
        note = "Model unknown and not yet pulled - showing a rough 4 GB placeholder."

    gpu_free = await _nvidia_free_gb()
    ram_free_gb = psutil.virtual_memory().available / 1024 ** 3
    if gpu_free is not None:
        available = max(gpu_free, ram_free_gb / 2)
    else:
        available = ram_free_gb / 2

    if estimate_gb > available:
        level = "block"
    elif estimate_gb > available * 0.8:
        level = "warn"
    else:
        level = "ok"

    return VramEstimate(
        estimate_gb=estimate_gb,
        available_gb=round(available, 2),
        level=level,
        is_heuristic=heuristic,
        note=note,
    )
