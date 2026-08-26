# Pole Position HMI Philosophy

## Purpose

This document defines the operating philosophy for the Pole Position human-machine interface. It is the project-level basis for an ISA-101-aligned, high-performance HMI. It applies to the operator display, inspection detail, recipe workflow, guided ML training, diagnostics, events, and engineering settings.

This implementation is aligned with ISA-101 principles; formal site compliance requires review against the site's approved HMI philosophy, user roles, alarm philosophy, cybersecurity requirements, operating procedures, and acceptance testing.

## Human-performance goals

The HMI shall help an operator or maintenance technician answer these questions without interpretation:

1. Is the station ready to inspect?
2. Which recipe is active?
3. Was the last product accepted or rejected?
4. Why was it rejected or not inspected?
5. What image and exact regions were evaluated?
6. Is the condition a product-quality result or a station/system fault?
7. What action is permitted for the current user role?

## Display hierarchy

### Level 1 — Station overview

The Overview page is the normal operating display. It shows the machine state, the recipe that graded the last part, session production counts, last acquired image, result, reason, cycle/frame identity, recent session results, PLC commissioning state, and system-health summary. Counts reset at startup so PASS history is not persisted indirectly.

### Level 2 — Inspection detail

Inspection Detail shows one terminal at a time at a useful scale. It includes the terminal-search image, marking crop or diagnostic image, expected and detected marking, confidence, terminal-top lock, ring result, and evidence controls. Previous/Next navigation replaces vertical scrolling.

### Level 3 — Recipe and diagnostics

Recipes, Diagnostics, and Events provide maintenance information. Recipe tables and event history use fixed-height pages with explicit pagination. Diagnostics use a fixed grid and tabs rather than a long scrolling page.

### Level 4 — Configuration and guided ML training

Settings, the recipe wizard, and ML Training are controlled technician/engineering workflows. They use fixed pages, tabs, and explicit Back/Next actions. Low-level technicians do not edit JSON, model internals, coordinates, or PLC data types directly.

## Color philosophy

Normal operation is light, neutral, and low-chroma. Natural-color camera images remain natural because they carry process information.

Color is used only where it adds meaning:

| Color role | Meaning |
|---|---|
| Neutral gray/black | Normal operation, labels, healthy equipment, inactive controls |
| Blue-gray | Navigation, selected page, operator action, active non-alarm operation |
| Red | Product reject, failed check, system fault, destructive action |
| Amber | Warning, not ready, simulation/commissioning, conditional acceptance, attention required |
| Green | Explicit completed PASS or successful validation only |

Physical terminal roles do not use alarm red/pass green. Negative terminal identity is neutral graphite; positive terminal identity is blue-gray. Marking and battery ROIs use dedicated non-alarm colors and line styles.

Every colored state also includes text, shape, or an icon. Color alone never communicates the result.

## Interaction philosophy

- The application is designed for 1920 x 1080 and remains usable at 1280 x 760.
- Primary HMI pages, including ML Training, shall not display horizontal or vertical scroll bars.
- When information cannot fit, the interface uses tabs, pagination, a stacked detail page, or a guided wizard step.
- Buttons use plain action language: `RUN MANUAL INSPECTION`, `CAPTURE NEW REFERENCE`, `USE THIS IMAGE`, `SEND ONE TEST TRIGGER`, and `ACTIVATE SELECTED REVISION`.
- Destructive actions require clear wording and confirmation.
- Disabled controls remain visible so the operator can understand that the function exists but is unavailable.
- Focus borders are visible for keyboard operation.
- Text and controls are sized for an industrial touchscreen; hover behavior is supplementary, not required.

## State and result philosophy

The top machine-state banner is authoritative and derives from the controller state machine.

Normal active states such as `ACQUIRING`, `LOCATING`, `INSPECTING`, and `SAVING` are blue-gray informational states, not alarms. `NOT READY` is amber. A system fault is red. A completed product PASS is green; a product REJECT is red.

A product reject is not a machine alarm. The HMI records it, explains it, updates product counters, and returns to Ready. Camera acquisition failure, invalid recipe, model not ready, PLC communications failure, and internal exceptions are station conditions and are presented separately.

## Recipe philosophy

Recipe editing always creates an immutable new revision. The technician is guided through:

1. Capture or explicitly retain a reference image.
2. Identify the recipe.
3. Confirm the battery boundary and orientation reference.
4. Teach terminal-search and marking ROIs.
5. Define expected polarity and ring requirements.
6. Validate multiple fresh physical samples.
7. Save as draft or activate when qualification is complete.

The wizard shall keep the exact image and ROI visible. It shall block production activation when the reference, locator, classifier, or validation evidence is incomplete.

## Alarm and event philosophy

The current application separates product outcomes from system conditions. Site deployment should map system faults to the site's ISA-18.2 alarm philosophy where operator response is required. Events that do not require immediate operator action belong in the event/audit trail rather than being promoted to alarms.

## Lifecycle and change control

HMI changes require:

- review against this philosophy;
- verification at the deployed resolution and Windows scaling;
- operator and technician usability review;
- regression testing of navigation, state colors, result wording, and recipe workflow;
- updated screenshots or UI reference images;
- a versioned release and audit trail.

## Bypass / inhibited quality interlock

Inspection bypass is an abnormal operating mode and is always shown in amber. Enabling it requires deliberate operator confirmation. Bypass does not recolor normal process graphics, fabricate a PASS, or suppress evidence required by the active fail-only storage policy; the station continues to show the actual inspection result while the PLC bypass tag is active. Every change is audit logged.
