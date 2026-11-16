#!/usr/bin/env python3
"""Fetch the benchmark boards from their upstream sources and build the
boards every tool is run on.

    KICAD_PYTHON=/usr/bin/python3 [BOARD_SET=zap25] python3 scripts/fetch_boards.py [board ...]

For each board in boards/manifest.json (or boards/manifest-<BOARD_SET>.json):
  1. download the upstream files (or archive) and check them against
     boards/checksums.json;
  2. load the routed upstream board in KiCad 10 and save it as
     work/boards/<name>/circuit.reference.kicad_pcb (format upgrade only);
  3. write work/boards/<name>/circuit.kicad_pro: the upstream project with the
     benchmark rules in boards/rules/<name>.json applied, and the project's
     custom rules (.kicad_dru) as circuit.kicad_dru when it has them. Those
     rules are the same for every tool and are the DRC referee's rules.

`--record` writes the checksums of what was downloaded instead of checking them.
"""
import hashlib
import io
import json
import os
import shutil
import sys
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import ROOT, WORK, board_dir, kicad, manifest  # noqa: E402

CHECKSUMS = os.path.join(ROOT, "boards", "checksums.json")
CACHE = os.path.join(WORK, "downloads")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def download(url):
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, sha256(url.encode())[:16])
    if not os.path.exists(path):
        req = urllib.request.Request(url, headers={"User-Agent": "autorouter-bench"})
        with urllib.request.urlopen(req, timeout=300) as r, open(path + ".part", "wb") as f:
            shutil.copyfileobj(r, f)
        os.replace(path + ".part", path)
    with open(path, "rb") as f:
        return f.read()


def apply_rules(pro_path, rules_path):
    """The benchmark rules over the upstream project: rule minima, severities, net classes."""
    with open(pro_path) as f:
        pro = json.load(f)
    with open(rules_path) as f:
        rules = json.load(f)
    ds = pro.setdefault("board", {}).setdefault("design_settings", {})
    ds.setdefault("rules", {}).update(rules.get("rules", {}))
    ds.setdefault("rule_severities", {}).update(rules.get("rule_severities", {}))
    ns = pro.setdefault("net_settings", {})
    classes = ns.setdefault("classes", [])
    drop = set(rules.get("net_classes_removed", []))
    classes[:] = [c for c in classes if c.get("name") not in drop]
    for name, values in rules.get("net_classes", {}).items():
        c = next((c for c in classes if c.get("name") == name), None)
        if c is None:
            c = {"name": name}
            classes.append(c)
        c.update(values)
    for key in ("netclass_patterns", "netclass_assignments"):
        if key in rules:
            ns[key] = rules[key]
    with open(pro_path, "w") as f:
        json.dump(pro, f, indent=2)
        f.write("\n")


def main():
    record = "--record" in sys.argv
    picked = [a for a in sys.argv[1:] if not a.startswith("--")]
    sums = {}
    if os.path.exists(CHECKSUMS):
        with open(CHECKSUMS) as f:
            sums = json.load(f)
    for b in manifest():
        name = b["name"]
        if picked and name not in picked:
            continue
        print(f"=== {name}", flush=True)
        blobs = {}
        archive = None
        if "archive" in b:
            data = download(b["archive"]["url"])
            key = f"{name}:archive"
            if record:
                sums[key] = sha256(data)
            elif sums.get(key) != sha256(data):
                raise SystemExit(f"{name}: archive checksum mismatch ({sha256(data)})")
            archive = zipfile.ZipFile(io.BytesIO(data))
        for kind, spec in b["files"].items():
            data = archive.read(spec["member"]) if "member" in spec else download(spec["url"])
            key = f"{name}:{kind}"
            if record:
                sums[key] = sha256(data)
            elif sums.get(key) != sha256(data):
                raise SystemExit(f"{name}: {kind} checksum mismatch ({sha256(data)})")
            blobs[kind] = (spec.get("member") or spec["url"]).rsplit("/", 1)[-1], data
        up = os.path.join(WORK, "upstream", name)
        shutil.rmtree(up, ignore_errors=True)
        os.makedirs(up)
        # KiCad pairs a board with the project of the same base name.
        pro_ext = ".kicad_pro" if blobs["pro"][0].endswith(".kicad_pro") else ".pro"
        with open(os.path.join(up, "board.kicad_pcb"), "wb") as f:
            f.write(blobs["pcb"][1])
        with open(os.path.join(up, "board" + pro_ext), "wb") as f:
            f.write(blobs["pro"][1])
        out = board_dir(name)
        shutil.rmtree(out, ignore_errors=True)
        r = kicad("prepare", os.path.join(up, "board.kicad_pcb"), out)
        pro = os.path.join(out, "circuit.kicad_pro")
        if r.get("project"):
            shutil.move(r["project"], pro)
        elif pro_ext == ".kicad_pro":
            shutil.copy(os.path.join(up, "board.kicad_pro"), pro)
        else:
            raise SystemExit(f"{name}: KiCad wrote no project for the legacy board")
        # a project's custom rules (.kicad_dru) go beside the board under the project's name: KiCad reads
        # them with the project, for every router and for the referee
        if "dru" in blobs:
            with open(os.path.join(out, "circuit.kicad_dru"), "wb") as f:
                f.write(blobs["dru"][1])
        rules = os.path.join(ROOT, "boards", "rules", f"{name}.json")
        if os.path.exists(rules):
            apply_rules(pro, rules)
        m = kicad("measure", r["reference"])
        print(f"  reference: {m['unconnected']} unconnected, {m['vias']} vias, {m['trackMm']} mm of track", flush=True)
    if record:
        with open(CHECKSUMS, "w") as f:
            json.dump(dict(sorted(sums.items())), f, indent=2)
            f.write("\n")
        print(f"recorded {CHECKSUMS}")


if __name__ == "__main__":
    main()
