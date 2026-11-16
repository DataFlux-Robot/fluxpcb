#!/usr/bin/env python3
"""Build the PCBWorld-Bench test boards (D3-A: 99, D3-B: 10) in the layout the harness reads.

    KICAD_PYTHON=/usr/bin/python3 python3 pcbworld/prepare.py

1. clones PCBWorld (LGAI-Research/PCBWorld, pinned) and PCBench (pinned), under work/pcbworld/;
2. applies pcbworld/patches/ to PCBWorld's preparation chain: KiCad 10 writes net names where
   KiCad 9 wrote net numbers (make_guide), and adds a pth_inside_courtyard check KiCad 9 does not
   have, set to ignore like the other checks the chain ignores (drc_fix_v9);
3. runs PCBWorld's own chain (convert_v9, drc_fix_v9, make_guide) on the test boards of
   configs/datasets/d3.json only (easy/test = D3-A, medium/test = D3-B, with the json's
   _test_overrides already applied);
4. writes work/pcbworld/boards/<id>/circuit.reference.kicad_pcb (the routed guide board, which the
   benchmark loads; its routing is removed before a router sees it, its pours kept) and
   circuit.kicad_pro (the guide project: the rules the referee judges by), plus boards.json.

PCBWorld scores with KiCad 9.0.8; this runs the chain with the KiCad on the machine (10.0.4 here).
Under KiCad 10 the reference boards themselves pass the referee on 104 of 109 (five guide boards
keep a clearance finding make_guide could not remove).
"""
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work", "pcbworld")
PCBWORLD = ("https://github.com/LGAI-Research/PCBWorld.git", "b3d62f5c37e7528670d112e03d9a90029f23f4f3")
PCBENCH = ("https://github.com/PCBench/PCBench.git", "dec3be75cbdef74787625f9043c7391cd473bb64")


def clone(url, rev, dst):
    if not os.path.isdir(dst):
        subprocess.run(["git", "clone", "-q", url, dst], check=True)
    subprocess.run(["git", "-C", dst, "checkout", "-q", rev], check=True)


def main():
    os.makedirs(WORK, exist_ok=True)
    pw, pb = os.path.join(WORK, "PCBWorld"), os.path.join(WORK, "PCBench")
    clone(*PCBWORLD, pw)
    clone(*PCBENCH, pb)
    patches = sorted(os.path.join(ROOT, "pcbworld", "patches", p) for p in os.listdir(os.path.join(ROOT, "pcbworld", "patches")))
    if subprocess.run(["git", "-C", pw, "apply", "--check", "-p1", *patches], capture_output=True).returncode == 0:
        subprocess.run(["git", "-C", pw, "apply", "-p1", *patches], check=True)

    d3 = json.load(open(os.path.join(pw, "configs", "datasets", "d3.json")))
    tests = [("d3a", b) for b in d3["easy"]["test"]] + [("d3b", b) for b in d3["medium"]["test"]]
    # the chain reads every board under PCBENCH_PCBS_ROOT: a folder of links to the test boards only
    only = os.path.join(WORK, "pcbs-test")
    os.makedirs(only, exist_ok=True)
    for _, b in tests:
        name = b.split("_", 1)[1]
        link = os.path.join(only, name)
        if not os.path.islink(link):
            os.symlink(os.path.join(pb, "PCBs", name), link)
    env = dict(os.environ,
               KICAD_CLI=os.environ.get("KICAD_CLI", shutil.which("kicad-cli") or "kicad-cli"),
               PCBNEW_PYTHON=os.environ.get("KICAD_PYTHON", "/usr/bin/python3"),
               PCBENCH_PCBS_ROOT=only,
               PCBENCH_V9_ROOT=os.path.join(WORK, "v9"),
               PCBENCH_NEWDRC_OUT=os.path.join(WORK, "newdrc"))
    prep = os.path.join(pw, "tools", "datagen", "pcbench_prep")
    for cmd in (["convert_v9.py", "--workers", "4"], ["drc_fix_v9.py", "--workers", "4"],
                ["make_guide.py", "--base-dir", env["PCBENCH_NEWDRC_OUT"], "--stem", "processed_v9", "--suffix", "_guide_v3",
                 "--workers", "4", "--log", os.path.join(WORK, "make_guide_log.json")]):
        subprocess.run([sys.executable, os.path.join(prep, cmd[0]), *cmd[1:]], check=True, env=env)

    out = []
    for s, b in tests:
        src = os.path.join(env["PCBENCH_NEWDRC_OUT"], b.split("_", 1)[1])
        d = re.sub(r"[^A-Za-z0-9._-]", "-", b)   # a folder name without spaces or brackets
        dst = os.path.join(WORK, "boards", d)
        os.makedirs(dst, exist_ok=True)
        shutil.copy(os.path.join(src, "processed_v9_guide_v3.kicad_pcb"), os.path.join(dst, "circuit.reference.kicad_pcb"))
        shutil.copy(os.path.join(src, "processed_v9_guide_v3.kicad_pro"), os.path.join(dst, "circuit.kicad_pro"))
        out.append({"set": s, "id": b, "dir": d})
    with open(os.path.join(WORK, "boards.json"), "w") as f:
        json.dump(out, f, indent=1)
    print(f"{len(out)} boards in {os.path.join(WORK, 'boards')}")


main()
