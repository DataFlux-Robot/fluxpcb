# PCBWorld-Bench

The real-board test sets of [PCBWorld](https://github.com/LGAI-Research/PCBWorld)
(LG AI Research, [arXiv 2607.05915](https://arxiv.org/abs/2607.05915)): D3-A, 99 boards,
and D3-B, 10 boards, drawn from the public [PCBench](https://github.com/PCBench/PCBench)
collection. All but one are two-layer. No board is stored here.

```sh
export KICAD_PYTHON=/usr/bin/python3                # a python that imports pcbnew
python3 pcbworld/prepare.py                         # work/pcbworld/boards/<id>/, boards.json
python3 pcbworld/score.py work/pcbworld/boards.json <routed> <out.json>
```

`prepare.py` clones PCBWorld and PCBench at pinned commits and runs PCBWorld's own
preparation chain (`tools/datagen/pcbench_prep`) on the test boards only, with the two
patches in `patches/` so it runs under KiCad 10 (net names instead of net numbers in the
file; KiCad 10's extra `pth_inside_courtyard` check set to ignore, like the checks the chain
already ignores). Each board's folder holds the routed guide board as
`circuit.reference.kicad_pcb` and its project as `circuit.kicad_pro`; a router gets the board
with its tracks and vias removed, pours and placement kept.

`score.py` is the clean-pass check: `kicad-cli pcb drc` with the board's project, zones
refilled, error severity; a board passes with no error and no unconnected item. Results:
`results/pcbworld-2026-10/`.
