"""L2-③ A/B 小窗验证器(RSI 环的验证闸门).

用法: z_ab.py BASE_DIR CANDIDATE_JSON [N_STEPS]
  BASE_DIR = 干净基线板目录(FC_L2 等)
  CANDIDATE_JSON = improve.propose 产出的候选(带 prereg_criteria)

双臂对照(同一基线的两份新鲜副本):
  基线臂: 现算子库自然选择, N 微步
  候选臂: FLUXNT_L2_OP 钉扎候选算子(+ops_registry 注入参数), N 微步
判据 = 候选.prereg_criteria(预注册, 固定评测器):
  pass = 候选臂 sum_d_obj > 基线臂 AND 两臂 0 回滚 AND 0 新增短路
输出: ab_<id>.json(判据对照+原始轨迹, 采纳闸门消费)。
"""
import sys, json, shutil, os, time
from pathlib import Path

REPO = Path('E:/DATA/vscode/ZCODE/flux-neurotransmitter')
sys.path.insert(0, str(REPO))
from fluxpcb.routing import find_kicad, _closed_loop
from fluxpcb.kernel import state as kstate
from fluxpcb.kernel import harness as kharness
from fluxpcb.kernel import evolve as kevolve

LAB = Path('E:/DATA/vscode/ZCODE/z_lab')

# 冻结套件(跨运行落盘, HarnessPro: tests stay frozen across epochs)
FIXED = kharness.generate_fixed_tests(
    'z_ab A/B 裁决', [], log=lambda m: None,
    frozen_path=LAB / 'fixed_tests.json')
BASE = Path(sys.argv[1]) if len(sys.argv) > 1 else LAB / 'z_v/FC_L2'
CAND = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8')) \
    if len(sys.argv) > 2 else {}
N = int(sys.argv[3]) if len(sys.argv) > 3 else 8
cid = CAND.get('id', 'anon')
# 板维度入 tag(多板场: 跨板同候选不撞名)
tag = 'AB_' + cid.replace('/', '_') + '__' + BASE.name

if (LAB / f'z_v/{tag}/verdict.json').exists():
    print('SKIP ' + tag + ' (done)')
    sys.exit(0)


def run_arm(arm: str, pin_op: str, params: dict,
            variant: dict = None) -> dict:
    dst = LAB / f'z_v/{tag}_{arm}'
    if dst.exists():
        for _i in range(5):     # Windows 文件锁韧性(根因#5)
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
    if params:
        (dst / 'ops_registry.json').write_text(json.dumps({
            pin_op: {"action": params.pop('_action', 'gap_nudge'),
                     "params_default": params,
                     "classes": [], "status": "experimental"}}),
            encoding='utf-8')
        # 注册表路径单源: 臂内子进程按此 env 找到注入(旧版写 dst
        # loader 读仓库根 = 静默 no-op)
        os.environ['FLUXNT_OPS_REGISTRY'] = \
            str(dst / 'ops_registry.json')
    pcb = sorted((dst / 'pcb/board').glob('*.kicad_pcb'))[0]  # 多板参数化
    cls_file = dst / 'pcb/board/net_classes.json'
    nc = json.loads(cls_file.read_text(encoding='utf-8'))
    kicad_cli, kipy = find_kicad()
    steps = []
    t0 = time.time()
    env = {'FLUXNT_LOOP': '1', 'FLUXNT_LOOP_BUDGET_S': '2400',
           'FLUXNT_GRIND': '1', 'FLUXNT_GRIND_STEPS': str(N),
           'FLUXNT_V_STRICT': '1',
           'FLUXNT_PLAN_CACHE': str(LAB / 'plan_cache')}
    # variant edits 通道(policy/budget/objective -> env, 同 z_rollout 契约)
    if variant:
        pol = variant.get('policy') or {}
        bud = variant.get('budget') or {}
        obj = variant.get('objective') or {}
        if 'stall_penalty' in pol:
            env['FLUXNT_STALL_PENALTY'] = str(pol['stall_penalty'])
        if 'blacklist_fails' in pol:
            env['FLUXNT_BLACKLIST_FAILS'] = str(pol['blacklist_fails'])
        if 'steps_per_window' in bud:
            env['FLUXNT_GRIND_STEPS'] = str(bud['steps_per_window'])
        if 'w_clearance' in obj:    # C2: 磨山目标的间距权重通道
            env['FLUXNT_W_CLEARANCE'] = str(obj['w_clearance'])
        if variant.get('ops_params'):   # 变体参数化算子面(与 z_rollout 同契约)
            env['FLUXNT_OP_PARAMS'] = json.dumps(variant['ops_params'])
    if pin_op:
        env['FLUXNT_L2_OP'] = pin_op
    os.environ.update(env)
    try:
        _closed_loop(kicad_cli, kipy, pcb, cls_file,
                     nc.get('classes', {}), nc.get('pairs', []),
                     steps, log=lambda m: None)
    except Exception as ex:
        steps.append('ARM CRASH %s' % str(ex)[:100])
    gs = dst / 'pcb/board/grind_state.json'
    ops = []
    if gs.exists():
        gd = json.loads(gs.read_text(encoding='utf-8'))
        # 只计本臂新增算子(ts 过滤) —— 历史污染教训: BASE 工作区自带
        # 56 条历史算子, 不过滤则 A/B 比的是同一份历史(9.0=9.0 空裁决)
        ops = [o for v in gd.get('residuals', {}).values()
               for o in v.get('ops', [])
               if (o.get('ts') or 0) >= t0 - 10]
    qo = kstate.query(kicad_cli, kipy, pcb, 'opens', consolidate=False)
    qs = kstate.query(kicad_cli, kipy, pcb, 'shorts', consolidate=False)
    return {'arm': arm, 'ops': len(ops),
            'sum_d_obj': round(sum(o.get('d_obj', 0) for o in ops), 3),
            'sum_d_opens': sum(o.get('d_opens', 0) for o in ops),
            'wins': sum(1 for o in ops if o.get('improved')),
            'rollbacks': sum(1 for o in ops if o.get('rolled_back')),
            'opens_after': qo.value, 'shorts_after': qs.value,
            'wall_s': round(time.time() - t0)}


