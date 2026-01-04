"""Voice Assistant State Tracker for Home Assistant integration.

Publishes voice assistant state changes to Home Assistant as sensor entities.
This allows HA automations to react to assistant activity (listening, speaking, etc.).
"""

from datetime import datetime
from typing import Optional

from loguru import logger


class VoiceAssistantStateTracker:
    """Tracks voice assistant state and publishes to Home Assistant.

    Creates a sensor entity in Home Assistant (e.g., sensor.voice_assistant_bedroom)
    with states: asleep, listening, processing, speaking, idle, offline.

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

    def __init__(self, ha_client, room_name: str):
        """Initialize state tracker.

        Args:
            ha_client: HomeAssistantClient instance
            room_name: Room name for entity ID (e.g., "bedroom" -> sensor.voice_assistant_bedroom)
        """
        self.ha_client = ha_client
        self.room_name = room_name.lower().replace(" ", "_")
        self.entity_id = f"sensor.voice_assistant_{self.room_name}"
        self.current_state = self.STATE_OFFLINE

    async def set_state(self, state: str, **extra_attributes):
        """Update assistant state in Home Assistant.

        Args:
            state: New state (use STATE_* constants)
            **extra_attributes: Additional attributes to include
        """
        if state not in self.STATE_ICONS:
            logger.warning(f"Unknown state: {state}")
            return

        if state == self.current_state:
            # Don't spam HA with duplicate state updates
            return

        logger.info(f"Voice assistant state: {self.current_state} -> {state}")
        self.current_state = state

        attributes = {
            "friendly_name": f"Voice Assistant ({self.room_name.replace('_', ' ').title()})",
            "icon": self.STATE_ICONS[state],
            "last_updated": datetime.now().isoformat(),
            "room": self.room_name,
            **extra_attributes
        }

        await self.ha_client.set_state(self.entity_id, state, attributes)

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
