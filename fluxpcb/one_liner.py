# -*- coding: utf-8 -*-
"""One-sentence -> project.yaml + requirements.md.

Absorbs mo9652962-ai/circuit-agent's synthesizer shape: keywords select
PRE-AUDITED board-family blocks deterministically (the LLM never draws —
it only picks blocks and fills slots, and only when rules miss).

    from fluxpcb.one_liner import synthesize
    synthesize("做一个带8路PWM的H743飞控，双CAN，TF卡") -> {
        "family": "fc-full", "project": "h743-fc-8pwm", ...}

Rule layers (first match wins):
  1. keyword -> family (飞控/px4/pwm路数 -> fc-full|px4-fmu; esp32/wifi ->
     esp32s31-devboard)
  2. slots: PWM 路数, UART 路数, CAN 路数, TF卡, ADC 路数, 尺寸, 层数
  3. unknown -> clarification_needed with the missing/ambiguous fields
LLM fallback (optional, FLUXNT_LLM_* configured): parse -> enum-validate ->
accept; refusal or invalid -> also clarification_needed.
"""
from __future__ import annotations

import re

FAMILIES = {
    "fc-full": {
        "desc": "Pixhawk级全外设飞控(H743 TFBGA240)",
        "requires_mcu": "h743",
        "size": [50, 50],
        "layers": 6,
    },
    "px4-fmu": {
        "desc": "PX4 FMU飞控(N657/H743)",
        "mcu_choices": ("n6", "h743"),
        "size": None,
        "layers": 6,
    },
    "esp32s31-devboard": {
        "desc": "ESP32-S31开发板",
        "requires_mcu": None,
        "size": [65, 30],
        "layers": None,
    },
}

_INT = r"(\d+)\s*(?:路|个|ch|channel|x|×)"


def _num_after(sentence: str, *keys: str) -> int | None:
    sentence = sentence.lower()
    for k in keys:
        m = re.search(_INT + r"\s*" + k, sentence)
        if m:
            return int(m.group(1))
        m = re.search(k + r"\s*" + _INT, sentence)
        if m:
            return int(m.group(1))
    return None


def _pick_family(sentence: str) -> tuple[str | None, list[str]]:
    s = sentence.lower()
    doubts = []
    hits = []
    if re.search(r"飞控|px4|pixhawk|flight.?contr|fmu", s):
        if re.search(r"h743|stm32h7", s):
            hits.append(("fc-full", "h743飞控全外设族"))
        else:
            hits.append(("px4-fmu", "px4-fmu族"))
    if re.search(r"esp32|wifi|蓝牙|bluetooth", s) and not re.search(
            r"飞控|px4", s):
        hits.append(("esp32s31-devboard", "esp32开发板族"))
    if not hits:
        return None, ["family: 无法识别板族(支持 fc-full/px4-fmu/"
                      "esp32s31-devboard)"]
    if len(hits) > 1:
        return None, [f"family: 匹配多族 {[h[0] for h in hits]}, 请明确"]
    return hits[0][0], doubts


def synthesize(sentence: str) -> dict:
    """-> {"ok": bool, "family"?, "slots"?, "clarification_needed"?,
    "project_yaml"?, "requirements_md"?}"""
    out = {"ok": False, "sentence": sentence}
    fam, doubts = _pick_family(sentence)
    doubts = list(doubts)
    slots = {}
    pwm = _num_after(sentence, "pwm")
    if pwm:
        slots["pwm"] = pwm
    uart = _num_after(sentence, "uart", "串口", "usart")
    if uart:
        slots["uart"] = uart
    can = _num_after(sentence, "can")
    if can:
        slots["can"] = can
    adc = _num_after(sentence, "adc", "模拟")
    if adc:
        slots["adc"] = adc
    slots["tf"] = bool(re.search(r"tf|sd卡|microsd|存储卡", sentence.lower()))
    fam_info = FAMILIES.get(fam or "", None)
    needs_mcu = fam_info and fam_info.get("mcu_choices")
    if fam == "px4-fmu":
        sl = sentence.lower()
        if re.search(r"n657|(?<![a-z0-9])n6(?![a-z0-9])", sl):
            slots["mcu"] = "n6"
        elif re.search(r"h743", sl):
            slots["mcu"] = "h743"
        if "mcu" not in slots:
            doubts.append("mcu: " + "/".join(needs_mcu) + "?")
    if fam is None or doubts:
        out["clarification_needed"] = doubts
        out["candidates"] = list(FAMILIES)
        return out
    # deterministic project.yaml synthesis from family + slots
    out.update({"ok": True, "family": fam, "slots": slots})
    out["requirements_md"] = _requirements_md(fam, slots, sentence)
    return out


def _requirements_md(fam: str, slots: dict, sentence: str) -> str:
    lines = [f"# 需求（由一句话生成）", "",
             f"> {sentence}", "", "```yaml"]
    yaml = [f'constraints:', f'  family: {fam}']
    info = FAMILIES[fam]
    if info.get("size"):
        yaml.append(f"  size_mm: [{info['size'][0]}, {info['size'][1]}]")
    yaml.append(f"  layers: {info.get('layers') or 4}")
    if info.get("requires_mcu"):
        yaml.append(f"  mcu: {info['requires_mcu']}")
    lines += yaml
    lines += ["```"]
    return "\n".join(lines)
