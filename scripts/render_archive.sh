#!/bin/bash
# Renders every archived board: 3D top/bottom PNG and a 2D copper SVG (all copper layers +
# outline) plus one SVG per copper layer. Zones are filled in a temporary copy so pours show
# as fabricated; the archived file stays as the tool returned it.
#   render_archive.sh <archive dir> [tool ...]
A=$1; shift
TOOLS=${*:-human koppar tracemaker freerouting tscircuit krt zap}
for t in $TOOLS; do
  for d in "$A"/$t/*/; do
    b=$(basename "$d"); pcb="$d/$b.kicad_pcb"
    [ -f "$pcb" ] || continue
    out="$d/render"; [ -f "$out/top.png" ] && continue
    mkdir -p "$out"; tmp=$(mktemp -d); cp "$pcb" "$tmp/b.kicad_pcb"; cp "$d/$b.kicad_pro" "$tmp/b.kicad_pro" 2>/dev/null
    /usr/bin/python3 - "$tmp/b.kicad_pcb" <<'PY' >/dev/null 2>&1
import sys, pcbnew
b = pcbnew.LoadBoard(sys.argv[1]); pcbnew.ZONE_FILLER(b).Fill(b.Zones()); b.Save(sys.argv[1])
PY
    layers=$(grep -oE '\((0|[0-9]+) "(F|B|In[0-9]+)\.Cu"' "$tmp/b.kicad_pcb" | grep -oE '"[^"]+"' | tr -d '"' | awk '!s[$0]++' | paste -sd, -)
    kicad-cli pcb render --side top --width 2400 --height 1600 --background opaque -o "$out/top.png" "$tmp/b.kicad_pcb" >/dev/null 2>&1
    kicad-cli pcb render --side bottom --width 2400 --height 1600 --background opaque -o "$out/bottom.png" "$tmp/b.kicad_pcb" >/dev/null 2>&1
    kicad-cli pcb export svg --mode-single --page-size-mode 2 --exclude-drawing-sheet -l "$layers,Edge.Cuts" -o "$out/copper.svg" "$tmp/b.kicad_pcb" >/dev/null 2>&1
    for l in ${layers//,/ }; do
      kicad-cli pcb export svg --mode-single --page-size-mode 2 --exclude-drawing-sheet -l "$l,Edge.Cuts" -o "$out/$l.svg" "$tmp/b.kicad_pcb" >/dev/null 2>&1
    done
    rm -rf "$tmp"
    echo "$t/$b: $(ls "$out" | wc -l) files"
  done
done
