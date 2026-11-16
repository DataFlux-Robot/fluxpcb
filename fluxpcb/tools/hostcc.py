"""Host C compiler access for firmware regression gates.

Prefers a native gcc/clang on PATH; falls back to MSVC (common on Windows
dev boxes with Visual Studio) with a constructed INCLUDE/LIB environment.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

_MSVC_GLOBS = [
    r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Tools\MSVC\*",
    r"C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC\*",
]
_SDK_ROOT = r"C:\Program Files (x86)\Windows Kits\10"


def find_hostcc() -> dict | None:
    """Return {'kind': 'gcc'|'msvc', 'exe': path, 'env': {...}} or None."""
    import shutil
    for exe in ("gcc", "cc", "clang"):
        p = shutil.which(exe)
        if p:
            return {"kind": "gcc", "exe": p, "env": {}}

    import glob
    for pattern in _MSVC_GLOBS:
        for msvc in sorted(glob.glob(pattern), reverse=True):
            cl = Path(msvc) / "bin" / "HostX64" / "x64" / "cl.exe"
            if not cl.exists():
                continue
            inc = [str(Path(msvc) / "include")]
            libs = [str(Path(msvc) / "lib" / "x64")]
            for sdk_inc in sorted(glob.glob(_SDK_ROOT + r"\Include\*"), reverse=True):
                inc += [str(Path(sdk_inc) / "ucrt"),
                        str(Path(sdk_inc) / "um"),
                        str(Path(sdk_inc) / "shared")]
                sdk_lib = _SDK_ROOT + r"\Lib\\" + Path(sdk_inc).name
                libs += [str(Path(sdk_lib) / "ucrt" / "x64"),
                         str(Path(sdk_lib) / "um" / "x64")]
                break
            if len(inc) < 2:
                continue
            return {"kind": "msvc", "exe": str(cl),
                    "env": {"INCLUDE": ";".join(inc), "LIB": ";".join(libs)}}
    return None


def compile_and_run(sources: list[Path], workdir: Path,
                    exe_name: str = "regression") -> dict:
    """Compile C sources and run the binary. Never raises."""
    cc = find_hostcc()
    if cc is None:
        return {"status": "blocked", "error": "no host C compiler (gcc/MSVC)"}
    workdir.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **cc["env"]}
    exe = workdir.resolve() / (exe_name + ".exe")
    srcs = [str(s.resolve()) for s in sources]
    if cc["kind"] == "gcc":
        cmd = [cc["exe"], "-O1", "-Wall", "-o", str(exe), *srcs]
    else:
        cmd = [cc["exe"], "-nologo", "-Fe:" + str(exe), *srcs]
    p = subprocess.run(cmd, capture_output=True, timeout=300,
                       env=env, cwd=str(workdir),
                       encoding="utf-8", errors="replace")
    if p.returncode != 0 or not exe.exists():
        return {"status": "compile_fail",
                "error": (p.stdout + p.stderr)[-800:]}
    r = subprocess.run([str(exe)], capture_output=True, timeout=120,
                       cwd=str(workdir), encoding="utf-8", errors="replace")
    return {"status": "ok" if r.returncode == 0 else "run_fail",
            "returncode": r.returncode, "output": (r.stdout + r.stderr)[-2000:],
            "compiler": cc["kind"]}
