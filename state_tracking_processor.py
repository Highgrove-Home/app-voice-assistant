"""Frame processor for tracking voice assistant state.

Monitors pipeline frames and updates Home Assistant state accordingly.
"""

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    StartFrame,
    EndFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    TranscriptionFrame,
    LLMMessagesFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TextFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from voice_assistant_state import VoiceAssistantStateTracker


class StateTrackingProcessor(FrameProcessor):
    """Monitors pipeline activity and updates HA state sensor.

    Listens for frame events to track assistant state:
    - UserStartedSpeakingFrame -> listening
    - UserStoppedSpeakingFrame -> processing (if transcript exists)
    - TTSStartedFrame -> speaking
    - TTSStoppedFrame -> idle
    """

    def __init__(self, state_tracker: VoiceAssistantStateTracker, wake_processor):
        """Initialize state tracking processor.

        Args:
            state_tracker: VoiceAssistantStateTracker instance
            wake_processor: Reference to OpenWakeWordProcessor to check if awake
        """
        super().__init__()
        self.state_tracker = state_tracker
        self.wake_processor = wake_processor
        self._last_state = None
        self._current_assistant_message = ""  # Accumulate text chunks

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process frames and update state based on activity."""
        await super().process_frame(frame, direction)

        # Track different frame types
        if isinstance(frame, StartFrame):
            # Pipeline started - set to standby initially
            await self.state_tracker.on_standby()

        elif isinstance(frame, UserStartedSpeakingFrame):
            # User started speaking (after wake word)
            if self.wake_processor._is_awake:
                await self.state_tracker.on_listening()

        elif isinstance(frame, TranscriptionFrame):
            # Capture user transcription for conversation history
            if self.wake_processor._is_awake and frame.text:
                logger.debug(f"Captured user message: {frame.text}")
                self.state_tracker.add_user_message(frame.text)

        elif isinstance(frame, UserStoppedSpeakingFrame):
            # User stopped speaking - now processing
            if self.wake_processor._is_awake:
                await self.state_tracker.on_processing()

        elif isinstance(frame, TextFrame):
            # Accumulate text chunks from the assistant's response
            if frame.text:
                self._current_assistant_message += frame.text

        elif isinstance(frame, TTSStartedFrame):
            # Bot started speaking
            await self.state_tracker.on_speaking()
            # Reset message accumulator at start of TTS
            self._current_assistant_message = ""

        elif isinstance(frame, TTSStoppedFrame):
            # Bot stopped speaking - save accumulated message to history
            if self._current_assistant_message.strip():
                logger.debug(f"Captured assistant message: {self._current_assistant_message.strip()}")
                self.state_tracker.add_assistant_message(self._current_assistant_message.strip())
                self._current_assistant_message = ""
            else:
                logger.debug("No assistant message to save (empty accumulator)")

            # Now idle (still awake)
            if self.wake_processor._is_awake:
                await self.state_tracker.on_idle()

        elif isinstance(frame, EndFrame):
            # Pipeline ending - going offline
            await self.state_tracker.on_offline()

        # Pass frame through
        await self.push_frame(frame, direction)

    async def on_wake_detected(self):
        """Called by wake processor when wake word detected."""
        await self.state_tracker.on_listening()

    async def on_sleep(self):
        """Called by wake processor when going back to standby."""
        await self.state_tracker.on_standby()
