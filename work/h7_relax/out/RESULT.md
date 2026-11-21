# H7 BGA — Freerouting relaxed-clearance run (2026-11-21)

## Result
- Board: h743-fc base placement (STM32H743 TFBGA240, 6 layers, 259 ratsnest)
- DSN clearance: 0.10mm (board's own rule = 0.09mm, so COMPLIANT)
- **Unconnected: 14 / 259 = 94.6% completion** (was 71 opens = 72.6%)
- Vias: 105, track length: 2203.8mm
- Routing time: ~460s (Freerouting 2.4.1, 6 passes, plateau)

## DRC (board rules: clearance 0.09 / track 0.15)
- Errors: 59
  - malformed_courtyard: 33 (pre-existing footprint)
  - courtyards_overlap: 15 (placement, pre-existing)
  - pth_inside_courtyard: 6 (placement, pre-existing)
  - hole_clearance: 5 (routing-related)
- **clearance errors: 0** (routing used 0.10mm ≥ board's 0.09mm)
- Unconnected: 14

## Key discovery
`pcbnew.LoadBoard()` does not load the .kicad_pro, so KiCad's DSN export
always writes the default 0.2mm clearance — 2.2x stricter than this board's
0.09mm rule. Routing at the board's true rule (0.09-0.10mm) is what unlocks
the TFBGA240 0.8mm-pitch escape channels.

## Next
- Fix the 14 residual opens (tracks drawn but not connecting — connector-level)
- Router-related DRC is only 5 hole_clearance
