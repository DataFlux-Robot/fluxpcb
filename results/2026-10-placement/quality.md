# Quality, campaign 2026-10-placement

Measured with `scripts/quality.py` on each routed board; `human` is the upstream design. A dash: not measured (no board, or nothing of that kind on it).

## High-speed track over a ground plane (% of length)

| Board | human | koppar | koppar_filled | tracemaker | freerouting | tscircuit | krt | zap |
|---|---|---|---|---|---|---|---|---|
| pic-programmer | — | — | — | — | — | — | — | — |
| rp2040-minimal | 98.7% | 70.1% | 74.2% | 0.0% | 0.6% | 0.4% | — | 81.8% |
| pico-vga | 87.5% | 0.7% | 0.0% | 0.7% | 0.7% | 0.7% | 0.7% | 97.7% |
| cm5io | 97.2% | 58.6% | 63.9% | 0.4% | no board | — | 1.6% | 97.2% |
| rk3328-som-evb | 77.6% | 56.4% | 70.6% | 1.0% | 1.2% | 0.7% | 0.7% | 94.4% |
| scalenode-cm4 | 74.2% | 48.2% | 47.6% | 1.5% | no board | 1.5% | 1.4% | 95.2% |
| stmp157-som-evb | 47.6% | 40.4% | 42.8% | 1.6% | no board | — | 0.0% | 90.0% |
| imx8mp-som-evb | 56.0% | 45.6% | 40.7% | 0.6% | no board | 1.3% | 0.0% | 93.2% |

## High-speed track over any plane (% of length)

| Board | human | koppar | koppar_filled | tracemaker | freerouting | tscircuit | krt | zap |
|---|---|---|---|---|---|---|---|---|
| pic-programmer | — | — | — | — | — | — | — | — |
| rp2040-minimal | 98.7% | 70.1% | 74.2% | 0.0% | 0.6% | 0.4% | — | 84.3% |
| pico-vga | 87.5% | 86.3% | 89.9% | 0.7% | 0.7% | 0.7% | 0.7% | 97.7% |
| cm5io | 97.2% | 58.6% | 63.9% | 0.4% | no board | — | 1.6% | 97.2% |
| rk3328-som-evb | 90.1% | 88.0% | 90.7% | 1.0% | 1.2% | 0.7% | 0.7% | 94.4% |
| scalenode-cm4 | 90.2% | 71.7% | 76.0% | 1.5% | no board | 1.5% | 1.4% | 95.2% |
| stmp157-som-evb | 92.5% | 73.6% | 82.1% | 1.6% | no board | — | 0.0% | 90.0% |
| imx8mp-som-evb | 92.8% | 83.7% | 83.8% | 0.6% | no board | 1.3% | 0.0% | 93.2% |

## High-speed vias with a ground via within 1.5 mm (%)

| Board | human | koppar | koppar_filled | tracemaker | freerouting | tscircuit | krt | zap |
|---|---|---|---|---|---|---|---|---|
| pic-programmer | — | — | — | — | — | — | — | — |
| rp2040-minimal | — | 40.0% | 50.0% | — | 0.0% | 0.0% | — | 83.3% |
| pico-vga | — | 0.0% | 0.0% | 0.0% | — | 0.0% | — | — |
| cm5io | 100.0% | 73.9% | 71.6% | 16.4% | no board | — | 0.0% | 92.9% |
| rk3328-som-evb | 16.7% | 50.0% | 46.3% | 4.1% | 0.0% | 15.0% | 0.0% | 70.6% |
| scalenode-cm4 | 43.1% | 61.5% | 71.7% | 6.1% | no board | 12.2% | 0.0% | 83.7% |
| stmp157-som-evb | 6.7% | 53.1% | 40.5% | 6.9% | no board | — | 0.0% | 61.4% |
| imx8mp-som-evb | 9.8% | 53.6% | 55.8% | 6.2% | no board | 7.1% | 0.0% | 78.2% |

## Differential pairs: median length mismatch (mm)

