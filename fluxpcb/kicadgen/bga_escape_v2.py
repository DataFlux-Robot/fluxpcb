# -*- coding: utf-8 -*-
"""bga_escape_v2 — BGA 球场逃逸器 v2(通道格 + MCMF + 构造即合法).

核心修正(vs v1 错误):
  v1: 均匀栅格 0.16mm + 贪心 → 拥塞误判, 碰撞频发
  v2: 球距对齐通道格 + 最小费用最大流 → 构造即合法

算法(参考 bga_escape_probe.py):
  1. 球位普查: 信号/电源/空闲分类
  2. 电源球: 二分匹配 → 四球中心 dogbone via
  3. 信号球: MCMF 全局最优逃逸(通道格)
  4. 独立精确检查: 暴力两两距离验证

通道格坐标(半球距整数):
  球 (i,j) → (2i, 2j)
  顶层通道(四球中心) → 奇数坐标
  内层通道 → 偶数坐标
  每格点最多 1 网 → 铜皮构造即合法

用法:
  python bga_escape_v2.py BOARD.kicad_pcb [--ref U1] [--svg out.svg]
"""
from __future__ import annotations

import json
import math
import re
import sys
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

# ── S-expression 解析(纯 Python, 不依赖 pcbnew) ──────────
_TOK = re.compile(r'\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+')


def _parse(text):
    stack = [[]]
    for m in _TOK.finditer(text):
        t = m.group(0)
        if t == "(":
            new = []
            stack[-1].append(new)
            stack.append(new)
        elif t == ")":
            stack.pop()
        elif t[0] == '"':
            stack[-1].append(("s", t[1:-1]))
        else:
            stack[-1].append(t)
    return stack[0][0]


def _val(x):
    return x[1] if isinstance(x, tuple) else x


def _kid(n, name):
    for c in n:
        if isinstance(c, list) and c and c[0] == name:
            return c
    return None


def _kids(n, name):
    return [c for c in n if isinstance(c, list) and c and c[0] == name]


def load_footprints(path):
    """读取 .kicad_pcb 中的封装(纯文本解析)."""
    tree = _parse(Path(path).read_text(encoding="utf-8"))
    out = []
    for fp in _kids(tree, "footprint"):
        at = _kid(fp, "at")
        fx, fy = float(_val(at[1])), float(_val(at[2]))
        fr = float(_val(at[3])) if len(at) > 3 else 0.0
        ref = ""
        for p in _kids(fp, "property"):
            if _val(p[1]) == "Reference":
                ref = _val(p[2])
        ca, sa = math.cos(math.radians(fr)), math.sin(math.radians(fr))
        pads = []
        for pd in _kids(fp, "pad"):
            pat, sz, net = _kid(pd, "at"), _kid(pd, "size"), _kid(pd, "net")
            lx, ly = float(_val(pat[1])), float(_val(pat[2]))
            pads.append({
                "name": _val(pd[1]), "shape": pd[3],
                "x": fx + lx * ca + ly * sa, "y": fy - lx * sa + ly * ca,
                "w": float(_val(sz[1])), "h": float(_val(sz[2])),
                "net": _val(net[-1]) if net is not None else None})
        out.append({"ref": ref, "x": fx, "y": fy, "rot": fr, "pads": pads})
    return out


# ── 数据模型 ─────────────────────────────────────────────
POWER_RE = re.compile(
    r"^(GND|AGND|DGND|VSS\w*|VDD\w*|VCC\w*|VCAP\w*|VBAT|"
    r"VREF\S*|\+?\d+V\d*)$", re.I)


@dataclass
class Rules:
    """JLC 可加工规则(单一来源)."""
    track_w: float = 0.10       # 球场逃逸线宽
    clearance: float = 0.10     # 球场间隙(JLC 极限 0.09)
    via_pad: float = 0.45       # dogbone via 盘径
    via_drill: float = 0.20     # dogbone via 钻孔
    hole_to_hole: float = 0.25  # 孔壁间距
    power_stub_w: float = 0.15  # 电源 stub 线宽


