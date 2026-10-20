"""Gate registry. A gate is a pure function:

    (project_dir: Path, stage_outputs: list[str], config: dict) -> GateResult

Gates never raise; missing inputs/tools yield GateStatus.BLOCKED with a reason.
"""
from __future__ import annotations

from pathlib import Path

from fluxnt.models import GateResult, GateStatus

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
    if missing or bad_reqs:
        return GateResult("spec_schema", GateStatus.FAIL,
                          f"spec结构不完整: 缺字段{missing or []}, 不合格需求{len(bad_reqs)}条",
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
    findings = []
    for row in rows:
        miss = [k for k in BOM_REQUIRED_FIELDS if not (row.get(k) or "").strip()]
        if miss:
            findings.append({"ref": row.get("ref", "?"), "missing": miss})
    if findings:
        return GateResult("bom_completeness", GateStatus.FAIL,
                          f"{len(findings)}/{len(rows)} 行BOM不完整", findings=findings)
    return GateResult("bom_completeness", GateStatus.PASS,
                      f"{len(rows)} 行BOM全部完整", details={"lines": len(rows)})


# ------------------------------------------------------------ schematic
@gate("erc_zero")
def erc_zero(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    from fluxnt.tools.adapters import kicad_cli
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
    from fluxnt.tools.adapters import kicad_cli
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
    from fluxnt.tools.ipc2141 import ImpedanceTarget, solve_for_width
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
    from fluxnt.tools.fdm import Grid2D, solve_irdrop
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
    return GateResult("irdrop", GateStatus.PASS,
                      f"{len(details)} 条电源轨IR-drop全部≤限值", details=details)


@gate("thermal")
def thermal(project_dir: Path, outputs: list[str], cfg: dict) -> GateResult:
    from fluxnt.tools.fdm import Grid2D, solve_thermal
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
