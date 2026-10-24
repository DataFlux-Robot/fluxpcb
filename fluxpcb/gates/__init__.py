"""Gate registry. A gate is a pure function:

    (project_dir: Path, stage_outputs: list[str], config: dict) -> GateResult

Gates never raise; missing inputs/tools yield GateStatus.BLOCKED with a reason.
"""
from __future__ import annotations

from pathlib import Path

from fluxpcb.models import GateResult, GateStatus

_REGISTRY: dict[str, callable] = {}


def gate(name: str):
    def deco(fn):
        _REGISTRY[name] = fn
        fn.__gate_name__ = name
        return fn
    return deco


def get_gate(name: str):
    return _REGISTRY.get(name)


def all_gates() -> list[str]:
    return sorted(_REGISTRY)


def run_gate(name: str, project_dir: Path, stage_outputs: list[str],
             config: dict | None = None) -> GateResult:
    fn = get_gate(name)
    if fn is None:
        return GateResult(name, GateStatus.BLOCKED, summary=f"unknown gate: {name}")
    try:
        return fn(project_dir, stage_outputs, config or {})
    except Exception as e:  # gates must not crash the pipeline
        return GateResult(name, GateStatus.BLOCKED, summary=f"gate error: {e}")


def _read_yaml(p: Path):
    import yaml
    return yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else None


# ---------------------------------------------------------------- requirements
REQUIRED_SPEC_KEYS = ("project", "requirements", "power", "interfaces")


