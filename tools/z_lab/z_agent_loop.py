# -*- coding: utf-8 -*-
"""z_agent_loop v4 — 层1 并行攻坚循环(K 槽评估, 最优落板).

v3 -> v4 (加速评估, 串行求和改并行取最大):
  K 槽并行: 每轮对至多 NETS_PER_ROUND 个网 x 各自算子梯 并行发副本施工,
           全部预算封顶(OP_TIMEOUT), 谁先算完谁先好 —— 一轮顶 v3 三轮.
  最优落板: 每轮只落 1 个最优候选(副本互斥铁律: 落板会互相覆盖),
           其余候选全部入账 journal(败者=学习数据, 不浪费).
  proven 回放: 评估通过但没排上落板的配方记入 proven 队列, 下轮快路径
              优先回放(已证配方, 重放即闭合).
  六步纪律不变: 探判改验落记; 副本先行; certs 前缀断言; GAP 一击登记;
              stall 停机归层2; 增量落盘.

判据: opens 曲线 + 逐轮产出(闭合/轮), 只认实测.
用法: z_agent_loop.py [BOARD_DIR=FC_L13] [MAX_ROUNDS=24]
"""
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

LAB = Path("E:/DATA/vscode/ZCODE/z_lab")
REPO = Path("E:/DATA/vscode/ZCODE/flux-neurotransmitter")
KGEN = REPO / "fluxpcb" / "kicadgen"
KIPY = "python"
sys.path.insert(0, str(REPO))
from fluxpcb.routing import find_kicad          # noqa: E402
from fluxpcb.kernel import state as kstate      # noqa: E402

BOARD = Path(sys.argv[1]) if len(sys.argv) > 1 else LAB / "z_v/FC_L13"
MAX_ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 24
WORKROOT = LAB / "z_v" / "FC_L13_work"
JOURNAL = LAB / "z_agent_journal.jsonl"
REPORT = LAB / "z_agent_loop_report.json"

NETS_PER_ROUND = 2
FRAG_NETS = ("GND", "NRST", "UART4_TX")
# 算子梯(每网并行铺开): 长对信号给 harvest 更长预算(900s 曾超时, D3 账)
LADDER_GND = (("zone_bridge", 900), ("power_stitch", 600), ("harvest", 900))
LADDER_SIG = (("seg_harvest", 900), ("harvest", 1800), ("zone_bridge", 900))
DETACH = 0x00000008 | 0x00000200      # 清场盔甲(D 账)

kicad_cli, kipy = find_kicad()
pcb_main = sorted((BOARD / "pcb" / "board").glob("*.kicad_pcb"))[0]


def log(j):
    with open(JOURNAL, "a", encoding="utf-8") as f:
        f.write(json.dumps(j, ensure_ascii=False) + "\n")


def opens_now(pcb):
    q = kstate.query(kicad_cli, kipy, pcb, "opens", consolidate=True)
    return q.value, sorted((q.detail or {}).get("pairs") or [])


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def land(work_board, main_board):
    """原子落板: certs 前缀断言 + 全文件 hash diff 拷贝(单发, 副本互斥)."""
    mb, wb = main_board / "pcb" / "board", work_board / "pcb" / "board"
    mc, wc = mb / "certs.jsonl", wb / "certs.jsonl"
    if mc.exists() and wc.exists():
        ml = mc.read_text(encoding="utf-8").splitlines()
        wl = wc.read_text(encoding="utf-8").splitlines()
        if wl[:len(ml)] != ml:
            return 0, "certs 前缀不一致, 拒绝落板"
    n = 0
    for f in wb.iterdir():
        if not f.is_file():
            continue
        m = mb / f.name
        if not m.exists() or sha(f) != sha(m):
            shutil.copy2(f, m)
            n += 1
    return n, "ok"


