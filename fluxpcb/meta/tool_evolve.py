"""Tool evolution — the TextGrad step over the ACTION side.

For a zero-reach error model, the LLM gets: the model's numerical
conditions and counterfactual, its failure certificates, the retrieved
knowledge passages, and the source of the closest tool — and returns one
of:

  new_tool       full source implementing the counterfactual,
  tool_variant   an edited copy of an existing tool,
  tool_repair    a fix aimed at a specific failure certificate,
  skill_update   a change to a skill's TEXT (never code).

Every proposal must compile (tool_gate.check_static) and must declare
which error-model field it addresses and in which direction; without
that, gradient_check cannot falsify it.  Offline, a rule fallback
proposes compositions of the existing tools.
"""
from __future__ import annotations

import codecs  # noqa: F401
import json
import re
from dataclasses import asdict, dataclass, field

from ..knowledge.cards import KnowledgeCard
from .error_model import ErrorModel
from .tool_gate import check_static, name_gate

SYSTEM = (
    'You improve a PCB routing system by writing TOOLS, never by writing '
    'board-specific fixes. A tool is a Python module exposing run(args). '
    'Allowed imports: math, json, itertools, dataclasses, pathlib, heapq, '
    'pcbnew, fluxpcb (kicadgen/tools/verifiers subpackages), routegrid, '
    'geomir, maze. No subprocess/os/sys/shutil/socket/urllib. No board '
    'names, no absolute coordinates. The tool must run on ANY board with '
    'the same failure. Answer with JSON only.'
)


@dataclass
class ToolProposal:
    kind: str                    # new_tool | tool_variant | tool_repair | skill_update
    name: str
    description: str
    source: str                  # code for new_tool/variant/repair; '' for skill
    based_on: str = ''           # closest existing tool
    skill_text: str = ''         # for skill_update
    input_schema: dict = field(default_factory=dict)
    reasoning: str = ''
    address: dict = field(default_factory=dict)   # {model_id, field, prediction}
    proposer: str = 'rules'
    model_id: str = ''

    def validate(self) -> list:
        errs = []
        if self.kind not in ('new_tool', 'tool_variant', 'tool_repair',
                             'skill_update'):
            errs.append(f'bad kind {self.kind!r}')
        if self.kind != 'skill_update':
            if not self.source.strip():
                errs.append('code missing')
            else:
                static = check_static(self.source, self.name)
                if not static['ok']:
                    errs += [f'static: {p}' for p in static['problems']]
        else:
            if not self.skill_text.strip():
                errs.append('skill text missing')
        if not self.address.get('model_id'):
            errs.append('no model_id addressed')
        if self.address.get('prediction') not in ('up', 'down'):
            errs.append('no prediction direction')
        if not isinstance(self.input_schema, dict):
            errs.append('input_schema must be a dict')
        return errs

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- context
def primitive_api() -> list:
    """The real public signatures of the primitives a tool may call.

    A proposer that only sees tool NAMES invents functions that do not
    exist (observed: `routegrid.build_obstacles`).  Introspected, so it
    can never drift from the code.
    """
    import inspect
    from ..kicadgen import geomir, maze, routegrid, whynot
    out = []
    for name, mod in (('geomir', geomir), ('routegrid', routegrid),
                      ('maze', maze), ('whynot', whynot)):
        for n, f in inspect.getmembers(mod, inspect.isfunction):
            if n.startswith('_') or f.__module__ != mod.__name__:
                continue
            try:
                sig = str(inspect.signature(f))
            except (TypeError, ValueError):
                sig = '(?)'
            doc = (inspect.getdoc(f) or '').split(chr(10))[0][:90]
            out.append(f'{name}.{n}{sig}  -- {doc}')
    return out


