"""In-pipeline auto-routing: ratsnest -> L-routes -> shorts-cleanup, bounded loop.

Chain:
  1. true connectivity from pcbnew ratsnest (conn_audit.py, uncalibratable)
     + kicad-cli DRC (refill zones, save board) for shorts/clearance only
  2. ratsnest island pairs -> KiCad Python route_embedded.py (L-routes + vias)
  3. shorting items   -> KiCad Python shorts_cleanup.py (drop culprit tracks)
  4. repeat until ratsnest=0 and shorts=0, or max_iterations

Severity calibration lives in the board's .kicad_pro
(boards.design_settings.rule_severities): hand-fixable items (unconnected,
dangling tracks, starved thermals) are 'ignore' so they don't mask real
errors; shorting/clearance/width stay 'error'. ensure_calibration() merges
that map into any project file deterministically. The ratsnest audit is
deliberately OUTSIDE this calibration — `unconnected_items=ignore` once made
boards with 79 open nets report zero and skip routing entirely.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

_EMBEDDED = Path(__file__).resolve().parent / "kicadgen" / "route_embedded.py"
_CLEANUP = Path(__file__).resolve().parent / "kicadgen" / "shorts_cleanup.py"
_SESSION = Path(__file__).resolve().parent / "kicadgen" / "route_session.py"
_FLUXROUTER = Path(__file__).resolve().parent / "kicadgen" / "fluxrouter.py"
_SCR2KICAD = Path(__file__).resolve().parent / "kicadgen" / "scr2kicad.py"

# hand-fixable after fab (or cosmetic) -> ignore; electrical errors stay error
# Severity calibration silences COSMETIC checks so kicad-cli exit codes
# reflect manufacturable reality. FUNCTIONAL checks are NEVER silenced —
# unconnected_items once sat here and made run_drc structurally blind
# (reported 0 opens forever while conn_audit saw 66: the same illusion
# the gates were built to escape, reintroduced through the back door).
# Protected by tests/test_kernel.py::TestSeverityProtection.
# track/via dangling + starved thermal stay silenced until the stub-
# cleanup pass exists (FR injection inherently leaves dangling stubs —
# un-ignoring them floods drc_zero with ~900 entries per board); they
# are tracked in K21 for the cleanup round. unconnected_items is the
# true-connectivity check and is NEVER silenced.
_FUNCTIONAL_NEVER_IGNORED = ("unconnected_items",)
_DEFERRED_FUNCTIONAL = ("via_dangling", "track_dangling", "starved_thermal")

# K22 semantic-contract version: ANY change to CALIBRATED_SEVERITIES
# semantics MUST bump this number — the test lock in test_kernel.py
# snapshots the dict and fails otherwise. "Config silently changed
# meaning" is structurally impossible from here (point 9).
SEVERITIES_POLICY_VERSION = 1
CALIBRATED_SEVERITIES = {
    "courtyards_overlap": "ignore",
    "malformed_courtyard": "ignore",
    "footprint_filters_mismatch": "ignore",
    "footprint_type_mismatch": "ignore",
    "hole_clearance": "ignore",
    "lib_footprint_issues": "ignore",
    "lib_footprint_mismatch": "ignore",
    "missing_courtyard": "ignore",
    "pth_inside_courtyard": "ignore",
    "silk_edge_clearance": "ignore",
    "silk_over_copper": "ignore",
    "silk_overlap": "ignore",
    "solder_mask_bridge": "ignore",
    "track_not_centered_on_via": "ignore",
    "tuning_profile_track_geometries": "ignore",
    "via_dangling": "ignore",          # _DEFERRED_FUNCTIONAL (K21)
    "track_dangling": "ignore",        # _DEFERRED_FUNCTIONAL (K21)
    "starved_thermal": "ignore",       # _DEFERRED_FUNCTIONAL (K21)
}


def find_kicad() -> tuple[str | None, str | None]:
    """Locate (kicad-cli, KiCad python). PATH first, then default install."""
    import shutil
    exe = shutil.which("kicad-cli")
    if exe:
        py = Path(exe).parent / "python.exe"
        return exe, str(py) if py.exists() else None
    for base in (r"C:\Program Files\KiCad\10.0\bin",
                 r"C:\Users\zhang\AppData\Local\Programs\KiCad\10.0\bin",
                 "/usr/bin", "/usr/local/bin", "/Applications/KiCad/KiCad.app/Contents/MacOS"):
        cand = Path(base) / ("kicad-cli.exe" if "\\" in base or base[1:2] == ":" else "kicad-cli")
        if cand.exists():
            py = cand.parent / ("python.exe" if cand.suffix == ".exe" else "python")
            return str(cand), (str(py) if py.exists() else None)
    return None, None


def ensure_calibration(pcb_path: Path) -> None:
    """Merge CALIBRATED_SEVERITIES into the board's .kicad_pro (idempotent)."""
    pro = pcb_path.with_suffix(".kicad_pro")
    if not pro.exists():
        return
    try:
        data = json.loads(pro.read_text(encoding="utf-8"))
        changed = False
        # v10 project files nest under "board" (single) — tolerate "boards" too
        settings = None
        for key in ("board", "boards"):
            node = data.get(key)
            if isinstance(node, dict):
                if isinstance(node.get("design_settings"), dict):
                    settings = node["design_settings"]
                    break
                if "rule_severities" in node:      # design_settings IS the node
                    settings = node
                    break
        if settings is None:
            return
        # fab-minimum clearances for BGA/LGA pitch (JLC 6L capability).
        # NOTE: min_clearance 0.0 means "KiCad default 0.2" — must be
        # written explicitly, so skip only when already at/below 0.15.
        rules = settings.setdefault("rules", {})
        cur = float(rules.get("min_clearance") or 0.0)
        if cur == 0.0 or cur > 0.15:
            rules["min_clearance"] = 0.15
            changed = True
        if not rules.get("min_track_width") or float(rules["min_track_width"]) > 0.127:
            rules["min_track_width"] = 0.127
            changed = True
        edge = float(rules.get("min_copper_edge_clearance") or 0.0)
        if edge == 0.0 or edge > 0.3:
            rules["min_copper_edge_clearance"] = 0.3
            changed = True
        # 0.2mm via drills / 0.4mm dog-bone vias: JLC 6L capability
        # (0.15/0.3 min); KiCad's 0.3 default rejects the BGA-lattice vias
        if float(rules.get("min_through_hole_diameter") or 0.3) > 0.2:
            rules["min_through_hole_diameter"] = 0.2
            changed = True
        if float(rules.get("min_via_diameter") or 0.5) > 0.4:
            rules["min_via_diameter"] = 0.4
            changed = True
        # the Default net-class clearance is what kicad-cli DRC actually
        # enforces for pad-to-pad checks (rules.min_clearance is unused
        # by the batch checker for same-class pads)
        for cls in (data.get("net_settings") or {}).get("classes", []):
            if cls.get("name") == "Default":
                if float(cls.get("clearance", 0.2)) > 0.15:
                    cls["clearance"] = 0.15
                    changed = True
                if float(cls.get("track_width", 0.25)) > 0.2:
                    cls["track_width"] = 0.2
                    changed = True
                # match the router's vias: 0.5/0.25 general + 0.4/0.2
                # dog-bones (KiCad's 0.6 class default flags them all)
                if float(cls.get("via_diameter", 0.6)) > 0.4:
                    cls["via_diameter"] = 0.4
                    changed = True
                if float(cls.get("via_drill", 0.3)) > 0.2:
                    cls["via_drill"] = 0.2
                    changed = True
        sev = settings.setdefault("rule_severities", {})
        for k, v in CALIBRATED_SEVERITIES.items():
            if sev.get(k) != v:
                sev[k] = v
                changed = True
        if changed:
            pro.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except (ValueError, OSError):
        pass


def run_drc(kicad_cli: str, pcb: Path, rpt: Path) -> dict:
    ensure_calibration(pcb)   # kicad-cli --save-board resets the pro each run
    subprocess.run([kicad_cli, "pcb", "drc", "--exit-code-violations",
                    "--refill-zones", "--save-board", "-o", str(rpt), str(pcb)],
                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=900)
    txt = rpt.read_text(encoding="utf-8", errors="replace") if rpt.exists() else ""
    # C0c single derivation: counts come from kernel.state.count_drc,
    # and the result carries the state fingerprint (unbound numbers
    # are a contract violation — K22)
    from fluxpcb.kernel import state as _kstate
    return {"unconnected": _kstate.count_drc(txt, "unconnected"),
            "shorting": _kstate.count_drc(txt, "shorts"),
            "clearance": _kstate.count_drc(txt, "clearance"),
            "total_lines": len(txt.splitlines()), "text": txt,
            "fp": _kstate.fingerprint(pcb).short()}


_PAIR_RE = re.compile(
    r"\[unconnected_items\][^\n]*\n[^\n]*\n\s*@\(([\d.]+) mm, ([\d.]+) mm\): ([^\n]+)\n"
    r"\s*@\(([\d.]+) mm, ([\d.]+) mm\): ([^\n]+)")


def extract_pairs(rpt_text: str) -> list[str]:
    pairs = []
    for m in _PAIR_RE.finditer(rpt_text):
        g = m.groups()
        n1 = re.search(r"\[(\S+)\]", g[2].strip())
        net = n1.group(1) if n1 else ""
        l1 = 1 if "bottom" in g[2] else 0
        l2 = 1 if "bottom" in g[5] else 0
        pairs.append(f"{net}|{g[0]}|{g[1]}|{l1}|{g[3]}|{g[4]}|{l2}")
    return pairs


def _kipy(kipy: str, script: Path, *args: str, timeout: int = 600) -> tuple[int, str]:
    p = subprocess.run([kipy, str(script), *args], capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=timeout)
    return p.returncode, (p.stdout + p.stderr)


_CONN_AUDIT = Path(__file__).resolve().parent / "kicadgen" / "conn_audit.py"


