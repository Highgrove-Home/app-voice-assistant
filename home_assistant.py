"""Home Assistant integration for Pipecat voice bot.

This module provides an async client for interacting with Home Assistant API
and OpenAI function definitions for controlling smart home devices.
"""

import aiohttp
import asyncio
import json
import websockets
from loguru import logger
from typing import Any, Dict, List, Optional

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema


class HomeAssistantClient:
    """Async client for Home Assistant API."""

    def __init__(self, url: str, token: str, room_name: Optional[str] = None):
        """Initialize Home Assistant client.

        Args:
            url: Home Assistant URL (e.g., http://homeassistant.local:8123)
            token: Long-lived access token from Home Assistant
            room_name: Optional room/area name to filter devices (e.g., "bedroom")
        """
        self.url = url.rstrip("/")
        self.token = token
        self.room_name = room_name
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[Any] = None  # WebSocket connection
        self._ws_id_counter = 0  # Message ID counter for WebSocket calls
        self.entities: Dict[str, Dict[str, Any]] = {}
        self.room_entities: Dict[str, Dict[str, Any]] = {}
        self.area_id: Optional[str] = None

    async def __aenter__(self):
        """Async context manager entry."""
        self._session = aiohttp.ClientSession(headers=self.headers)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

    async def close(self):
        """Close all connections (HTTP session and WebSocket)."""
        if self._ws:
            await self._ws.close()
            self._ws = None
            logger.info("WebSocket connection closed")
        if self._session:
            await self._session.close()
            self._session = None

    async def get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if not self._session:
            self._session = aiohttp.ClientSession(headers=self.headers)
        return self._session

    async def _ws_connect(self):
        """Establish and authenticate WebSocket connection to Home Assistant."""
        if self._ws:
            return  # Already connected

        ws_url = self.url.replace("http://", "ws://").replace("https://", "wss://") + "/api/websocket"

        try:
            self._ws = await websockets.connect(ws_url)

            # Receive auth required message
            auth_msg = await self._ws.recv()
            auth_data = json.loads(auth_msg)

            if auth_data.get("type") != "auth_required":
                logger.error(f"Unexpected message type: {auth_data.get('type')}")
                await self._ws.close()
                self._ws = None
                return

            # Send auth
            await self._ws.send(json.dumps({
                "type": "auth",
                "access_token": self.token
            }))

            # Receive auth result
            auth_result = await self._ws.recv()
            auth_result_data = json.loads(auth_result)

            if auth_result_data.get("type") != "auth_ok":
                logger.error(f"WebSocket authentication failed: {auth_result_data}")
                await self._ws.close()
                self._ws = None
                return

            logger.info("WebSocket connection established and authenticated")

        except Exception as e:
            logger.error(f"Failed to connect WebSocket: {e}")
            if self._ws:
                await self._ws.close()
            self._ws = None

    async def _ws_call(self, message_type: str, **kwargs) -> Any:
        """Make a WebSocket call to Home Assistant using persistent connection.

        Args:
            message_type: The message type to send
            **kwargs: Additional message parameters

        Returns:
            The result from the WebSocket call
        """
        # Ensure WebSocket is connected
        await self._ws_connect()

        if not self._ws:
            logger.error("WebSocket not connected")
            return None

        try:
            # Increment message ID
            self._ws_id_counter += 1
            request_id = self._ws_id_counter

            # Send request
            request = {
                "id": request_id,
                "type": message_type,
                **kwargs
            }
            await self._ws.send(json.dumps(request))

            # Receive response
            response = await self._ws.recv()
            response_data = json.loads(response)

            if response_data.get("success"):
                return response_data.get("result")
            else:
                logger.error(f"WebSocket call failed: {response_data}")
                return None

        except Exception as e:
            logger.error(f"WebSocket call failed: {e}")
            # Connection might be broken, reset it
            if self._ws:
                await self._ws.close()
            self._ws = None
            return None

    async def fetch_areas(self) -> Optional[str]:
        """Fetch areas via WebSocket and find the area_id for the configured room.

        Returns:
            Area ID for the configured room, or None if not found
        """
        if not self.room_name:
            return None

        try:
            areas = await self._ws_call("config/area_registry/list")

            if not areas:
                logger.warning("Failed to fetch areas via WebSocket")
                return None

            # Find area matching room_name (case-insensitive)
            for area in areas:
                if area.get("name", "").lower() == self.room_name.lower():
                    self.area_id = area.get("area_id")
                    logger.info(f"Found area '{self.room_name}' with ID: {self.area_id}")
                    return self.area_id

            logger.warning(f"Room '{self.room_name}' not found in Home Assistant areas")
            return None

        except Exception as e:
            logger.error(f"Failed to fetch areas: {e}")
            return None

    async def fetch_device_registry(self) -> Dict[str, str]:
        """Fetch device registry via WebSocket to get device->area mappings.

        Returns:
            Dictionary mapping device_id to area_id
        """
        try:
            devices = await self._ws_call("config/device_registry/list")

            if not devices:
                logger.warning("Failed to fetch device registry via WebSocket")
                return {}

            # Build device_id -> area_id mapping
            device_area_map = {}
            for device in devices:
                device_id = device.get("id")
                area_id = device.get("area_id")
                if device_id and area_id:
                    device_area_map[device_id] = area_id

            logger.info(f"Fetched {len(device_area_map)} device->area mappings")
            return device_area_map

        except Exception as e:
            logger.error(f"Failed to fetch device registry: {e}")
            return {}

    async def fetch_entity_registry(self) -> Dict[str, str]:
        """Fetch entity registry via WebSocket to get entity->area mappings.

        Entities can have area assigned directly OR inherit from their parent device.

        Returns:
            Dictionary mapping entity_id to area_id
        """
        try:
            # Get device->area mappings first
            device_area_map = await self.fetch_device_registry()

            entities = await self._ws_call("config/entity_registry/list")

            if not entities:
                logger.warning("Failed to fetch entity registry via WebSocket")
                return {}

            # Build entity_id -> area_id mapping
            entity_area_map = {}
            for entity in entities:
                entity_id = entity.get("entity_id")
                area_id = entity.get("area_id")  # Direct area assignment
                device_id = entity.get("device_id")  # Parent device

                if not entity_id:
                    continue

                # Use direct area assignment if available
                if area_id:
                    entity_area_map[entity_id] = area_id
                # Otherwise inherit from parent device
                elif device_id and device_id in device_area_map:
                    entity_area_map[entity_id] = device_area_map[device_id]

            logger.info(f"Fetched {len(entity_area_map)} entity->area mappings (including device inheritance)")
            return entity_area_map

        except Exception as e:
            logger.error(f"Failed to fetch entity registry: {e}")
            return {}

    async def fetch_entities(self) -> Dict[str, Dict[str, Any]]:
        """Fetch all entities from Home Assistant and filter by room if configured.

        Returns:
            Dictionary mapping entity_id to entity state data
        """
        session = await self.get_session()
        try:
            # Fetch all entity states
            async with session.get(f"{self.url}/api/states") as response:
                response.raise_for_status()
                states = await response.json()

                # Store all entities by ID
                self.entities = {
                    entity["entity_id"]: entity for entity in states
                }

                logger.info(f"Fetched {len(self.entities)} entities from Home Assistant")

                # If room is configured, filter entities by area
                if self.room_name:
                    await self.fetch_areas()
                    if self.area_id:
                        entity_area_map = await self.fetch_entity_registry()

                        # Filter entities in this room
                        self.room_entities = {
                            entity_id: entity
                            for entity_id, entity in self.entities.items()
                            if entity_area_map.get(entity_id) == self.area_id
                        }

                        logger.info(f"Filtered to {len(self.room_entities)} entities in room '{self.room_name}'")

                        # Log light entities for debugging
                        room_lights = [eid for eid in self.room_entities.keys() if eid.startswith("light.")]
                        if room_lights:
                            logger.info(f"Lights in '{self.room_name}': {room_lights}")
                    else:
                        logger.warning(f"Area '{self.room_name}' not found in Home Assistant. Falling back to friendly_name filtering.")
                        # Fallback: filter by friendly_name containing room name
                        room_name_lower = self.room_name.lower()
                        self.room_entities = {
                            entity_id: entity
                            for entity_id, entity in self.entities.items()
                            if room_name_lower in entity.get("attributes", {}).get("friendly_name", "").lower()
                        }
                        logger.info(f"Filtered to {len(self.room_entities)} entities by friendly_name matching '{self.room_name}'")

                        # Log light entities for debugging
                        room_lights = [eid for eid in self.room_entities.keys() if eid.startswith("light.")]
                        if room_lights:
                            logger.info(f"Lights matching '{self.room_name}': {room_lights}")

                return self.entities

        except Exception as e:
            logger.error(f"Failed to fetch entities from Home Assistant: {e}")
            return {}

    async def call_service(
        self,
        domain: str,
        service: str,
        entity_id: Optional[str] = None,
        **kwargs
    ) -> bool:
        """Call a Home Assistant service.

        Args:
            domain: Service domain (e.g., 'light', 'switch')
            service: Service name (e.g., 'turn_on', 'turn_off')
            entity_id: Optional entity ID to target
            **kwargs: Additional service data

        Returns:
            True if successful, False otherwise
        """
        session = await self.get_session()
        service_data = kwargs.copy()
        if entity_id:
            service_data["entity_id"] = entity_id

        try:
            async with session.post(
                f"{self.url}/api/services/{domain}/{service}",
                json=service_data
            ) as response:
                response.raise_for_status()
                logger.info(f"Called {domain}.{service} for {entity_id}")
                return True

        except Exception as e:
            logger.error(f"Failed to call service {domain}.{service}: {e}")
            return False

    async def get_state(self, entity_id: str) -> Optional[Dict[str, Any]]:
        """Get current state of an entity.

        Args:
            entity_id: Entity ID to query

        Returns:
            Entity state data or None if not found
        """
        session = await self.get_session()
        try:
            async with session.get(f"{self.url}/api/states/{entity_id}") as response:
                response.raise_for_status()
                return await response.json()

        except Exception as e:
            logger.error(f"Failed to get state for {entity_id}: {e}")
            return None

    def get_entities_by_domain(self, domain: str, room_only: bool = True) -> List[Dict[str, Any]]:
        """Get all entities for a specific domain.

        Args:
            domain: Domain to filter by (e.g., 'light', 'switch', 'climate')
            room_only: If True and room is configured, only return entities in the room

        Returns:
            List of entity state dictionaries
        """
        # Choose which entity set to use
        entities = self.room_entities if (room_only and self.room_entities) else self.entities

        return [
            entity for entity_id, entity in entities.items()
            if entity_id.startswith(f"{domain}.")
        ]

    def get_entity_summary(self) -> str:
        """Generate a human-readable summary of available entities.

        Returns:
            Summary string describing available devices
        """
        # Use room entities if available, otherwise all entities
        entities_to_summarize = self.room_entities if self.room_entities else self.entities

        if not entities_to_summarize:
            return "No devices available."

        domains = {}
        for entity_id in entities_to_summarize.keys():
            domain = entity_id.split(".")[0]
            domains[domain] = domains.get(domain, 0) + 1

        summary_parts = []
        domain_names = {
            "light": "lights",
            "switch": "switches",
            "climate": "thermostats",
            "cover": "covers/blinds",
            "fan": "fans",
            "lock": "locks",
            "media_player": "media players",
            "sensor": "sensors",
            "binary_sensor": "binary sensors",
        }

        for domain, count in sorted(domains.items()):
            name = domain_names.get(domain, f"{domain}s")
            summary_parts.append(f"{count} {name}")

        prefix = f"In the {self.room_name}: " if self.room_name else "Available devices: "
        summary = prefix + ", ".join(summary_parts)

        # Add detailed sensor information (temperature, humidity, etc.)
        sensors = self.get_entities_by_domain("sensor", room_only=True)
        if sensors:
            sensor_details = []
            for sensor in sensors:
                entity_id = sensor.get("entity_id")
                friendly_name = sensor.get("attributes", {}).get("friendly_name", "")
                device_class = sensor.get("attributes", {}).get("device_class", "")

                # Include sensors with useful device classes
                if device_class in ["temperature", "humidity", "pressure", "battery", "illuminance", "power", "energy"]:
                    sensor_details.append(f"{entity_id} ({friendly_name})")

            if sensor_details:
                summary += f"\n\nKey sensors: {', '.join(sensor_details[:10])}"  # Limit to first 10

        return summary

    def find_entity_in_room(self, domain: str, name_hint: Optional[str] = None) -> Optional[str]:
        """Find an entity ID in the current room by domain and optional name hint.

        Args:
            domain: Entity domain (e.g., 'light', 'switch')
            name_hint: Optional name hint to match against friendly_name

        Returns:
            Entity ID if found, None otherwise
        """
        entities = self.get_entities_by_domain(domain, room_only=True)

        if not entities:
            return None

        # If no name hint, return first entity of this domain in the room
        if not name_hint:
            return entities[0]["entity_id"]

        # Try to match name hint
        name_hint_lower = name_hint.lower()
        for entity in entities:
            friendly_name = entity.get("attributes", {}).get("friendly_name", "").lower()
            entity_id = entity["entity_id"]

            if name_hint_lower in friendly_name or name_hint_lower in entity_id:
                return entity_id

        # No match found, return first entity
        return entities[0]["entity_id"] if entities else None


