# OpenFOAM chtMultiRegionFoam case skeleton (auto-generated)

solid region: board (k=0.3 W/mK, copper planes smeared 35um => effective kx=ky~30, kz~0.3); heat sources per footprint from sim/thermal_input.yaml.
fluid region: air enclosure, natural convection (Boussinesq).
Run: blockMesh; topoSet; chtMultiRegionFoam (or foamRun -solver chtMultiRegionFoam in ESI v23+)
Alternative: Elmer StatCurrentSolver+HeatSolver, see gates/thermal_cfd.