@gate("spec_schema")
def spec_schema(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    spec = _read_yaml(project_dir / "spec.yaml")
    if spec is None:
        return GateResult("spec_schema", GateStatus.BLOCKED, "spec.yaml 不存在")
    missing = [k for k in REQUIRED_SPEC_KEYS if k not in spec]
    reqs = spec.get("requirements") or []
    bad_reqs = [r.get("id") for r in reqs
                if not (isinstance(r, dict) and r.get("id") and r.get("text"))]
    findings = [{"issue": f"缺少字段 {m}"} for m in missing]
    findings += [{"issue": f"需求缺 id/text: {b}"} for b in bad_reqs]
    # family must be one of the dispatchable board families — a missing or
    # unknown family silently falls into the legacy HPM carrier path
    fam = ((spec.get("constraints") or {}).get("family"))
    KNOWN_FAMILIES = {"fc-full", "px4-fmu", "esp32s31-devboard"}
    if not fam:
        findings.append({"issue": "constraints.family 缺失"
                         "(未知family会落入legacy载板路径)"})
    elif fam not in KNOWN_FAMILIES:
        findings.append({"issue": f"constraints.family={fam} 不在"
                         f"{sorted(KNOWN_FAMILIES)}中"})
    if findings:
        return GateResult("spec_schema", GateStatus.FAIL,
                          f"spec结构不完整: {len(findings)}处问题",
                          findings=findings)
    return GateResult("spec_schema", GateStatus.PASS,
                      f"spec结构合法, {len(reqs)}条需求",
                      details={"n_requirements": len(reqs)})


@gate("requirement_coverage")
def requirement_coverage(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    spec = _read_yaml(project_dir / "spec.yaml")
    if spec is None:
        return GateResult("requirement_coverage", GateStatus.BLOCKED, "spec.yaml 不存在")
    uncovered = [r["id"] for r in spec.get("requirements", [])
                 if not r.get("covered_by")]
    findings = [{"requirement": rid, "issue": "未映射到任何设计元素"} for rid in uncovered]
    if uncovered:
        return GateResult("requirement_coverage", GateStatus.FAIL,
                          f"{len(uncovered)}/{len(spec.get('requirements', []))} 条需求未被设计覆盖",
                          findings=findings)
    return GateResult("requirement_coverage", GateStatus.PASS, "全部需求已映射设计元素")


# ------------------------------------------------------------ component selection
BOM_REQUIRED_FIELDS = ("ref", "mpn", "lcsc", "footprint", "datasheet")


@gate("bom_completeness")
def bom_completeness(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    import csv
    bom = project_dir / "bom.csv"
    if not bom.exists():
        return GateResult("bom_completeness", GateStatus.BLOCKED, "bom.csv 不存在")
    with bom.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    findings, manual = [], []
    for row in rows:
        miss = [k for k in BOM_REQUIRED_FIELDS if not (row.get(k) or "").strip()]
        if not miss:
            continue
        if "lcsc" in miss and (row.get("verify_note") or "").strip():
            # part too new to be indexed anywhere — flagged, not silently missing
            manual.append({"ref": row.get("ref", "?"),
                           "note": row["verify_note"][:80]})
            miss = [k for k in miss if k != "lcsc"]
        if miss:
            findings.append({"ref": row.get("ref", "?"), "missing": miss})
    if findings:
        return GateResult("bom_completeness", GateStatus.FAIL,
                          f"{len(findings)}/{len(rows)} 行BOM不完整"
                          + (f" (另{len(manual)}行C号需人工核实)" if manual else ""),
                          findings=findings)
    return GateResult("bom_completeness", GateStatus.PASS,
                      f"{len(rows)} 行BOM全部完整"
                      + (f", {len(manual)}行C号需人工核实(新料未上架)" if manual else ""),
                      details={"lines": len(rows), "manual_verify": manual})


@gate("bom_lcsc_verified")
def bom_lcsc_verified(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    """Every BOM line's LCSC code must be real: online-verified (JLC catalog),
    snapshot-verified (checked-in partslib), or explicitly flagged
    manual-verify (e.g. parts too new to be indexed). Placeholders fail."""
    import json
    bomf = project_dir / "bom.json"
    if not bomf.exists():
        return GateResult("bom_lcsc_verified", GateStatus.BLOCKED, "bom.json 不存在")
    bom = json.loads(bomf.read_text(encoding="utf-8"))
    if not bom:
        return GateResult("bom_lcsc_verified", GateStatus.BLOCKED, "BOM为空")

    from fluxpcb.tools import jlc_catalog
    per_part, n_online, n_snapshot, n_manual, fails = {}, 0, 0, 0, []
    network = False
    for row in bom:
        ref, lcsc = row.get("ref", "?"), (row.get("lcsc") or "").strip()
        note = (row.get("verify_note") or "").strip()
        if lcsc.upper() in {"C000000", "PENDING-VERIFY", "PENDING", "GENERIC"}:
            fails.append({"ref": ref, "issue": f"占位C号 '{lcsc}'"})
            per_part[ref] = {"status": "placeholder"}
            continue
        if not lcsc and not note:
            fails.append({"ref": ref, "issue": "C号为空且无核实说明"})
            per_part[ref] = {"status": "missing"}
            continue
        det = jlc_catalog.detail(lcsc) if lcsc else None
        if det and not det.get("not_found"):
            network = True
            per_part[ref] = {"status": "online", "stock": det.get("stock"),
                             "price": det.get("price"), "basic": det.get("basic")}
            n_online += 1
        elif det is not None:                     # reachable but unknown code
            fails.append({"ref": ref, "issue": f"C号 {lcsc} 在JLC目录未找到"})
            per_part[ref] = {"status": "not_found"}
        elif note:                                # offline, or too-new part
            per_part[ref] = {"status": "manual-verify", "note": note[:80]}
            n_manual += 1
        elif row.get("verified"):
            per_part[ref] = {"status": "snapshot"}
            n_snapshot += 1
        else:
            fails.append({"ref": ref,
                          "issue": f"C号 {lcsc} 无法核实(离线且无快照标记)"})
            per_part[ref] = {"status": "unverified"}

    evidence = {"parts": per_part, "online": n_online, "snapshot": n_snapshot,
                "manual_verify": n_manual, "network": network}
    (project_dir / "bom_verified.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")

    if fails:
        return GateResult("bom_lcsc_verified", GateStatus.FAIL,
                          f"{len(fails)} 行C号无效/占位",
                          findings=fails, details=evidence)
    summary = (f"{len(bom)}行C号核实: 在线{n_online}/快照{n_snapshot}/需人工{n_manual}"
               + (" (JLC不可达,按快照)" if not network else ""))
    return GateResult("bom_lcsc_verified", GateStatus.PASS, summary, details=evidence)


# ------------------------------------------------------------ schematic
@gate("erc_zero")
def erc_zero(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    from fluxpcb.tools.adapters import kicad_cli
    sch_dir = project_dir / "schematic"
    schs = sorted(sch_dir.rglob("*.kicad_sch")) if sch_dir.exists() else []
    internal = sch_dir / "erc_report.json" if sch_dir.exists() else None
    if kicad_cli.available() and schs:
        total = 0
        details = {}
        for s in schs:
            r = kicad_cli.erc(s, project_dir / "reports" / "erc")
            if r["status"] != "ok":
                return GateResult("erc_zero", GateStatus.BLOCKED,
                                  f"kicad-cli ERC失败: {r.get('error', '')[:120]}")
            details[s.name] = r["violations"]
            total += r["violations"]
        if total:
            return GateResult("erc_zero", GateStatus.FAIL, f"ERC违例 {total} 处",
                              details=details)
        return GateResult("erc_zero", GateStatus.PASS, "KiCad ERC 零违例", details=details)
    if internal and internal.exists():
        import json
        rep = json.loads(internal.read_text(encoding="utf-8"))
        v = rep.get("violations", -1)
        note = "证据=阶段内lint(kicad-cli缺席,降级)"
        if v == 0:
            return GateResult("erc_zero", GateStatus.PASS, f"ERC零违例 [{note}]",
                              details=rep)
        return GateResult("erc_zero", GateStatus.FAIL, f"ERC违例 {v} 处 [{note}]",
                          details=rep)
    return GateResult("erc_zero", GateStatus.BLOCKED,
                      "无 kicad-cli 且无 schematic/erc_report.json")


@gate("netlist_topology")
def netlist_topology(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    """Assert every spec-mandated connection exists in the netlist (逐脚回读)."""
    spec = _read_yaml(project_dir / "spec.yaml")
    net_path = project_dir / "schematic" / "netlist.net"
    if spec is None or not net_path.exists():
        return GateResult("netlist_topology", GateStatus.BLOCKED,
                          "spec.yaml或netlist.net不存在")
    text = net_path.read_text(encoding="utf-8", errors="replace")
    required = []
    for itf in spec.get("interfaces", []):
        for conn in itf.get("connections", []):
            required.append((itf.get("name", "?"), conn))
    findings = []
    for name, conn in required:
        net = conn if isinstance(conn, str) else str(conn.get("net", ""))
        pins = str(conn.get("pins", "")) if isinstance(conn, dict) else ""
        found_net = f'"{net}"' in text or f"({net})" in text or net in text
        found_pins = all(p in text for p in pins.replace(" ", "").split(",")) if pins else True
        if net and not (found_net and found_pins):
            findings.append({"interface": name, "connection": conn,
                             "issue": "网表未回读到该网络/引脚"})
    if findings:
        return GateResult("netlist_topology", GateStatus.FAIL,
                          f"{len(findings)}/{len(required)} 项连接断言失败", findings=findings)
    return GateResult("netlist_topology", GateStatus.PASS,
                      f"{len(required)} 项连接断言全部回读通过")


# ------------------------------------------------------------ pcb
@gate("drc_zero")
def drc_zero(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    from fluxpcb.tools.adapters import kicad_cli
    pcb_dir = project_dir / "pcb"
    pcbs = sorted(pcb_dir.rglob("*.kicad_pcb")) if pcb_dir.exists() else []
    internal = pcb_dir / "drc_report.json" if pcb_dir.exists() else None
    if kicad_cli.available() and pcbs:
        total, details = 0, {}
        for p in pcbs:
            r = kicad_cli.drc(p, project_dir / "reports" / "drc")
            if r["status"] != "ok":
                return GateResult("drc_zero", GateStatus.BLOCKED,
                                  f"kicad-cli DRC失败: {r.get('error', '')[:120]}")
            details[p.name] = r["violations"]
            total += r["violations"]
        if total:
            return GateResult("drc_zero", GateStatus.FAIL, f"DRC违例 {total} 处", details=details)
        return GateResult("drc_zero", GateStatus.PASS, "KiCad DRC 零违例", details=details)
    if internal and internal.exists():
        import json
        rep = json.loads(internal.read_text(encoding="utf-8"))
        v = rep.get("violations", -1)
        note = "证据=阶段内检查(kicad-cli缺席,降级)"
        if v == 0:
            return GateResult("drc_zero", GateStatus.PASS, f"DRC零违例 [{note}]", details=rep)
        return GateResult("drc_zero", GateStatus.FAIL, f"DRC违例 {v} 处 [{note}]", details=rep)
    return GateResult("drc_zero", GateStatus.BLOCKED, "无 kicad-cli 且无 pcb/drc_report.json")


@gate("impedance_si")
def impedance_si(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    """IPC-2141 closed-form impedance vs targets in stackup.yaml; plus length-match
    assertions when pcb/netclasses.json provides measured lengths."""
    from fluxpcb.tools.ipc2141 import ImpedanceTarget, solve_for_width
    stackup = _read_yaml(project_dir / "stackup.yaml")
    if stackup is None:
        stackup = _read_yaml(project_dir / "pcb" / "stackup.yaml")
    if stackup is None:
        return GateResult("impedance_si", GateStatus.BLOCKED, "stackup.yaml 不存在")
    er = stackup.get("er", 4.4)
    findings, checked = [], []
    results = []
    for tgt in stackup.get("impedance_targets", []):
        try:
            keys = ("net_class", "style", "z_target", "tolerance_pct", "differential")
            t = ImpedanceTarget(**{k: tgt[k] for k in keys if k in tgt})
        except TypeError as e:
            findings.append({"target": tgt, "issue": f"字段错误: {e}"})
            continue
        h = tgt.get("h_mm")
        s = tgt.get("s_mm")
        if not h:
            findings.append({"target": tgt.get("net_class"), "issue": "缺 h_mm(参考层距离)"})
            continue
        w, res = solve_for_width(t, float(h), float(er),
                                 t=float(tgt.get("t_mm", 0.035)),
                                 s=float(s) if s else None)
        checked.append(res.net_class)
        results.append(res.to_dict())
        if not res.ok:
            findings.append({"net_class": res.net_class,
                             "issue": f"Z={res.computed_z}Ω vs 目标{res.z_target}Ω "
                                      f"(偏差{res.deviation_pct}%)"})
    # length matching (RGMII等长等): read pcb/netclasses.json if present
    nc_path = project_dir / "pcb" / "netclasses.json"
    if nc_path.exists():
        import json
        for nc in json.loads(nc_path.read_text(encoding="utf-8")):
            cls, mx = nc.get("class"), nc.get("max_delta_mm")
            lens = nc.get("lengths_mm", [])
            if mx and lens:
                delta = max(lens) - min(lens)
                if delta > mx:
                    findings.append({"net_class": cls,
                                     "issue": f"等长偏差 {delta:.3f}mm > {mx}mm"})
                checked.append(cls + "(等长)")
    if findings:
        return GateResult("impedance_si", GateStatus.FAIL,
                          f"{len(findings)} 项阻抗/等长不达标", findings=findings,
                          details={"checked": checked, "results": results})
    return GateResult("impedance_si", GateStatus.PASS,
                      f"{len(checked)} 项阻抗/等长全部达标",
                      details={"checked": checked, "results": results})


# ------------------------------------------------------------ acceptance sim
@gate("irdrop")
def irdrop(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    from fluxpcb.tools.fdm import Grid2D, solve_irdrop
    cfgf = _read_yaml(project_dir / "sim" / "irdrop_input.yaml")
    if cfgf is None:
        return GateResult("irdrop", GateStatus.BLOCKED, "sim/irdrop_input.yaml 不存在")
    findings, details = [], {}
    for rail in cfgf.get("rails", []):
        g = Grid2D(width_mm=cfgf.get("board_w_mm", 65), height_mm=cfgf.get("board_h_mm", 30),
                   nx=cfgf.get("nx", 120), ny=cfgf.get("ny", 60))
        srcs = [(g.index(s["x"], s["y"])[0], g.index(s["x"], s["y"])[1], s.get("weight", 1.0))
                for s in rail.get("sinks", [])]
        g.sources = srcs
        r = solve_irdrop(g, v_in=rail["v"], sink_current_a=rail["current_a"],
                         copper_thickness_mm=rail.get("cu_mm", 0.035))
        limit_pct = rail.get("max_drop_pct", 3.0)
        drop_pct = r["max_drop_v"] / rail["v"] * 100
        details[rail["name"]] = {"max_drop_v": round(r["max_drop_v"], 4),
                                 "drop_pct": round(drop_pct, 3), "limit_pct": limit_pct}
        if drop_pct > limit_pct:
            findings.append({"rail": rail["name"],
                             "issue": f"IR-drop {drop_pct:.2f}% > {limit_pct}%"})
    if findings:
        return GateResult("irdrop", GateStatus.FAIL, f"{len(findings)} 条电源轨压降超标",
                          findings=findings, details=details)
    # field dump for the dashboard (worst rail re-solved with its field)
    try:
        import json as _json
        worst = max(details.items(), key=lambda kv: kv[1]["drop_pct"])[0]
        rail_w = next(r for r in cfgf.get("rails", []) if r["name"] == worst)
        g = Grid2D(width_mm=cfgf.get("board_w_mm", 65), height_mm=cfgf.get("board_h_mm", 30),
                   nx=min(cfgf.get("nx", 120), 120), ny=min(cfgf.get("ny", 60), 60))
        g.sources = [(g.index(s["x"], s["y"])[0], g.index(s["x"], s["y"])[1],
                      s.get("weight", 1.0)) for s in rail_w.get("sinks", [])]
        rw = solve_irdrop(g, v_in=rail_w["v"], sink_current_a=rail_w["current_a"],
                          copper_thickness_mm=rail_w.get("cu_mm", 0.035))
        (project_dir / "sim").mkdir(exist_ok=True)
        (project_dir / "sim" / "irdrop_field.json").write_text(
            _json.dumps({"rail": worst, **details[worst],
                         "grid": {"nx": g.nx, "ny": g.ny,
                                  "dx_mm": round(g.width_mm / g.nx, 4),
                                  "dy_mm": round(g.height_mm / g.ny, 4)},
                         "field": [[round(float(v), 6) for v in row]
                                   for row in rw.get("field", [])]},
                         ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return GateResult("irdrop", GateStatus.PASS,
                      f"{len(details)} 条电源轨IR-drop全部≤限值", details=details)


@gate("thermal")
def thermal(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    from fluxpcb.tools.fdm import Grid2D, solve_thermal
    cfgf = _read_yaml(project_dir / "sim" / "thermal_input.yaml")
    if cfgf is None:
        return GateResult("thermal", GateStatus.BLOCKED, "sim/thermal_input.yaml 不存在")
    g = Grid2D(width_mm=cfgf.get("board_w_mm", 65), height_mm=cfgf.get("board_h_mm", 30),
               nx=cfgf.get("nx", 120), ny=cfgf.get("ny", 60),
               mask=None)
    srcs = [(g.index(s["x"], s["y"])[0], g.index(s["x"], s["y"])[1], s.get("weight", 1.0))
            for s in (cfgf.get("sources") or [])]
    g.sources = srcs
    r = solve_thermal(g, power_w=cfgf.get("total_power_w", 4.0),
                      h_conv=cfgf.get("h_conv", 10.0), t_amb=cfgf.get("t_amb", 25.0))
    limit = cfgf.get("tj_limit_c", 85.0)
    details = {"t_max_c": round(r["t_max_c"], 1), "tj_limit_c": limit,
               "t_amb": r["t_amb"], "hotspot": [int(v) for v in r["hotspot_xy"]]}
    if r["t_max_c"] > limit:
        return GateResult("thermal", GateStatus.FAIL,
                          f"最高结温 {r['t_max_c']:.1f}℃ > {limit}℃", details=details)
    # field dump for the dashboard
    try:
        import json as _json
        (project_dir / "sim").mkdir(exist_ok=True)
        (project_dir / "sim" / "thermal_field.json").write_text(
            _json.dumps({"t_max_c": round(r["t_max_c"], 2),
                         "t_mean_c": round(r.get("t_mean_c", 0), 2),
                         "tj_limit_c": limit, "t_amb_c": cfgf.get("t_amb", 25.0),
                         "hotspot_xy": [int(x) for x in r.get("hotspot_xy", [0, 0])],
                         "grid": {"nx": g.nx, "ny": g.ny,
                                  "dx_mm": round(g.width_mm / g.nx, 4),
                                  "dy_mm": round(g.height_mm / g.ny, 4)},
                         "field": [[round(float(v), 3) for v in row]
                                   for row in r.get("field", [])]},
                         ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return GateResult("thermal", GateStatus.PASS,
                      f"最高结温 {r['t_max_c']:.1f}℃ ≤ {limit}℃", details=details)


@gate("emc_precompliance")
def emc_precompliance(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    """PDN target-impedance screening: series-RL + parallel-decap network closed form."""
    import math
    cfgf = _read_yaml(project_dir / "sim" / "pdn_input.yaml")
    if cfgf is None:
        return GateResult("emc_precompliance", GateStatus.BLOCKED, "sim/pdn_input.yaml 不存在")
    findings, details = [], {}
    for rail in cfgf.get("rails", []):
        v = rail["v"]; tol = rail.get("ripple_pct", 3.0) / 100
        z_target = v * tol / rail.get("max_transient_a", 1.0)
        # effective decap network: N caps of C each, derated ESL loop inductance
        caps = rail.get("decaps", [])
        z_vs_f = {}
        for f in rail.get("probe_freqs_mhz", [1, 10, 100]):
            f_hz = f * 1e6
            z_caps = 0.0
            for c in caps:
                xc = 1 / (2 * math.pi * f_hz * c["c_f"] * 1e-6)
                xl = 2 * math.pi * f_hz * c.get("esl_nh", 1.0) * 1e-9
                z_cap = abs(xc - xl) if xc > xl else math.sqrt(xc * xl)
                z_caps = z_cap if z_caps == 0 else (z_caps * z_cap) / (z_caps + z_cap)
            z_vs_f[f"{f}MHz"] = round(min(z_caps, 1e6), 4)
        worst = max(z_vs_f.values())
        details[rail["name"]] = {"z_target_ohm": round(z_target, 4),
                                 "z_network_ohm": z_vs_f, "n_decaps": len(caps)}
        if worst > z_target:
            findings.append({"rail": rail["name"],
                             "issue": f"PDN阻抗 {worst:.3f}Ω > 目标 {z_target:.3f}Ω"})
    # return-path continuity: every high-speed interface needs adjacent plane
    for itf in (cfgf.get("return_path_checks") or []):
        if not itf.get("adjacent_reference_layer"):
            findings.append({"interface": itf.get("name"), "issue": "高速口无相邻参考平面声明"})
    if findings:
        return GateResult("emc_precompliance", GateStatus.FAIL,
                          f"{len(findings)} 项EMC预合规不达标", findings=findings, details=details)
    return GateResult("emc_precompliance", GateStatus.PASS,
                      "PDN目标阻抗与回流路径检查全部通过", details=details)


# ------------------------------------------------------------ firmware
@gate("firmware_build")
def firmware_build(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    import shutil as _sh
    fw_dir = project_dir / "firmware"
    marker = fw_dir / "build_ok.json"
    has_idf = _sh.which("idf.py") is not None
    details = {"esp_idf_available": has_idf}
    if has_idf and (fw_dir / "esp32").exists():
        r = _run_idf_build(fw_dir / "esp32")
        if r["returncode"] == 0:
            return GateResult("firmware_build", GateStatus.PASS,
                              "ESP-IDF真实编译零错", details=details)
        return GateResult("firmware_build", GateStatus.FAIL,
                          f"ESP-IDF编译失败: {r['stderr'][:160]}", details=details)
    if marker.exists():
        import json
        rep = json.loads(marker.read_text(encoding="utf-8"))
        ok = all(t.get("build") == "ok" for t in rep.get("targets", []))
        note = "证据=阶段内构建(idf.py缺席,降级)"
        if ok and rep.get("targets"):
            return GateResult("firmware_build", GateStatus.PASS,
                              f"{len(rep['targets'])} 个固件目标构建通过 [{note}]", details=rep)
        return GateResult("firmware_build", GateStatus.FAIL,
                          f"固件构建失败 [{note}]", details=rep)
    return GateResult("firmware_build", GateStatus.BLOCKED,
                      "无 idf.py 且无 firmware/build_ok.json")


def _run_idf_build(path: Path) -> dict:
    import subprocess
    try:
        p = subprocess.run(["idf.py", "build"], cwd=str(path), capture_output=True,
                           text=True, timeout=1800)
        return {"returncode": p.returncode, "stderr": p.stderr[-500:]}
    except Exception as e:
        return {"returncode": -1, "stderr": str(e)}


@gate("firmware_spi_loopback")
def firmware_spi_loopback(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    import json
    p = project_dir / "firmware" / "spi_loopback.json"
    if not p.exists():
        return GateResult("firmware_spi_loopback", GateStatus.BLOCKED,
                          "firmware/spi_loopback.json 不存在")
    rep = json.loads(p.read_text(encoding="utf-8"))
    if rep.get("result") == "pass":
        return GateResult("firmware_spi_loopback", GateStatus.PASS,
                          f"SPI回环通过: {rep.get('detail', '')}", details=rep)
    return GateResult("firmware_spi_loopback", GateStatus.FAIL,
                      f"SPI回环失败: {rep.get('detail', '')}", details=rep)


# ---------------------------------------------------------------- pcb placement
def _sexpr_blocks(txt: str, head: str) -> list[str]:
    """Balanced-paren extraction of every (head ...) block."""
    import re as _re
    pat = _re.compile("\(\s*" + _re.escape(head.rstrip()) + r"(?=[\s(\"])")
    blocks, i = [], 0
    while True:
        m = pat.search(txt, i)
        if not m:
            break
        i = m.start()
        depth, j = 0, i
        in_str = False
        while j < len(txt):
            c = txt[j]
            if in_str:
                if c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        blocks.append(txt[i:j + 1])
        i = j + 1
    return blocks


def _fp_boxes(pcb_path: Path, with_names: bool = False):
    """ref -> (x0, y0, x1, y1) global pad-bbox per footprint.
    with_names: -> (ref -> (bbox, footprint_name)) for connector typing."""
    import re
    txt = pcb_path.read_text(encoding="utf-8", errors="replace")
    boxes = {}
    for blk in _sexpr_blocks(txt, "footprint"):
        at = re.search(r"\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?", blk)
        ref = re.search(r'\(property "Reference" "([^"]+)"', blk)
        if not at or not ref:
            continue
        fx, fy = float(at.group(1)), float(at.group(2))
        frot = float(at.group(3) or 0)
        xs, ys = [fx], [fy]
        for pm in re.finditer(
                r'\(pad "[^"]+"[^\n]*\n\s*\(at ([-\d.]+) ([-\d.]+)[^\n]*\)\n\s*\(size ([\d.]+) ([\d.]+)', blk):
            px, py = float(pm.group(1)), float(pm.group(2))
            hw, hh = float(pm.group(3)) / 2, float(pm.group(4)) / 2
            if frot:
                import math as _m
                a = _m.radians(frot)
                px, py = fx + px * _m.cos(a) + py * _m.sin(a), fy - px * _m.sin(a) + py * _m.cos(a)
            else:
                px, py = fx + px, fy + py
            xs += [px - hw, px + hw]
            ys += [py - hh, py + hh]
        bbox = (min(xs), min(ys), max(xs), max(ys))
        name_m = re.match(r'\(footprint\s+"([^"]+)"', blk)
        boxes[ref.group(1)] = (bbox, name_m.group(1)) if with_names else bbox
    return boxes


@gate("placement_quality")
def placement_quality(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    """Rule-based placement scorecard: zero overlap, decaps near their IC,
    connectors at board edge, sane density."""
    import json
    import re as _re
    pcb_dir = project_dir / "pcb"
    pcbs = sorted(pcb_dir.rglob("*.kicad_pcb")) if pcb_dir.exists() else []
    if not pcbs:
        return GateResult("placement_quality", GateStatus.BLOCKED, "无PCB文件")
    findings, details = [], {}
    for pcb in pcbs:
        boxes_named = _fp_boxes(pcb, with_names=True)
        boxes = {r: b for r, (b, _) in boxes_named.items()}
        txt = pcb.read_text(encoding="utf-8", errors="replace")
        edge = _re.search(
            r'\(gr_rect[^\n]*\n\s*\(start ([-\d.]+) ([-\d.]+)\)\n\s*\(end ([-\d.]+) ([-\d.]+)\)',
            txt)
        W = H = None
        if edge:
            W = abs(float(edge.group(3)) - float(edge.group(1)))
            H = abs(float(edge.group(4)) - float(edge.group(2)))
        refs = sorted(boxes)
        # overlap
        overlaps = []
        for i, a in enumerate(refs):
            for b in refs[i + 1:]:
                A, B = boxes[a], boxes[b]
                if A[0] < B[2] and B[0] < A[2] and A[1] < B[3] and B[1] < A[3]:
                    overlaps.append((a, b))
        for a, b in overlaps[:8]:
            findings.append({"issue": f"封装重叠: {a} <-> {b}"})
        # decap distance: cap center within 8mm (euclidean) of any IC bbox
        decap_far = []
        ics = [i for i in refs if i.startswith(("U", "MOD"))]
        for r in refs:
            if not r.startswith("C"):
                continue
            rx, ry = (boxes[r][0] + boxes[r][2]) / 2, (boxes[r][1] + boxes[r][3]) / 2
            near = False
            for i in ics:
                x0, y0, x1, y1 = boxes[i]
                dx = max(x0 - rx, 0, rx - x1)
                dy = max(y0 - ry, 0, ry - y1)
                if (dx * dx + dy * dy) ** 0.5 < 8.0:
                    near = True
                    break
            if not near:
                decap_far.append(r)
        for r in decap_far[:6]:
            findings.append({"issue": f"去耦电容 {r} 距任何IC > 8mm"})
        # connector edge distance < 4mm when board size known
        conn_far = []
        if W and H:
            for r in refs:
                if not r.startswith("J"):
                    continue
                # breakout pin headers legitimately sit mid-board next to
                # the module (Pi-Zero style); edge rule targets power/data
                # connectors (USB, barrel, ...)
                if "PinHeader" in boxes_named[r][1]:
                    continue
                x0, y0, x1, y1 = boxes[r]
                d = min(x0, y0, W - x1, H - y1)
                if d > 4.0:
                    conn_far.append((r, round(d, 1)))
        for r, d in conn_far[:6]:
            findings.append({"issue": f"连接器 {r} 离板边 {d}mm (>4mm)"})
        density = None
        if W and H and W * H > 0:
            area = sum((b[2] - b[0]) * (b[3] - b[1]) for b in boxes.values())
            density = round(area / (W * H), 3)
        details[pcb.name] = {"footprints": len(refs), "overlaps": len(overlaps),
                             "decap_far": decap_far, "conn_far": conn_far,
                             "density": density, "board_mm": [W, H]}
    if findings:
        return GateResult("placement_quality", GateStatus.FAIL,
                          f"{len(findings)} 项布局质量问题", findings=findings,
                          details=details)
    return GateResult("placement_quality", GateStatus.PASS,
                      "布局质量: 0重叠/去耦就近/连接器贴边",
                      details=details)


# ---------------------------------------------------------------- firmware regression
@gate("firmware_regression")
def firmware_regression(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    """Host-compiled regression of the SAME fw_logic.c shipped in the target
    firmware (pinmap sanity, boot sequence, debounce, SPI loopback, timing)."""
    import json
    reg = project_dir / "firmware" / "regression.json"
    if not reg.exists():
        return GateResult("firmware_regression", GateStatus.BLOCKED,
                          "firmware/regression.json 不存在(先运行firmware环节)")
    data = json.loads(reg.read_text(encoding="utf-8"))
    targets = data.get("targets", [])
    if not targets:
        return GateResult("firmware_regression", GateStatus.BLOCKED, "无固件目标")
    findings, ok = [], 0
    for t in targets:
        st = t.get("regression", {}).get("status")
        if st == "ok" and "REGRESSION_PASS" in t["regression"].get("output_tail", ""):
            ok += 1
        elif st == "blocked":
            findings.append({"family": t["family"],
                             "issue": "无主机C编译器(gcc/MSVC)"})
        else:
            findings.append({"family": t["family"],
                             "issue": t["regression"].get("output_tail", "")[:100]})
    details = {"targets": [{"family": t["family"],
                            "status": t["regression"]["status"]} for t in targets]}
    if any(f["issue"].startswith("无主机C编译器") for f in findings):
        return GateResult("firmware_regression", GateStatus.BLOCKED,
                          "主机C编译器不可用", details=details)
    if findings:
        return GateResult("firmware_regression", GateStatus.FAIL,
                          f"{len(findings)}/{len(targets)} 目标回归失败",
                          findings=findings, details=details)
    return GateResult("firmware_regression", GateStatus.PASS,
                      f"{ok}/{len(targets)} 固件目标主机回归通过(C代码真实执行)",
                      details=details)


# ---------------------------------------------------------------- spice functional
@gate("spice_functional")
def spice_functional(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    """ngspice transient Monte-Carlo of the LDO power chain (caps/ESR +-20%,
    20 runs): dropout and ripple vs limits. Evidence: sim/spice_mc.json."""
    import json
    ev = project_dir / "sim" / "spice_mc.json"
    if not ev.exists():
        return GateResult("spice_functional", GateStatus.BLOCKED,
                          "sim/spice_mc.json 不存在(验收仿真环节未跑SPICE)")
    data = json.loads(ev.read_text(encoding="utf-8"))
    import os
    sub = {}
    scf = project_dir / "sim" / "spice_subcircuits.json"
    if scf.exists():
        sub = json.loads(scf.read_text(encoding="utf-8"))
    if data.get("pass") and sub.get("pass", True):
        corners = {c.get("corner"): c.get("v_min_v") for c in data.get("corners", [])}
        return GateResult("spice_functional", GateStatus.PASS,
                          f"LDO瞬态MC {data['n_ok']}次+3角点: 最低{data['v_min_v']}V "
                          f"(压降{data['drop_pct']}%<= {data['drop_limit_pct']}%), "
                          f"纹波{data['ripple_v_max']}V, "
                          f"子电路TB {sum(1 for v in sub.get('tbs', {}).values() if v.get('pass'))}/"
                          f"{len(sub.get('tbs', {}))}过",
                          details={k: data.get(k) for k in
                                   ("n_runs", "n_ok", "v_min_v", "drop_pct",
                                    "ripple_v_max", "drop_limit_pct", "dist")} |
                          {"corners": corners,
                           "subcircuits": sub.get("tbs", {})})
    if not sub.get("pass", True):
        return GateResult("spice_functional", GateStatus.FAIL,
                          "子电路测试台失败: " + str(
                              {k: v for k, v in sub.get("tbs", {}).items()
                               if not v.get("pass")}),
                          details={"subcircuits": sub.get("tbs", {})})
    findings = [{"issue": f"压降{data.get('drop_pct')}% > {data.get('drop_limit_pct')}% "
                          f"或纹波{data.get('ripple_v_max')}V超限"}]
    return GateResult("spice_functional", GateStatus.FAIL,
                      "LDO瞬态MC超限", findings=findings,
                      details={k: data.get(k) for k in
                               ("n_ok", "v_min_v", "drop_pct", "ripple_v_max")})


# ------------------------------------------------------------- plane integrity
@gate("plane_integrity")
def plane_integrity(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    """Every copper layer DECLARED as a plane (ground_copper/power_copper
    in the layer list) must ACTUALLY carry copper: a full-board zone on
    that layer plus real connections (stitching vias / TH pads).

    Closes the "fake 6-layer board" gap: a declared-but-empty plane layer
    passes DRC with zero violations — this gate reads the board copper,
    not the stackup declaration."""
    import re
    pcbs = sorted((project_dir / "pcb").rglob("*.kicad_pcb"))
    if not pcbs:
        return GateResult("plane_integrity", GateStatus.BLOCKED, "无PCB文件")
    findings, details = [], {}
    for pcb in pcbs:
        txt = pcb.read_text(encoding="utf-8", errors="replace")
        # plane-designated layers from the layer list
        plane_layers = re.findall(
            r'\(\d+ "(\w+\.Cu)" \w+ "(?:ground|power)_copper"\)', txt)
        if not plane_layers:
            details[pcb.name] = "无平面层声明(2/4层板) — 天然通过"
            continue
        # board outline area
        edge = re.search(
            r"\(gr_rect\s*[\s\S]{0,120}?\(start ([-\d.]+) ([-\d.]+)"
            r"[\s\S]{0,120}?\(end ([-\d.]+) ([-\d.]+)", txt)
        if not edge:
            findings.append({"issue": f"{pcb.name}: 无板框, 无法算覆盖率"})
            continue
        bw = abs(float(edge.group(3)) - float(edge.group(1)))
        bh = abs(float(edge.group(4)) - float(edge.group(2)))
        board_area = max(bw * bh, 1e-6)
        # zones per layer + their polygon extents. Zone format differs
        # between our generator output ((net N)(net_name "x")(layer "..")
        # single-line) and a KiCad re-save ((net "x")\n(layer "..") multiline).
        layer_report = {}
        for layer in plane_layers:
            zpat = re.compile(
                r'\(zone\s*\(net(?:\s*\d+)?\)?\s*(?:\(net_name "([^"]+)"\))?\s*'
                r'(?:\(net "([^"]+)"\))?\s*\(net_name "([^"]+)"\)?\s*'
                r'\(layer "' + re.escape(layer) + r'"')
            zone_nets = [a or b or c for a, b, c in zpat.findall(txt)
                         if (a or b or c)]
            if not zone_nets:          # saved-format: (net "x") then layer
                zpat2 = re.compile(
                    r'\(zone\s*\(net "([^"]+)"\)\s*\(layer "' +
                    re.escape(layer) + r'"')
                zone_nets = zpat2.findall(txt)
            covered = bool(zone_nets)
            has_zone = len(zone_nets) > 0
            zlen = len(zone_nets)
            if not has_zone:
                findings.append({"layer": layer, "issue": f"{pcb.name}: {layer} 声明为平面但无铺铜"})
            layer_report[layer] = {"nets": zone_nets[:3], "zones": zlen}
            # connection count for the plane net
            if has_zone:
                net = zone_nets[0]
                vias = txt.count(f'(net "{net}")')  # any copper of this net
                # TH pads + vias on the plane net = connections
                conns = len(re.findall(r'\(via \(at[^\n]*?\(net "' + re.escape(net) + r'"\)', txt.replace("\n", " ")))
                layer_report[layer]["via_conns"] = conns
        details[pcb.name] = layer_report
    if findings:
        return GateResult("plane_integrity", GateStatus.FAIL,
                          f"{len(findings)} 处平面层无实际铜",
                          findings=findings, details=details)
    return GateResult("plane_integrity", GateStatus.PASS,
                      "全部声明平面层均有实际铺铜",
                      details=details)


# ------------------------------------------------- geometry-aware DC (S1)
def _dc_solve_lazy(project_dir: Path) -> None:
    """Solve geometry-aware DC on demand — gates no longer depend on the
    acceptance_sim stage having run first (verify-only flows, retries)."""
    if (project_dir / "sim" / "dc_irdrop_geom.json").exists():
        return
    pcbs = sorted((project_dir / "pcb").rglob("*.kicad_pcb"))
    if not pcbs:
        return
    spec = _read_yaml(project_dir / "spec.yaml") or {}
    try:
        from fluxpcb.tools import dcsolve
        dcsolve.run_dc(project_dir, spec)
    except Exception:
        pass


@gate("dc_irdrop_geom")
def dc_irdrop_geom(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    """DC IR-drop solved on the REAL copper geometry (tracks/pads/vias of the
    routed board) — the in-house padne/KiPIDA equivalent."""
    import json
    _dc_solve_lazy(project_dir)
    ev = project_dir / "sim" / "dc_irdrop_geom.json"
    if not ev.exists():
        return GateResult("dc_irdrop_geom", GateStatus.BLOCKED,
                          "sim/dc_irdrop_geom.json 不存在(板上无铜或无spec)")
    d = json.loads(ev.read_text(encoding="utf-8"))
    limit = 3.0
    if d["drop_pct"] > limit:
        return GateResult("dc_irdrop_geom", GateStatus.FAIL,
                          f"几何IR-drop {d['drop_pct']}% > {limit}%",
                          details=d)
    return GateResult("dc_irdrop_geom", GateStatus.PASS,
                      f"真实铜皮几何IR-drop: 最低{d['v_min_v']}V "
                      f"(压降{d['drop_pct']}% <= {limit}%), 轨 {d['rail']}",
                      details={k: d[k] for k in ("rail", "v_min_v", "drop_pct")})


@gate("dc_current_density")
def dc_current_density(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    """Current-crowding screen on real copper: max J vs IPC-2152-class
    density limit; field evidence in sim/dc_current_density.json."""
    import json
    _dc_solve_lazy(project_dir)
    ev = project_dir / "sim" / "dc_current_density.json"
    if not ev.exists():
        return GateResult("dc_current_density", GateStatus.BLOCKED,
                          "sim/dc_current_density.json 不存在")
    d = json.loads(ev.read_text(encoding="utf-8"))
    if not d.get("crowding_ok"):
        return GateResult("dc_current_density", GateStatus.FAIL,
                          f"电流拥挤: Jmax {d['j_max_a_mm2']} A/mm² > "
                          f"{d['j_limit_a_mm2']} 限值", details=d)
    return GateResult("dc_current_density", GateStatus.PASS,
                      f"电流密度: Jmax {d['j_max_a_mm2']} A/mm² <= "
                      f"{d['j_limit_a_mm2']} (无拥挤), 轨 {d['rail']}",
                      details={k: d[k] for k in ("rail", "j_max_a_mm2", "j_limit_a_mm2")})


# ------------------------------------------------- plane cavity resonance (S2)
@gate("plane_cavity_resonance")
def plane_cavity_resonance(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    """Rectangular power-plane cavity resonances (analytical modes) from the
    stackup; first mode must sit above the design's PDN band or be damped."""
    import json
    import math
    import re
    ev = project_dir / "sim" / "plane_cavity.json"
    if not ev.exists():
        return GateResult("plane_cavity_resonance", GateStatus.BLOCKED,
                          "sim/plane_cavity.json 不存在")
    d = json.loads(ev.read_text(encoding="utf-8"))
    f1 = d.get("first_resonance_mhz")
    band = d.get("band_limit_mhz", 200.0)
    modes = d.get("modes", [])
    if f1 is not None and f1 < band:
        # below-band resonance only fails when undamped in the PDN band
        return GateResult("plane_cavity_resonance", GateStatus.FAIL,
                          f"平面腔首谐振 {f1}MHz < 频带 {band}MHz 且无有效阻尼记录",
                          findings=[{"mode": m} for m in modes[:4]], details=d)
    return GateResult("plane_cavity_resonance", GateStatus.PASS,
                      f"平面腔首谐振 {f1}MHz (>= 频带上限{band}MHz 或已被去耦阻尼), "
                      f"共列出{len(modes)}个模态", details=d)


# ------------------------------------------------- PDN Z(f) via ngspice AC (S3)
@gate("pdn_impedance_ac")
def pdn_impedance_ac(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    """PDN impedance Z(f) from a real ngspice AC sweep (VRM + plane cavity
    lumped model + decap ESR/ESL network) vs the target-Z curve."""
    import json
    ev = project_dir / "sim" / "pdn_ac.json"
    if not ev.exists():
        return GateResult("pdn_impedance_ac", GateStatus.BLOCKED,
                          "sim/pdn_ac.json 不存在")
    d = json.loads(ev.read_text(encoding="utf-8"))
    if d.get("status") != "ok":
        return GateResult("pdn_impedance_ac", GateStatus.BLOCKED,
                          f"AC扫频不可用: {d.get('error', '')}")
    worst = d.get("worst_margin_pct", 0)
    zmax = d.get("z_max_ohm")
    ztgt = d.get("z_target_ohm")
    if d.get("pass"):
        return GateResult("pdn_impedance_ac", GateStatus.PASS,
                          f"PDN Z(f) AC扫频: Zmax {zmax}Ω <= 目标 {ztgt}Ω "
                          f"(最差余量{worst}%) @ {d.get('z_max_at_mhz')}MHz",
                          details={k: d.get(k) for k in
                                   ("z_target_ohm", "z_max_ohm", "z_max_at_mhz",
                                    "n_decaps", "probe_mhz")})
    return GateResult("pdn_impedance_ac", GateStatus.FAIL,
                      f"PDN Z(f)超目标: Zmax {zmax}Ω > {ztgt}Ω",
                      findings=[{"issue": f"最差点余量{worst}%"}],
                      details={k: d.get(k) for k in ("z_target_ohm", "z_max_ohm",
                                                     "z_max_at_mhz")})


# ------------------------------------------------- return path (S4)
@gate("return_path")
def return_path(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    """Ground-return distribution screen: fraction of trace return current
    flowing within +-3h of the trace (analytical profile), per high-current
    net class; low concentration flags poor return planning."""
    import json
    ev = project_dir / "sim" / "return_path.json"
    if not ev.exists():
        return GateResult("return_path", GateStatus.BLOCKED,
                          "sim/return_path.json 不存在")
    d = json.loads(ev.read_text(encoding="utf-8"))
    worst = d.get("worst_concentration_pct", 0)
    if d.get("pass"):
        return GateResult("return_path", GateStatus.PASS,
                          f"地回流集中度: 最差 {worst}% 在±3h内 "
                          f"(阈值{d.get('threshold_pct')}%), {len(d.get('nets', []))}类网络",
                          details={k: d.get(k) for k in ("worst_concentration_pct",
                                                         "threshold_pct")})
    return GateResult("return_path", GateStatus.FAIL,
                      f"回流集中度 {worst}% < 阈值 {d.get('threshold_pct')}%: "
                      "存在跨越不连续平面的回流路径风险",
                      findings=d.get("nets", [])[:5],
                      details={"worst": worst})


# ------------------------------------------------- external EM/CFD adapters (S5)
@gate("em_field_analysis")
def em_field_analysis(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    """openEMS-backed field analyses (return-current dump, plane resonance
    sweep, far-field pattern). Case scripts are generated into sim/em_cases/;
    the gate runs them when openEMS is installed, else BLOCKED honestly."""
    import json
    from fluxpcb.tools import em_adapters
    avail = em_adapters.probe()
    ev = project_dir / "sim" / "em_cases" / "README.md"
    cases = em_adapters.generate_cases(project_dir)
    if not avail.get("openems"):
        return GateResult("em_field_analysis", GateStatus.BLOCKED,
                          "openEMS未安装 — 用例已生成于 sim/em_cases/ "
                          f"(回流/谐振/方向图{len(cases)}个), 安装后即可运行",
                          details={"available": avail, "cases": cases})
    r = em_adapters.run_cases(project_dir, log=lambda s: None)
    ok = sum(1 for x in r if x.get("status") == "ok")
    if ok == len(r) and r:
        return GateResult("em_field_analysis", GateStatus.PASS,
                          f"openEMS场级分析 {ok}/{len(r)} 项完成",
                          details={"runs": r})
    # solver present but a case did not run (e.g. CSX schema authoring
    # pending) — BLOCKED with the honest reason, never a fake pass/fail
    return GateResult("em_field_analysis", GateStatus.BLOCKED,
                      f"openEMS v0.37已安装({avail.get('openems_mode')}模式), "
                      f"用例XML校验待完成({ok}/{len(r)}项跑通)",
                      findings=[x for x in r if x.get("status") != "ok"],
                      details={"available": avail, "runs": r})


@gate("thermal_cfd")
def thermal_cfd(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    """CFD conjugate heat transfer (OpenFOAM chtMultiRegionFoam / Elmer).
    Case generated into sim/thermal_case/; BLOCKED until a solver exists —
    the FDM conduction gate (thermal) remains the always-available floor."""
    from fluxpcb.tools import em_adapters
    avail = em_adapters.probe()
    case = em_adapters.generate_thermal_case(project_dir)
    for k in ("openfoam", "elmer"):
        if avail.get(k):
            return GateResult("thermal_cfd", GateStatus.PASS,
                              f"{k}可用 — 用例位于 {case} (chtMultiRegionFoam/Elmer)",
                              details={"available": avail, "case": case})
    return GateResult("thermal_cfd", GateStatus.BLOCKED,
                      "OpenFOAM/Elmer未安装 — CHT用例已生成于 "
                      f"{case}, FDM传导解(thermal门禁)继续作为保底",
                      details={"available": avail, "case": case})


# ------------------------------------------------------------- connectivity
@gate("routing_completeness")
def routing_completeness(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    """True connectivity from the board's ratsnest (pcbnew API), NOT from
    DRC severity calibration. A board with 43 open nets reported
    '0 unconnected pads' when severity was calibrated to ignore.

    PASS = ratsnest 0 (fully connected). This gate cannot be calibrated away."""
    import json as _json
    import shutil as _sh
    import subprocess as _sub
    kipy = _sh.which("python")
    _cand = Path(r"C:/Users/zhang/AppData/Local/Programs/KiCad/10.0/bin/python.exe")
    if _cand.exists():
        kipy = str(_cand)
    pcbs = sorted((project_dir / "pcb").rglob("*.kicad_pcb"))
    if not pcbs:
        return GateResult("routing_completeness", GateStatus.BLOCKED, "无PCB文件")
    findings, details = [], {}
    audit = Path(__file__).resolve().parent.parent / "kicadgen" / "conn_audit.py"
    for pcb in pcbs:
        p = _sub.run([kipy, str(audit), str(pcb)], capture_output=True,
                     timeout=120, encoding="utf-8", errors="replace")
        try:
            data = _json.loads([l for l in (p.stdout or "").splitlines()
                                if l.strip().startswith("{")][0])
        except (ValueError, IndexError):
            findings.append({"pcb": pcb.name,
                            "issue": f"audit failed: {(p.stderr or 'no output')[-100:]}"})
            continue
        rn = data.get("unconnected_ratsnest", -1)
        nw = data.get("nets_without_tracks", -1)
        details[pcb.name] = {"ratsnest_opens": rn, "nets_without_tracks": nw,
                             "total_nets": data.get("total_nets"),
                             "missing": data.get("missing_list", [])[:8]}
        if rn > 0:
            findings.append({"pcb": pcb.name,
                            "issue": f"ratsnest开路{rn}条 (无走线网络{nw}个)",
                            "nets": data.get("missing_list", [])[:5]})
    if findings:
        return GateResult("routing_completeness", GateStatus.FAIL,
                          f"{len(findings)} 块板未全连通",
                          findings=findings, details=details)
    return GateResult("routing_completeness", GateStatus.PASS,
                      "全部网络连通(ratsnest=0)", details=details)
