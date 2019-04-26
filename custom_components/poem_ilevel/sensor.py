"""
Support for displaying the current oil tank level.
For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/sensor.poem_ilevel/
"""
import asyncio
import json
import logging
import re

import aiohttp
import async_timeout
import voluptuous as vol

import homeassistant.helpers.config_validation as cv
from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (
    CONF_USERNAME, CONF_PASSWORD, ATTR_ATTRIBUTION)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util


_LOGGER = logging.getLogger(__name__)


ILEVEL = 'ilevel'
CONF_ATTRIBUTION = "Oil level measured with iLevel by Poem Technology."

RETRY_MINUTES = 10
REFRESH_MINUTES = 30
BASE_URL = 'https://myilevel.com/iLevel/'
LOGIN_URL = BASE_URL+'login/iLevel_login.php'
CLIENT_URL = BASE_URL+'ClientView.php'

ATTR_GALLONS = 'gallons'
ATTR_CAPACITY = 'capacity'
ATTR_INCHES = 'inches'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_USERNAME): cv.string,
    vol.Optional(CONF_PASSWORD): cv.string,
})

# pylint: disable=unused-argument
async def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    """Set up the poem_ilevel sensors."""
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)

    if None in (username, password):
        _LOGGER.error("%s or %s not set in Home Assistant config", CONF_USERNAME, CONF_PASSWORD)
        return False

    data = PoemData(hass, async_add_devices, username, password)
    async_call_later(hass, 1, data.async_refresh)

    return True


class PoemData(object): # pylint: disable=too-few-public-methods
    """Data Fetcher for poem_ilevel"""

    def __init__(self, hass, async_add_devices, username, password):
        """Initialize the data object"""
        self._hass = hass
        self._async_add_devices = async_add_devices
        self._username = username
        self._password = password
        self._client_id = ''
        self._backend_url = ''
        self._devices = {}

    async def async_refresh(self, *_):
        """Refresh data from the cloud"""

        def try_again(reason: str, err: str):
            """Retry later."""
            _LOGGER.warning("%s:Retrying in %i minutes: %s", reason, RETRY_MINUTES, err)
            async_call_later(self._hass, RETRY_MINUTES*60, self.async_refresh)

        async def async_login(websession):
            """Login"""
            # Post Login data
            try:
                data = aiohttp.FormData()
                data.add_field('username', self._username)
                data.add_field('pass', self._password)
                data.add_field('submit', "Log in")

                with async_timeout.timeout(10, loop=self._hass.loop):
                    resp = await websession.post(LOGIN_URL, data=data)

                if resp.status != 200:
                    try_again('POST LOGIN', '{} returned {}'.format(resp.url, resp.status))
                    return False

                text = await resp.text()

                #_LOGGER.info("POST LOGIN GOT %s", text)

                match = re.search(r"Invalid", text)
                if match is not None:
                    _LOGGER.error("Fatal. Invalid username or password!")
                    return False

                match = re.search(r"globals.clientID\s*=\s*(?P<clientId>\d+)", text)
                if match is None:
                    raise ValueError("Fatal. Invalid username or password?")

                self._client_id = match.group('clientId')

                match = re.search(r"globals.backendURL\s*=\s*'(?P<backendURL>.*?)';", text)
                if match is None:
                    raise ValueError("Fatal. Invalid username or password?")

                self._backend_url = BASE_URL + match.group('backendURL')

                _LOGGER.debug("POST LOGIN clientId =  %s @ %s", self._client_id, self._backend_url)
                return True

            except (asyncio.TimeoutError, aiohttp.ClientError) as err:
                try_again('POST LOGIN', err)
                return False

            except (ValueError) as value_error:
                _LOGGER.error(value_error)
                return False

        async def async_get_tank_list(websession):
            """Call the gcvt method to get the list of tanks"""

            try:
                data = aiohttp.FormData()
                data.add_field('cmd', 'gcvt')
                data.add_field('data', self._client_id)

                with async_timeout.timeout(10, loop=self._hass.loop):
                    resp = await websession.post(self._backend_url, data=data)

                if resp.status != 200:
                    try_again('POST gcvt', '{} returned {}'.format(resp.url, resp.status))
                    return False

                text = await resp.text()
                #_LOGGER.info("POST gcvt GOT %s", text)

                json_object = json.loads(text)

                # Something like this...
                # [{ "tankID":1745,
                #     ...
                # }]
                for tank in json_object:
                    tank_id = tank['tankID']

                    if tank_id in self._devices:
                        found = self._devices[tank_id]
                        _LOGGER.info("Updating TANK %d", tank_id)
                        found.update_from_tank(tank)
                        found.schedule_update_ha_state()
                    else:
                        _LOGGER.info("Adding TANK %d", tank_id)
                        found = PoemILevelSensor(tank_id, tank)
                        self._devices[tank_id] = found
                        self._async_add_devices([found])

                return True
            except (asyncio.TimeoutError, aiohttp.ClientError) as err:
                try_again('POST gcvt', err)
                return False


        _LOGGER.info("Refreshing %s data (%s)", ILEVEL, self._username)

        # We need a Http client that respects cookies for this...
        websession = async_get_clientsession(self._hass)

        if await async_login(websession):
            if await async_get_tank_list(websession):
                async_call_later(self._hass, REFRESH_MINUTES*60, self.async_refresh)


class PoemILevelSensor(Entity):
    """Representation of an Poem iLevel sensor."""

    def __init__(self, tank_id, json_tank):
        """Initialize the sensor."""
        # Something like this...
        # [ { "tankID":1745,
        #     "tankDescription":"Tank 205201"
        #     "gallons":"322",
        #     "tankcapacity":"330",
        #     "level":98,
        #     "inches":44,
        #     ...
        # }]
        self._prior_state = 0
        self._state = 0

        self._tank_id = tank_id
        self.update_from_tank(json_tank)

    def update_from_tank(self, json_tank):
        """Refresh from a json object"""
        self._name = json_tank['tankDescription']

        # Avoid oscillation of value.
        if json_tank['level'] < self._state or json_tank['level'] > self._prior_state or json_tank['level'] == 100:
            self._prior_state = self._state
            self._state = json_tank['level']
            self._gallons = json_tank['gallons']
            self._capacity = json_tank['tankCapacity']
            self._inches = json_tank['inches']
            self._last_update = dt_util.utcnow()

    @property
    def name(self):
        """Return the name of the sensor."""
        return '{} {}'.format(ILEVEL, self._tank_id)

    @property
    def state(self):
        """Return the state of the device."""
        return self._state

    @property
    def should_poll(self):  # pylint: disable=no-self-use
        """No polling needed."""
        return False

    @property
    def device_state_attributes(self):
        """Return the state attributes."""
        return {
            ATTR_GALLONS: self._gallons,
            ATTR_CAPACITY: self._capacity,
            ATTR_INCHES: self._inches,
            ATTR_ATTRIBUTION: CONF_ATTRIBUTION
        }

    @property
    def unit_of_measurement(self): # pylint: disable=no-self-use
        """Return the unit of measurement of this entity, if any."""
        return '%'

    @property
    def icon(self):
        if self._state > 75:
            return "mdi:gauge-full"

        if self._state > 50:
            return "mdi:gauge"

        if self._state > 25:
            return "mdi:gauge-low"

        return "mdi:gauge-empty"