def generate_openai_functions() -> ToolsSchema:
    """Generate function definitions for Home Assistant control.

    Returns:
        ToolsSchema object with function definitions
    """
    standard_tools = [
        FunctionSchema(
            name="turn_on_device",
            description="Turn on a device (light, switch, etc.). Can specify entity_id or just device_type to control devices in the current room.",
            properties={
                "entity_id": {
                    "type": "string",
                    "description": "Optional specific entity ID (e.g., 'light.living_room'). If not provided, device_type must be specified."
                },
                "device_type": {
                    "type": "string",
                    "description": "Type of device to control if entity_id not provided (e.g., 'light', 'switch', 'fan')",
                    "enum": ["light", "switch", "fan", "climate", "cover", "media_player"]
                },
                "brightness": {
                    "type": "integer",
                    "description": "Brightness level (0-255) for lights",
                    "minimum": 0,
                    "maximum": 255
                }
            },
            required=[]
        ),
        FunctionSchema(
            name="turn_off_device",
            description="Turn off a device (light, switch, etc.). Can specify entity_id or just device_type to control devices in the current room.",
            properties={
                "entity_id": {
                    "type": "string",
                    "description": "Optional specific entity ID (e.g., 'light.living_room'). If not provided, device_type must be specified."
                },
                "device_type": {
                    "type": "string",
                    "description": "Type of device to control if entity_id not provided (e.g., 'light', 'switch', 'fan')",
                    "enum": ["light", "switch", "fan", "climate", "cover", "media_player"]
                }
            },
            required=[]
        ),
        FunctionSchema(
            name="set_temperature",
            description="Set temperature for a thermostat/climate device",
            properties={
                "entity_id": {
                    "type": "string",
                    "description": "The climate entity ID (e.g., 'climate.living_room')"
                },
                "temperature": {
                    "type": "number",
                    "description": "Target temperature"
                }
            },
            required=["entity_id", "temperature"]
        ),
        FunctionSchema(
            name="get_device_state",
            description="Get the current state of a device in the current room",
            properties={
                "entity_id": {
                    "type": "string",
                    "description": "The entity ID to query (e.g., 'light.bedroom_ceiling')"
                }
            },
            required=["entity_id"]
        ),
        FunctionSchema(
            name="list_devices",
            description="List all available smart home devices in the current room, optionally filtered by domain",
            properties={
                "domain": {
                    "type": "string",
                    "description": "Device domain to filter by (e.g., 'light', 'switch', 'climate'). Leave empty for all devices in current room.",
                    "enum": ["light", "switch", "climate", "cover", "fan", "lock", "media_player", "all"]
                }
            },
            required=[]
        ),
        FunctionSchema(
            name="set_timer",
            description="Set a timer for a specified duration",
            properties={
                "duration_minutes": {
                    "type": "number",
                    "description": "Duration in minutes (e.g., 5, 10, 0.5 for 30 seconds)"
                },
                "name": {
                    "type": "string",
                    "description": "Optional name for the timer (e.g., 'pasta timer', 'workout')"
                }
            },
            required=["duration_minutes"]
        ),
        FunctionSchema(
            name="cancel_timer",
            description="Cancel a specific timer by name",
            properties={
                "name": {
                    "type": "string",
                    "description": "Name of the timer to cancel"
                }
            },
            required=["name"]
        ),
        FunctionSchema(
            name="list_timers",
            description="List all active timers",
            properties={},
            required=[]
        ),
        FunctionSchema(
            name="get_timer_status",
            description="Get the remaining time for a specific timer",
            properties={
                "name": {
                    "type": "string",
                    "description": "Name of the timer to check"
                }
            },
            required=["name"]
        )
    ]

    return ToolsSchema(standard_tools=standard_tools)


