"""Test the deep-sleep timing model."""

import pytest

from opendisplay.models.enums import PowerMode
from opendisplay.sleep import DEFAULT_WAKE_WINDOW_MS, SleepModel


def test_deep_sleep_needs_battery_power_and_an_interval(power_option) -> None:
    """Mirrors the firmware's platformIdle() gate: battery mode and a non-zero timer."""
    assert SleepModel.from_power(power_option()).is_deep_sleeping is True
    assert SleepModel.from_power(power_option(power_mode=int(PowerMode.USB))).is_deep_sleeping is False
    assert SleepModel.from_power(power_option(deep_sleep_time_seconds=0)).is_deep_sleeping is False


def test_wake_window_falls_back_to_the_firmware_default(power_option) -> None:
    """sleep_timeout_ms == 0 means "use DEFAULT_IDLE_HOLD_MS", as the firmware does."""
    model = SleepModel.from_power(power_option(sleep_timeout_ms=0))
    assert model.wake_window_s == DEFAULT_WAKE_WINDOW_MS / 1000.0
    assert model.wake_window_s == 10.0


def test_wake_window_honours_a_configured_value(power_option) -> None:
    assert SleepModel.from_power(power_option(sleep_timeout_ms=40_000)).wake_window_s == 40.0


def test_never_seen_counts_as_asleep(power_option) -> None:
    model = SleepModel.from_power(power_option())
    assert model.probably_asleep(None) is True
    assert model.probably_asleep(None, now=1_000.0) is True


def test_freshness_is_measured_against_the_wake_window(power_option) -> None:
    model = SleepModel.from_power(power_option(sleep_timeout_ms=10_000))
    now = 1_000.0
    assert model.probably_asleep(now - 5.0, now=now) is False
    assert model.probably_asleep(now - 9.9, now=now) is False
    # Exactly one window still counts as awake; strictly older does not.
    assert model.probably_asleep(now - 10.0, now=now) is False
    assert model.probably_asleep(now - 10.1, now=now) is True


def test_slack_extends_the_freshness_horizon(power_option) -> None:
    """Host-side latency is the caller's to declare, not the model's to assume."""
    model = SleepModel.from_power(power_option(sleep_timeout_ms=10_000))
    now = 1_000.0
    assert model.probably_asleep(now - 12.0, now=now) is True
    assert model.probably_asleep(now - 12.0, now=now, slack=5.0) is False
    assert model.probably_asleep(now - 15.1, now=now, slack=5.0) is True


def test_freshness_does_not_consult_is_deep_sleeping(power_option) -> None:
    """probably_asleep is a pure freshness test; callers combine it themselves.

    A mains-powered device is never asleep in this sense, but the model does not
    silently make that decision on the caller's behalf.
    """
    mains = SleepModel.from_power(power_option(power_mode=int(PowerMode.USB)))
    assert mains.is_deep_sleeping is False
    assert mains.probably_asleep(1_000.0 - 60.0, now=1_000.0) is True


def test_next_expected_wake_is_one_interval_after_the_last_sighting(power_option) -> None:
    model = SleepModel.from_power(power_option(deep_sleep_time_seconds=300))
    assert model.next_expected_wake(1_000.0) == 1_300.0


@pytest.mark.parametrize(
    ("power_mode", "interval"),
    [
        (int(PowerMode.USB), 300),  # not a deep sleeper
        (int(PowerMode.BATTERY), 0),  # no interval to predict from
    ],
)
def test_next_expected_wake_is_unpredictable_without_deep_sleep(power_option, power_mode: int, interval: int) -> None:
    model = SleepModel.from_power(power_option(power_mode=power_mode, deep_sleep_time_seconds=interval))
    assert model.next_expected_wake(1_000.0) is None


def test_next_expected_wake_needs_a_sighting(power_option) -> None:
    assert SleepModel.from_power(power_option()).next_expected_wake(None) is None


def test_from_config_reads_the_power_section(power_option, global_config) -> None:
    """from_config is sugar for from_power on config.power, not a second code path."""
    power = power_option(sleep_timeout_ms=5_000, deep_sleep_time_seconds=120)
    config = global_config(power=power)
    assert SleepModel.from_config(config) == SleepModel.from_power(power)
    assert SleepModel.from_config(config).wake_window_s == 5.0
