"""flux CLI: init / run / report / gate / probe."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fluxpcb import __version__
from fluxpcb.engine import run_pipeline, verify_only
from fluxpcb.gates import run_gate
from fluxpcb.models import GateStatus
from fluxpcb.tools.adapters import probe_all

ROOT = Path(__file__).resolve().parent.parent

EXAMPLE_PROJECT = """\
name: {name}
requirements:
  - id: FR-001
    text: 核心功能可运行
    priority: must
    covered_by: [bom, schematic]
power:
  input: USB-C 5V
  total_w: 2.0
  rails:
    - 3V3
    - 1V8
interfaces:
  - name: spi_host
    type: spi
    high_speed: true
    connections: [SPI_MOSI, SPI_MISO, SPI_SCK, SPI_CS]
    impedance_ohm: 50
    style: microstrip
    h_mm: 0.2
  - name: wifi_module
    type: wifi
    mcu:
      family: esp32s31
      pins:
        spi_mosi: "GPIO11"
        spi_miso: "GPIO13"
        spi_sck: "GPIO12"
        spi_cs: "GPIO10"
constraints:
  size_mm: [65, 30]
  layers: 6
  er: 4.4
bom_seed:
  mcu:
    mpn: HPM6E80IVM1
    lcsc: C000000
    footprint: BGA
    datasheet: reference/hpm6e80_ds.pdf
    value: HPM6E80
  wifi:
    mpn: ESP32-S31-WROOM-3U
    lcsc: C000000
    footprint: MOD_WROOM
    datasheet: reference/esp32s31_wroom3u.pdf
    value: ESP32-S31
"""


def main(argv=None):
    ap = argparse.ArgumentParser(prog="flux",
                                 description="FluxPCB 全自动电路设计流水线")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="创建项目(从需求模板)")
    p_init.add_argument("dir", type=Path)

    p_run = sub.add_parser("run", help="执行六环节流水线+门禁")
    p_run.add_argument("dir", type=Path)
    p_run.add_argument("--pipeline", type=Path, default=None)
    p_run.add_argument("--only", nargs="*", default=None,
                       help="只执行指定环节(如 requirements schematic)")

    p_report = sub.add_parser("report", help="查看最近一次运行报告")
    p_report.add_argument("dir", type=Path)
    p_report.add_argument("--json", action="store_true")

    p_gate = sub.add_parser("gate", help="单独运行门禁")
    p_gate.add_argument("dir", type=Path)
    p_gate.add_argument("--only", nargs="+", required=True)

    sub.add_parser("probe", help="探测外部工具链可用性")

    p_ver = sub.add_parser("verify", help="仅复检门禁(对磁盘产物,不重新生成)")
    p_ver.add_argument("dir", type=Path)
    p_ver.add_argument("--pipeline", type=Path, default=None)

    p_dash = sub.add_parser("dashboard", help="启动本地Grounding Map可视化面板")
    p_dash.add_argument("dir", type=Path, nargs="?", default=ROOT / "projects",
                        help="projects根目录或单个项目目录")
    p_dash.add_argument("--port", type=int, default=8765)
    p_dash.add_argument("--no-open", action="store_true", help="不自动打开浏览器")

    args = ap.parse_args(argv)

    if args.cmd == "init":
        d = args.dir
        d.mkdir(parents=True, exist_ok=True)
        (d / "project.yaml").write_text(EXAMPLE_PROJECT.format(name=d.name),
                                        encoding="utf-8")
        (d / "requirements.md").write_text(
            f"# {d.name} 需求\n\n(在此用自然语言描述需求,六环节流水线将以此生成spec)\n",
            encoding="utf-8")
        print(f"[flux] 项目已创建: {d}")
        return 0

    if args.cmd == "run":
        pipeline = args.pipeline or (ROOT / "flux.yaml")
        report = run_pipeline(args.dir, pipeline, only_stages=args.only)
        print(report.markdown())
        return 0 if report.released else 1

    if args.cmd == "report":
        latest = args.dir / "reports" / ("latest.json" if args.json else "latest.md")
        if not latest.exists():
            print("[flux] 尚无运行报告,先执行 flux run")
            return 1
        print(latest.read_text(encoding="utf-8"))
        return 0

    if args.cmd == "gate":
        for name in args.only:
            res = run_gate(name, args.dir, [])
            mark = {"pass": "✅", "fail": "⛔", "blocked": "⏸️", "skipped": "⏭️"}[res.status.value]
            print(f"{mark} {name}: {res.summary}")
            for f in res.findings[:5]:
                print(f"    - {f}")
        return 0

    if args.cmd == "probe":
        print(json.dumps(probe_all(), ensure_ascii=False, indent=1))
        return 0

    if args.cmd == "verify":
        pipeline = args.pipeline or (ROOT / "flux.yaml")
        report = verify_only(args.dir, pipeline)
        print(report.markdown())
        return 0 if report.released else 1

    if args.cmd == "dashboard":
        from fluxpcb.dashboard import serve
        root = args.dir.resolve()
        if (root / "project.yaml").exists():  # single project dir -> its parent
            root = root.parent
        serve(root, port=args.port, open_browser=not args.no_open)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
