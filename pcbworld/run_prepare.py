#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run PCBWorld's prepare chain for D3-A + D3-B + D3-C.

Replicates pcbworld/prepare.py but:
  * uses the already-extracted PCBWorld/ and PCBench/ dirs (no git clone);
  * also builds the D3-C split (hard.test) which prepare.py omits;
  * copies board dirs instead of symlinking (Windows symlink privileges).

Usage (from autorouter-bench):
    KICAD_PYTHON=<kicad python> python work/pcbworld/run_prepare.py
"""
import json
import os
import shutil
import subprocess
import sys
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PW = os.path.join(HERE, "PCBWorld")
PB = os.path.join(HERE, "PCBench")
WORK = HERE

sys.path.insert(0, os.path.join(HERE, "..", "..", "scripts"))
from common import KICAD_PYTHON  # noqa: E402

KICAD_CLI = os.environ.get("KICAD_CLI", shutil.which("kicad-cli") or "kicad-cli")


def main():
    d3 = json.load(open(os.path.join(PW, "configs", "datasets", "d3.json")))
    overrides = d3.get("_test_overrides", {}).get("medium", {})

    tests = []
    for split, key, tag in (("d3a", "easy", "easy"),
                            ("d3b", "medium", "medium"),
                            ("d3c", "hard", "hard")):
        for b in d3[key]["test"]:
            b2 = overrides.get(b, b)
            tests.append((split, b2))
    print(f"{len(tests)} test boards: "
          f"{sum(1 for s,_ in tests if s=='d3a')} d3a + "
          f"{sum(1 for s,_ in tests if s=='d3b')} d3b + "
          f"{sum(1 for s,_ in tests if s=='d3c')} d3c", flush=True)

    # 1. pcbs-test: one entry per board (copied dir, name = id minus prefix)
    only = os.path.join(WORK, "pcbs-test")
    os.makedirs(only, exist_ok=True)
    missing = []
    for _, b in tests:
        name = b.split("_", 1)[1]
        src = os.path.join(PB, "PCBs", name)
        dst = os.path.join(only, name)
        if not os.path.isdir(src):
            missing.append(name)
            continue
        if not os.path.isdir(dst):
            shutil.copytree(src, dst)
    if missing:
        print(f"WARNING: {len(missing)} boards missing from PCBench: {missing[:5]}", flush=True)

    env = dict(os.environ,
               KICAD_CLI=KICAD_CLI,
               PCBNEW_PYTHON=KICAD_PYTHON,
               PCBENCH_PCBS_ROOT=only,
               PCBENCH_V9_ROOT=os.path.join(WORK, "v9"),
               PCBENCH_NEWDRC_OUT=os.path.join(WORK, "newdrc"))

    prep = os.path.join(PW, "tools", "datagen", "pcbench_prep")
    steps = [
        ["convert_v9.py", "--workers", "4"],
        ["drc_fix_v9.py", "--workers", "4"],
        ["make_guide.py", "--base-dir", env["PCBENCH_NEWDRC_OUT"],
         "--stem", "processed_v9", "--suffix", "_guide_v3", "--workers", "4",
         "--log", os.path.join(WORK, "make_guide_log.json")],
    ]
    for cmd in steps:
        t0 = time.time()
        print(f"[chain] {cmd[0]} ...", flush=True)
        r = subprocess.run([sys.executable, os.path.join(prep, cmd[0]), *cmd[1:]],
                           env=env)
        print(f"[chain] {cmd[0]} exit={r.returncode} in {time.time()-t0:.0f}s", flush=True)
        if r.returncode != 0:
            print(f"[chain] aborting at {cmd[0]}", flush=True)
            return 1

    # 4. Copy guides into the harness layout
    out = []
    built = 0
    for s, b in tests:
        name = b.split("_", 1)[1]
        src = os.path.join(env["PCBENCH_NEWDRC_OUT"], name)
        d = re.sub(r"[^A-Za-z0-9._-]", "-", b)
        dst = os.path.join(WORK, "boards", d)
        pcb = os.path.join(src, "processed_v9_guide_v3.kicad_pcb")
        pro = os.path.join(src, "processed_v9_guide_v3.kicad_pro")
        if not (os.path.exists(pcb) and os.path.exists(pro)):
            print(f"MISSING guide for {b}", flush=True)
            continue
        os.makedirs(dst, exist_ok=True)
        shutil.copy(pcb, os.path.join(dst, "circuit.reference.kicad_pcb"))
        shutil.copy(pro, os.path.join(dst, "circuit.kicad_pro"))
        out.append({"set": s, "id": b, "dir": d})
        built += 1

    with open(os.path.join(WORK, "boards.json"), "w") as f:
        json.dump(out, f, indent=1)
    print(f"{built}/{len(tests)} boards in {os.path.join(WORK, 'boards')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
