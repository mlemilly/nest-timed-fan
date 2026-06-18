"""Support for Google Nest SDM sensors."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from google_nest_sdm.device import Device
from google_nest_sdm.device_traits import FanTrait, HumidityTrait, TemperatureTrait

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .device_info import NestDeviceInfo
from .types import NestConfigEntry

_LOGGER = logging.getLogger(__name__)
FAN_RUNTIME_UPDATE_INTERVAL = timedelta(seconds=30)


DEVICE_TYPE_MAP = {
    "sdm.devices.types.CAMERA": "Camera",
    "sdm.devices.types.DISPLAY": "Display",
    "sdm.devices.types.DOORBELL": "Doorbell",
    "sdm.devices.types.THERMOSTAT": "Thermostat",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NestConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors."""

    def devices_added(devices: list[Device]) -> None:
        entities: list[SensorEntity] = []
        for device in devices:
            if TemperatureTrait.NAME in device.traits:
                entities.append(TemperatureSensor(device))
            if HumidityTrait.NAME in device.traits:
                entities.append(HumiditySensor(device))
            if FanTrait.NAME in device.traits:
                entities.append(FanRuntimeMinutesSensor(device))
        async_add_entities(entities)

    entry.runtime_data.register_devices_listener(devices_added)


class SensorBase(SensorEntity):
    """Representation of a dynamically updated Sensor."""

    _attr_should_poll = False
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_has_entity_name = True

    def __init__(self, device: Device) -> None:
        """Initialize the sensor."""
        self._device = device
        self._device_info = NestDeviceInfo(device)
        self._attr_unique_id = f"{device.name}-{self.device_class}"
        self._attr_device_info = self._device_info.device_info

    @property
    def available(self) -> bool:
        """Return the device availability."""
        return self._device_info.available

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to register update signal handler."""
        self.async_on_remove(
            self._device.add_update_listener(self.async_write_ha_state)
        )


class TemperatureSensor(SensorBase):
    """Representation of a Temperature Sensor."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> float:
        """Return the state of the sensor."""
        trait: TemperatureTrait = self._device.traits[TemperatureTrait.NAME]
        # Round for display purposes because the API returns 5 decimal places.
        # This can be removed if the SDM API issue is fixed, or a frontend
        # display fix is added for all integrations.
        return float(round(trait.ambient_temperature_celsius, 1))


class HumiditySensor(SensorBase):
    """Representation of a Humidity Sensor."""

    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_native_unit_of_measurement = PERCENTAGE

    @property
    def native_value(self) -> int:
        """Return the state of the sensor."""
        trait: HumidityTrait = self._device.traits[HumidityTrait.NAME]
        # Cast without loss of precision because the API always returns an integer.
        return int(trait.ambient_humidity_percent)


class FanRuntimeMinutesSensor(SensorBase):
    """Representation of fan runtime in minutes."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_icon = "mdi:fan-clock"
    _attr_name = "Fan Runtime"

    def __init__(self, device: Device) -> None:
        """Initialize the fan runtime sensor."""
        super().__init__(device)
        self._attr_unique_id = f"{device.name}-fan-runtime-minutes"
        self._runtime_started_at: datetime | None = None
        self._runtime_unsub: Any | None = None

    @property
    def native_value(self) -> float:
        """Return elapsed fan runtime in minutes while fan is on."""
        if not self._is_fan_on() or not self._runtime_started_at:
            return 0
        elapsed_seconds = max(
            int((dt_util.utcnow() - self._runtime_started_at).total_seconds()),
            0,
        )
        return round(elapsed_seconds / 60, 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return formatted elapsed runtime."""
        elapsed_seconds = 0
        if self._is_fan_on() and self._runtime_started_at:
            elapsed_seconds = max(
                int((dt_util.utcnow() - self._runtime_started_at).total_seconds()),
                0,
            )
        hours, remainder = divmod(elapsed_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return {
            "elapsed_hh_mm_ss": f"{hours:02}:{minutes:02}:{seconds:02}",
            "elapsed_seconds": elapsed_seconds,
        }

    async def async_added_to_hass(self) -> None:
        """Run when entity is added and register update handlers."""
        self.async_on_remove(self._device.add_update_listener(self._handle_device_update))
        self._refresh_runtime_state()

    async def async_will_remove_from_hass(self) -> None:
        """Stop periodic callbacks on entity removal."""
        self._stop_runtime_updates()

    def _handle_device_update(self) -> None:
        """Handle updates from the SDM device."""
        self._refresh_runtime_state()
        self.async_write_ha_state()

    def _handle_runtime_tick(self, _: datetime) -> None:
        """Write state while fan is on at a controlled cadence."""
        if not self._is_fan_on():
            self._stop_runtime_updates()
            return
        self.async_write_ha_state()

    def _refresh_runtime_state(self) -> None:
        """Refresh runtime tracking and update timer callbacks."""
        if not self._is_fan_on():
            self._runtime_started_at = None
            self._stop_runtime_updates()
            return

        self._runtime_started_at = self._resolve_runtime_started_at()
        if self._runtime_unsub is None:
            self._runtime_unsub = async_track_time_interval(
                self.hass, self._handle_runtime_tick, FAN_RUNTIME_UPDATE_INTERVAL
            )

    def _stop_runtime_updates(self) -> None:
        """Cancel runtime updates when fan is off."""
        if self._runtime_unsub:
            self._runtime_unsub()
            self._runtime_unsub = None

    def _is_fan_on(self) -> bool:
        """Return whether the fan timer is currently running."""
        trait: FanTrait = self._device.traits[FanTrait.NAME]
        return trait.timer_mode == "ON"

    def _resolve_runtime_started_at(self) -> datetime:
        """Determine runtime start from trait metadata when available."""
        trait: FanTrait = self._device.traits[FanTrait.NAME]
        timer_end_time = self._coerce_datetime(getattr(trait, "timer_end_time", None))
        timer_duration = self._coerce_duration_seconds(
            getattr(trait, "timer_duration", None)
        )
        if timer_end_time is not None and timer_duration is not None:
            return timer_end_time - timedelta(seconds=timer_duration)
        if self._runtime_started_at is not None:
            return self._runtime_started_at
        return dt_util.utcnow()

    def _coerce_datetime(self, value: Any) -> datetime | None:
        """Convert timer_end_time values into UTC datetimes."""
        if isinstance(value, datetime):
            return dt_util.as_utc(value)
        if isinstance(value, str):
            parsed_value = dt_util.parse_datetime(value)
            if parsed_value is not None:
                return dt_util.as_utc(parsed_value)
        return None

    def _coerce_duration_seconds(self, value: Any) -> int | None:
        """Convert timer duration values into integer seconds."""
        if isinstance(value, timedelta):
            return int(value.total_seconds())
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            raw_value = value.strip()
            if raw_value.endswith("s"):
                raw_value = raw_value[:-1]
            try:
                return int(float(raw_value))
            except ValueError:
                return None
        return None
