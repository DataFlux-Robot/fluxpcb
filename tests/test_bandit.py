# -*- coding: utf-8 -*-
"""A2 Thompson Sampling 修复臂调度器(PCBSchemaGen 配方)."""
import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fluxpcb.kernel import bandit as bd                # noqa: E402


class TestReward:
    """契约: 奖励只认效果不认意图"""

    def test_closed_scores_full(self):
        assert bd.outcome_reward({"delta_opens": 2}) == bd.R_CLOSED

    def test_rollback_scores_zero(self):
        assert bd.outcome_reward(
            {"delta_opens": 0, "strict_rollback": "x"}) == bd.R_REGRESS
        assert bd.outcome_reward(
            {"delta_opens": 0, "rolled_back": ["I3"]}) == bd.R_REGRESS

    def test_short_increase_is_regression(self):
        assert bd.outcome_reward(
            {"delta_opens": 1, "delta_shorts": 1}) == bd.R_REGRESS

    def test_structural_progress(self):
        assert bd.outcome_reward(
            {"delta_opens": 0, "effect": 3}) == bd.R_PROGRESS

    def test_neutral(self):
        assert bd.outcome_reward({"delta_opens": 0, "effect": 0}) \
            == bd.R_NEUTRAL


class TestBandit:
    def test_no_starvation(self):
        # FC_L8 饥饿回归锁: 静态权重下收割臂零预算; TS 探索保证
        # 任何臂在 N 次选择里都有非零出现率
        b = bd.ArmBandit(rng=random.Random(42))
        picks = []
        cands = [{"action": "fanout_repair", "class": "escape-crossing"},
                 {"action": "direct_link", "class": "escape-crossing"}]
        for _ in range(60):
            ranked = b.rank(cands)
            picks.append(ranked[0]["action"])
            # 更新: fanout 奖励高(结构进展), direct_link 中性
            act = ranked[0]["action"]
            b.update(act, "escape-crossing",
                     bd.R_PROGRESS if act == "fanout_repair"
                     else bd.R_NEUTRAL)
        assert "direct_link" in picks        # 永不饿死

    def test_learns_better_arm(self):
        b = bd.ArmBandit(rng=random.Random(7))
        for _ in range(40):
            b.update("good", "cls", 1.0)
            b.update("bad", "cls", 0.0)
        wins = sum(1 for _ in range(50)
                   if b.rank([{"action": "good", "class": "cls"},
                              {"action": "bad", "class": "cls"}]
                             )[0]["action"] == "good")
        assert wins >= 40                   # 胜者被压倒性选中

    def test_class_isolation(self):
        # DS-MoE 语义: 同动作不同类是不同臂(专家)
        assert (bd.ArmBandit.arm_key("fanout_repair", "escape-crossing")
                != bd.ArmBandit.arm_key("fanout_repair",
                                        "power-fragment"))

    def test_state_roundtrip(self, tmp_path):
        p = tmp_path / "bandit_state.json"
        b = bd.ArmBandit(state_path=p, rng=random.Random(1))
        b.update("direct_link", "cls", 1.0)
        b.save()
        b2 = bd.ArmBandit(state_path=p, rng=random.Random(2))
        assert b2.state["direct_link|cls"] == \
            b.state["direct_link|cls"]

    def test_journal_ingest(self, tmp_path):
        j = tmp_path / "decision_journal.json"
        j.write_text(json.dumps([
            {"decision": {"action": "direct_link",
                          "params": {"net": "N1"}},
             "outcome": {"delta_opens": 1}},
            {"decision": {"action": "fanout_repair",
                          "params": {"nets": ["N2"]}},
             "outcome": {"delta_opens": 0, "effect": 2}},
        ]), encoding="utf-8")
        b = bd.ArmBandit(rng=random.Random(3))
        n = b.ingest_journal(j, {"N1": "escape-crossing",
                                 "N2": "escape-crossing"})
        assert n == 2
        assert b.state["direct_link|escape-crossing"][0] >= 2.0  # 1+1
        assert b.state["fanout_repair|escape-crossing"][0] >= 1.7  # 1+0.7

    def test_same_action_params_counted_once(self, tmp_path):
        j = tmp_path / "decision_journal.json"
        j.write_text(json.dumps([
            {"decision": {"action": "direct_link",
                          "params": {"net": "N1"}},
             "outcome": {"delta_opens": 1}},
            {"decision": {"action": "direct_link",
                          "params": {"net": "N1"}},
             "outcome": {"delta_opens": 1}},
        ]), encoding="utf-8")
        b = bd.ArmBandit(rng=random.Random(4))
        assert b.ingest_journal(j) == 1     # 重复签名只计一次

