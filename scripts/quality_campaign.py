#!/usr/bin/env python3
"""Run scripts/quality.py over every routed board of a campaign and summarise it beside the
human reference.

    KICAD_PYTHON=/usr/bin/python3 python3 scripts/quality_campaign.py <campaign> <tool>=<boards dir> ...

<boards dir>/<board>/ holds that tool's routed board (routed.kicad_pcb or <board>.kicad_pcb);
the human reference is work/boards/<board>/circuit.reference.kicad_pcb. Writes
results/<campaign>/quality.json (every measure, per tool and board) and quality.md (the
headline numbers). Only boards a tool returned are measured.
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import KICAD_PYTHON, ROOT, board_dir, manifest  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def run_quality(pcb):
    out = subprocess.run([KICAD_PYTHON, os.path.join(HERE, "quality.py"), pcb, "--json", "/dev/stdout"],
                         capture_output=True, text=True)
    text = out.stdout
    start = text.find("{")
    try:
        return json.JSONDecoder().raw_decode(text[start:])[0]
    except ValueError:
        return {"error": (out.stderr or text)[-400:]}


def main():
    campaign = sys.argv[1]
    tools = [("human", None)] + [tuple(a.split("=", 1)) for a in sys.argv[2:]]
    res = {}
    for b in manifest():
        name = b["name"]
        for tool, d in tools:
            pcb = os.path.join(board_dir(name), "circuit.reference.kicad_pcb") if tool == "human" else next(
                (p for p in (os.path.join(d, name, "routed.kicad_pcb"), os.path.join(d, name, f"{name}.kicad_pcb")) if os.path.exists(p)), None)
            if pcb is None:
                continue
            q = run_quality(pcb)
            q.pop("board", None)
            res.setdefault(name, {})[tool] = q
            print(name, tool, "ok" if "error" not in q else q["error"][:120], flush=True)
    here = os.path.join(ROOT, "results", campaign)
    with open(os.path.join(here, "quality.json"), "w") as f:
        json.dump({"note": "scripts/quality.py on every routed board and the human reference", "boards": res}, f, indent=1)
        f.write("\n")
    names = [t for t, _ in tools]

    def cell(q, path, fmt="{}"):
        v = q
        for k in path:
            v = v.get(k) if isinstance(v, dict) else None
        return "—" if v is None else fmt.format(v)

    lines = [f"# Quality, campaign {campaign}", "",
             "Measured with `scripts/quality.py` on each routed board; `human` is the upstream design. A dash: not measured (no board, or nothing of that kind on it).", ""]
    for title, path, fmt in [
        ("High-speed track over a ground plane (% of length)", ("return_path", "hs_over_ground_pct"), "{}%"),
        ("High-speed track over any plane (% of length)", ("return_path", "hs_over_plane_pct"), "{}%"),
        ("High-speed vias with a ground via within 1.5 mm (%)", ("return_path", "hs_vias_with_ground_via_pct"), "{}%"),
        ("Differential pairs: median length mismatch (mm)", ("pairs", "median_skew_mm"), "{}"),
        ("Differential pairs: median share routed side by side (%)", ("pairs", "median_coupled_pct"), "{}%"),
        ("Decoupling: median track path, capacitor to pin (mm)", ("decoupling", "median_path_mm"), "{}"),
        ("Decoupling: median vias on that path", ("decoupling", "median_vias"), "{}"),
    ]:
        lines += [f"## {title}", "", "| Board | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
        for name, row in res.items():
            lines.append(f"| {name} | " + " | ".join(cell(row.get(t, {}), path, fmt) if t in row else "no board" for t in names) + " |")
        lines.append("")
    with open(os.path.join(here, "quality.md"), "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
