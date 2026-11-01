# -*- coding: utf-8 -*-
"""证书库回归(方案④): VDDA 类假增益的语义级终结 + 证书核红绿.

场景复现: stale pairs 快照把已闭合的对当目标, '收割'后宣称闭合 ——
证书核以**实时测量对账**拒绝该声明, 假增益在语义层无法存活。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fluxpcb.kernel import proofs              # noqa: E402


class TestPredicates:
    def test_coords_rounded(self):
        assert proofs.coords_emitted_rounded(
            [[28.4, 20.0, 2], [7.495, 7.4, 0]])
        assert not proofs.coords_emitted_rounded(
            [[28.40049, 20.0, 2]])

    def test_ball_key(self):
        assert proofs.ball_key_wellformed("D12")
        assert proofs.ball_key_wellformed("0,10")
        assert not proofs.ball_key_wellformed("?")

    def test_pairs_col(self):
        assert proofs.pairs_col_wellformed(
            "SDMMC1_CMD|28.4|20.0|0|7.495|7.4|0")
        assert not proofs.pairs_col_wellformed("net|1|2|3")

    def test_rules_ref(self):
        assert proofs.rules_ref_not_per_pair("kicad_dru:clearance")
        assert not proofs.rules_ref_not_per_pair("flux_waiver_A-B_0p14")


class TestClosureCert:
    LIVE = ["SDMMC1_CMD|28.4|20.0|0|7.495|7.4|0",
            "UART8_RX|30.0|26.4|0|35.5|47.54|0"]

    def _cert(self, **kw):
        base = {
            "net": "VDDA",
            "path_pts": [[8.725, 37.25, 0], [10.25, 36.225, 0]],
            "endpoint_attach": [
                {"endpoint": 0, "mode": "attach-via", "via_ok": True},
                {"endpoint": -1, "mode": "same-layer"}],
            "rule_table_ref": "kernel.rules:clearance",
            "measured_after": {"islands_after": 1, "drawn": 3},
            "closes_pair": "8.725",
        }
        base.update(kw)
        return base

    def test_good_cert_passes(self):
        ok, why = proofs.closure_cert_ok(self._cert(), self.LIVE, 6)
        assert ok, why

    def test_stale_cert_rejected(self):
        """VDDA 案: 声称闭合的对仍在实时开路集 -> 作废。"""
        cert = self._cert(
            net="GND",
            closes_pair="17.7625|20.0",
            measured_after={"islands_after": 1})
        live = ["GND|17.7625|20.0|0|17.7625|19.5|0"]
        ok, why = proofs.closure_cert_ok(cert, live, 6)
        assert not ok
        assert any("stale-cert" in r for r in why)

    def test_unrounded_path_rejected(self):
        ok, why = proofs.closure_cert_ok(
            self._cert(path_pts=[[28.40049, 20.0, 2]]), self.LIVE, 6)
        assert not ok

    def test_not_truly_closed_rejected(self):
        ok, why = proofs.closure_cert_ok(
            self._cert(measured_after={"islands_after": 3}),
            self.LIVE, 6)
        assert not ok
        assert any("未真闭合" in r for r in why)

    def test_no_measurement_rejected(self):
        ok, why = proofs.closure_cert_ok(
            self._cert(measured_after={}), self.LIVE, 6)
        assert not ok


class TestWaiverCert:
    def test_good_waiver_cert(self):
        ok, why = proofs.waiver_cert_ok({
            "policy_floor_mm": 0.12, "probe": "PROBE_OK",
            "rule_names": ["flux_policy_A-B_0p1308"],
            "audit": {"source": "fab_close"}})
        assert ok, why

    def test_floor_lowered_rejected(self):
        """地板被调低凑数 -> 拒。"""
        ok, why = proofs.waiver_cert_ok({
            "policy_floor_mm": 0.1, "probe": "PROBE_OK",
            "rule_names": ["flux_policy_A-B_0p1308"],
            "audit": {"source": "fab_close"}})
        assert not ok
        assert any("地板" in r for r in why)

    def test_probe_failed_rejected(self):
        ok, why = proofs.waiver_cert_ok({
            "policy_floor_mm": 0.12, "probe": "PROBE_FAILED",
            "rule_names": ["flux_policy_A-B_0p1308"],
            "audit": {"source": "fab_close"}})
        assert not ok

    def test_no_audit_rejected(self):
        ok, why = proofs.waiver_cert_ok({
            "policy_floor_mm": 0.12, "probe": "PROBE_OK",
            "rule_names": ["flux_policy_A-B_0p1308"], "audit": {}})
        assert not ok