TOOL_CONTRACT = (
    'TOOL CONTRACT: define `run(args)`. args has: board (a PATH to a '
    '.kicad_pcb; call pcbnew.LoadBoard(args["board"]) to get a BOARD '
    'object), net (name), x, y (the failing anchor in mm), width, '
    'clearance, item, attempt, requirement, why_not (the failure '
    'certificate; its exact structure is given in args_example — iterate '
    'lists with for-loops, read dicts with .get(), never call .items() on '
    'a list and never unpack more values than a tuple holds), prior (what '
    'earlier tools reported). A REPAIR tool '
    'changes the board and calls pcbnew.SaveBoard(args["board"], board); '
    'an ANALYSIS tool returns a dict and changes nothing. Use ONLY the '
    'functions listed in primitive_api; they take mm floats and plain '
    'lists. segs are [(net,(x1,y1),(x2,y2),half_w)] per layer; rects are '
    '[(net,(cx,cy,hw,hh,ang[,round]))] per layer; geomir.extract_objects('
    'board, layer_ids) builds them; routegrid.signal_layer_ids(board) '
    'gives the layer ids. A new track: routegrid.add_track(board, x1, y1, '
    'x2, y2, layer_id, net_object, width); a via: routegrid.add_via(board, '
    'x, y, net_object). Verify every drawn segment with geomir.'
    'verify_polyline and every via with geomir.via_ok BEFORE saving.')

def example_args(cert) -> dict:
    """The EXACT args dict a tool receives for this failure.

    Models mis-read hand-written shape descriptions (observed: .items()
    on a list, unpacking a 3-tuple into 4 names).  A literal example
    generated from the real certificate leaves nothing to imagine.
    """
    cert = cert or {}
    item = dict(cert.get('item') or {})
    pos = list(item.pop('pos', None) or [None, None])
    attempt = dict(cert.get('attempt') or {})
    requirement = dict(cert.get('requirement') or {})
    args = {'board': '<path to a .kicad_pcb; pcbnew.LoadBoard(args["board"]) '
                    'returns the BOARD object>',
            'repeats': 1, 'x': pos[0], 'y': pos[1], 'pos': pos, 'item': item,
            'attempt': attempt, 'requirement': requirement,
            'why_not': cert.get('why_not') or {}, 'prior': []}
    for k in ('net', 'width_mm', 'clearance_mm'):
        for src in (item, attempt, requirement):
            if src.get(k) is not None:
                args[k] = src[k]
                break
    if 'width_mm' in args:
        args['width'] = args['width_mm']
    return args


def build_context(model: ErrorModel, certs: list, passages: list,
                  registry, nearest_source: str = '') -> dict:
    """What the proposer sees: numbers, evidence, knowledge, the tool API."""
    tools = [{'name': s.name, 'description': s.description,
              'schema': s.input_schema, 'cost': s.cost_band,
              'emits': list(s.emits)} for s in registry.list()]
    return {
        'tool_contract': TOOL_CONTRACT,
        'args_example': example_args(certs[0] if certs else None),
        'primitive_api': primitive_api(),
        'error_model': {
            'conditions': list(model.conditions),
            'counterfactual': model.counterfactual,
            'capability': model.capability,
            'support': model.support,
            'model_id': model.model_id(),
        },
        'failure_certificates': certs[:6],
        'knowledge': [{'source': p.get('path') or p.get('url'),
                       'quote': p.get('quote'),
                       'score': p.get('score')} for p in passages[:6]],
        'tools': tools,
        'nearest_tool_source': nearest_source,
        'rules': ('one proposal; kind new_tool/tool_variant/tool_repair/'
                  'skill_update; declare address.model_id and '
                  'address.prediction ("up" or "down"); code must import '
                  'only from the whitelist; a REPAIR tool must save the '
                  'board; never call a function that is not in '
                  'primitive_api'),
    }