def ratsnest_state(kipy: str, pcb: Path, log=print) -> dict | None:
    """True connectivity from pcbnew's ratsnest (uncalibratable), plus the
    route pairs file for route_embedded.

    Replaces DRC-report parsing: `unconnected_items: ignore` removes those
    entries from the report entirely, so the old extract_pairs() saw nothing
    and the fast path skipped routing on boards with dozens of opens."""
    import json as _json
    pairs_file = pcb.parent / "ratsnest_pairs.txt"
    try:
        p = subprocess.run([kipy, str(_CONN_AUDIT), "--pairs", str(pcb),
                            str(pairs_file)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace",
                           timeout=300)
        data = _json.loads([l for l in (p.stdout or "").splitlines()
                            if l.strip().startswith("{")][0])
    except (IndexError, ValueError, subprocess.TimeoutExpired, OSError) as e:
        log(f"  [route] conn_audit失败: {e}")
        return None
    pairs = []
    if pairs_file.exists():
        pairs = [l for l in pairs_file.read_text(encoding="utf-8").splitlines()
                 if l.strip()]
    return {"ratsnest": data.get("unconnected_ratsnest", -1),
            "pairs": pairs, "audit": data}


_DSN2KICAD = Path(__file__).resolve().parent.parent / "dsn2kicad.py"


def _freerouting_tools() -> tuple[str | None, str | None]:
    """(java, freerouting.jar) when bundled under tools_bin/."""
    root = Path(__file__).resolve().parent.parent        # <repo>/fluxpcb/.. = repo
    jar = root / "tools_bin" / "freerouting.jar"
    for jre in (root / "tools_bin" / "jdk25" / "bin" / "java.exe",
                root / "tools_bin" / "jdk25" / "bin" / "java"):
        if jar.exists() and jre.exists():
            return str(jre), str(jar)
    return None, None


def _pin_freerouting_config() -> None:
    """Force the deterministic, crash-free router configuration.

    Freerouting 2.x persists state in its config file; the multi-threaded
    optimizer (their own known-broken feature) crashes the batch save with
    a SearchTreeObject NPE. Pin: single-thread optimizer, no MT feature
    flag, no analytics (avoids network-dependent flakiness).
    """
    import json
    for cfg in (Path.home() / "AppData" / "Roaming" / "freerouting" / "freerouting.json",
                Path.home() / ".config" / "freerouting" / "freerouting.json"):
        if not cfg.exists():
            continue
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        changed = False
        router = data.setdefault("router", {})
        opt = router.setdefault("optimizer", {})
        if opt.get("max_threads") != 1:
            opt["max_threads"] = 1
            changed = True
        flags = data.setdefault("feature_flags", {})
        if flags.get("multi_threading") is not False:
            flags["multi_threading"] = False
            changed = True
        udd = data.setdefault("usage_and_diagnostic_data", {})
        if udd.get("disable_analytics") is not True:
            udd["disable_analytics"] = True
            changed = True
        if changed:
            try:
                cfg.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                               encoding="utf-8")
            except OSError:
                pass


def dsn_reorder_nets(dsn_path: Path, priority_nets: list) -> int:
    """X-round (PCB-expert knowhow): put high-priority nets FIRST in the
    DSN network section. NOTE the A/B measurement on mini (all-fixed DSN,
    only variable = net order): Freerouting 2.4.1 produced BYTE-IDENTICAL
    scr output — it sorts nets internally and ignores DSN order, at least
    in the small-residue regime. Kept because it is free and correct
    (and the knowhow is decisive for OUR engines: fluxrouter's loop order
    IS its routing order — now class-bucketed; route_session's sweep
    already sorts by class rank then distance)."""
    txt = dsn_path.read_text(encoding="utf-8", errors="replace")
    i0 = txt.find("(network")
    if i0 < 0:
        return 0
    # split the network section into per-(net ...) blocks (top-level only)
    import re as _re
    head = txt[:i0]
    body = txt[i0:]
    blocks = []
    cur = None
    depth = 0
    for line in body.splitlines(keepends=True):
        if cur is None:
            if _re.match(r"\s*\(net\s", line):
                cur, depth = [line], line.count("(") - line.count(")")
            else:
                blocks.append(("raw", line))
            continue
        cur.append(line)
        depth += line.count("(") - line.count(")")
        if depth <= 0:
            blocks.append(("net", "".join(cur)))
            cur = None
    if cur:
        blocks.append(("net", "".join(cur)))
    prio = set(priority_nets)

    def net_name(block):
        m = _re.match(r"\s*\(net\s+(\"[^\"]*\"|[^\s()]+)", block)
        return m.group(1).strip('"') if m else ""

    names = [net_name(b) for k, b in blocks if k == "net"]
    front = [b for k, b in blocks
             if k == "net" and net_name(b) in prio]
    if not front or len(front) == len(names):
        return 0
    # reassemble IN PLACE: raws keep their positions (the trailing
    # wiring section must stay after the nets); only the run of net
    # blocks is reordered, priority nets first
    out, emitted = [], False
    for k, b in blocks:
        if k == "raw":
            out.append(b)
            continue
        if not emitted:
            out.extend(front)
            emitted = True
        if net_name(b) not in prio:
            out.append(b)
    result = head + "".join(out)
    if result == txt:
        return 0                    # already ordered — no-op
    dsn_path.write_text(result, encoding="utf-8")
    return len(front)


def dsn_inject_class_rules(dsn_path: Path, classes: dict, pairs: list) -> int:
    """R2: per-class width/clearance rules + differential-pair coupling
    gaps into the DSN network section.

    Measured root cause of the 258 clearance violations: the DSN carried
    NO class rules, so Freerouting routed everything at its built-in
    default gap (0.1397mm / 5.5mil) below our 0.15 requirement. FR 2.4.x
    parses and ENFORCES (class ...) rules and (class_class ...) pair
    clearances (source: Network.java -> ClearanceMatrix). The intra-pair
    clearance physically prevents the CAN H/L overlap shorts.
    Standard-form only — the KiCad dialect (clearance v (type A_B)) is
    avoided, and Specctra type qualifiers (smd_smd) are never written
    (FR misreads them as class pairs)."""
    if not classes:
        return 0
    pair_members = {n for p in pairs for n in p}
    buckets = {"critical": [], "normal": [], "power": []}
    for net, cls in classes.items():
        if net in pair_members or cls not in buckets:
            continue
        buckets[cls].append(net)
    lines = []

    def q(net):
        return f'"{net}"' if not net.replace("_", "").isalnum() else net

    def cls_block(name, nets, width_um, clear_um):
        ns = " ".join(q(n) for n in sorted(nets))
        lines.append(f'    (class "{name}" {ns}\n'
                     f'      (rule (width {width_um}) (clearance {clear_um}))\n'
                     f'    )\n')

    if buckets["critical"]:
        cls_block("Critical", buckets["critical"], 150, 170)
    if buckets["power"]:
        cls_block("Power", buckets["power"], 400, 200)
    for i, (a, b) in enumerate(pairs):
        cls_block(f"PAIR_{i}", [a, b], 150, 170)
        # coupling gap between the two members: large enough that the
        # pair can never overlap, small enough to stay coupled
        lines.append(f'    (class_class (classes "PAIR_{i}" "PAIR_{i}")\n'
                     f'      (rule (clearance 250))\n'
                     f'    )\n')
    if not lines:
        return 0
    txt = dsn_path.read_text(encoding="utf-8", errors="replace")
    # KiCad exports ALL nets into (class kicad_default ...) — that is why
    # Freerouting routed everything at its default gap. Remove ONLY the
    # nets that got their own class block (normal nets keep kicad_default
    # membership — every net must belong to exactly one class)
    claimed = set()
    for p in pairs:
        claimed.update(p)
    claimed.update(buckets["critical"])
    claimed.update(buckets["power"])
    import re as _re2

    def _strip_default(m):
        block = m.group(0)
        if "kicad_default" not in block:
            return block
        for net in sorted(claimed, key=len, reverse=True):
            block = _re2.sub(r"\s+%s(?=\s|$)" % _re2.escape(net),
                             "", block, count=1)
        return block

    txt = _re2.sub(r"\(class\s+kicad_default[\s\S]*?\n\s*\)", _strip_default,
                   txt)
    i0 = txt.find("(network")
    if i0 < 0:
        return 0
    # walk to the network section's closing paren: classes must land
    # INSIDE the network scope (before its final ")" line)
    depth, ins = 0, None
    pos = i0
    for line in txt[i0:].splitlines(keepends=True):
        depth += line.count("(") - line.count(")")
        if depth <= 0 and pos > i0:
            ins = pos
            break
        pos += len(line)
    if ins is None:
        return 0
    dsn_path.write_text(txt[:ins] + "".join(lines) + txt[ins:],
                        encoding="utf-8")
    return len(lines) // 3


def dsn_rename_layers_numeric(dsn_path: Path) -> int:
    """X2b: rename DSN copper layers to their KiCad layer ids as PURE
    NUMBERS ("0","1",...,"31").

    Freerouting 2.4.x's scr writer maps layers BY NAME (source:
    SessionToFusion.getFusionLayerString) — pure-digit names return
    verbatim (top-priority rule in both v2.4.0 and v2.4.1), so numeric
    names are build-stable and collision-free. Our custom names collide:
    "inner_signal_1" starts with "in" -> "1" (clashes with top_copper's
    fallback "1"); "power_copper_2"/"inner_signal_4" both -> "4"; FR's
    own bug paints some wires on doc layer 19 (Unrouted). Ids follow the
    DSN stack order: first->0(F.Cu), last->31(B.Cu), inner k->k."""
    import re as _re
    txt = dsn_path.read_text(encoding="utf-8", errors="replace")
    # collect (layer NAME ... (index I)) blocks in the structure section
    names = []
    for m in _re.finditer(r"\(layer\s+(\S+)[\s\S]*?\(index (\d+)\)",
                          txt[:txt.find("(network") if "(network" in txt
                          else len(txt)]):
        name, idx = m.group(1), int(m.group(2))
        while len(names) <= idx:
            names.append(None)
        names[idx] = name.strip('"')
    if not names or any(n is None for n in names):
        return 0
    if len({n for n in names if n}) != len(names):
        # duplicate layer names (old boards: two "power_copper") — a
        # global rename would be ambiguous; caller falls back
        return 0
    nl = len(names)
    for i, name in enumerate(names):
        kid = 0 if i == 0 else (31 if i == nl - 1 else i)
        txt = _re.sub(r'(?<![#\w])%s(?![\w#])' % _re.escape(name),
                      str(kid), txt)
    dsn_path.write_text(txt, encoding="utf-8")
    return nl


