#!/usr/bin/env python3
"""Run one routing tool on the benchmark boards and score what it returns.

    KICAD_PYTHON=/usr/bin/python3 python3 harness/run.py <tool> [board ...]

tool: freerouting | tscircuit | krt | tracemaker | koppar | koppar_filled | zap

Every tool gets the same input: the routed upstream board with its tracks and
vias and copper zones deleted (placement, keepout areas and rules kept), the board's benchmark project
beside it, and the same wall-clock cap. What it returns is scored by KiCad:

    completionPct = 100 * (1 - unconnected / ratsnest of the unrouted board)
    drc           = kicad-cli pcb drc --severity-error, zones refilled

Results merge by board into results/<CAMPAIGN>/<tool>.json.

Environment: KICAD_PYTHON (a python that imports pcbnew), KICAD_CLI, CAP_S
(default 1200), CAMPAIGN (default 2026-10-placement), and per tool FREEROUTING_JAR,
JAVA, FREEROUTING_THREADS, TRACEMAKER, KOPPAR_API, KOPPAR_API_KEY (an account key, needed for a
board past 300 connections), ZAP_KEY_FILE (a file holding the Zap! API key, default ~/.zap-key;
never on a command line).
"""
import re
from datetime import datetime
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
from common import ROOT, WORK, board_dir, completion, drc, kicad, manifest, place_project  # noqa: E402

CAP_S = int(os.environ.get("CAP_S", "1200"))
CAMPAIGN = os.environ.get("CAMPAIGN", "2026-10-placement")
NODE = os.environ.get("NODE", "node")
JAVA = os.environ.get("JAVA", "java")
FREEROUTING_JAR = os.environ.get("FREEROUTING_JAR", os.path.join(ROOT, "tools", "freerouting-2.5.0.jar"))
FREEROUTING_THREADS = os.environ.get("FREEROUTING_THREADS", "8")
FREEROUTING_MAX_PASSES = 100
TRACEMAKER = os.environ.get("TRACEMAKER", os.path.join(ROOT, "tools", "TraceMaker", "build", "release", "src", "app", "tracemaker"))
KOPPAR_API = os.environ.get("KOPPAR_API", "https://tools.koppar.ai")
ZAP_API = "https://zap.mutual.solutions"
ZAP_KEY_FILE = os.environ.get("ZAP_KEY_FILE", os.path.expanduser("~/.zap-key"))


def default_class(name):
    """The board's Default net class from its benchmark project: what a router is told."""
    with open(os.path.join(board_dir(name), "circuit.kicad_pro")) as f:
        pro = json.load(f)
    c = next(c for c in pro["net_settings"]["classes"] if c.get("name") == "Default")
    return {"clearance": c["clearance"], "track": c["track_width"], "via": c["via_diameter"], "drill": c["via_drill"]}


def run(cmd, timeout_s, log_path, cwd=None):
    t0 = time.time()
    timed_out = False
    import os
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, cwd=cwd, env=env)
        log, rc = (p.stdout or "") + (p.stderr or ""), p.returncode
    except subprocess.TimeoutExpired as e:
        timed_out, rc = True, None
        log = "".join(x.decode() if isinstance(x, bytes) else (x or "") for x in (e.stdout, e.stderr))
    with open(log_path, "w") as f:
        f.write(log)
    return {"seconds": round(time.time() - t0, 1), "timedOut": timed_out, "returncode": rc, "log": log}


