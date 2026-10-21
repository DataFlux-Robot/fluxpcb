"""Zero-dependency local server for the Grounding Map dashboard.

Serves a self-contained HTML page plus a small JSON API over the repo:

  GET /                     dashboard page
  GET /api/projects         [{name, released, finished}] for projects/*/
  GET /api/report?p=<name>  latest.json of a project
  GET /api/events?p=<name>&after=N   events.jsonl entries after line N
  GET /api/grounding?p=<name>        requirement -> gate -> artifact evidence map

Binds 127.0.0.1 only; no external dependencies, no CDN (works offline).
"""
from __future__ import annotations

import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

STATIC_DIR = Path(__file__).resolve().parent / "static"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _list_projects(projects_root: Path) -> list[dict]:
    out = []
    if not projects_root.is_dir():
        return out
    for d in sorted(projects_root.iterdir()):
        if not d.is_dir() or not (d / "project.yaml").exists():
            continue
        rep = _read_json(d / "reports" / "latest.json")
        # a run is live when events exist without a closing run_end
        live = False
        evf = d / "reports" / "events.jsonl"
        if evf.exists():
            try:
                lines = [l for l in evf.read_text(encoding="utf-8").splitlines() if l.strip()]
                if lines and json.loads(lines[-1]).get("ev") not in ("run_end", "verify_start"):
                    live = True
            except (OSError, ValueError):
                pass
        out.append({
            "name": d.name,
            "released": bool(rep and rep.get("released")),
            "finished": (rep or {}).get("finished"),
            "has_report": rep is not None,
            "live": live,
        })
    return out


def _grounding(project_dir: Path) -> dict:
    """Requirement -> covering design elements -> gate/artifact evidence."""
    spec = None
    for cand in ("spec.yaml", "project.yaml"):
        p = project_dir / cand
        if p.exists():
            try:
                import yaml
                spec = yaml.safe_load(p.read_text(encoding="utf-8"))
                break
            except Exception:
                spec = None
    reqs = (spec or {}).get("requirements", []) if isinstance(spec, dict) else []

    rep = _read_json(project_dir / "reports" / "latest.json") or {}
    stage_by_gate: dict[str, dict] = {}
    for s in rep.get("stages", []):
        for g in s.get("gates", []):
            stage_by_gate[g["name"]] = {"stage": s.get("title", s["stage"]), **g}

    nodes = []
    import re
    for r in reqs:
        rid = r.get("id", "?")
        covered = r.get("covered_by", []) or []
        ev: list[dict] = []
        for c in covered:
            key = str(c)
            # map design element -> most relevant gate
            gate_name = {
                "bom": "bom_completeness", "component": "bom_completeness",
                "schematic": "erc_zero", "netlist": "netlist_topology",
                "pcb": "drc_zero", "layout": "drc_zero", "constraints": "drc_zero",
                "zones": "drc_zero", "stackup": "impedance_si", "impedance": "impedance_si",
                "thermal": "thermal", "irdrop": "irdrop", "power": "irdrop",
                "emc": "emc_precompliance", "firmware": "firmware_build",
                "software": "firmware_build",
            }.get(key.lower())
            if gate_name is None and re.fullmatch(r"[A-Z]{1,3}\d+.*", key):
                gate_name = "erc_zero"  # 器件位号(U1/J4/SW1): 原理图连接性门禁覆盖
            g = stage_by_gate.get(gate_name) if gate_name else None
            ev.append({"element": key,
                       "gate": gate_name or "—",
                       "status": (g or {}).get("status", "design"),
                       "summary": (g or {}).get("summary", "")})
        nodes.append({"id": rid, "text": r.get("text", ""), "priority": r.get("priority", ""),
                      "evidence": ev})
    return {"requirements": nodes,
            "uncovered": [n["id"] for n in nodes if not n["evidence"]]}


class _Handler(BaseHTTPRequestHandler):
    server_version = "FluxPCB-Dashboard/1.0"
    projects_root: Path = None  # injected

    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _project_dir(self, qs: dict) -> Path:
        name = (qs.get("p") or [""])[0]
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            name = ""
        d = (self.projects_root / name) if name else self.projects_root
        return d

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)

        if u.path == "/" or u.path == "/index.html":
            body = (STATIC_DIR / "index.html").read_bytes()
            self._send(200, body, "text/html; charset=utf-8")
            return

        if u.path == "/api/projects":
            self._json({"now": time.time(), "projects": _list_projects(self.projects_root)})
            return

        if u.path == "/api/report":
            rep = _read_json(self._project_dir(qs) / "reports" / "latest.json")
            self._json(rep if rep is not None else {"error": "no report"}, 200 if rep else 404)
            return

        if u.path == "/api/events":
            d = self._project_dir(qs)
            after = int((qs.get("after") or ["0"])[0])
            out, n = [], 0
            evf = d / "reports" / "events.jsonl"
            if evf.exists():
                for line in evf.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    n += 1
                    if n > after:
                        try:
                            out.append(json.loads(line))
                        except ValueError:
                            pass
            self._json({"cursor": n, "events": out[-300:]})
            return

        if u.path == "/api/grounding":
            self._json(_grounding(self._project_dir(qs)))
            return

        if u.path == "/api/render":
            # list gallery renders for a project: gallery/<name>/renders/* or gallery/<name>/*
            g = self.projects_root.parent / "gallery" / (qs.get("p") or [""])[0]
            imgs = []
            if g.is_dir():
                for sub in (g / "renders", g):
                    if sub.is_dir():
                        imgs += [str(f.relative_to(g)).replace("\\", "/")
                                 for f in sorted(sub.iterdir())
                                 if f.suffix.lower() in (".png", ".svg", ".jpg")]
                        if imgs:
                            break
            self._json({"renders": imgs})
            return

        if u.path == "/render":
            # serve one gallery image (path-safe, under gallery/<project>/)
            rel = (qs.get("f") or [""])[0]
            proj = (qs.get("p") or [""])[0]
            g = (self.projects_root.parent / "gallery").resolve()
            target = (g / proj / rel).resolve()
            ctype = {".png": "image/png", ".svg": "image/svg+xml",
                     ".jpg": "image/jpeg"}.get(target.suffix.lower())
            if (ctype and target.is_file() and str(target).startswith(str(g))):
                self._send(200, target.read_bytes(), ctype)
            else:
                self._send(404, b"not found", "text/plain")
            return

        self._send(404, b"not found", "text/plain")


def serve(projects_root: Path, port: int = 8765, open_browser: bool = True) -> None:
    handler = type("BoundHandler", (_Handler,), {"projects_root": projects_root.resolve()})
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{port}"
    print(f"[flux] Grounding Map dashboard -> {url}  (projects root: {projects_root})")
    print("[flux] Ctrl+C 停止")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[flux] dashboard stopped")