async def handle_function_call(
    ha_client: HomeAssistantClient,
    function_name: str,
    arguments: Dict[str, Any],
    timer_manager=None
) -> str:
    """Handle OpenAI function call and execute Home Assistant or timer action.

    Args:
        ha_client: Home Assistant client instance
        function_name: Name of the function to execute
        arguments: Function arguments from OpenAI
        timer_manager: Optional TimerManager instance for timer functions

    Returns:
        Result message to send back to the LLM
    """
    try:
        if function_name == "turn_on_device":
            # Get entity_id - either provided or find in room by device_type
            entity_id = arguments.get("entity_id")
            device_type = arguments.get("device_type")

            # If specific entity_id provided, control only that one
            if entity_id:
                domain = entity_id.split(".")[0]
                service_data = {}
                if "brightness" in arguments:
                    service_data["brightness"] = arguments["brightness"]

                success = await ha_client.call_service(
                    domain, "turn_on", entity_id, **service_data
                )

                if success:
                    brightness_msg = f" at {arguments['brightness']} brightness" if "brightness" in arguments else ""
                    return f"Turned on {entity_id}{brightness_msg}"
                return f"Failed to turn on {entity_id}"

            # If device_type provided, control ALL of that type in the room
            if device_type:
                # Get all entities of this type in the room
                entities_in_room = ha_client.get_entities_by_domain(device_type, room_only=True)

                if not entities_in_room:
                    return f"No {device_type} devices found in the {ha_client.room_name or 'room'}"

                if not ha_client.room_entities:
                    logger.warning(f"Room filtering not working - room_entities is empty!")
                    return f"Room filtering not configured properly. Please check ROOM_NAME matches a Home Assistant area."

                service_data = {}
                if "brightness" in arguments:
                    service_data["brightness"] = arguments["brightness"]

                # Control all entities of this type in the room
                successes = []
                for entity in entities_in_room:
                    eid = entity["entity_id"]
                    success = await ha_client.call_service(
                        device_type, "turn_on", eid, **service_data
                    )
                    if success:
                        successes.append(eid)

                if successes:
                    brightness_msg = f" at {arguments['brightness']} brightness" if "brightness" in arguments else ""
                    room_msg = f" in the {ha_client.room_name}" if ha_client.room_name else ""
                    return f"Turned on {len(successes)} {device_type}(s){room_msg}{brightness_msg}"
                return f"Failed to turn on {device_type}"

            return "Please specify either entity_id or device_type"

        elif function_name == "turn_off_device":
            # Get entity_id - either provided or find in room by device_type
            entity_id = arguments.get("entity_id")
            device_type = arguments.get("device_type")

            # If specific entity_id provided, control only that one
            if entity_id:
                domain = entity_id.split(".")[0]
                success = await ha_client.call_service(domain, "turn_off", entity_id)

                if success:
                    return f"Turned off {entity_id}"
                return f"Failed to turn off {entity_id}"

            # If device_type provided, control ALL of that type in the room
            if device_type:
                # Get all entities of this type in the room
                entities_in_room = ha_client.get_entities_by_domain(device_type, room_only=True)

                if not entities_in_room:
                    return f"No {device_type} devices found in the {ha_client.room_name or 'room'}"

                if not ha_client.room_entities:
                    logger.warning(f"Room filtering not working - room_entities is empty!")
                    return f"Room filtering not configured properly. Please check ROOM_NAME matches a Home Assistant area."

                # Control all entities of this type in the room
                successes = []
                for entity in entities_in_room:
                    eid = entity["entity_id"]
                    success = await ha_client.call_service(device_type, "turn_off", eid)
                    if success:
                        successes.append(eid)

                if successes:
                    room_msg = f" in the {ha_client.room_name}" if ha_client.room_name else ""
                    return f"Turned off {len(successes)} {device_type}(s){room_msg}"
                return f"Failed to turn off {device_type}"

            return "Please specify either entity_id or device_type"

        elif function_name == "set_temperature":
            entity_id = arguments["entity_id"]
            temperature = arguments["temperature"]

            success = await ha_client.call_service(
                "climate", "set_temperature",
                entity_id,
                temperature=temperature
            )

            return f"Set {entity_id} to {temperature}°" if success else f"Failed to set temperature"

        elif function_name == "get_device_state":
            entity_id = arguments["entity_id"]
            state = await ha_client.get_state(entity_id)

            if state:
                state_value = state.get("state", "unknown")
                attributes = state.get("attributes", {})
                friendly_name = attributes.get("friendly_name", entity_id)

                # Build a descriptive response
                response_parts = [f"{friendly_name} is {state_value}"]

                # Add relevant attributes based on domain
                if "brightness" in attributes:
                    response_parts.append(f"brightness {attributes['brightness']}")
                if "temperature" in attributes:
                    response_parts.append(f"temperature {attributes['temperature']}°")

                return ", ".join(response_parts)
            return f"Could not get state for {entity_id}"

        elif function_name == "list_devices":
            domain = arguments.get("domain", "all")

            if domain == "all" or not domain:
                # Return summary of all devices in the current room
                return ha_client.get_entity_summary()
            else:
                # Get devices of specific type in current room only
                entities = ha_client.get_entities_by_domain(domain, room_only=True)

                if not entities:
                    room_msg = f" in the {ha_client.room_name}" if ha_client.room_name else ""
                    return f"No {domain} devices found{room_msg}"

                # Build list with states
                device_info = []
                for e in entities:
                    entity_id = e.get("entity_id")
                    friendly_name = e.get("attributes", {}).get("friendly_name", entity_id)
                    state = e.get("state", "unknown")
                    device_info.append(f"{friendly_name} ({state})")

                    # Debug logging
                    logger.debug(f"Including {domain} device: {entity_id} (friendly_name: {friendly_name}, state: {state})")

                room_msg = f" in the {ha_client.room_name}" if ha_client.room_name else ""
                return f"{domain.capitalize()} devices{room_msg}: {', '.join(device_info)}"

        # Timer functions
        elif function_name == "set_timer":
            if not timer_manager:
                return "Timer functionality is not available."

            duration_minutes = arguments["duration_minutes"]
            name = arguments.get("name")

            result = await timer_manager.set_timer(duration_minutes, name)
            return result

        elif function_name == "cancel_timer":
            if not timer_manager:
                return "Timer functionality is not available."

            name = arguments["name"]
            result = await timer_manager.cancel_timer(name)
            return result

        elif function_name == "list_timers":
            if not timer_manager:
                return "Timer functionality is not available."

            result = await timer_manager.list_timers()
            return result

        elif function_name == "get_timer_status":
            if not timer_manager:
                return "Timer functionality is not available."

            name = arguments["name"]
            result = await timer_manager.get_timer_status(name)
            return result

        return f"Unknown function: {function_name}"

    except Exception as e:
        logger.error(f"Error handling function call {function_name}: {e}")
        return f"Error executing {function_name}: {str(e)}"
