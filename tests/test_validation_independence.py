"""What makes a validation sample count.

Validation needs several independent pieces of evidence, not the same evidence
several times. The rule used to be pose alone: a sample counted only if the
battery sat meaningfully differently from every counted sample before it.

That is unworkable on a fixed-stop fixture, and for the reason the fixture
exists -- the stop is there to make the pose repeatable, so requiring a
different pose asks the technician to defeat it. A different physical battery
is both achievable there and better evidence: part-to-part variation in stamp
depth, finish, and ring is what actually varies in production.

Either now satisfies the gate. Neither is optional.
"""

from __future__ import annotations

import pytest

from battery_inspector.config import AppConfig
from battery_inspector.ui.wizard.recipe_wizard import ReadinessPage

independent = ReadinessPage._independent_of_previous


def _sample(x: float, y: float, *, rotation: float = 0.0, different_part: bool = False) -> dict:
    return {
        "disposition": "pass",
        "different_part": different_part,
        "locator_metrics": {
            "battery_center_normalized": [x, y],
            "rotation_deg": rotation,
            "scale": 1.0,
        },
    }


def test_the_same_part_in_the_same_place_is_not_independent() -> None:
    """The case the gate exists for: one frame counted five times."""

    first = _sample(0.5, 0.5)

    assert independent(_sample(0.5, 0.5), [first]) is False


def test_a_confirmed_different_part_counts_at_the_same_position() -> None:
    """The fixed-stop station. The pose repeats because the fixture works."""

    first = _sample(0.5, 0.5)

    assert independent(_sample(0.5, 0.5, different_part=True), [first]) is True


def test_a_moved_part_still_counts_without_confirmation() -> None:
    """A station where the part is free to sit differently is unaffected."""

    first = _sample(0.5, 0.5)

    assert independent(_sample(0.62, 0.5), [first]) is True
    assert independent(_sample(0.5, 0.5, rotation=20.0), [first]) is True


def test_five_samples_of_one_unmoved_part_cannot_all_count() -> None:
    """The whole point: a recipe must not qualify on one frame repeated."""

    counted: list[dict] = []
    for _ in range(5):
        candidate = _sample(0.5, 0.5)
        if independent(candidate, counted):
            counted.append(candidate)

    assert len(counted) == 1


def test_five_confirmed_parts_at_one_stop_all_count() -> None:
    """The flow a fixed-stop station has to be able to complete."""

    counted: list[dict] = []
    for _ in range(5):
        candidate = _sample(0.5, 0.5, different_part=True)
        if independent(candidate, counted):
            counted.append(candidate)

    assert len(counted) == 5


def test_only_counted_samples_are_compared_against() -> None:
    """A rejected sample is evidence, but it is not a pose already used."""

    rejected = {**_sample(0.5, 0.5), "disposition": "reject"}

    assert independent(_sample(0.5, 0.5), [rejected]) is True


def test_a_sample_without_locator_metrics_is_not_silently_dropped() -> None:
    """No metrics means the check cannot judge, so it must not block."""

    assert independent({"disposition": "pass"}, [_sample(0.5, 0.5)]) is True


# --- the station setting ----------------------------------------------------


def test_the_required_sample_count_is_a_station_setting() -> None:
    config = AppConfig()

    assert config.validation_runs_required == 5


@pytest.mark.parametrize("requested,expected", [(0, 1), (1, 1), (12, 12), (999, 50)])
def test_the_required_count_is_bounded(requested, expected) -> None:
    """One is the floor; a typo must not demand a thousand samples."""

    import dataclasses

    config = dataclasses.replace(
        AppConfig(), validation_runs_required=requested
    ).normalized()

    assert config.validation_runs_required == expected
