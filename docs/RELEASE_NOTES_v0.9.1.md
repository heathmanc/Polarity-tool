# Polarity Tool v0.9.1 Release Notes

## Purpose

v0.9.1 is a recipe-database compatibility hotfix for the v0.9.0 light HMI.
The inspection engine, HMI layout, color philosophy, camera interface, PLC
interface, and recipe-validation requirements are unchanged.

## Corrected startup failure

v0.8.1 stored this classifier setting in recipe JSON:

```text
terminal_top_conditional_minimum_geometry_confidence
```

v0.9.0 shortened the Python attribute to:

```text
terminal_top_conditional_geometry_confidence
```

The v0.9.0 reader did not translate the existing key, so loading a station
recipe database could stop application startup with a `TypeError`.

v0.9.1 translates the legacy key during deserialization and preserves the
stored numeric value. If both the legacy and current keys are present, the
current key takes precedence.

## Forward-compatible settings loading

Persisted locator and marking-classifier settings now ignore unknown keys.
This prevents a setting added by another compatible build from making the
entire HMI unavailable. Known settings continue to be normalized and bounded
by the same safety limits.

The compatibility behavior is intentionally limited to settings dictionaries.
Required recipe identity, terminal geometry, reference-image, and validation
fields are still validated normally.

## Data effect

- Existing SQLite databases are read in place.
- No recipe rows are deleted or rewritten during startup.
- No camera or PLC settings are changed.
- The legacy geometry-confidence threshold is preserved exactly.
- The retired v0.8.1 center-offset field has no direct equivalent in the v0.9
  centered-stamp gate; it is ignored and the current conservative center-score
  default remains in force.
- Saving a recipe revision writes the current field name.
- v0.8.1/v0.9.0 recipe validation evidence remains valid because the inspection
  engine is unchanged.

## Verification

The release includes regression coverage that:

1. Loads the exact v0.8.1 field name.
2. Preserves its numeric value under the current field.
3. Loads a SQLite recipe row containing the legacy payload through
   `RecipeRepository.list_latest_recipes()`.
4. Ignores unknown future locator/classifier keys without discarding known
   settings.