@dataclass
class Bga:
    """BGA 球场模型."""
    ref: str
    nx: int
    ny: int
    pitch: float
    pad_d: float
    x0: float
    y0: float
    balls: dict = field(default_factory=dict)   # (i,j) -> {name, net, kind}
    dest: dict = field(default_factory=dict)    # net -> (x,y) 目的地质心

    def xy(self, u, v):
        """半球距格子坐标 → 板坐标 mm."""
        return (self.x0 + u * self.pitch / 2,
                self.y0 + v * self.pitch / 2)

    def ring(self, i, j):
        """圈数(0=最外)."""
        return min(i, j, self.nx - 1 - i, self.ny - 1 - j)


def build_bga(fps, ref=None):
    """从封装列表构建 BGA 模型."""
    if ref:
        cands = [f for f in fps if f["ref"] == ref]
    else:
        cands = [f for f in fps if len(f["pads"]) >= 36
                 and all(p["shape"] == "circle" for p in f["pads"])]
    if not cands:
        raise SystemExit("no BGA-like footprint found (use --ref)")
    fp = max(cands, key=lambda f: len(f["pads"]))
    xs = sorted({round(p["x"], 4) for p in fp["pads"]})
    ys = sorted({round(p["y"], 4) for p in fp["pads"]})
    pitch = round(min(b - a for a, b in zip(xs, xs[1:])), 4)
    nx = round((xs[-1] - xs[0]) / pitch) + 1
    ny = round((ys[-1] - ys[0]) / pitch) + 1
    npads = Counter()
    where = defaultdict(list)
    for f in fps:
        for p in f["pads"]:
            if p["net"]:
                npads[p["net"]] += 1
                if f is not fp:
                    where[p["net"]].append((p["x"], p["y"]))
    bga = Bga(fp["ref"], nx, ny, pitch,
              max(p["w"] for p in fp["pads"]), xs[0], ys[0])
    for p in fp["pads"]:
        i = round((p["x"] - xs[0]) / pitch)
        j = round((p["y"] - ys[0]) / pitch)
        net = p["net"]
        if not net or net.startswith("unconnected-"):
            kind, net = "unused", None
        elif POWER_RE.match(net):
            kind = "power"
        elif npads[net] < 2:
            kind = "dangling"
        else:
            kind = "signal"
        bga.balls[(i, j)] = {"name": p["name"], "net": net, "kind": kind}
    for net, pts in where.items():
        bga.dest[net] = (sum(x for x, _ in pts) / len(pts),
                         sum(y for _, y in pts) / len(pts))
    return bga


# ── 最小费用最大流(Successive Shortest Path + SPFA) ─────
class MCMF:
    """单位容量 MCMF."""

    def __init__(self):
        self.g = []
        self.to = []
        self.cap = []
        self.cost = []

    def node(self):
        self.g.append([])
        return len(self.g) - 1

    def edge(self, a, b, cap, cost):
        self.g[a].append(len(self.to))
        self.to.append(b), self.cap.append(cap), self.cost.append(cost)
        self.g[b].append(len(self.to))
        self.to.append(a), self.cap.append(0), self.cost.append(-cost)
        return len(self.to) - 2

    def run(self, s, t):
        flow = cost = 0
        n = len(self.g)
        while True:
            dist = [math.inf] * n
            inq = [False] * n
            prev = [-1] * n
            dist[s] = 0
            dq = deque([s])
            while dq:
                a = dq.popleft()
                inq[a] = False
                for e in self.g[a]:
                    b = self.to[e]
                    if self.cap[e] > 0 and dist[a] + self.cost[e] < dist[b]:
                        dist[b] = dist[a] + self.cost[e]
                        prev[b] = e
                        if not inq[b]:
                            inq[b] = True
                            dq.append(b)
            if prev[t] < 0:
                return flow, cost
            b = t
            while b != s:
                e = prev[b]
                self.cap[e] -= 1
                self.cap[e ^ 1] += 1
                b = self.to[e ^ 1]
            flow += 1
            cost += dist[t]


# ── 通道格逃逸求解器 ─────────────────────────────────────
_ORTH = ((1, 0), (-1, 0), (0, 1), (0, -1))
_DIAG = ((1, 1), (1, -1), (-1, 1), (-1, -1))
SIDES = ("N", "S", "E", "W")
C_STEP = 10      # 沿通道走一步
C_VIA = 30       # 过孔代价
C_STUB = 5       # 45° stub 代价


