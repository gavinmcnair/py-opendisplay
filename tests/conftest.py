"""Pytest configuration and fixtures."""

from __future__ import annotations

from collections import deque
from pathlib import Path

import pytest
from PIL import Image

from opendisplay.models.config import GlobalConfig, ManufacturerData, PowerOption, SystemConfig
from opendisplay.models.enums import PowerMode

# Path to captured real protocol data
FIXTURES_DIR = Path(__file__).parent / "fixtures/real_protocol_data"


class FakeTransport:
    """In-memory Transport implementation for driving device flows without bleak.

    Replays a scripted list of response frames (bytes, or an Exception
    class/instance to raise) and records every written command. Structurally
    satisfies ``opendisplay.transport.Transport``, so it can back PIPE/crypto/
    upload suites without a real BLE or TCP link.
    """

    def __init__(
        self,
        responses: list | None = None,
        *,
        max_frame: int = 244,
        supports_write_without_response: bool = True,
        device_name: str | None = "FakeDevice",
    ) -> None:
        self.max_frame = max_frame
        self.supports_write_without_response = supports_write_without_response
        self.device_name = device_name
        self.written: list[bytes] = []
        self.write_responses: list[bool] = []
        self.drain_flags: list[bool] = []
        self.timeouts: list[float] = []
        self._responses: deque = deque(responses or [])
        self._connected = False
        self.drained = 0

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def write_command(self, data: bytes, response: bool = True, drain_stale: bool = True) -> None:
        self.written.append(data)
        self.write_responses.append(response)
        self.drain_flags.append(drain_stale)

    async def read_response(self, timeout: float = 5.0) -> bytes:
        self.timeouts.append(timeout)
        if not self._responses:
            raise RuntimeError("FakeTransport: no responses left")
        item = self._responses.popleft()
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item("scripted")
        if isinstance(item, BaseException):
            raise item
        return item

    def drain_notifications(self) -> int:
        self.drained += 1  # record the call; scripted responses are never dropped
        return 0

    async def clear_cache(self) -> bool:
        return False


@pytest.fixture
def fake_transport():
    """Factory fixture returning a configured :class:`FakeTransport`."""

    def _make(responses: list | None = None, **kwargs: object) -> FakeTransport:
        return FakeTransport(responses, **kwargs)

    return _make


@pytest.fixture
def power_option():
    """Factory fixture returning a :class:`PowerOption` with test defaults.

    Defaults describe a battery device configured to deep sleep on a 5 minute
    timer with the firmware's default wake window, which is the interesting case
    for sleep behaviour; pass keyword overrides for anything else. Every field is
    filled so callers only state what their test is actually about.
    """

    def _make(**overrides: object) -> PowerOption:
        params: dict[str, object] = {
            "power_mode": int(PowerMode.BATTERY),
            "battery_capacity_mah": b"\x00\x00\x00",
            "sleep_timeout_ms": 0,
            "tx_power": 0,
            "sleep_flags": 0,
            "battery_sense_pin": 0xFF,
            "battery_sense_enable_pin": 0xFF,
            "battery_sense_flags": 0,
            "capacity_estimator": 0,
            "voltage_scaling_factor": 0,
            "deep_sleep_current_ua": 0,
            "deep_sleep_time_seconds": 300,
            "charge_enable_pin": 0xFF,
            "charge_state_pin": 0xFF,
            "charger_flags": 0,
            "min_wake_time_seconds": 0,
            "screen_timeout_seconds": 0,
            "reserved": b"\x00" * 10,
        }
        params.update(overrides)
        return PowerOption(**params)  # type: ignore[arg-type]

    return _make


@pytest.fixture
def global_config(power_option):
    """Factory fixture returning a minimal :class:`GlobalConfig`.

    Carries only the three required sections; pass ``power=`` to supply a
    specific :class:`PowerOption`, or any other field as a keyword override.
    """

    def _make(power: PowerOption | None = None, **overrides: object) -> GlobalConfig:
        params: dict[str, object] = {
            "system": SystemConfig(ic_type=2, communication_modes=0x05, device_flags=0, pwr_pin=0xFF, reserved=b""),
            "manufacturer": ManufacturerData(manufacturer_id=1, board_type=1, board_revision=1, reserved=b""),
            "power": power if power is not None else power_option(),
        }
        params.update(overrides)
        return GlobalConfig(**params)  # type: ignore[arg-type]

    return _make


@pytest.fixture
def small_test_image():
    """Create a small RGB test image for encoding tests."""
    return Image.new("RGB", (10, 10), color=(128, 128, 128))


@pytest.fixture
def real_read_config_command():
    """Real READ_CONFIG command captured from device."""
    file = FIXTURES_DIR / "01_read_config_command.bin"
    if file.exists():
        return file.read_bytes()
    # Fallback if not captured yet
    return b"\x00\x40"


@pytest.fixture
def real_read_config_response():
    """Real config response from actual device."""
    file = FIXTURES_DIR / "01_read_config_response.bin"
    if file.exists():
        return file.read_bytes()
    return b""


@pytest.fixture
def real_firmware_command():
    """Real READ_FW_VERSION command."""
    file = FIXTURES_DIR / "02_read_firmware_command.bin"
    if file.exists():
        return file.read_bytes()
    return b"\x00\x43"


@pytest.fixture
def real_firmware_response():
    """Real firmware version response from device."""
    file = FIXTURES_DIR / "02_read_firmware_response.bin"
    if file.exists():
        return file.read_bytes()
    return b""


@pytest.fixture
def real_upload_start_command():
    """Real DIRECT_WRITE_START command (uncompressed)."""
    file = FIXTURES_DIR / "03_upload_start_uncompressed_command.bin"
    if file.exists():
        return file.read_bytes()
    return b"\x00\x70"


@pytest.fixture
def real_data_chunk_command():
    """Real DIRECT_WRITE_DATA chunk command."""
    file = FIXTURES_DIR / "04_data_chunk_command.bin"
    if file.exists():
        return file.read_bytes()
    return b""


@pytest.fixture
def real_upload_end_command():
    """Real DIRECT_WRITE_END command."""
    file = FIXTURES_DIR / "05_upload_end_command.bin"
    if file.exists():
        return file.read_bytes()
    return b"\x00\x72\x00"


def real_advertisement_data():
    """Real advertisement data from the device (manufacturer ID stripped by Bleak)."""
    # Format: [protocol:7][battery:2 LE][temp:1 signed][loop:1]
    # Real captured data: Battery 3925mV, Temp 22°C, Loop 77
    return bytes([0x02, 0x36, 0x00, 0x6C, 0x00, 0xC3, 0x01, 0x55, 0x0F, 0x16, 0x4D])