print('A/B ' + tag + ' N=' + str(N))
base_res = run_arm('base', '', {})
from fluxpcb.kernel import operators as kops_reg
_KIND = CAND.get('kind') or ''
pin_op = CAND.get('op') or (CAND.get('sketch') or {}).get('name') \
    or cid
# 注册表键撞内置名 = emit 内置优先**静默遮蔽**(探索实证) —— 前缀脱撞
if pin_op in kops_reg.OPERATORS:
    pin_op = 'x_' + pin_op
params = ((CAND.get('params_grid') or [{}])[0]
          if CAND.get('params_grid') else {})
if _KIND == 'new_op':
    params = dict(CAND.get('sketch') or {}).get('params_default', {})
cand_params = dict(params) if params else {}


def _act_of(op_name: str) -> str:
    """op 名 -> ACTION_CONTRACT 动作名(修复 primitives[-1] 误当动作名).
    双表查询: 算子名走 OPERATORS 映射; 本就是动作名则直用。"""
    if op_name in kops_reg.OPERATORS:
        return kops_reg.OPERATORS[op_name].get('action', 'gap_nudge')
    from fluxpcb.kernel.decision import ACTION_CONTRACT
    return op_name if op_name in ACTION_CONTRACT else 'gap_nudge'


if _KIND == 'new_op' and CAND.get('sketch'):
    prims = CAND['sketch'].get('primitives') or ['gap_nudge']
    cand_params.setdefault('_action', _act_of(prims[-1])
                           if prims[-1] in kops_reg.OPERATORS
                           else prims[-1])
elif _KIND in ('op_variant', 'op_repair'):
    # 参数变体/修复案: 前缀脱撞后走注册表 params_default, 动作由内置映射
    cand_params.setdefault('_action', _act_of(CAND.get('op') or ''))
elif _KIND == 'op_combo':
    # crossover: 两段序列(执行器 combo, 门禁贯穿一段回滚即停)
    prims = (CAND.get('sketch') or {}).get('primitives') or []
    steps = []
    for _p in prims[:2]:
        steps.append({'action': _act_of(_p), 'params': {}})
    cand_params = {'_action': 'combo', 'steps': steps}
# edits 消费: LLM 提案的 variant 字段修改进候选臂
# (此前 z_ab 不吃 edits -> 变体参数面断头)
cand_variant = None
if CAND.get('edits'):
    cand_variant = kharness.edit_variant(
        kevolve.new_variant(name=cid), CAND['edits'])
cand_res = run_arm('cand', pin_op, cand_params,
                   variant=cand_variant)

passed = (cand_res['sum_d_obj'] > base_res['sum_d_obj']
          and cand_res['rollbacks'] == 0
          and cand_res['shorts_after'] <= base_res['shorts_after'])
fixed_v = kharness.run_fixed_tests(FIXED, {
    'rollbacks': cand_res['rollbacks'],
    'gain': cand_res['sum_d_obj'] - base_res['sum_d_obj'],
    'wall': cand_res['wall_s'], 'wall_base': base_res['wall_s'],
    'floor': 0,
    'contracts_ok': kharness.contracts_preflight(REPO)})
verdict = {'id': cid, 'ts': round(time.time()),
           'prereg': CAND.get('prereg_criteria', {}),
           'base': base_res, 'cand': cand_res,
           'PASS': bool(passed), 'n_steps': N,
           'fixed': fixed_v,
           'edits_applied': bool(cand_variant)}
out = LAB / f'z_v/{tag}'
out.mkdir(parents=True, exist_ok=True)
(out / 'verdict.json').write_text(
    json.dumps(verdict, ensure_ascii=False, indent=1), encoding='utf-8')
print('VERDICT ' + json.dumps({'id': cid, 'PASS': passed,
      'base_d': base_res['sum_d_obj'], 'cand_d': cand_res['sum_d_obj']}))
