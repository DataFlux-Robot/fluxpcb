"""contract — executable semantic contracts between fluxpcb's components.

Four pieces, each closing a documented defect class:

    sexpr     span-preserving Specctra DSN scanning (no blind regex surgery)
    quant     nanometre-exact units + indeterminate comparisons at the
              representation boundary (review B §F.5)
    rules     the rule model as a finite table + exhaustive L1 diff
              (review D §6.1 — decidable, so a diff IS a proof)
    dsn_patch parsed rewrite with asserted firing + read-back verification

The rule model is a map ``(net) -> (width_nm, clearance_nm)`` plus a default
(net-less) rule and the via padstack geometry.  Two components that disagree
on this map are not talking about the same board; the diff makes that a
mechanical error instead of a silent divergence.
"""
from .quant import (NM_PER_MM, NM_PER_UM, Cmp, compare_min, mm_to_nm,
                    nm_to_um_str, parse_um, um_str_to_nm)
from .register import RuleRegister, RuleSpec
from .rules import ClassRule, RuleDiff, RuleModel
from .scope import Executor, Scope, ScopeBuilder, Tools
from .dsn_patch import (PatchReport, CongruenceError, congruence_audit,
                        patch_dsn)
from .sexpr import (find_node, direct_children, match_pairs, node_name,
                    span, tokenize, unquote)

__all__ = [
    'NM_PER_MM', 'NM_PER_UM', 'Cmp', 'compare_min', 'mm_to_nm',
    'nm_to_um_str', 'parse_um', 'um_str_to_nm',
    'ClassRule', 'RuleDiff', 'RuleModel',
    'RuleRegister', 'RuleSpec',
    'Executor', 'Scope', 'ScopeBuilder', 'Tools',
    'PatchReport', 'CongruenceError', 'congruence_audit', 'patch_dsn',
    'find_node', 'direct_children', 'match_pairs', 'node_name', 'span',
    'tokenize', 'unquote',
]