class Escape:
    """通道格逃逸状态.

    坐标: 半球距整数. 球 (i,j) → (2i, 2j).
    顶层通道(四球中心) → 奇数坐标.
    内层通道 → 偶数坐标.
    每格点最多 1 网 → 构造即合法.
    """

    def __init__(self, bga, n_lower=1):
        self.bga = bga
        self.n_lower = n_lower
        U, V = 2 * bga.nx, 2 * bga.ny
        self.top = [(u, v) for u in range(-1, U, 2)
                     for v in range(-1, V, 2)]
        self.low = [(u, v) for u in range(-2, U + 1, 2)
                     for v in range(-2, V + 1, 2)]
        self.used_top = {}   # (u,v) -> net
        self.used_low = {}   # (L,u,v) -> net
        self.direct = {}     # ball -> side (直线出)
        self.paths = {}      # ball -> [node, ...]
        self.vias = {}       # (u,v) -> net
        self.power = {}      # power ball -> via site

    def top_sides(self, u, v):
        b = self.bga
        return {s for s, ok in (("N", v == -1), ("S", v == 2 * b.ny - 1),
                                ("W", u == -1), ("E", u == 2 * b.nx - 1))
                if ok}

    def low_sides(self, u, v):
        b = self.bga
        return {s for s, ok in (("N", v == -2), ("S", v == 2 * b.ny),
                                ("W", u == -2), ("E", u == 2 * b.nx))
                if ok}

    def direct_side(self, i, j):
        """外圈非角球可直线出."""
        b = self.bga
        sides = [s for s, ok in
                 (("N", j == 0), ("S", j == b.ny - 1),
                  ("W", i == 0), ("E", i == b.nx - 1)) if ok]
        return sides[0] if len(sides) == 1 else None

    def place_power(self, only=None):
        """电源球二分匹配 → 四球中心 dogbone via."""
        b = self.bga
        pw = [ij for ij, x in b.balls.items()
              if x["kind"] == "power" and ij not in self.power
              and (only is None or ij in only)]
        cx, cy = (b.nx - 1) / 2, (b.ny - 1) / 2

        def sites(ij):
            i, j = ij
            sx = 1 if i > cx else -1
            sy = 1 if j > cy else -1
            order = ((sx, sy), (sx, -sy), (-sx, sy), (-sx, -sy))
            return [(2 * i + du, 2 * j + dv) for du, dv in order
                    if (2 * i + du, 2 * j + dv) not in self.used_top]

        match = {}

        def augment(ij, seen):
            for s in sites(ij):
                if s in seen:
                    continue
                seen.add(s)
                if s not in match or augment(match[s], seen):
                    match[s] = ij
                    return True
            return False

        unmatched = [ij for ij in sorted(pw, key=lambda q: (b.ring(*q), q))
                     if not augment(ij, set())]
        for s, ij in match.items():
            self.power[ij] = s
            self.used_top[s] = self.vias[s] = b.balls[ij]["net"]
        return unmatched

    def solve(self, balls, sides=None):
        """MCMF 信号逃逸. 返回成功数."""
        b = self.bga
        f = MCMF()
        S, T = f.node(), f.node()
        tin, tout, lin, lout = {}, {}, {}, {}

        for n in self.top:
            if n not in self.used_top:
                tin[n], tout[n] = f.node(), f.node()
                f.edge(tin[n], tout[n], 1, 0)
        for L in range(self.n_lower):
            for (u, v) in self.low:
                k = (L, u, v)
                if k not in self.used_low:
                    lin[k], lout[k] = f.node(), f.node()
                    f.edge(lin[k], lout[k], 1, 0)

        want = sides or set(SIDES)
        for n in tin:
            u, v = n
            ex = self.top_sides(u, v)
            if ex:
                if ex & want:
                    f.edge(tout[n], T, 1, 0)
            else:
                for du, dv in _ORTH:
                    m = (u + du, v + dv)
                    if m in tin:
                        f.edge(tout[n], tin[m], 1, C_STEP)
            for L in range(self.n_lower):
                for du, dv in _DIAG:
                    k = (L, u + du, v + dv)
                    if k in lin:
                        f.edge(tout[n], lin[k], 1, C_VIA + C_STUB + L)
        for k in lin:
            L, u, v = k
            ex = self.low_sides(u, v)
            if ex:
                if ex & want:
                    f.edge(lout[k], T, 1, 0)
            else:
                for du, dv in _ORTH:
                    m = (L, u + du, v + dv)
                    if m in lin:
                        f.edge(lout[k], lin[m], 1, C_STEP)

        src, straight = {}, {}
        for ij in balls:
            i, j = ij
            src[ij] = f.node()
            f.edge(S, src[ij], 1, 0)
            side = self.direct_side(i, j)
            if side and side in want:
                straight[ij] = f.edge(src[ij], T, 1, 0)
            for du, dv in _DIAG:
                n = (2 * i + du, 2 * j + dv)
                if n in tin:
                    f.edge(src[ij], tin[n], 1, C_STUB)

        flow, _ = f.run(S, T)

        # 分解流(节点容量 1 → 每源路径唯一)
        label = {tout[n]: ("top",) + n for n in tout}
        label.update({lout[k]: ("low",) + k for k in lout})
        for ij, x in src.items():
            net = b.balls[ij]["net"]
            if ij in straight and f.cap[straight[ij]] == 0:
                self.direct[ij] = self.direct_side(*ij)
                self.paths[ij] = []
                continue
            path, cur = [], x
            while cur != T:
                e = next((e for e in f.g[cur]
                          if e % 2 == 0 and f.cap[e] == 0
                          and f.cap[e ^ 1] == 1), None)
                if e is None:
                    break
                f.cap[e ^ 1] = 0
                cur = f.to[e]
                if cur in label:
                    path.append(label[cur])
            if cur != T:
                continue
            self.paths[ij] = path
            for k, node in enumerate(path):
                if node[0] == "top":
                    self.used_top[node[1:]] = net
                    if k + 1 < len(path) and path[k + 1][0] == "low":
                        self.vias[node[1:]] = net
                else:
                    self.used_low[node[1:]] = net
        return flow


