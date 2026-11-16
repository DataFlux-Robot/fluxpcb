from pathlib import Path

for f in ("fluxpcb/tools/dcsolve.py", "fluxpcb/tools/cavity.py"):
    p = Path(f)
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    out = []
    for ln in lines:
        if "gr_rect" in ln:
            ln = ln.replace('[^\\n]*\\n\\s*', '[\\s\\S]{0,120}?')
        if ln.strip().startswith('r"\\n\\s*\\(end'):
            ln = ln.replace('r"\\n\\s*\\(end', 'r"[\\s\\S]{0,120}?\\(end')
        out.append(ln)
    p.write_text("".join(out), encoding="utf-8")
    print(f, "patched")
