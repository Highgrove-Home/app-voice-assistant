"""Timer management for Pipecat voice bot.

This module provides timer functionality with state management using asyncio tasks.
Timers can be set, cancelled, listed, and automatically announce when they expire.
"""

import asyncio
import time
from dataclasses import dataclass
from typing import Dict, Optional, List
from loguru import logger

from pipecat.frames.frames import TTSTextFrame
from pipecat.pipeline.task import PipelineTask


@dataclass
class Timer:
    """Represents a single timer."""
    name: str
    duration_seconds: float
    start_time: float
    task: asyncio.Task


class TimerManager:
    """Manages multiple timers with background asyncio tasks."""

    def __init__(self, pipeline_task: PipelineTask):
        """Initialize the timer manager.

        Args:
            pipeline_task: The PipelineTask instance for queuing announcement frames
        """
        self.pipeline_task = pipeline_task
        self.timers: Dict[str, Timer] = {}
        self._lock = asyncio.Lock()

    async def set_timer(self, duration_minutes: float, name: Optional[str] = None) -> str:
        """Set a new timer.

        Args:
            duration_minutes: Duration in minutes
            name: Optional name for the timer (defaults to duration)

        Returns:
            Confirmation message
        """
        async with self._lock:
            # Generate name if not provided
            if not name:
                name = f"{duration_minutes} minute timer"

            # Check if timer with this name already exists
            if name in self.timers:
                return f"A timer named '{name}' already exists. Please cancel it first or choose a different name."

            duration_seconds = duration_minutes * 60
            start_time = time.time()

            # Create background task
            task = asyncio.create_task(
                self._timer_countdown(name, duration_seconds)
            )

            # Store timer
            self.timers[name] = Timer(
                name=name,
                duration_seconds=duration_seconds,
                start_time=start_time,
                task=task
            )

            logger.info(f"Timer set: {name} for {duration_minutes} minutes")
            return f"Timer '{name}' set for {duration_minutes} minutes."

    async def _timer_countdown(self, name: str, duration_seconds: float):
        """Background task that waits for timer to expire.

        Args:
            name: Timer name
            duration_seconds: Duration to wait in seconds
        """
        try:
            await asyncio.sleep(duration_seconds)

            # Timer expired - announce it
            logger.info(f"Timer expired: {name}")

            # Queue TTS frame to make the bot speak
            announcement = f"Your timer '{name}' is done!"
            await self.pipeline_task.queue_frames([
                TTSTextFrame(text=announcement, aggregated_by="timer")
            ])

            # Clean up timer from dictionary
            async with self._lock:
                if name in self.timers:
                    del self.timers[name]

        except asyncio.CancelledError:
            logger.info(f"Timer cancelled: {name}")
            # Don't announce cancelled timers
            async with self._lock:
                if name in self.timers:
                    del self.timers[name]

    async def cancel_timer(self, name: str) -> str:
        """Cancel a timer by name.

        Args:
            name: Name of the timer to cancel

        Returns:
            Confirmation message
        """
        async with self._lock:
            if name not in self.timers:
                return f"No timer named '{name}' found."

            # Cancel the background task
            self.timers[name].task.cancel()
            del self.timers[name]

            logger.info(f"Timer cancelled: {name}")
            return f"Timer '{name}' cancelled."

    async def list_timers(self) -> str:
        """List all active timers.

        Returns:
            Human-readable list of timers
        """
        async with self._lock:
            if not self.timers:
                return "No active timers."

            timer_list = []
            current_time = time.time()

            for timer in self.timers.values():
                elapsed = current_time - timer.start_time
                remaining = timer.duration_seconds - elapsed

                if remaining > 0:
                    minutes_remaining = remaining / 60
                    timer_list.append(
                        f"'{timer.name}': {minutes_remaining:.1f} minutes remaining"
                    )

            if not timer_list:
                return "No active timers."

            return "Active timers: " + ", ".join(timer_list)

    async def get_timer_status(self, name: str) -> str:
        """Get the status of a specific timer.

        Args:
            name: Name of the timer

        Returns:
            Status message with remaining time
        """
        async with self._lock:
            if name not in self.timers:
                return f"No timer named '{name}' found."

            timer = self.timers[name]
            current_time = time.time()
            elapsed = current_time - timer.start_time
            remaining = timer.duration_seconds - elapsed

            if remaining > 0:
                minutes_remaining = remaining / 60
                return f"Timer '{name}' has {minutes_remaining:.1f} minutes remaining."
            else:
                return f"Timer '{name}' should have expired by now."

    async def cancel_all_timers(self):
        """Cancel all active timers (useful for cleanup)."""
        async with self._lock:
            for timer in self.timers.values():
                timer.task.cancel()

            count = len(self.timers)
            self.timers.clear()
            logger.info(f"Cancelled {count} timers")
