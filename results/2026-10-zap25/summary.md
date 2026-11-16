# The 25 public boards of Zap!'s comparison, October 2026

Koppar on the 25 public boards Zap! (Mutual Solutions) published its comparison on
([zap.mutual.solutions/compare](https://zap.mutual.solutions/compare)), pinned to the same commits
(`boards/manifest-zap25.json`, `BOARD_SET=zap25`), each board under its own project rules and, where the
project ships one, its custom rules file. Koppar: one run per board, measured 2026-10-21 (stm32h7-hdmi-sodimm on 2026-10-22, from a build whose only change routes its address bus at its rules' 0.18 mm outer width; no other board's routing changed), 1200 s cap, one
workstation (24 cores, one GPU), KiCad 10.0.4 as referee (zones refilled, error severity). Per-board
results in `koppar.json`.

Every other column is copied from Zap!'s published data (`compare.json`, measured 2026-10-12 to 14 with
KiCad 10.0.6, one CPU core per board): Zap!'s single run (its service default), TraceMaker, Freerouting
and tscircuit with up to 60 minutes. We did not re-run them.

## Summary

| Router | Fully connected | Clean (0 open, 0 errors) | Connected, no more errors than the human board |
| --- | --- | --- | --- |
| **Koppar** | **14** | **11** | **14** |
| Zap! | 14 | 6 | 9 |
| TraceMaker | 7 | 3 | 4 |
| Freerouting | 11 | 2 | 2 |
| tscircuit | 10 | 0 | 0 |

## Per board

Each cell: open connections / DRC errors · seconds. Human: the DRC errors the published
design itself carries under the same rules (Zap!'s count).

| Board | Layers | Connections | Koppar | Zap! | TraceMaker | Freerouting | tscircuit | Human |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [water-level-monitor](https://github.com/Srushti-D-Hebbar/arduino-water-level-monitor/tree/7b47f46949e99d8075590de0c5a08a550d646d84) | 2 | 52 | **0 / 0 · 6 s** | 0 / 0 · 5 s | 0 / 0 · 11 s | 0 / 7 · 17 s | 0 / 3 · 8 s | 0 |
| [dual-ipex-csi-interposer](https://github.com/antmicro/dual-ipex-csi-interposer/tree/6b6bd0af5aafe13c02377d534a23a6373922f350) | 4 | 85 | **0 / 0 · 19 s** | 0 / 9 · 33 s | 0 / 69 · 412 s | 0 / 68 · 71 s | 0 / 35 · 61 s | 0 |
| [si-test-board](https://github.com/antmicro/signal-integrity-test-board/tree/de0b5922c372481bb146cbd1d0a9f3b7e97c124a) | 4 | 107 | **0 / 0 · 10 s** | 0 / 0 · 88 s | 5 / 0 · 319 s | 0 / 0 · 529 s | 36 / 20 · 34 s | 3 |
| [rp2040-devboard](https://github.com/acornitum/fucked-up-rp2040-devboard/tree/d94328619f4fcaef2f2d62aa42c62c789a463d56) | 2 | 139 | **26 / 17 · 1099 s** | 1 / 4 · 184 s | 11 / 5 · 2174 s | 17 / 35 · 257 s | 0 / 793 · 518 s | 3 |
| [ecp5-bga381-ft2232](https://github.com/gildobjanschi/ECP5_BGA381_FT2232HQ_FIFO_EXT/tree/81403f7487a9eded36c6500b8c8b6c12a6705f81) | 4 | 144 | **0 / 0 · 30 s** | 0 / 0 · 69 s | 6 / 0 · 3602 s | 0 / 0 · 120 s | 25 / 26 · 63 s | 0 |
| [ender3-loadcell](https://github.com/jujojujo2003/Ender-3-3DPrinter-Load-Cell-Bed-Leveling-Sensor/tree/eae5c6f3e67d0b7f83a500bfb3d1aef38cb69b0b) | 2 | 157 | **0 / 0 · 61 s** | 0 / 2 · 29 s | 0 / 2 · 109 s | 1 / 2 · 71 s | 0 / 285 · 103 s | 0 |
| [pmod-i3c-sensor](https://github.com/antmicro/pmod-i3c-sensor-board/tree/5ee5d34ff918422fe7af59f7ffdc9d4bdabf18a7) | 4 | 185 | **0 / 7 · 46 s** | 0 / 7 · 10 s | 0 / 7 · 324 s | 1 / 86 · 414 s | 5 / 232 · 42 s | 7 |
| [trellis-core](https://github.com/protolux-electronics/trellis_core/tree/db4fe71623c14bcea47d7457d0db4c99d3899124) | 4 | 208 | **0 / 0 · 19 s** | 0 / 0 · 20 s | 0 / 0 · 559 s | 0 / 2 · 130 s | 0 / 130 · 80 s | 0 |
| [cvbs-mipi-bridge](https://github.com/antmicro/cvbs-mipi-bridge/tree/7cbcec2bceaf6e1efd25cce1d73ff4989dfcfe02) | 4 | 211 | **0 / 0 · 205 s** | 0 / 2 · 109 s | 3 / 1 · 3601 s | 4 / 16 · 410 s | 0 / 240 · 144 s | 0 |
| [argos-demo](https://github.com/CNES/argos-demo-board/tree/1eccb3f20cb9004c046dba82b72bf85c500efcaa) | 4 | 234 | **0 / 0 · 18 s** | 0 / 0 · 24 s | 0 / 0 · 511 s | 0 / 1 · 104 s | 0 / 114 · 104 s | 0 |
| [seven](https://github.com/id8-engineering/seven-hardware/tree/7c69a1482ffd1dddc206294a55602477c55e17d8) | 4 | 240 | **2 / 0 · 1089 s** | 2 / 0 · 402 s | 14 / 2 · 3603 s | 18 / 7 · 692 s | 0 / 540 · 130 s | 0 |
| [gmsl-serializer](https://github.com/antmicro/gmsl-serializer/tree/58641df7b331050bbb4456cb4cb73e3975ae9de9) | 4 | 256 | **0 / 2 · 1071 s** | 4 / 14 · 131 s | 2 / 13 · 3382 s | 20 / 18 · 547 s | no board | 7 |
| [ftdi-jtag-programmer](https://github.com/anthony-bernaert/ftdi-jtag-programmer/tree/c33005ef528526c137ba8fd1e9cf4e8ceab63a1f) | 4 | 264 | **0 / 0 · 153 s** | 0 / 1 · 20 s | 0 / 3 · 72 s | 11 / 4 · 205 s | 0 / 419 · 89 s | 0 |
| [rp2350-pico2-bb48](https://github.com/OLIMEX/RP2350-PICO2-BB48/tree/74b030363ca03eea5bfa6dac59cbcd9e4277e910) | 4 | 269 | **26 / 0 · 1084 s** | 34 / 2 · 376 s | 30 / 2 · 3601 s | 8 / 245 · 557 s | 3 / 1028 · 941 s | 0 |
| [xerxes](https://github.com/rapidanalysis/xerxes/tree/43abf5730d092358bc6d10e9faa7233818b5412e) | 6 | 314 | **0 / 0 · 410 s** | 11 / 15 · 518 s | 27 / 13 · 3601 s | 0 / 237 · 172 s | 25 / 481 · 234 s | 0 |
| [bitaxe-ultra](https://github.com/BastelPichi/bitaxe/tree/0c3e71fd54fc0edef4c19c06d2320ef5468adc6a) | 4 | 333 | **0 / 3 · 247 s** | 0 / 3 · 420 s | 3 / 3 · 3601 s | 1 / 69 · 86 s | 0 / 444 · 256 s | 3 |
| [cm4-lvds-adapter](https://github.com/antmicro/cm4-baseboard-lvds-adapter/tree/4af945ed558e58e4b8aea6fe909fc33d78b63f6f) | 6 | 377 | **0 / 0 · 75 s** | 0 / 13 · 478 s | 1 / 9 · 3602 s | 6 / 137 · 733 s | 1 / 389 · 439 s | 1 |
| [tiliqua-motherboard](https://github.com/apfaudio/tiliqua-hardware/tree/4ee5878ae821a6b7096f6aed6795dc1349475cb5) | 6 | 531 | **30 / 24 · 1119 s** | 2 / 17 · 533 s | 42 / 16 · 3601 s | 0 / 242 · 1727 s | no board | 14 |
| [stm32h7-hdmi-sodimm](https://github.com/antmicro/stm32h7-hdmi-board-sodimm/tree/27e3ac9968983f236155c288ded30beb1a2d24a1) | 6 | 567 | **5 / 6 · 1117 s** | 0 / 4 · 219 s | 36 / 203 · 3601 s | 0 / 240 · 3621 s | 71 / 1122 · 470 s | 4 |
| [discretesim-hwtest](https://github.com/cnlohr/discretesim/tree/f7a169ee07b18b6c857d23582474b8ba20185436) | 2 | 582 | **24 / 0 · 1188 s** | 0 / 0 · 40 s | 25 / 0 · 3007 s | 62 / 0 · 613 s | 2 / 566 · 139 s | 0 |
| [kintex-pcie](https://github.com/piotr-binkowski/kintex-pcie-board/tree/056792c009a8920d4ef11e76ba3284ee58376078) | 8 | 706 | **17 / 22 · 1090 s** | 6 / 30 · 567 s | 57 / 55 · 3601 s | 0 / 263 · 3618 s | no board | 18 |
| [buildbotics-controller](https://github.com/buildbotics/bbctrl-pcb-new/tree/1a2f36ddc4797ac05e45405fb5664ff1f7ca7974) | 4 | 890 | **9 / 4 · 1186 s** | 7 / 4 · 515 s | 46 / 4 · 3601 s | 26 / 203 · 3618 s | 1 / 899 · 1383 s | 5 |
| [emberone](https://github.com/256foundation/emberone00-pcb/tree/b21b32ca49710b99cb75f86406b0df5190c9beb4) | 6 | 1125 | **24 / 0 · 1190 s** | 5 / 1 · 445 s | 18 / 0 · 3602 s | 0 / 218 · 2653 s | 20 / 799 · 577 s | 0 |
| [hackrf-pro](https://github.com/greatscottgadgets/hackrf-pro/tree/359b98f2451395408bc25dae64efa9fc518e93d0) | 6 | 1677 | **102 / 0 · 1157 s** | 11 / 9 · 642 s | 130 / 9 · 3602 s | 80 / 210 · 3626 s | no board | 8 |
| [ecp5-dc-scm](https://github.com/antmicro/ecp5-dc-scm/tree/0019313fd2e214f618788e5e61636a4a023ee361) | 8 | 1843 | **105 / 26 · 1163 s** | 113 / 50 · 937 s | 352 / 23 · 3602 s | 1818 / 8 · 3689 s | no board | 9 |
