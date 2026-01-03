"""Interrupt handler for detecting user interrupt commands.

Detects phrases like "shut up", "stop talking", etc. and interrupts the bot.
"""

import re
from loguru import logger
from pipecat.frames.frames import (
    CancelFrame,
    Frame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class InterruptHandler(FrameProcessor):
    """Detects user interrupt commands and cancels bot speech.

    Monitors transcriptions for interrupt phrases like:
    - "shut up"
    - "stop talking"
    - "be quiet"

    When detected, sends a CancelFrame to interrupt the bot and tells
    the wake word processor to go back to sleep.
    """

    # Interrupt phrases that trigger cancellation (with word boundaries)
    INTERRUPT_PHRASES = [
        r"\bshut up\b",
        r"\bstop talking\b",
        r"\bbe quiet\b",
        r"\bquiet\b",
        r"\bcancel\b",
        r"\bnever mind\b",
        r"\bnevermind\b",
    ]

    def __init__(self, wake_processor):
        """Initialize the interrupt handler.

        Args:
            wake_processor: Reference to OpenWakeWordProcessor to control sleep state
        """
        super().__init__()
        self._wake_processor = wake_processor

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process frames and detect interrupt commands."""
        await super().process_frame(frame, direction)

        # Check transcriptions for interrupt phrases
        if isinstance(frame, TranscriptionFrame):
            text = frame.text.lower().strip()

            # Check if any interrupt phrase matches (using word boundaries)
            for pattern in self.INTERRUPT_PHRASES:
                if re.search(pattern, text):
                    logger.info(f"🛑 Interrupt detected: '{text}' - canceling bot and going to sleep")

                    # Send cancel frame to interrupt bot speech
                    await self.push_frame(CancelFrame(), direction)

                    # Put wake word processor back to sleep
                    await self._wake_processor.go_to_sleep()

                    # Don't pass the interrupt transcription to the LLM
                    return

        # Pass all other frames through
        await self.push_frame(frame, direction)
