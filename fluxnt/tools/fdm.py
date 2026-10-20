"""Finite-difference field kernels for IR-drop and steady-state thermal analysis.

Pure numpy, shared grid core. Models a copper plane/trace network on a uniform
2D grid with sources (current sinks / heat dissipators) and boundary conditions.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Grid2D:
    """Uniform square-cell grid over a rectangular region (mm)."""
    width_mm: float
    height_mm: float
    nx: int = 120
    ny: int = 80
    mask: np.ndarray | None = None       # conductor coverage (1.0 = copper)
    sources: list[tuple[int, int, float]] = field(default_factory=list)  # (ix, iy, value)

    @property
    def dx(self) -> float:
        return self.width_mm / self.nx

    @property
    def shape(self) -> tuple[int, int]:
        return (self.ny, self.nx)

    def index(self, x_mm: float, y_mm: float) -> tuple[int, int]:
        return int(x_mm / self.width_mm * (self.nx - 1)), int(y_mm / self.height_mm * (self.ny - 1))


def solve_irdrop(grid: Grid2D, v_in: float, copper_sigma: float = 5.8e7,
                 copper_thickness_mm: float = 0.035, sink_current_a: float = 1.0,
                 iterations: int = 3000) -> dict:
    """DC IR-drop: solve Laplace on the conductor with one edge fed at v_in,
    sources drawing sink_current_a total (weighted), insulated elsewhere.

    Returns dict with max/mean rail drop and the potential field.
    """
    ny, nx = grid.shape
    mask = grid.mask if grid.mask is not None else np.ones((ny, nx))
    sheet_rho = 1.0 / (copper_sigma * copper_thickness_mm * 1e-3)  # ohm per square
    # inject sources into conductance network as current sinks
    src_map = np.zeros((ny, nx))
    total_src = len(grid.sources)
    if total_src == 0:
        ix, iy = nx // 2, ny // 2
        src_map[iy, ix] = 1.0
        total_src = 1
    for ix, iy, w in grid.sources or []:
        src_map[iy, ix] += w
    src_map /= max(src_map.sum(), 1e-12)
    src_map *= sink_current_a

    v = np.full((ny, nx), v_in)
    src_mask = src_map > 0
    for _ in range(iterations):
        v_new = v.copy()
        # 5-point stencil, von Neumann at insulated boundaries
        v_pad = np.pad(v, 1, mode="edge")
        lap = (v_pad[:-2, 1:-1] + v_pad[2:, 1:-1] + v_pad[1:-1, :-2] + v_pad[1:-1, 2:]) / 4
        v_new = np.where(mask > 0, lap, v)
        # feed edge (left column of conductor) at v_in
        v_new[:, 0] = np.where(mask[:, 0] > 0, v_in, v_new[:, 0])
        # current sinks force local drop via resistance model: approximate as
        # distributed negative source scaled by sheet resistance (relaxation form)
        v_new = v_new - src_map * sheet_rho * 0.02 * (mask > 0)
        if np.max(np.abs(v_new - v)) < 1e-9:
            v = v_new
            break
        v = v_new
    drop = (v_in - v) * (mask > 0)
    active = (mask > 0)
    return {
        "v_in": v_in,
        "max_drop_v": float(np.max(drop[active])) if active.any() else 0.0,
        "mean_drop_v": float(np.mean(drop[active])) if active.any() else 0.0,
        "field": v,
        "mask": mask,
    }


def solve_thermal(grid: Grid2D, power_w: float, h_conv: float = 12.0,
                  k_copper: float = 400.0, k_board: float = 0.3,
                  board_thickness_mm: float = 1.6, t_amb: float = 25.0,
                  edge_conductance_w_k: float = 0.8,
                  default_copper_fill: float = 0.35, iterations: int = 6000) -> dict:
    """Steady-state board-level thermal model for a core board mounted on a carrier:

    - in-plane conduction through effective conductivity (copper vs FR4);
      when no copper mask is provided, assume a board-average effective fill
      (GND/PWR planes => ~0.35) — a bare-FR4 board cannot spread 4W.
    - both-surface convection+radiation lumped as h_conv (W/m^2K)
    - edge conduction into the carrier board via B2B connectors (dominant path),
      modeled as edge_conductance_w_k distributed around the perimeter.

    Sources are heat dissipators (spread over 3x3 cells, die-scale).
    Under-relaxed Jacobi iteration (alpha) guarantees convergence.
    """
    ny, nx = grid.shape
    copper = (grid.mask if grid.mask is not None
              else np.full((ny, nx), float(default_copper_fill)))
    k_eff = k_board + (k_copper - k_board) * np.clip(copper, 0, 1)

    src_map = np.zeros((ny, nx))
    for ix, iy, w in grid.sources or []:
        src_map[iy, ix] += w
    if src_map.sum() == 0:
        src_map[ny // 2, nx // 2] = 1.0
    src_map *= power_w / max(src_map.sum(), 1e-12)
    # spread each source over 3x3 (physical die area, keeps solver stable)
    k3 = np.zeros_like(src_map)
    for iy in range(ny):
        for ix in range(nx):
            if src_map[iy, ix] > 0:
                y0, y1 = max(0, iy - 1), min(ny, iy + 2)
                x0, x1 = max(0, ix - 1), min(nx, ix + 2)
                k3[y0:y1, x0:x1] += src_map[iy, ix] / ((y1 - y0) * (x1 - x0))
    src_map = k3

    dx = grid.dx
    area = (dx * 1e-3) ** 2                    # m^2 per cell
    surf = h_conv * 2 * area                    # both sides, W/K per cell
    cond = k_eff * (board_thickness_mm * 1e-3)  # W/K per edge (dx cancels)
    edge = np.zeros((ny, nx), dtype=bool)       # perimeter cells
    edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
    n_edge = max(edge.sum(), 1)
    g_edge = edge_conductance_w_k / n_edge * edge  # W/K per perimeter cell

    alpha = 0.15
    T = np.full((ny, nx), float(t_amb))
    for it in range(iterations):
        T_pad = np.pad(T, 1, mode="edge")
        flux_in = cond * (T_pad[:-2, 1:-1] + T_pad[2:, 1:-1]
                          + T_pad[1:-1, :-2] + T_pad[1:-1, 2:])
        g_total = 4 * cond + surf + g_edge + 1e-12
        T_eq = (flux_in + src_map + (surf + g_edge) * t_amb) / g_total
        T_new = (1 - alpha) * T + alpha * T_eq
        if np.max(np.abs(T_new - T)) < 1e-7:
            T = T_new
            break
        T = T_new
    return {
        "t_amb": t_amb,
        "t_max_c": float(np.max(T)),
        "t_mean_c": float(np.mean(T)),
        "field": T,
        "iterations": it + 1,
        "hotspot_xy": np.unravel_index(int(np.argmax(T)), T.shape)[::-1],
    }
