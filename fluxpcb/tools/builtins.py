"""Built-in tools: the routing primitives, callable through one registry.

Inline tools are pcbnew-free geometry; subprocess tools run under
KICAD_PYTHON through drivers/ so the meta process never loads pcbnew.
"""
from __future__ import annotations

from .registry import ToolSpec, ToolRegistry

SCHEMA_INT = {'type': 'integer'}


def _seg(args):
    return (args['net'], (args['ax'], args['ay']),
            (args['bx'], args['by']), args.get('obs_hw', 0.1))


def geomir_plan(args):
    """Candidate verified polylines between two anchors (pure geometry)."""
    # routegrid bootstraps its directory onto sys.path on import; import it
    # through the package first so geomir's internal `from routegrid` works.
    from ..kicadgen import routegrid as _rg  # noqa: F401
    from ..kicadgen import geomir
    # plan_paths takes one obstacle list PER LAYER; the tool interface is
    # single-layer, so nest it.
    segs = [[tuple(s) for s in (args.get('segs') or [])]]
    rects = [[tuple(r) for r in (args.get('rects') or [])]]
    plans, rejects = geomir.plan_paths(
        (args['ax'], args['ay']), (args['bx'], args['by']),
        args['width'], args['nc'], segs, rects,
        bounds=tuple(args['bounds']) if args.get('bounds') else None,
        width_ladder=tuple(args.get('width_ladder') or (args['width'],)),
        rules=args.get('rules') or None,
        planes_by_layer=None)
    return {'plans': plans, 'rejects': rejects}


def fanout_calc(args):
    """IPC-7095 dogbone fanout geometry for a BGA (pure)."""
    from ..kicadgen.ipc7095_fanout import calc_bga_escape_routing
    return calc_bga_escape_routing(
        args['n_balls'], args['pitch'], args['via_drill'], args['via_pad'],
        args['ball_pad'], args['track_width'])


def bga_escape_plan(args):
    """Min-cost max-flow escape plan for one BGA (pure)."""
    from ..kicadgen.bga_escape_solver import run as _run
    return _run(**args)


def builtins() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ToolSpec(
        name='geomir_plan', category='route', source='builtin',
        fn='fluxpcb.tools.builtins.geomir_plan',
        description=('Verified polyline candidates between two anchors '
                     'against exact obstacle lists; returns plans and '
                     'the rejected candidates with their blockers'),
        input_schema={'type': 'object', 'required': ['ax', 'ay', 'bx', 'by',
                                                     'width', 'nc'],
                      'properties': {
                          'ax': {'type': 'number'}, 'ay': {'type': 'number'},
                          'bx': {'type': 'number'}, 'by': {'type': 'number'},
                          'width': {'type': 'number'}, 'nc': SCHEMA_INT,
                          'segs': {'type': 'array'},
                          'rects': {'type': 'array'},
                          'rules': {'type': 'object'},
                          'bounds': {'type': 'array'}}},
        emits=('plan',), cost_band='light'))
    reg.register(ToolSpec(
        name='fanout_calc', category='plan', source='builtin',
        fn='fluxpcb.tools.builtins.fanout_calc',
        description='IPC-7095 parametric dogbone fanout geometry',
        input_schema={'type': 'object',
                      'required': ['n_balls', 'pitch', 'via_drill',
                                   'via_pad', 'ball_pad', 'track_width'],
                      'properties': {k: {'type': 'number'} for k in
                                     ('n_balls', 'pitch', 'via_drill',
                                      'via_pad', 'ball_pad', 'track_width')}},
        emits=('plan',), cost_band='light'))
    reg.register(ToolSpec(
        name='bga_escape_plan', category='plan', source='builtin',
        fn='fluxpcb.tools.builtins.bga_escape_plan',
        description='Min-cost max-flow escape assignment for a BGA',
        input_schema={'type': 'object'},
        emits=('plan',), cost_band='medium'))
    # subprocess tools: drivers/ + KICAD_PYTHON
    for name, desc, emits, cost in (
            ('maze_route', 'Grid A* corridor between two anchors with '
             'exact re-verification', ('tracks', 'vias'), 'light'),
            ('explore_route', 'Visibility-graph A* route between anchors '
             'with exact-clearance edges', ('tracks', 'vias'), 'light'),
            ('eikonal_route', 'Eikonal wavefront route between anchors '
             '(CPU fallback without GPU)', ('tracks', 'vias'), 'medium'),
            ('ncer_board', 'Negotiated-congestion route-all for a board',
             ('tracks', 'vias'), 'heavy'),
            ('freerouting_board', 'Headless Freerouting on a rule-patched '
             'DSN (all router options exposed)', ('board',), 'heavy'),
            ('pathfinder_board', 'PathFinder negotiated-congestion '
             'route-all (fluxrouter)', ('tracks', 'vias'), 'heavy')):
        reg.register(ToolSpec(
            name=name, category='route', source='builtin',
            driver=f'{name}.py',
            description=desc, emits=emits, cost_band=cost,
            input_schema={'type': 'object'}))
    return reg


DEFAULT = builtins()
