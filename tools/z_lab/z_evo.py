"""L3 代际进化驱动器 v2(Agent4Sci x HarnessPro).

每代: 种群 variant 各 G rollout(子进程并发, env 隔离) -> 双诊断
-> LLM 规划器(三 skill) -> 编辑器(Git) -> 固定套件裁决 -> Pareto
三档 -> 种群更新(父代保留) -> evo_gen_<n>.json 代际账(含审计)。
用法: z_evo.py [GENS] [BASE_DIR]
"""
import sys, json, subprocess, os, time, re
from pathlib import Path

REPO = Path('E:/DATA/vscode/ZCODE/flux-neurotransmitter')
sys.path.insert(0, str(REPO))
from fluxpcb.kernel import evolve, harness

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
GENS = int(sys.argv[1]) if len(sys.argv) > 1 else 2
BASE = (Path(sys.argv[2]) if len(sys.argv) > 2
        else LAB / 'z_v/FC_L2')
G, N = 2, 4
POP_MAX = 3
CONC = 3            # B1 并发度


def run_batch(jobs):
    """B1 并发 rollout(子进程 env 隔离)。jobs = [(variant, tag)]"""
    results = []
    for k in range(0, len(jobs), CONC):
        results.extend(_run_chunk(jobs[k:k + CONC]))
    return results


