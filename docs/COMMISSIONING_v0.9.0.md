# Commissioning Polarity Tool v0.9.0

## Display acceptance

Commission at the target monitor resolution and Windows scaling. The primary target is 1920 x 1080; the supported lower bound is 1280 x 760.

Verify each page without using a mouse wheel:

1. Overview — all controls, acquired image, result, and PLC commissioning strip are visible.
2. Inspection Detail — terminal evidence is legible; Previous/Next changes terminal pages.
3. Recipes — ten rows fit; Previous/Next changes pages; selected-recipe details remain visible.
4. Diagnostics — all four panels fit without scrolling.
5. Events — fourteen rows fit; Previous/Next changes pages.
6. Settings — all six tabs are selectable without tab scroll buttons; each tab fits.
7. Recipe wizard — every step fits; Back/Next/Cancel are always visible.

## Color acceptance

- Normal steady state is neutral gray/white, not green and not dark.
- Active acquisition is blue-gray informational.
- PLC Simulation and Not Ready are amber.
- PASS is green.
- REJECT and system fault are red and have different text labels.
- Healthy camera, lighting, PLC, disk, and system indicators are neutral.
- ROI labels and line styles remain readable over representative battery images.

## Touch and keyboard acceptance

- All production buttons can be operated by touch without relying on hover.
- Keyboard focus is visible.
- F11 toggles full-screen mode.
- No critical content is clipped at the deployed scaling.

## Inspection regression

Run the existing v0.8.1 vision and terminal-top smoke tests. v0.9.0 does not modify the inspection engine, so results must remain identical.
