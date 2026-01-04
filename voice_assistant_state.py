"""Voice Assistant State Tracker for Home Assistant integration via MQTT.

Publishes voice assistant state changes to Home Assistant using MQTT Discovery.
This creates a persistent sensor entity that survives HA restarts.
"""

import asyncio
import json
import time
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
    STATE_STANDBY = "standby"  # Wake word processor sleeping, waiting for wake word
    STATE_LISTENING = "listening"  # User speaking (after wake word)
    STATE_PROCESSING = "processing"  # LLM thinking / function calls
    STATE_SPEAKING = "speaking"  # Bot generating/playing TTS
    STATE_IDLE = "idle"  # Awake but not active
    STATE_MUTED = "muted"  # Assistant muted, wake word detection disabled
    STATE_OFFLINE = "offline"  # Disconnected

    # State icons for HA UI
    STATE_ICONS = {
        STATE_STANDBY: "mdi:power-standby",
        STATE_LISTENING: "mdi:microphone",
        STATE_PROCESSING: "mdi:brain",
        STATE_SPEAKING: "mdi:speaker",
        STATE_IDLE: "mdi:account-voice",
        STATE_MUTED: "mdi:microphone-off",
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

        # MQTT topics for sensor
        self.device_id = f"voice_assistant_{self.room_name}"
        self.entity_id = f"sensor.{self.device_id}"
        self.config_topic = f"homeassistant/sensor/{self.device_id}/config"
        self.state_topic = f"homeassistant/sensor/{self.device_id}/state"
        self.attributes_topic = f"homeassistant/sensor/{self.device_id}/attributes"

        # MQTT topics for mute switch
        self.mute_switch_id = f"switch.{self.device_id}_mute"
        self.mute_config_topic = f"homeassistant/switch/{self.device_id}_mute/config"
        self.mute_state_topic = f"homeassistant/switch/{self.device_id}_mute/state"
        self.mute_command_topic = f"homeassistant/switch/{self.device_id}_mute/set"

        self._mqtt_client: Optional[aiomqtt.Client] = None
        self._mqtt_task: Optional[asyncio.Task] = None
        self._connected = False
        self._is_muted = False  # Track mute state
        self._mute_callback = None  # Callback for mute state changes

        # Debouncing for state changes
        self._last_state_change = 0.0  # Timestamp of last state change
        self._debounce_interval = 0.3  # Minimum seconds between state changes

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

            # Publish discovery config for sensor and switch
            await self._publish_discovery()

            # Subscribe to mute command topic
            await self._mqtt_client.subscribe(self.mute_command_topic)
            logger.debug(f"Subscribed to mute commands: {self.mute_command_topic}")

            # Start listening for mute commands
            asyncio.create_task(self._listen_for_mute_commands())

            logger.info(f"✅ MQTT connected, entity: {self.entity_id}, mute switch: {self.mute_switch_id}")

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
        device_info = {
            "identifiers": [self.device_id],
            "name": f"Pipecat Voice Assistant ({self.room_name.replace('_', ' ').title()})",
            "manufacturer": "Pipecat",
            "model": "Voice Assistant",
        }

        # Sensor config (for state tracking)
        sensor_config = {
            "name": f"Pipecat ({self.room_name.replace('_', ' ').title()})",
            "unique_id": self.device_id,
            "object_id": self.device_id,  # Explicitly set entity ID to avoid suffix
            "state_topic": self.state_topic,
            "json_attributes_topic": self.attributes_topic,
            "icon": "mdi:account-voice",
            "device": device_info,
        }

        # Switch config (for mute control)
        switch_config = {
            "name": "Mute",
            "unique_id": f"{self.device_id}_mute",
            "object_id": f"{self.device_id}_mute",
            "state_topic": self.mute_state_topic,
            "command_topic": self.mute_command_topic,
            "payload_on": "ON",
            "payload_off": "OFF",
            "state_on": "ON",
            "state_off": "OFF",
            "icon": "mdi:microphone-off",
            "device": device_info,
        }

        if self._mqtt_client:
            # Publish sensor config
            await self._mqtt_client.publish(
                self.config_topic,
                payload=json.dumps(sensor_config),
                retain=True,
            )
            logger.debug(f"Published sensor config to {self.config_topic}")

            # Publish switch config
            await self._mqtt_client.publish(
                self.mute_config_topic,
                payload=json.dumps(switch_config),
                retain=True,
            )
            logger.debug(f"Published mute switch config to {self.mute_config_topic}")

            # Publish initial mute state
            await self._publish_mute_state()

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

        # Debounce state changes (except for critical states like offline)
        current_time = time.time()
        time_since_last_change = current_time - self._last_state_change

        if time_since_last_change < self._debounce_interval and state != self.STATE_OFFLINE:
            # Skip rapid state changes to avoid flickering
            logger.debug(f"Debounced state change: {self.current_state} -> {state} (too fast: {time_since_last_change:.2f}s)")
            return

        logger.info(f"Voice assistant state: {self.current_state} -> {state}")
        self.current_state = state
        self._last_state_change = current_time

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

    async def _publish_mute_state(self):
        """Publish current mute state to MQTT."""
        if not self._connected or not self._mqtt_client:
            return

        try:
            payload = "ON" if self._is_muted else "OFF"
            await self._mqtt_client.publish(
                self.mute_state_topic,
                payload=payload,
                retain=True,
            )
            logger.debug(f"Published mute state: {payload}")
        except Exception as e:
            logger.error(f"Failed to publish mute state: {e}")

    async def _listen_for_mute_commands(self):
        """Background task to listen for mute switch commands from HA."""
        try:
            async for message in self._mqtt_client.messages:
                if message.topic.matches(self.mute_command_topic):
                    command = message.payload.decode()
                    new_muted = (command == "ON")

                    if new_muted != self._is_muted:
                        logger.info(f"Mute command received: {command} (muted={new_muted})")
                        self._is_muted = new_muted

                        # Publish state back
                        await self._publish_mute_state()

                        # Call callback if registered
                        if self._mute_callback:
                            await self._mute_callback(new_muted)

        except Exception as e:
            logger.error(f"Error in mute command listener: {e}")

    def set_mute_callback(self, callback):
        """Register callback for mute state changes.

        Args:
            callback: Async function with signature: async def callback(muted: bool)
        """
        self._mute_callback = callback

    async def set_muted(self, muted: bool):
        """Set mute state programmatically (updates switch in HA).

        Args:
            muted: True to mute, False to unmute
        """
        if muted != self._is_muted:
            self._is_muted = muted
            await self._publish_mute_state()
            logger.info(f"Mute state set to: {muted}")

    async def on_standby(self):
        """Assistant went to standby (wake word processor sleeping, waiting for wake word)."""
        await self.set_state(self.STATE_STANDBY)

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

    async def on_muted(self):
        """Assistant muted, wake word detection disabled."""
        await self.set_state(self.STATE_MUTED)

    async def on_offline(self):
        """Client disconnected or service stopped."""
        await self.set_state(self.STATE_OFFLINE)
