"""Test firmware version formatting and OTA install capability."""

import pytest

from opendisplay.models.enums import ICType
from opendisplay.models.firmware import (
    firmware_ota_asset,
    format_firmware_version,
    supports_ble_ota_install,
)


@pytest.mark.parametrize(
    ("major", "minor", "patch", "expected"),
    [
        # The minor byte is the literal digits in the tag, never scaled.
        (1, 6, None, "1.6"),
        (1, 71, None, "1.71"),
        (2, 20, None, "2.20"),
        # A patch release must render three parts or it can never match its tag,
        # which is what makes an update look permanently pending.
        (2, 25, 1, "2.25.1"),
        (1, 6, 0, "1.6.0"),
    ],
)
def test_format_matches_the_release_tag_convention(major: int, minor: int, patch: int | None, expected: str) -> None:
    assert format_firmware_version(major, minor, patch) == expected


def test_absent_patch_and_zero_patch_are_different() -> None:
    """None means "this source predates the patch byte", 0 means "patch zero".

    Collapsing them would make a 1.6.0 device report 1.6 and stop matching the
    1.6.0 tag.
    """
    assert format_firmware_version(1, 6, None) == "1.6"
    assert format_firmware_version(1, 6, 0) == "1.6.0"


def test_patch_defaults_to_absent() -> None:
    assert format_firmware_version(3, 4) == "3.4"


def test_ble_ota_install_is_offered_only_for_silabs() -> None:
    assert supports_ble_ota_install(ICType.EFR32BG22) is True
    # nRF Legacy DFU has an asset but strands the device when driven over a
    # Bluetooth proxy, so it is deliberately not offered.
    assert supports_ble_ota_install(ICType.NRF52811) is False
    assert supports_ble_ota_install(ICType.NRF52840) is False
    # ESP32 has no BLE OTA path at all.
    assert supports_ble_ota_install(ICType.ESP32_C6) is False


def test_install_capability_is_stricter_than_asset_availability() -> None:
    """Every installable IC has an asset, but not every IC with an asset is installable.

    Pinned because the two are easy to conflate, and conflating them is what
    would re-enable the nRF-over-proxy path that bricks devices into their
    bootloader.
    """
    installable = [ic for ic in ICType if supports_ble_ota_install(ic)]
    assert installable, "expected at least one installable IC type"
    for ic in installable:
        assert firmware_ota_asset(ic, "2.25.1") is not None

    has_asset = {ic for ic in ICType if firmware_ota_asset(ic, "2.25.1") is not None}
    assert has_asset - set(installable), "expected an IC with an asset that is not installable"
