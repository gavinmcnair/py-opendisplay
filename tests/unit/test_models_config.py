"""Test config model computed properties."""

import pytest

from opendisplay.models.config import BinaryInputs, DisplayConfig, ManufacturerData, PowerOption, SensorData
from opendisplay.models.enums import (
    BoardManufacturer,
    DIYBoardType,
    PowerMode,
    Rotation,
    SeeedBoardType,
    WaveshareBoardType,
)


def _power_option(power_mode: int, deep_sleep_time_seconds: int) -> PowerOption:
    return PowerOption(
        power_mode=power_mode,
        battery_capacity_mah=b"\x00\x00\x00",
        sleep_timeout_ms=0,
        tx_power=0,
        sleep_flags=0,
        battery_sense_pin=0xFF,
        battery_sense_enable_pin=0xFF,
        battery_sense_flags=0,
        capacity_estimator=0,
        voltage_scaling_factor=0,
        deep_sleep_current_ua=0,
        deep_sleep_time_seconds=deep_sleep_time_seconds,
        charge_enable_pin=0xFF,
        charge_state_pin=0xFF,
        charger_flags=0,
        min_wake_time_seconds=0,
        screen_timeout_seconds=0,
        reserved=b"\x00" * 10,
    )


def _display_config(active_width_mm: int, active_height_mm: int) -> DisplayConfig:
    return DisplayConfig(
        instance_number=0,
        display_technology=1,
        panel_ic_type=0,
        pixel_width=296,
        pixel_height=128,
        active_width_mm=active_width_mm,
        active_height_mm=active_height_mm,
        tag_type=0,
        rotation=0,
        reset_pin=0xFF,
        busy_pin=0xFF,
        dc_pin=0xFF,
        cs_pin=0xFF,
        data_pin=0,
        partial_update_support=1,
        color_scheme=0,
        transmission_modes=0,
        clk_pin=0,
        reserved_pins=b"\x00" * 7,
        full_update_mC=0,
        reserved=b"\x00" * 13,
    )


class TestDisplayConfigScreenDiagonal:
    """Test DisplayConfig.screen_diagonal_inches."""

    def test_returns_diagonal_inches_when_dimensions_set(self):
        display = _display_config(active_width_mm=120, active_height_mm=90)

        # 3-4-5 triangle: hypot(120, 90) = 150mm
        assert display.screen_diagonal_inches == pytest.approx(150 / 25.4)

    def test_returns_none_when_width_unset(self):
        display = _display_config(active_width_mm=0, active_height_mm=90)

        assert display.screen_diagonal_inches is None

    def test_returns_none_when_height_unset(self):
        display = _display_config(active_width_mm=120, active_height_mm=0)

        assert display.screen_diagonal_inches is None


class TestDisplayConfigColorScheme:
    """Test DisplayConfig.color_scheme_enum."""

    def test_returns_enum_for_known_color_scheme(self):
        display = _display_config(active_width_mm=120, active_height_mm=90)
        display.color_scheme = 0

        assert display.color_scheme_enum.name == "MONO"

    def test_returns_raw_int_for_unknown_color_scheme(self):
        display = _display_config(active_width_mm=120, active_height_mm=90)
        display.color_scheme = 99

        assert display.color_scheme_enum == 99


class TestManufacturerDataBoardTyping:
    """Test ManufacturerData board typing and names."""

    def _mfg(self, manufacturer_id: int, board_type: int) -> ManufacturerData:
        return ManufacturerData(
            manufacturer_id=manufacturer_id,
            board_type=board_type,
            board_revision=1,
            reserved=b"\x00" * 18,
        )

    def test_seeed_board_type_enum_and_name(self):
        mfg = self._mfg(BoardManufacturer.SEEED, 1)
        assert mfg.board_type_enum == SeeedBoardType.EN04
        assert mfg.board_type_name == "EN04"

    def test_diy_board_type_enum_and_name(self):
        mfg = self._mfg(BoardManufacturer.DIY, 0)
        assert mfg.board_type_enum == DIYBoardType.CUSTOM
        assert mfg.board_type_name == "Custom"

    def test_waveshare_board_type_enum_and_name(self):
        mfg = self._mfg(BoardManufacturer.WAVESHARE, 0)
        assert mfg.board_type_enum == WaveshareBoardType.ESP32_S3_PHOTOPAINTER
        assert mfg.board_type_name == "PhotoPainter"

    def test_unknown_board_type_falls_back_to_int(self):
        mfg = self._mfg(BoardManufacturer.SEEED, 99)
        assert mfg.board_type_enum == 99
        assert mfg.board_type_name is None


