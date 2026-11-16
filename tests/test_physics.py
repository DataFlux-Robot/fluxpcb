"""IPC-2141 and FDM kernel numerical cross-checks."""
import math

import pytest

from fluxpcb.tools.ipc2141 import (ImpedanceTarget, edge_coupled_microstrip_zdiff,
                                  microstrip_z0, solve_for_width, stripline_z0)
from fluxpcb.tools.fdm import Grid2D, solve_irdrop, solve_thermal


class TestIPC2141:
    def test_microstrip_typical_value(self):
        # FR4 er=4.4, w=0.3mm, h=0.2mm -> ~50 ohm class (textbook case)
        z = microstrip_z0(0.35, 0.2, 4.4)
        assert 45 <= z <= 60, f"Z0={z}"

    def test_wider_trace_lower_impedance(self):
        assert microstrip_z0(0.5, 0.2, 4.4) < microstrip_z0(0.2, 0.2, 4.4)

    def test_stripline_lower_than_microstrip(self):
        assert stripline_z0(0.2, 0.4, 4.4) < microstrip_z0(0.2, 0.2, 4.4)

    def test_100ohm_diff_pair(self):
        # w=0.15 s=0.2 h=0.2 on FR4: single Z0~81 -> Zdiff~132 (narrow-gap pair)
        _, zdiff = edge_coupled_microstrip_zdiff(0.15, 0.2, 0.2, 4.4)
        assert 120 <= zdiff <= 145, f"Zdiff={zdiff}"
        # solving the 100-ohm diff target must converge within tolerance
        t = ImpedanceTarget(net_class="eth", style="microstrip", z_target=100.0,
                            tolerance_pct=10.0, differential=True)
        w, res = solve_for_width(t, h=0.2, er=4.4, s=0.3)
        assert res.ok and abs(res.computed_z - 100.0) < 10.0

    def test_solve_for_width_hits_target(self):
        t = ImpedanceTarget(net_class="clk", style="microstrip", z_target=50.0,
                            tolerance_pct=5.0, differential=False)
        w, res = solve_for_width(t, h=0.2, er=4.4)
        assert res.ok
        assert abs(res.computed_z - 50.0) < 2.5
        assert 0.05 < w < 2.0

    def test_reference_value_textbook_crosscheck(self):
        # textbook case: 50-ohm microstrip on FR4 er=4.2, h=0.2032mm needs
        # w~0.36-0.40mm (w/h~1.8-2.0); w=0.254mm (w/h=1.25) gives ~65 ohm
        z_narrow = microstrip_z0(0.254, 0.2032, 4.2, t=0.035)
        assert 60 <= z_narrow <= 70, f"Z0={z_narrow}"
        z_50 = microstrip_z0(0.38, 0.2032, 4.2, t=0.035)
        assert abs(z_50 - 50.0) / 50.0 < 0.07, f"Z0={z_50} vs 50"


class TestFDM:
    def test_irdrop_scales_with_current(self):
        g1 = Grid2D(65, 30, nx=60, ny=30,
                    mask=None, sources=[(30, 15, 1.0)])
        r1 = solve_irdrop(g1, v_in=3.3, sink_current_a=0.5, iterations=800)
        g2 = Grid2D(65, 30, nx=60, ny=30, mask=None, sources=[(30, 15, 1.0)])
        r2 = solve_irdrop(g2, v_in=3.3, sink_current_a=2.0, iterations=800)
        assert r1["max_drop_v"] >= 0 and r2["max_drop_v"] > r1["max_drop_v"]

    def test_thermal_hotspot_above_ambient(self):
        g = Grid2D(65, 30, nx=60, ny=30, mask=None, sources=[(30, 15, 1.0)])
        r = solve_thermal(g, power_w=4.0, iterations=1500)
        assert r["t_max_c"] > 25.0
        assert r["t_max_c"] < 200.0  # sanity ceiling for 4W on this board

    def test_thermal_converges_monotonic_power(self):
        g1 = Grid2D(65, 30, nx=50, ny=25, mask=None, sources=[(25, 12, 1.0)])
        r1 = solve_thermal(g1, power_w=2.0, iterations=1200)
        g2 = Grid2D(65, 30, nx=50, ny=25, mask=None, sources=[(25, 12, 1.0)])
        r2 = solve_thermal(g2, power_w=8.0, iterations=1200)
        assert r2["t_max_c"] > r1["t_max_c"]
