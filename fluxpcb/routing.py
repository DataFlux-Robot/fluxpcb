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
    "starved_thermal": "ignore",
    "track_dangling": "ignore",
    "track_not_centered_on_via": "ignore",
    "tuning_profile_track_geometries": "ignore",
    "unconnected_items": "ignore",
    "via_dangling": "ignore",
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
                   capture_output=True, text=True, timeout=900)
    txt = rpt.read_text(encoding="utf-8", errors="replace") if rpt.exists() else ""
    return {"unconnected": txt.count("[unconnected_items]"),
            "shorting": txt.count("[shorting_items]"),
            "total_lines": len(txt.splitlines()), "text": txt}


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
                       text=True, timeout=timeout)
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


def route_with_freerouting(pcb_path: Path, kipy: str, log=print) -> dict:
    """Heavy-duty pass: pcbnew DSN export -> freerouting -> routed-DSN
    injection via dsn2kicad (SES import hangs headless; DSN write-back works).
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
    if "DSN_OK" not in out:
        return {"ok": False, "error": f"DSN导出失败: {out[-200:]}"}
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
    _fr_timeout = int(_os2.environ.get("FLUXNT_FR_TIMEOUT", "1800"))
    scr = work / ("routed-" + pcb_path.stem + ".scr")
    if scr.exists():
        scr.unlink()
    last_err = ""
    try:
        p = subprocess.run([java, "-Djava.awt.headless=true", "-jar", jar,
                            "-de", dsn.name, "-do", scr.name,
                            "-mp", "40", "-mt", "1", "-l", "en"],
                           capture_output=True, text=True, timeout=_fr_timeout,
                           cwd=str(work))
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
    # Y1: compute the set of already-fully-connected nets so scr2kicad
    # preserves their tracks (FR's .scr would otherwise destroy the
    # negotiated router's successful routes — 144 shorts on fc)
    preserve_file = work / "preserve_nets.json"
    try:
        rn = ratsnest_state(kipy, pcb_path, log=log)
        if rn:
            frag = rn.get("audit", {}).get("fragmented_nets", {})
            all_nets = set()
            for net_name in (rn.get("audit", {}).get("missing_list", [])
                             or []):
                all_nets.add(net_name)
            all_nets.update(frag.keys())
            # fully-connected = nets NOT in the fragmented list — but we
            # need all net names; use the board's nets minus fragmented
            import json as _pj
            _pj.dump({"_dummy": True}, open(preserve_file, "w"))  # reset
            # simpler: list all nets from board, subtract fragmented
            rc_n, out_n = _kipy_run(kipy,
                                    "import pcbnew; b=pcbnew.LoadBoard(r'%s'); "
                                    "print([n.GetNetname() for n in "
                                    "[b.FindNet(i) for i in range(1, "
                                    "b.GetNetInfo().NetsByNetcode().__len__()+1)] "
                                    "if n])" % pcb_path, timeout=120)
            all_board_nets = []
            try:
                import ast as _pa
                all_board_nets = _pa.literal_eval(
                    out_n.strip().splitlines()[-1])
            except (ValueError, SyntaxError, IndexError):
                pass
            preserve = set(all_board_nets) - set(frag.keys())
            preserve_file.write_text(_pj.dumps(sorted(preserve)),
                                    encoding="utf-8")
            log(f"  [route] Y1 preserve: {len(preserve)} nets pre-routed")
    except Exception as _e:
        preserve_file.write_text("[]", encoding="utf-8")
        log(f"  [route] Y1 preserve fallback: {_e}")

    code = (
        "import sys; sys.argv = ['x', r'%s', r'%s', r'%s']; "
        "exec(open(r'%s', encoding='utf-8').read())"
        % (pcb_path, scr, preserve_file, _SCR2KICAD))
    rc, out = _kipy_run(kipy, code, timeout=300)
    log(f"  [route] scr注入: {out.strip()[-120:]}")
    # free B.Cu under BGAs (separate process: the KiCad wrappers poison
    # containers after Add/Remove churn) so dog-bone vias can land
    _CLR = Path(__file__).resolve().parent / "kicadgen" / "clear_bcup.py"
    try:
        rc2, out2 = _kipy(kipy, _CLR, str(pcb_path), timeout=180)
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
                       timeout=timeout)
    return p.returncode, (p.stdout + p.stderr)


def _run_session(kipy: str, pcb_path: Path, cls_file: Path, log=print) -> dict | None:
    """One routing session: in-process fill+audit+stitch+A*+rip-up+verify.
    Returns the session's JSON progress dict, or None on failure."""
    import json as _json
    try:
        rc, out = _kipy(kipy, _SESSION, str(pcb_path), str(cls_file),
                        timeout=1800)
    except subprocess.TimeoutExpired:
        log("  [route] session timeout(900s)")
        return None
    try:
        return _json.loads([l for l in (out or "").splitlines()
                            if l.strip().startswith("{")][-1])
    except (ValueError, IndexError):
        log(f"  [route] session无JSON: {(out or '')[-160:]}")
        return None


