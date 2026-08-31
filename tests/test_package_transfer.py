"""Moving one model, or one recipe, to another machine.

The workstation backup moves a whole station and is the right tool for
replacing a machine. These packages are for the two things that happen between
machines that are staying: send a trained model to a second station, and put a
qualified recipe on a second line.

A recipe package carries validation evidence across stations by explicit
product decision. These tests hold the parts of that decision that are not
negotiable: the package is checksummed and a damaged one is refused, the
reference image travels with it and is repointed at this station's copy, and an
import can never quietly overwrite an existing immutable revision.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from battery_inspector.package_transfer import (
    PackageTransferError,
    export_model_package,
    export_recipe_package,
    import_model_package,
    import_recipe_package,
    inspect_model_package,
    inspect_recipe_package,
)
from battery_inspector.models import (
    Marking,
    NormalizedRect,
    Recipe,
    ReferenceCapture,
    TerminalRecipe,
    TerminalRole,
)

from conftest import mark_validated


def _model(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    model = tmp_path / "polarity_classifier.onnx"
    manifest = tmp_path / "polarity_classifier.json"
    model.write_bytes(b"onnx-bytes-standing-in-for-a-real-model")
    manifest.write_text(
        json.dumps(
            {
                "model_id": "polarity-model",
                "model_version": "2026.03",
                "classes": ["plus", "minus", "blank", "invalid_marking"],
                "input_crop_contract": "taught_circle_v017",
            }
        ),
        encoding="utf-8",
    )
    return model, manifest


def _recipe(tmp_path, *, with_reference: bool = True) -> Recipe:
    tmp_path.mkdir(parents=True, exist_ok=True)
    reference = None
    if with_reference:
        image = tmp_path / "reference.png"
        image.write_bytes(b"a-reference-image")
        reference = ReferenceCapture(
            capture_id="CAP-1",
            path=str(image),
            sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
            captured_at_utc="2026-01-01T00:00:00+00:00",
            width_px=100,
            height_px=80,
        )
    recipe = Recipe.new(
        name="GROUP31",
        recipe_number=7,
        part_number="PN-7",
        description="",
        created_by="test",
        battery_roi=NormalizedRect(0.0, 0.0, 1.0, 1.0),
        terminals=[
            TerminalRecipe(
                key="negative",
                name="Negative Terminal",
                role=TerminalRole.NEGATIVE,
                search_roi=NormalizedRect(0.1, 0.1, 0.2, 0.2),
                marking_roi=NormalizedRect(0.3, 0.3, 0.3, 0.3),
                expected_marking=Marking.MINUS,
                red_ring_required=False,
            ),
            TerminalRecipe(
                key="positive",
                name="Positive Terminal",
                role=TerminalRole.POSITIVE,
                search_roi=NormalizedRect(0.6, 0.6, 0.2, 0.2),
                marking_roi=NormalizedRect(0.3, 0.3, 0.3, 0.3),
                expected_marking=Marking.PLUS,
                red_ring_required=False,
            ),
        ],
        reference_image=reference,
    )
    return mark_validated(recipe)


# --- model packages ---------------------------------------------------------


def test_a_model_package_round_trips(tmp_path) -> None:
    model, manifest = _model(tmp_path / "source")
    package = tmp_path / "model.zip"

    export_model_package(
        model_path=model, manifest_path=manifest, destination=package, station_name="LINE 1"
    )
    described = inspect_model_package(package)
    imported = import_model_package(package, tmp_path / "destination")

    assert described["model_id"] == "polarity-model"
    assert described["model_version"] == "2026.03"
    assert described["source_station"] == "LINE 1"
    from battery_inspector.package_transfer import _sha256_file

    assert imported["model_sha256"] == _sha256_file(model)
    assert Path(imported["model_path"]).is_file()
    assert Path(imported["manifest_path"]).is_file()


def test_a_tampered_model_package_is_refused(tmp_path) -> None:
    """The checksum is the only thing standing between a station and a swapped model."""

    model, manifest = _model(tmp_path / "source")
    package = tmp_path / "model.zip"
    export_model_package(model_path=model, manifest_path=manifest, destination=package)

    rewritten = tmp_path / "tampered.zip"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(rewritten, "w") as target:
        for item in source.namelist():
            data = source.read(item)
            if item.endswith("polarity_classifier.onnx"):
                data = b"a-different-model-entirely"
            target.writestr(item, data)

    with pytest.raises(PackageTransferError, match="checksum"):
        import_model_package(rewritten, tmp_path / "destination")


def test_a_zip_that_is_not_a_package_is_refused(tmp_path) -> None:
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as archive:
        archive.writestr("notes.txt", "nothing to do with Pole Position")

    with pytest.raises(PackageTransferError, match="not a Pole Position package"):
        inspect_model_package(plain)


def test_a_recipe_package_is_not_a_model_package(tmp_path) -> None:
    recipe = _recipe(tmp_path / "source")
    package = tmp_path / "recipe.zip"
    export_recipe_package(recipe=recipe, destination=package)

    with pytest.raises(PackageTransferError):
        inspect_model_package(package)


# --- recipe packages --------------------------------------------------------


def test_a_recipe_package_carries_its_reference_and_evidence(tmp_path) -> None:
    recipe = _recipe(tmp_path / "source")
    package = tmp_path / "recipe.zip"

    export_recipe_package(recipe=recipe, destination=package, station_name="LINE 1")
    described = inspect_recipe_package(package)
    imported = import_recipe_package(
        package,
        reference_root=tmp_path / "destination" / "staging",
        models_root=tmp_path / "destination" / "models",
    )

    restored = imported["recipe"]
    assert described["source_station"] == "LINE 1"
    assert described["validation_complete"] is True
    assert restored.recipe_number == 7
    assert restored.name == "GROUP31"
    assert restored.validation_complete is True
    # Repointed at this station's copy, not the exporting station's path.
    assert restored.reference_image is not None
    assert Path(restored.reference_image.path).is_file()
    assert restored.reference_image.path != str(tmp_path / "source" / "reference.png")


def test_a_recipe_with_no_reference_cannot_be_packaged(tmp_path) -> None:
    """A package that looked complete and could not grade a part would be worse."""

    recipe = _recipe(tmp_path / "source", with_reference=False)

    with pytest.raises(PackageTransferError, match="no reference image"):
        export_recipe_package(recipe=recipe, destination=tmp_path / "recipe.zip")


def test_a_recipe_package_carries_the_model_it_is_bound_to(tmp_path) -> None:
    recipe = _recipe(tmp_path / "source")
    model, manifest = _model(tmp_path / "source_model")
    package = tmp_path / "recipe.zip"

    export_recipe_package(
        recipe=recipe,
        destination=package,
        model_path=model,
        model_manifest_path=manifest,
    )
    imported = import_recipe_package(
        package,
        reference_root=tmp_path / "destination" / "staging",
        models_root=tmp_path / "destination" / "models",
    )

    assert inspect_recipe_package(package)["includes_model"] is True
    assert imported["model"] is not None
    assert Path(imported["model"]["model_path"]).is_file()
    assert Path(imported["model"]["manifest_path"]).is_file()


def test_a_tampered_reference_image_is_refused(tmp_path) -> None:
    recipe = _recipe(tmp_path / "source")
    package = tmp_path / "recipe.zip"
    export_recipe_package(recipe=recipe, destination=package)

    rewritten = tmp_path / "tampered.zip"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(rewritten, "w") as target:
        for item in source.namelist():
            data = source.read(item)
            if item.startswith("reference/"):
                data = b"a-photograph-of-a-different-battery"
            target.writestr(item, data)

    with pytest.raises(PackageTransferError, match="checksum"):
        import_recipe_package(
            rewritten,
            reference_root=tmp_path / "destination" / "staging",
            models_root=tmp_path / "destination" / "models",
        )


# --- what the station does with a package -----------------------------------


def test_an_import_never_overwrites_an_existing_revision(qapp, controller, tmp_path) -> None:
    """A revision is an immutable production record, on either machine.

    Overwriting one on import would rewrite the evidence a station already
    shipped parts against, and it would do it silently.
    """

    recipe = _recipe(tmp_path / "source")
    package = tmp_path / "recipe.zip"
    export_recipe_package(recipe=recipe, destination=package)
    controller.import_recipe_package(package, install_model=False)

    with pytest.raises(ValueError, match="already on this station"):
        controller.import_recipe_package(package, install_model=False)


def test_an_imported_recipe_is_runnable_as_the_source_validated_it(
    qapp, controller, tmp_path
) -> None:
    """The point of the package: no re-teaching on the second machine."""

    recipe = _recipe(tmp_path / "source")
    package = tmp_path / "recipe.zip"
    export_recipe_package(recipe=recipe, destination=package)

    imported = controller.import_recipe_package(package, install_model=False)["recipe"]

    resolved = controller.repository.resolve_production_recipe(recipe_number=7)
    assert resolved is not None
    assert resolved.recipe_id == imported.recipe_id
    assert resolved.validation_complete is True


def test_importing_a_recipe_whose_number_is_taken_is_refused(
    qapp, controller, tmp_path
) -> None:
    """One selector value names one product, whatever machine it arrived from."""

    from battery_inspector.data.repository import DuplicateRecipeIdentifier

    resident = _recipe(tmp_path / "resident")
    resident.name = "SOMETHING_ELSE"
    controller.repository.save_recipe(resident, username="test")

    incoming = _recipe(tmp_path / "source")
    package = tmp_path / "recipe.zip"
    export_recipe_package(recipe=incoming, destination=package)

    with pytest.raises(DuplicateRecipeIdentifier, match="number 7"):
        controller.import_recipe_package(package, install_model=False)


def test_the_import_is_written_to_the_audit_log(qapp, controller, tmp_path) -> None:
    """Evidence that crossed machines has to be traceable to where it came from."""

    recipe = _recipe(tmp_path / "source")
    package = tmp_path / "recipe.zip"
    export_recipe_package(recipe=recipe, destination=package, station_name="LINE 1")

    controller.import_recipe_package(package, install_model=False)

    entries = [
        event
        for event in controller.audit_events()
        if event["category"] == "RECIPE" and "Imported recipe package" in event["message"]
    ]
    assert len(entries) == 1
    assert "LINE 1" in entries[0]["message"]
