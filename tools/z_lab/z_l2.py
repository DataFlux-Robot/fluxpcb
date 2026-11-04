"""L2 自改进环(RSI 闭环验证模式)驱动器.

用法: z_l2.py [GRIND_STATE] [BASE_DIR]
每轮: ①诊断(台账矩阵) -> ②立题(候选+预注册判据) -> ③验证(A/B
小窗) -> ④采纳/拒绝 -> ⑤报告 l2_cycle_<n>.json。
元层闸门(RSI² 有限展开): 每轮最多验证 2 候选、最多采纳 1 条。
"""
import sys, json, subprocess, os, time
from pathlib import Path

REPO = Path('E:/DATA/vscode/ZCODE/flux-neurotransmitter')
sys.path.insert(0, str(REPO))
from fluxpcb.kernel import ledger, improve, harness, evolve


# LLM 配置注入(密钥在 z_lab/.env_secret, 不入仓库不进日志)
for _ln in open('E:/DATA/vscode/ZCODE/z_lab/.env_secret',
                encoding='utf-8'):
    if '=' in _ln:
        _k, _v = _ln.strip().split('=', 1)
        os.environ[_k] = _v
os.environ.setdefault('FLUXNT_LLM_PROVIDER', 'openai')
os.environ.setdefault('FLUXNT_LLM_BASEURL',
                      'https://tokenrhythm.studio/v1')
os.environ.setdefault('FLUXNT_LLM_MODEL', 'mimo-v2.6-pro')
os.environ.setdefault('FLUXNT_LLM_API_KEY_ENV', 'FLUXNT_LLM_API_KEY')
LAB = Path('E:/DATA/vscode/ZCODE/z_lab')
GRIND = (Path(sys.argv[1]) if len(sys.argv) > 1
         else LAB / 'z_v/FC_L13/pcb/board/grind_state.json')
BASE = (Path(sys.argv[2]) if len(sys.argv) > 2
        else LAB / 'z_v/FC_L2')

cycles = sorted(LAB.glob('l2_cycle_*.json'))
n = len(cycles) + 1

# ① 诊断
gd = ledger.load_grind(GRIND)
matrix = ledger.build_matrix(gd)
known_ops = ['harvest', 'stitch', 'nudge_blocker', 'rip_blocker',
             'widen_rescue', 'move_blocker', 'swap_pair',
             'swap_chain3', 'single_drift']
gaps = ledger.reachability_gap(matrix, list(matrix.keys()))
zcells = ledger.zero_reach(matrix, known_ops)
print('L2 cycle %d: 矩阵 %d 类, 类级零可达 %d, 零胜格 %d'
      % (n, len(matrix), len(gaps), len(zcells)))
for g in gaps:
    print('  零可达:', g['cls'], '--', g['proof'])

# ② 立题(候选+预注册判据)
# ② 立题: 疯狂科学家 LLM 优先(禁预设路线), 规则兜底
_diag = {'failure_modes': [{'op': g['cls'], 'n': g.get('trials', 0),
                            'gain': 0} for g in gaps],
         'redundancy': [], 'variance': 0,
         'failed_branches': (gd.get('failed_branches') or [])[-6:]}
# 研究语料显式注入(research_battery.json 死端接活 + KNOWHOW)
_research = harness.research_context(
    REPO / 'KNOWHOW.md', _diag,
    battery_path=LAB / 'research_battery.json')
_llm_props = harness.plan_modification(
    _diag, [], evolve.new_variant(name='current'),
    research=_research)
if _llm_props and _llm_props[0].get('source') == 'llm':
    cands = []
    for i, pr in enumerate(_llm_props[:3]):
        cands.append({'id': 'hyp-%d-%s' % (i, pr.get('verdict', '')),
                      'kind': 'new_op' if pr.get('boldness') == 'bold'
                      else 'op_variant',
                      'applies_to': gaps[0]['cls'] if gaps else '',
                      'changes': pr.get('changes', ''),
                      'hypothesis': pr.get('hypothesis', ''),
                      'hypothesis_class': pr.get('hypothesis_class', ''),
                      'verdict': pr.get('verdict', ''),
                      'boldness': pr.get('boldness', ''),
                      'skill_refs': pr.get('skill_refs', []),
                      'edits': pr.get('edits', {}),
                      'prereg_criteria': {'pass': 'A/B 双臂 objective'
                                          ' 对比+零回滚+0新短',
                                          'ab_runs': 2},
                      'source': 'llm'})
else:
    cands = improve.rank_candidates(improve.propose(matrix, gaps))
cands = cands[:3]          # 元层闸门: 每轮验证 3 个(多方案纪律)
print('立题:')
for c in cands:
    print('  [%s] %s -> %s' % (c['kind'], c['id'],
                               c['changes'][:70]))

