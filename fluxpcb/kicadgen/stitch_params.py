"""Via ladder for power stitching, importable without a board.

`power_stitch.py` is a script (it loads a board at import).  The ladder lives
here so the invariant can be tested: the smallest rung must satisfy the
board's own via minimum (0.4 pad / 0.2 drill / 0.1 annular).  The JLC process
floor (0.3/0.15) is smaller, but a via the board's rules reject is not a
stitch: on the H743 run the 0.3/0.15 rung produced four vias and twelve DRC
violations (via_diameter, drill_out_of_range, annular_width).
"""
from __future__ import annotations

# (annulus radius, drill radius) mm, largest first
VIA_SIZES = [(0.25, 0.125), (0.2, 0.1)]
