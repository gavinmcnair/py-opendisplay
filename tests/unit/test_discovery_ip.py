"""Tests for mDNS/DNS-SD discovery of WiFi devices (discover_ip_devices).

zeroconf is stubbed at the module attributes the function imports lazily, so
no real multicast traffic happens. The TXT decode helpers are tested directly
since they define how firmware-supplied records map onto IpDeviceInfo.
"""

from __future__ import annotations

from typing import Any

import pytest
import zeroconf
import zeroconf.asyncio
from zeroconf import ServiceStateChange

from opendisplay.discovery_ip import IpDeviceInfo, _txt_bool, _txt_str, discover_ip_devices

SERVICE_TYPE = "_opendisplay._tcp.local."


def _txt(**kwargs: str) -> dict[bytes, bytes | None]:
    return {key.encode(): value.encode() for key, value in kwargs.items()}


class _FakeServiceInfo:
    """Stands in for AsyncServiceInfo; behavior comes from _REGISTRY."""

    def __init__(self, _service_type: str, name: str) -> None:
        self.name = name
        spec = _REGISTRY[name]
        self._resolves: bool = spec.get("resolves", True)
        self._addresses: list[str] = spec.get("addresses", ["192.168.1.50"])
        self.port: int | None = spec.get("port", 2446)
        self.properties: dict[bytes, bytes | None] = spec.get("properties", {})

    async def async_request(self, _zc: object, timeout: int = 3000) -> bool:
        return self._resolves

    def parsed_scoped_addresses(self) -> list[str]:
        return self._addresses


class _FakeBrowser:
    """Fires Added for every registered name as soon as browsing starts."""

    instances: list[_FakeBrowser] = []

    def __init__(self, _zc: object, _service_type: str, handlers: list[Any]) -> None:
        self.cancelled = False
        _FakeBrowser.instances.append(self)
        for name in _REGISTRY:
            for handler in handlers:
                # Real zeroconf dispatches via Signal.fire() using KEYWORD
                # arguments; calling positionally here would let a handler with
                # mismatched parameter names pass the suite and fail on hardware.
                handler(
                    zeroconf=_zc,
                    service_type=_service_type,
                    name=name,
                    state_change=ServiceStateChange.Added,
                )
                # A concurrent Removed for the same name must not register it.
                handler(
                    zeroconf=_zc,
                    service_type=_service_type,
                    name=name,
                    state_change=ServiceStateChange.Removed,
                )

    async def async_cancel(self) -> None:
        self.cancelled = True


class _FakeAsyncZeroconf:
    instances: list[_FakeAsyncZeroconf] = []

    def __init__(self) -> None:
        self.zeroconf = object()
        self.closed = False
        _FakeAsyncZeroconf.instances.append(self)

    async def async_close(self) -> None:
        self.closed = True


_REGISTRY: dict[str, dict[str, Any]] = {}


@pytest.fixture(autouse=True)
def _stub_zeroconf(monkeypatch: pytest.MonkeyPatch) -> None:
    _REGISTRY.clear()
    _FakeAsyncZeroconf.instances.clear()
    _FakeBrowser.instances.clear()
    monkeypatch.setattr(zeroconf.asyncio, "AsyncZeroconf", _FakeAsyncZeroconf)
    monkeypatch.setattr(zeroconf.asyncio, "AsyncServiceBrowser", _FakeBrowser)
    monkeypatch.setattr(zeroconf.asyncio, "AsyncServiceInfo", _FakeServiceInfo)


# ── TXT decoding helpers ─────────────────────────────────────────────────────


def test_txt_str_decodes_present_key() -> None:
    assert _txt_str(_txt(mac="aa:bb:cc:dd:ee:ff"), "mac") == "aa:bb:cc:dd:ee:ff"


def test_txt_str_missing_key_is_none() -> None:
    assert _txt_str({}, "mac") is None
    assert _txt_str({b"mac": None}, "mac") is None


def test_txt_str_undecodable_value_is_none() -> None:
    assert _txt_str({b"fw": b"\xff\xfe"}, "fw") is None


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", " Yes "])
def test_txt_bool_truthy_forms(raw: str) -> None:
    assert _txt_bool({b"tls": raw.encode()}, "tls") is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "", "maybe"])
