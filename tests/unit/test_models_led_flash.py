"""Test typed LED flash config model."""

import pytest

from opendisplay.models.led_flash import (
    LedFlashConfig,
    LedFlashStep,
    ms_to_inter_delay_units,
    ms_to_loop_delay_units,
    pack_led_color,
    unpack_led_color,
)


def test_led_flash_config_to_bytes_and_from_bytes_roundtrip() -> None:
    cfg = LedFlashConfig(
        mode=1,
        brightness=8,
        step1=LedFlashStep(color=0xE0, flash_count=2, loop_delay_units=2, inter_delay_units=5),
        step2=LedFlashStep(color=0x1C, flash_count=3, loop_delay_units=4, inter_delay_units=7),
        step3=LedFlashStep(color=0x03, flash_count=1, loop_delay_units=6, inter_delay_units=9),
        group_repeats=4,
        reserved=0xAA,
    )

    raw = cfg.to_bytes()

    assert raw == bytes(
        [
            0x71,
            0xE0,
            0x22,
            0x05,
            0x1C,
            0x43,
            0x07,
            0x03,
            0x61,
            0x09,
            0x03,
            0xAA,
        ]
    )
    assert LedFlashConfig.from_bytes(raw) == cfg


def test_led_flash_config_single_helper() -> None:
    cfg = LedFlashConfig.single(
        color=0xE0,
        flash_count=2,
        loop_delay_units=1,
        inter_delay_units=4,
        brightness=10,
        group_repeats=2,
    )

    raw = cfg.to_bytes()
    assert raw[0] == 0x91  # brightness 10 -> raw 9, mode 1
    assert raw[1] == 0xE0
    assert raw[2] == 0x12
    assert raw[3] == 0x04
    assert raw[10] == 0x01  # group repeats 2 -> encoded 1


def test_led_flash_config_supports_infinite_group_repeats() -> None:
    cfg = LedFlashConfig(group_repeats=None)
    raw = cfg.to_bytes()
    assert raw[10] == 0xFE
    parsed = LedFlashConfig.from_bytes(raw)
    assert parsed.group_repeats is None


def test_led_flash_config_rejects_invalid_values() -> None:
    with pytest.raises(ValueError, match="brightness out of range"):
        LedFlashConfig(brightness=0)

    with pytest.raises(ValueError, match="group_repeats out of range"):
        LedFlashConfig(group_repeats=0)

    with pytest.raises(ValueError, match="color out of range"):
        LedFlashStep(color=256)


def test_group_repeats_255_rejected_to_avoid_infinite_sentinel() -> None:
    # 255 would encode to raw 0xFE (the infinite sentinel) and loop forever (M11).
    with pytest.raises(ValueError, match="group_repeats out of range"):
        LedFlashConfig(group_repeats=255)


def test_group_repeats_254_is_max_finite() -> None:
    cfg = LedFlashConfig(group_repeats=254)
    assert cfg.to_bytes()[10] == 253  # raw = group_repeats - 1


def test_from_bytes_accepts_raw_0xff_without_raising() -> None:
    payload = bytes([0x70, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0xFF, 0])
    cfg = LedFlashConfig.from_bytes(payload)  # must not raise
    assert cfg.group_repeats is None


def test_pack_led_color_saturates_each_channel() -> None:
    """Full-scale input maps to each channel's maximum: 3 bits, 3 bits, 2 bits."""
    assert pack_led_color(255, 0, 0) == 0b111_000_00
    assert pack_led_color(0, 255, 0) == 0b000_111_00
    assert pack_led_color(0, 0, 255) == 0b000_000_11
    assert pack_led_color(255, 255, 255) == 0xFF
    assert pack_led_color(0, 0, 0) == 0x00


def test_pack_led_color_rejects_out_of_range_channels() -> None:
    for bad in ((256, 0, 0), (0, -1, 0), (0, 0, 999)):
        with pytest.raises(ValueError):
            pack_led_color(*bad)


