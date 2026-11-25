"""One release manifest: what was built, from what, with which tools.

Today the evidence is split across three files that never meet: the run
report's `input_hash`, `fab_package.json` (waivers + truthnet), and the
exported files themselves.  A factory (or a reader six months later) cannot
tell from the package which source hash it came from, which kicad-cli wrote
the gerbers, or what exceptions were accepted.

This module builds `fab/release_manifest.json`:

    schema      fluxpcb-release-1
    input_hash  the run's input fingerprint (board + project + rules + stackup)
    tools       kicad-cli / python / freerouting / ngspice versions
    artifacts   every file in the package with sha256 and kind
    fab         gerber / drill / pos / bom / cpl / step file names
    accepted_exceptions   waivers and truthnet from fab_package.json
    unresolved  what the run itself said is still open
    created

The manifest is itself hashed into the run report, so "released" means the
package and its description travelled together.

    build(project_dir, run=None, fab_dir=None) -> dict
    write(project_dir, manifest) -> path
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import time

SCHEMA = 'fluxpcb-release-1'


def _sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def _run(cmd, timeout=20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout)
        return ((r.stdout or '') + (r.stderr or '')).strip().splitlines()[0][:120]
    except Exception:
        return ''


def tool_versions():
    """Best effort, never raises: 'unknown' is a legitimate answer."""
    out = {'python': platform.python_version(),
           'platform': platform.platform()[:80]}
    cli = shutil.which('kicad-cli')
    out['kicad_cli'] = _run([cli, '--version']) if cli else 'absent'
    ng = shutil.which('ngspice')
    out['ngspice'] = _run([ng, '-v']) if ng else 'absent'
    jar = os.environ.get('FLUXPCB_FREEROUTING_JAR') or ''
    java = shutil.which('java')
    if jar and os.path.exists(jar) and java:
        out['freerouting'] = _run([java, '-jar', jar, '--version'], timeout=25) \
            or f'jar {os.path.basename(jar)}'
    else:
        out['freerouting'] = 'absent'
    return out


KIND_BY_EXT = {'.gbr': 'gerber', '.gtl': 'gerber', '.gbl': 'gerber',
               '.gts': 'gerber', '.gbs': 'gerber', '.gto': 'gerber',
               '.gbo': 'gerber', '.gtp': 'gerber', '.gbp': 'gerber',
               '.gm1': 'outline', '.gko': 'outline',
               '.drl': 'drill', '.csv': 'csv', '.step': 'step',
               '.stp': 'step', '.json': 'json'}


def _kind(name):
    return KIND_BY_EXT.get(os.path.splitext(name)[1].lower(), 'other')


def build(project_dir, run=None, fab_dir=None, unresolved=None):
    project_dir = str(project_dir)
    fab_dir = fab_dir or os.path.join(project_dir, 'pcb', 'fab')
    artifacts, fab = [], {}
    for root, _dirs, files in os.walk(fab_dir):
        for name in sorted(files):
            if name == 'release_manifest.json':
                continue
            p = os.path.join(root, name)
            rel = os.path.relpath(p, project_dir)
            kind = _kind(name)
            artifacts.append({'path': rel, 'sha256': _sha(p),
                              'bytes': os.path.getsize(p), 'kind': kind})
            fab.setdefault(kind, []).append(name)
    for name in ('bom.json',):
        p = os.path.join(project_dir, name)
        if os.path.exists(p):
            artifacts.append({'path': name, 'sha256': _sha(p),
                              'bytes': os.path.getsize(p), 'kind': 'bom'})
    pcbs = []
    pdir = os.path.join(project_dir, 'pcb')
    if os.path.isdir(pdir):
        for root, _d, files in os.walk(pdir):
            for name in files:
                if name.endswith('.kicad_pcb'):
                    p = os.path.join(root, name)
                    rel = os.path.relpath(p, project_dir)
                    artifacts.append({'path': rel, 'sha256': _sha(p),
                                      'bytes': os.path.getsize(p),
                                      'kind': 'board'})
                    pcbs.append(rel)
    exceptions = {}
    fpk = os.path.join(project_dir, 'pcb', 'fab_package.json')
    if not os.path.exists(fpk):
        fpk = os.path.join(project_dir, 'fab_package.json')
    if os.path.exists(fpk):
        try:
            d = json.load(open(fpk, encoding='utf-8'))
            exceptions = {k: d.get(k) for k in
                          ('waivers', 'truthnet', 'fab_export') if k in d}
        except Exception as e:
            exceptions = {'error': f'unreadable fab_package.json: {e}'}
    man = {
        'schema': SCHEMA,
        'project': os.path.basename(os.path.normpath(project_dir)),
        'created': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'input_hash': getattr(run, 'input_hash', '') if run is not None else '',
        'tools': tool_versions(),
        'boards': pcbs,
        'fab': {k: v for k, v in fab.items()},
        'artifacts': artifacts,
        'n_files': len(artifacts),
        'accepted_exceptions': exceptions,
        'unresolved': list(unresolved or []),
    }
    return man


def write(project_dir, manifest, fab_dir=None):
    fab_dir = fab_dir or os.path.join(str(project_dir), 'pcb', 'fab')
    os.makedirs(fab_dir, exist_ok=True)
    p = os.path.join(fab_dir, 'release_manifest.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=1, ensure_ascii=False)
    return p


def stamp(project_dir, run=None, fab_dir=None, unresolved=None):
    """build + write + return the pointer the run report keeps."""
    m = build(project_dir, run=run, fab_dir=fab_dir, unresolved=unresolved)
    p = write(project_dir, m, fab_dir=fab_dir)
    return {'path': os.path.relpath(p, str(project_dir)),
            'sha256': _sha(p), 'schema': SCHEMA,
            'n_files': m['n_files'], 'tools': m['tools']}
