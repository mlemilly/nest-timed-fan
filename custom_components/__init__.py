"""Support for Nest devices."""

from __future__ import annotations

import logging

from aiohttp import ClientError
from google_nest_sdm.device import Device
from google_nest_sdm.device_manager import DeviceManager
from google_nest_sdm.exceptions import (
    ApiException,
    AuthException,
    ConfigurationException,
    SubscriberException,
    SubscriberTimeoutException,
)
import voluptuous as vol

from homeassistant.const import (
    CONF_BINARY_SENSORS,
    CONF_CLIENT_ID,
    CONF_CLIENT_SECRET,
    CONF_MONITORED_CONDITIONS,
    CONF_SENSORS,
    CONF_STRUCTURE,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import Event, HomeAssistant, callback, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    OAuth2TokenRequestError,
    OAuth2TokenRequestReauthError,
)
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
)
from homeassistant.helpers.typing import ConfigType

from . import api
from .const import (
    CONF_CLOUD_PROJECT_ID,
    CONF_PROJECT_ID,
    CONF_SUBSCRIBER_ID,
    CONF_SUBSCRIBER_ID_IMPORTED,
    CONF_SUBSCRIPTION_NAME,
    DATA_SDM,
    DOMAIN,
    SERVICE_SET_FAN_TIMER,
    ATTR_ENTITY_ID,
    ATTR_DURATION,
    ATTR_FAN_MODE,
)
from .types import DevicesAddedListener, NestConfigEntry, NestData

_LOGGER = logging.getLogger(__name__)


SENSOR_SCHEMA = vol.Schema(
    {vol.Optional(CONF_MONITORED_CONDITIONS): vol.All(cv.ensure_list)}
)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Required(CONF_CLIENT_ID): cv.string,
                vol.Required(CONF_CLIENT_SECRET): cv.string,
                # Required to use the new API (optional for compatibility)
                vol.Optional(CONF_PROJECT_ID): cv.string,
                vol.Optional(CONF_SUBSCRIBER_ID): cv.string,
                # Config that only currently works on the old API
                vol.Optional(CONF_STRUCTURE): vol.All(cv.ensure_list, [cv.string]),
                vol.Optional(CONF_SENSORS): SENSOR_SCHEMA,
                vol.Optional(CONF_BINARY_SENSORS): SENSOR_SCHEMA,
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)