class TestLayeredScore:
    '''A3 分层连续评分器(PCBSchemaGen 5 层+证据链)'''

    def _rep(self, *types):
        return {'violations': [
            {'type': t, 'description': 'x%d' % i,
             'items': [{'uuid': 'u%d' % i, 'description': 'n%d' % i}]}
            for i, t in enumerate(types)]}

    def test_clean_board_scores_one(self):
        from fluxpcb.kernel import score
        r = score.score_board(0, 10, {'violations': []})
        assert abs(r['score'] - 1.0) < 0.001
        assert r['evidence'] == []

    def test_layers_and_evidence(self):
        from fluxpcb.kernel import score
        rep = self._rep('clearance', 'track_width', 'shorting_items')
        r = score.score_board(5, 10, rep)
        assert r['layers']['connectivity'] == 0.5
        assert r['layers']['clearance'] < 1.0
        assert r['layers']['width_via'] < 1.0
        assert r['layers']['electrical'] < 1.0
        assert len(r['evidence']) == 3
        assert r['evidence'][0]['uuids'] == ['u0']  # 证据链

    def test_shorts_heavy_penalty(self):
        from fluxpcb.kernel import score
        r = score.score_board(0, 10, self._rep('shorting_items'))
        assert r['layers']['electrical'] == 0.8

    def test_progress_gate(self):
        from fluxpcb.kernel import score
        a = {'score': 0.5}
        b = {'score': 0.501}
        assert score.progress(a, b)          # 平台期
        assert not score.progress(a, {'score': 0.9})
        assert score.score_delta_reward(a, {'score': 0.6}) == 1.0
        assert score.score_delta_reward(a, {'score': 0.4}) == 0.0
        assert score.score_delta_reward(a, {'score': 0.5}) == 0.5

class TestSemanticView:
    '''A4 语义耦合表示(SchGen/PCB-QA/OmniRouting)'''

    def test_roles(self):
        from fluxpcb.kernel import semantic
        assert semantic.net_role('3V3', {'3V3': 'power'}) == 'power'
        assert semantic.net_role('CAN1_H', diff_pairs=
                                 [['CAN1_H', 'CAN1_L']]) == 'diff'
        assert semantic.net_role('OSC32_IN') == 'clock'
        assert semantic.net_role('SPI1_SCK', {'SPI1_SCK': 'critical'}) \
            == 'critical'
        assert semantic.net_role('UART4_TX') == 'signal'

    def test_view_pairs_and_summary(self):
        from fluxpcb.kernel import semantic
        lines = ['OSC32_IN|1.0|2.0|0|3.0|4.0|0',
                 'OSC32_IN|3.0|4.0|0|5.0|6.0|0',
                 '3V3|9.0|9.0|0|8.0|8.0|0']
        v = semantic.semantic_view(
            lines, {'power-fragment': ['3V3'],
                    'escape-crossing': ['OSC32_IN']},
            {'3V3': 'power'})
        assert v['nets']['OSC32_IN']['role'] == 'clock'
        assert v['nets']['OSC32_IN']['class'] == 'escape-crossing'
        assert len(v['nets']['OSC32_IN']['pairs']) == 2
        assert v['summary']['open_nets'] == 2
        assert v['summary']['open_pairs'] == 3
        assert v['summary']['by_class']['power-fragment'] == 1
        # JSON 可序列化(PCB-QA: 表示要喂 LLM)
        json.dumps(v)