def _run_fluxrouter(kipy: str, pcb_path: Path, cls_file: Path, log=print) -> dict | None:
    """One PathFinder negotiated-congestion pass (K14): every negotiable
    net rerouted on a cost field where contested cells grow a history
    penalty — competing nets push each other aside instead of the legacy
    one-shot sweep's first-come-first-served."""
    import json as _json
    import os as _os
    _iters = _os.environ.get("FLUXNT_PF_ITERS", "15")
    try:
        rc, out = _kipy(kipy, _FLUXROUTER, str(pcb_path), str(cls_file),
                        _iters, timeout=2700)
    except subprocess.TimeoutExpired:
        log("  [route] fluxrouter timeout(1800s)")
        return None
    try:
        return _json.loads([l for l in (out or "").splitlines()
                            if l.strip().startswith("{")][-1])
    except (ValueError, IndexError):
        log(f"  [route] fluxrouter无JSON: {(out or '')[-160:]}")
        return None


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
    cls_file.write_text(_json.dumps(
        {"classes": net_classes, "ipc_widths": ipc_widths,
         "widths": {c: _rc.DEFAULT_WIDTH_MM[c] for c in _rc.ORDER}},
        ensure_ascii=False, indent=1), encoding="utf-8")
    steps.append(f"netclass: {sum(1 for c in net_classes.values() if c=='critical')}critical/"
                 f"{sum(1 for c in net_classes.values() if c=='power')}power")

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

    # Freerouting is the PRIMARY router (real push-and-shove rip-up
    # rerouter; completed the 240-ball h743-fc in ~18min once given a
    # 1800s budget). The session engine finishes its residue (power-plane
    # stitching + the pairs FR leaves). FLUXNT_FR=0 disables.
    if os.environ.get("FLUXNT_FR", "1") != "0" and not light:
        fr = route_with_freerouting(pcb_path, kipy, log=log)
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
    # negotiated rerouting closes what the sequential session cannot
    if os.environ.get("FLUXNT_ROUTER") == "pathfinder":
        frn = _run_fluxrouter(kipy, pcb_path, cls_file, log=log)
        if frn:
            steps.append(f"fluxrouter: iters={frn.get('iters')} "
                         f"routed={frn.get('routed_nets')}/{frn.get('nets')} "
                         f"final={frn.get('final')} ({frn.get('time_s')}s)")
            log(f"  [route] fluxrouter: {frn.get('routed_nets')}/"
                f"{frn.get('nets')} nets, final {frn.get('final')}")

    # closing DRC (+scrubber). One pass; if the scrubber removed
    # load-bearing copper, one more session re-closes what it broke.
    drc = run_drc(kicad_cli, pcb_path, rpt)
    scrubbed = False
    if drc["shorting"] or "[clearance]" in drc["text"] \
            or "[holes_co_located]" in drc["text"] \
            or "[copper_edge_clearance]" in drc["text"]:
        rc, out = _kipy(kipy, _CLEANUP, str(pcb_path), str(rpt))
        lastline = out.strip().splitlines()[-1][:60] if out.strip() else ""
        steps.append(f"final cleanup rc={rc} {lastline}")
        scrubbed = True
        drc = run_drc(kicad_cli, pcb_path, rpt)
    rnf = ratsnest_state(kipy, pcb_path, log=log)
    if scrubbed and rnf and rnf["ratsnest"] > 0:
        sess = _run_session(kipy, pcb_path, cls_file, log=log)
        if sess:
            steps.append(f"post-cleanup session: ->{sess.get('final')}")
            rnf = ratsnest_state(kipy, pcb_path, log=log) or rnf
            drc = run_drc(kicad_cli, pcb_path, rpt)
    last = {"unconnected": (rnf or {}).get("ratsnest", -1),
            "shorting": drc["shorting"]}
    clean = last["unconnected"] == 0 and last["shorting"] == 0
    steps.append(f"final: ratsnest={last['unconnected']} shorts={last['shorting']}")
    # K4 knowhow: post-routing scorecard + structured feedback for retries
    try:
        from fluxpcb.routing_quality import score_board, structured_feedback
        report = {"unconnected": last["unconnected"], "shorting": last["shorting"]}
        score = score_board(pcb_path, report, spec)
        (pcb_path.parent / "route_report.json").write_text(_json.dumps(
            {**report, "score": score,
             "structured_feedback": structured_feedback(drc["text"], net_classes),
             "fragmented_nets": (rnf or {}).get("audit", {}).get("fragmented_nets", {})},
            ensure_ascii=False, indent=1), encoding="utf-8")
        steps.append(f"score={score['score']}")
    except Exception as _e:
        steps.append(f"scorecard skipped: {_e}")
    return {"iterations": it, **last, "clean": clean, "steps": steps}