def llm_propose(ctx: dict, allow_llm: bool = True,
                max_tokens: int = 4000) -> ToolProposal:
    if not allow_llm:
        raise RuntimeError('llm disabled')
    try:
        from ..llm import LLMAdapter
        adapter = LLMAdapter()
    except Exception:
        raise RuntimeError('llm adapter unavailable')
    if not adapter.online:
        raise RuntimeError('llm offline')
    prompt = ('Propose one tool change as JSON:\n'
              '{"kind": "...", "name": "...", "description": "...", '
              '"source": "...", "based_on": "...", "input_schema": {}, '
              '"reasoning": "...", "address": {"model_id": "...", '
              '"field": "...", "prediction": "up|down"}}\n'
              'Context:\n' + json.dumps(ctx, ensure_ascii=False,
                                        default=str)[:30000])
    out = adapter.complete(SYSTEM, prompt)
    if not out.get('ok'):
        raise RuntimeError(f'llm call failed: {out.get("error")}')
    text = out.get('text') or ''
    body = _parse_reply(text)
    if body is None:
        raise RuntimeError(f'llm reply is not JSON; head: {text[:200]!r}')
    body = _normalise(body)
    prediction = str((body.get('address') or {}).get('prediction') or '')
    if prediction not in ('up', 'down'):
        # models sometimes put the rationale in the direction field
        prediction = 'up'
    return ToolProposal(
        kind=body.get('kind', ''), name=body.get('name', ''),
        description=body.get('description', ''),
        source=body.get('source', ''), based_on=body.get('based_on', ''),
        skill_text=body.get('skill_text', ''),
        input_schema=body.get('input_schema') or {},
        reasoning=body.get('reasoning', ''),
        address=dict(body.get('address') or {}, prediction=prediction),
        proposer='llm', model_id=ctx['error_model']['model_id'])


def _parse_reply(text: str):
    """Tolerant JSON reader for model replies.

    Models escape wrongly inside embedded source strings (a Windows path,
    a regex).  Try strict JSON first, then repair the usual bad escapes,
    then salvage the typed fields one by one.
    """
    if '{' not in text or '}' not in text:
        return None
    frag = text[text.index('{'):text.rindex('}') + 1]
    try:
        return json.loads(frag)
    except ValueError:
        pass
    BAD_ESCAPE = chr(92) + chr(92) + '(?![' + chr(92) + chr(92) + '/' + chr(92) + '"bfnrtu])'
    repaired = re.sub(BAD_ESCAPE, chr(92) * 4, frag)
    try:
        return json.loads(repaired)
    except ValueError:
        pass
    body = {}

    def _string(key):
        # a JSON string value: starts at the opening quote, honours
        # backslash-escaped quotes, ends at the next bare quote
        pat = r'"' + key + r'"\s*:\s*"((?:[^"' + chr(92) + chr(92) + ']|' \
              + chr(92) + chr(92) + r'.)*)"'
        m = re.search(pat, frag, re.S)
        return m.group(1) if m else None

    def _load(raw):
        try:
            return json.loads('"' + raw + '"')
        except ValueError:
            # undo the three escapes a source string actually uses
            out = raw
            for esc, ch in ((chr(92) + 'n', chr(10)),
                            (chr(92) + '"', '"'),
                            (chr(92) + chr(92), chr(92))):
                out = out.replace(esc, ch)
            return out

    for key in ('kind', 'name', 'description', 'based_on', 'reasoning'):
        raw = _string(key)
        if raw is not None:
            body[key] = _load(raw)
    for key in ('source', 'code'):
        raw = _string(key)
        if raw is not None:
            body['source'] = _load(raw)
            break
    m = re.search(r'"address"\s*:\s*(\{.*?\})', frag, re.S)
    if m:
        try:
            body['address'] = json.loads(m.group(1))
        except ValueError:
            body['address'] = {}
    return body or None


def _normalise(body: dict) -> dict:
    """Accept the key-name variants models actually answer with."""
    if not body.get('source') and body.get('code'):
        body['source'] = body['code']
    if not body.get('source') and body.get('tool'):
        t = body['tool']
        if isinstance(t, dict) and t.get('source'):
            body['source'] = t['source']
            body.setdefault('name', t.get('name', ''))
            body.setdefault('input_schema', t.get('input_schema') or {})
    if not body.get('reasoning') and body.get('tools'):
        body['reasoning'] = json.dumps(body['tools'], default=str)[:1500]
    return body


