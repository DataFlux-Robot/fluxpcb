# -*- coding: utf-8 -*-
"""④ 加工闭环 — fab-margin waiver for clearance near-misses.

本质:设计规则(0.15mm)比板厂真实能力(JLC 4/4mil≈0.1mm)更严。一个
shortfall 仅 2-8μm 的间距违例,其 ACTUAL gap(0.137-0.148mm)远超板厂
最小间距 — 它是"规则过严"而不是"不可加工"。诚实的做法是带证据豁免,
不是掩盖。

豁免机制 = .kicad_dru 命名规则(不是 drc_exclusions 字符串):
  - exclusions 需要 marker 序列化坐标, 而 kicad-cli JSON 报告不含
    marker 位置 — 程序化生成无法保证精确匹配(KiCad 会静默丢弃)。
  - DRC 规则是**工艺能力声明**(对该网对间距下限 = actual), 语义上
    正是"板厂做得到" — 跨位置成立, 不是点状掩盖。规则名带证据。

三分类(verdict, 全部可机检):
  - waived-fab-margin: shortfall ≤ 8μm 且 actual ≥ 板厂能力
  - bend-too-big:      gap 在工艺带内但规则弯曲超窗 — 需布局修复
                       或显式政策决定, 不许用"工艺豁免"偷渡
  - genuine-violation: actual < fab_min — 不可加工, 照实交付

契约:
  1. 证据随行:每条豁免记录 required/actual/fab_min 三值。
  2. 写后探针(T2/ByTaymur 教训:损坏 .kicad_dru 会被 KiCad 静默丢弃、
     DRC 假零):写规则后重跑 DRC, 被豁免网对的近失必须消失, 否则
     PROBE_FAILED 响亮失败。
  3. 终审不隐匿:豁免清单独立成文件随加工包交付。
  4. 报告解析 locale 无关(KiCad 10 中文环境: "间距 0.1500 mm; 实际
     0.1370 mm") — 从描述里抓两个数值, 不做语言匹配。

出处:
  - JLC 4-layer capability: min spacing 0.09-0.1mm (4/4mil)
  - DRC 规则后写优先(DRC_ENGINE::EvalRules 逆序首个命中)
  - write-then-probe: T2 规则表加载探针同款方法论
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

# JLC 标准工艺最小间距 (mm); 4/4mil 能力带
FAB_MIN_MM = 0.1
# C3 政策放行地板: 实际间隙低于此值必须布局手术(不许调低地板凑数,
# ledger 记录地板值, 审计对表)。地板=JLC 能力 0.1 + 20% 裕量。
POLICY_FLOOR_MM = 0.12
POLICY_MAX_BEND_MM = 0.03
# 豁免容差: shortfall ≤ 8μm 视为"规则近失"(measured: 2-8μm 档)
TOL_MM = 0.008

_RE_NUMS = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*mm")
_RE_NET = re.compile(r"\[([^\]]+)\]")


def parse_clearance(report: dict) -> list:
    """kicad-cli --format json 报告 -> 结构化间距违例清单。
    locale 无关: 描述里的两个数值 = (required, actual) 顺序固定;
    网名取 item 描述 [...] 里的第一个。"""
    out = []
    for v in report.get("violations") or []:
        if v.get("type") != "clearance":
            continue
        desc = v.get("description", "")
        nums = _RE_NUMS.findall(desc)
        if len(nums) < 2:
            continue
        req, act = float(nums[0]), float(nums[1])
        items = v.get("items") or []
        nets = []
        for it in items[:2]:
            m = _RE_NET.search(it.get("description", ""))
            if m:
                nets.append(m.group(1))
        pos = (items[0].get("pos") or {}) if items else {}
        out.append({
            "required_mm": req,
            "actual_mm": act,
            "shortfall_mm": round(req - act, 6),
            "nets": nets,
            "pos_mm": (pos.get("x"), pos.get("y")),
            "uuids": [it.get("uuid", "") for it in items[:2]],
            "desc": desc[:120],
        })
    return out


def classify(items: list, fab_min_mm: float = FAB_MIN_MM,
             tol_mm: float = TOL_MM) -> tuple:
    """(waivers, genuine) 分类 — 见模块 docstring。
    C3 政策层: bend-too-big 中 actual >= POLICY_FLOOR_MM(0.12, JLC
    能力 0.1 的 20% 裕量) 且弯曲 <= POLICY_MAX_BEND_MM 的走**显式
    政策放行**(named 规则+evidence+审计计数), 其余必须布局手术。
    地板不许被调低来凑数 —— ledger 记录 floor, 审计对表。"""
    waivers, genuine = [], []
    for it in items:
        if it["shortfall_mm"] <= 0:
            continue
        if any(n.startswith("<") for n in (it.get("nets") or [])):
            # 无名网判定最先: 规则无法匹配, 任何豁免/政策都不适用
            it = dict(it, verdict="unnamed-net",
                      reason="unnamed net pair: rule cannot match")
            genuine.append(it)
        elif it["actual_mm"] < fab_min_mm:
            it = dict(it, verdict="genuine-violation",
                      reason="actual < fab_min — 不可加工")
            genuine.append(it)
        elif it["shortfall_mm"] > tol_mm:
            if (it["actual_mm"] >= POLICY_FLOOR_MM
                    and it["shortfall_mm"] <= POLICY_MAX_BEND_MM):
                it = dict(it, verdict="policy-waiver", policy=True,
                          evidence="policy: actual %smm >= floor %smm"
                                   " (fab_min %smm + 20%%裕量), bend "
                                   "%smm <= %smm — 显式政策放行"
                                   % (it["actual_mm"], POLICY_FLOOR_MM,
                                      fab_min_mm, it["shortfall_mm"],
                                      POLICY_MAX_BEND_MM))
                waivers.append(it)
            else:
                it = dict(it, verdict="bend-too-big",
                          reason="shortfall %smm > %smm 且 actual %smm"
                                 " < 政策地板 %smm — 需布局手术"
                                 % (it["shortfall_mm"], tol_mm,
                                    it["actual_mm"], POLICY_FLOOR_MM))
                genuine.append(it)
        else:
            it = dict(it, verdict="waived-fab-margin",
                      evidence="actual %smm >= fab_min %smm, "
                               "shortfall %smm <= %smm"
                               % (it["actual_mm"], fab_min_mm,
                                  it["shortfall_mm"], tol_mm))
            waivers.append(it)
    return waivers, genuine


def _rule_name(it: dict) -> str:
    nets = "-".join(it.get("nets") or ["?", "?"])
    prefix = "flux_policy" if it.get("policy") else "flux_waiver"
    return "%s_%s_%s" % (prefix, nets,
                         str(it["actual_mm"]).replace(".", "p"))


def write_waivers(dru_path: Path, waivers: list) -> int:
    """豁免 = .kicad_dru 命名规则: 该网对间距下限 = actual(工艺能力
    声明)。无证据/无网名 = 响亮拒绝(契约1)。后写优先。
    同网对合并为**一条**规则取最小 actual —— 一规则对一网对(PWM
    CH1/CH2 双实例双规则重叠, 严格者赢, 0.1315 实例被 0.1397 规则
    判负 = PROBE_FAILED still_waived=1 实证)。"""
    # 按无序网对合并, 保留最小 actual 的证据
    merged = {}
    for it in waivers:
        ev = it.get("evidence")
        nets = it.get("nets") or []
        if not ev or len(nets) < 2:
            raise ValueError("waiver without evidence/nets: %s"
                             % it.get("desc", "?"))
        if any(n.startswith("<") for n in nets):
            raise ValueError("unnamed net cannot be waived: %s"
                             % it.get("desc", "?"))
        key = tuple(sorted(nets[:2]))
        cur = merged.get(key)
        if cur is None or float(it["actual_mm"]) < float(cur["actual_mm"]):
            merged[key] = it
    blocks = []
    for (a, b), it in merged.items():
        cond = ("(A.NetName == '%s' && B.NetName == '%s')"
                " || (A.NetName == '%s' && B.NetName == '%s')"
                % (a, b, b, a))
        blocks.append(
            '(rule "%s"\n'
            '  # fluxpcb fab-margin waiver: %s\n'
            '  (condition "%s")\n'
            '  (constraint clearance (min %smm)))\n'
            % (_rule_name(it), it.get("evidence"), cond,
               round(float(it["actual_mm"]) - 0.001, 4)))
    old = (dru_path.read_text(encoding="utf-8")
           if dru_path.exists() else "")
    if "(version" not in old:
        # KiCad 静默忽略无 (version 1) 头的 .kicad_dru ——
        # 全部豁免规则假生效的根因(PROBE_FAILED 实证)
        old = "(version 1)" + chr(10) + old
    n = 0
    for blk in blocks:
        name = blk.split('"')[1]
        if name in old:
            continue
        old += ("\n" if old and not old.endswith("\n") else "") + blk
        n += 1
    if n:
        dru_path.write_text(old, encoding="utf-8")
    return n


def run_drc_json(kicad_cli: str, pcb: Path) -> dict:
    rpt = pcb.parent / (pcb.stem + ".fabprobe.json")
    subprocess.run([kicad_cli, "pcb", "drc", "--format", "json",
                    "-o", str(rpt), "--severity-all", str(pcb)],
                   capture_output=True, timeout=600)
    return json.loads(rpt.read_text(encoding="utf-8"))


def probe(kicad_cli: str, pcb: Path, waivers: list) -> dict:
    """写后探针: 规则写入后重跑 DRC — 被豁免网对的近失必须消失。
    不消失 = PROBE_FAILED(响亮失败,绝不静默)。"""
    after = run_drc_json(kicad_cli, pcb)
    rem = parse_clearance(after)
    keys = set()
    for it in waivers:
        keys.add((tuple(it["nets"]), round(it["actual_mm"], 4)))
    still = [r for r in rem
             if (tuple(r["nets"]), round(r["actual_mm"], 4)) in keys]
    return {"probe": "PROBE_FAILED" if still else "PROBE_OK",
            "still_waived": len(still),
            "total_clearance_after": len(rem)}


def fab_close(kicad_cli: str, pcb: Path,
              fab_min_mm: float = FAB_MIN_MM,
              tol_mm: float = TOL_MM) -> dict:
    """加工闭环一步: 分类 -> 写规则 -> 探针 -> 豁免清单落盘。"""
    items = parse_clearance(run_drc_json(kicad_cli, pcb))
    waivers, genuine = classify(items, fab_min_mm, tol_mm)
    dru_path = pcb.parent / (pcb.stem + ".kicad_dru")
    if waivers:
        write_waivers(dru_path, waivers)
        pr = probe(kicad_cli, pcb, waivers)
    else:
        pr = {"probe": "NO_WAIVERS", "still_waived": 0,
              "total_clearance_after": len(items)}
    rep = pcb.parent / "fab_waivers.json"
    rep.write_text(json.dumps(
        {"waivers": waivers, "genuine": genuine, "probe": pr,
         "fab_min_mm": fab_min_mm, "tol_mm": tol_mm,
         "policy_floor_mm": POLICY_FLOOR_MM,
         "policy_count": sum(1 for w in waivers if w.get("policy"))},
        ensure_ascii=False, indent=1), encoding="utf-8")
    return {"waivers": len(waivers), "genuine": len(genuine),
            **pr, "report_path": str(rep)}


def export_fab(kicad_cli: str, pcb: Path) -> dict:
    """C5 制造就绪(RF workflow 2608.31006 判据): gerber+钻孔套件
    导出 — 成功判据是"可加工产物在盘", 不是仿真/DRC 通过。"""
    out = pcb.parent / "fab"
    out.mkdir(exist_ok=True)
    try:
        subprocess.run([kicad_cli, "pcb", "export", "gerbers",
                        "-o", str(out), str(pcb)],
                       capture_output=True, timeout=600)
        subprocess.run([kicad_cli, "pcb", "export", "drill",
                        "-o", str(out) + "/", str(pcb)],
                       capture_output=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"gerbers": 0, "error": str(e)[:80]}
    n = len([f for f in out.iterdir()
             if f.suffix.lower() in (".gbr", ".drl", ".gbl", ".gtl",
                                     ".gbo", ".gto", ".gbs", ".gts",
                                     ".gm1", ".gd1", ".gg1")])
    return {"gerbers": n, "out": str(out)}


def fab_package(kicad_cli: str, pcb: Path, sch: Path | None = None,
                fab_min_mm: float = FAB_MIN_MM,
                tol_mm: float = TOL_MM) -> dict:
    """成套加工交付: 裕度豁免(写后探针) + truthnet IPC-D-356 连通性
    终审 — 加工包一单据, 违例/豁免/连通性三账合一。"""
    res = fab_close(kicad_cli, pcb, fab_min_mm, tol_mm)
    res["fab_export"] = export_fab(kicad_cli, pcb)   # C5 可加工产物
    if sch is not None and sch.exists():
        try:
            import sys as _s
            _s.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from kernel import truthnet
            res["truthnet"] = truthnet.audit(kicad_cli, pcb, sch)
        except Exception as e:
            # loud: 终审失败绝不静默成"无问题"
            res["truthnet"] = {"error": str(e)[:120]}
    (pcb.parent / "fab_package.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1),
        encoding="utf-8")
    return res


if __name__ == "__main__":
    import sys
    pcb = Path(sys.argv[1])
    kicad_cli = sys.argv[2] if len(sys.argv) > 2 else "kicad-cli"
    sch = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    print(json.dumps(fab_package(kicad_cli, pcb, sch),
                     ensure_ascii=False))
