#
# Copyright (c) 2024–2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Pipecat Quickstart Example.

The example runs a simple voice AI bot that you can connect to using your
browser and speak with it. You can also deploy this bot to Pipecat Cloud.

Required AI services:
- Deepgram (Speech-to-Text)
- OpenAI (LLM)
- Cartesia (Text-to-Speech)

Run the bot using::

    uv run bot.py
"""

import os
import json

from dotenv import load_dotenv
from loguru import logger

from home_assistant import (
    HomeAssistantClient,
    generate_openai_functions,
    handle_function_call,
)
from timer_manager import TimerManager

print("🚀 Starting Pipecat bot...")
print("⏳ Loading models and imports (20 seconds, first run only)\n")

logger.info("Loading Local Smart Turn Analyzer V3...")
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3

logger.info("✅ Local Smart Turn Analyzer V3 loaded")
logger.info("Loading Silero VAD model...")
from pipecat.audio.vad.silero import SileroVADAnalyzer

logger.info("✅ Silero VAD model loaded")

from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame, LLMSetToolsFrame, TTSSpeakFrame

from openwakeword_processor import OpenWakeWordProcessor

logger.info("Loading pipeline components...")
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frameworks.rtvi import RTVIConfig, RTVIObserver, RTVIProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.daily.transport import DailyParams

logger.info("✅ All components loaded successfully!")

load_dotenv(override=True)


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    logger.info(f"Starting bot")

    # Initialize Home Assistant client
    ha_url = os.getenv("HOME_ASSISTANT_URL")
    ha_token = os.getenv("HOME_ASSISTANT_TOKEN")
    room_name = os.getenv("ROOM_NAME", "bedroom")  # Default to bedroom

    ha_client = None
    ha_summary = ""
    if ha_url and ha_token:
        ha_client = HomeAssistantClient(ha_url, ha_token, room_name=room_name)
        logger.info(f"Initializing Home Assistant client for room: {room_name}...")
        await ha_client.fetch_entities()
        ha_summary = ha_client.get_entity_summary()
        logger.info(f"Home Assistant ready: {ha_summary}")
    else:
        logger.warning("Home Assistant credentials not found. Smart home features disabled.")

    stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))

    tts = CartesiaTTSService(
        api_key=os.getenv("CARTESIA_API_KEY"),
        voice_id="71a7ad14-091c-4e8e-a314-022ece01c121",  # British Reading Lady
    )

    # Configure LLM
    llm = OpenAILLMService(api_key=os.getenv("OPENAI_API_KEY"))

    # Build system prompt
    system_prompt = "You are a helpful AI assistant. Be concise and direct - keep your responses brief and to the point. Respond naturally but avoid unnecessary elaboration.\n\nIMPORTANT: Never make up or hallucinate information. If you don't know something or can't retrieve it, say so clearly. Only provide information you can verify through function calls or that is explicitly provided to you."

    # Add timer capabilities
    system_prompt += "\n\nYou can set and manage timers for the user. When they ask for a timer, use the timer functions."

    if ha_client:
        system_prompt += f"\n\nYou are located in the {room_name}. IMPORTANT: ALL device queries and controls default to THIS ROOM ONLY unless the user explicitly mentions another room."
        system_prompt += f"\n\n{ha_summary}"
        system_prompt += "\n\nWhen the user asks about devices (e.g., 'which lights are on?', 'turn off the lights'), they mean devices in the current room. Only access devices in other rooms if explicitly requested."
        system_prompt += "\n\nCRITICAL: You MUST use the get_device_state function to retrieve real-time sensor data (temperature, humidity, CO2, etc.). NEVER guess or make up sensor values. If the user asks about temperature, humidity, CO2, or any sensor reading, ALWAYS call get_device_state with the appropriate entity_id from the sensor list above. Do not respond with estimated or remembered values."

    messages = [
        {
            "role": "system",
            "content": system_prompt,
        },
    ]

    context = LLMContext(messages)
    context_aggregator = LLMContextAggregatorPair(context)

    # Register Home Assistant function handlers if available
    if ha_client:
        async def handle_ha_function(params):
            """Generic handler for all Home Assistant function calls."""
            # Note: timer_manager will be available when this is called
            result = await handle_function_call(
                ha_client,
                params.function_name,
                params.arguments,
                timer_manager
            )
            await params.result_callback(result)

        llm.register_function("turn_on_device", handle_ha_function)
        llm.register_function("turn_off_device", handle_ha_function)
        llm.register_function("set_temperature", handle_ha_function)
        llm.register_function("get_device_state", handle_ha_function)
        llm.register_function("list_devices", handle_ha_function)

    rtvi = RTVIProcessor(config=RTVIConfig(config=[]))

    # Audio-based wake word detection using OpenWakeWord
    logger.info("Initializing OpenWakeWord wake word detection...")
    wake_processor = OpenWakeWordProcessor(
        wake_words=["alexa"],                 # Use alexa for now (hey_jarvis requires custom model)
        threshold=0.5,                        # Confidence threshold (0.3-0.7)
        keepalive_timeout=5.0,                # Stay awake for 5 seconds
        inference_framework="onnx"            # ONNX supports Python 3.13
    )
    logger.info("✅ Wake word processor ready")

    # Suppress harmless Deepgram finalization warnings
    import logging
    logging.getLogger("deepgram.clients.common.v1.abstract_async_websocket").setLevel(logging.CRITICAL)

    pipeline = Pipeline(
        [
            transport.input(),  # Transport user input
            rtvi,  # RTVI processor
            wake_processor,  # Audio-based wake word detection
            stt,  # Speech-to-Text (only processes when awake)
            context_aggregator.user(),  # User responses
            llm,  # LLM
            tts,  # TTS
            transport.output(),  # Transport bot output
            context_aggregator.assistant(),  # Assistant spoken responses
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=[RTVIObserver(rtvi)],
    )

    # Initialize timer manager
    timer_manager = TimerManager(task)
    logger.info("Timer manager initialized")

    # Register timer functions (always available)
    async def handle_timer_function(params):
        """Handler for timer function calls."""
        result = await handle_function_call(
            None,  # No HA client needed for timers
            params.function_name,
            params.arguments,
            timer_manager
        )
        await params.result_callback(result)

    llm.register_function("set_timer", handle_timer_function)
    llm.register_function("cancel_timer", handle_timer_function)
    llm.register_function("list_timers", handle_timer_function)
    llm.register_function("get_timer_status", handle_timer_function)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"Client connected")

        # Set tools if Home Assistant is enabled
        frames_to_queue = []
        if ha_client:
            tools_frame = LLMSetToolsFrame(tools=generate_openai_functions())
            frames_to_queue.append(tools_frame)

        # Explain wake word usage
        frames_to_queue.append(
            TTSSpeakFrame("Hello! Say 'Alexa' to wake me up.")
        )

        # Kick off the conversation.
        messages.append({"role": "system", "content": "Briefly introduce yourself and your capabilities."})
        frames_to_queue.append(LLMRunFrame())

        await task.queue_frames(frames_to_queue)

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"Client disconnected")
        # Cancel all timers
        await timer_manager.cancel_all_timers()
        # Clean up Home Assistant connections
        if ha_client:
            await ha_client.close()
        await task.cancel()

    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)

    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    """Main bot entry point for the bot starter."""

    transport_params = {
        "daily": lambda: DailyParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(
                stop_secs=0.1,
                start_secs=0.1,
                confidence=0.6
            )),
            turn_analyzer=LocalSmartTurnAnalyzerV3(),
        ),
        "webrtc": lambda: TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(
                stop_secs=0.1,
                start_secs=0.1,
                confidence=0.6
            )),
            turn_analyzer=LocalSmartTurnAnalyzerV3(),
        ),
    }

    transport = await create_transport(runner_args, transport_params)

    await run_bot(transport, runner_args)


if __name__ == "__main__":
    import sys
    from pipecat.runner.run import main

    # Set default host and port if not already specified
    if "--host" not in sys.argv:
        sys.argv.extend(["--host", "0.0.0.0"])
    if "--port" not in sys.argv:
        sys.argv.extend(["--port", "7860"])

    main()
