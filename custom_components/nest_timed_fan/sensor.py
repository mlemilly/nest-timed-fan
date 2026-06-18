"""Support for Google Nest SDM sensors."""

from __future__ import annotations

from datetime import datetime
import logging

from google_nest_sdm.device import Device
from google_nest_sdm.device_traits import FanTrait, HumidityTrait, TemperatureTrait

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .device_info import NestDeviceInfo
from .types import NestConfigEntry

_LOGGER = logging.getLogger(__name__)


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
                entities.append(FanTimerRemainingSensor(device))
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


class FanTimerRemainingSensor(SensorBase):
    """Representation of a Fan Timer Remaining Sensor."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = "s"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, device: Device) -> None:
        """Initialize the sensor."""
        super().__init__(device)
        self._attr_unique_id = f"{device.name}-fan-timer-remaining"

    @property
    def available(self) -> bool:
        """Return the device availability and fan timer status."""
        if not self._device_info.available:
            return False
        # Only available if fan is on and timer is set
        if FanTrait.NAME not in self._device.traits:
            return False
        trait: FanTrait = self._device.traits[FanTrait.NAME]
        return trait.timer_mode == "ON" and trait.timer_end_time is not None

    @property
    def native_value(self) -> int | None:
        """Return the remaining duration in seconds."""
        if FanTrait.NAME not in self._device.traits:
            return None
        
        trait: FanTrait = self._device.traits[FanTrait.NAME]
        
        # Check if fan timer is active and has an end time
        if trait.timer_mode != "ON" or not trait.timer_end_time:
            return None
        
        try:
            # Parse the ISO format datetime string
            end_time = datetime.fromisoformat(trait.timer_end_time.replace("Z", "+00:00"))
            current_time = datetime.now(end_time.tzinfo)
            remaining = (end_time - current_time).total_seconds()
            
            # Return remaining time in seconds, or 0 if timer has expired
            return max(0, int(remaining))
        except (ValueError, AttributeError, TypeError) as err:
            _LOGGER.error("Error parsing timer end time: %s", err)
            return None

    @property
    def name(self) -> str:
        """Return the name of the sensor."""
        return "Fan Timer Remaining"
