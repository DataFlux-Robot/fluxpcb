"""In-pipeline auto-routing: DRC -> L-routes -> shorts-cleanup, bounded loop.

Chain (proven on the esp32s31-dev board):
  1. kicad-cli DRC (refill zones, save board) with calibrated severities
  2. unconnected pairs -> KiCad Python route_embedded.py (L-routes + vias)
  3. shorting items   -> KiCad Python shorts_cleanup.py (drop culprit tracks)
  4. repeat until clean or max_iterations

Severity calibration lives in the board's .kicad_pro
(boards.design_settings.rule_severities): hand-fixable items (unconnected,
dangling tracks, starved thermals) are 'ignore' so they don't mask real
errors; shorting/clearance/width stay 'error'. ensure_calibration() merges
that map into any project file deterministically.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

_EMBEDDED = Path(__file__).resolve().parent / "kicadgen" / "route_embedded.py"
_CLEANUP = Path(__file__).resolve().parent / "kicadgen" / "shorts_cleanup.py"

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
    # K5 knowhow: SES output bypasses the DSN batch-save NPE path
    # (AvatarSD/KiSkill DSN/SES closed loop). Try SES once (bounded), then
    # DSN write-back once; timeouts count as failures, never kill the chain.
    ses = work / ("routed-" + pcb_path.stem + ".ses")
    last_err = ""
    produced_ses = False
    if ses.exists():
        ses.unlink()
    try:
        p = subprocess.run([java, "-Djava.awt.headless=true", "-jar", jar,
                            "-de", dsn.name, "-do", ses.name,
                            "-mp", "40", "-l", "en"],
                           capture_output=True, text=True, timeout=480,
                           cwd=str(work))
        if ses.exists() and ses.stat().st_size > 0:
            produced_ses = True
        else:
            last_err = f"SES rc={p.returncode}"
    except subprocess.TimeoutExpired:
        last_err = "SES timeout(480s)"
    log(f"  [route] freerouting SES: {'ok' if produced_ses else last_err}")
    if produced_ses:
        from fluxpcb.ses2kicad import inject_ses
        stats = inject_ses(ses, pcb_path)
        log(f"  [route] freerouting(SES)注入: {stats}")
        return {"ok": True, "stats": stats, "mode": "ses"}
    # DSN write-back fallback (original path), one bounded attempt
    if routed.exists():
        routed.unlink()
    try:
        p = subprocess.run([java, "-Djava.awt.headless=true", "-jar", jar,
                            "-de", dsn.name, "-do", routed.name,
                            "-mp", "40", "-l", "en"],
                           capture_output=True, text=True, timeout=480,
                           cwd=str(work))
        if not (routed.exists() and routed.stat().st_size > 0):
            tail = (p.stdout + p.stderr)[-200:].replace("\n", " ")
            last_err = f"rc={p.returncode} {tail}"
    except subprocess.TimeoutExpired:
        last_err = "DSN timeout(480s)"
    if not (routed.exists() and routed.stat().st_size > 0):
        return {"ok": False, "error": f"freerouting无产出: {last_err}"}
    import sys
    sys.path.insert(0, str(_DSN2KICAD.parent))
    from dsn2kicad import convert
    stats = convert(routed, pcb_path)
    log(f"  [route] freerouting注入: {stats}")
    return {"ok": True, "stats": stats, "mode": "dsn"}


def _kipy_run(kipy: str, code: str, timeout: int = 600) -> tuple[int, str]:
    p = subprocess.run([kipy, "-c", code], capture_output=True, text=True,
                       timeout=timeout)
    return p.returncode, (p.stdout + p.stderr)


def auto_route(pcb_path: Path, max_iterations: int = 5, log=print) -> dict:
    """Route a generated board to DRC-clean (calibrated severities).

    Returns {"iterations", "unconnected", "shorting", "clean", "steps"}.
    Never raises — a board that cannot be cleaned reports its residue.
    """
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

    # fast path: an already-clean board (e.g. released green board under
    # re-verify) skips the whole chain — also protects it from re-routing
    drc0 = run_drc(kicad_cli, pcb_path, rpt)
    if drc0["unconnected"] == 0 and drc0["shorting"] == 0             and "[clearance]" not in drc0["text"]:
        steps.append("initial DRC clean — routing skipped")
        log("  [route] initial DRC clean, skip")
        return {"iterations": 0, "unconnected": 0, "shorting": 0,
                "clean": True, "steps": steps}
    if light:
        max_iterations = min(max_iterations, 1)

    # net classification (K1 knowhow): Power/Critical classes into the
    # .kicad_pro so the DSN export carries them to Freerouting; pairs get
    # routed critical-first, power-last with per-class widths
    import json as _json
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

    # heavy-duty pass first when freerouting is bundled, then the in-process
    # L-router finishes residue; both bounded
    fr = ({"ok": False, "error": "light mode"}
          if light else route_with_freerouting(pcb_path, kipy, log=log))
    if fr.get("ok"):
        steps.append(f"freerouting: {fr.get('stats')}")
    else:
        steps.append(f"freerouting跳过: {fr.get('error', '')[:80]}")
        log(f"  [route] freerouting跳过: {fr.get('error', '')[:100]}")

    for it in range(1, max_iterations + 1):
        drc = run_drc(kicad_cli, pcb_path, rpt)
        last = {"unconnected": drc["unconnected"], "shorting": drc["shorting"]}
        steps.append(f"iter{it}: unconn={drc['unconnected']} shorts={drc['shorting']}")
        log(f"  [route] iter{it}: unconn={drc['unconnected']} shorts={drc['shorting']}")
        if drc["unconnected"] == 0 and drc["shorting"] == 0:
            break   # fall through to the shared final DRC + scorecard block

        if drc["unconnected"] > 0:
            pairs = extract_pairs(drc["text"])
            pairs = _rc.routing_order_pairs(pairs, net_classes)  # critical first
            if pairs:
                pairs_file = pcb_path.parent / "pairs.txt"
                pairs_file.write_text("\n".join(pairs), encoding="utf-8")
                rc, out = _kipy(kipy, _EMBEDDED, str(pcb_path), str(pairs_file),
                                str(cls_file))
                ok = "ROUTES_ADDED" in out
                steps.append(f"route_embedded rc={rc} {'ok' if ok else out[:120]}")
                log(f"  [route] embedded: {out.strip().splitlines()[-1][:90] if out.strip() else 'no output'}")
        if drc["shorting"] > 0:
            rc, out = _kipy(kipy, _CLEANUP, str(pcb_path), str(rpt))
            ok = "VIOLATIONS_REMOVED" in out
            steps.append(f"shorts_cleanup rc={rc} {'ok' if ok else out[:120]}")
            log(f"  [route] cleanup: {out.strip().splitlines()[-1][:90] if out.strip() else 'no output'}")

    # final DRC for the report, then one last scrubber pass — the loop's
    # route->cleanup ordering converges one scrub behind
    drc = run_drc(kicad_cli, pcb_path, rpt)
    if drc["shorting"] or "[clearance]" in drc["text"]             or "[holes_co_located]" in drc["text"]             or "[copper_edge_clearance]" in drc["text"]:
        rc, out = _kipy(kipy, _CLEANUP, str(pcb_path), str(rpt))
        lastline = out.strip().splitlines()[-1][:60] if out.strip() else ""
        steps.append(f"final cleanup rc={rc} {lastline}")
        drc = run_drc(kicad_cli, pcb_path, rpt)
    last = {"unconnected": drc["unconnected"], "shorting": drc["shorting"]}
    clean = last["unconnected"] == 0 and last["shorting"] == 0
    iters_used = it if not clean else it   # last loop counter
    steps.append(f"final: unconn={last['unconnected']} shorts={last['shorting']}")
    # K4 knowhow: post-routing scorecard + structured feedback for retries
    try:
        from fluxpcb.routing_quality import score_board, structured_feedback
        report = {"unconnected": last["unconnected"], "shorting": last["shorting"]}
        score = score_board(pcb_path, report, spec)
        (pcb_path.parent / "route_report.json").write_text(_json.dumps(
            {**report, "score": score,
             "structured_feedback": structured_feedback(drc["text"], net_classes)},
            ensure_ascii=False, indent=1), encoding="utf-8")
        steps.append(f"score={score['score']}")
    except Exception as _e:
        steps.append(f"scorecard skipped: {_e}")
    return {"iterations": iters_used, **last, "clean": clean, "steps": steps}
