"""The interface document states exact values. These check it still can.

An interface control document is handed to a controls engineer who will write a
program against it and will not be reading the source. A number that drifts out
of date here is worse than no document: it is a specification that is confidently
wrong, and the fault shows up as a station that does not respond to a trigger.
"""

from __future__ import annotations

import re
from pathlib import Path

from battery_inspector.config import AppConfig, PlcTagMap

ROOT = Path(__file__).resolve().parents[1]
ICD = ROOT / "docs" / "manual" / "plc-icd.html.in"


def _document() -> str:
    return ICD.read_text(encoding="utf-8")


def test_every_tag_the_station_uses_is_specified() -> None:
    document = _document()
    defaults = PlcTagMap()

    for field in PlcTagMap.__dataclass_fields__:
        tag = str(getattr(defaults, field))
        if not tag:
            # The acknowledge tag ships blank, and the document says so rather
            # than inventing a default that the station does not use.
            assert "blank by default" in document, (
                f"{field} has no default tag; the document must say it is blank"
            )
            continue
        assert tag in document, f"{field} default tag {tag} is not in the document"


def test_the_quoted_timings_match_the_application_defaults() -> None:
    document = _document()
    config = AppConfig()

    assert f"<strong>{config.plc_poll_ms} ms</strong>" in document, (
        f"the document must quote the real poll interval ({config.plc_poll_ms} ms)"
    )
    assert f"<strong>{config.plc_heartbeat_ms} ms</strong>" in document, (
        f"the document must quote the real heartbeat interval "
        f"({config.plc_heartbeat_ms} ms)"
    )
    # The watchdog recommendation is derived from the heartbeat interval, so it
    # cannot be left behind when that changes.
    assert f"default {config.plc_heartbeat_ms} ms heartbeat" in document


def test_persistence_is_stated_for_every_signal() -> None:
    """The column the document exists for."""

    document = _document()
    # Stop at the legend, which carries one badge of each kind by design.
    register = document[
        document.index("Signal register") : document.index('<div class="legend">')
    ]
    rows = re.findall(r"<tr>\s*<td><strong>(.*?)</strong>", register, re.S)
    assert len(rows) == len(PlcTagMap.__dataclass_fields__), (
        f"the register lists {len(rows)} signals, the station has "
        f"{len(PlcTagMap.__dataclass_fields__)}"
    )

    badges = re.findall(r'<span class="badge (\w+)">', register)
    assert len(badges) == len(rows), "every signal needs a persistence badge"
    assert set(badges) <= {"level", "edge", "latched", "alt"}


def test_the_cases_that_publish_nothing_are_documented() -> None:
    """Silence is the failure a controls engineer cannot see from the tags."""

    document = _document()
    for behaviour in (
        "publish nothing",          # recipe mismatch
        "silently lost",            # trigger pulsed during Busy
        "never touch the PLC tags", # manual inspection
        "frozen at its last written value",  # communication loss
    ):
        assert behaviour in document, f"the document must state: {behaviour}"


def test_the_selector_is_documented_as_deciding_the_recipe() -> None:
    """The one contract change a controls engineer cannot discover by reading tags.

    The selector used to be a permissive checked against a recipe activated at
    the HMI. It now decides the recipe. A program written against the old
    wording would leave a mixed line inhibited on every product change, so the
    document must not still describe a match.
    """

    document = _document()

    assert "decides the recipe on every trigger" in document
    assert "newest revision of that recipe whose validation is complete" in document
    # Refusal, not substitution, is the safety-relevant half of the contract.
    assert "never substitutes another recipe" in document
    assert "does not match the active" not in document