def finished_at(tool, out, r):
    """When the tool reached the result it returned (s from its start), from its own log where
    the log says: Freerouting's best-scoring routing pass, TraceMaker's winning variant (that
    variant's end: a variant that completes keeps cleaning up until its time limit, so this is an
    upper bound). A tool that returns when it is done (tscircuit, KiCad Routing Tools) finished
    at its wall time. None when the log does not say."""
    if tool == "freerouting":
        path = os.path.join(out, "freerouting.log")
        if not os.path.exists(path):
            return None
        stamp = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+)")
        lines = open(path, errors="replace").read().splitlines()
        times = [datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f") for m in map(stamp.match, lines) if m]
        if not times:
            return None
        best, best_t = None, None
        for line in lines:
            m = re.search(r"Auto-routing pass #(\d+) .* completed in [\d.]+ seconds with score ([\d.]+)", line)
            t = stamp.match(line)
            if m and t:
                score = float(m.group(2))
                if best is None or score > best:
                    best, best_t = score, datetime.strptime(t.group(1), "%Y-%m-%d %H:%M:%S.%f")
        return round((best_t - times[0]).total_seconds(), 1) if best_t else None
    if tool == "tracemaker":
        path = os.path.join(out, "tracemaker.log")
        if not os.path.exists(path):
            return None
        for line in open(path, errors="replace"):
            m = re.search(r"routed \d+ in ([\d.]+) s\s+<- best", line)
            if m:
                return float(m.group(1))
        return None
    return r.get("seconds")


def freerouting(name, out, unrouted, dsn):
    ses = os.path.join(out, "board.ses")
    r = run([JAVA, "-Djava.awt.headless=true", "-jar", FREEROUTING_JAR, "-de", dsn, "-do", ses,
             "-mp", str(FREEROUTING_MAX_PASSES), "-mt", FREEROUTING_THREADS], CAP_S, os.path.join(out, "freerouting.log"))
    routed = os.path.join(out, "routed.kicad_pcb")
    if os.path.exists(ses):
        kicad("import-ses", unrouted, ses, routed)
    version = os.path.basename(FREEROUTING_JAR).replace("freerouting-", "").replace(".jar", "")
    return routed if os.path.exists(routed) else None, r, {"version": version}


def srj_tool(tool):
    def go(name, out, unrouted, dsn):
        cls = default_class(name)
        inp, outp = os.path.join(out, "input.srj.json"), os.path.join(out, "output.srj.json")
        kicad("srj", unrouted, inp, cls["track"], cls["via"], cls["drill"])
        r = run([NODE, os.path.join(ROOT, "harness", "srj_route.mjs"), tool, inp, outp, str(CAP_S), str(cls["clearance"])],
                CAP_S + 300, os.path.join(out, f"{tool}.log"), cwd=ROOT)
        routed = os.path.join(out, "routed.kicad_pcb")
        extra = {}
        if os.path.exists(outp):
            with open(outp) as f:
                res = json.load(f)
            traces = os.path.join(out, "traces.json")
            with open(traces, "w") as f:
                json.dump(res["traces"], f)
            kicad("apply-traces", unrouted, traces, routed, cls["track"], cls["via"], cls["drill"])
            r["seconds"] = round(res["elapsedMs"] / 1000, 1)
            r["timedOut"] = res["timedOut"]
            extra = {"solved": res["solved"], "failed": res["failed"], "error": res.get("error"), "netByNet": res.get("netByNet", False),
                     "version": res.get("version")}
            if not res["traces"] and (res["timedOut"] or res["failed"]):
                return None, r, extra
        return (routed if os.path.exists(routed) else None), r, extra
    return go


def tracemaker(name, out, unrouted, dsn):
    routed = os.path.join(out, "routed.kicad_pcb")
    place_project(name, unrouted)
    r = run([TRACEMAKER, "route", unrouted, "-o", routed, "--time", str(CAP_S)], CAP_S + 600, os.path.join(out, "tracemaker.log"))
    version = subprocess.run([TRACEMAKER, "--version"], capture_output=True, text=True).stdout.strip()
    return (routed if os.path.exists(routed) else None), r, {"version": version or None}


def koppar(name, out, unrouted, dsn, vias_in_pads="none"):
    """The public route endpoint: the unrouted board and its project in, the routed board out.
    vias_in_pads "none" (the default) keeps every via out of pin pads; "allow" lets vias sit in
    them, for a board ordered with filled and capped vias (the koppar_filled row)."""
    import urllib.request
    with open(unrouted) as f:
        board = f.read()
    with open(os.path.join(board_dir(name), "circuit.kicad_pro")) as f:
        project = f.read()
    req_body = {"content": board, "project": project, "vias_in_pads": vias_in_pads}
    # the project's custom rules file, where it has one: the referee judges by it too
    dru = os.path.join(board_dir(name), "circuit.kicad_dru")
    if os.path.exists(dru):
        with open(dru) as f:
            req_body["rules"] = f.read()
    body = json.dumps(req_body).encode()
    t0 = time.time()
    # a board past the free size (300 connections) routes only with an account API key
    headers = {"content-type": "application/json"}
    if os.environ.get("KOPPAR_API_KEY"):
        headers["authorization"] = f"Bearer {os.environ['KOPPAR_API_KEY']}"
    req = urllib.request.Request(f"{KOPPAR_API}/api/tools/route", data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        job_id = json.load(resp)["job_id"]
    status = {}
    # the service holds the route to the cap itself (a step that would end past it is skipped or
    # undone), so the board it returns is an in-cap one; the extra 600 s is only how long this
    # client keeps polling, never routing time
    while time.time() - t0 < CAP_S + 600:
        time.sleep(10)
        with urllib.request.urlopen(f"{KOPPAR_API}/api/tools/jobs/{job_id}", timeout=60) as resp:
            status = json.load(resp)
        if status.get("status") in ("completed", "failed"):
            break
    r = {"seconds": round(time.time() - t0, 1), "timedOut": status.get("status") not in ("completed", "failed"), "returncode": None, "log": json.dumps(status)}
    with open(os.path.join(out, "koppar.json"), "w") as f:
        json.dump(status, f, indent=2)
    routed = os.path.join(out, "routed.kicad_pcb")
    names = [a.get("name", "") if isinstance(a, dict) else str(a) for a in status.get("artifacts", [])]
    art = "circuit.kicad_pcb" if "circuit.kicad_pcb" in names else next((n for n in names if n.endswith(".kicad_pcb")), None)
    if art:
        with urllib.request.urlopen(f"{KOPPAR_API}/api/tools/jobs/{job_id}/artifact/{art}", timeout=120) as resp, open(routed, "wb") as f:
            shutil.copyfileobj(resp, f)
    return (routed if os.path.exists(routed) else None), r, {"job": job_id}


def zap(name, out, unrouted, dsn):
    """Zap! (Mutual Solutions), its hosted API: the unrouted board and its benchmark project in,
    the routed board out. fab_floor=board: the board's own rules, as every other tool gets (the
    default would hold it to JLC's minimums); everything else at the service defaults (its own
    pours, no vias in pads). minutes = the cap: the time counted is the job's run on the service,
    which leaves out upload and download, so the whole cap is the service's to route in. Timed from the job's start on the service, not its queue. Polled to the cap +
    600 s like Koppar: a board that comes back after the cap (time_budget.stopped "over_time") is
    scored and marked late; a job still running then is cancelled and counts as timed out."""
    import urllib.error
    import urllib.request
    import uuid
    with open(ZAP_KEY_FILE) as f:
        auth = {"Authorization": "Bearer " + f.read().strip()}
    fields = {"fab_floor": "board", "consent_training": "false", "minutes": str(CAP_S // 60)}
    files = {"board": (os.path.basename(unrouted), unrouted), "project": ("circuit.kicad_pro", os.path.join(board_dir(name), "circuit.kicad_pro"))}
    bnd = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += f"--{bnd}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for k, (fn, path) in files.items():
        with open(path, "rb") as f:
            body += f"--{bnd}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fn}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode() + f.read() + b"\r\n"
    body += f"--{bnd}--\r\n".encode()

    def call(path, data=None, method=None):
        req = urllib.request.Request(ZAP_API + path, data=data, method=method, headers={**auth, **({"content-type": f"multipart/form-data; boundary={bnd}"} if data else {})})
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()

    t0 = time.time()
    try:
        job = json.loads(call("/v1/jobs", body))["id"]
    except urllib.error.HTTPError as e:
        # refused at submission (a board the service cannot take): recorded with its reason
        refusal = json.loads(e.read() or b"{}")
        with open(os.path.join(out, "zap.json"), "w") as f:
            json.dump({"status": "refused", "http": e.code, **refusal}, f, indent=2)
        return None, {"seconds": 0.0, "timedOut": False, "returncode": e.code, "log": json.dumps(refusal)}, {"refused": refusal}
    status, started = {}, None
    while True:
        time.sleep(10)
        status = json.loads(call(f"/v1/jobs/{job}"))
        if status.get("status") == "running" and started is None:
            started = time.time()
        if status.get("status") in ("succeeded", "failed", "cancelled"):
            break
        if started is not None and time.time() - started > CAP_S + 600:
            call(f"/v1/jobs/{job}", method="DELETE")
            break
    ended = time.time()
    with open(os.path.join(out, "zap.json"), "w") as f:
        json.dump(status, f, indent=2)
    wall = (status.get("metrics") or {}).get("wall_time_s")
    secs = wall if wall is not None else round(ended - (started or t0), 1)
    r = {"seconds": round(secs, 1), "timedOut": status.get("status") not in ("succeeded", "failed"), "returncode": None, "log": json.dumps(status)}
    routed = os.path.join(out, "routed.kicad_pcb")
    if status.get("status") == "succeeded":
        with open(routed, "wb") as f:
            f.write(call(f"/v1/jobs/{job}/result"))
    stopped = ((status.get("metrics") or {}).get("time_budget") or {}).get("stopped")
    late = status.get("status") == "succeeded" and (secs > CAP_S or stopped == "over_time")
    return (routed if os.path.exists(routed) else None), r, {"job": job, "version": status.get("version"), "queuedS": round((started or ended) - t0, 1),
                                                           "late": late, "zapMetrics": status.get("metrics")}


TOOLS = {"freerouting": freerouting, "tscircuit": srj_tool("tscircuit"), "krt": srj_tool("krt"), "tracemaker": tracemaker, "koppar": koppar,
         "koppar_filled": lambda *a: koppar(*a, vias_in_pads="allow"), "zap": zap}


def fluxpcb(name, out, unrouted, dsn):
    """fluxpcb NCER: congestion-cost-guided rerouting (Koppar-style)."""
    routed = os.path.join(out, "routed.kicad_pcb")
    kicad_py = os.environ.get("KICAD_PYTHON", "python3")
    r = run([kicad_py, os.path.join(ROOT, "tools", "fluxpcb_ncer.py"),
             unrouted, routed, "--timeout", str(CAP_S)],
            CAP_S + 300, os.path.join(out, "fluxpcb.log"))
    extra = {}
    if os.path.exists(routed):
        try:
            with open(os.path.join(out, "fluxpcb.log")) as f:
                for line in f:
                    if line.strip().startswith("{"):
                        extra = json.loads(line.strip())
                        break
        except Exception:
            pass
    # Always return routed path if it exists (even on timeout)
    # The harness will measure completion from the routed board
    return (routed if os.path.exists(routed) else None), r, extra


TOOLS["fluxpcb"] = fluxpcb


def fluxpcb_hybrid(name, out, unrouted, dsn):
    """fluxpcb hybrid: Freerouting backend + skill-package repair operators.

    Routes with the Freerouting backend (fast bulk), then applies the
    pcb-hybrid-routing-advanced repair operators (copper text → silk,
    width cascade, policy waivers, endpoint attach) and re-verifies.
    """
    routed = os.path.join(out, "routed.kicad_pcb")
    kicad_py = os.environ.get("KICAD_PYTHON", "python3")
    runner = os.path.join(
        os.path.dirname(ROOT), "flux-neurotransmitter", "fluxpcb", "bench_runner.py")
    r = run([kicad_py, runner, unrouted, routed,
             "--timeout", str(CAP_S), "--engine", "freerouting"],
            CAP_S + 300, os.path.join(out, "fluxpcb_hybrid.log"))
    extra = {}
    try:
        with open(os.path.join(out, "fluxpcb_hybrid.log")) as f:
            for line in f:
                s = line.strip()
                if s.startswith("{"):
                    extra = json.loads(s)
                    break
    except Exception:
        pass
    return (routed if os.path.exists(routed) else None), r, extra


TOOLS["fluxpcb_hybrid"] = fluxpcb_hybrid


def save(tool, rows):
    dest = os.path.join(ROOT, "results", CAMPAIGN, f"{tool}.json")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    merged = {}
    if os.path.exists(dest):
        with open(dest) as f:
            merged = {b["board"]: b for b in json.load(f)["boards"]}
    merged.update({r["board"]: r for r in rows})
    order = [b["name"] for b in manifest()]
    doc = {"tool": tool, "capS": CAP_S, "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "boards": sorted(merged.values(), key=lambda b: order.index(b["board"]))}
    with open(dest + ".tmp", "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    os.replace(dest + ".tmp", dest)


def main():
    tool = sys.argv[1]
    go = TOOLS[tool]
    picked = sys.argv[2:]
    rows = []
    for b in manifest():
        name = b["name"]
        if picked and name not in picked:
            continue
        ref = os.path.join(board_dir(name), "circuit.reference.kicad_pcb")
        if not os.path.exists(ref):
            raise SystemExit(f"{name}: run scripts/fetch_boards.py first")
        out = os.path.join(WORK, "runs", tool, name)
        shutil.rmtree(out, ignore_errors=True)
        os.makedirs(out)
        print(f"=== {tool} / {name} ({b['layers']}L)", flush=True)
        st = kicad("strip", ref, out)
        place_project(name, st["unrouted"])
        rats = kicad("measure", st["unrouted"])["unconnected"]
        routed, r, extra = go(name, out, st["unrouted"], st["dsn"])
        row = {"board": name, "layers": b["layers"], "ratsnest": rats, "seconds": r["seconds"], "timedOut": r["timedOut"],
               "finishedAtS": finished_at(tool, out, r), **extra}
        if routed:
            place_project(name, routed)
            m = kicad("measure", routed)
            row.update(completionPct=completion(m["unconnected"], rats), unconnected=m["unconnected"], vias=m["vias"], trackMm=m["trackMm"],
                       viasInPinPads=m.get("viasInPinPads"), drc=drc(name, routed))
        else:
            row.update(completionPct=None, unconnected=None, vias=None, trackMm=None, viasInPinPads=None, drc=None)
        print(f"  {json.dumps({k: row[k] for k in ('completionPct', 'vias', 'seconds', 'timedOut')})}", flush=True)
        rows.append(row)
        save(tool, rows)


if __name__ == "__main__":
    main()
