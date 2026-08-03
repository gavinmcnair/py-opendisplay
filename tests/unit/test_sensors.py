"""Test joining device config with advertisement data to read sensor values."""

from opendisplay.models.advertisement import AdvertisementData, parse_advertisement
from opendisplay.models.config import (
    GlobalConfig,
    ManufacturerData,
    PowerOption,
    SensorData,
    SystemConfig,
)
from opendisplay.models.enums import SensorType
from opendisplay.sensors import read_sensor_values

SHT40_BLOCK = bytes.fromhex("d7c109")  # 22.4 C / 47.1 %RH


def _advertisement(block: bytes = SHT40_BLOCK, start_byte: int = 7) -> AdvertisementData:
    """A v1 advertisement carrying one SHT40 reading at ``start_byte``."""
    dynamic = bytearray(11)
    dynamic[start_byte : start_byte + 3] = block
    return parse_advertisement(bytes(dynamic) + bytes([124, 139, 0x00]))


def _config(*sensors: SensorData) -> GlobalConfig:
    """A config carrying only what read_sensor_values looks at."""
    return GlobalConfig(
        system=SystemConfig(ic_type=2, communication_modes=0x05, device_flags=0, pwr_pin=0xFF, reserved=b""),
        manufacturer=ManufacturerData(manufacturer_id=1, board_type=1, board_revision=1, reserved=b""),
        power=PowerOption(
            power_mode=1,
            battery_capacity_mah=(3000).to_bytes(3, "little"),
            sleep_timeout_ms=40000,
            tx_power=8,
            sleep_flags=0,
            battery_sense_pin=1,
            battery_sense_enable_pin=0x28,
            battery_sense_flags=0,
            capacity_estimator=5,
            voltage_scaling_factor=0xA1,
            deep_sleep_current_ua=0,
            deep_sleep_time_seconds=0,
            charge_enable_pin=0,
            charge_state_pin=0,
            charger_flags=0,
            min_wake_time_seconds=0,
            screen_timeout_seconds=0,
            reserved=b"",
        ),
        sensors=list(sensors),
    )


def test_reads_sht40_at_default_slot() -> None:
    config = _config(SensorData(instance_number=0, sensor_type=SensorType.SHT40, bus_id=1))

    readings = read_sensor_values(config, _advertisement())

    assert len(readings) == 1
    assert readings[0].instance_number == 0
    assert readings[0].sensor_type_enum is SensorType.SHT40
    assert readings[0].temperature_c == 22.4
    assert readings[0].humidity_percent == 47.1


def test_reads_sht40_at_relocated_slot() -> None:
    """The offset comes from config, not from the default."""
    config = _config(
        SensorData(instance_number=0, sensor_type=SensorType.SHT40, bus_id=1, msd_data_start_byte=2),
    )

    readings = read_sensor_values(config, _advertisement(start_byte=2))

    assert len(readings) == 1
    assert readings[0].temperature_c == 22.4


def test_ignores_unsupported_sensor_type() -> None:
    """A sensor we cannot decode is omitted rather than guessed at."""
    config = _config(SensorData(instance_number=0, sensor_type=SensorType.AXP2101_PMIC, bus_id=1))

    assert read_sensor_values(config, _advertisement()) == []


def test_omits_sensor_without_valid_reading() -> None:
    """A configured-but-failing sensor reports nothing, not a bogus value."""
    config = _config(SensorData(instance_number=0, sensor_type=SensorType.SHT40, bus_id=1))

    assert read_sensor_values(config, _advertisement(block=b"\xff\xff\xff")) == []


def test_no_sensors_configured() -> None:
    assert read_sensor_values(_config(), _advertisement()) == []


def test_multiple_sensors_keep_config_order() -> None:
    """Each sensor is decoded from its own slot, in the order config lists them."""
    dynamic = bytearray(11)
    dynamic[7:10] = SHT40_BLOCK  # instance 0, at the default slot
    dynamic[4:7] = bytes.fromhex("f44106")  # instance 1, 0.0 C / 50.0 %RH
    adv = parse_advertisement(bytes(dynamic) + bytes([124, 139, 0x00]))
    config = _config(
        SensorData(instance_number=1, sensor_type=SensorType.SHT40, bus_id=1, msd_data_start_byte=4),
        SensorData(instance_number=0, sensor_type=SensorType.SHT40, bus_id=1, msd_data_start_byte=0),
    )

    readings = read_sensor_values(config, adv)

    assert [r.instance_number for r in readings] == [1, 0]
    assert readings[0].temperature_c == 0.0
    assert readings[1].temperature_c == 22.4