def op_cmd(op, pcbw, workdir, net):
    if op == "harvest":
        return [KIPY, "-u", str(LAB / "z_harvest.py"),
                str(workdir / "pcb" / "board"), net]
    if op == "seg_harvest":
        return [KIPY, "-u", str(LAB / "z_seg_harvest.py"),
                str(workdir / "pcb" / "board"), net]
    if op == "zone_bridge":
        return [KIPY, "-u", str(KGEN / "zone_bridge.py"), str(pcbw), net]
    if op == "power_stitch":
        return [KIPY, "-u", str(KGEN / "power_stitch.py"), str(pcbw), net]
    return None


def launch_slots(net_ladder_pairs):
    """并行铺开所有槽: [(net, op, timeout, workdir, Popen)]; 每槽全新副本."""
    slots = []
    for net, ladder in net_ladder_pairs:
        for op, tmo in ladder:
            wd = WORKROOT / ("%s__%s" % (net, op))
            if wd.exists():
                shutil.rmtree(wd, ignore_errors=True)
            shutil.copytree(BOARD, wd)
            pcbw = sorted((wd / "pcb" / "board").glob("*.kicad_pcb"))[0]
            cmd = op_cmd(op, pcbw, wd, net)
            if cmd is None:
                continue
            o0, pairs0 = opens_now(pcbw)
            if o0 < 0:
                continue
            f = open(wd / "_slot.log", "w", encoding="utf-8")
            p = subprocess.Popen(cmd, stdout=f, stderr=f,
                                 creationflags=DETACH)
            slots.append({"net": net, "op": op, "tmo": tmo, "wd": wd,
                          "pcb": pcbw, "o0": o0, "pairs0": pairs0,
                          "proc": p, "logf": f})
    return slots


def harvest_result(slot):
    """等槽收工 -> 结构化评估 dict(实测为准)."""
    p = slot["proc"]
    try:
        p.wait(timeout=slot["tmo"])
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        return {"delta": -99, "new_pairs": [], "err": "timeout(%ds)" % slot["tmo"]}
    slot["logf"].close()
    lines = (slot["wd"] / "_slot.log").read_text(
        encoding="utf-8", errors="replace").splitlines()
    tail = [l for l in lines if l.strip().startswith("{")]
    try:
        hres = json.loads(tail[-1]) if tail else {}
    except ValueError:
        hres = {}
    err = str(hres.get("error") or hres.get("why") or "")
    if not tail:
        err = "CRASH:%s" % (lines[-1] if lines else "")[-100:]
    o1, pairs1 = opens_now(slot["pcb"])
    delta = (slot["o0"] - o1) if (slot["o0"] >= 0 and o1 >= 0) else -99
    new_pairs = sorted(set(pairs1) - set(slot["pairs0"]))
    if delta <= 0 and not err:
        err = "no_gain"
    return {"delta": delta, "new_pairs": new_pairs, "err": err,
            "out": (tail[-1] if tail else "")[:200]}


report = {"board": str(BOARD), "trajectory": [], "gaps": {}, "proven": [],
          "ts": round(time.time())}
stall = 0

