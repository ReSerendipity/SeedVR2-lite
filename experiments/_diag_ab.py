"""诊断8 (A/B 对照): 预占显存大小 -> 单个 VAE 解码耗时。

用法: DIT_GB=0|3|6.31 ./.venv/Scripts/python.exe -u experiments/_diag_ab.py
"""

import gc
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import torch  # noqa: E402

from experiments._diag_vae_bench import build_vae  # noqa: E402

GB = float(os.environ.get("DIT_GB", "0"))


def main():
    out_path = ROOT / "experiments" / f"_diag_ab_{GB}.out"
    with open(out_path, "w", buffering=1) as out:

        def p(*a):
            out.write(" ".join(str(x) for x in a) + "\n")

        vae = build_vae()
        torch.cuda.empty_cache()
        hold = None
        if GB > 0:
            hold = torch.empty(int(GB * 2**30 // 2), dtype=torch.bfloat16, device="cuda")
            torch.cuda.empty_cache()
        free, total = torch.cuda.mem_get_info()
        p(
            f"DIT_GB={GB} alloc={torch.cuda.memory_allocated() / 2**30:.2f}GB "
            f"free={free / 2**30:.2f}GB total={total / 2**30:.2f}GB"
        )

        z_small = torch.randn(1, 16, 1, 64, 64, device="cuda", dtype=torch.bfloat16)

        # 预热 (不计入)
        with torch.no_grad():
            vae.decode(z_small, tiled=False)
        torch.cuda.synchronize()
        p("warmup done")

        for i in range(3):
            torch.cuda.synchronize()
            t0 = time.perf_counter()
            with torch.no_grad():
                o = vae.decode(z_small, tiled=False)
            torch.cuda.synchronize()
            p(f"  run{i}: {time.perf_counter() - t0:.3f}s")
            del o
        del hold
        gc.collect()
        torch.cuda.empty_cache()
        p("DONE")


if __name__ == "__main__":
    main()
