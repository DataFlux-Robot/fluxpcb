#!/usr/bin/env python3
"""One table from the per-tool results of a campaign.

    KICAD_PYTHON=/usr/bin/python3 python3 scripts/collect.py [campaign]

Reads results/<campaign>/<tool>.json, runs KiCad DRC on each human reference
board once (the baseline: two upstream boards carry their own findings, an
unclosed outline or a courtyard overlap), and reports for every tool the DRC
errors it added over that baseline, by type. Writes
results/<campaign>/summary.json and summary.md.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, board_dir, drc, manifest  # noqa: E402

TOOLS = [("koppar", "Koppar"), ("koppar_filled", "Koppar (filled vias)"), ("freerouting", "Freerouting"), ("tscircuit", "tscircuit"), ("krt", "KiCad Routing Tools"), ("tracemaker", "TraceMaker"), ("zap", "Zap!")]


def main():
    campaign = sys.argv[1] if len(sys.argv) > 1 else "2026-10-placement"
    here = os.path.join(ROOT, "results", campaign)
    runs = {}
    for key, _ in TOOLS:
        p = os.path.join(here, f"{key}.json")
        if os.path.exists(p):
            with open(p) as f:
                runs[key] = json.load(f)
    rows = []
    for b in manifest():
        name = b["name"]
        base = drc(name, os.path.join(board_dir(name), "circuit.reference.kicad_pcb")) or {}
        bt = base.get("errorsByType") or {}
        row = {"board": name, "layers": b["layers"], "source": b["home"], "reference": {"drc": base}}
        for key, _ in TOOLS:
            r = next((x for x in runs.get(key, {}).get("boards", []) if x["board"] == name), None)
            if not r:
                continue
            row.setdefault("ratsnest", r.get("ratsnest"))
            d = r.get("drc")
            if isinstance(d, dict) and d.get("errors") is not None:
                new = {k: v - bt.get(k, 0) for k, v in (d.get("errorsByType") or {}).items() if v - bt.get(k, 0) > 0}
                d = {**d, "netNew": sum(new.values()), "netNewByType": new}
            row[key] = {k: r.get(k) for k in ("completionPct", "unconnected", "vias", "viasInPinPads", "trackMm", "seconds", "finishedAtS", "timedOut", "late", "netByNet", "version", "refused")} | {"drc": d}
        rows.append(row)
    out = {
        "campaign": campaign,
        "metric": "completionPct = 100 * (1 - KiCad unconnected / ratsnest of the unrouted board); drc.netNew = kicad-cli DRC errors over the human reference's own, with the board's benchmark project; seconds = wall clock of the tool.",
        "capS": next((r.get("capS") for r in runs.values() if r.get("capS")), None),
        "boards": rows,
    }
    with open(os.path.join(here, "summary.json"), "w") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    lines = [f"# Campaign {campaign}", "", "Completion by KiCad ratsnest, DRC errors added over the human board, vias, wall-clock seconds.", "",
             "| Board | Layers | Connections | " + " | ".join(t for _, t in TOOLS) + " |", "|---|---|---|" + "---|" * len(TOOLS)]
    for r in rows:
        cells = []
        for key, _ in TOOLS:
            x = r.get(key)
            if not x:
                cells.append("not run")
            elif x.get("refused"):
                cells.append(f"refused ({x['refused'].get('error', 'no reason given')})")
            elif x["completionPct"] is None or (x["completionPct"] == 0 and x.get("timedOut")):
                cells.append(f"timed out ({x['seconds']} s)" if x.get("timedOut") else f"no board ({x['seconds']} s)")
            else:
                n = (x.get("drc") or {}).get("netNew")
                cells.append(f"{x['completionPct']:g}% · {n if n is not None else '?'} DRC · {x['vias']} vias · {x['seconds']} s" + (" (late)" if x.get("late") else ""))
        lines.append(f"| {r['board']} | {r['layers']} | {r.get('ratsnest')} | " + " | ".join(cells) + " |")
    with open(os.path.join(here, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
