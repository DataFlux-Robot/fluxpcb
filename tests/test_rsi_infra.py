# -*- coding: utf-8 -*-
"""RSI 训练基建契约(阶段0): plan 缓存精确键/奖励统一/转移日志重建面."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


class TestPlanCache:
    def test_roundtrip_exact_key(self):
        from fluxpcb import routing as R
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            plan = {"net": "X", "paths": [[1.0, 2.0, 0]],
                    "segs": ["huge"], "rects": ["huge"],
                    "islands": [], "rejects": []}
            R._plan_cache_put(td, "X", "abc123", plan)
            hit = R._plan_cache_get(td, "X", "abc123")
            assert hit and hit["paths"] == [[1.0, 2.0, 0]]
            assert "segs" not in hit      # 瘦身: 巨型几何不入缓存

    def test_sha_mismatch_misses(self):
        from fluxpcb import routing as R
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            R._plan_cache_put(td, "X", "sha1", {"paths": [1]})
            assert R._plan_cache_get(td, "X", "sha2") is None   # 精确键

    def test_empty_plan_not_cached(self):
        from fluxpcb import routing as R
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            R._plan_cache_put(td, "X", "s", {"paths": [], "islands": []})
            assert R._plan_cache_get(td, "X", "s") is None


class TestRewardUnified:
    def test_blend_and_clip(self):
        from fluxpcb.kernel import bandit as B
        out = {"delta_opens": 0, "effect": 0}
        # 无 norm: 纯离散档
        assert B.outcome_reward(out) == B.R_NEUTRAL
        # 正梯度上抬中性分
        r = B.outcome_reward(out, reward_norm=0.1)
        assert r > B.R_NEUTRAL
        # 回滚仍然压到底(离散档主导)
        r2 = B.outcome_reward({"rolled_back": True}, reward_norm=0.9)
        assert r2 < 0.1
        # 裁剪: norm 超界不越 [0,1]
        r3 = B.outcome_reward(out, reward_norm=99.0)
        assert r3 <= 1.0

    def test_closure_dominates(self):
        from fluxpcb.kernel import bandit as B
        r = B.outcome_reward({"delta_opens": 1}, reward_norm=0.0)
        assert r >= 0.7      # 闭合语义不被零梯度抹掉


class TestTransitionLog:
    def test_record_step_fields(self):
        from fluxpcb.kernel.grind import GrindState
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            g = GrindState(Path(td) / "gs.json")
            res = g.record_step("NET", "op1", {}, 10.0, 9.5,
                                {"delta_opens": 0, "effect": 0},
                                episode_id="board#1", obj_kind="gap",
                                terminal="")
            row = g.residual("NET")["ops"][-1]
            for k in ("obj_before", "obj_after", "reward_norm",
                      "obj_kind", "episode_id", "terminal", "wall_s"):
                assert k in row, k
            assert row["obj_before"] == 10.0 and row["obj_after"] == 9.5
            assert abs(row["reward_norm"] - 0.05) < 1e-6
            assert res["reward_norm"] == row["reward_norm"]
