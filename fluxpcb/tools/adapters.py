"""External toolchain adapters with availability detection.

Every adapter follows the same contract:
  available() -> bool
  run(...)    -> dict result (never raises on missing tool; gate reads status)
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


class ToolAdapter:
    name: str = "tool"
    exe: str = ""

    def available(self) -> bool:
        return shutil.which(self.exe) is not None

    def probe(self) -> dict:
        return {"tool": self.name, "available": self.available()}


def _run(cmd: list[str], timeout_s: int = 300) -> dict:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        return {"returncode": p.returncode, "stdout": p.stdout[-4000:],
                "stderr": p.stderr[-4000:]}
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return {"returncode": -1, "stdout": "", "stderr": str(e)}


class KicadCli(ToolAdapter):
    name = "kicad-cli"
    exe = "kicad-cli"

    @staticmethod
    def _parse_count(text: str) -> int:
        """Parse '** ERC/DRC messages: N Errors E Warnings W' tail line."""
        import re
        m = re.findall(r"\*\*\s+\w+\s+messages:\s+(\d+)\s+Errors\s+(\d+)\s+Warnings\s+(\d+)",
                       text)
        if m:
            return int(m[-1][0])
        return len(re.findall(r"^\[\w+\]:", text, re.M))

    def erc(self, sch: Path, out_dir: Path) -> dict:
        """Run KiCad ERC, return parsed violation count."""
        out_dir.mkdir(parents=True, exist_ok=True)
        rpt = out_dir / "erc.rpt"
        r = _run([self.exe, "sch", "erc", "--exit-code-violations", "-o", str(rpt), str(sch)])
        text = rpt.read_text(encoding="utf-8", errors="replace") if rpt.exists() else r["stdout"]
        if r["returncode"] < 0 or ("失败" in r["stderr"] and not text):
            return {"status": "blocked", "error": r["stderr"], "violations": None}
        return {"status": "ok", "violations": self._parse_count(text), "raw": text[-4000:],
                "returncode": r["returncode"]}

    def drc(self, pcb: Path, out_dir: Path) -> dict:
        out_dir.mkdir(parents=True, exist_ok=True)
        rpt = out_dir / "drc.rpt"
        r = _run([self.exe, "pcb", "drc", "--exit-code-violations", "-o", str(rpt), str(pcb)])
        text = rpt.read_text(encoding="utf-8", errors="replace") if rpt.exists() else r["stdout"]
        if r["returncode"] < 0 or ("失败" in r["stderr"] and not text):
            return {"status": "blocked", "error": r["stderr"], "violations": None}
        return {"status": "ok", "violations": self._parse_count(text), "raw": text[-4000:],
                "returncode": r["returncode"]}

    def netlist(self, sch: Path, out_dir: Path) -> Path | None:
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / "netlist.net"
        r = _run([self.exe, "sch", "netlist", "-o", str(out), "--format", "kicadsexpr", str(sch)])
        return out if r["returncode"] == 0 and out.exists() else None


class Ngspice(ToolAdapter):
    name = "ngspice"
    exe = "ngspice"

    def run_deck(self, deck: Path, out_dir: Path) -> dict:
        out_dir.mkdir(parents=True, exist_ok=True)
        raw = out_dir / (deck.stem + ".raw")
        r = _run([self.exe, "-b", "-o", str(out_dir / (deck.stem + ".log")),
                  "-r", str(raw), str(deck)])
        return {"status": "ok" if r["returncode"] == 0 else "fail", **r}


class OpenEMS(ToolAdapter):
    name = "openems"
    exe = "openEMS"

    def run_csx(self, csx_script: Path, out_dir: Path) -> dict:
        out_dir.mkdir(parents=True, exist_ok=True)
        r = _run([self.exe, str(csx_script), str(out_dir / "sim")], timeout_s=7200)
        return {"status": "ok" if r["returncode"] == 0 else "fail", **r}


class Freerouting(ToolAdapter):
    name = "freerouting"
    exe = "freerouting-cli"

    def route(self, dsn: Path, out_dir: Path) -> dict:
        ses = out_dir / (dsn.stem + ".ses")
        r = _run([self.exe, "-de", str(dsn), "-do", str(ses), "-ds", str(out_dir / "status.log")],
                 timeout_s=7200)
        return {"status": "ok" if r["returncode"] == 0 and ses.exists() else "fail",
                "ses": str(ses), **r}


class LCSC(ToolAdapter):
    """LCSC/JLC parts lookup with offline cache (no network needed for cached parts)."""
    name = "lcsc"
    exe = ""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def lookup(self, part_no: str) -> dict | None:
        f = self.cache_dir / f"{part_no.upper().replace('/', '_')}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        return None

    def cache_put(self, part: dict) -> None:
        pn = str(part.get("lcsc", part.get("mpn", "unknown")))
        (self.cache_dir / f"{pn.upper().replace('/', '_')}.json").write_text(
            json.dumps(part, ensure_ascii=False, indent=1), encoding="utf-8")

    def available(self) -> bool:
        return True  # offline cache always usable


kicad_cli = KicadCli()
ngspice = Ngspice()
openems = OpenEMS()
freerouting = Freerouting()


def probe_all(cache_dir: Path | None = None) -> dict:
    tools = [kicad_cli, ngspice, openems, freerouting]
    if cache_dir:
        tools.append(LCSC(cache_dir))
    return {t.name: t.available() for t in tools}
