"""Unit tests for native fan runtime sensor behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


def _install_test_stubs() -> None:
    """Install minimal stubs so sensor module can be imported without HA deps."""
    custom_components_pkg = ModuleType("custom_components")
    custom_components_pkg.__path__ = ["custom_components"]
    sys.modules["custom_components"] = custom_components_pkg

    nest_pkg = ModuleType("custom_components.nest_timed_fan")
    nest_pkg.__path__ = ["custom_components/nest_timed_fan"]
    sys.modules["custom_components.nest_timed_fan"] = nest_pkg

    sensor_module = ModuleType("homeassistant.components.sensor")

    class _SensorDeviceClass:
        TEMPERATURE = "temperature"
        HUMIDITY = "humidity"
        DURATION = "duration"

    class _SensorStateClass:
        MEASUREMENT = "measurement"

    class _SensorEntity:
        def __init__(self) -> None:
            self.hass = object()

        @property
        def device_class(self) -> str | None:
            return getattr(self, "_attr_device_class", None)

        def async_on_remove(self, _: object) -> None:
            return None

        def async_write_ha_state(self) -> None:
            return None

    sensor_module.SensorDeviceClass = _SensorDeviceClass
    sensor_module.SensorEntity = _SensorEntity
    sensor_module.SensorStateClass = _SensorStateClass
    sys.modules["homeassistant.components.sensor"] = sensor_module

    const_module = ModuleType("homeassistant.const")
    const_module.PERCENTAGE = "%"
    const_module.UnitOfTemperature = SimpleNamespace(CELSIUS="°C")
    const_module.UnitOfTime = SimpleNamespace(MINUTES="min")
    sys.modules["homeassistant.const"] = const_module

    core_module = ModuleType("homeassistant.core")
    core_module.HomeAssistant = object
    sys.modules["homeassistant.core"] = core_module

    entity_platform_module = ModuleType("homeassistant.helpers.entity_platform")
    entity_platform_module.AddConfigEntryEntitiesCallback = object
    sys.modules["homeassistant.helpers.entity_platform"] = entity_platform_module

    event_module = ModuleType("homeassistant.helpers.event")
    event_module.async_track_time_interval = lambda *args, **kwargs: (lambda: None)
    sys.modules["homeassistant.helpers.event"] = event_module

    dt_module = ModuleType("homeassistant.util.dt")
    dt_module.utcnow = lambda: datetime.now(timezone.utc)
    dt_module.as_utc = lambda value: value.astimezone(timezone.utc)
    dt_module.parse_datetime = (
        lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    sys.modules["homeassistant.util.dt"] = dt_module

    util_module = ModuleType("homeassistant.util")
    util_module.dt = dt_module
    sys.modules["homeassistant.util"] = util_module

    google_device_module = ModuleType("google_nest_sdm.device")
    google_device_module.Device = object
    sys.modules["google_nest_sdm.device"] = google_device_module

    google_traits_module = ModuleType("google_nest_sdm.device_traits")
    google_traits_module.TemperatureTrait = SimpleNamespace(NAME="temperature")
    google_traits_module.HumidityTrait = SimpleNamespace(NAME="humidity")
    google_traits_module.FanTrait = SimpleNamespace(NAME="fan")
    sys.modules["google_nest_sdm.device_traits"] = google_traits_module

    device_info_module = ModuleType("custom_components.nest_timed_fan.device_info")

    class _NestDeviceInfo:
        def __init__(self, _: object) -> None:
            self.available = True
            self.device_info = {}

    device_info_module.NestDeviceInfo = _NestDeviceInfo
    sys.modules["custom_components.nest_timed_fan.device_info"] = device_info_module

    types_module = ModuleType("custom_components.nest_timed_fan.types")
    types_module.NestConfigEntry = object
    sys.modules["custom_components.nest_timed_fan.types"] = types_module


_install_test_stubs()

from custom_components.nest_timed_fan.sensor import FanRuntimeMinutesSensor


class FanRuntimeMinutesSensorTests(unittest.TestCase):
    """Tests for fan runtime sensor state and metadata."""

    def _make_sensor(self, trait: object) -> FanRuntimeMinutesSensor:
        device = SimpleNamespace(name="device-123", traits={"fan": trait})
        sensor = FanRuntimeMinutesSensor(device)
        sensor.hass = object()
        return sensor

    def test_runtime_increments_while_fan_on(self) -> None:
        now = datetime(2026, 6, 18, 4, 0, 0, tzinfo=timezone.utc)
        trait = SimpleNamespace(
            timer_mode="ON",
            timer_duration=3600,
            timer_end_time=now + timedelta(seconds=3600),
        )
        sensor = self._make_sensor(trait)

        with patch(
            "custom_components.nest_timed_fan.sensor.dt_util.utcnow",
            return_value=now + timedelta(seconds=120),
        ):
            sensor._refresh_runtime_state()
            self.assertEqual(sensor.native_value, 2.0)

    def test_runtime_resets_when_fan_off(self) -> None:
        now = datetime(2026, 6, 18, 4, 0, 0, tzinfo=timezone.utc)
        trait = SimpleNamespace(
            timer_mode="ON",
            timer_duration=1200,
            timer_end_time=now + timedelta(seconds=1200),
        )
        sensor = self._make_sensor(trait)

        with patch("custom_components.nest_timed_fan.sensor.dt_util.utcnow", return_value=now):
            sensor._refresh_runtime_state()

        trait.timer_mode = "OFF"
        sensor._refresh_runtime_state()
        self.assertEqual(sensor.native_value, 0)
        self.assertEqual(sensor.extra_state_attributes["elapsed_hh_mm_ss"], "00:00:00")

    def test_entity_metadata(self) -> None:
        trait = SimpleNamespace(timer_mode="OFF", timer_duration=None, timer_end_time=None)
        sensor = self._make_sensor(trait)

        self.assertEqual(sensor._attr_unique_id, "device-123-fan-runtime-minutes")
        self.assertEqual(sensor._attr_name, "Fan Runtime")
        self.assertEqual(sensor._attr_native_unit_of_measurement, "min")


if __name__ == "__main__":
    unittest.main()
