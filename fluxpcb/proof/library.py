"""The module library: versioned entries, instantiation, instances, retrieval.

An entry is a theorem statement for a reusable block: roles (role -> refs),
the nets it expects, the lemmas its proof uses, and its proven instances.  An
instance records the board, its state digest, the proof digest and the derived
contract, so `check` can tell a fresh entry from a stale one without trusting
a number typed into a note.

`find` is a retrieval heuristic: it scores entries by the nets they expect,
the ref prefixes they contain and the pad counts of their instances, and
returns candidates.  A candidate is not a proof; the caller must instantiate
and prove it on the target board.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import gate, modules, state as S

SCHEMA = 'fluxpcb-module-2'
LEGACY = 'fluxpcb-module-1'


def _digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)
                          .encode('utf-8')).hexdigest()[:16]


def _expand(refs):
    """'D1-D4' -> D1..D4; plain refs pass through."""
    out = []
    for r in refs:
        r = str(r)
        if '-' in r:
            a, b = r.split('-', 1)
            pre = ''.join(ch for ch in a if not ch.isdigit())
            if pre == ''.join(ch for ch in b if not ch.isdigit()) and \
                    a[len(pre):].isdigit() and b[len(pre):].isdigit():
                for n in range(int(a[len(pre):]), int(b[len(pre):]) + 1):
                    out.append(f'{pre}{n}')
                continue
        out.append(r)
    return out


@dataclass
class Entry:
    module_id: str
    version: str
    function: str
    roles: dict
    expects: list
    lemma_refs: list
    params: dict
    deps: list
    instances: list
    path: Path
    raw: dict = field(default_factory=dict)

    def refs(self):
        return sorted({r for rs in self.roles.values() for r in _expand(rs)})

    def as_dict(self):
        return {'schema': SCHEMA, 'module_id': self.module_id,
                'version': self.version, 'function': self.function,
                'contract': {'roles': self.roles},
                'expects': self.expects, 'lemma_refs': self.lemma_refs,
                'params': self.params, 'deps': self.deps,
                'instances': self.instances}

    def instantiate(self, st, role_refs=None):
        """Build the concrete module contract on a board state."""
        refs = []
        if role_refs:
            for role, got in role_refs.items():
                vals = got if isinstance(got, (list, tuple)) else [got]
                refs.extend(_expand(vals))
        else:
            refs = self.refs()
        missing = [r for r in refs if r not in (st.get('footprints') or {})]
        if missing:
            raise ValueError(f'{self.module_id}: refs not on board: {missing}')
        c = modules.derive(st, self.module_id, refs)
        return c

    def prove(self, board, rules, role_refs=None):
        st = S.extract(board, rules)
        c = self.instantiate(st, role_refs)
        r = modules.prove(board, c, rules)
        return r, c

    def register(self, proof, contract, board, board_fp, rules=None,
                 date=None):
        inst = {'board': str(board), 'board_fp': board_fp,
                'rules': dict(rules or {}),
                'digest': proof['digest'], 'refs': contract.refs,
                'boundary': contract.boundary, 'internal': contract.internal,
                'proof': {'ok': proof['ok'], 'summary': proof['summary'],
                          'digest': _digest(proof.get('theorem') or {}),
                          'seconds': proof['seconds']},
                'date': date or time.strftime('%Y-%m-%d')}
        self.instances = [i for i in self.instances
                          if i.get('board_fp') != board_fp] + [inst]
        return inst


class Library:
    def __init__(self, root):
        self.root = Path(root)
        self.entries = {}
        if self.root.exists():
            for p in sorted(self.root.glob('*/entry.json')):
                e = self._load(p)
                self.entries[e.module_id] = e

    def _load(self, path):
        d = json.loads(path.read_text(encoding='utf-8'))
        schema = d.get('schema')
        if schema not in (SCHEMA, LEGACY):
            raise ValueError(f'{path}: unknown schema {schema}')
        contract = d.get('contract') or {}
        roles = contract.get('roles') or d.get('roles') or {}
        if not roles and d.get('refs'):
            roles = {'module': list(d['refs'])}
        return Entry(module_id=d.get('module_id') or d.get('name') or path.parent.name,
                     version=str(d.get('version', '1')),
                     function=d.get('function', ''),
                     roles={k: _expand(v) for k, v in roles.items()},
                     expects=list(d.get('expects') or
                                  d.get('boundary_roles') or []),
                     lemma_refs=list(d.get('lemma_refs') or []),
                     params=dict(d.get('params') or {}),
                     deps=list(d.get('deps') or []),
                     instances=list(d.get('instances') or
                                    d.get('proven_instances') or []),
                     path=path, raw=d)

    def get(self, module_id):
        return self.entries[module_id]

    def list(self):
        return sorted(self.entries)

    def graph(self):
        """deps DAG; returns (adjacency, cycles)."""
        adj = {k: [d for d in e.deps if d in self.entries]
               for k, e in self.entries.items()}
        seen, stack, cycles = set(), set(), []

        def visit(n, path):
            if n in stack:
                cycles.append(path + [n])
                return
            if n in seen:
                return
            stack.add(n)
            for d in adj.get(n, []):
                visit(d, path + [n])
            stack.discard(n)
            seen.add(n)

        for n in adj:
            visit(n, [])
        return adj, cycles

    def save(self, entry):
        p = entry.path
        p.write_text(json.dumps(entry.as_dict(), indent=1,
                                ensure_ascii=False), encoding='utf-8')

    def check(self, board=None, rules=None, verify=False):
        """Fresh/stale per instance: recompute the module state and compare."""
        out = []
        for mid, e in self.entries.items():
            for inst in e.instances:
                rec = {'module': mid, 'board': inst.get('board'),
                       'board_fp': inst.get('board_fp')}
                b = board or inst.get('board')
                if not b or not Path(b).exists():
                    rec['status'] = 'unchecked'
                    out.append(rec)
                    continue
                inst_rules = rules or inst.get('rules') or S.board_rules(b)[0]
                try:
                    st = S.extract(b, inst_rules)
                    c = modules.derive(st, mid, inst.get('refs') or e.refs())
                    mst = modules.module_state(st, c)
                    rec['status'] = ('fresh'
                                     if S.board_fp(mst) == inst.get('board_fp')
                                     and mst['digest'] == inst.get('digest')
                                     else 'stale')
                    if verify and rec['status'] == 'fresh':
                        r = modules.prove(b, c, inst_rules)
                        rec['reproved'] = r['ok']
                        rec['status'] = 'fresh' if r['ok'] else 'stale'
                except Exception as ex:  # noqa: BLE001
                    rec['status'] = 'error'
                    rec['error'] = str(ex)[:200]
                out.append(rec)
        return out

    def find(self, st, terms=()):
        """Rank entries against a board state.  Returns candidate dicts."""
        fp = st.get('footprints') or {}
        nets = set(st.get('netPads') or {})
        cands = []
        for mid, e in self.entries.items():
            score, why = 0.0, []
            for key in ('input_net', 'output_net', 'net'):
                n = e.params.get(key)
                if n and n in nets:
                    score += 2
                    why.append(f'net {n}')
            for role, rs in e.roles.items():
                have = [r for r in _expand(rs) if r in fp]
                if have:
                    score += len(have)
                    why.append(f'{role}: {len(have)} refs')
            for t in terms:
                if t.lower() in (e.function + ' ' + mid).lower():
                    score += 3
                    why.append(f'term {t}')
            for inst in e.instances:
                if inst.get('board_fp') and inst.get('board_fp') == S.board_fp(st):
                    score += 5
                    why.append('same board as a proven instance')
            if score:
                cands.append({'module': mid, 'score': round(score, 2),
                              'why': why, 'version': e.version})
        return sorted(cands, key=lambda x: -x['score'])

    def prove_instance(self, module_id, board, rules=None, role_refs=None,
                       register=True):
        e = self.entries[module_id]
        rules = rules or S.board_rules(board)[0]
        st = S.extract(board, rules)
        c = e.instantiate(st, role_refs)
        r = modules.prove(board, c, rules)
        if register and r['ok']:
            mst = modules.module_state(st, c)
            e.register(r, c, board, S.board_fp(mst), rules=rules)
            self.save(e)
        return r
