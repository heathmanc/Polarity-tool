# Pole Position HMI Style Guide

## Controlled palette

The source of truth is `battery_inspector/ui/palette.py`.

| Token | Value | Use |
|---|---:|---|
| `APP_BACKGROUND` | `#D8DCDE` | Main application background |
| `HEADER_BACKGROUND` | `#ECEEEF` | Header and footer |
| `SIDEBAR_BACKGROUND` | `#E4E7E9` | Navigation rail |
| `SURFACE` | `#F7F8F8` | Main panels |
| `SURFACE_ALT` | `#ECEFF0` | Subpanels and grouped controls |
| `SURFACE_STRONG` | `#FFFFFF` | Inputs, tables, high-contrast cards |
| `TEXT` | `#1D2429` | Primary text |
| `TEXT_MUTED` | `#59636A` | Captions and secondary text |
| `BLUE` | `#2F5D7C` | Navigation and operator action |
| `BAD` | `#B42318` | Reject/fault/destructive action |
| `AMBER` | `#9A6700` | Warning/not ready/simulation |
| `GOOD` | `#2F6B3F` | Explicit PASS/validation success |

Do not introduce arbitrary page-level colors. Add a named palette token with a documented purpose when a new visual role is required.

## Surfaces and borders

- Flat light surfaces; no dark theme, gradients, gloss, neon effects, or decorative shadows.
- One-pixel neutral borders define panels and groups.
- Two-pixel borders are reserved for machine-state/result badges and keyboard focus.
- Camera viewports use medium neutral gray so letterboxing is visible without becoming a dark application theme.

## Typography

- Segoe UI is the primary Windows font; Arial is the fallback.
- Page title: 22 px, bold.
- Panel title: 16 px, bold.
- Body: 14 px.
- Captions: 11–13 px, bold where needed.
- Result/state text: 20–42 px depending on hierarchy.
- Avoid all-uppercase prose. Use uppercase for short statuses, controls, and labels.

## Navigation and content density

- Fixed header, left navigation rail, central stacked page, and fixed health footer.
- No visible page scroll bars.
- Tables use fixed row counts and Previous/Next pagination.
- Terminal evidence uses one large terminal page at a time.
- Settings use fixed tabs: General, Camera Device, Camera Image, Camera I/O, Vision / ML, PLC Mode, and PLC Tags.
- Wizard content uses one controlled step at a time.

## ROI conventions

| Overlay | Color/line |
|---|---|
| Battery boundary | Blue, solid |
| Negative terminal search | Graphite, solid |
| Positive terminal search | Blue-gray, solid |
| Marking/classifier ROI | Purple, dashed |
| Auxiliary/orientation feature | Brown-gray, solid or dashed as appropriate |

Each overlay includes a text label. An ROI must remain obvious on both the camera image and a grayscale printout through line style and labeling.

## Status conventions

| State | Treatment |
|---|---|
| Running/Ready, no recent result | Neutral |
| Active acquisition/analysis | Blue-gray informational |
| PASS | Green border/text on pale green surface |
| REJECT | Red border/text on pale red surface |
| NOT READY / simulation / conditional result | Amber border/text on pale amber surface |
| System fault | Red, explicitly labeled `SYSTEM FAULT` or `DEGRADED` |

Healthy camera, PLC, lighting, and disk indicators are neutral. Their failure state is conspicuous red. This prevents an all-green display from masking the one condition that requires attention.

## Scrollbar rule

Primary HMI pages shall not display scroll bars. Use one of these patterns instead:

1. Tabs for configuration groups.
2. Previous/Next pagination for tables and terminal evidence.
3. Stacked wizard pages for guided setup.
4. Fixed-size text fields with concise content.
5. A separate drill-down page when information is too dense.

The stylesheet suppresses scrollbar chrome as a final safeguard, but layout design—not hidden overflow—is the primary control.
