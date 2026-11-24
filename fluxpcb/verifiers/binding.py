"""binding — tie a verdict to the exact bytes it measured.

Two false-pass vectors are closed here:

1. `rule_congruence` compares the project table to the DSN table.  If both
   come from the same wrong project, they agree.  `project_identity`
   compares the project bytes to the manifest's expected hash.
2. `drc_clean` trusts a report's error count.  A zero-error report from
   another board (or another state) passes.  `bind_report` writes a sidecar
   carrying the board hash, and `check_binding` refuses a report whose
   sidecar does not match the current board.
"""
from __future__ import annotations

import hashlib
import json
import os
import time


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def bind_report(board_path: str, report_path: str,
                sidecar_path: str = None) -> dict:
    """Record which board a DRC report was produced from."""
    sidecar_path = sidecar_path or (report_path + '.binding.json')
    rec = {'board_sha': sha256_file(board_path),
           'report_sha': sha256_file(report_path),
           'report': os.path.basename(report_path),
           'at': time.time()}
    with open(sidecar_path, 'w', encoding='utf-8') as f:
        json.dump(rec, f, indent=1)
    return rec


def check_binding(board_path: str, sidecar_path: str) -> dict:
    """The context for the `report_binding` gate."""
    if not os.path.exists(sidecar_path):
        return {'report_exists': False, 'note': 'no binding sidecar'}
    try:
        with open(sidecar_path, encoding='utf-8') as f:
            rec = json.load(f)
    except Exception as e:
        return {'report_exists': False, 'note': f'unreadable binding: {e}'}
    return {'report_exists': True,
            'board_sha': sha256_file(board_path),
            'binding_board_sha': rec.get('board_sha', '')}


def project_identity(pro_path: str, expected_sha: str = None) -> dict:
    """The context for the `project_identity` gate."""
    if not os.path.exists(pro_path):
        return {'expected_project_sha': expected_sha or '',
                'actual_project_sha': ''}
    return {'expected_project_sha': expected_sha or '',
            'actual_project_sha': sha256_file(pro_path)}


def rule_scope_ctx(register=None, model=None, declared: int = None,
                   represented: int = None) -> dict:
    """Context for gate_rule_scope; prefers register/model identity sets."""
    if register is not None and model is not None:
        return {'declared_rule_ids': sorted(register.declared_ids()),
                'represented_rule_ids': sorted(register.represented_ids(model))}
    return {'declared_rules': declared, 'represented_rules': represented}
