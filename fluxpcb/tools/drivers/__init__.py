"""Drivers: one small script per subprocess tool.

    python driver.py <board.kicad_pcb> <args.json>

prints one `__TOOL__{...}` line (fluxpcb.tools.call.emit_line) and exits.
The meta process stays pcbnew-free; a driver crash is an error envelope.
"""