class TestBuildPipeline:
    '''A1 建造流水线状态机接线'''

    def test_phases_and_gated_steps(self):
        src = (REPO / 'fluxpcb' / 'routing.py').read_text(
            encoding='utf-8')
        body = src.split('def _build_pipeline')[1].split(chr(10) + 'def ')[0]
        assert 'P1-逃逸重构' in body and 'P3-全局布线' in body
        assert 'P4-收割' in body
        assert body.count('_gated_step') >= 1   # 每步同一门禁
        assert 'fanout_repair' in body and 'power_stitch' in body
        assert 'rerun_freerouting' in body and 'batch_escape' in body
        assert 'score_board' in body            # A3 连续评分

    def test_auto_route_guard(self):
        src = (REPO / 'fluxpcb' / 'routing.py').read_text(
            encoding='utf-8')
        assert 'FLUXNT_PIPELINE' in src         # 消融开关
        assert '_build_pipeline(kicad_cli' in src

    def test_bandit_in_loop(self):
        src = (REPO / 'fluxpcb' / 'routing.py').read_text(
            encoding='utf-8')
        assert 'FLUXNT_BANDIT' in src           # 消融开关
        body = src.split('def _closed_loop')[1].split(chr(10) + 'def ')[0]
        assert '_bandit.rank' in body           # 抽样调度
        assert '_bandit.update' in body         # 战绩回流

class TestGrindState:
    '''阶段1 磨山状态机: 残差级跨轮记忆(RSI 磨山地基)'''

    def test_objective_improvement_is_success(self):
        from fluxpcb.kernel import grind
        g = grind.GrindState()
        # objective 改善(缺口 0.3->0.1)即使开路未闭也算成功
        r = g.record_step('N1', 'nudge', {'dx': 0},
                          0.3, 0.1, {'delta_opens': 0, 'effect': 1})
        assert r['improved'] and abs(r['d_obj'] - 0.2) < 1e-6

    def test_rollback_blacklists(self):
        from fluxpcb.kernel import grind
        g = grind.GrindState()
        g.record_step('N1', 'rip', {'net': 'X'}, 0.3, 0.3,
                      {'rolled_back': ['I3']})
        assert g.is_blacklisted('N1', 'rip', {'net': 'X'})

    def test_stall_detection(self):
        from fluxpcb.kernel import grind
        g = grind.GrindState()
        for i in range(3):
            g.record_step('N1', 'nudge', {'i': i}, 0.3, 0.3,
                          {'effect': 0})
        assert g.stalled('N1', k=3)

    def test_persistence_roundtrip(self, tmp_path):
        from fluxpcb.kernel import grind
        pth = tmp_path / 'grind_state.json'
        g = grind.GrindState(state_path=pth)
        g.set_class('N1', 'escape-crossing')
        g.record_step('N1', 'swap', {}, 2, 1, {'effect': 1})
        g.add_erosion('N1', 'widen', {'backup': 'x.json'})
        g2 = grind.GrindState(state_path=pth)
        r = g2.residual('N1')
        assert r['class'] == 'escape-crossing'
        assert len(r['erosion']) == 1 and r['erosion'][0]['token']
        assert g2.best_objective('N1') == 1

    def test_gap_objective(self):
        from fluxpcb.kernel.grind import gap_objective
        plan = {'paths': [], 'rejects': [
            {'blocked_by': {'short_by_mm': 0.3}},
            {'blocked_by': {'short_by_mm': 0.12}}]}
        assert gap_objective(plan) == 0.12   # min 近失 = 缺口
        assert gap_objective({'paths': [{'pts': []}]}) == 0.0
        assert gap_objective({}) == -1.0     # 未知不当 0

    def test_failed_branch_archive(self):
        from fluxpcb.kernel import grind
        g = grind.GrindState()
        g.record_step('N1', 'nudge', {}, 0.3, 0.35,
                      {'delta_opens': 0, 'effect': 0})
        assert len(g.data['failed_branches']) == 1  # RSI 负样本档案