class TestPowerOptionDeepSleepEnabled:
    """Test PowerOption.deep_sleep_enabled (mirrors firmware sleep-entry condition)."""

    def test_enabled_when_battery_and_positive_interval(self):
        power = _power_option(power_mode=PowerMode.BATTERY, deep_sleep_time_seconds=300)
        assert power.deep_sleep_enabled is True

    def test_disabled_when_interval_zero(self):
        power = _power_option(power_mode=PowerMode.BATTERY, deep_sleep_time_seconds=0)
        assert power.deep_sleep_enabled is False

    def test_disabled_when_not_battery(self):
        power = _power_option(power_mode=PowerMode.USB, deep_sleep_time_seconds=300)
        assert power.deep_sleep_enabled is False

    def test_disabled_when_usb_and_zero_interval(self):
        power = _power_option(power_mode=PowerMode.USB, deep_sleep_time_seconds=0)
        assert power.deep_sleep_enabled is False


class TestDisplayConfigTransmissionModes:
    """Test DisplayConfig.supports_zip from transmission_modes bitfield."""

    def _display(self, transmission_modes: int) -> DisplayConfig:
        d = _display_config(active_width_mm=120, active_height_mm=90)
        d.transmission_modes = transmission_modes
        return d

    def test_supports_zip_true_when_bit_set(self):
        assert self._display(transmission_modes=0x02).supports_zip is True

    def test_supports_zip_false_when_no_bits_set(self):
        assert self._display(transmission_modes=0x00).supports_zip is False

    def test_supports_zip_false_when_only_raw_bit_set(self):
        assert self._display(transmission_modes=0x01).supports_zip is False

    def test_supports_zip_true_with_multiple_bits_set(self):
        assert self._display(transmission_modes=0x03).supports_zip is True


class TestBinaryInputsPublishedButtonByteIndex:
    """0xFF means 'not published'; the firmware also ignores indices past the block."""

    def _inputs(self, button_data_byte_index: int) -> BinaryInputs:
        return BinaryInputs(
            instance_number=0,
            input_type=1,
            display_as=1,
            reserved_pins=b"\x00" * 8,
            input_flags=0x01,
            invert=0,
            pullups=0,
            pulldowns=0,
            button_data_byte_index=button_data_byte_index,
        )

    def test_returns_index_when_published(self) -> None:
        assert self._inputs(0).published_button_byte_index == 0

    def test_returns_highest_valid_index(self) -> None:
        assert self._inputs(10).published_button_byte_index == 10

    def test_returns_none_for_not_published(self) -> None:
        assert self._inputs(0xFF).published_button_byte_index is None

    def test_returns_none_for_index_past_block(self) -> None:
        assert self._inputs(11).published_button_byte_index is None


class TestSensorDataMsdStartByte:
    """The firmware treats 0 and 0xFF as 'use the default slot' (sht40_msd_start)."""

    def test_zero_means_default_slot(self) -> None:
        sensor = SensorData(instance_number=0, sensor_type=4, bus_id=1, msd_data_start_byte=0)

        assert sensor.sht40_msd_start_byte == 7

    def test_ff_means_default_slot(self) -> None:
        sensor = SensorData(instance_number=0, sensor_type=4, bus_id=1, msd_data_start_byte=0xFF)

        assert sensor.sht40_msd_start_byte == 7

    def test_explicit_offset_is_kept(self) -> None:
        sensor = SensorData(instance_number=0, sensor_type=4, bus_id=1, msd_data_start_byte=3)

        assert sensor.sht40_msd_start_byte == 3


