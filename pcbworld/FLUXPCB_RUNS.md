# fluxpcb runs on PCBWorld-Bench (scripts and result summaries)

Scripts and result summaries for the 118-board runs
(D3-A 99 / D3-B 10 / D3-C 9), scored by `kicad-cli pcb drc` under each
board's own project rules. `README.md` above is the upstream description of
the benchmark itself.

## Where the data lives

Board inputs and per-board run artifacts are **not in this repository**
(about 1.7 GB of boards, routed outputs, DRC reports and caches). On the
development machine they live outside the repo and `work/pcbworld` links to
that directory. The scripts resolve `boards/`, `runs/` and `rounds/` relative
to their own location, so run them from there, not from this copy.

```
<data root>/
  boards/<dir>/circuit.reference.kicad_pcb   # placed, unrouted input
  boards/<dir>/circuit.kicad_pro             # the project that judges it
  runs/<dir>/                                # latest per-board run
  rounds/<r>/runs/<dir>/cert.json            # round-scoped runs
```

`manifests/boards_full.json` lists the 118 boards and their split.

## Requirements

- KiCad 10: `kicad-cli` and its bundled Python (the one that imports `pcbnew`).
- JDK 25 and `freerouting-2.5.0.jar` (see `fluxpcb/router/freerouting.py`).
- The `fluxpcb` package from the flux-neurotransmitter repository.

```bash
export KICAD_CLI=<kicad>/bin/kicad-cli.exe
export KICAD_PYTHON=<kicad>/bin/python.exe
```

## Entry points added for the fluxpcb runs

| Script | Purpose |
|---|---|
| `run_bench.py` | Route and repair boards; writes `cert.json` per board. `--runs-dir`, `--rows`, `--policy` scope a round. |
| `full_round.py` | Run a split one board per subprocess; resumable; completion is judged by a fresh `cert.json`. |
| `score_runs.py` | Re-score routed boards with the source project's DRC; total and routing-axis clean rates. |
| `tri_round.py`, `compare_rounds.py` | Run one policy round; compare rounds with each other and with PCBRouteAgent. |
| `iterate.py` | Small policy-iteration driver (train / held-out / audit). |
| `redteam.py` | Second oracle: re-judge clean boards under a JLC fab profile (AHR). |
| `mutate_boards.py`, `pair_probe.py`, `inject_corruption.py`, `coverage_report.py` | Negative controls and gate coverage. |

## Results

`results/` holds summaries only (no boards):

- `rounds/r1|r2|r3/`: `policy.json`, `round.json`, `score.json`,
  `score_summary.json`, `status.jsonl`; and `rounds/comparison.json`.
- `docs/ROUNDS_20261124.md`: the three-round result **and its correction**.

**Read the correction before quoting the round numbers.** The latest
configuration scores 109/118, but the 101 → 106 → 109 progression across
rounds is not an iteration gain: a policy that disallowed one operator
vetoed the whole repair batch on every board where that operator ran.
Per-operator attribution has not been measured.