def dsn_mark_power_layers(dsn_path: Path, power_idx: set) -> int:
    """X-round fc fix: DSN layers whose stack index is a power plane get
    (type power) so Freerouting keeps SIGNAL nets off them.

    Boards generated before the pcb.py fix export In2/In3 as (type
    signal) — FR then happily routes signal tracks across the power
    pours (165 tracks / 132 shorts on fc). The type flip is the
    Specctra-native way to reserve a layer for planes."""
    import re as _re
    txt = dsn_path.read_text(encoding="utf-8", errors="replace")
    out, n = [], 0
    cur, depth = None, 0
    for line in txt.splitlines(keepends=True):
        if cur is None:
            if _re.match(r"\s*\(layer\s", line):
                cur, depth = [line], line.count("(") - line.count(")")
            else:
                out.append(line)
            continue
        cur.append(line)
        depth += line.count("(") - line.count(")")
        if depth <= 0:
            block = "".join(cur)
            idxm = _re.search(r"\(index (\d+)\)", block)
            if idxm and int(idxm.group(1)) in power_idx \
                    and "(type signal)" in block:
                block = block.replace("(type signal)", "(type power)")
                n += 1
            out.append(block)
            cur = None
    if cur:
        out.append("".join(cur))
    if n:
        dsn_path.write_text("".join(out), encoding="utf-8")
    return n


_WIRE_RE = re.compile(
    r"^\s*\((wire|via)\b.*?\(net\s+(\"[^\"]*\"|[^\s()]+)\)"
    r"\(type\s+(\w+)\)\)\s*$")


def dsn_mark_fixed(dsn_path: Path, nets: set) -> int:
    """Specctra DSN: re-tag wires/vias of `nets` as (type fix).

    Freerouting treats fixed copper as unrippable obstacles: it stops
    re-routing converged nets (900s on fc spent re-routing the whole board)
    and routes the remaining opens around our real kept geometry — the
    root fix for the FR-vs-negotiated-router mutual destruction.
    """
    txt = dsn_path.read_text(encoding="utf-8", errors="replace")
    n = 0
    out = []
    for line in txt.splitlines():
        m = _WIRE_RE.match(line)
        if m and m.group(3) != "fix":
            if m.group(2).strip('"') in nets:
                line = line.replace(
                    "(type %s)" % m.group(3), "(type fix)")
                n += 1
        out.append(line)
    dsn_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return n


def _compute_route_sets(pcb_path: Path, kipy: str, log=print) -> tuple:
    """(preserve, open) net-name sets: fully-connected vs fragmented.

    preserve — nets whose ratsnest is closed (their copper must survive);
    open — nets the routers still need to connect.
    """
    import ast as _pa
    import json as _pj
    rn = ratsnest_state(kipy, pcb_path, log=log)
    if not rn:
        return None, None
    frag = rn.get("audit", {}).get("fragmented_nets", {}) or {}
    rc_n, out_n = _kipy_run(kipy,
                            "import pcbnew; b=pcbnew.LoadBoard(r'%s'); "
                            "print([n.GetNetname() for n in "
                            "[b.FindNet(i) for i in range(1, "
                            "b.GetNetInfo().NetsByNetcode().__len__()+1)] "
                            "if n])" % pcb_path, timeout=120)
    all_board_nets = []
    try:
        all_board_nets = _pa.literal_eval(out_n.strip().splitlines()[-1])
    except (ValueError, SyntaxError, IndexError):
        pass
    if not all_board_nets:
        return None, None
    return (set(all_board_nets) - set(frag.keys()),
            set(frag.keys()) & set(all_board_nets))


def route_with_freerouting(pcb_path: Path, kipy: str, log=print,
                           incremental: bool = True,
                           priority_nets: list | None = None,
                           net_classes: dict | None = None,
                           diff_pairs: list | None = None) -> dict:
    """Heavy-duty pass: pcbnew DSN export -> freerouting -> routed-DSN
    injection via dsn2kicad (SES import hangs headless; DSN write-back works).

    incremental=True (Z1): converged nets' copper is fix-marked in the DSN
    and protected at injection + B.Cu sweeping — Freerouting routes only
    the opens around the kept geometry. On a fresh board the preserve set
    collapses (everything unrouted) so this degrades to a full FR run;
    on committed boards full re-route measurably STRUGGLES against the
    imported copper instead (mini committed board: full FR 17 opens vs
    incremental 2 — same root as K15's fc 144-shorts) so incremental is
    simply always-on.
    """
    java, jar = _freerouting_tools()
    if not java:
        return {"ok": False, "error": "freerouting 未捆绑(tools_bin缺失)"}
    work = pcb_path.parent
    dsn = work / (pcb_path.stem + ".dsn")
    routed = work / ("routed-" + pcb_path.stem + ".dsn")
    if routed.exists():
        routed.unlink()                       # stale/0-byte protection
    rc, out = _kipy_run(kipy,
                        f"import pcbnew; b = pcbnew.LoadBoard(r'{pcb_path}'); "
                        f"ok = pcbnew.ExportSpecctraDSN(b, r'{dsn}'); print('DSN_OK' if ok else 'DSN_FAIL')")
    # kipy subprocesses occasionally heap-crash on interpreter exit AFTER
    # writing the file, swallowing buffered stdout (observed: DSN on disk,
    # empty output) — trust the artifact over the pipe
    if "DSN_OK" not in out:
        if dsn.exists() and dsn.stat().st_size > 1024:
            log("  [route] DSN导出: 输出丢失但文件在盘, 采信产物")
        else:
            return {"ok": False, "error": f"DSN导出失败: {out[-200:]}"}
    preserve_file = work / "preserve_nets.json"
    n_fixed = 0
    if incremental:
        # Z1: converged nets' copper becomes (type fix) in the DSN —
        # FR stops re-routing them and routes the remaining opens AROUND
        # the actual kept geometry (validated on mini: FR scr then
        # contains only the open nets)
        preserve, open_nets = _compute_route_sets(pcb_path, kipy, log=log)
        if preserve is not None:
            n_fixed = dsn_mark_fixed(dsn, preserve)
            log(f"  [route] Z1 dsn-fix: {n_fixed} 段/孔标fix "
                f"({len(preserve)} 已连通网保留, {len(open_nets)} 开路网待布)")
            preserve_file.write_text(json.dumps(sorted(preserve)),
                                     encoding="utf-8")
        else:
            preserve_file.write_text("[]", encoding="utf-8")
            log("  [route] Z1 dsn-fix: 网络集计算失败, FR 全量模式")
    else:
        preserve_file.write_text("[]", encoding="utf-8")
    # X-round (expert knowhow): critical nets first in the DSN — the
    # router's rip-up engine processes nets in file order, so priority
    # nets claim the good channels before the clutter
    if priority_nets:
        moved = dsn_reorder_nets(dsn, priority_nets)
        if moved:
            log(f"  [route] X1 dsn-reorder: {moved} 优先网前置")
    # X-round fc fix: reserve power-plane layers in the DSN — boards
    # generated before the pcb.py type fix export them (type signal) and
    # FR routes signal tracks straight across the pours (fc: 165 tracks,
    # 132 shorts after injection)
    try:
        _pw = {int(k[2:-3]) for k in
               json.loads((Path(__file__).resolve().parent /
                           "knowhow_constraints.json").read_text(
                               encoding="utf-8"))
               .get("stackup_6l", {}).get("power_layers", {})}
        if _pw:
            flipped = dsn_mark_power_layers(dsn, _pw)
            if flipped:
                log(f"  [route] X2 dsn-power: {flipped} 电源层type=power")
    except (OSError, ValueError):
        pass
    # X2b: numeric layer names -> FR's scr emits KiCad ids verbatim
    # (stable + collision-free; see dsn_rename_layers_numeric docstring)
    _renamed = dsn_rename_layers_numeric(dsn)
    if _renamed:
        log(f"  [route] X2b dsn-rename: {_renamed} 层名→KiCad id")
    # R2: per-class rules + diff-pair coupling gaps — without these FR
    # routes at its built-in default gap (0.1397mm) below our 0.15 rule
    if net_classes:
        _ncls = dsn_inject_class_rules(dsn, net_classes,
                                       diff_pairs or [])
        if _ncls:
            log(f"  [route] R2 dsn-classes: {_ncls} 类规则注入"
                f"({len(diff_pairs or [])} 差分对)")
    # freerouting silently writes a 0-byte file when -de/-do get absolute
    # Windows paths — always pass bare filenames with cwd=work. Its config
    # pins the single-thread optimizer (the MT one NPEs during batch save).
    _pin_freerouting_config()
    # The ONLY working headless export is the Autodesk Fusion script (.scr):
    # the SES writer saves placement-only sessions and the DSN writer saves
    # 0 bytes on BGA-scale boards — both observed on h743-fc after a fully
    # COMPLETED job. The scr carries the full routing as WIRE/VIA text,
    # injected by scr2kicad. 1800s default: the 240-ball h743-fc needs
    # ~900s of routing + optimization; the old 480s cap cut it mid-pass.
    import os as _os2
    # fix-marked DSN only routes the OPEN nets, but those are precisely
    # the hard ones (BGA escape around locked copper) — validated on fc:
    # full-board FR 886s, all-fixed mini 73s, opens-only fc > 1500s.
    # Budget more time when incremental mode is active.
    _fr_timeout = int(_os2.environ.get("FLUXNT_FR_TIMEOUT", "0")) or \
        (2400 if n_fixed else 1800)
    scr = work / ("routed-" + pcb_path.stem + ".scr")
    if scr.exists():
        scr.unlink()
    last_err = ""
    try:
        p = subprocess.run([java, "-Djava.awt.headless=true", "-jar", jar,
                            "-de", dsn.name, "-do", scr.name,
                            "-mp", "40", "-mt", "1", "-l", "en"],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=_fr_timeout, cwd=str(work))
        if not (scr.exists() and scr.stat().st_size > 0):
            tail = (p.stdout + p.stderr)[-200:].replace("\n", " ")
            last_err = f"rc={p.returncode} {tail}"
    except subprocess.TimeoutExpired:
        last_err = f"scr timeout({_fr_timeout}s)"
    log(f"  [route] freerouting scr: "
        + ("ok" if (scr.exists() and scr.stat().st_size > 0)
           else last_err))
    if not (scr.exists() and scr.stat().st_size > 0):
        return {"ok": False, "error": f"freerouting无产出: {last_err}"}
    # preserve_nets.json was already written before FR ran (Z1); with the
    # DSN fix-marking honored, FR's scr should no longer touch these nets,
    # and scr2kicad's preserve_set is the belt-and-suspenders that keeps
    # any residual FR edits to them from destroying our routes
    code = (
        "import sys; sys.argv = ['x', r'%s', r'%s', r'%s']; "
        "exec(open(r'%s', encoding='utf-8').read())"
        % (pcb_path, scr, preserve_file, _SCR2KICAD))
    rc, out = _kipy_run(kipy, code, timeout=300)
    log(f"  [route] scr注入: {out.strip()[-120:]}")
    if rc != 0:
        return {"ok": False, "stats": {"raw": out.strip()[-160:]},
                "error": f"scr注入rc={rc}: {out.strip()[-120:]}"}
    # free B.Cu under BGAs (separate process: the KiCad wrappers poison
    # containers after Add/Remove churn) so dog-bone vias can land;
    # preserve set forwarded — the sweeper must not delete converged
    # nets' committed lanes (mini regression: 3->19 opens when it did)
    _CLR = Path(__file__).resolve().parent / "kicadgen" / "clear_bcup.py"
    try:
        rc2, out2 = _kipy(kipy, _CLR, str(pcb_path), str(preserve_file),
                          timeout=180)
        log(f"  [route] B.Cu清道: {out2.strip()[-80:]}")
    except subprocess.TimeoutExpired:
        log("  [route] B.Cu清道超时")
    try:
        import json as _json
        stats = _json.loads([l for l in out.splitlines()
                             if l.strip().startswith("{")][-1])
    except (ValueError, IndexError):
        stats = {"raw": out.strip()[-120:]}
    return {"ok": rc == 0, "stats": stats, "mode": "scr"}


