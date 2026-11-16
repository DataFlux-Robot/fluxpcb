"""Gate unit tests on synthetic project fixtures."""
import json
from pathlib import Path

import pytest
import yaml

from fluxpcb.gates import run_gate, all_gates
from fluxpcb.models import GateStatus


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    """A minimal passing project fixture."""
    spec = {
        "project": "fixture",
        "requirements": [
            {"id": "FR-001", "text": "SPI通信", "priority": "must",
             "covered_by": ["spi_host"]},
            {"id": "FR-002", "text": "WiFi", "priority": "must", "covered_by": ["wifi"]},
        ],
        "power": {"input": "USB-C 5V", "rails": [{"name": "3V3", "v": 3.3,
                                                   "current_a": 0.5,
                                                   "max_drop_pct": 3.0,
                                                   "cu_mm": 0.035,
                                                   "sinks": [{"x": 32, "y": 8}]}]},
        "interfaces": [
            {"name": "spi_host", "type": "spi", "high_speed": True,
             "connections": ["SPI_MOSI", "SPI_MISO"],
             "impedance_ohm": 50, "style": "microstrip", "h_mm": 0.2},
        ],
        "constraints": {"size_mm": [65, 30], "layers": 6,
                        "family": "esp32s31-devboard"},
    }
    (tmp_path / "spec.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    bom = [{"ref": "U1", "mpn": "HPM6E80IVM1", "lcsc": "C1", "footprint": "BGA",
            "datasheet": "d.pdf", "value": "HPM6E80"}]
    import csv
    with (tmp_path / "bom.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["ref", "mpn", "lcsc", "footprint",
                                          "datasheet", "value"])
        w.writeheader()
        w.writerow(bom[0])
    sdir = tmp_path / "schematic"
    sdir.mkdir()
    (sdir / "netlist.net").write_text('(net "SPI_MOSI")\n(net "SPI_MISO")', encoding="utf-8")
    (sdir / "erc_report.json").write_text(json.dumps({"violations": 0}), encoding="utf-8")
    pdir = tmp_path / "pcb"
    pdir.mkdir()
    (pdir / "drc_report.json").write_text(json.dumps({"violations": 0}), encoding="utf-8")
    stackup = {"layers": 6, "er": 4.4, "impedance_targets": [
        {"net_class": "spi_host", "style": "microstrip", "z_target": 50,
         "tolerance_pct": 10, "h_mm": 0.2, "s_mm": 0.15}]}
    (tmp_path / "stackup.yaml").write_text(yaml.safe_dump(stackup), encoding="utf-8")
    sim = tmp_path / "sim"
    sim.mkdir()
    (sim / "irdrop_input.yaml").write_text(yaml.safe_dump(
        {"rails": [{"name": "3V3", "v": 3.3, "current_a": 0.5, "max_drop_pct": 3.0,
                    "cu_mm": 0.035, "sinks": [{"x": 32, "y": 8, "weight": 1}]}]}),
        encoding="utf-8")
    (sim / "thermal_input.yaml").write_text(yaml.safe_dump(
        {"total_power_w": 3.0, "t_amb": 25.0, "tj_limit_c": 85.0,
         "sources": [{"x": 30, "y": 15, "weight": 1}]}), encoding="utf-8")
    (sim / "pdn_input.yaml").write_text(yaml.safe_dump(
        {"rails": [{"name": "3V3", "v": 3.3, "max_transient_a": 1.0, "ripple_pct": 3.0,
                    "decaps": [{"c_f": 100, "esl_nh": 1.0}, {"c_f": 10, "esl_nh": 0.8},
                               {"c_f": 1, "esl_nh": 0.6}] * 2,
                    "probe_freqs_mhz": [1, 10, 100]}],
         "return_path_checks": [{"name": "spi_host", "adjacent_reference_layer": True}]}),
        encoding="utf-8")
    fw = tmp_path / "firmware"
    fw.mkdir()
    (fw / "build_ok.json").write_text(json.dumps(
        {"targets": [{"family": "esp32s31", "build": "ok"}]}), encoding="utf-8")
    (fw / "spi_loopback.json").write_text(json.dumps({"result": "pass"}), encoding="utf-8")
    return tmp_path


def test_all_registered_gates_runnable(proj):
    assert len(all_gates()) >= 12


def test_spec_schema_pass(proj):
    assert run_gate("spec_schema", proj, []).status == GateStatus.PASS


def test_spec_schema_fail_on_missing(proj):
    spec = yaml.safe_load((proj / "spec.yaml").read_text(encoding="utf-8"))
    del spec["power"]
    (proj / "spec.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    r = run_gate("spec_schema", proj, [])
    assert r.status == GateStatus.FAIL


def test_requirement_coverage_fails_when_uncovered(proj):
    spec = yaml.safe_load((proj / "spec.yaml").read_text(encoding="utf-8"))
    spec["requirements"][0]["covered_by"] = None
    (proj / "spec.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    assert run_gate("requirement_coverage", proj, []).status == GateStatus.FAIL


def test_bom_completeness(proj):
    assert run_gate("bom_completeness", proj, []).status == GateStatus.PASS


def test_erc_zero_degraded_pass(proj):
    r = run_gate("erc_zero", proj, [])
    assert r.status in (GateStatus.PASS, GateStatus.BLOCKED)  # kicad-cli dependent


def test_netlist_topology(proj):
    assert run_gate("netlist_topology", proj, []).status == GateStatus.PASS


def test_netlist_topology_catches_missing_net(proj):
    spec = yaml.safe_load((proj / "spec.yaml").read_text(encoding="utf-8"))
    spec["interfaces"][0]["connections"].append("SPI_CS")
    (proj / "spec.yaml").write_text(yaml.safe_dump(spec), encoding="utf-8")
    assert run_gate("netlist_topology", proj, []).status == GateStatus.FAIL


def test_impedance_si_pass(proj):
    r = run_gate("impedance_si", proj, [])
    assert r.status == GateStatus.PASS, r.summary


def test_impedance_si_fails_on_impossible_target(proj):
    stackup = yaml.safe_load((proj / "stackup.yaml").read_text(encoding="utf-8"))
    stackup["impedance_targets"][0]["z_target"] = 5  # unreachable
    stackup["impedance_targets"][0]["tolerance_pct"] = 5
    (proj / "stackup.yaml").write_text(yaml.safe_dump(stackup), encoding="utf-8")
    assert run_gate("impedance_si", proj, []).status == GateStatus.FAIL


def test_irdrop_pass_and_fail(proj):
    assert run_gate("irdrop", proj, []).status in (GateStatus.PASS, GateStatus.FAIL)
    cfg = yaml.safe_load((proj / "sim" / "irdrop_input.yaml").read_text(encoding="utf-8"))
    cfg["rails"][0]["max_drop_pct"] = 0.0  # impossible limit
    (proj / "sim" / "irdrop_input.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    assert run_gate("irdrop", proj, []).status == GateStatus.FAIL


def test_thermal(proj):
    assert run_gate("thermal", proj, []).status == GateStatus.PASS


def test_emc_precompliance(proj):
    r = run_gate("emc_precompliance", proj, [])
    assert r.status == GateStatus.PASS, r.summary


def test_firmware_gates(proj):
    assert run_gate("firmware_build", proj, []).status == GateStatus.PASS
    assert run_gate("firmware_spi_loopback", proj, []).status == GateStatus.PASS
    (proj / "firmware" / "spi_loopback.json").write_text(
        json.dumps({"result": "fail", "detail": "mismatch"}), encoding="utf-8")
    assert run_gate("firmware_spi_loopback", proj, []).status == GateStatus.FAIL
