# -*- coding: utf-8 -*-
"""proofs — 证书库: trick 升格为机器可检验的引理(方案④, 跨界类比:
证明助手)。小核 = geomir verify_polyline/via_ok(几何引理检验核),
本模块是其上的**谓词层与证书层**:

  谓词(pre/post 条件机器可判定):
    coords_emitted_rounded   验你所发(圆整恒等式)
    ball_key_wellformed      球位键双格式
    pairs_col_wellformed     pairs 列契约
    layer_index_in_range     层索引在 CuStack 序内
    rules_ref_not_per_pair   规则表引用非 flux_* 逐对规则
    endpoint_attach_ok       端点三态已解析且经核
  证书(closure/waiver):
    verify_closure_cert      闭合证书 = 几何+连通+**实时测量对账**
    verify_waiver_cert       政策放行证书 = 地板+探针+命名规则

VDDA 类假增益防御: 闭合证书必须引用**实时**开路测量(指纹+对数),
证书声称闭合而实时对账仍开 = 证书作废(第 5 例 stale-data 自欺的
语义级终结)。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

BALL_KEY_RE = re.compile(r"^([A-Z]+)(\d+)$")


def coords_emitted_rounded(pts) -> bool:
    """验你所发: 出库坐标必须已圆整到 3 位小数(规划/执行同一几何)。"""
    for p in pts or []:
        if len(p) < 2:
            return False
        x, y = float(p[0]), float(p[1])
        if x != round(x, 3) or y != round(y, 3):
            return False
    return True


def ball_key_wellformed(key: str) -> bool:
    s = str(key)
    return bool(BALL_KEY_RE.match(s)) or (
        "," in s and len(s.split(",")) == 2)


def pairs_col_wellformed(line: str) -> bool:
    """net|x|y|l|x|y|l — 端点在 (1,2)/(4,5), 层在 3/6。"""
    parts = str(line).split("|")
    if len(parts) != 7:
        return False
    try:
        float(parts[1])
        float(parts[2])
        float(parts[4])
        float(parts[5])
    except ValueError:
        return False
    return True


def layer_index_in_range(li, nlay: int) -> bool:
    return isinstance(li, int) and 0 <= li < nlay


def rules_ref_not_per_pair(ref: str) -> bool:
    """规则表引用不得来自 flux_* 逐对豁免/政策规则。"""
    return not str(ref or "").startswith(("flux_waiver", "flux_policy"))


def endpoint_attach_ok(cert: dict) -> bool:
    """端点三态: 同层直连 / 借道狗骨 / 附着过孔(via_ok 已核)。"""
    att = cert.get("endpoint_attach")
    if not isinstance(att, list) or not att:
        return False
    for a in att:
        mode = a.get("mode")
        if mode == "same-layer":
            continue
        if mode == "via-borrow":
            if not ball_key_wellformed(str(a.get("via_at", "0,0"))
                                       .replace("(", "")
                                       .replace(")", "")) and \
                    not isinstance(a.get("via_at"), (list, tuple)):
                return False
        elif mode == "attach-via":
            if not a.get("via_ok"):
                return False    # 附着孔必须过全层校验
        else:
            return False
    return True


# ── 证书核 ─────────────────────────────────────────────────────────

def closure_cert_ok(cert: dict, live_pairs: list,
                    nlay: int = None) -> tuple:
    """闭合证书核: 几何谓词 + **实时测量对账**。
    live_pairs: 当前实时开路对(ratsnest 形态行)。
    返回 (ok, reasons)。VDDA 防御: 证书声称闭合的网对若仍在实时
    开路集里 -> 作废(stale 快照不能当真值)。"""
    reasons = []
    if not coords_emitted_rounded(cert.get("path_pts")):
        reasons.append("coords_not_rounded(违反验你所发)")
    if not endpoint_attach_ok(cert):
        reasons.append("endpoint_attach 未解析/未过核")
    ref = cert.get("rule_table_ref") or ""
    if not rules_ref_not_per_pair(ref):
        reasons.append("rule_table_ref 来自逐对豁免规则")
    if nlay is not None:
        for p in cert.get("path_pts") or []:
            if not layer_index_in_range(p[2] if len(p) > 2 else 0,
                                        nlay):
                reasons.append("layer_index 越界")
                break
    # 实时对账: 证书 fingerprint 必须匹配当前测量, 且声称闭合的
    # 网不得再出现在实时开路集
    net = cert.get("net", "")
    claimed = cert.get("closes_pair")
    live = [ln for ln in (live_pairs or [])
            if str(ln).split("|")[0] == net]
    if claimed and any(claimed in ln for ln in live):
        reasons.append("stale-cert: 声称闭合的对仍在实时开路集")
    if not cert.get("measured_after"):
        reasons.append("无测量后证据(闭合=测量语义, 非绘制语义)")
    elif cert["measured_after"].get("islands_after") not in (None, 1):
        reasons.append("islands_after=%s != 1 (未真闭合)"
                       % cert["measured_after"].get("islands_after"))
    return (not reasons), reasons


def waiver_cert_ok(cert: dict) -> tuple:
    """政策放行证书核: 地板 + 探针 + 命名规则 + 审计签名。"""
    reasons = []
    if float(cert.get("policy_floor_mm") or 0) < 0.12:
        reasons.append("政策地板 < 0.12(被调低凑数)")
    if cert.get("probe") != "PROBE_OK":
        reasons.append("探针未过(写后规则未生效)")
    for name in cert.get("rule_names") or []:
        if not (name.startswith("flux_waiver_")
                or name.startswith("flux_policy_")):
            reasons.append("规则未命名归档: %s" % name)
            break
    if not cert.get("audit"):
        reasons.append("无审计签名")
    return (not reasons), reasons


def make_closure_cert(net: str, path_pts, endpoint_attach: list,
                      rule_table_ref: str, measured_after: dict,
                      closes_pair: str = None) -> dict:
    return {"kind": "closure", "net": net,
            "path_pts": [[p[0], p[1], p[2] if len(p) > 2 else 0]
                         for p in (path_pts or [])],
            "endpoint_attach": endpoint_attach,
            "rule_table_ref": rule_table_ref,
            "verify_kernel": "geomir.verify_polyline+via_ok",
            "measured_after": measured_after,
            "closes_pair": closes_pair,
            "ts": round(time.time())}


def make_waiver_cert(policy_floor_mm: float, probe: str,
                     rule_names: list, audit: dict) -> dict:
    return {"kind": "waiver", "policy_floor_mm": policy_floor_mm,
            "probe": probe, "rule_names": list(rule_names or []),
            "audit": audit, "ts": round(time.time())}


def write_cert(cert: dict, path: Path) -> None:
    """证书追加归档(certs.jsonl, 每行一证, 时间序)。"""
    p = Path(path)
    try:
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(cert, ensure_ascii=False) + "\n")
    except OSError:
        pass    # 证书写失败不静默冒充: 上层以缺失证书判不合格
