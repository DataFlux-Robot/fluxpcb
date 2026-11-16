#!/usr/bin/env python3
"""PCBWorld-Bench clean pass, scored by KiCad: a routed board passes when kicad-cli DRC
(the board's own .kicad_pro, zones refilled, error severity) reports no violation and no
unconnected item.

    python3 pcbworld/score.py work/pcbworld/boards.json <routed-root> <out.json>

<routed-root>/<dir>/<dir>.kicad_pcb (+ .kicad_pro beside it) for each board in boards.json."""
import json, os, shutil, subprocess, sys, tempfile

def drc(pcb, pro):
    with tempfile.TemporaryDirectory() as t:
        b = os.path.join(t, "b.kicad_pcb")
        shutil.copy(pcb, b)
        shutil.copy(pro, os.path.join(t, "b.kicad_pro"))
        rep = os.path.join(t, "r.json")
        subprocess.run(["kicad-cli", "pcb", "drc", "--format", "json", "--severity-error", "--refill-zones", "-o", rep, b],
                       capture_output=True, timeout=600)
        r = json.load(open(rep))
    errs = [v for v in r.get("violations", []) if not v.get("excluded")]
    by = {}
    for v in errs:
        by[v["type"]] = by.get(v["type"], 0) + 1
    return {"errors": len(errs), "errorsByType": by, "unconnected": len(r.get("unconnected_items", []))}

def main():
    boards, root, out = json.load(open(sys.argv[1])), sys.argv[2], sys.argv[3]
    rows = []
    for b in boards:
        d = b["dir"]
        pcb = os.path.join(root, d, f"{d}.kicad_pcb")
        pro = os.path.join(root, d, f"{d}.kicad_pro")
        if not os.path.exists(pcb):
            pcb, pro = os.path.join(root, d, "circuit.reference.kicad_pcb"), os.path.join(root, d, "circuit.kicad_pro")
        r = drc(pcb, pro) if os.path.exists(pcb) else None
        row = {**b, "drc": r, "cleanPass": bool(r) and r["errors"] == 0 and r["unconnected"] == 0}
        rows.append(row)
        print(d, row["cleanPass"], r, flush=True)
    json.dump(rows, open(out, "w"), indent=1)
    for s in sorted({b["set"] for b in boards}):
        sel = [r for r in rows if r["set"] == s]
        print(s, sum(r["cleanPass"] for r in sel), "/", len(sel))

main()
