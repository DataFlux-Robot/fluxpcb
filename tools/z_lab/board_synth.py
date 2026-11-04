# -*- coding: utf-8 -*-
"""board_synth — 板池构建(阶段5③: 多板对抗验证场的板源).

用法: board_synth.py
产出: z_lab/board_pool/manifest.json + 每板 pcb/board/ 工件副本

板池(计划: fc锚 + 合成BGA烤验 + 2层 + held-out插槽; 实测仓库自带
4 块真实板家族 —— 真实板优于合成, 烤验板单独合成):
  fc          = h743-fc(真实锚, A组全)
  px4         = stm32h743-px4(真实, BGA 同构不同布局)
  mini2l      = esp32s31-mini(真实小板, 板层以 stackup 实测为准)
  heldout     = esp32s31-dev(真实, 永不进训练轮, split seed 换)
  torture     = h743-fc 合成烤验变体: 球场逃逸通道随机封堵(种子固定),
                专打逃逸拥塞类(topology-swap/H2b 的跨板存活测试)

每板最小工件(A组硬性): *.kicad_pcb / net_classes.json /
ratsnest_pairs.txt; 由 z_rollout/z_ab 的相对路径契约消费。
"""
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pcbnew

MM = 1000000
REPO = Path("E:/DATA/vscode/ZCODE/flux-neurotransmitter")
LAB = Path("E:/DATA/vscode/ZCODE/z_lab")
POOL = LAB / "board_pool"

# 真实板家族(仓库自带, A组工件实测完备)
REAL_BOARDS = {
    "fc": "h743-fc",
    "px4": "stm32h743-px4",
    "mini2l": "esp32s31-mini",
    "heldout": "esp32s31-dev",
}


def _stage(pool_name: str, proj: str) -> dict:
    src = REPO / "projects" / proj / "pcb" / "board"
    dst = POOL / pool_name / "pcb" / "board"
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)
    pcb = sorted(dst.glob("*.kicad_pcb"))
    ok = bool(pcb) and (dst / "net_classes.json").exists() \
        and (dst / "ratsnest_pairs.txt").exists()
    return {"pool_name": pool_name, "proj": proj,
            "pcb": pcb[0].name if pcb else None,
            "artifacts_ok": ok, "path": str(POOL / pool_name)}


def _torture(src_pool: str = "fc", seed: int = 42,
             n_rips: int = 60, level: str = "medium") -> dict:
    """合成烤验板: 球场逃逸通道封堵(撕逃逸走线让通道死锁)。
    对手进化(流派三): level = easy/medium/hard 对应封堵密度 ——
    解方在课程梯度上训练, 对手参数是可进化对象。种子固定=可复现;
    撕后不重填(重填由消费端做, 单变更纪律)。"""
    LEVELS = {"easy": 30, "medium": 60, "hard": 90}
    n_rips = LEVELS.get(level, n_rips)
    base = POOL / src_pool
    dst = POOL / ("torture-%s" % level)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(base, dst)
    pcb = sorted((dst / "pcb" / "board").glob("*.kicad_pcb"))[0]
    b = pcbnew.LoadBoard(str(pcb))
    # 球场窗口(fc 实测: U1 盒 ~19-32 x 17-30mm), 找 F.Cu 逃逸走线
    X0, X1, Y0, Y1 = 19.0, 32.5, 17.0, 31.0
    cands = []
    for t in b.GetTracks():
        if type(t).__name__ != "PCB_TRACK":
            continue
        s, e = t.GetStart(), t.GetEnd()
        sx, sy = s.x / MM, s.y / MM
        ex, ey = e.x / MM, e.y / MM
        if (X0 <= sx <= X1 and Y0 <= sy <= Y1) or \
                (X0 <= ex <= X1 and Y0 <= ey <= Y1):
            cands.append(t)
    rng = random.Random(seed)
    rng.shuffle(cands)
    ripped = 0
    for t in cands[:n_rips]:
        b.Remove(t)
        ripped += 1
    b.Save(str(pcb))
    return {"pool_name": "torture-%s" % level,
            "proj": f"{src_pool}-torture-{level}-seed{seed}",
            "pcb": pcb.name, "artifacts_ok": True,
            "ripped_escapes": ripped, "path": str(dst),
            "note": "逃逸通道封堵烤验板(拓扑类算子跨板存活测试)"}


def main():
    # --torture <level> 子进程模式: KiCad SWIG 同进程多板变更必崩
    # (Remove+GetTracks 代理失效族), 每级烤验板独立进程
    if len(sys.argv) >= 3 and sys.argv[1] == "--torture":
        t = _torture(level=sys.argv[2])
        (POOL / ("_torture_%s.json" % sys.argv[2])).write_text(
            json.dumps(t, ensure_ascii=False), encoding="utf-8")
        print("%-16s ripped=%s" % (t["pool_name"], t["ripped_escapes"]))
        return
    manifest = {"ts": __import__("time").time(), "boards": []}
    for name, proj in REAL_BOARDS.items():
        e = _stage(name, proj)
        # held-out 换 split seed 防泄漏(z_rollout 的 split_residuals)
        e["split_seed"] = 43 if name == "heldout" else 42
        manifest["boards"].append(e)
        print("%-8s %-16s ok=%s" % (name, proj, e["artifacts_ok"]))
    # 课程梯度(流派三): easy -> medium -> hard, 解方按序通关
    curriculum = []
    for lvl in ("easy", "medium", "hard"):
        subprocess.run([sys.executable, __file__, "--torture", lvl],
                       capture_output=True, timeout=600)
        piece = POOL / ("_torture_%s.json" % lvl)
        if piece.exists():
            t = json.loads(piece.read_text(encoding="utf-8"))
            manifest["boards"].append(t)
            curriculum.append(t["pool_name"])
            print("%-16s ripped=%s" % (t["pool_name"],
                                       t["ripped_escapes"]))
    manifest["curriculum"] = curriculum
    (POOL / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1),
        encoding="utf-8")
    n_ok = sum(1 for b in manifest["boards"] if b["artifacts_ok"])
    print("manifest: %d boards (%d ok)" % (len(manifest["boards"]), n_ok))


if __name__ == "__main__":
    main()
