# Polarity Tool v0.9.0 Release Notes

## Purpose

v0.9.0 replaces the original dark visual treatment with a light, ISA-101-aligned high-performance HMI and removes visible page scroll bars. The inspection engine and v0.8.1 terminal-top decision logic are unchanged.

## HMI changes

- Light neutral operating theme with flat panels and high-contrast dark text.
- Sparse semantic color: red for reject/fault, amber for warning/not-ready/simulation, green for explicit completed PASS, and blue-gray for navigation/action.
- Healthy equipment and steady-state operation are neutral rather than continuously green.
- Physical positive/negative terminal identities use non-alarm role colors.
- Natural-color camera imagery remains unchanged.
- Active inspection states use informational blue-gray instead of warning amber.
- Product rejects use red and remain distinct from station faults through explicit wording.

## Scrollbar-free navigation

- Recipe and event tables use fixed-height pagination.
- Inspection Detail presents one large terminal evidence page at a time with Previous/Next controls.
- Settings are divided into fixed tabs for General, Camera Device, Camera Image, Camera I/O, PLC Mode, and PLC Tags.
- Recipe creation remains a fixed-step wizard.
- QTextEdit and table scrollbars are disabled where the content is intentionally bounded.
- The global stylesheet suppresses residual scrollbar chrome as a safeguard.

## Documentation and controls

- Added `HMI_PHILOSOPHY.md`.
- Added `HMI_STYLE_GUIDE.md`.
- Updated the controlled UI contract and light-theme reference image.
- Added static tests that prevent dark-theme colors, QScrollArea reintroduction, and unpaginated primary tables.

## Compliance statement

The release is an ISA-101-aligned implementation foundation, not a formal certification. Site acceptance still requires review against the facility's approved HMI philosophy, user roles, alarm philosophy, cybersecurity and operating procedures, and deployed-monitor usability testing.

## Upgrade effect

This release changes presentation and navigation only. It does not invalidate v0.8.1 recipe reference images, inspection-engine validation evidence, classifier thresholds, PLC tags, or camera configuration.
