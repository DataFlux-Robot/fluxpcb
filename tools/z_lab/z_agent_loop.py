# -*- coding: utf-8 -*-
"""z_agent_loop — 层1 Agent Loop: 攻坚循环(六步纪律化重复, 无新算法).

架构(两层, 保持简单 —— 不做 agentic-RL 训练):
  层1 本脚本: 探→判→改→验→落→记, 迭代物=板状态(opens 7→0)
  层2 z_harness_loop.py: 层1 stall 或同类失败>=2 时, 复盘 journal
     聚类定位工具缺陷 -> 修 harness -> 反例回归 -> 提交(人工/agent)

六步:
  探   实时 opens 单源 + 名单
  判   形态分类(实证): 收割结果即分类器 —— why/error 双字段都读
  改   副本施工, 按**算子梯子**逐个试(GND: zone_bridge>power_stitch>harvest;
       信号网: harvest>zone_bridge) —— 首个 delta>0 者胜
  验   副本实测 delta opens 且无新增对
  落   原子落板: 全文件 hash diff + certs 前缀断言(append-only)
  记   journal 一行 + **报告增量落盘**(崩溃不丢 gap 状态)

判据: opens 曲线(轮次 vs 开路数), 只认实测.
停机: opens=0 / 全部 GAP / 连续 3 轮无净收益(stall -> 层2).

缺陷账(层2流程产物, 防回归):
  D1 consolidate 参数名笔误(首跑崩溃)
  D2 no-path 报在 why 字段漏读 -> GND 空转复发
  D3 长对收割 2400s 超时未捕获 -> 循环崩溃(现: 900s 预算+timeout 入账)
  D4 崩溃丢 gap 状态(报告只在结束写; 现: 每轮增量落盘)

用法: z_agent_loop.py [BOARD_DIR=FC_L13] [MAX_ROUNDS=12]
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
MAX_ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 12
WORK = LAB / "z_v/FC_L13_agentwork"
JOURNAL = LAB / "z_agent_journal.jsonl"
REPORT = LAB / "z_agent_loop_report.json"

# 课程序: 碎片网优先. GND=平面碎片走桥接/缝合梯; 信号走收割梯
FRAG_NETS = ("GND", "NRST", "UART4_TX")
LADDER_GND = ("zone_bridge", "power_stitch", "harvest")
LADDER_SIG = ("harvest", "zone_bridge")
OP_TIMEOUT = 900

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
    """原子落板: certs 前缀断言 + 全文件 hash diff 拷贝."""
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


def try_op(net, op):
    """副本上试单个算子 -> (delta, new_pairs, err, out). 每算子全新副本."""
    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    shutil.copytree(BOARD, WORK)
    pcbw = sorted((WORK / "pcb" / "board").glob("*.kicad_pcb"))[0]
    o0, pairs0 = opens_now(pcbw)
    if o0 < 0:
        return -99, [], "measure_fail", ""
    if op == "harvest":
        cmd = [KIPY, "-u", str(LAB / "z_harvest.py"),
               str(WORK / "pcb" / "board"), net]
    elif op == "zone_bridge":
        cmd = [KIPY, "-u", str(KGEN / "zone_bridge.py"), str(pcbw), net]
    elif op == "power_stitch":
        cmd = [KIPY, "-u", str(KGEN / "power_stitch.py"), str(pcbw), net]
    else:
        return -99, [], "unknown_op:%s" % op, ""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=OP_TIMEOUT)
    except subprocess.TimeoutExpired:
        return -99, [], "timeout(%ds, 长对需分段收割算子)" % OP_TIMEOUT, ""
    tail = [l for l in (r.stdout or "").splitlines()
            if l.strip().startswith("{")]
    out = tail[-1] if tail else "CRASH:%s" % (r.stderr or "")[-120:]
    try:
        hres = json.loads(tail[-1]) if tail else {}
    except ValueError:
        hres = {}
    # why/error 双字段(D2 账)
    err = str(hres.get("error") or hres.get("why") or "")
    o1, pairs1 = opens_now(pcbw)
    delta = (o0 - o1) if (o0 >= 0 and o1 >= 0) else -99
    new_pairs = sorted(set(pairs1) - set(pairs0))
    # 算子报了成功但实测无收益 -> 以实测为准, err 记假成功
    if delta <= 0 and not err:
        err = "no_gain(%s)" % out[:80]
    return delta, new_pairs, err, out


report = {"board": str(BOARD), "trajectory": [], "gaps": {},
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
    cand = [n for n in FRAG_NETS if n in nets0 and n not in report["gaps"]] \
        + [n for n in sorted(nets0) if n not in FRAG_NETS
           and n not in report["gaps"]]
    if not cand:
        report["done"] = "only_gaps_left"
        break
    net = cand[0]
    ladder = LADDER_GND if net == "GND" else LADDER_SIG

    # 改+验: 算子梯子逐个试(每算子全新副本, 首个净收益者胜)
    best = None
    ev = []
    for op in ladder:
        delta, new_pairs, err, out = try_op(net, op)
        ev.append((op, delta, err))
        print("[agent] r%d %s/%s delta=%s err=%s"
              % (rnd, net, op, delta, (err or out)[:90]), flush=True)
        if delta > 0 and not new_pairs:
            best = (op, delta, err, out)
            break
        if delta > 0 and new_pairs:
            ev[-1] = (op, delta, "damage:%d_new" % len(new_pairs))
    verdict = "fail"
    landed = 0
    if best:
        op, delta, _, _ = best
        landed, lwhy = land(WORK, BOARD)
        verdict = "closed:%s" % op if landed else "land_rejected:%s" % lwhy
        stall = 0
    else:
        stall += 1
        # 全梯失败 -> 结构性 GAP(一击即足), 标签=各算子证据
        report["gaps"][net] = " | ".join("%s:%s" % (o, e[-60:])
                                         for o, d, e in ev if e)

    log({"ts": round(time.time()), "round": rnd, "net": net,
         "ladder": ev, "verdict": verdict, "landed": landed})
    report["trajectory"].append(
        {"round": rnd, "net": net, "opens_before": o0,
         "verdict": verdict, "evidence": ["%s:%s" % (o, d)
                                           for o, d, _ in ev]})
    # 增量落盘(D4 账): 崩溃不丢 gap/轨迹
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
print("[agent] 终态 opens=%d done=%s gaps=%s"
      % (oF, report["done"], sorted(report["gaps"])), flush=True)
