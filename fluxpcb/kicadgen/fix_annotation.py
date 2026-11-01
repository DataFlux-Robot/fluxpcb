# -*- coding: utf-8 -*-
"""fix_annotation — 位号注解注入(N1: REF** 封装写回真实位号).

truthnet 7 注解差异(REF** vs U3)的上游修复: 把缺失位号写回封装。
判定 = truthnet 审计(annotation_map 命中+pin_mismatch 归零)。
Usage: fix_annotation.py PCB {"REF**": "U3"}
"""
import json
import sys
from pathlib import Path

import pcbnew

pcb_path = Path(sys.argv[1])
mapping = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

b = pcbnew.LoadBoard(str(pcb_path))
fixed = []
for fp in b.GetFootprints():
    ref = fp.GetReference()
    if ref in mapping:
        fp.SetReference(mapping[ref])
        fixed.append((ref, mapping[ref]))
b.Save(str(pcb_path))
print(json.dumps({"fixed": fixed, "n": len(fixed)}))