class TestBinaryInputsEnabledButtonIds:
    """input_flags is a bitmask over 8 pin slots; the bit position is the button id."""

    def _inputs(self, input_flags: int) -> BinaryInputs:
        return BinaryInputs(
            instance_number=0,
            input_type=1,
            display_as=1,
            reserved_pins=b"\x00" * 8,
            input_flags=input_flags,
            invert=0,
            pullups=0,
            pulldowns=0,
        )

    def test_no_slots_populated(self) -> None:
        assert self._inputs(0x00).enabled_button_ids == ()

    def test_single_slot(self) -> None:
        assert self._inputs(0x01).enabled_button_ids == (0,)

    def test_all_slots(self) -> None:
        assert self._inputs(0xFF).enabled_button_ids == (0, 1, 2, 3, 4, 5, 6, 7)

    def test_sparse_mask_keeps_bit_positions(self) -> None:
        """Ids are bit positions, not a count: gaps must not renumber the buttons."""
        assert self._inputs(0b1010_0100).enabled_button_ids == (2, 5, 7)

    def test_highest_bit_is_button_seven(self) -> None:
        """The report byte carries a 3-bit id, so 7 is the last addressable slot."""
        assert self._inputs(0x80).enabled_button_ids == (7,)

    @pytest.mark.parametrize("flags", [0x00, 0x01, 0x0F, 0x55, 0xAA, 0xFF])
    def test_agrees_with_an_explicit_bit_scan(self, flags: int) -> None:
        expected = tuple(bit for bit in range(8) if flags & (1 << bit))
        assert self._inputs(flags).enabled_button_ids == expected


class TestDisplayConfigCanvasSize:
    """Axis order follows the combined device + caller rotation."""

    def _display(self, rotation: int) -> DisplayConfig:
        return DisplayConfig(
            instance_number=0,
            display_technology=1,
            panel_ic_type=0,
            pixel_width=296,
            pixel_height=128,
            active_width_mm=0,
            active_height_mm=0,
            tag_type=0,
            rotation=rotation,
            reset_pin=0xFF,
            busy_pin=0xFF,
            dc_pin=0xFF,
            cs_pin=0xFF,
            data_pin=0,
            partial_update_support=1,
            color_scheme=0,
            transmission_modes=0,
            clk_pin=0,
            reserved_pins=b"\x00" * 7,
            full_update_mC=0,
            reserved=b"\x00" * 13,
        )

    def test_unrotated_panel_uses_its_own_dimensions(self) -> None:
        assert self._display(0).canvas_size() == (296, 128)

    @pytest.mark.parametrize("degrees", [90, 270])
    def test_quarter_turns_transpose(self, degrees: int) -> None:
        assert self._display(0).canvas_size(degrees) == (128, 296)

    @pytest.mark.parametrize("degrees", [0, 180, 360])
    def test_half_turns_do_not_transpose(self, degrees: int) -> None:
        assert self._display(0).canvas_size(degrees) == (296, 128)

    def test_device_rotation_alone_transposes(self) -> None:
        """A panel mounted sideways needs a transposed canvas with no caller rotation."""
        assert self._display(90).canvas_size() == (128, 296)

    def test_rotations_combine_rather_than_override(self) -> None:
        """90 on top of 90 is 180, which is back to the panel's own axis order."""
        assert self._display(90).canvas_size(90) == (296, 128)
        assert self._display(90).canvas_size(180) == (128, 296)
        assert self._display(180).canvas_size(180) == (296, 128)

    def test_accepts_a_rotation_enum(self) -> None:
        assert self._display(0).canvas_size(Rotation.ROTATE_90) == (128, 296)
        assert self._display(0).canvas_size(Rotation.ROTATE_0) == (296, 128)

    def test_unknown_stored_rotation_is_treated_as_zero(self) -> None:
        """A config carrying a rotation the library cannot decode must still render."""
        assert self._display(200).canvas_size() == (296, 128)
        assert self._display(200).canvas_size(90) == (128, 296)
