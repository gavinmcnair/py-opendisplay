"""Firmware version data structure."""

from __future__ import annotations

from typing import Final, TypedDict

from .enums import ICType

_FIRMWARE_REPOS: Final[dict[int, str]] = {
    ICType.NRF52840: "OpenDisplay/Firmware",
    ICType.ESP32_S3: "OpenDisplay/Firmware",
    ICType.ESP32_C3: "OpenDisplay/Firmware",
    ICType.ESP32_C6: "OpenDisplay/Firmware",
    ICType.NRF52811: "OpenDisplay/Firmware_NRF",
    ICType.EFR32BG22: "OpenDisplay/Firmware_Silabs",
}


def firmware_release_repo(ic_type: int) -> str | None:
    """Return the GitHub repo slug for a device's firmware, or None if unknown."""
    return _FIRMWARE_REPOS.get(ic_type)


def firmware_ota_asset(ic_type: int, tag: str) -> str | None:
    """Return the expected OTA asset filename for a given IC type and release tag.

    Returns None for IC types without a BLE OTA path (ESP32 variants).
    """
    if ic_type == ICType.NRF52840:
        return "NRF52840.zip"
    if ic_type == ICType.NRF52811:
        return f"EPD-nRF52-{tag}-ota.zip"
    if ic_type == ICType.EFR32BG22:
        return f"opendisplay-bg22-v{tag}.gbl"
    return None


# BLE OTA install is only advertised for ICs where the flash completes reliably
# over an ESPHome Bluetooth proxy, which is the common deployment. EFR32BG22
# (Silabs AppLoader) does. nRF Legacy DFU does NOT: verified end to end, the
# device receives the full, CRC-valid image but the final activate/commit write
# is unreliable over a proxy and strands the device in the bootloader. It works
# over a *direct* connection, so nRF firmware must be flashed directly or via
# USB-UF2. Note this is narrower than firmware_ota_asset(), which answers "is
# there an asset for this IC" rather than "should a host offer to install it".
_BLE_OTA_INSTALL_IC_TYPES: Final[frozenset[int]] = frozenset({ICType.EFR32BG22})


def supports_ble_ota_install(ic_type: int) -> bool:
    """Return True if a host should offer a BLE OTA install for this IC type.

    Deliberately stricter than ``firmware_ota_asset`` being non-None: an asset
    can exist for an IC whose over-the-proxy install path is not dependable
    (nRF Legacy DFU), where offering the install strands the device in its
    bootloader. Hosts that only surface release notes should use
    ``firmware_release_repo`` instead.
    """
    return ic_type in _BLE_OTA_INSTALL_IC_TYPES


def format_firmware_version(major: int, minor: int, patch: int | None = None) -> str:
    """Format a firmware version to match the GitHub release tag convention.

    Firmware parses its own BUILD_VERSION string with a plain int conversion
    (``atoi`` on the substring after the dot), so the minor byte already equals
    the literal digits in the tag_name: 1.6 -> 6, 1.71 -> 71, 2.20 -> 20. No
    scaling is applied.

    ``patch`` is None when the source predates the trailing patch byte of the
    version response (for example a device dict cached by an older host), and
    the two-part form is used. With a patch available the three-part form lets a
    device on a patch release such as 2.25.1 match its tag instead of appearing
    to have an update pending forever.

    Every consumer that displays a version must format it through here. A host
    that formats the installed version one way and compares it against a tag
    formatted another way shows a permanently pending update.
    """
    if patch is None:
        return f"{major}.{minor}"
    return f"{major}.{minor}.{patch}"


class FirmwareVersion(TypedDict):
    """Firmware version information.

    Attributes:
        major: Major version number (0-255)
        minor: Minor version number (0-255)
        sha: Git commit SHA hash
        patch: Patch version number (0-255); 0 when the firmware predates
            the trailing patch byte in the version response
    """

    major: int
    minor: int
    sha: str
    patch: int