| Board | human | koppar | koppar_filled | tracemaker | freerouting | tscircuit | krt | zap |
|---|---|---|---|---|---|---|---|---|
| pic-programmer | — | — | — | — | — | — | — | — |
| rp2040-minimal | 0.104 | 0.215 | 0.01 | 0.14 | 0.151 | 0.104 | — | 0.095 |
| pico-vga | — | — | — | — | — | — | — | — |
| cm5io | 0.04 | 4.357 | 1.986 | 1.824 | no board | — | — | 0.494 |
| rk3328-som-evb | 0.0 | 2.486 | 1.132 | 0.617 | 3.302 | 1.21 | 3.372 | 0.166 |
| scalenode-cm4 | 0.018 | 1.825 | 1.775 | 1.477 | no board | 3.106 | 3.917 | 0.134 |
| stmp157-som-evb | 0.0 | 0.134 | 0.274 | 1.36 | no board | — | 4.203 | 0.788 |
| imx8mp-som-evb | 0.0 | 2.162 | 1.916 | 1.678 | no board | 2.014 | 0.896 | 0.228 |

## Differential pairs: median share routed side by side (%)

| Board | human | koppar | koppar_filled | tracemaker | freerouting | tscircuit | krt | zap |
|---|---|---|---|---|---|---|---|---|
| pic-programmer | — | — | — | — | — | — | — | — |
| rp2040-minimal | 100.0% | 55.4% | 87.5% | 4.3% | 0.0% | 0.0% | — | 98.9% |
| pico-vga | — | — | — | — | — | — | — | — |
| cm5io | 92.8% | 21.0% | 30.75% | 0.3% | no board | — | — | 22.3% |
| rk3328-som-evb | 87.1% | 23.8% | 29.4% | 5.7% | 14.5% | 9.9% | 0.0% | 77.9% |
| scalenode-cm4 | 94.5% | 39.1% | 44.45% | 4.1% | no board | 4.1% | 0.7% | 67.3% |
| stmp157-som-evb | 82.1% | 45.8% | 44.9% | 2.1% | no board | — | 35.5% | 39.75% |
| imx8mp-som-evb | 84.4% | 35.7% | 30.6% | 9.0% | no board | 6.6% | 7.0% | 60.4% |

## Decoupling: median track path, capacitor to pin (mm)

| Board | human | koppar | koppar_filled | tracemaker | freerouting | tscircuit | krt | zap |
|---|---|---|---|---|---|---|---|---|
| pic-programmer | 35.12 | 20.48 | 21.59 | 17.25 | 35.0 | 17.25 | 17.25 | — |
| rp2040-minimal | 2.595 | 5.43 | 5.81 | 3.06 | 3.9 | 3.15 | 8.61 | 2.46 |
| pico-vga | 4.07 | 7.87 | 8.19 | 5.89 | 5.23 | 5.89 | 14.2 | 5.52 |
| cm5io | 4.18 | 5.13 | 4.07 | 5.63 | no board | — | — | 1.92 |
| rk3328-som-evb | 4.38 | 6.8 | 4.49 | 4.43 | 4.17 | 5.89 | — | 1.45 |
| scalenode-cm4 | 1.86 | 5.1 | 5.07 | 5.3 | no board | 7.015 | — | 1.34 |
| stmp157-som-evb | 4.58 | 19.605 | 15.405 | 5.39 | no board | — | 84.39 | 3.53 |
| imx8mp-som-evb | 2.7 | 3.11 | 3.01 | 1.795 | no board | 3.19 | 35.7 | 1.45 |

## Decoupling: median vias on that path

| Board | human | koppar | koppar_filled | tracemaker | freerouting | tscircuit | krt | zap |
|---|---|---|---|---|---|---|---|---|
| pic-programmer | 0 | 0 | 0 | 0 | 0 | 0 | 0 | — |
| rp2040-minimal | 0.0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0 |
| pico-vga | 0.0 | 0.0 | 0.0 | 0 | 0.0 | 0 | 0.0 | 0 |
| cm5io | 0 | 0 | 1 | 1 | no board | — | — | 0.0 |
| rk3328-som-evb | 0 | 0 | 2 | 0 | 0 | 0.0 | — | 0 |
| scalenode-cm4 | 0.0 | 0 | 0 | 0.0 | no board | 0.5 | — | 0.0 |
| stmp157-som-evb | 0.0 | 2.0 | 2.0 | 0.0 | no board | — | 4 | 0.0 |
| imx8mp-som-evb | 0 | 0 | 0 | 0.0 | no board | 0.0 | 0 | 0 |
