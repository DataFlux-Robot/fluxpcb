"""B1 rollout 子进程化 — 独立进程跑一次磨山窗(env 隔离, 可并发).

用法: z_rollout.py BASE_DIR VARIANT_JSON TAG OUT_JSON [SPLIT]
variant 的 policy/budget 经 env 注入本进程(不污染兄弟进程)。
"""
import sys, json, shutil, os, time
from pathlib import Path

REPO = Path('E:/DATA/vscode/ZCODE/flux-neurotransmitter')
sys.path.insert(0, str(REPO))
from fluxpcb.routing import find_kicad, _closed_loop
from fluxpcb.kernel import state as kstate

LAB = Path('E:/DATA/vscode/ZCODE/z_lab')
BASE = Path(sys.argv[1])
VARIANT = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))
TAG = sys.argv[3]
OUT = Path(sys.argv[4])
SPLIT = sys.argv[5] if len(sys.argv) > 5 else 'train'

dst = LAB / ('z_v/EVO_%s' % TAG.replace('/', '_'))
if dst.exists():
    # Windows 文件锁韧性(根因#5): 上窗 freerouting 残句柄占文件,
    # rmtree 抛 PermissionError 崩窗 —— 重试+末次忽略
    for _i in range(5):
        try:
            shutil.rmtree(dst)
            break
        except (PermissionError, OSError):
            time.sleep(3 * (_i + 1))
    else:
        shutil.rmtree(dst, ignore_errors=True)
dst.parent.mkdir(parents=True, exist_ok=True)
shutil.copytree(BASE, dst)
(dst / 'v_result.json').unlink(missing_ok=True)

# A1 DPP: 冻结切分注入
from fluxpcb.kernel import ledger as _led
_pairs_f = dst / 'pcb/board/ratsnest_pairs.txt'
_nets = sorted({ln.split('|')[0] for ln in
                _pairs_f.read_text(encoding='utf-8').splitlines()
                if ln.strip()}) if _pairs_f.exists() else []
(dst / 'pcb/board/residual_split.json').write_text(
    json.dumps(_led.split_residuals(_nets)), encoding='utf-8')

pcb = sorted((dst / 'pcb/board').glob('*.kicad_pcb'))[0]   # 多板参数化
cls_file = dst / 'pcb/board/net_classes.json'
nc = json.loads(cls_file.read_text(encoding='utf-8'))
os.environ.update({
    'FLUXNT_LOOP': '1', 'FLUXNT_LOOP_BUDGET_S': '1800',
    'FLUXNT_GRIND': '1',
    'FLUXNT_GRIND_STEPS': str(VARIANT['budget']['steps_per_window']),
    'FLUXNT_STALL_PENALTY': str(VARIANT['policy']['stall_penalty']),
    'FLUXNT_BLACKLIST_FAILS':
        str(VARIANT['policy']['blacklist_fails']),
    'FLUXNT_V_STRICT': '1',
    'FLUXNT_GRIND_SPLIT': SPLIT,
    # variant ops_params -> 环内 ctx(变体参数化算子面接线)
    'FLUXNT_OP_PARAMS': json.dumps(VARIANT.get('ops_params') or {}),
    # C2: objective.w_clearance -> 磨山目标间距权重通道
    'FLUXNT_W_CLEARANCE':
        str((VARIANT.get('objective') or {}).get('w_clearance', 0)),
    # 全局 plan 缓存(跨窗共享暖机, sha 键安全)
    'FLUXNT_PLAN_CACHE': str(LAB / 'plan_cache'),
})
kicad_cli, kipy = find_kicad()
q0 = kstate.query(kicad_cli, kipy, pcb, 'opens', consolidate=True)
t0 = time.time()
try:
    _closed_loop(kicad_cli, kipy, pcb, cls_file,
                 nc.get('classes', {}), nc.get('pairs', []),
                 [], log=lambda m: None)
except Exception as ex:
    pass    # ok-silent: 崩溃轨迹由 grind_state 保留(失效安全)
wall = round(time.time() - t0)
q1 = kstate.query(kicad_cli, kipy, pcb, 'opens', consolidate=False)
gs = dst / 'pcb/board/grind_state.json'
ops = []
if gs.exists():
    gd = json.loads(gs.read_text(encoding='utf-8'))
    ops = [o for v in gd.get('residuals', {}).values()
           for o in v.get('ops', [])]
for o in ops:
    o.setdefault('wall_s', wall / max(1, len(ops)))
wo = VARIANT['objective'].get('w_opens', 1.0)
res = {'variant': VARIANT['id'], 'tag': TAG,
       'gain': round(wo * (q0.value - q1.value), 3),
       'opens_before': q0.value, 'opens_after': q1.value,
       'wall_s': wall, 'ops': ops, 'split': SPLIT}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(res, ensure_ascii=False), encoding='utf-8')
print('ROLLOUT ' + json.dumps({'tag': TAG, 'gain': res['gain'],
      'wall': wall, 'ops': len(ops)}))
