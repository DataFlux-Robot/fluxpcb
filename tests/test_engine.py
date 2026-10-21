"""Engine tests: DAG execution, feedback retry, audit trail."""
import json
from pathlib import Path

import yaml

from fluxpcb.engine import run_pipeline
from fluxpcb.models import GateStatus, StageStatus


def _make_project(tmp_path: Path) -> Path:
    d = tmp_path / "demo"
    d.mkdir()
    proj = {
        "name": "demo",
        "requirements": [
            {"id": "FR-001", "text": "SPI host", "priority": "must",
             "covered_by": ["spi_host"]},
            {"id": "FR-002", "text": "WiFi module", "priority": "must",
             "covered_by": ["wifi_module"]},
        ],
        "power": {"input": "USB-C 5V", "total_w": 2.0, "rails": ["3V3"]},
        "interfaces": [
            {"name": "spi_host", "type": "spi", "high_speed": True,
             "connections": ["SPI_MOSI", "SPI_MISO", "SPI_SCK", "SPI_CS"],
             "impedance_ohm": 50, "style": "microstrip", "h_mm": 0.2},
            {"name": "wifi_module", "type": "wifi", "power_w": 1.0,
             "mcu": {"family": "esp32s31",
                     "pins": {"spi_mosi": "GPIO11", "spi_miso": "GPIO13",
                              "spi_sck": "GPIO12", "spi_cs": "GPIO10"}}},
        ],
        "constraints": {"size_mm": [65, 30], "layers": 6, "er": 4.4},
        "bom_seed": {
            "mcu": {"mpn": "HPM6E80IVM1", "lcsc": "C1", "footprint": "BGA",
                    "datasheet": "ref/ds.pdf", "value": "HPM6E80"},
            "wifi": {"mpn": "ESP32-S31-WROOM-3U", "lcsc": "C2", "footprint": "MOD",
                     "datasheet": "ref/wifi.pdf", "value": "ESP32-S31"},
        },
    }
    (d / "project.yaml").write_text(yaml.safe_dump(proj, allow_unicode=True),
                                    encoding="utf-8")
    (d / "requirements.md").write_text("# demo\nSPI host + WiFi\n", encoding="utf-8")
    return d


def test_pipeline_end_to_end(tmp_path):
    d = _make_project(tmp_path)
    report = run_pipeline(d)
    stage_names = [s.stage for s in report.stages]
    assert stage_names == ["requirements", "component_selection", "schematic",
                           "pcb_layout", "acceptance_sim", "firmware"]
    # every stage executed at least once with bounded iterations
    assert all(1 <= s.iterations <= 3 for s in report.stages)
    # artifacts produced and hashed
    artifacts = [a for s in report.stages for a in s.artifacts]
    assert artifacts, "no artifacts captured"
    assert all(len(a.sha256) == 64 for a in artifacts)
    # evidence files exist
    assert (d / "spec.yaml").exists()
    assert (d / "bom.csv").exists()
    assert (d / "schematic" / "netlist.net").exists()
    assert (d / "stackup.yaml").exists()
    assert (d / "sim" / "pdn_input.yaml").exists()
    assert (d / "firmware" / "build_ok.json").exists()
    # report saved
    assert (d / "reports" / "latest.json").exists()
    assert (d / "reports" / "latest.md").exists()
    rep = json.loads((d / "reports" / "latest.json").read_text(encoding="utf-8"))
    assert set(rep) >= {"project", "released", "stages"}


def test_pipeline_offline_all_gates_evaluated(tmp_path):
    d = _make_project(tmp_path)
    report = run_pipeline(d)
    gate_names = [g.name for s in report.stages for g in s.gates]
    for expected in ("spec_schema", "requirement_coverage", "bom_completeness",
                     "netlist_topology", "impedance_si", "irdrop", "thermal",
                     "emc_precompliance", "firmware_build", "firmware_spi_loopback"):
        assert expected in gate_names, f"gate {expected} not evaluated"
    # no gate crashed to unknown state
    assert all(g.status != GateStatus.SKIPPED for s in report.stages for g in s.gates)


def test_reproducibility_same_hashes(tmp_path):
    d = _make_project(tmp_path)
    r1 = run_pipeline(d)
    h1 = [a.sha256 for a in r1.stages[0].artifacts]
    r2 = run_pipeline(d)
    h2 = [a.sha256 for a in r2.stages[0].artifacts]
    assert h1 == h2  # deterministic offline mode -> identical spec hash
