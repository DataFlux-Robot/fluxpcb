"""The stitch ladder must not go below the board's own via minimum.

On the H743 run the smallest rung (0.3 pad / 0.15 drill) produced four vias
and twelve DRC violations (via_diameter, drill_out_of_range, annular_width).
A via the fab rules reject is not a stitch.
"""
from __future__ import annotations

from fluxpcb.kicadgen import stitch_params as power_stitch


def test_smallest_rung_is_the_board_minimum():
    for (annulus, drill) in power_stitch.VIA_SIZES:
        assert annulus * 2 >= 0.4 - 1e-9, (annulus, drill)   # pad >= 0.4
        assert drill * 2 >= 0.2 - 1e-9, (annulus, drill)     # drill >= 0.2
        assert annulus - drill >= 0.1 - 1e-9, (annulus, drill)  # annular >= 0.1