class TestOperators:
    '''阶段2 算子库: 注册表+升级链+发射'''

    def test_emit_shapes(self):
        from fluxpcb.kernel import operators as ops
        a = ops.emit('swap_pair', 'N1')
        assert a['action'] == 'reassign_pins' and a['params']['mode'] \
            == 'swap_pair'
        b = ops.emit('nudge_blocker', 'N1')
        assert b['action'] == 'gap_nudge'
        c = ops.emit('rip_blocker', 'N1', {'blockers': ['B1', 'B2']})
        assert c['params']['nets'][:2] == ['N1', 'B1']
        d = ops.emit('stitch', 'GND')
        assert d['action'] == 'power_stitch'

    def test_upgrade_chain(self):
        from fluxpcb.kernel import operators as ops
        assert ops.next_after_fail('nudge_blocker') == 'rip_blocker'
        assert ops.next_after_fail('move_blocker') is None  # 到底
        # K33 修复(算子注册≠算子入梯): 计划驱动算子(harvest/zone_bridge)
        # 对 local-congestion/unclassified 可见 —— 漏挂=窗内纯空转
        c0 = ops.candidates_for('local-congestion', 0)
        c4 = ops.candidates_for('local-congestion', 4)
        assert c0[0] == 'harvest' and c0[1] == 'zone_bridge'
        # 连续停滞推进链仍在: 4 停滞时墙侵蚀链首档升到 widen
        assert c4[0] == 'harvest' and c4[2] == 'widen_rescue'

    def test_class_routing(self):
        from fluxpcb.kernel import operators as ops
        assert ops.candidates_for('power-fragment')[0] == 'harvest'
        esc = ops.candidates_for('escape-crossing', 0)
        assert 'swap_pair' in esc and 'nudge_blocker' in esc

    def test_unknown_op_loud(self):
        from fluxpcb.kernel import operators as ops
        import pytest
        with pytest.raises(ValueError):
            ops.emit('nonsense', 'N1')

class TestPredictor:
    '''阶段3 预测器 v1: bandit 冷启 x 缺口启发 x 停滞惩罚'''

    def test_gap_proximity_boosts(self):
        from fluxpcb.kernel import predict
        near = {'objective': {'trace': [[0, 0.02]]}, 'ops': []}
        far = {'objective': {'trace': [[0, 0.5]]}, 'ops': []}
        g_near = predict.predicted_gain('nudge_blocker', 'c', near)
        g_far = predict.predicted_gain('nudge_blocker', 'c', far)
        assert g_near > g_far   # 缺口小 = 接近闭合优先

    def test_stall_penalty(self):
        from fluxpcb.kernel import predict
        stalled = {'objective': {'trace': [[0, 0.1]]},
                   'ops': [{'op': 'nudge_blocker', 'improved': False},
                           {'op': 'nudge_blocker', 'improved': False}]}
        fresh = {'objective': {'trace': [[0, 0.1]]}, 'ops': []}
        assert predict.predicted_gain('nudge_blocker', 'c', stalled) \
            < predict.predicted_gain('nudge_blocker', 'c', fresh)

    def test_rank_orders_by_gain(self):
        from fluxpcb.kernel import predict
        residuals = {
            'A#0': {'net': 'A', 'class': 'local-congestion',
                    'objective': {'trace': [[0, 0.02]]}, 'ops': []},
            'B#0': {'net': 'B', 'class': 'local-congestion',
                    'objective': {'trace': [[0, 0.9]]}, 'ops': []},
        }
        out = predict.rank_microsteps(residuals, {})
        assert out[0]['net'] == 'A'   # 近闭合的排最前
        assert out[0]['gain'] >= out[-1]['gain']