def test_packed_colors_survive_a_round_trip_through_the_wire_format() -> None:
    """Every representable colour byte must unpack and repack to itself.

    Exhaustive over all 256 values, which is the whole space: this pins the
    channel bit widths and their order, so a change to either fails here rather
    than silently lighting the wrong colour.
    """
    for packed in range(256):
        assert pack_led_color(*unpack_led_color(packed)) == packed


def test_packed_color_survives_a_step_round_trip() -> None:
    """A packed colour must reach the device unchanged through the real payload."""
    packed = pack_led_color(255, 128, 64)
    cfg = LedFlashConfig(step1=LedFlashStep(color=packed, flash_count=1))
    assert LedFlashConfig.from_bytes(cfg.to_bytes()).step1.color == packed


@pytest.mark.parametrize(
    ("ms", "expected"),
    [(0, 0), (100, 1), (150, 2), (1500, 15), (1600, 15), (99_999, 15)],
)
def test_ms_to_loop_delay_units_convert_and_clamp(ms: int, expected: int) -> None:
    """The field is a nibble, so out-of-range delays clamp rather than raise."""
    assert ms_to_loop_delay_units(ms) == expected


@pytest.mark.parametrize(
    ("ms", "expected"),
    [(0, 0), (100, 1), (25_500, 255), (26_000, 255), (99_999_999, 255)],
)
def test_ms_to_inter_delay_units_convert_and_clamp(ms: int, expected: int) -> None:
    assert ms_to_inter_delay_units(ms) == expected


def test_delay_units_are_accepted_by_the_step_model() -> None:
    """The converters must land inside the ranges LedFlashStep validates."""
    step = LedFlashStep(
        color=pack_led_color(0, 0, 255),
        loop_delay_units=ms_to_loop_delay_units(99_999),
        inter_delay_units=ms_to_inter_delay_units(99_999_999),
    )
    assert step.loop_delay_units == 15
    assert step.inter_delay_units == 255


class TestLedFlashStepFromRgb:
    """The human-facing constructor: RGB and milliseconds, not wire encodings."""

    def test_converts_colour_and_delays(self) -> None:
        step = LedFlashStep.from_rgb((255, 0, 0), flash_count=2, loop_delay_ms=300, inter_delay_ms=1000)
        assert step.color == pack_led_color(255, 0, 0)
        assert step.flash_count == 2
        assert step.loop_delay_units == 3
        assert step.inter_delay_units == 10

    def test_matches_the_equivalent_manual_construction(self) -> None:
        """from_rgb must be sugar, not a second encoding path."""
        assert LedFlashStep.from_rgb(
            (10, 200, 90), flash_count=3, loop_delay_ms=500, inter_delay_ms=200
        ) == LedFlashStep(
            color=pack_led_color(10, 200, 90),
            flash_count=3,
            loop_delay_units=ms_to_loop_delay_units(500),
            inter_delay_units=ms_to_inter_delay_units(200),
        )

    def test_out_of_range_delays_clamp_rather_than_raise(self) -> None:
        """A too-long delay should still blink, not refuse to build a step."""
        step = LedFlashStep.from_rgb((0, 0, 255), loop_delay_ms=99_999, inter_delay_ms=99_999_999)
        assert step.loop_delay_units == 15
        assert step.inter_delay_units == 255

    def test_rejects_an_out_of_range_channel(self) -> None:
        with pytest.raises(ValueError):
            LedFlashStep.from_rgb((256, 0, 0))

    def test_rgb_property_round_trips_through_the_wire_format(self) -> None:
        step = LedFlashStep.from_rgb((255, 255, 255))
        assert step.rgb == (255, 255, 255)
        assert LedFlashStep.from_rgb(step.rgb).color == step.color

    def test_survives_a_full_payload_round_trip(self) -> None:
        cfg = LedFlashConfig(step1=LedFlashStep.from_rgb((255, 128, 0), flash_count=2, loop_delay_ms=200))
        decoded = LedFlashConfig.from_bytes(cfg.to_bytes()).step1
        assert decoded == cfg.step1
        assert decoded.rgb == cfg.step1.rgb