# ③④ 验证+采纳(每轮最多采纳 1 条)
verdicts = []
adopted = 0
for c in cands:
    if c.get('kind') == 'new_op' and not c.get('edits'):
        # 无参数化原型的 new_op: PENDING_IMPL(诚实); 带 edits 的
        # bold 提案以参数化原型进 A/B — 不空转
        verdicts.append({'id': c['id'], 'PASS': False,
                         'status': 'PENDING_IMPL',
                         'note': '选题证明已立, 实现后下轮验证'})
        print('  验证 %s: PENDING_IMPL(选题已立, 待实现)' % c['id'])
        continue
    cj = LAB / ('cand_%s.json' % c['id'])
    cj.write_text(json.dumps(c, ensure_ascii=False), encoding='utf-8')
    try:
        r = subprocess.run([sys.executable, str(LAB / 'z_ab.py'),
                            str(BASE), str(cj), '8'],
                           capture_output=True, text=True,
                           encoding='utf-8', errors='replace',
                           timeout=10800, cwd=str(REPO))
    except subprocess.TimeoutExpired:
        # 超时诚实化: 记账继续, 不崩环(7200s 崩环事故的修复)
        verdicts.append({'id': c['id'], 'PASS': False,
                         'status': 'TIMEOUT',
                         'note': 'A/B 超时(争抢/预算), 记账待重跑'})
        print('  验证 %s: TIMEOUT(记账, 待重跑)' % c['id'])
        continue
    v = {}
    try:
        # tag 带板维度(z_ab 多板改造): AB_<cid>__<BASE.name>
        vdir = LAB / 'z_v' / ('AB_%s__%s' % (
            c['id'].replace('/', '_'), BASE.name))
        v = json.loads((vdir / 'verdict.json').read_text(
            encoding='utf-8'))
    except (ValueError, OSError):
        v = {'id': c['id'], 'PASS': False,
             'error': (r.stdout or '')[-200:]}
    verdicts.append(v)
    print('  验证 %s: %s (base d=%s cand d=%s)'
          % (c['id'], 'PASS' if v.get('PASS') else 'FAIL',
             (v.get('base') or {}).get('sum_d_obj'),
             (v.get('cand') or {}).get('sum_d_obj')))
    if v.get('PASS') and adopted < 1:
        # 采纳前置独立审计(四类作弊检查) —— 通过才落注册表
        _audit = harness.audit_trajectory(gd, _llm_props, log=print)
        v['audit'] = {k2: _audit.get(k2) for k2 in
                      ('verdict', 'findings', 'source')}
        if _audit.get('verdict') != 'clean':
            print('  审计 %s: %s — 采纳暂缓: %s'
                  % (c['id'], _audit.get('verdict'),
                     (_audit.get('findings') or ['?'])[0][:60]))
            continue
        adopted += 1
        reg = LAB / 'ops_registry.json'
        data = {}
        if reg.exists():
            try:
                data = json.loads(reg.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                data = {}
        key = (c.get('op')
               or (c.get('sketch') or {}).get('name')
               or c['id'])
        ent = data.get(key) or {}
        ent['status'] = 'promoted'
        ent.setdefault('evidence', []).append('ab:%s' % c['id'])
        data[key] = ent
        reg.write_text(json.dumps(data, indent=1), encoding='utf-8')
        # 采纳工作区 Git 提交(HarnessPro: 可追溯可回滚)
        ws = LAB / ('z_v/ADOPT_%s' % c['id'].replace('/', '_')
                    .replace(':', '_'))
        ws.mkdir(parents=True, exist_ok=True)
        (ws / 'candidate.json').write_text(
            json.dumps(c, ensure_ascii=False), encoding='utf-8')
        (ws / 'verdict.json').write_text(
            json.dumps(v, ensure_ascii=False), encoding='utf-8')
        (ws / 'audit.json').write_text(
            json.dumps(v.get('audit'), ensure_ascii=False),
            encoding='utf-8')
        _c = harness.commit_workspace(ws, 'adopt: %s' % c['id'])
        # skill 存活记账(阶段4验收: 引用+存活双指标)
        for _sk in (c.get('skill_refs') or []):
            harness.skill_ledger(skill=_sk, outcome='adopted')
        print('  采纳: %s (evidence %s, commit %s)'
              % (key, ent['evidence'], _c[:8] or 'n/a'))

# ⑤ 报告
rep = {'cycle': n, 'ts': round(time.time()),
       'matrix_classes': list(matrix.keys()),
       'gaps': gaps, 'zero_cells': zcells[:10],
       'candidates': [{k: c.get(k) for k in
                       ('id', 'kind', 'changes', 'prereg_criteria',
                        'hypothesis', 'hypothesis_class', 'verdict',
                        'boldness', 'skill_refs')}
                      for c in cands],
       'verdicts': [{k: v.get(k) for k in
                     ('id', 'PASS', 'status', 'base', 'cand')}
                    for v in verdicts],
       'adopted': adopted}
out = LAB / ('l2_cycle_%d.json' % n)
out.write_text(json.dumps(rep, ensure_ascii=False, indent=1),
               encoding='utf-8')
print('L2 cycle %d 报告 -> %s (采纳 %d)' % (n, out, adopted))
