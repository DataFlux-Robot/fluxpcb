# PCBWorld-Bench, October 2026

Koppar on the PCBWorld-Bench real-board test sets, one attempt per board, measured
2026-10-18. Boards prepared with `pcbworld/prepare.py`, scored with `pcbworld/score.py`;
per-board results in `koppar.json`.

**Re-run, 2026-10-22.** The same 109 boards, one attempt each, on a later Koppar build: again
99/99 on D3-A (5.0 s median, 32.8 s longest) and 10/10 on D3-B (7.6 s median, 10.3 s longest), 283 and
151 vias. Per-board results in `koppar-2026-10-22.json`; the table below is the 2026-10-18 run.

**Clean pass**: the routed board has every connection made and zero error-level
violations in KiCad's design-rule check, under the board's own rules (the PCBWorld guide
project). One missing connection scores zero. The score is the fraction of boards that pass.

| Method | D3-A (99 boards) | Time per attempt | D3-B (10 boards) | Time per attempt |
| --- | --- | --- | --- | --- |
| **Koppar** | **1.000** (99/99) | 4.7 s median, 32 s longest | **1.000** (10/10) | 6.5 s median, 23 s longest |
| DeepPCB | 0.929 | 3 min budget; first clean pass 38 s (median) | 0.800 | 3 min budget; first clean pass 46 s (median) |
| PPO (w/o finish) | 0.81 | 3.05 s | 0.22 | 14.44 s |
| Freerouting | 0.75 | 7.09 s | 0.64 | 9.94 s |
| KiCadRoutingTools | 0.74 | 0.65 s | 0.20 | 3.27 s |
| PPO | 0.68 | 1.83 s | 0.21 | 10.77 s |
| PPO (terminal) | 0.65 | 2.58 s | 0.15 | 12.60 s |
| GRPO | 0.64 | 3.54 s | 0.07 | 11.20 s |
| GPT-5.4 | 0.42 | 231.15 s | 0.00 | 865.83 s |
| Qwen3.5-397B | 0.19 | 81.82 s | 0.00 | 222.29 s |
| GPT-5.4-nano | 0.15 | 100.76 s | 0.00 | 510.25 s |
| GPT-5.4-mini | 0.13 | 56.85 s | 0.00 | 422.06 s |
| OrthoRoute | 0.02 | 2.20 s | 0.00 | 9.30 s |

Every row but Koppar's is copied as published by DeepPCB on 28 September 2026
([PCBWorld-Bench: DeepPCB results](https://deeppcb.ai/benchmark/pcbworld-bench-deeppcb-results/)),
whose baseline rows come from the PCBWorld paper
([arXiv 2607.05915](https://arxiv.org/abs/2607.05915v4)). We did not re-run any of them.
The paper also reports a best-of-five score with its own selection (D3-A 0.94, D3-B 0.78);
the table compares single attempts.

## How Koppar was run

- **Input**: each test board's guide board (`processed_v9_guide_v3`) with its tracks, vias and
  arcs removed, its copper pours and placement kept, and the guide project beside it, as
  PCBWorld's own pipeline loads it.
- **Router**: Koppar's route path with its 1200 s cap per board (no board came near it), one
  configuration for every board, run on the benchmark machine.
- **Machine**: one workstation (24 cores, one GPU). DeepPCB's times are from its own setup.

## Referee and differences from the paper's setup

- KiCad 10.0.4 (`kicad-cli pcb drc`, zones refilled, error severity, the guide project's
  rules). PCBWorld scores with its own KiCad 9.0.8 build. The preparation chain ran under
  KiCad 10 with two small patches (`pcbworld/patches/`): KiCad 10 writes net names where
  KiCad 9 wrote net numbers, and it adds a `pth_inside_courtyard` check, set to ignore like
  the other checks the chain ignores.
- Under KiCad 10 the reference boards themselves pass on 104 of 109: five guide boards keep a
  clearance finding the chain could not narrow away (0024, 0040, 0083, 0203, 0344). Koppar's
  routing passes on all five.

## What we do not claim

- Vias: Koppar placed 290 vias on D3-A and 140 on D3-B, against 117 and 67 on the reference
  boards (2.1 to 2.5 times). DeepPCB reports 0.59 and 0.50 times the reference on the nets
  without a pour. A clean pass does not score via count or track length.
- One run per board. The route is a timed search; a board can pass in one run and miss in another.
