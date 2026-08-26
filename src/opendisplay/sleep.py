"""When a deep-sleeping OpenDisplay device is reachable.

A battery device configured for deep sleep is dark most of the time: it wakes on
a timer, advertises for a short window, and returns to sleep if nothing talks to
it. A host that wants to reach one has to know how long that window is and
whether it has already closed, and every host that reimplements those rules
drifts from the firmware independently.

:class:`SleepModel` holds only what the *device* determines. Host policy - how
many missed wakes to tolerate before calling a device unavailable, how long to
keep queued work, how much slack to allow for scanner latency - stays with the
host, which is why :meth:`SleepModel.probably_asleep` takes ``slack`` as an
argument rather than baking a value in.

Verified against firmware ``upstream/main``: ``DEFAULT_IDLE_HOLD_MS`` in
``src/main.h`` and the sleep-entry condition in ``platformIdle()``
(``src/main.cpp``). Deep sleep is an ESP32 behaviour; nRF targets idle at their
configured cadence instead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Final

from .models.config import GlobalConfig, PowerOption

#: Quiet window the firmware holds open when ``sleep_timeout_ms`` is 0, in
#: milliseconds (``DEFAULT_IDLE_HOLD_MS`` in the firmware's ``src/main.h``).
DEFAULT_WAKE_WINDOW_MS: Final = 10_000


@dataclass(frozen=True, slots=True)
class SleepModel:
    """Device-derived deep-sleep timing for one device.

    Build with :meth:`from_config` or :meth:`from_power` rather than
    constructing directly, so the fields stay consistent with the device config
    they came from.
    """

    #: Whether the device is configured to deep sleep at all.
    is_deep_sleeping: bool
    #: Timer-wake interval in seconds; 0 when deep sleep is off.
    deep_sleep_time_seconds: int
    #: Configured quiet window in milliseconds; 0 means "use the firmware default".
    sleep_timeout_ms: int

    @classmethod
    def from_power(cls, power: PowerOption) -> SleepModel:
        """Build from a device's power configuration."""
        return cls(
            is_deep_sleeping=power.deep_sleep_enabled,
            deep_sleep_time_seconds=power.deep_sleep_time_seconds,
            sleep_timeout_ms=power.sleep_timeout_ms,
        )

    @classmethod
    def from_config(cls, config: GlobalConfig) -> SleepModel:
        """Build from a full device config, read via ``OpenDisplayDevice.config``."""
        return cls.from_power(config.power)

    @property
    def wake_window_s(self) -> float:
        """Quiet window after a wake, in seconds, before the device sleeps again.

        Falls back to the firmware default when ``sleep_timeout_ms`` is 0, which
        is what the firmware itself does.
        """
        return (self.sleep_timeout_ms or DEFAULT_WAKE_WINDOW_MS) / 1000.0

    def probably_asleep(
        self,
        last_seen: float | None,
        now: float | None = None,
        slack: float = 0.0,
    ) -> bool:
        """Return True if the device has almost certainly gone back to sleep.

        A sleeping device advertises only while its window is open, so an
        advertisement older than one window means the window has closed and a
        connect attempt would spend a full retry budget on a dark radio.

        This is a *prediction, not a fact*, and it errs towards "asleep" in two
        known ways. The firmware measures the window from the last activity
        rather than from the wake, so a client that connects and drops re-arms
        the whole window; and a button wake holds the device up for at least
        ``min_wake_time_seconds`` regardless. In both cases the device may still
        be reachable when this returns True. Callers that can afford one cheap
        connect attempt should prefer trying over trusting this.

        The test is pure freshness and does not consult ``is_deep_sleeping``:
        callers combine the two where the distinction matters, since a mains
        powered device is never "asleep" in this sense.

        Args:
            last_seen: Wall-clock timestamp of the most recent advertisement, or
                None if the device has never been seen - which counts as asleep.
            now: Wall-clock override, for tests.
            slack: Extra seconds to tolerate on top of the window, for host-side
                latency between the device transmitting and the host recording
                it (scanners, Bluetooth proxies). Defaults to none.
        """
        if last_seen is None:
            return True
        current = time.time() if now is None else now
        return (current - last_seen) > (self.wake_window_s + slack)

    def next_expected_wake(self, last_seen: float | None) -> float | None:
        """Wall-clock time of the next timer wake, or None if not predictable.

        Returns None when the device does not deep sleep or has never been seen.
        Assumes the device slept immediately after ``last_seen``, so one whose
        window was extended by activity wakes slightly later than predicted.
        """
        if last_seen is None or not self.is_deep_sleeping:
            return None
        return last_seen + self.deep_sleep_time_seconds
