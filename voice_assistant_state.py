"""Voice Assistant State Tracker for Home Assistant integration via MQTT.

Publishes voice assistant state changes to Home Assistant using MQTT Discovery.
This creates a persistent sensor entity that survives HA restarts.
"""

import asyncio
import json
from datetime import datetime
from typing import Optional

from loguru import logger
import aiomqtt


class VoiceAssistantStateTracker:
    """Tracks voice assistant state and publishes to Home Assistant via MQTT.

    Uses MQTT Discovery to create a sensor entity in Home Assistant
    (e.g., sensor.voice_assistant_bedroom) with states: asleep, listening,
    processing, speaking, idle, offline.

    This enables HA automations like:
    - Dim lights when assistant is speaking
    - Show visual indicators when listening
    - Track usage patterns
    """

    # State definitions
    STATE_ASLEEP = "asleep"  # Wake word processor sleeping
    STATE_LISTENING = "listening"  # User speaking (after wake word)
    STATE_PROCESSING = "processing"  # LLM thinking / function calls
    STATE_SPEAKING = "speaking"  # Bot generating/playing TTS
    STATE_IDLE = "idle"  # Awake but not active
    STATE_OFFLINE = "offline"  # Disconnected

    # State icons for HA UI
    STATE_ICONS = {
        STATE_ASLEEP: "mdi:sleep",
        STATE_LISTENING: "mdi:microphone",
        STATE_PROCESSING: "mdi:brain",
        STATE_SPEAKING: "mdi:speaker",
        STATE_IDLE: "mdi:account-voice",
        STATE_OFFLINE: "mdi:close-circle",
    }

    def __init__(
        self,
        mqtt_host: str,
        mqtt_port: int,
        room_name: str,
        mqtt_username: Optional[str] = None,
        mqtt_password: Optional[str] = None,
    ):
        """Initialize state tracker.

        Args:
            mqtt_host: MQTT broker hostname
            mqtt_port: MQTT broker port
            room_name: Room name for entity ID (e.g., "bedroom" -> sensor.voice_assistant_bedroom)
            mqtt_username: MQTT username (optional)
            mqtt_password: MQTT password (optional)
        """
        self.mqtt_host = mqtt_host
        self.mqtt_port = mqtt_port
        self.mqtt_username = mqtt_username
        self.mqtt_password = mqtt_password
        self.room_name = room_name.lower().replace(" ", "_")
        self.current_state = self.STATE_OFFLINE

        # MQTT topics
        self.device_id = f"voice_assistant_{self.room_name}"
        self.entity_id = f"sensor.{self.device_id}"
        self.config_topic = f"homeassistant/sensor/{self.device_id}/config"
        self.state_topic = f"homeassistant/sensor/{self.device_id}/state"
        self.attributes_topic = f"homeassistant/sensor/{self.device_id}/attributes"

        self._mqtt_client: Optional[aiomqtt.Client] = None
        self._mqtt_task: Optional[asyncio.Task] = None
        self._connected = False

    async def connect(self):
        """Connect to MQTT broker and publish discovery config."""
        try:
            logger.info(f"Connecting to MQTT broker at {self.mqtt_host}:{self.mqtt_port}")

            # Create MQTT client
            self._mqtt_client = aiomqtt.Client(
                hostname=self.mqtt_host,
                port=self.mqtt_port,
                username=self.mqtt_username,
                password=self.mqtt_password,
            )

            # Connect in background
            await self._mqtt_client.__aenter__()
            self._connected = True

            # Publish discovery config
            await self._publish_discovery()

            logger.info(f"✅ MQTT connected, entity: {self.entity_id}")

        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            self._connected = False

    async def disconnect(self):
        """Disconnect from MQTT broker."""
        if self._mqtt_client:
            try:
                # Set state to offline before disconnecting
                await self.set_state(self.STATE_OFFLINE)
                await self._mqtt_client.__aexit__(None, None, None)
                logger.info("MQTT disconnected")
            except Exception as e:
                logger.error(f"Error disconnecting from MQTT: {e}")
            finally:
                self._connected = False

    async def _publish_discovery(self):
        """Publish MQTT discovery config for Home Assistant."""
        config = {
            "name": "Voice Assistant",
            "unique_id": self.device_id,
            "state_topic": self.state_topic,
            "json_attributes_topic": self.attributes_topic,
            "icon": "mdi:account-voice",
            "device": {
                "identifiers": [self.device_id],
                "name": f"Voice Assistant ({self.room_name.replace('_', ' ').title()})",
                "manufacturer": "Pipecat",
                "model": "Voice Assistant",
            },
        }

        if self._mqtt_client:
            await self._mqtt_client.publish(
                self.config_topic,
                payload=json.dumps(config),
                retain=True,
            )
            logger.debug(f"Published MQTT discovery config to {self.config_topic}")

    async def set_state(self, state: str, **extra_attributes):
        """Update assistant state in Home Assistant via MQTT.

        Args:
            state: New state (use STATE_* constants)
            **extra_attributes: Additional attributes to include
        """
        if state not in self.STATE_ICONS:
            logger.warning(f"Unknown state: {state}")
            return

        if state == self.current_state:
            # Don't spam MQTT with duplicate state updates
            return

        logger.info(f"Voice assistant state: {self.current_state} -> {state}")
        self.current_state = state

        if not self._connected or not self._mqtt_client:
            logger.warning("MQTT not connected, skipping state update")
            return

        try:
            # Publish state
            await self._mqtt_client.publish(
                self.state_topic,
                payload=state,
                retain=True,
            )

            # Publish attributes
            attributes = {
                "icon": self.STATE_ICONS[state],
                "last_updated": datetime.now().isoformat(),
                "room": self.room_name,
                **extra_attributes,
            }
            await self._mqtt_client.publish(
                self.attributes_topic,
                payload=json.dumps(attributes),
                retain=True,
            )

        except Exception as e:
            logger.error(f"Failed to publish state to MQTT: {e}")

    async def on_asleep(self):
        """Assistant went to sleep (wake word processor sleeping)."""
        await self.set_state(self.STATE_ASLEEP)

    async def on_listening(self):
        """User started speaking after wake word detected."""
        await self.set_state(self.STATE_LISTENING)

    async def on_processing(self):
        """LLM is processing user input / running function calls."""
        await self.set_state(self.STATE_PROCESSING)

    async def on_speaking(self):
        """Bot started speaking (TTS playing)."""
        await self.set_state(self.STATE_SPEAKING)

    async def on_idle(self):
        """Bot finished speaking, waiting for next command."""
        await self.set_state(self.STATE_IDLE)

    async def on_offline(self):
        """Client disconnected or service stopped."""
        await self.set_state(self.STATE_OFFLINE)
