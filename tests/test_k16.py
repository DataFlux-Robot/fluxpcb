# -*- coding: utf-8 -*-
"""K16 Z-round tests: constraints wiring (Z2), constructive grid (Z3),
multi-layer PathFinder (Z4), auditability (Z5), FR incremental (Z1)."""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
KGEN = REPO / "fluxpcb" / "kicadgen"


class TestConstraintsWiring:
    """Z2: knowhow_constraints.json is the single source — the K15 audit
    found zero references and three drifted value copies."""

    def test_loader_merges_json_over_defaults(self):
        sys.path.insert(0, str(KGEN))
        try:
            import route_constraints
        finally:
            sys.path.pop(0)
        c = route_constraints.load()
        assert c["negotiation"]["pfac_first_iter"] == 0.0
        assert c["negotiation"]["pfac_growth"] == 1.3
        # Z3 lesson: the constructive worst-case (0.30) is geometrically
        # infeasible on 0.8mm-pitch BGA channels (nopath 42/43 on fc);
        # margin stays permissive with veto+retry as the residual gate
        assert c["grid"]["track_margin_mm"] == 0.15
        assert c["stackup_6l"]["signal_layers"] == [
            "F.Cu", "In1.Cu", "In4.Cu", "B.Cu"]

    def test_json_file_is_valid_and_consistent(self):
        d = json.loads(
            (REPO / "fluxpcb" / "knowhow_constraints.json").read_text(
                encoding="utf-8"))
        n = d["negotiation"]
        assert 0.0 <= n["pfac_first_iter"] < n["pfac_initial"]
        assert 1.0 < n["pfac_growth"] <= 2.0
        assert n["max_iterations"] >= 10

    def test_routegrid_reads_constraints(self):
        src = (KGEN / "routegrid.py").read_text(encoding="utf-8")
        assert "route_constraints" in src and "CONS" in src
        assert "GRID = CONS[" in src

    def test_fluxrouter_reads_constraints(self):
        src = (KGEN / "fluxrouter.py").read_text(encoding="utf-8")
        assert "route_constraints" in src
        assert 'NEG["pfac_first_iter"]' in src
        assert 'NEG["pfac_growth"]' in src
        assert 'CONS["negotiation"]["max_iterations"]' in src

    def test_routing_default_iters_from_constraints(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        assert "knowhow_constraints.json" in src
        assert "max_iterations" in src


class TestVPRSchedule:
    def test_first_iter_pure_shortest_path(self):
        src = (KGEN / "fluxrouter.py").read_text(encoding="utf-8")
        assert 'PFAC = float(NEG["pfac_first_iter"])' in src
        assert 'PFAC = float(NEG["pfac_initial"])' in src

    def test_shuffle_per_iteration(self):
        src = (KGEN / "fluxrouter.py").read_text(encoding="utf-8")
        assert "random.shuffle(order)" in src


class TestConstructiveGrid:
    """Z3: grid-free => clear_exact passes, by construction."""

    def test_track_margin_from_constraints(self):
        src = (KGEN / "routegrid.py").read_text(encoding="utf-8")
        assert "w + TRACK_MARGIN" in src
        assert "TRACK_MARGIN = CONS[" in src


class TestMultiLayer:
    """Z4: joint 3D negotiation over all stackup signal layers."""

    def test_layer_enumeration(self):
        src = (KGEN / "routegrid.py").read_text(encoding="utf-8")
        assert "def signal_layer_ids" in src
        assert "layer_ids" in src

    def test_astar_layer_transitions(self):
        src = (KGEN / "fluxrouter.py").read_text(encoding="utf-8")
        assert "for lj in range(NL)" in src
        assert "h_hist = [" in src
        assert "LIDS = rg.signal_layer_ids" in src

    def test_through_via_clears_all_layers(self):
        src = (KGEN / "fluxrouter.py").read_text(encoding="utf-8")
        assert "viaany" in src
        src_rg = (KGEN / "routegrid.py").read_text(encoding="utf-8")
        assert "all(not self.via_block[k][i] for k in range(self.nl))" \
            in src_rg

    def test_preferred_direction_bias(self):
        src = (KGEN / "fluxrouter.py").read_text(encoding="utf-8")
        assert "DIR_MULT" in src and "preferred_direction" in src


class TestAuditability:
    """Z5: failure causes persisted per iteration + congestion heatmap."""

    def test_why_history_persisted(self):
        src = (KGEN / "fluxrouter.py").read_text(encoding="utf-8")
        assert "why_history" in src
        assert '"why_history": why_history' in src

    def test_congestion_svg(self):
        src = (KGEN / "fluxrouter.py").read_text(encoding="utf-8")
        assert "write_congestion_svg" in src
        assert "congestion.svg" in src


class TestFRIncremental:
    """Z1: converged nets marked (type fix) in DSN before Freerouting."""

    def test_dsn_mark_fixed(self):
        src = (REPO / "fluxpcb" / "routing.py").read_text(encoding="utf-8")
        assert "def dsn_mark_fixed" in src
        assert "(type fix)" in src
        assert "_compute_route_sets" in src

    def test_scr_eagle_layer_mapping(self):
        """fc 96-shorts root cause: FR writes Eagle copper ids
        (1=Top, 16=Bottom, 17+k = k-th inner). The old In{n-1}
        mapping resolved 17 to nonexistent In16.Cu."""
        src = (KGEN / "scr2kicad.py").read_text(encoding="utf-8")
        assert '"In%d.Cu" % (n - 16)' in src
        # unknown layers must skip wire placement, not guess
        assert "layer_known" in src

    def test_mark_only_target_nets(self):
        import tempfile
        sys.path.insert(0, str(REPO))
        try:
            from fluxpcb.routing import dsn_mark_fixed
        finally:
            sys.path.pop(0)
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.dsn"
            p.write_text(
                '(wiring\n'
                '    (wire (path top_copper 400  100 100  200 200)'
                '(net 3V3)(type route))\n'
                '    (wire (path top_copper 400  300 300  400 400)'
                '(net GND)(type route))\n'
                '    (via "Via[0-1]_500:250_um"  150 150 (net 3V3)'
                '(type route))\n'
                ')\n', encoding="utf-8")
            n = dsn_mark_fixed(p, {"3V3"})
            txt = p.read_text(encoding="utf-8")
            assert n == 2
            assert "(net 3V3)(type fix)" in txt
            assert "(net GND)(type route)" in txt   # untouched
