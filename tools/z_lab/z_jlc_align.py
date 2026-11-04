# -*- coding: utf-8 -*-
"""z_jlc_align — JLC 工艺规范分级对齐(阶段0.4: 规则基线=真能力线).

嘉立创 6 层 1oz 标准能力: 线宽/线距 0.09/0.09mm(BGA 处线宽 0.076),
多层过孔最小 0.15 内/0.25 外(0.15 钻孔非常规加价, 0.2 常规)。
现行板: Default 0.15 一刀切 + Critical/Power 网类**零分配**(空挂) ——
DRC 全部按 0.15 判, A 类残差(实测 0.09-0.15)是假违规。

分级策略(裕量换违规数, 关键网保裕量):
  critical(26 网: 时钟/CAN/USB/SPI/SWD) -> 0.15(信号完整性, 不降)
  power(4 网: 3V3/5V/GND/VDDA)           -> 0.12(载流裕量)
  normal(44 网)                          -> 0.09(JLC 标准线)

用法: z_jlc_align.py BOARD_DIR
动作: 写 netclass_assignments + 三级 clearance 进 .kicad_pro,
输出对齐清单。DRC 验证由调用方跑(fab_close)。
"""
import json
import sys
from pathlib import Path

board_dir = Path(sys.argv[1])
pcb = sorted(board_dir.glob("*.kicad_pcb"))[0]
pro_path = pcb.with_suffix(".kicad_pro")
nc_path = board_dir / "net_classes.json"

nc = json.loads(nc_path.read_text(encoding="utf-8"))
classes = nc.get("classes", {})

assign = {}
for net, cls in classes.items():
    if cls == "critical":
        assign[net] = "Critical"
    elif cls == "power":
        assign[net] = "Power"
    # normal -> Default(省略, KiCad 默认)

pro = json.loads(pro_path.read_text(encoding="utf-8"))
ns = pro.setdefault("net_settings", {})
ns["netclass_assignments"] = assign

# 全局地板: design_settings.rules.min_clearance 会把分级钳回一刀切
# (实测: netclass 0.09 写入后 required 仍 0.15 —— 地板反制案)
try:
    rules = pro.setdefault("board", {}).setdefault(
        "design_settings", {}).setdefault("rules", {})
    old_floor = rules.get("min_clearance")
    rules["min_clearance"] = 0.09
    print("min_clearance %s -> 0.09" % old_floor)
except Exception as e:
    print("min_clearance 设置失败(响亮):", e)

TIERS = {"Default": 0.09, "Power": 0.12, "Critical": 0.15}
seen = {c.get("name") for c in ns.get("classes", [])}
for c in ns.get("classes", []):
    name = c.get("name")
    if name in TIERS:
        old = c.get("clearance")
        c["clearance"] = TIERS[name]
        print("%-8s clearance %s -> %s" % (name, old, TIERS[name]))
# 类不存在则创建(px4 实证: kicad_pro 只有 Default, Critical/Power
# 空缺 -> 分配无家可归)
for name, val in TIERS.items():
    if name not in seen:
        ns.setdefault("classes", []).append(
            {"name": name, "clearance": val,
             "via_diameter": 0.6, "via_drill": 0.3})
        print("%-8s 类新建 clearance %s" % (name, val))

pro_path.write_text(json.dumps(pro, ensure_ascii=False, indent=2),
                    encoding="utf-8")
print("netclass_assignments: %d 网已分配" % len(assign))
print(json.dumps({"critical": sum(1 for v in assign.values()
                                  if v == "Critical"),
                  "power": sum(1 for v in assign.values()
                               if v == "Power")}))