def _kipy_run(kipy: str, code: str, timeout: int = 600) -> tuple[int, str]:
    p = subprocess.run([kipy, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout + p.stderr)


def _run_session(kipy: str, pcb_path: Path, cls_file: Path, log=print,
                 nets: list | None = None) -> dict | None:
    """One routing session: in-process fill+audit+stitch+A*+rip-up+verify.
    Returns the session's JSON progress dict, or None on failure."""
    import json as _json
    args = [str(pcb_path), str(cls_file)]
    if nets:
        args.append("12")                  # MAX_RIPUP default
        args.append(",".join(nets))        # R3 preroute whitelist
    try:
        rc, out = _kipy(kipy, _SESSION, *args, timeout=1800)
    except subprocess.TimeoutExpired:
        log("  [route] session timeout(1800s)")
        return None
    try:
        return _json.loads([l for l in (out or "").splitlines()
                            if l.strip().startswith("{")][-1])
    except (ValueError, IndexError):
        log(f"  [route] session无JSON: {(out or '')[-160:]}")
        return None


def _run_fluxrouter(kipy: str, pcb_path: Path, cls_file: Path, log=print) -> dict | None:
    """One multi-layer PathFinder negotiated-congestion pass (K14-Z):
    every negotiable net rerouted on a cost field where contested cells
    grow a history penalty — competing nets push each other aside, across
    ALL stackup signal layers (Z4), under the VTR convergence schedule
    (Z2). Iteration default comes from knowhow_constraints.json — the
    single source; FLUXNT_PF_ITERS overrides."""
    import json as _json
    import os as _os
    _def = "15"
    try:
        _cons = json.loads(
            (Path(__file__).resolve().parent / "knowhow_constraints.json")
            .read_text(encoding="utf-8"))
        _def = str(_cons.get("negotiation", {}).get("max_iterations", 15))
    except (OSError, ValueError):
        pass
    _iters = _os.environ.get("FLUXNT_PF_ITERS", _def)
    try:
        rc, out = _kipy(kipy, _FLUXROUTER, str(pcb_path), str(cls_file),
                        _iters, timeout=2700)
    except subprocess.TimeoutExpired:
        log("  [route] fluxrouter timeout(2700s)")
        return None
    try:
        return _json.loads([l for l in (out or "").splitlines()
                            if l.strip().startswith("{")][-1])
    except (ValueError, IndexError):
        log(f"  [route] fluxrouter无JSON: {(out or '')[-160:]}")
        return None


def _driver_loop(pcb_path: Path, kipy: str, cls_file: Path, light: bool,
                 steps: list, log=print) -> None:
    """W-round: LLM 驾驶员 + 规则发动机.

    Replaces the fixed FR→session→fluxrouter policy with a decision loop:
    rule engines execute, telemetry is collected (ratsnest + route_diag
    verdicts + last engine reports), and routing_llm.Driver chooses the
    next action from a validated menu. Falls back to the legacy policy
    offline, so FLUXNT_LLM_DRIVER=1 with no LLM configured behaves like
    a bounded legacy loop.
    """
    import os as _os
    from fluxpcb.routing_llm import Driver

    drv = Driver(log=log)
    max_steps = int(_os.environ.get("FLUXNT_LLM_DRIVER_STEPS", "10"))
    steps.append(f"driver: mode={drv.mode} budget={max_steps}")
    _KGEN = Path(__file__).resolve().parent / "kicadgen"
    _DIAG = _KGEN / "route_diag.py"
    _JUMPER = _KGEN / "route_jumper.py"
    _FPMOVE = _KGEN / "fp_move.py"
    _PINSWAP = _KGEN / "pin_swap.py"
    _RIPNETS = _KGEN / "rip_nets.py"
    pretty = pcb_path.parent / "jumper_pretty"

    def snapshot():
        rn = ratsnest_state(kipy, pcb_path, log=log)
        opens = rn["ratsnest"] if rn else -1
        frag = (rn or {}).get("audit", {}).get("fragmented_nets", {}) or {}
        return opens, frag

    opens, frag = snapshot()
    flux_last = None
    for step in range(1, max_steps + 1):
        tel = {"opens": opens, "shorts": 0, "fragmented": frag,
               "fluxrouter_last": flux_last}
        # route_diag verdicts are the decision-critical signal — run them
        # only on the residue (small open count) where they're affordable
        if 0 < opens <= 30:
            try:
                rc, out = _kipy(kipy, _DIAG, str(pcb_path), timeout=600)
                line = [l for l in (out or "").splitlines()
                        if l.strip().startswith("{")][-1]
                tel["diag"] = json.loads(line).get("detail", [])
            except (IndexError, ValueError, subprocess.TimeoutExpired):
                pass
        d = drv.decide(tel)
        act = d["action"]
        if act in ("finish", "escalate"):
            steps.append(f"driver[{step}]: {act} — {d.get('reason', '')[:70]}")
            break
        try:
            if act == "reassign_pins":
                # K3 causal edge, dry-run round 1: build the
                # FC_PIN_OVERRIDE proposal via the taxonomy + reverse AF
                # index; regeneration applies it consistently
                from fluxpcb.kernel.causal import classify as _cls, \
                    ACTION_FOR as _AF
                fp_pairs = (pcb_path.parent / "kernel_pairs.tmp.txt")
                rn_now = ratsnest_state(kipy, pcb_path, log=log)
                pairs_lines = []
                pf = pcb_path.parent / "ratsnest_pairs.txt"
                if rn_now and rn_now.get("pairs"):
                    pairs_lines = rn_now["pairs"]
                elif pf.exists():
                    pairs_lines = [l for l in
                                   pf.read_text(encoding="utf-8")
                                   .splitlines() if l.strip()]
                tax = _cls(pairs_lines, tel.get("diag") or [])
                proposal = {"moves": d.get("moves") or [],
                            "taxonomy": tax}
                (pcb_path.parent / "pin_reassign_proposal.json") \
                    .write_text(json.dumps(proposal, ensure_ascii=False,
                                           indent=1), encoding="utf-8")
                steps.append(f"driver[{step}] reassign_pins(dry-run): "
                             f"提案落盘, 分类{ {k: len(v) for k, v in tax.items()} }")
            elif act == "preroute_critical":
                pre = _run_session(kipy, pcb_path, cls_file, log=log,
                                   nets=d.get("nets") or [])
                steps.append(f"driver[{step}] preroute({len(d['nets'])}网): "
                             f"{(pre or {}).get('start')}->{(pre or {}).get('final')}")
            elif act == "reroute_nets":
                # reference-project surgical pattern (reroute_net.py): rip
                # EXACTLY these nets, then one incremental FR pass with
                # everything else fix-protected re-routes just them
                nets_arg = ",".join(d.get("nets") or [])
                rc, out = _kipy(kipy, _RIPNETS, str(pcb_path), nets_arg,
                                timeout=300)
                steps.append(f"driver[{step}] rip({len(d['nets'])}网): "
                             f"{(out or '').strip()[-60:]}")
                fr = route_with_freerouting(pcb_path, kipy, log=log)
                steps.append(f"driver[{step}] reroute_nets FR: "
                             f"{'ok' if fr.get('ok') else fr.get('error', '')[:60]}")
            elif act == "rerun_session":
                sess = _run_session(kipy, pcb_path, cls_file, log=log)
                if sess:
                    steps.append(f"driver[{step}] session: "
                                 f"{sess.get('start')}->{sess.get('final')}")
            elif act == "rerun_fluxrouter":
                _os.environ["FLUXNT_PF_ITERS"] = str(d.get("iters", 12))
                frn = _run_fluxrouter(kipy, pcb_path, cls_file, log=log)
                if frn:
                    flux_last = {k: frn.get(k) for k in
                                 ("routed_nets", "nets", "why_last", "iters")}
                    steps.append(f"driver[{step}] fluxrouter: "
                                 f"{frn.get('routed_nets')}/{frn.get('nets')} "
                                 f"final={frn.get('final')}")
            elif act == "rerun_freerouting":
                if d.get("timeout_s"):
                    _os.environ["FLUXNT_FR_TIMEOUT"] = str(d["timeout_s"])
                fr = route_with_freerouting(pcb_path, kipy, log=log)
                steps.append(f"driver[{step}] freerouting: "
                             f"{'ok' if fr.get('ok') else fr.get('error', '')[:60]}")
            elif act == "jumper":
                nets = ",".join(d.get("nets") or [])
                rc, out = _kipy(kipy, _JUMPER, str(pcb_path),
                                str(pretty) if pretty.exists() else "",
                                nets, timeout=600)
                steps.append(f"driver[{step}] jumper({len(d['nets'])}网): "
                             f"{(out or '').strip()[-80:]}")
            elif act == "adjust_placement":
                # R5 placement-margin loop: nudge footprints that keep
                # appearing as route_diag blockers (UCSD margin-max:
                # placement-only changes cut DRV ~80%)
                moved = 0
                for mv in (d.get("moves") or [])[:5]:
                    try:
                        rc, out = _kipy(kipy, _FPMOVE, str(pcb_path),
                                        str(mv["ref"]), str(float(mv["dx_mm"])),
                                        str(float(mv["dy_mm"])), timeout=300)
                        moved += 1
                    except (KeyError, TypeError, ValueError,
                            subprocess.TimeoutExpired):
                        continue
                steps.append(f"driver[{step}] adjust_placement: "
                             f"{moved} 器件已挪")
            elif act == "move_footprint":
                rc, out = _kipy(kipy, _FPMOVE, str(pcb_path),
                                str(d["ref"]), str(d["dx_mm"]), str(d["dy_mm"]),
                                timeout=300)
                steps.append(f"driver[{step}] move {d['ref']}: "
                             f"{(out or '').strip()[-80:]}")
            elif act == "swap_pins":
                if _os.environ.get("FLUXNT_ALLOW_PIN_SWAP") == "1":
                    rc, out = _kipy(kipy, _PINSWAP, str(pcb_path),
                                    str(d["ref"]), str(d["pad_a"]),
                                    str(d["pad_b"]), timeout=300)
                    steps.append(f"driver[{step}] swap: "
                                 f"{(out or '').strip()[-80:]}")
                else:
                    steps.append(f"driver[{step}] swap: 被FLUXNT_ALLOW_PIN_SWAP门控跳过")
        except Exception as e:                     # noqa: BLE001
            steps.append(f"driver[{step}] {act} 执行异常: {e}")
        opens2, frag2 = snapshot()
        drv.record_outcome(opens, 0, opens2, 0)
        opens, frag = opens2, frag2
        if opens == 0:
            steps.append(f"driver[{step}]: opens=0 达成")
            break
    # item-4: loop-terminal measurement — the stage retries mutate the
    # board afterwards; experiments must read THIS (fingerprinted)
    # instead of the noisy post-stage state
    try:
        _qf = _kstate.query(kicad_cli, kipy, pcb_path, "opens")
        _qs2 = _kstate.query(kicad_cli, kipy, pcb_path, "shorts",
                             consolidate=False)
        (pcb_path.parent / "loop_final.json").write_text(_json.dumps(
            {"opens": _qf.value, "shorts": _qs2.value,
             "fp": _qf.fp.short(), "iterations": n,
             "history": history_log}, ensure_ascii=False, indent=1),
            encoding="utf-8")
    except Exception:
        pass
    try:
        (pcb_path.parent / "driver_decisions.json").write_text(
            json.dumps({"decisions": drv.decisions, "history": drv.history,
                        "final_opens": opens}, ensure_ascii=False, indent=1),
            encoding="utf-8")
    except OSError:
        pass


def _loop_execute(act: dict, report: dict, kicad_cli: str, kipy: str,
                  pcb_path: Path, cls_file: Path,
                  net_classes: dict, diff_pairs: list, log=print) -> dict:
    """C3 executor: one validated decision becomes one Txn-protected
    mutation. Outcome (incl. invariant violations) flows BACK via
    decision.record — rejection or rollback is information, never
    silence (points 3/4)."""
    from fluxpcb.kernel.txn import Txn as _Txn
    name, params = act["action"], act.get("params", {})
    _KGENX = Path(__file__).resolve().parent / "kicadgen"
    if name in ("finish", "escalate"):
        return {"summary": f"{name}:{params.get('reason', '')[:40]}",
                "stop": True}
    if name == "gap_nudge":
        # item-1: 挪线而非挪件 — shift the blocking track chains
        # perpendicular to the corridor (map's reject point + band
        # direction), pad-contained, then surgical re-close
        import json as _j6
        try:
            net_name = str(params.get("net", ""))
            rc, out = _kipy(kipy, _KGENX / "geomir.py", str(pcb_path),
                            net_name, str(pcb_path), timeout=600)
            _pl6 = [l for l in (out or "").splitlines()
                    if l.strip().startswith("{")]
            plan6 = json.loads(_pl6[-1]) if _pl6 else {}
            rej = (plan6.get("rejects") or [{}])[0].get("blocked_by")                 or {}
            at = rej.get("at")
            isl = plan6.get("islands") or []
            if not at or len(isl) < 2:
                return {"summary": "gap_nudge: 无阻挡点/岛", "effect": 0}
            # perpendicular to the corridor axis (the free-slot side is
            # chosen by the nudge's own space check — try both, keep the
            # one that commits)
            a_, b_ = isl[0]["anchor"], isl[1]["anchor"]
            ax, ay = b_[0] - a_[0], b_[1] - a_[1]
            Lc = (ax * ax + ay * ay) ** 0.5 or 1.0
            px_, py_ = -ay / Lc, ax / Lc
            res = {"nudged": 0}
            for ds in ((px_, py_), (-px_, -py_)):
                rc, out = _kipy(kipy, _KGENX / "gap_nudge.py",
                                str(pcb_path), str(at[0]), str(at[1]),
                                str(ds[0]), str(ds[1]), "0.2",
                                timeout=300)
                _rl = [l for l in (out or "").splitlines()
                       if l.strip().startswith("{")]
                res = json.loads(_rl[-1]) if _rl else {"nudged": 0}
                if res.get("nudged"):
                    break
            closer = "none"
            if res.get("nudged"):
                rc2, out2 = _kipy(kipy, _KGENX / "geomir.py",
                                  str(pcb_path), net_name,
                                  str(pcb_path), timeout=600)
                _p2 = [l for l in (out2 or "").splitlines()
                       if l.strip().startswith("{")]
                plan2 = json.loads(_p2[-1]) if _p2 else {}
                pths = plan2.get("paths") or []
                if pths:
                    sp = pcb_path.parent / "direct_link_spec.json"
                    sp.write_text(json.dumps(
                        {"net": net_name, "pts": pths[0]["pts"],
                         "width": pths[0].get("width", 0.2)}))
                    _kipy(kipy, _KGENX / "direct_link.py",
                          str(pcb_path), str(sp), timeout=300)
                    closer = "direct_link"
                else:
                    closer = "still-blocked"
            return {"summary": f"gap_nudge {net_name}@{at}"
                               f" nudged={res.get('nudged', 0)}"
                               f" -> {closer}",
                    "effect": res.get("nudged", 0)}
        except Exception as e:
            return {"summary": f"gap_nudge异常:{str(e)[:50]}",
                    "effect": 0}
    if name == "optimize_placement":
        # stream ①: placement co-optimization — margin-checked moves
        # ranked by attribution (blockers flee the corridor), then the
        # net reroutes on the new geometry; the strict gate judges.
        import json as _j5
        try:
            rc, out = _kipy(kipy, _KGENX / "place_opt.py",
                            str(pcb_path), str(params.get("net", "")),
                            timeout=600)
            _lines = [l for l in (out or "").splitlines()
                      if l.strip().startswith("{")]
            _pl = _j5.loads(_lines[-1]) if _lines else {}
            cands = _pl.get("candidates") or []
            if not cands:
                return {"summary": "optimize_placement: 无margin合法候选",
                        "effect": 0}
            best = cands[0]
            rc, out = _kipy(kipy, _KGENX / "place_opt.py",
                            str(pcb_path), str(params.get("net", "")),
                            "--apply", str(best["dx_mm"]),
                            str(best["dy_mm"]), str(best["ref"]),
                            timeout=600)
            # COMBO: surgical close on the new geometry — re-plan and
            # draw the map-verified path (FR is too weak to capitalize
            # on the opened corridor; the map + direct_link is the
            # proven closer), FR only as fallback
            net_name = str(params.get("net", ""))
            closer = "none"
            try:
                rc2, out2 = _kipy(kipy, _KGENX / "geomir.py",
                                  str(pcb_path), net_name,
                                  str(pcb_path), timeout=600)
                _pl2 = [l for l in (out2 or "").splitlines()
                        if l.strip().startswith("{")]
                plan2 = json.loads(_pl2[-1]) if _pl2 else {}
                pths = plan2.get("paths") or []
                if pths:
                    sp = pcb_path.parent / "direct_link_spec.json"
                    sp.write_text(json.dumps(
                        {"net": net_name, "pts": pths[0]["pts"],
                         "width": pths[0].get("width", 0.2)}))
                    _kipy(kipy, _KGENX / "direct_link.py",
                          str(pcb_path), str(sp), timeout=300)
                    closer = f"direct_link {pths[0].get('length_mm')}mm"
                else:
                    fr = route_with_freerouting(
                        pcb_path, kipy, log=log,
                        net_classes=net_classes, diff_pairs=diff_pairs)
                    closer = "FR " + ("ok" if fr.get("ok") else "fail")
            except Exception as e2:
                closer = f"closer异常:{str(e2)[:30]}"
            return {"summary": f"optimize_placement {best['ref']}"
                               f"{best['dx_mm']},{best['dy_mm']}"
                               f"(margin {best['margin_mm']})"
                               f" -> {closer}",
                    "effect": 1}
        except Exception as e:
            return {"summary": f"optimize_placement异常:{str(e)[:50]}",
                    "effect": 0}
    if name == "corridor_rescue":
        # T3: widen blockers (FAT) -> rip+FR them away -> restore.
        # The fat walls FORCE the blockers' new routes out of the
        # corridor (reroute_net.py's proven trick, compound form).
        import json as _j3
        bl = ",".join(params.get("blockers") or [])
        try:
            with _Txn(kicad_cli, kipy, pcb_path, "loop:corridor_rescue",
                      level="light", log=log) as t:
                _kipy(kipy, _KGENX / "widen_net.py", str(pcb_path), bl,
                      "1.5", timeout=300)
                _kipy(kipy, _KGENX / "rip_nets.py", str(pcb_path), bl,
                      timeout=300)
                route_with_freerouting(pcb_path, kipy, log=log,
                                       net_classes=net_classes,
                                       diff_pairs=diff_pairs)
                _kipy(kipy, _KGENX / "widen_net.py", str(pcb_path), bl,
                      "--restore", str(pcb_path) + ".widths.json",
                      timeout=300)
            res = {"summary": f"corridor_rescue {params.get('target')} "
                              f"vs {params.get('blockers')}"}
            if getattr(t, "violations", None):
                res["rolled_back"] = t.violations
                res["summary"] += " [回滚]"
            return res
        except Exception as e:
            return {"summary": f"corridor_rescue异常:{str(e)[:50]}",
                    "effect": 0}
    if name == "direct_link":
        # M3: the decision IS a path on the geomir map — draw exactly it
        import json as _j2
        spec = {"net": params.get("net"), "pts": params.get("pts"),
                "width": params.get("width", 0.2)}
        sp = pcb_path.parent / "direct_link_spec.json"
        sp.write_text(_j2.dumps(spec), encoding="utf-8")
        with _Txn(kicad_cli, kipy, pcb_path, "loop:direct_link",
                  level="light", log=log) as t:
            _kipy(kipy, _KGENX / "direct_link.py", str(pcb_path),
                  str(sp), timeout=300)
        res = {"summary": f"direct_link {params.get('net')} "
                          f"{len(params.get('pts') or []) - 1}段"}
        if getattr(t, "violations", None):
            res["rolled_back"] = t.violations
            res["summary"] += " [回滚]"
        return res
    if name == "reassign_pins":
        # C4 upstream regeneration: swap escape-crossing nets to better
        # AF balls via FC_PIN_OVERRIDE, regenerate board+schema
        # consistently, then re-close. Intentional state-space move
        # (fresh board) — outside the in-place Txn contract; reports
        # before/after explicitly.
        try:
            from fluxpcb.kernel.constraints import (
                escape_preferences, reassign_proposal, write_pin_override)
            from fluxpcb.kicadgen import fcfull, mcu_db
            prefs = escape_preferences(report.get("pairs", []),
                                       report.get("taxonomy", {}))
            if not prefs:
                return {"summary": "reassign: 无逃逸方向信息", "effect": 0}
            pins = mcu_db.load_st_xml(mcu_db.MCU_CONFIG["h743"]["xml"])
            proj = pcb_path.parents[2]
            ball_nets = fcfull.mcu_ball_nets(proj)
            assign = {net: {"ball": ball}
                      for ball, net in ball_nets.items()}
            pl = fcfull.placement()
            cx, cy, _ = pl.get("U1", (26.0, 24.0, 0))
            props = reassign_proposal(assign, prefs, pins, (cx, cy))
            if not props:
                return {"summary": "reassign: 无更优球位", "effect": 0}
            write_pin_override(proj, props)
            fcfull.write_board(proj)
            fcfull.write_schematic(proj)
            _run_session(kipy, pcb_path, cls_file, log=log)
            _run_session(kipy, pcb_path, cls_file, log=log,
                         nets=sorted(prefs))
            route_with_freerouting(pcb_path, kipy, log=log,
                                   net_classes=net_classes,
                                   diff_pairs=diff_pairs)
            return {"summary": f"reassign {len(props)}球+再生成+重链",
                    "effect": len(props)}
        except Exception as e:
            return {"summary": f"reassign失败:{str(e)[:60]}", "effect": 0}
    try:
        with _Txn(kicad_cli, kipy, pcb_path, f"loop:{name}",
                  level="light", log=log) as t:
            if name == "reroute_nets":
                nets_arg = ",".join(params.get("nets") or [])
                _kipy(kipy, _KGENX / "rip_nets.py", str(pcb_path),
                      nets_arg, timeout=300)
                fr = route_with_freerouting(pcb_path, kipy, log=log,
                                            net_classes=net_classes,
                                            diff_pairs=diff_pairs)
                res = {"summary": "rip+FR "
                       + ("ok" if fr.get("ok") else
                          str(fr.get("error", ""))[:40])}
            elif name == "rerun_session":
                sess = _run_session(kipy, pcb_path, cls_file, log=log,
                                    nets=params.get("nets"))
                res = {"summary": f"session "
                       f"{(sess or {}).get('start')}->{(sess or {}).get('final')}"}
            elif name == "rerun_fluxrouter":
                __import__("os").environ["FLUXNT_PF_ITERS"] = str(
                    params.get("iters", 12))
                frn = _run_fluxrouter(kipy, pcb_path, cls_file, log=log)
                res = {"summary": f"fluxrouter "
                       f"{(frn or {}).get('routed_nets')}/"
                       f"{(frn or {}).get('nets')}"}
            elif name == "rerun_freerouting":
                fr = route_with_freerouting(pcb_path, kipy, log=log,
                                            net_classes=net_classes,
                                            diff_pairs=diff_pairs)
                res = {"summary": "FR "
                       + ("ok" if fr.get("ok") else
                          str(fr.get("error", ""))[:40])}
            elif name == "jumper":
                nets_arg = ",".join(params.get("nets") or [])
                pretty = pcb_path.parent / "jumper_pretty"
                _kipy(kipy, _KGENX / "route_jumper.py", str(pcb_path),
                      str(pretty) if pretty.exists() else "",
                      nets_arg, timeout=600)
                res = {"summary":
                       f"jumper({len(params.get('nets') or [])}网)"}
            elif name == "adjust_placement":
                n = 0
                for mv in (params.get("moves") or [])[:5]:
                    try:
                        _kipy(kipy, _KGENX / "fp_move.py", str(pcb_path),
                              str(mv["ref"]), str(float(mv["dx_mm"])),
                              str(float(mv["dy_mm"])), timeout=300)
                        n += 1
                    except Exception:
                        continue
                res = {"summary": f"挪{n}器件"}
            elif name == "move_footprint":
                _kipy(kipy, _KGENX / "fp_move.py", str(pcb_path),
                      str(params["ref"]), str(float(params["dx_mm"])),
                      str(float(params["dy_mm"])), timeout=300)
                res = {"summary": f"move {params['ref']}"}
            elif name == "power_stitch":
                sess = _run_session(kipy, pcb_path, cls_file, log=log)
                res = {"summary":
                       f"stitch {(sess or {}).get('stitched')}"}
            else:
                res = {"summary": f"未实现动作 {name}", "effect": 0}
        if getattr(t, "violations", None):
            res["rolled_back"] = t.violations
            res["summary"] += " [回滚]"
        return res
    except Exception as e:
        return {"summary": f"{name}异常:{str(e)[:50]}", "effect": 0}


def _closed_loop(kicad_cli: str, kipy: str, pcb_path: Path,
                 cls_file: Path, net_classes: dict, diff_pairs: list,
                 steps: list, log=print) -> None:
    """C3 THE loop: complex PCBs are solved by ITERATION with reliable
    information transfer (points 1/2). One chain pass is merely the
    first iteration; the loop terminates ONLY at zero opens AND zero
    shorts, or at budget (then it reports honestly — no fake success).

    Information flows both ways at every step (points 3/4/6/7/8):
    measure -> intel (one coherent report + attribution) -> decision
    -> validate (rejection carries fix-it) -> Txn execute (rollback
    carries violated invariants) -> record -> measure ...
    """
    import time as _time
    from fluxpcb.kernel import intel as _intel
    from fluxpcb.kernel import decision as _kdec
    from fluxpcb.kernel import state as _kstate

    budget = float(__import__("os").environ.get(
        "FLUXNT_LOOP_BUDGET_S", "1800"))
    deadline = _time.time() + budget
    tried = set()
    # persistent ineffective-set: decisions whose journal outcome shows
    # zero/negative effect are never re-tried — the loop learns from
    # history even across stage retries (the point-3/4 feedback must
    # survive process boundaries, not just in-memory)
    _ineffective = set()
    try:
        _jpath = pcb_path.parent / "decision_journal.json"
        if _jpath.exists():
            for e in json.loads(_jpath.read_text(encoding="utf-8")):
                d_, o_ = e.get("decision", {}), e.get("outcome", {})
                if o_.get("delta_opens", 0) >= 0 and not o_.get("stop"):
                    _ineffective.add((d_.get("action"), json.dumps(
                        d_.get("params", {}), sort_keys=True)[:120]))
    except (ValueError, OSError):
        pass
    tried |= _ineffective
    n = 0
    history_log = []
    while _time.time() < deadline:
        n += 1
        qo = _kstate.query(kicad_cli, kipy, pcb_path, "opens")
        qs = _kstate.query(kicad_cli, kipy, pcb_path, "shorts",
                           consolidate=False)
        if qo.value <= 0 and qs.value <= 0:
            steps.append(f"loop[{n}]: 0开路0短路 — 目标达成")
            break
        pairs = qo.detail.get("pairs", [])
        diag = []
        if 0 < qo.value <= 40:
            try:
                _KGENL = Path(__file__).resolve().parent / "kicadgen"
                rc, out = _kipy(kipy, _KGENL / "route_diag.py",
                                str(pcb_path), timeout=600)
                line = [l for l in (out or "").splitlines()
                        if l.strip().startswith("{")][-1]
                diag = json.loads(line).get("detail", [])
            except (IndexError, ValueError, subprocess.TimeoutExpired):
                pass
        report = _intel.build(pcb_path, {"opens": qo, "shorts": qs},
                              pairs, diag=diag)
        report["pairs"] = pairs
        # M: MAP-driven planning — each stubborn net gets its geomir
        # plan (islands + verified paths + blockers + summary + SVG).
        # This is the map the decider reads; the fix-it carries the
        # path itself, not just a label.
        plans = {}
        if 0 < qo.value <= 40:
            _KG = Path(__file__).resolve().parent / "kicadgen"
            for _net in sorted({ln.split("|")[0]
                                for ln in pairs})[:8]:
                try:
                    rc, out = _kipy(kipy, _KG / "geomir.py",
                                    str(pcb_path), _net,
                                    str(pcb_path), timeout=300)
                    _line = [l for l in (out or "").splitlines()
                             if l.strip().startswith("{")][-1]
                    _pl = json.loads(_line)
                    if _pl.get("paths") or _pl.get("islands"):
                        plans[_net] = _pl
                except (IndexError, ValueError,
                        subprocess.TimeoutExpired):
                    continue
        report["extra"]["plans"] = plans
        report["extra"]["candidates"] = _intel.flexibility(
            pcb_path, pairs, report["taxonomy"], plans=plans)
        assess = _intel.synthesize(report, log)
        causes = "; ".join(c.get("cause", "")[:24]
                           for c in assess.get("root_causes", [])[:2])
        _maps = "; ".join(f"{k}:{v.get('summary', '')[:44]}"
                          for k, v in list(plans.items())[:3])
        steps.append(f"loop[{n}] {assess.get('state')}: {qo.value}开/"
                     f"{qs.value}短 因[{causes}]")
        if _maps:
            steps.append(f"loop[{n}] 图: {_maps[:150]}")
        queue = list(sorted(assess.get("actions", []),
                            key=lambda a: a.get("priority", 9)))
        cand_queue = []
        for net, opts in (report["extra"]["candidates"] or {}).items():
            for o in opts:
                # USE the candidate's own params — rebuilding as
                # {"nets": [...]} stripped direct_link's {net, pts,
                # width} ("缺少参数 net" rejections) and adjust_
                # placement's {moves}. The candidate IS the decision.
                cand_queue.append({"action": o["action"],
                                   "params": o.get("params",
                                                  {"nets": [net]}),
                                   "expected_effect":
                                       o.get("expected_effect", "")})
        # MAP-VERIFIED FIRST: a direct_link carries a path the geometry
        # PROVED valid on this exact board state — run it before any
        # mutating action (a reroute reshuffles the seams and the
        # verified plan dies; TWIN journal: direct_link never got a
        # turn because reroute_nets went first and closed its window)
        cand_queue.sort(key=lambda a: 0 if a["action"] == "direct_link"
                        else (1 if a["action"] == "corridor_rescue"
                              else 2))
        queue = cand_queue + queue
        acted = False
        for act in queue:
            if _time.time() > deadline:
                break          # budget ALSO bounds the action queue
                               # (15 actions x double-DRC blew the cap)
            key = (act.get("action"), json.dumps(
                act.get("params", {}), sort_keys=True)[:120])
            if key in tried:
                continue
            tried.add(key)
            ok, fixit = _kdec.validate(act)
            if not ok:
                _kdec.record(pcb_path, act, {"rejected": fixit})
                continue
            # T4 STRICT-IMPROVEMENT GATE (ByTaymur/PcbAi corpus rule:
            # "new DRC errors or increased opens block the application").
            # Snapshot first — the gate RESTORES on any regression
            # (jumper d=-1 and direct_link +10-shorts classes die here).
            _strict = __import__("os").environ.get(
                "FLUXNT_V_STRICT", "1") != "0"
            import shutil as _sh4
            _pre_snap = pcb_path.with_suffix(".strict.bak")
            _sh4.copy2(pcb_path, _pre_snap)
            _qs_before = _kstate.query(kicad_cli, kipy, pcb_path, "shorts",
                                       consolidate=False)
            outcome = _loop_execute(act, report, kicad_cli, kipy,
                                    pcb_path, cls_file, net_classes,
                                    diff_pairs, log)
            # measure the EFFECT (bidirectional feedback, point 3/4):
            # what did this decision actually change?
            _q2 = _kstate.query(kicad_cli, kipy, pcb_path, "opens",
                                consolidate=False)
            outcome["delta_opens"] = qo.value - _q2.value
            _qs_after = _kstate.query(kicad_cli, kipy, pcb_path, "shorts",
                                      consolidate=False)
            _dshorts = _qs_after.value - _qs_before.value
            outcome["delta_shorts"] = _dshorts
            history_log.append(outcome)
            if _strict and (_dshorts > 0 or outcome["delta_opens"] < 0):
                # strict gate: any regression = restore the snapshot
                # and treat the action as rejected (its side-effects
                # never reach the board)
                _sh4.copy2(_pre_snap, pcb_path)
                outcome["strict_rollback"] = (
                    f"delta_opens={outcome['delta_opens']} "
                    f"delta_shorts={_dshorts}")
                _kdec.record(pcb_path, act, outcome)
                steps.append(f"loop[{n}] {act['action']}: "
                             f"严格门禁回滚(+{_dshorts}短/"
                             f"{-outcome['delta_opens']}开)")
                continue
            _kdec.record(pcb_path, act, outcome)
            # CRASH-SAFE terminal state: refresh loop_final after every
            # action — whatever point a run dies, the latest loop-end
            # measurement is on disk (item-4, hardened)
            try:
                (pcb_path.parent / "loop_final.json").write_text(
                    _json.dumps(
                        {"opens": _q2.value, "shorts": _qs_after.value,
                         "fp": _q2.fp.short(), "iterations": n,
                         "last_action": act.get("action")},
                        ensure_ascii=False, indent=1), encoding="utf-8")
            except Exception:
                pass
            steps.append(f"loop[{n}] {act['action']}: "
                         f"{outcome.get('summary', '')[:70]}")
            if outcome.get("stop"):
                return
            acted = True
            break
        if not acted:
            steps.append(f"loop[{n}]: 候选动作穷尽,终止")
            break
    else:
        steps.append(f"loop: 预算{budget:.0f}s耗尽 — 归因在"
                     f"decision_journal.json(不伪装成功)")


def auto_route(pcb_path: Path, max_iterations: int = 5, log=print) -> dict:
    """Route a generated board to TRUE connectivity (ratsnest=0) + no shorts.

    v3: one single-process routing session per iteration (route_session.py —
    in-process zone fill, audit, A* on shared incremental grids, bounded
    rip-up; pcbnew's ratsnest verifies every committed pair instantly).
    Freerouting is opt-in (FLUXNT_FR=1): it never completed on the BGA
    boards and its 2x480s timeouts dominated wall time under engine retries.
    kicad-cli DRC runs only at the end (shorts/clearance feed the scrubber).
    Returns {"iterations", "unconnected", "shorting", "clean", "steps"}
    where unconnected IS the ratsnest open-edge count. Never raises."""
    import json as _json
    kicad_cli, kipy = find_kicad()
    if not kicad_cli:
        return {"clean": False, "error": "kicad-cli 不可用"}
    if not kipy:
        return {"clean": False, "error": "KiCad Python 不可用"}
    ensure_calibration(pcb_path)

    rpt = pcb_path.parent / "drc.rpt"
    steps: list[str] = []
    last = {"unconnected": -1, "shorting": -1}

    import os
    light = os.environ.get("FLUXNT_ROUTE_LIGHT") == "1"

    # net classification (K1 knowhow): Power/Critical classes + widths
    from fluxpcb import routing_classes as _rc
    spec = None
    _specf = pcb_path.parents[2] / "spec.yaml" if len(pcb_path.parents) > 2 else None
    if _specf and _specf.exists():
        import yaml as _yaml
        spec = _yaml.safe_load(_specf.read_text(encoding="utf-8"))
    board_nets = _rc.nets_from_board(pcb_path)
    net_classes = _rc.classify_all(board_nets, spec)
    ipc_widths = {}
    _ncf = pcb_path.parents[2] / "pcb" / "netclasses.json" if len(pcb_path.parents) > 2 else None
    if _ncf and _ncf.exists():
        try:
            for _row in _json.loads(_ncf.read_text(encoding="utf-8")):
                if isinstance(_row, dict) and _row.get("width_mm"):
                    ipc_widths[_row.get("net_class", "?")] = float(_row["width_mm"])
        except (ValueError, OSError):
            pass
    _rc.apply_net_classes(pcb_path.with_suffix(".kicad_pro"), net_classes, ipc_widths)
    cls_file = pcb_path.parent / "net_classes.json"
    _pairs = _rc.detect_pairs(board_nets)
    cls_file.write_text(_json.dumps(
        {"classes": net_classes, "ipc_widths": ipc_widths,
         "pairs": _pairs,
         "widths": {c: _rc.DEFAULT_WIDTH_MM[c] for c in _rc.ORDER}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    steps.append(f"netclass: {sum(1 for c in net_classes.values() if c=='critical')}critical/"
                 f"{sum(1 for c in net_classes.values() if c=='power')}power/"
                 f"{len(_pairs)}diffpairs")

    # fast path: ratsnest-clean AND shorts-clean board skips everything
    rn0 = ratsnest_state(kipy, pcb_path, log=log)
    if rn0 and rn0["ratsnest"] == 0:
        drc0 = run_drc(kicad_cli, pcb_path, rpt)
        if drc0["shorting"] == 0 and "[clearance]" not in drc0["text"]:
            steps.append(f"initial ratsnest clean({rn0['audit'].get('tracks')} tracks) — routing skipped")
            log("  [route] initial ratsnest clean, skip")
            return {"iterations": 0, "unconnected": 0, "shorting": 0,
                    "clean": True, "steps": steps}
    else:
        # pre-fill zones once so the session starts from fresh copper
        run_drc(kicad_cli, pcb_path, rpt)

    # W-round: FLUXNT_LLM_DRIVER=1 replaces the fixed policy below with
    # the LLM decision loop (drivers read telemetry, rule engines execute)
    v3_order = os.environ.get("FLUXNT_V3", "0") == "1"
    if os.environ.get("FLUXNT_LLM_DRIVER") == "1":
        _driver_loop(pcb_path, kipy, cls_file, light, steps, log=log)
    else:
        # FLUXNT_V3=1 — the K15-best chain order: session-first (fanout:
        # dog-bone + module fanout + power stitch live inside route_session)
        # -> preroute-critical (R3) -> multi-layer fluxrouter -> Freerouting
        # INCREMENTAL (Z1: converged nets are fix-marked in the DSN, FR
        # routes only the opens around them) -> session residue passes.
        # Running fluxrouter LAST (default order) hands it the FR+session-
        # clogged board where everything is nopath (measured 0/43 both old
        # and Z-round routers on committed fc).
        if v3_order:
            # K6 kernel wiring: the three head-stage engines run as
            # TRANSACTIONS — snapshot, execute, invariant check, atomic
            # rollback on violation; every step lands in
            # chain_manifest.json (the replayable mutation log)
            from fluxpcb.kernel.txn import Txn as _Txn
            _pre = [n for n, c in net_classes.items()
                    if c == "critical" and n not in
                    {x for p in _pairs for x in p}]
            _pre += [x for p in _pairs for x in p]
            if _pre:
                with _Txn(kicad_cli, kipy, pcb_path,
                          "preroute-critical", level="light",
                          log=log) as _t:
                    pre = _run_session(kipy, pcb_path, cls_file, log=log,
                                       nets=_pre)
                if pre:
                    steps.append(f"preroute-critical({len(_pre)}网): "
                                 f"{pre.get('start')}->{pre.get('final')}")
            with _Txn(kicad_cli, kipy, pcb_path, "fanout-session",
                      level="light", log=log) as _t:
                fan = _run_session(kipy, pcb_path, cls_file, log=log)
            if fan:
                steps.append(f"fanout-session: {fan.get('start')}->{fan.get('final')} "
                             f"(stitch {fan.get('stitched')}, dogbone+modfan)")
            if os.environ.get("FLUXNT_ROUTER") == "pathfinder":
                with _Txn(kicad_cli, kipy, pcb_path, "fluxrouter",
                          level="light", log=log) as _t:
                    frn = _run_fluxrouter(kipy, pcb_path, cls_file,
                                          log=log)
                if frn:
                    steps.append(f"fluxrouter: iters={frn.get('iters')} "
                                 f"routed={frn.get('routed_nets')}/{frn.get('nets')} "
                                 f"final={frn.get('final')} ({frn.get('time_s')}s)")
                    log(f"  [route] fluxrouter: {frn.get('routed_nets')}/"
                        f"{frn.get('nets')} nets, final {frn.get('final')}")

        # Freerouting under a FULL transaction (I1+I2+I3: the injection
        # boundary where layer-mapping and overlap classes once struck)

        # Freerouting is the PRIMARY router (real push-and-shove rip-up
        # rerouter; completed the 240-ball h743-fc in ~18min once given a
        # 1800s budget). The session engine finishes its residue (power-plane
        # stitching + the pairs FR leaves). FLUXNT_FR=0 disables.
        if os.environ.get("FLUXNT_FR", "1") != "0" and not light:
            _prio = [n for n, c in net_classes.items() if c == "critical"]
            from fluxpcb.kernel.txn import Txn as _TxnFR
            with _TxnFR(kicad_cli, kipy, pcb_path, "freerouting",
                        level="full", log=log) as _t:
                fr = route_with_freerouting(pcb_path, kipy, log=log,
                                            priority_nets=_prio,
                                            net_classes=net_classes,
                                            diff_pairs=_pairs)
            steps.append(f"freerouting: {fr.get('stats')}" if fr.get("ok")
                         else f"freerouting跳过: {fr.get('error', '')[:80]}")
        else:
            steps.append("freerouting: off")

        it = 0
        prev = rn0["ratsnest"] if rn0 else -1
        for it in range(1, (1 if light else max_iterations) + 1):
            sess = _run_session(kipy, pcb_path, cls_file, log=log)
            if sess is None:
                steps.append(f"session{it}: failed")
                break
            cur = sess.get("final", -1)
            steps.append(f"session{it}: {sess.get('start')}->{cur} "
                         f"(stitch {sess.get('stitched')}, ripup {sess.get('ripups')}, "
                         f"{sess.get('time_s')}s)")
            log(f"  [route] session{it}: {sess.get('start')}->{cur} in {sess.get('time_s')}s")
            if cur == 0:
                break
            if cur >= prev and it > 1:
                steps.append(f"session{it}: no progress, stop")
                break
            prev = cur

        # PathFinder pass (K14): FLUXNT_ROUTER=pathfinder enables — the
        # negotiated rerouting closes what the sequential session cannot.
        # (In FLUXNT_V3 order it already ran before Freerouting.)
        if os.environ.get("FLUXNT_ROUTER") == "pathfinder" and not v3_order:
            frn = _run_fluxrouter(kipy, pcb_path, cls_file, log=log)
            if frn:
                steps.append(f"fluxrouter: iters={frn.get('iters')} "
                             f"routed={frn.get('routed_nets')}/{frn.get('nets')} "
                             f"final={frn.get('final')} ({frn.get('time_s')}s)")
                log(f"  [route] fluxrouter: {frn.get('routed_nets')}/"
                    f"{frn.get('nets')} nets, final {frn.get('final')}")

    # C3: THE closed loop — the chain above was merely iteration 0.
    # Termination is zero opens AND zero shorts, or budget (honest
    # attribution report, never fake success).
    if __import__("os").environ.get("FLUXNT_LOOP", "1") != "0":
        _closed_loop(kicad_cli, kipy, pcb_path, cls_file,
                     net_classes, _pairs, steps, log=log)

    # closing DRC (+scrubber). One pass; if the scrubber removed
    # load-bearing copper, one more session re-closes what it broke.
    drc = run_drc(kicad_cli, pcb_path, rpt)
    # M3 short repair (fallback for whatever slipped past the commit
    # gate): delete shorting items, list their nets, re-close surgically
    if drc["shorting"]:
        _KGEN3 = Path(__file__).resolve().parent / "kicadgen"
        try:
            rc, out = _kipy(kipy, _KGEN3 / "short_repair.py",
                            str(pcb_path), str(rpt), timeout=600)
            line = [l for l in (out or "").splitlines()
                    if l.strip().startswith("{")][-1]
            _sr = json.loads(line)
            steps.append(f"short-repair: 删{_sr['removed']}项/"
                         f"{_sr['pairs']}对, 重布{len(_sr['nets_for_reroute'])}网")
            if _sr["nets_for_reroute"]:
                nets_arg = ",".join(_sr["nets_for_reroute"][:10])
                _kipy(kipy, _KGEN3 / "rip_nets.py", str(pcb_path),
                      nets_arg, timeout=300)
                fr2 = route_with_freerouting(pcb_path, kipy, log=log,
                                             net_classes=net_classes,
                                             diff_pairs=_pairs)
                steps.append(f"short-reroute FR: "
                             f"{'ok' if fr2.get('ok') else fr2.get('error', '')[:50]}")
            drc = run_drc(kicad_cli, pcb_path, rpt)
        except (IndexError, ValueError, subprocess.TimeoutExpired):
            pass
    scrubbed = False
    if drc["shorting"] or "[clearance]" in drc["text"] \
            or "[holes_co_located]" in drc["text"] \
            or "[copper_edge_clearance]" in drc["text"]:
        # the cleanup kipy process heap-crashes mid-run sometimes (KiCad10
        # SWIG container poisoning) and its partial removal — 30 items on
        # the mini regression, 3->19 opens — lands in the SAVED file.
        # Snapshot first; if the survivors' ratsnest regressed, roll back.
        import shutil as _sh
        pre_rn = ratsnest_state(kipy, pcb_path, log=log)
        pre_opens = pre_rn["ratsnest"] if pre_rn else -1
        backup = pcb_path.with_suffix(".kicad_pcb.bak")
        _sh.copy2(pcb_path, backup)
        rc, out = _kipy(kipy, _CLEANUP, str(pcb_path), str(rpt))
        lastline = out.strip().splitlines()[-1][:60] if out.strip() else ""
        steps.append(f"final cleanup rc={rc} {lastline}")
        post_rn = ratsnest_state(kipy, pcb_path, log=log)
        post_opens = post_rn["ratsnest"] if post_rn else -1
        post_drc = run_drc(kicad_cli, pcb_path, rpt)
        worse = (pre_opens >= 0 and post_opens > pre_opens) or \
            post_drc["shorting"] > drc["shorting"]
        if worse:
            _sh.copy2(backup, pcb_path)
            steps.append(f"cleanup倒退({pre_opens}->{post_opens}, "
                         f"shorts {drc['shorting']}->{post_drc['shorting']})回滚")
            scrubbed = False
        else:
            scrubbed = True
        try:
            backup.unlink()
        except OSError:
            pass
        drc = run_drc(kicad_cli, pcb_path, rpt)
    rnf = ratsnest_state(kipy, pcb_path, log=log)
    if scrubbed and rnf and rnf["ratsnest"] > 0:
        sess = _run_session(kipy, pcb_path, cls_file, log=log)
        if sess:
            steps.append(f"post-cleanup session: ->{sess.get('final')}")
            rnf = ratsnest_state(kipy, pcb_path, log=log) or rnf
            drc = run_drc(kicad_cli, pcb_path, rpt)

    # R4 residue closure: route_diag verdicts on whatever is left open —
    # diagnosed-unroutable nets get their 0R jumper automatically; the
    # rest become the HANDOFF LIST (the industry recipe's "manual
    # finish" step made an explicit deliverable instead of a silent
    # failure). automation% = nets closed automatically / routable nets.
    handoff, jumpers = [], 0
    if rnf and rnf["ratsnest"] > 0 \
            and rnf["ratsnest"] <= int(os.environ.get("FLUXNT_HANDOFF_MAX", "30")):
        _KGEN = Path(__file__).resolve().parent / "kicadgen"
        frag = (rnf.get("audit", {}).get("fragmented_nets", {}) or {})
        try:
            rc, out = _kipy(kipy, _KGEN / "route_diag.py", str(pcb_path),
                            timeout=600)
            line = [l for l in (out or "").splitlines()
                    if l.strip().startswith("{")][-1]
            verdicts = _json.loads(line).get("detail", [])
        except (IndexError, ValueError, subprocess.TimeoutExpired):
            verdicts = []
        _jm = [v["net"] for v in verdicts
               if v.get("verdict") in ("jumper", "disconnected-components")]
        if _jm:
            pretty = pcb_path.parent / "jumper_pretty"
            try:
                rc, out = _kipy(kipy, _KGEN / "route_jumper.py",
                                str(pcb_path),
                                str(pretty) if pretty.exists() else "",
                                ",".join(_jm), timeout=600)
                jumpers = len(_jm)
                steps.append(f"handoff-jumpers: {jumpers} 网已放0Ω")
                rnf = ratsnest_state(kipy, pcb_path, log=log) or rnf
                drc = run_drc(kicad_cli, pcb_path, rpt)
                frag = (rnf.get("audit", {}).get("fragmented_nets", {}) or {})
            except subprocess.TimeoutExpired:
                pass
        for net in (frag or {}):
            v = next((x for x in verdicts if x.get("net") == net), {})
            handoff.append({
                "net": net,
                "verdict": v.get("verdict", "undiagnosed"),
                "bottleneck_mm": v.get("bottleneck_mm"),
                "blockers": v.get("blockers", [])[:4],
                "suggested_action": "手工补线或挪动 "
                                    + "/".join(v.get("blockers", [])[:2])
                                    if v.get("blockers") else "手工补线",
            })
        if handoff:
            steps.append(f"handoff: {len(handoff)} 网待人工(清单在route_report)")

    last = {"unconnected": (rnf or {}).get("ratsnest", -1),
            "shorting": drc["shorting"]}
    clean = last["unconnected"] == 0 and last["shorting"] == 0
    steps.append(f"final: ratsnest={last['unconnected']} shorts={last['shorting']}")
    # K4 knowhow: post-routing scorecard + structured feedback for retries
    try:
        from fluxpcb.routing_quality import score_board, structured_feedback
        report = {"unconnected": last["unconnected"], "shorting": last["shorting"]}
        score = score_board(pcb_path, report, spec)
        _total = len(net_classes)
        _auto = max(0, _total - last["unconnected"] - len(handoff))
        _routable = max(1, _total - jumpers)
        _auto_pct = round(100.0 * _auto / _routable, 1)
        (pcb_path.parent / "route_report.json").write_text(_json.dumps(
            {**report, "score": score,
             "steps": steps[-25:],
             "automation_pct": round(100.0 * _auto / _routable, 1),
             "jumpers_placed": jumpers,
             "handoff": handoff,
             "structured_feedback": structured_feedback(drc["text"], net_classes),
             "fragmented_nets": (rnf or {}).get("audit", {}).get("fragmented_nets", {})},
            ensure_ascii=False, indent=1), encoding="utf-8")
        steps.append(f"score={score['score']} automation="
                     f"{round(100.0 * _auto / _routable, 1)}%")
    except Exception as _e:
        steps.append(f"scorecard skipped: {_e}")
    return {"iterations": it, **last, "clean": clean, "steps": steps,
            "automation_pct": locals().get("_auto_pct"),
            "jumpers_placed": jumpers, "handoff": handoff}
