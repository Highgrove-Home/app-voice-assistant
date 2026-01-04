"""OpenWakeWord integration for Pipecat.

Provides audio-based wake word detection as a FrameProcessor.
"""

import asyncio
import numpy as np
from loguru import logger
from openwakeword.model import Model

from pipecat.frames.frames import Frame, InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class OpenWakeWordProcessor(FrameProcessor):
    """Audio-based wake word detection using OpenWakeWord.

    This processor monitors incoming audio frames for wake words and only allows
    frames to pass through after detection. More reliable than transcription-based
    detection since it works directly on audio.

    Args:
        wake_words: List of wake word model names (e.g., ["hey_jarvis", "alexa"])
        threshold: Detection confidence threshold (0.0-1.0, default 0.5)
        keepalive_timeout: Seconds to stay awake after detection (default 5.0)
        inference_framework: "onnx" (default, better Python 3.13 support) or "tflite"
        chunk_size_samples: Audio chunk size in samples (default 1280 = 80ms at 16kHz)
    """

    def __init__(
        self,
        wake_words: list[str] = ["hey_jarvis"],
        threshold: float = 0.5,
        keepalive_timeout: float = 5.0,
        inference_framework: str = "onnx",
        chunk_size_samples: int = 1280,
        state_tracker=None,
    ):
        super().__init__()

        self._wake_words = wake_words
        self._threshold = threshold
        self._keepalive_timeout = keepalive_timeout
        self._is_awake = False
        self._is_muted = False  # Mute flag controlled by HA switch
        self._keepalive_task = None
        self._state_tracker = state_tracker  # Optional state tracker for HA updates

        # Audio buffering
        self._chunk_size_samples = chunk_size_samples
        self._chunk_size_bytes = chunk_size_samples * 2  # 16-bit = 2 bytes per sample
        self._audio_buffer = bytearray()

        # Initialize OpenWakeWord model
        logger.info(f"Initializing OpenWakeWord with models: {wake_words}")
        logger.info(f"Using {inference_framework} inference framework")

        try:
            self._model = Model(
                wakeword_models=wake_words,
                inference_framework=inference_framework
            )
            logger.info("✅ OpenWakeWord initialized successfully")
            logger.info(f"Loaded models: {list(self._model.models.keys())}")
        except Exception as e:
            logger.error(f"Failed to initialize OpenWakeWord: {e}")
            raise

    async def set_muted(self, muted: bool):
        """Set mute state. When muted, wake word detection is disabled."""
        self._is_muted = muted
        if muted:
            logger.info("🔇 Assistant MUTED - wake word detection disabled")
            # Put system to sleep when muted
            self._is_awake = False
            if self._keepalive_task:
                self._keepalive_task.cancel()

            # Notify state tracker if available
            if self._state_tracker:
                await self._state_tracker.on_muted()
        else:
            logger.info("🔊 Assistant UNMUTED - wake word detection enabled")

            # Go back to standby when unmuted
            if self._state_tracker:
                await self._state_tracker.on_standby()

    def is_muted(self) -> bool:
        """Check if assistant is muted."""
        return self._is_muted

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        """Process frames, filtering based on wake word detection."""
        await super().process_frame(frame, direction)

        # Only block user input frames when not awake
        # Allow all control frames (LLMSetToolsFrame, etc.) and bot output frames
        from pipecat.frames.frames import (
            InputAudioRawFrame,
            TranscriptionFrame,
            UserStartedSpeakingFrame,
            UserStoppedSpeakingFrame,
        )

        # Block everything when muted
        if self._is_muted and isinstance(
            frame,
            (InputAudioRawFrame, TranscriptionFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame),
        ):
            return

        # Process audio frames for wake word detection
        if isinstance(frame, InputAudioRawFrame):
            await self._process_audio(frame)

            # Only pass audio frames through when awake
            if self._is_awake:
                await self.push_frame(frame, direction)
            # Block audio when asleep
            return

        # Block user input frames when not awake
        if not self._is_awake and isinstance(
            frame,
            (TranscriptionFrame, UserStartedSpeakingFrame, UserStoppedSpeakingFrame),
        ):
            # Block user input when asleep
            return

        # Allow all other frames through (control frames, bot output, etc.)
        await self.push_frame(frame, direction)

    async def _process_audio(self, frame: InputAudioRawFrame):
        """Process audio frame for wake word detection."""

        # Verify audio format
        if frame.sample_rate != 16000:
            logger.warning(
                f"OpenWakeWord requires 16kHz audio, got {frame.sample_rate}Hz. "
                f"Wake word detection may not work correctly."
            )
            return

        if frame.num_channels != 1:
            logger.warning(
                f"OpenWakeWord requires mono audio, got {frame.num_channels} channels. "
                f"Wake word detection may not work correctly."
            )
            return

        # Add to buffer
        self._audio_buffer.extend(frame.audio)

        # Process all complete chunks
        while len(self._audio_buffer) >= self._chunk_size_bytes:
            # Extract chunk
            chunk_bytes = bytes(self._audio_buffer[:self._chunk_size_bytes])
            self._audio_buffer = self._audio_buffer[self._chunk_size_bytes:]

            # Convert to numpy array (OpenWakeWord expects np.int16)
            try:
                audio_array = np.frombuffer(chunk_bytes, dtype=np.int16)
            except Exception as e:
                logger.error(f"Failed to convert audio to numpy array: {e}")
                continue

            # Run prediction
            try:
                prediction = self._model.predict(audio_array)

                # Check for wake word detection
                for wake_word, score in prediction.items():
                    if score >= self._threshold:
                        logger.info(
                            f"🎤 Wake word '{wake_word}' detected! "
                            f"(confidence: {score:.2f})"
                        )
                        await self._wake_up()
                        break

            except Exception as e:
                logger.error(f"Wake word prediction failed: {e}")

    async def _wake_up(self):
        """Activate wake state with keepalive timeout."""
        if not self._is_awake:
            logger.info("✨ System is now AWAKE")
            self._is_awake = True

            # Notify state tracker if available
            if self._state_tracker:
                await self._state_tracker.on_listening()

        # Reset keepalive timer
        if self._keepalive_task:
            self._keepalive_task.cancel()

        self._keepalive_task = asyncio.create_task(self._keepalive())

    async def _keepalive(self):
        """Keep system awake for specified timeout."""
        try:
            await asyncio.sleep(self._keepalive_timeout)
            self._is_awake = False
            logger.info("⏸️  System went back to STANDBY")

            # Notify state tracker if available
            if self._state_tracker:
                await self._state_tracker.on_standby()
        except asyncio.CancelledError:
            # Keepalive was reset
            pass

    async def go_to_sleep(self):
        """Manually put the system to sleep (e.g., when user says 'shut up')."""
        if self._is_awake:
            logger.info("🛑 User interrupted - going back to STANDBY")
            self._is_awake = False
            if self._keepalive_task:
                self._keepalive_task.cancel()
                self._keepalive_task = None

            # Notify state tracker if available
            if self._state_tracker:
                await self._state_tracker.on_standby()
