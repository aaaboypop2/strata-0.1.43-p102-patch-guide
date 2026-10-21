"""Compile Strata's engine from a (patched) checkout with Strata's own setup.py build steps.

  python tools/build-engine.py <Strata checkout> --arch 61 [--arch 86 ...] [--toolkit 12|13] [--vision gpu|cpu|none]
                               [--jobs N]

--arch: the CUDA compute capability of every card that runs the model (P102-100 / GTX 10xx = 61, RTX 20 = 75,
RTX 30 = 86, RTX 40 = 89).  --toolkit 12 is needed for Pascal (sm_61): CUDA 13 cannot compile for it.
--jobs: compile jobs at a time (default: half the CPUs); lower it when little RAM is free.
It never installs build tools: if the CUDA Toolkit or the C++ compiler is missing it stops and says what to install.
Run it with the checkout's own Python environment (.venv) so setup.py's imports resolve.
"""
import argparse
import os
import sys

ap = argparse.ArgumentParser()
ap.add_argument("root")
ap.add_argument("--arch", type=int, action="append", required=True)
ap.add_argument("--toolkit", type=int, default=None)
ap.add_argument("--vision", default="gpu")
ap.add_argument("--jobs", type=int, default=None)
a = ap.parse_args()
if a.jobs:
    os.cpu_count = lambda: 2 * a.jobs       # setup.py builds with -j max(2, cpu_count // 2)
sys.path.insert(0, os.path.abspath(a.root))
import setup  # noqa: E402

gpu = {"arch": max(a.arch), "archs": sorted(set(a.arch))}
eng = setup.build_engine(gpu, a.vision, False, setup.get_llama_cpp(), toolkit=a.toolkit)
print("engine:", eng)
print((eng / "BUILD.json").read_text(encoding="utf-8"))
