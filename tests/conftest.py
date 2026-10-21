import os

# keep the suite fast: pcb_layout routes with the in-process L-router only
os.environ.setdefault("FLUXNT_ROUTE_LIGHT", "1")