def rule_propose(ctx: dict) -> ToolProposal:
    """Offline fallback: compose existing tools for the counterfactual."""
    cf = ctx['error_model']['counterfactual']
    model_id = ctx['error_model']['model_id']
    names = {t['name'] for t in ctx['tools']}
    address = {'model_id': model_id, 'field': 'counterfactual',
               'prediction': 'up'}
    if 'narrower track' in cf and 'geomir_plan' in names:
        return ToolProposal(
            kind='skill_update', name='narrow_then_verify', description=cf,
            source='', skill_text=(
                '# narrow-then-verify\n'
                'When the corridor is short by less than the width step, '
                'plan at width = w_max from the failure certificate and '
                're-verify; commit only the verified plan.'),
            reasoning='rule fallback: use the measured w_max', address=address,
            model_id=model_id)
    if 'sealed' in cf and 'maze_route' in names:
        return ToolProposal(
            kind='skill_update', name='maze_when_sealed', description=cf,
            source='', skill_text=(
                '# maze-when-sealed\n'
                'When every via slot near the anchor is illegal, run the '
                'grid maze on the layer the far anchor admits and attach '
                'the via at the track end.'),
            reasoning='rule fallback: grid search behind a sealed anchor',
            address=address, model_id=model_id)
    return ToolProposal(
        kind='skill_update', name='replan_with_certificate',
        description=cf, source='',
        skill_text=('# replan-with-certificate\n'
                    'Re-read the failure certificate before retrying; '
                    'retry only what the counterfactual says is missing.'),
        reasoning='rule fallback', address=address, model_id=model_id)


def propose(model: ErrorModel, certs: list, passages: list, registry,
            nearest_source: str = '', allow_llm: bool = True,
            repair: int = 1) -> ToolProposal:
    """One proposal, plus up to `repair` name-gate repair rounds.

    A proposal that fails the AST name gate gets the offending symbols
    and their closest valid names back for one repair round: the DGM-H
    "list what exists" fix, applied as feedback instead of as a document.
    """
    ctx = build_context(model, certs, passages, registry, nearest_source)
    last_error = ''
    rounds = []
    if allow_llm:
        try:
            proposal = llm_propose(ctx, allow_llm=True)
            rounds.append({'round': 1, 'name_ok': None})
            for attempt in range(repair + 1):
                errs = proposal.validate()
                gate = (name_gate(proposal.source)
                        if proposal.kind != 'skill_update'
                        else {'ok': True, 'problems': []})
                rounds[-1]['name_ok'] = bool(gate['ok'])
                if not errs and gate['ok']:
                    note = ' [name gate: ok at round %d]' % (attempt + 1)
                    proposal.reasoning = (proposal.reasoning or '') + note
                    proposal.rounds = rounds
                    return proposal
                if not gate['ok'] and attempt < repair:
                    ctx['name_gate'] = {
                        'invalid_symbols': gate['problems'],
                        'rule': ('these functions/attributes DO NOT EXIST; '
                                 'call only functions listed in '
                                 'primitive_api; rewrite the whole tool '
                                 'with valid names')}
                    proposal = llm_propose(ctx, allow_llm=True)
                    rounds.append({'round': attempt + 2, 'name_ok': None})
                    continue
                if errs:
                    last_error = 'invalid proposal: ' + '; '.join(errs)[:300]
                else:
                    last_error = ('name gate: '
                                  + json.dumps(gate['problems'])[:300])
                break
        except Exception as e:
            last_error = ('%s: %s' % (type(e).__name__, e))[:300]
    proposal = rule_propose(ctx)
    proposal.reasoning = ('[llm unavailable or unusable: %s] ' % last_error
                         + proposal.reasoning)
    proposal.rounds = rounds
    return proposal

# optional attribute: how many rounds the proposer needed
for _p in (ToolProposal,):
    if not hasattr(_p, 'rounds'):
        pass
