#!/usr/bin/env python3
"""Add the measures a campaign's results lack, from the routed boards they were scored on.

    KICAD_PYTHON=/usr/bin/python3 python3 scripts/remeasure.py <campaign> <tool> <boards dir>

<boards dir>/<board>/ holds the routed board (routed.kicad_pcb, or <board>.kicad_pcb). Each
row of results/<campaign>/<tool>.json gets the fields `measure` returns that it does not have
yet (viasInPinPads); completion, DRC and times are left as they were scored.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, kicad  # noqa: E402


def main():
    campaign, tool, boards = sys.argv[1], sys.argv[2], sys.argv[3]
    path = os.path.join(ROOT, "results", campaign, f"{tool}.json")
    with open(path) as f:
        doc = json.load(f)
    for row in doc["boards"]:
        name = row["board"]
        pcb = next((p for p in (os.path.join(boards, name, "routed.kicad_pcb"), os.path.join(boards, name, f"{name}.kicad_pcb"))
                    if os.path.exists(p)), None)
        if pcb is None or row.get("completionPct") is None:
            continue
        m = kicad("measure", pcb)
        for k in ("viasInPinPads",):
            row[k] = m.get(k)
        print(f"{tool} {name}: viasInPinPads {row.get('viasInPinPads')}", flush=True)
    with open(path + ".tmp", "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    os.replace(path + ".tmp", path)


if __name__ == "__main__":
    main()
