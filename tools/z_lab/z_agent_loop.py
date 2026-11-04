# -*- coding: utf-8 -*-
"""z_agent_loop — 层1 Agent Loop: 攻坚循环(六步纪律化重复, 无新算法).

架构(两层, 保持简单 —— 不做 agentic-RL 训练):
  层1 本脚本: 探→判→改→验→落→记, 迭代物=板状态(opens 7→0)
  层2 z_harness_loop.py: 层1 stall 或同类失败>=2 时, 复盘 journal
     聚类定位工具缺陷 -> 修 harness -> 反例回归 -> 提交(人工/agent)

六步:
  探   实时 opens 单源 + 名单
  判   形态分类(死铜/走廊开/多墙/封死) —— 收割尝试的实证结果即分类器:
       stale-plan rejected(via-vs-track)=堵点; no-path+多墙=多墙; 先验=走廊开
  改   副本施工: 走廊开->z_harvest; 其余形态登记 HARNESS_GAP(缺算子)
  验   副本实测 delta opens 且无新增对
  落   原子落板: 全文件 hash diff + certs 前缀断言(append-only)
  记   journal 一行(attempt+verdict) —— 只当运行日志/复盘素材, 不训 policy

判据: opens 曲线(轮次 vs 开路数), 只认实测.
停机: opens=0 / 全部 GAP / 连续 3 轮无净收益(stall -> 层2).

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
KIPY = "python"
sys.path.insert(0, str(REPO))
from fluxpcb.routing import find_kicad          # noqa: E402
from fluxpcb.kernel import state as kstate      # noqa: E402

BOARD = Path(sys.argv[1]) if len(sys.argv) > 1 else LAB / "z_v/FC_L13"
MAX_ROUNDS = int(sys.argv[2]) if len(sys.argv) > 2 else 12
WORK = LAB / "z_v/FC_L13_agentwork"
JOURNAL = LAB / "z_agent_journal.jsonl"
REPORT = LAB / "z_agent_loop_report.json"

# 课程序: 碎片对(便宜)先, 硬对后 —— 但 v0 只有收割线算子,
# 多墙/封死直接登记 GAP(算子缺口是层2的靶, 不硬试浪费算力)
FRAG_NETS = ("GND", "NRST", "UART4_TX")

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
    mc = mb / "certs.jsonl"
    wc = wb / "certs.jsonl"
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
    # 探+判: 碎片网优先; GAP 登记过的不重试
    cand = [n for n in FRAG_NETS if n in nets0 and n not in report["gaps"]] \
        + [n for n in sorted(nets0) if n not in FRAG_NETS
           and n not in report["gaps"]]
    if not cand:
        report["done"] = "only_gaps_left"
        break
    net = cand[0]

    # 改: 副本施工(一进程一变更纪律只约束主板; 副本可重来)
    if WORK.exists():
        shutil.rmtree(WORK, ignore_errors=True)
    shutil.copytree(BOARD, WORK)
    r = subprocess.run(
        [KIPY, "-u", str(LAB / "z_harvest.py"),
         str(WORK / "pcb" / "board"), net],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=2400)
    tail = [l for l in (r.stdout or "").splitlines()
            if l.strip().startswith("{")]
    out = tail[-1] if tail else "CRASH:%s" % (r.stderr or "")[-120:]
    err = ""
    try:
        hres = json.loads(tail[-1]) if tail else {}
    except ValueError:
        hres = {}
    if tail and not hres.get("error"):
        err = ""
    else:
        err = str(hres.get("error") or out)

    # 验: 副本实测
    o1, pairs1 = opens_now(WORK / "pcb" / "board" / pcb_main.name \
        if (WORK / "pcb" / "board" / pcb_main.name).exists() \
        else sorted((WORK / "pcb" / "board").glob("*.kicad_pcb"))[0])
    delta = (o0 - o1) if (o0 >= 0 and o1 >= 0) else -99
    new_pairs = sorted(set(pairs1) - set(pairs0)) if delta != -99 else []

    verdict = "fail"
    landed = 0
    if delta > 0 and not new_pairs:
        landed, why = land(WORK, BOARD)
        verdict = "closed" if landed else "land_rejected:%s" % why
    elif delta > 0 and new_pairs:
        verdict = "damage:%d_new" % len(new_pairs)   # 副本拦截, 不落板

    # 形态分类(实证): 堵点/多墙 -> GAP 登记(层2的靶, 一击即足 —— 同工具重试=空转)
    if verdict.startswith("fail") and err:
        if "stale-plan rejected" in err or "via-vs-track" in err:
            report["gaps"][net] = "blocked(需拆死残段泛化): %s" % err[-80:]
            stall += 1
        elif "no path" in err.lower() or "no-path" in err:
            report["gaps"][net] = "no_path(需复合算子/板级): %s" % err[-80:]
            stall += 1
        else:
            stall += 1      # 暂时性(崩溃/测量失败) 不永久 gap, 只计 stall
    elif verdict == "closed":
        stall = 0
    else:
        stall += 1

    print("[agent] r%d %s -> %s delta=%s new=%d | %s"
          % (rnd, net, verdict, delta, len(new_pairs), out[:110]),
          flush=True)
    log({"ts": round(time.time()), "round": rnd, "net": net,
         "op": "harvest", "delta": delta, "new_pairs": new_pairs,
         "verdict": verdict, "error": err[-300:],
         "harvest_out": out[:300]})
    report["trajectory"].append(
        {"round": rnd, "net": net, "opens_before": o0,
         "opens_after": o1, "verdict": verdict})
    if stall >= 3:
        report["done"] = "stall"
        break

oF, pairsF = opens_now(pcb_main)
report["opens_end"] = oF
report["pairs_end"] = pairsF
report["ts"] = round(time.time())
if "done" not in report:
    report["done"] = "max_rounds"
# 收工语义给看门狗: 清零/仅剩GAP/停机(stall=层2的事)
report["only_hard_left"] = bool(
    oF == 0 or report["done"] in ("only_gaps_left",)
    or ({p.split("|")[0] for p in pairsF} if oF >= 0 else set())
    <= {"SDMMC1_CMD", "USART6_RX", "UART8_RX"})
REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=1),
                  encoding="utf-8")
print("[agent] 终态 opens=%d done=%s gaps=%s"
      % (oF, report["done"], sorted(report["gaps"])), flush=True)