class TestLedger:
    '''L2-① 验证台账: 残差类x算子矩阵(一表两用)'''

    def _grind(self):
        return {'residuals': {
            'GND#0': {'class': 'power-fragment', 'blacklist': [],
                      'ops': [
                {'op': 'stitch', 'class_at': 'power-fragment',
                 'improved': True, 'd_obj': 5.0, 'd_opens': 5,
                 'ts': 100},
                {'op': 'stitch', 'class_at': 'power-fragment',
                 'improved': False, 'd_obj': 0, 'd_opens': 0,
                 'ts': 200}]},
            'S1#0': {'class': 'escape-crossing', 'blacklist': [],
                     'ops': [
                {'op': 'swap_pair', 'class_at': 'escape-crossing',
                 'improved': True, 'd_obj': 3.0, 'd_opens': 3,
                 'ts': 150}]},
            'Z1#0': {'class': 'zone-fragment', 'blacklist': [],
                     'ops': [
                {'op': 'harvest', 'class_at': 'zone-fragment',
                 'improved': False, 'd_obj': 0, 'd_opens': 0,
                 'ts': 300},
                {'op': 'nudge_blocker',
                 'class_at': 'zone-fragment',
                 'improved': False, 'd_obj': 0, 'd_opens': 0,
                 'ts': 400}]},
        }}

    def test_matrix_aggregation(self):
        from fluxpcb.kernel import ledger
        m = ledger.build_matrix(self._grind())
        c = m['power-fragment']['stitch']
        assert c['trials'] == 2 and c['wins'] == 1
        assert c['sum_d_obj'] == 5.0 and c['sum_d_opens'] == 5
        assert m['escape-crossing']['swap_pair']['wins'] == 1

    def test_class_at_freezes_history(self):
        # 类变更不污染: class_at 优先于当前 class
        from fluxpcb.kernel import ledger
        g = self._grind()
        g['residuals']['GND#0']['class'] = 'unclassified'  # 改类
        m = ledger.build_matrix(g)
        assert m['power-fragment']['stitch']['trials'] == 2
        assert 'unclassified' not in m or \
            'stitch' not in m.get('unclassified', {})

    def test_reachability_gap_proof(self):
        # 零可达类的结构性证明 = new_op 选题依据
        from fluxpcb.kernel import ledger
        m = ledger.build_matrix(self._grind())
        gaps = ledger.reachability_gap(m, ['zone-fragment'])
        assert len(gaps) == 1 and gaps[0]['cls'] == 'zone-fragment'
        assert '2 次尝试 0 胜' in gaps[0]['proof']
        # 有胜的类不出证明
        assert ledger.reachability_gap(m, ['power-fragment']) == []

    def test_zero_reach_cells(self):
        from fluxpcb.kernel import ledger
        m = ledger.build_matrix(self._grind())
        z = ledger.zero_reach(m, ['stitch', 'swap_pair', 'zone_bridge'])
        zone_cell = [x for x in z if x['cls'] == 'zone-fragment'
                     and x['op'] == 'zone_bridge']
        assert zone_cell and zone_cell[0]['reason'] == '未尝试'

    def test_snapshot_roundtrip(self, tmp_path):
        from fluxpcb.kernel import ledger
        led = ledger.Ledger(tmp_path / 'ledger.json')
        m = ledger.build_matrix(self._grind())
        led.snapshot(m, fp='abc/1', note='round1')
        led2 = ledger.Ledger(tmp_path / 'ledger.json')
        assert led2.latest()['fp'] == 'abc/1'
        assert led2.latest()['matrix']['power-fragment']

    def test_record_step_fields(self, tmp_path):
        # A1: class_at/ts/effect 三字段
        from fluxpcb.kernel import grind
        g = grind.GrindState()
        g.set_class('N1', 'escape-crossing')
        g.record_step('N1', 'op1', {}, 1, 0,
                      {'delta_opens': 1, 'effect': 2},
                      cls='escape-crossing')
        o = g.residual('N1')['ops'][0]
        assert o['class_at'] == 'escape-crossing'
        assert o['ts'] > 0 and o['effect'] == 2

