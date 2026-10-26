# -*- coding: utf-8 -*-
"""Board State Kernel — truthnet.py: fabrication-grade final audit.

Facet 5's hard edge (truth closure). Industry standard: the fab
extracts a "golden netlist" from copper geometry and compares it to the
design netlist (IPC-D-356A). KiCad can produce BOTH sides headlessly:

    kicad-cli pcb export ipcd356  -> board.d356   (fab-side truth)
    kicad-cli sch export netlist --format kicadsexpr -> sch.net

This module parses both and compares net -> pin-ref sets. It is the
ONLY connectivity verdict that cannot be gamed by severity settings,
stale fills, or model divergence — the file the fab will actually etch
versus the schematic the designer intended.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


def export_both(kicad_cli: str, pcb: Path, sch: Path,
                workdir: Path | None = None) -> tuple[Path, Path]:
    workdir = workdir or pcb.parent
    d356 = workdir / (pcb.stem + ".d356")
    net = workdir / (pcb.stem + ".sch.net")
    subprocess.run([kicad_cli, "pcb", "export", "ipcd356",
                    "-o", str(d356), str(pcb)],
                   capture_output=True, text=True, timeout=300,
                   encoding="utf-8", errors="replace", check=True)
    subprocess.run([kicad_cli, "sch", "export", "netlist",
                    "--format", "kicadsexpr",
                    "-o", str(net), str(sch)],
                   capture_output=True, text=True, timeout=300,
                   encoding="utf-8", errors="replace", check=True)
    return d356, net


def parse_d356(path: Path) -> dict:
    """IPC-D-356 (KiCad output shape) -> {net: set('REF.PIN', ...)}.

    Real shape observed on dev (KiCad 10.0.6), columns verified:
      3175V               J1    -1    D0394...   (component pads)
      327CC2              R6    -1          A01X...  (routed/test pts)
      0-2 code | 3-19 net | 19-24 ref | 24-29 pin (may carry '-')
    Both 317 and 327 carry net/ref/pin at identical columns; via
    records have ref='VIA' and empty pin (skipped).
    """
    nets = {}
    for line in path.read_text(encoding="utf-8", errors="replace") \
            .splitlines():
        if not (line.startswith("317") or line.startswith("327")):
            continue
        net = line[3:19].strip()
        ref = line[19:24].strip()
        pin = line[24:29].strip().lstrip("-")
        if not net:
            continue
        nets.setdefault(net, set())
        if ref and pin:
            nets[net].add(f"{ref}.{pin}")
    return {k: v for k, v in nets.items() if v}


def parse_kicad_netlist(path: Path) -> dict:
    """kicadsexpr netlist -> {net: set('REF.PIN', ...)}.

    Real shape: multi-line indented s-expr —
      (net (code "1") (name "3V3") (class "Default")
        (node (ref "C2") (pin "1") (pinfunction ...) ...))
    Line-scanner state machine: (name starts a net AND clears any
    pending ref — a ref leaked across nets was misattributing header
    pins), (ref) pends against the CURRENT net only, (pin) consumes
    the pending ref. 'unconnected-*' nets are dropped (industry
    practice: no-connects are not part of the golden netlist).
    """
    nets = {}
    cur = None
    pend = None                      # (net, ref) — bound to its net
    in_nets = False
    for line in path.read_text(encoding="utf-8", errors="replace") \
            .splitlines():
        s = line.strip()
        if s == "(nets":
            in_nets = True
            continue
        if not in_nets:
            continue                 # component properties carry their
                                     # own (name "Footprint") shapes
        if s.startswith('(name "'):
            m = re.match(r'\(name "([^"]+)"\)', s)
            if m:
                cur = m.group(1)
                pend = None
                if not cur.startswith("unconnected"):
                    nets.setdefault(cur, set())
        elif s.startswith('(ref "') and cur is not None:
            mr = re.match(r'\(ref "([^"]+)"\)', s)
            if mr:
                pend = (cur, mr.group(1))
        elif s.startswith('(pin "') and pend is not None:
            mp = re.match(r'\(pin "([^"]+)"\)', s)
            if mp and pend[0] == cur and cur in nets:
                nets[cur].add(f"{pend[1]}.{mp.group(1)}")
            pend = None
    return nets


def compare(board_nets: dict, sch_nets: dict) -> dict:
    """The audit verdict: what the fab will etch vs the intent."""
    missing = {n: sorted(pins) for n, pins in sch_nets.items()
               if n not in board_nets}
    pin_mismatch = {}
    for n, pins in sch_nets.items():
        if n in board_nets and board_nets[n] != pins:
            pin_mismatch[n] = {
                "sch_only": sorted(pins - board_nets[n]),
                "board_only": sorted(board_nets[n] - pins),
            }
    return {"nets_sch": len(sch_nets), "nets_board": len(board_nets),
            "missing_nets": missing, "pin_mismatch": pin_mismatch,
            "clean": not missing and not pin_mismatch}


def audit(kicad_cli: str, pcb: Path, sch: Path) -> dict:
    d356, net = export_both(kicad_cli, pcb, sch)
    return compare(parse_d356(d356), parse_kicad_netlist(net))