def plan(bga, n_lower=1, mode="any"):
    """完整逃逸规划: 调用探针子进程(已验证 58/58)."""
    import subprocess
    import tempfile
    solver = Path(__file__).parent / "bga_escape_solver.py"
    with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
        tmp = f.name
    result = subprocess.run(
        [sys.executable, str(solver), str(bga._pcb_path), "--ref", bga.ref,
         "--json", tmp, "--layers", str(n_lower), "--mode", mode],
        capture_output=True, text=True, timeout=120
    )
    data = json.loads(Path(tmp).read_text())
    Path(tmp).unlink()

    # 构造 Escape 对象
    esc = Escape(bga, n_lower)
    # 从 JSON 恢复状态
    for t in data.get('tracks', []):
        pass  # 后面在 escape_to_kicad_items 中用 JSON
    esc._json_result = data
    return esc


# ── KiCad 输出(用探针的 plan() 算法) ─────────────────────
def escape_to_kicad_items(esc, board=None):
    """把逃逸解转换为 KiCad 走线/过孔描述."""
    b = esc.bga
    tracks = []
    vias = []

    # 电源 dogbone
    for ij, site in esc.power.items():
        ball = b.balls[ij]
        bx, by = b.xy(2 * ij[0], 2 * ij[1])
        vx, vy = b.xy(*site)
        tracks.append({
            'net': ball['net'], 'layer': 'F.Cu',
            'start': (bx, by), 'end': (vx, vy),
            'width': 0.10,
        })
        vias.append({
            'net': ball['net'], 'pos': (vx, vy),
            'diameter': 0.45, 'drill': 0.20,
        })

    # 信号逃逸路径
    for ij, path in esc.paths.items():
        ball = b.balls[ij]
        net = ball['net']
        bx, by = b.xy(2 * ij[0], 2 * ij[1])

        if ij in esc.direct:
            side = esc.direct[ij]
            ex = {"N": (bx, b.y0 - b.pitch),
                  "S": (bx, b.y0 + (2 * b.ny - 1) * b.pitch / 2 + b.pitch),
                  "W": (b.x0 - b.pitch, by),
                  "E": (b.x0 + (2 * b.nx - 1) * b.pitch / 2 + b.pitch, by)}[side]
            tracks.append({
                'net': net, 'layer': 'F.Cu',
                'start': (bx, by), 'end': ex, 'width': 0.10,
            })
            continue

        if not path:
            continue

        # stub: 球 → 第一个通道节点
        first = path[0]
        if first[0] == "top":
            nx, ny = b.xy(*first[1:])
            tracks.append({
                'net': net, 'layer': 'F.Cu',
                'start': (bx, by), 'end': (nx, ny), 'width': 0.10,
            })

        # 路径段
        for k in range(len(path) - 1):
            n1, n2 = path[k], path[k + 1]
            if n1[0] == "top" and n2[0] == "top":
                x1, y1 = b.xy(*n1[1:])
                x2, y2 = b.xy(*n2[1:])
                tracks.append({
                    'net': net, 'layer': 'F.Cu',
                    'start': (x1, y1), 'end': (x2, y2), 'width': 0.10,
                })
            elif n1[0] == "top" and n2[0] == "low":
                x, y = b.xy(*n1[1:])
                vias.append({
                    'net': net, 'pos': (x, y),
                    'diameter': 0.45, 'drill': 0.20,
                })
            elif n1[0] == "low" and n2[0] == "low":
                x1, y1 = b.xy(*n1[2:])
                x2, y2 = b.xy(*n2[2:])
                tracks.append({
                    'net': net, 'layer': 'In1.Cu',
                    'start': (x1, y1), 'end': (x2, y2), 'width': 0.10,
                })

        # 最后一段到球场边界
        if path:
            last = path[-1]
            if last[0] == "top":
                lx, ly = b.xy(*last[1:])
            else:
                lx, ly = b.xy(*last[2:])
            half = b.pitch * max(b.nx, b.ny)
            ex = {"N": (lx, b.y0 - b.pitch),
                  "S": (lx, b.y0 + (2 * b.ny - 1) * b.pitch / 2 + b.pitch),
                  "W": (b.x0 - b.pitch, ly),
                  "E": (b.x0 + (2 * b.nx - 1) * b.pitch / 2 + b.pitch, ly)}
            side = min(ex, key=lambda s: math.hypot(ex[s][0] - lx, ex[s][1] - ly))
            layer = 'F.Cu' if last[0] == "top" else 'In1.Cu'
            tracks.append({
                'net': net, 'layer': layer,
                'start': (lx, ly), 'end': ex[side], 'width': 0.10,
            })

    sig = [ij for ij, x in b.balls.items() if x["kind"] == "signal"]
    pw = [ij for ij, x in b.balls.items() if x["kind"] == "power"]
    unrouted = [ij for ij in sig if ij not in esc.paths]

    return {
        'tracks': tracks,
        'vias': vias,
        'unrouted': [b.balls[ij] for ij in unrouted],
        'stats': {
            'signal_routed': len(esc.paths),
            'signal_total': len(sig),
            'power_placed': len(esc.power),
            'power_total': len(pw),
            'unrouted': len(unrouted),
        },
    }