def test_txt_bool_falsy_forms(raw: str) -> None:
    assert _txt_bool({b"tls": raw.encode()}, "tls") is False


def test_txt_bool_missing_key_is_false() -> None:
    assert _txt_bool({}, "tls") is False


# ── discovery ────────────────────────────────────────────────────────────────


async def test_discovers_device_with_full_txt_record() -> None:
    _REGISTRY[f"kitchen.{SERVICE_TYPE}"] = {
        "addresses": ["192.168.1.50"],
        "port": 2446,
        "properties": _txt(mac="aa:bb:cc:dd:ee:ff", tls="1", msd="4.2", fw="2.2.0", cm="1"),
    }
    found = await discover_ip_devices(scan_seconds=0)
    assert found == {
        "kitchen": IpDeviceInfo(
            name="kitchen",
            host="192.168.1.50",
            port=2446,
            mac="aa:bb:cc:dd:ee:ff",  # passed through raw (lowercase-colon)
            tls=True,
            msd="4.2",
            fw="2.2.0",
            cm="1",
        )
    }


async def test_optional_txt_fields_default_to_none() -> None:
    _REGISTRY[f"bare.{SERVICE_TYPE}"] = {"properties": _txt(mac="11:22:33:44:55:66", tls="0")}
    device = (await discover_ip_devices(scan_seconds=0))["bare"]
    assert (device.tls, device.msd, device.fw, device.cm) == (False, None, None, None)


async def test_unresolvable_service_is_skipped() -> None:
    _REGISTRY[f"ghost.{SERVICE_TYPE}"] = {"resolves": False}
    assert await discover_ip_devices(scan_seconds=0) == {}


async def test_service_without_address_is_skipped() -> None:
    _REGISTRY[f"noaddr.{SERVICE_TYPE}"] = {"addresses": []}
    assert await discover_ip_devices(scan_seconds=0) == {}


async def test_service_without_port_is_skipped() -> None:
    _REGISTRY[f"noport.{SERVICE_TYPE}"] = {"port": None}
    assert await discover_ip_devices(scan_seconds=0) == {}


async def test_unexpected_suffix_falls_back_to_first_label() -> None:
    _REGISTRY["odd-name._other._tcp.local."] = {"properties": _txt(mac="aa:bb:cc:dd:ee:ff")}
    found = await discover_ip_devices(scan_seconds=0)
    assert list(found) == ["odd-name"]


async def test_mixed_results_keeps_only_usable_devices() -> None:
    _REGISTRY[f"good.{SERVICE_TYPE}"] = {"properties": _txt(mac="aa:bb:cc:dd:ee:ff", tls="1")}
    _REGISTRY[f"ghost.{SERVICE_TYPE}"] = {"resolves": False}
    _REGISTRY[f"noaddr.{SERVICE_TYPE}"] = {"addresses": []}
    found = await discover_ip_devices(scan_seconds=0)
    assert list(found) == ["good"]


async def test_no_services_returns_empty_mapping() -> None:
    assert await discover_ip_devices(scan_seconds=0) == {}


async def test_zeroconf_is_closed_even_when_resolution_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ExplodingInfo(_FakeServiceInfo):
        async def async_request(self, _zc: object, timeout: int = 3000) -> bool:
            raise RuntimeError("mDNS stack died")

    monkeypatch.setattr(zeroconf.asyncio, "AsyncServiceInfo", _ExplodingInfo)
    _REGISTRY[f"kitchen.{SERVICE_TYPE}"] = {}
    with pytest.raises(RuntimeError):
        await discover_ip_devices(scan_seconds=0)
    assert _FakeAsyncZeroconf.instances[-1].closed  # no leaked socket


async def test_browser_and_zeroconf_are_torn_down() -> None:
    _REGISTRY[f"kitchen.{SERVICE_TYPE}"] = {}
    await discover_ip_devices(scan_seconds=0)
    assert _FakeBrowser.instances[-1].cancelled
    assert _FakeAsyncZeroconf.instances[-1].closed