class TestImprove:
    '''L2-② 改进规划器: 矩阵驱动选题(不拍脑袋)'''

    def _matrix(self):
        return {
            'zone-fragment': {'harvest': {'trials': 3, 'wins': 0,
                'sum_d_obj': 0, 'sum_d_opens': 0, 'blacklisted': 0,
                'last_ts': 0}},
            'power-fragment': {'stitch': {'trials': 5, 'wins': 1,
                'sum_d_obj': 5, 'sum_d_opens': 5, 'blacklisted': 0,
                'last_ts': 0}},
        }

    def test_zero_reach_generates_new_op(self):
        from fluxpcb.kernel import improve, ledger
        gaps = ledger.reachability_gap(self._matrix(),
                                       ['zone-fragment'])
        cands = improve.propose(self._matrix(), gap_classes=gaps)
        newops = [c for c in cands if c['kind'] == 'new_op']
        assert len(newops) == 1
        assert newops[0]['id'] == 'newop-zone-fragment'
        assert 'zone_bridge' in newops[0]['sketch']['name']
        # 预注册判据必带(HarnessPro: 禁主观验收)
        assert 'pass' in newops[0]['prereg_criteria']
        assert 'ab_runs' in newops[0]['prereg_criteria']

    def test_low_winrate_generates_variant(self):
        from fluxpcb.kernel import improve
        m = {'zone-fragment': {
            'nudge_blocker': {'trials': 3, 'wins': 0,
                'sum_d_obj': 0, 'sum_d_opens': 0,
                'blacklisted': 0, 'last_ts': 0}}}
        cands = improve.propose(m)
        v = [c for c in cands if c['kind'] == 'op_variant']
        assert v and v[0]['op'] == 'nudge_blocker'
        assert len(v[0]['params_grid']) == 4   # DIST 扫描档

    def test_rank_puts_structural_first(self):
        from fluxpcb.kernel import improve
        m = self._matrix()
        cands = improve.propose(m)
        ranked = improve.rank_candidates(cands)
        assert ranked[0]['kind'] == 'new_op'   # 补短边优先

class TestExperimentalOps:
    '''D1 数据驱动实验算子(ops_registry)'''

    def test_registry_load_and_emit(self, tmp_path):
        from fluxpcb.kernel import operators as ops
        reg = tmp_path / 'ops_registry.json'
        reg.write_text(json.dumps({
            'nudge_b015': {'action': 'gap_nudge',
                'params_default': {'dist': 0.15},
                'classes': [], 'status': 'experimental'}}),
            encoding='utf-8')
        assert ops.load_registry(reg) == 1
        a = ops.emit('nudge_b015', 'N1')
        assert a['action'] == 'gap_nudge'
        assert a['params']['dist'] == 0.15
        assert a['params']['net'] == 'N1'
        assert 'experimental' in a['expected_effect']

    def test_unknown_still_loud(self, tmp_path):
        from fluxpcb.kernel import operators as ops
        ops.load_registry(tmp_path / 'none.json')
        import pytest
        with pytest.raises(ValueError):
            ops.emit('nope', 'N1')