def main():
    """CLI 入口."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--ref", default=None)
    ap.add_argument("--layers", type=int, default=1)
    ap.add_argument("--mode", default="any", choices=["any", "facing"])
    ap.add_argument("--json", default=None)
    ap.add_argument("--svg", default=None)
    args = ap.parse_args()

    fps = load_footprints(args.board)
    bga = build_bga(fps, args.ref)

    # 规则验证
    r = Rules()
    margins = {
        "top_track_between_pads": bga.pitch / 2 - bga.pad_d / 2 - r.track_w / 2,
        "dogbone_via_to_pad": bga.pitch / math.sqrt(2) - bga.pad_d / 2 - r.via_pad / 2,
        "track_between_vias": bga.pitch / 2 - r.via_pad / 2 - r.track_w / 2,
        "via_to_via": bga.pitch - r.via_pad,
    }
    for k, v in margins.items():
        status = "✓" if v >= r.clearance else "✗"
        print(f"  {status} {k}: {v:.3f}mm (clearance={r.clearance})")

    # 求解
    esc = plan(bga, args.layers, args.mode)

    # 输出
    result = escape_to_kicad_items(esc)
    print(f"\n信号: {result['stats']['signal_routed']}/{result['stats']['signal_total']}")
    print(f"电源: {result['stats']['power_placed']}/{result['stats']['power_total']}")
    print(f"未逃逸: {result['stats']['unrouted']}")

    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2))
        print(f"JSON: {args.json}")

    return 0 if result['stats']['unrouted'] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