for rnd in range(MAX_ROUNDS):
    o0, pairs0 = opens_now(pcb_main)
    if o0 < 0:
        print("[agent] 测量失败, 本轮跳过(绝不计清光)", flush=True)
        continue
    if o0 == 0:
        report["done"] = "cleared"
        break
    nets0 = {p.split("|")[0] for p in pairs0}

    # proven 快路径: 已证配方直接进本轮梯首(重放即闭合)
    prov = [x for x in report.get("proven", [])
            if x.get("net") in nets0 and x.get("net") not in report["gaps"]]
    planned = []
    for x in prov[:NETS_PER_ROUND]:
        planned.append((x["net"], ((x["op"], 900),) +
                        tuple(l for l in (LADDER_GND if x["net"] == "GND"
                                          else LADDER_SIG)
                              if l[0] != x["op"])))
    used = {n for n, _ in planned}
    cand = [n for n in FRAG_NETS
            if n in nets0 and n not in report["gaps"] and n not in used] \
        + [n for n in sorted(nets0)
           if n not in FRAG_NETS and n not in report["gaps"] and n not in used]
    for n in cand[:NETS_PER_ROUND - len(planned)]:
        planned.append((n, LADDER_GND if n == "GND" else LADDER_SIG))
    if not planned:
        report["done"] = "only_gaps_left"
        break

    # 改+验: K 槽并行
    slots = launch_slots(planned)
    print("[agent] r%d 并行 %d 槽: %s" % (rnd, len(slots),
          ", ".join("%s/%s" % (s["net"], s["op"]) for s in slots)),
          flush=True)
    results = []
    for s in slots:
        res = harvest_result(s)
        results.append((s, res))
        print("[agent] r%d %s/%s delta=%s err=%s"
              % (rnd, s["net"], s["op"], res["delta"],
                 (res["err"] or res.get("out", ""))[:80]), flush=True)

    # 落: 全场最优单发(副本互斥), 败者 proven/入账
    winners = [(s, r) for s, r in results
               if r["delta"] > 0 and not r["new_pairs"]]
    winners.sort(key=lambda sr: -sr[1]["delta"])
    verdict = "fail"
    if winners:
        s, r = winners[0]
        landed, lwhy = land(s["wd"], BOARD)
        if landed:
            verdict = "closed:%s(+%d)" % (s["op"], r["delta"])
            stall = 0
        else:
            verdict = "land_rejected:%s" % lwhy
            stall += 1
        for s2, r2 in winners[1:]:
            report["proven"].append({"net": s2["net"], "op": s2["op"],
                                     "delta": r2["delta"]})
    else:
        stall += 1

    # 记: 每候选一行(败者=学习数据) + 结构性失败 GAP(一击)
    for s, r in results:
        log({"ts": round(time.time()), "round": rnd, "net": s["net"],
             "op": s["op"], "delta": r["delta"],
             "new_pairs": r["new_pairs"], "err": r["err"][:200],
             "landed": bool(winners and s is winners[0][0])})
        if r["delta"] <= 0 and r["err"] and not r["err"].startswith("timeout"):
            net = s["net"]
            if net not in report["gaps"]:
                e = r["err"]
                if "stale-plan" in e or "via-vs-track" in e:
                    report["gaps"][net] = "blocked(需拆死残段泛化): %s" % e[-70:]
                elif "no path" in e.lower() or "no-path" in e:
                    report["gaps"][net] = (
                        "no_path(GND 平面需缝合)" if net == "GND"
                        else "no_path(需复合算子/板级)") + ": " + e[-60:]
                else:
                    report["gaps"][net] = e[-100:]
        elif r["err"].startswith("timeout"):
            # 超时=暂时性(预算问题), 不永久 GAP —— 靶2a 分段收割的料
            pass

    report["trajectory"].append(
        {"round": rnd, "slots": ["%s/%s:%s" % (s["net"], s["op"], r["delta"])
                                 for s, r in results],
         "verdict": verdict})
    report["opens_now"] = o0
    report["ts"] = round(time.time())
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                      encoding="utf-8")
    if stall >= 3:
        report["done"] = "stall"
        break

oF, pairsF = opens_now(pcb_main)
report["opens_end"] = oF
report["pairs_end"] = pairsF
report["ts"] = round(time.time())
if "done" not in report:
    report["done"] = "max_rounds"
report["only_hard_left"] = bool(
    oF == 0 or report["done"] in ("only_gaps_left",)
    or ({p.split("|")[0] for p in pairsF} if oF >= 0 else set())
    <= {"SDMMC1_CMD", "USART6_RX", "UART8_RX"})
REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                  encoding="utf-8")
print("[agent] 终态 opens=%d done=%s gaps=%s proven=%d"
      % (oF, report["done"], sorted(report["gaps"]),
         len(report.get("proven", []))), flush=True)
