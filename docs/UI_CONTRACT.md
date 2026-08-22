# UI Contract

The file `battery_inspector/assets/ui_reference.png` is the approved light-theme visual reference. The composition remains based on the operator-approved Pole Position layout while the coloration follows the project HMI philosophy.

## Fixed composition

1. Top header with brand, authoritative machine state, active recipe, part/pass/fail/reject counters, and current user.
2. Left navigation rail: Overview, Inspection, Recipes, ML Training, Diagnostics, Events, Settings, Logout.
3. Central stacked page area.
4. Bottom health strip: camera, lighting, PLC, disk, aggregate system status, current user, and Help.

Overview provides a confirmed **RESET PRODUCTION COUNTERS** action. It clears
only session yield totals and the recent-result strip, is disabled while the
station is busy, and never deletes inspection evidence or configuration assets.

## Visual rules

- Light neutral gray backgrounds dominate normal operation. There is no dark theme.
- Saturated color is sparse and semantic: red reject/fault, amber warning/not-ready/simulation, green explicit completed PASS, blue-gray navigation/action.
- Healthy equipment is neutral rather than green.
- The natural-color camera image remains visible because it carries process information.
- Product rejects and system faults are separate concepts and use explicit text.
- Every inspection detail shows the terminal search image and the exact marking ROI/crop or diagnostic image.
- Physical positive/negative terminal identity uses non-alarm role colors.
- Primary pages display no scroll bars. Tabs, pagination, stacked detail cards, and wizard pages are used instead.
- Technician pages use plain process language. ML Training exposes capture/label/train/deploy actions and bounded training parameters, while JSON, model internals, and PLC data types remain engineering-level concerns.

## Target resolution

Primary design target: 1920 x 1080. Qt layouts remain responsive down to 1280 x 760. Final acceptance shall be performed at the exact deployed monitor resolution, Windows scaling, touch configuration, and viewing distance.

See `HMI_PHILOSOPHY.md` and `HMI_STYLE_GUIDE.md` for the controlled design rules.