def _run_chunk(jobs):
    procs = []
    for v, tag in jobs:
        vfile = LAB / ('z_v/_variant_%s.json' % tag.replace('/', '_'))
        vfile.parent.mkdir(parents=True, exist_ok=True)
        vfile.write_text(json.dumps(v, ensure_ascii=False),
                         encoding='utf-8')
        out = LAB / ('z_v/_rollout_%s.json' % tag.replace('/', '_'))
        pr = subprocess.Popen(
            [sys.executable, str(LAB / 'z_rollout.py'),
             str(BASE), str(vfile), tag, str(out), 'train'],
            cwd=str(REPO), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT)
        procs.append((pr, out, tag))
    results = []
    for pr, out, tag in procs:
        pr.wait(timeout=3600)
        try:
            r = json.loads(out.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            r = {'variant': '?', 'tag': tag, 'gain': 0, 'wall_s': 0,
                 'ops': []}
        results.append(r)
        print('  rollout %s: gain=%s wall=%ss ops=%d'
              % (tag, r.get('gain'), r.get('wall_s'),
                 len(r.get('ops', []))))
    return results


fixed_tests = harness.generate_fixed_tests(
    'harness 进化: fc 残差集磨山', [],
    frozen_path=LAB / 'fixed_tests.json')

# 反例引理预检(六条契约回归, 启动一次, 0.8s)
import subprocess as _sp                      # noqa: E402
_ct = _sp.run([sys.executable, '-m', 'pytest',
               str(REPO / 'tests' / 'test_contracts.py'), '-q'],
              capture_output=True, timeout=120)
_CONTRACTS_OK = (_ct.returncode == 0)
if not _CONTRACTS_OK:
    print(' 反例引理预检失败 — 契约回归红灯: %s'
          % (_ct.stdout or b'')[-200:])
baseline = {'gain': 0.0, 'wall': 1}
front_best = []
pop = [evolve.new_variant(name='v0-base'),
       evolve.new_variant(name='v1-relaxed'),
       evolve.new_variant(name='v2-strict')]
pop[1]['policy']['stall_penalty'] = 0.2
pop[2]['policy']['stall_penalty'] = 0.6

for gen in range(1, GENS + 1):
    print('=== generation %d ===' % gen)
    jobs = [(v, 'g%d_%s_r%d' % (gen, v['id'], g))
            for v in pop for g in range(G)]
    rolls_by_var = {}
    for r in run_batch(jobs):
        rolls_by_var.setdefault(r.get('variant'), []).append(r)
    results = []
    for v in pop:
        rolls = rolls_by_var.get(v['id'], [])
        diag = evolve.diagnose(rolls)
        results.append({'variant': v, 'rolls': rolls, 'diag': diag,
                        'gain': diag['mean_gain'],
                        'wall': diag['mean_cost']})
    best = max(results, key=lambda r: r['gain'])
    diffs = [{'better': r is not best and best['gain'] > r['gain'],
              'variant': best['variant'],
              'note': 'g%s>%s' % (best['gain'], r['gain'])}
             for r in results if r is not best]
    # A2 审计(全轨迹样本)
    audit = harness.audit_trajectory(
        {'residuals': {'_': {'ops': [
            o for r in results if r['rolls']
            for o in r['rolls'][0].get('ops', [])][:20]}}},
        [])
    print(' 审计:', audit.get('verdict'), audit.get('source'),
          (audit.get('checklist_answers') or {})
          .get('creative_or_tuning'))
    # LLM 规划器(三 skill) -> 子代
    offspring = []
    for r in results[:2]:
        for sub in harness.plan_modification(r['diag'], diffs,
                                             r['variant']):
            base_v = evolve.new_variant(
                base=r['variant'],
                name=re.sub(r'[^\w\-一-鿿]', '',
                            sub.get('changes', 'x')[:20])
                    .replace(' ', '-') or 'sub')
            sub['variant'] = harness.edit_variant(base_v,
                                                  sub.get('edits', {}))
            ws = LAB / ('z_v/WS_%s' % sub['variant']['id'])
            ws.mkdir(parents=True, exist_ok=True)
            (ws / 'variant.json').write_text(
                json.dumps(sub['variant'], ensure_ascii=False),
                encoding='utf-8')
            sub['commit'] = harness.commit_workspace(
                ws, sub.get('changes', '')[:80])
            offspring.append(sub)
    print(' 子代提案: %d (llm %d)'
          % (len(offspring),
             sum(1 for s in offspring if s.get('source') == 'llm')))
    if offspring:
        ojobs = [(s['variant'], 'g%d_%s' % (gen, s['variant']['id']))
                 for s in offspring[:3]]
        for r in run_batch(ojobs):
            for s in offspring:
                if s['variant']['id'] == r.get('variant'):
                    d = evolve.diagnose([r])
                    results.append({'variant': s['variant'],
                                    'rolls': [r], 'diag': d,
                                    'gain': d['mean_gain'],
                                    'wall': d['mean_cost'],
                                    'changes': s.get('changes', ''),
                                    'verdict': s.get('verdict', ''),
                                    'boldness': s.get('boldness', ''),
                                    'commit': s.get('commit', '')})
                    print('  子代 %s: gain=%s wall=%ss [%s|%s]'
                          % (s['variant']['id'], d['mean_gain'],
                             d['mean_cost'], s.get('verdict'),
                             s.get('boldness')))
    for r in results:
        r['fixed'] = harness.run_fixed_tests(fixed_tests, {
            'rollbacks': 0, 'gain': r['gain'], 'wall': r['wall'],
            'wall_base': baseline.get('wall', 10 ** 9),
            'floor': baseline.get('gain', 0),
            'contracts_ok': _CONTRACTS_OK})
    front = evolve.pareto_filter(
        [{'variant': r['variant'], 'gain': r['gain'],
          'wall': r['wall']} for r in results], baseline)
    tiers = evolve.select_tiers(front, baseline)
    front.sort(key=lambda c: -c['gain'])
    pop = [c['variant'] for c in front[:POP_MAX]] or \
        [evolve.new_variant(name='v-reset-%d' % gen)]
    front_best = front
    rep = {'gen': gen, 'ts': round(time.time()),
           'population': [v['id'] for v in pop],
           'audit': audit,
           'results': [{'id': r['variant']['id'],
                        'gain': r['gain'], 'wall': r['wall'],
                        'diag': r['diag'],
                        'fixed': r.get('fixed'),
                        'verdict': r.get('verdict', ''),
                        'boldness': r.get('boldness', ''),
                        'commit': r.get('commit', '')}
                       for r in results],
           'front': [{'id': c['variant']['id'], 'gain': c['gain'],
                      'wall': c['wall']} for c in front],
           'tiers': tiers}
    out = LAB / ('evo_gen_%d.json' % gen)
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                   encoding='utf-8')
    print(' generation %d: front=%s' % (gen,
          [(c['variant']['id'], c['gain'], c['wall'])
           for c in front]))
print('EVO DONE.')