# Platforms for SDM API - only climate and sensors
PLATFORMS = [Platform.CLIMATE, Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up Nest integration."""
    return True


async def _async_set_fan_timer_service(service_call: ServiceCall) -> None:
    """Handle set fan timer service call."""
    from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
    from homeassistant.helpers.entity_component import EntityComponent

    entity_id = service_call.data.get(ATTR_ENTITY_ID)
    duration = service_call.data.get(ATTR_DURATION)
    fan_mode = service_call.data.get(ATTR_FAN_MODE)

    if not entity_id or fan_mode is None:
        _LOGGER.warning("Missing entity_id or fan_mode in service call")
        return

    hass = service_call.hass

    # Get the climate component and find the entity
    climate_component: EntityComponent = hass.data.get(CLIMATE_DOMAIN)
    if climate_component is None:
        _LOGGER.warning("Climate component not found")
        return

    # Find the entity with the matching entity_id
    entity = None
    for entity_obj in climate_component.entities:
        if entity_obj.entity_id == entity_id:
            entity = entity_obj
            break

    if entity is None or not hasattr(entity, "async_set_fan_timer"):
        _LOGGER.warning(
            "Entity %s not found or does not support set_fan_timer service", entity_id
        )
        return

    try:
        await entity.async_set_fan_timer(fan_mode, duration=duration)
    except (ValueError, HomeAssistantError) as err:
        _LOGGER.error("Error calling set_fan_timer service: %s", err)


class SignalUpdateCallback:
    """Handle device updates from the subscriber."""

    def __init__(self, hass: HomeAssistant, config_entry: NestConfigEntry) -> None:
        """Initialize callback handler."""
        self._hass = hass
        self._config_entry = config_entry
        self._device_listeners: list[DevicesAddedListener] = []
        self._known_devices: dict[str, Device] = {}

    def set_device_manager(self, device_manager: DeviceManager) -> None:
        """Set the device manager and register for device changes."""
        device_manager.set_change_callback(self._devices_updated_cb)
        self._update_devices(device_manager.devices)

    async def _devices_updated_cb(self) -> None:
        """Handle callback when devices are updated."""
        _LOGGER.debug("Devices updated callback invoked")
        if self._config_entry.runtime_data is None:
            return
        device_manager = self._config_entry.runtime_data.device_manager
        if device_manager is None:
            return
        self._update_devices(device_manager.devices)

    def register_devices_listener(self, listener: DevicesAddedListener) -> None:
        """Add a listener for device changes."""
        self._device_listeners.append(listener)
        listener(list(self._known_devices.values()))

    def _update_devices(self, devices: dict[str, Device]) -> None:
        """Update devices and notify listeners of changes."""
        added_devices = []
        for device_id, device in devices.items():
            if device_id in self._known_devices:
                continue
            added_devices.append(device)
            self._known_devices[device_id] = device

        if added_devices:
            _LOGGER.debug("Adding new devices: %s", added_devices)
            for listener in self._device_listeners:
                listener(added_devices)

        # Remove any device entries that are no longer present
        device_registry = dr.async_get(self._hass)
        device_entries = dr.async_entries_for_config_entry(
            device_registry, self._config_entry.entry_id
        )
        for device_entry in device_entries:
            device_id = next(iter(device_entry.identifiers))[1]
            if device_id in devices:
                continue
            _LOGGER.info("Removing stale device entry '%s'", device_id)
            device_registry.async_update_device(
                device_id=device_entry.id,
                remove_config_entry_id=self._config_entry.entry_id,
            )


async def async_setup_entry(hass: HomeAssistant, entry: NestConfigEntry) -> bool:
    """Set up Nest from a config entry with dispatch between old/new flows."""
    if DATA_SDM not in entry.data:
        hass.async_create_task(hass.config_entries.async_remove(entry.entry_id))
        return False

    if entry.unique_id != entry.data[CONF_PROJECT_ID]:
        hass.config_entries.async_update_entry(
            entry, unique_id=entry.data[CONF_PROJECT_ID]
        )

    auth = await api.new_auth(hass, entry)
    try:
        await auth.async_get_access_token()
    except OAuth2TokenRequestReauthError as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN, translation_key="reauth_required"
        ) from err
    except OAuth2TokenRequestError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="auth_server_error"
        ) from err
    except ClientError as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN, translation_key="auth_client_error"
        ) from err

    subscriber = await api.new_subscriber(hass, entry, auth)
    if not subscriber:
        return False

    update_callback = SignalUpdateCallback(hass, entry)

    try:
        unsub = await subscriber.start_async()
    except AuthException as err:
        raise ConfigEntryAuthFailed(
            translation_domain=DOMAIN,
            translation_key="reauth_required",
        ) from err
    except ConfigurationException as err:
        _LOGGER.error("Configuration error: %s", err)
        return False
    except SubscriberTimeoutException as err:
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="subscriber_timeout",
        ) from err
    except SubscriberException as err:
        _LOGGER.error("Subscriber error: %s", err)
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="subscriber_error",
        ) from err

    try:
        device_manager = await subscriber.async_get_device_manager()
    except ApiException as err:
        unsub()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="device_api_error",
        ) from err

    @callback
    def on_hass_stop(_: Event) -> None:
        """Close connection when hass stops."""
        unsub()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, on_hass_stop)
    )

    update_callback.set_device_manager(device_manager)

    entry.async_on_unload(unsub)
    entry.runtime_data = NestData(
        subscriber=subscriber,
        device_manager=device_manager,
        register_devices_listener=update_callback.register_devices_listener,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register the set_fan_timer service
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_FAN_TIMER,
        _async_set_fan_timer_service,
        schema=vol.Schema(
            {
                vol.Required(ATTR_ENTITY_ID): cv.entity_id,
                vol.Required(ATTR_FAN_MODE): cv.string,
                vol.Optional(ATTR_DURATION): cv.positive_int,
            }
        ),
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: NestConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: NestConfigEntry) -> None:
    """Handle removal of pubsub subscriptions created during config flow."""
    if (
        DATA_SDM not in entry.data
        or not (
            CONF_SUBSCRIPTION_NAME in entry.data or CONF_SUBSCRIBER_ID in entry.data
        )
        or CONF_SUBSCRIBER_ID_IMPORTED in entry.data
    ):
        return
    if (subscription_name := entry.data.get(CONF_SUBSCRIPTION_NAME)) is None:
        subscription_name = entry.data[CONF_SUBSCRIBER_ID]
    admin_client = api.new_pubsub_admin_client(
        hass,
        access_token=entry.data["token"]["access_token"],
        cloud_project_id=entry.data[CONF_CLOUD_PROJECT_ID],
    )
    _LOGGER.debug("Deleting subscription '%s'", subscription_name)
    try:
        await admin_client.delete_subscription(subscription_name)
    except ApiException as err:
        _LOGGER.warning(
            (
                "Unable to delete subscription '%s'; Will be automatically cleaned up"
                " by cloud console: %s"
            ),
            subscription_name,
            err,
        )
