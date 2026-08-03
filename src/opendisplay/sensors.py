"""Read attached-sensor values out of a device's BLE advertisement.

Sensor readings are not a separate protocol path: the firmware bit-packs them into
the 11-byte dynamic block that is broadcast in every advertisement, at the offset
recorded in the device's own config (TLV packet 0x23, ``SensorData``). Decoding one
therefore needs both halves -- the config for the offset, the advertisement for the
bytes -- which is what this module joins together.

This is a convenience layer for scripts and the CLI. Consumers that render one
entity per measurement (Home Assistant, for example) should skip it and call
:meth:`~opendisplay.models.advertisement.AdvertisementData.sht40_reading` directly
with ``SensorData.sht40_msd_start_byte``, rather than rebuilding and searching a
list on every read.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models.advertisement import AdvertisementData
from .models.config import GlobalConfig, SensorData
from .models.enums import SensorType

__all__ = ["SensorReading", "read_sensor_values"]


@dataclass(frozen=True)
class SensorReading:
    """One configured sensor's current measurements.

    Fields a given sensor type does not report stay None -- a temperature-only
    sensor leaves ``humidity_percent`` unset.
    """

    instance_number: int
    sensor_type: int
    sensor_type_enum: SensorType | int
    temperature_c: float | None = None
    humidity_percent: float | None = None


def _read_one(sensor: SensorData, advertisement: AdvertisementData) -> SensorReading | None:
    """Decode one configured sensor, or None if it has no readable value."""
    if sensor.sensor_type_enum is not SensorType.SHT40:
        return None

    reading = advertisement.sht40_reading(sensor.sht40_msd_start_byte)
    if reading is None:
        return None

    return SensorReading(
        instance_number=sensor.instance_number,
        sensor_type=sensor.sensor_type,
        sensor_type_enum=sensor.sensor_type_enum,
        temperature_c=reading.temperature_c,
        humidity_percent=reading.humidity_percent,
    )


def read_sensor_values(config: GlobalConfig, advertisement: AdvertisementData) -> list[SensorReading]:
    """Decode every configured sensor that currently has a valid reading.

    Args:
        config: Device config, read via ``OpenDisplayDevice.config``. Supplies both
            which sensors exist and where each one's bytes live.
        advertisement: A v1 advertisement from the same device.

    Returns:
        One entry per sensor with a usable reading, in config order. Sensors of an
        unsupported type, and sensors whose slot holds no valid measurement, are
        omitted -- so an empty list means "nothing readable right now", not
        "no sensors fitted".
    """
    readings = (_read_one(sensor, advertisement) for sensor in config.sensors)
    return [reading for reading in readings if reading is not None]