class TestEvolve:
    '''L3 方法进化环(HarnessPro): variant/诊断/变异/Pareto 三档'''

    def test_variant_schema(self):
        from fluxpcb.kernel import evolve
        v = evolve.new_variant(name='v0')
        assert set(v) >= {'id', 'ops_params', 'policy',
                         'budget', 'objective'}
        assert v['policy']['stall_penalty'] == 0.4

    def test_diagnose_dual(self):
        # 双诊断: 失败模式 + 冗余成本 + 方差
        from fluxpcb.kernel import evolve
        rolls = [
            {'gain': 1.0, 'wall_s': 100, 'ops': [
                {'op': 'stitch', 'd_obj': 1.0}]},
            {'gain': 0.0, 'wall_s': 300, 'ops': [
                {'op': 'nudge', 'd_obj': 0, 'wall_s': 150},
                {'op': 'nudge', 'd_obj': 0, 'wall_s': 150}]},
        ]
        d = evolve.diagnose(rolls)
        assert d['mean_gain'] == 0.5
        assert any(r['op'] == 'nudge' for r in d['redundancy'])
        assert any(f['op'] == 'nudge'
                   for f in d['failure_modes'])
        assert d['variance'] > 0

    def test_mutate_is_diagnosis_driven_no_presets(self):
        from fluxpcb.kernel import evolve
        v = evolve.new_variant(name='p')
        d = {'redundancy': [{'op': 'widen_rescue', 'n': 2,
                            'wall': 500.0, 'gain': 0}],
             'failure_modes': [{'op': 'nudge_blocker',
                                'n': 3, 'gain': 0}]}
        subs = evolve.mutate(d, v)
        # 变异落点由诊断指认(冗余降权+失败扰动), 无预设类别
        kinds = [x['changes'] for x in subs]
        assert any('widen_rescue 降权' in k for k in kinds)
        assert any('nudge_blocker 参数扰动' in k for k in kinds)
        for x in subs:
            assert set(x) >= {'changes', 'quality_impact',
                              'cost_impact', 'risks'}

    def test_pareto_and_tiers(self):
        from fluxpcb.kernel import evolve
        mk = lambda i, g, w: {'variant': {'id': i},
                              'gain': g, 'wall': w}
        cands = [mk('a', 1.0, 100), mk('b', 2.0, 300),
                 mk('c', 0.5, 100),    # 被 a 支配
                 mk('d', 1.0, 400)]    # 被 a 支配
        front = evolve.pareto_filter(cands, {'gain': 0, 'wall': 1})
        ids = sorted(x['variant']['id'] for x in front)
        assert ids == ['a', 'b']
        tiers = evolve.select_tiers(front, {'gain': 0, 'wall': 1})
        # 结构契约: 三档皆出自前沿; lite 成本不高于 heavy(加权折中
        # 语义 — heavy=性能加权最优, 非极值)
        assert set(tiers) == {'heavy', 'medium', 'lite'}
        front_ids = {x['variant']['id'] for x in front}
        for t in tiers.values():
            assert t['variant_id'] in front_ids
        assert tiers['lite']['wall'] <= tiers['heavy']['wall']

    def test_pareto_floor(self):
        # 性能底线: 低于起点者直接出局
        from fluxpcb.kernel import evolve
        front = evolve.pareto_filter(
            [{'variant': {'id': 'x'}, 'gain': -1, 'wall': 10}],
            {'gain': 0, 'wall': 1})
        assert front == []

class TestHarness:
    '''HarnessPro 完整移植: 三角色+固定测试+编辑器'''

    def test_roles_fallback_when_offline(self):
        from fluxpcb.kernel import harness
        rolls = [{'gain': 0, 'wall_s': 10, 'ops': [
            {'op': 'x', 'd_obj': 0}]}]
        d = harness.analyze_trajectories(rolls)
        assert d['source'] == 'rule'      # 离线诚实标注
        props = harness.plan_modification(d, [], {'id': 'v'})
        assert all(x['source'] == 'rule' for x in props)

    def test_editor_schema_guards(self):
        from fluxpcb.kernel import harness
        v = {'id': 'v', 'ops_params': {}, 'policy': {},
             'budget': {}, 'objective': {}}
        v2 = harness.edit_variant(v, {'policy.stall_penalty': 0.5})
        assert v2['policy']['stall_penalty'] == 0.5
        v3 = harness.edit_variant(v, {'evil.field': 1})
        assert 'evil' not in v3      # schema 外拒绝

    def test_fixed_tests_frozen_and_mechanical(self):
        from fluxpcb.kernel import harness
        ts = harness.generate_fixed_tests('task', [])
        assert ts['frozen'] is True
        assert ts['source'] == 'rule'   # 离线内置, 不冒充
        names = [t['name'] for t in ts['tests']]
        assert 'contracts_preflight' in names   # 反例引理种子
        r = harness.run_fixed_tests(ts, {'rollbacks': 0,
                                        'gain': 1, 'wall': 50,
                                        'wall_base': 100,
                                        'floor': 0,
                                        'contracts_ok': True})
        assert r['passed'] is True
        r2 = harness.run_fixed_tests(ts, {'rollbacks': 1,
                                          'contracts_ok': True})
        assert r2['passed'] is False
        # 契约预检红灯 = 整体不过(反例引理是前置门)
        r3 = harness.run_fixed_tests(ts, {'rollbacks': 0,
                                          'gain': 1, 'wall': 50,
                                          'wall_base': 100,
                                          'floor': 0,
                                          'contracts_ok': False})
        assert r3['passed'] is False

    def test_prompts_contain_discipline(self):
        from fluxpcb.kernel import harness
        assert '反作弊' in harness._PLAN_SYS
        assert '证据驱动' in harness._PLAN_SYS
        assert '预设路线' in harness.EVIDENCE_DRIVEN

class TestAgent4SciProtocol:
    '''Agent4Sci 三阶段: DPP 切分/审计角色/三 skill'''

    def test_dpp_split_stable(self):
        from fluxpcb.kernel import ledger
        nets = ['GND', 'SDMMC1_CMD', 'UART8_RX', 'USART6_RX',
                'VDDA', '3V3', 'SPI1_SCK', 'I2C1_SCL',
                'OSC_IN', 'PWM_CH1']
        s1 = ledger.split_residuals(nets)
        s2 = ledger.split_residuals(nets)
        assert s1 == s2                 # 确定性(跨轮稳定)
        assert set(s1.values()) <= {'train', 'val', 'test'}
        assert any(v == 'train' for v in s1.values())

    def test_matrix_split_isolation(self):
        # test 残差战绩不得泄漏进选题(zero_reach/gap)
        from fluxpcb.kernel import ledger
        gd = {'residuals': {
            'A#0': {'net': 'A', 'class': 'c1', 'blacklist': [],
                    'ops': [{'op': 'x', 'class_at': 'c1',
                             'improved': False, 'd_obj': 0,
                             'd_opens': 0, 'ts': 1}]},
            'B#0': {'net': 'B', 'class': 'c1', 'blacklist': [],
                    'ops': [{'op': 'x', 'class_at': 'c1',
                             'improved': True, 'd_obj': 1,
                             'd_opens': 1, 'ts': 1}]},
        }}
        split = {'A': 'test', 'B': 'train'}
        m_train = ledger.build_matrix(gd, split, only='train')
        assert 'c1' in m_train
        assert m_train['c1']['x']['wins'] == 1   # 只剩 B
        m_test = ledger.build_matrix(gd, split, only='test')
        assert m_test['c1']['x']['wins'] == 0

    def test_audit_role(self, monkeypatch):
        from fluxpcb.kernel import harness
        gd = {'residuals': {'A#0': {'net': 'A', 'ops': [
            {'op': 'nudge', 'd_obj': 0, 'rolled_back': False,
             'summary': 's'}], 'blacklist': []}}}
        r = harness.audit_trajectory(gd, [])
        assert r['source'] == 'rule'      # 离线诚实
        assert 'checklist_answers' in r
        assert r['verdict'] in ('clean', 'suspect')

    def test_planner_skills_in_prompts(self):
        from fluxpcb.kernel import harness
        assert 'verdict' in harness._PLAN_SYS
        assert 'boldness' in harness._PLAN_SYS
        assert 'bold' in harness._PLAN_SYS    # 双方案强制
        assert 'creative' in harness._PLAN_SYS  # 防探索坍缩

    def test_research_context(self, tmp_path):
        from fluxpcb.kernel import harness
        kh = tmp_path / 'KNOWHOW.md'
        kh.write_text('## K1 x' + chr(10) + '泛化为: y',
                      encoding='utf-8')
        rc = harness.research_context(kh, {'failed_branches': [
            {'net': 'N', 'op': 'nudge'}]})
        assert rc['knowhow'] and rc['failed_branches']
        assert '证据' in rc['note']
