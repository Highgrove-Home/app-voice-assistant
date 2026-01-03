# Voice Assistant with Home Assistant Integration

A voice-controlled AI assistant built with Pipecat that integrates with Home Assistant for smart home control. Features room-aware device control, timer management, and real-time sensor monitoring.

## Features

- **Wake Word Detection**: Server-side audio-based wake word detection using OpenWakeWord ("Alexa")
- **Smart Home Control**: Control lights, switches, thermostats, and other Home Assistant devices via voice
- **Room-Aware**: Automatically scopes device control to the configured room (e.g., "turn off the lights" only affects bedroom lights)
- **Real-time Sensors**: Query temperature, humidity, CO2, and other sensor data
- **Timer Management**: Set, cancel, and check voice-controlled timers with automatic notifications
- **WebSocket Integration**: Efficient persistent connection to Home Assistant for area/device registry access
- **Anti-Hallucination**: Configured to only provide verified data through function calls

## Architecture

### Overview

The voice assistant uses a **client-server architecture** where:
- **Server** runs on Raspberry Pi (port 7860), hosting the Pipecat pipeline
- **Clients** connect via browser using WebRTC
- **Audio streaming** is continuous - the client constantly sends audio to the server
- **Wake word detection** happens server-side using OpenWakeWord
- **Speech processing** only occurs after "Alexa" is detected

This design provides privacy (no cloud wake word detection), reduces API costs (STT only runs when needed), and allows the assistant to work offline for wake word detection.

### System Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         Browser Client                               │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  Microphone → WebRTC (continuous audio stream) → Speaker       │ │
│  └────────────────────────────────────────────────────────────────┘ │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ WebRTC Connection (audio in/out)
                                ↓
┌─────────────────────────────────────────────────────────────────────┐
│                    Raspberry Pi Server (bot.py)                      │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Pipecat Pipeline                          │   │
│  │                                                              │   │
│  │  1. WebRTC Input Transport                                  │   │
│  │     • Receives raw audio (16kHz, mono)                      │   │
│  │     • Receives control messages                             │   │
│  │         ↓                                                    │   │
│  │  2. OpenWakeWord Processor ⭐                               │   │
│  │     • Analyzes ALL incoming audio in 80ms chunks            │   │
│  │     • Runs ONNX model to detect "Alexa" (threshold: 0.5)    │   │
│  │     • Blocks audio frames when asleep                       │   │
│  │     • Allows control frames (LLMSetToolsFrame) through      │   │
│  │     • Wakes for 5 seconds after detection                   │   │
│  │         ↓ (audio only passes when awake)                    │   │
│  │  3. Deepgram STT                                            │   │
│  │     • Converts speech → text (only when awake)              │   │
│  │     • WebSocket connection to Deepgram                      │   │
│  │         ↓                                                    │   │
│  │  4. User Context Aggregator                                 │   │
│  │     • Builds conversation history                           │   │
│  │         ↓                                                    │   │
│  │  5. OpenAI LLM (GPT-4)                                      │   │
│  │     • Function calling for Home Assistant                   │   │
│  │     • Function calling for timer management                 │   │
│  │     • Room-aware system prompt                              │   │
│  │         ↓                                                    │   │
│  │  6. Cartesia TTS                                            │   │
│  │     • Converts text → speech                                │   │
│  │     • British Reading Lady voice                            │   │
│  │         ↓                                                    │   │
│  │  7. WebRTC Output Transport                                 │   │
│  │     • Streams audio back to browser                         │   │
│  │         ↓                                                    │   │
│  │  8. Assistant Context Aggregator                            │   │
│  │     • Stores assistant responses in history                 │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │              Home Assistant Integration                      │   │
│  │                                                              │   │
│  │  WebSocket Connection (persistent):                         │   │
│  │    • Area registry (bedroom → area_id)                      │   │
│  │    • Device registry (device → area mapping)                │   │
│  │    • Entity registry (entity → device/area mapping)         │   │
│  │                                                              │   │
│  │  REST API (per-request):                                    │   │
│  │    • Get entity states                                      │   │
│  │    • Call services (turn_on, turn_off, set_temperature)     │   │
│  │                                                              │   │
│  │  Room Filtering:                                            │   │
│  │    • Filters all entities to configured room (bedroom)      │   │
│  │    • Only exposes room devices to LLM                       │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### Audio Flow

**When Asleep (Default State):**
1. Client continuously sends audio → Server receives audio frames
2. OpenWakeWord analyzes every 80ms chunk for "Alexa"
3. Audio frames are **blocked** from reaching Deepgram STT
4. Control frames (LLMSetToolsFrame, system frames) pass through
5. No STT processing → No API costs

**When "Alexa" Detected:**
1. OpenWakeWord detects wake word (confidence ≥ 0.5)
2. State changes to "awake" for 5 seconds
3. Audio frames now **pass through** to Deepgram
4. Deepgram transcribes speech → text
5. Text goes to LLM for processing
6. LLM response → TTS → audio output to client
7. After 5 seconds of silence, returns to sleep

### Wake Word Detection Details

**OpenWakeWord Processor (`openwakeword_processor.py`):**
- Buffers incoming audio into 80ms chunks (1280 samples at 16kHz)
- Runs ONNX inference on each chunk
- Detects "Alexa" with configurable confidence threshold (default: 0.5)
- Uses keepalive mechanism - stays awake for 5 seconds after detection
- **Frame Filtering Logic:**
  - Audio frames: Block when asleep, pass when awake
  - Transcription frames: Block when asleep, pass when awake
  - Control frames (LLMSetToolsFrame, etc.): Always pass through
  - Bot output frames: Always pass through

**Why Server-Side Wake Word Detection?**
- **Privacy**: Audio analyzed locally on Pi, not sent to cloud
- **Cost**: Deepgram STT only runs when needed (after wake word)
- **Reliability**: OpenWakeWord works offline, doesn't depend on STT accuracy
- **Flexibility**: Can use custom wake word models in the future

### Home Assistant Integration

**Room-Aware Device Control:**
1. On startup, fetch area registry via WebSocket to find "bedroom" → `area_id`
2. Fetch device registry to map devices → areas
3. Fetch entity registry to map entities → devices → areas
4. Filter all entities to only those in the configured room
5. Build entity summary with lights, sensors, switches in that room
6. LLM system prompt emphasizes "you are in the bedroom"
7. All device queries default to current room unless user specifies otherwise

**Function Calling:**
- `turn_on_device(entity_id)` - Turn on light/switch in current room
- `turn_off_device(entity_id)` - Turn off light/switch in current room
- `set_temperature(entity_id, temperature)` - Set thermostat
- `get_device_state(entity_id)` - Read real-time sensor data
- `list_devices(device_type)` - List lights, sensors, etc. in current room

**Anti-Hallucination:**
- System prompt explicitly forbids making up sensor values
- Forces LLM to call `get_device_state()` for temperature, humidity, CO2
- Only provides entity IDs that actually exist in Home Assistant
- Validates all function calls against actual device registry

### Timer Management

**AsyncIO-Based Timers (`timer_manager.py`):**
- Stores active timers in memory with asyncio background tasks
- Each timer runs countdown in separate task
- On expiry, queues TTS announcement to pipeline
- Can set named timers: "pasta timer", "laundry timer"
- Can cancel by name or cancel all timers

### Key Design Decisions

1. **Continuous Audio Streaming**: Client always sends audio, server decides what to process
   - Pros: Instant wake word detection, low latency
   - Cons: Higher bandwidth usage (mitigated by WebRTC compression)

2. **Server-Side Wake Word**: Processing happens on Pi, not in browser
   - Pros: Privacy, works on any client, centralized control
   - Cons: Pi must handle audio processing (acceptable with ONNX)

3. **Frame Blocking vs STT Muting**: Block frames before they reach STT service
   - Pros: Clean pipeline control, clear state management
   - Cons: Slightly more complex frame filtering logic

4. **WebSocket for HA Registry**: Persistent connection for area/device/entity data
   - Pros: Efficient, real-time updates possible in future
   - Cons: More complex than REST-only approach

5. **Room Filtering at Startup**: Pre-filter entities to room before LLM sees them
   - Pros: Reduces LLM confusion, smaller context, clearer intent
   - Cons: Can't easily control other rooms without code change

## Prerequisites

### Environment

- Python 3.10-3.12 (3.13+ not supported - openwakeword requires tflite-runtime which only supports up to 3.12)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) package manager installed

### AI Service API Keys

- [Deepgram](https://console.deepgram.com/signup) for Speech-to-Text
- [OpenAI](https://auth.openai.com/create-account) for LLM inference
- [Cartesia](https://play.cartesia.ai/sign-up) for Text-to-Speech

### Home Assistant

- Home Assistant server (2025.12.5 or later recommended)
- Long-lived access token ([create one here](https://www.home-assistant.io/docs/authentication/#your-account-profile))
- Configured areas/rooms with assigned devices

## Setup

1. Clone this repository

   ```bash
   git clone https://github.com/Highgrove-Home/app-voice-assistant.git
   cd app-voice-assistant
   ```

2. Configure environment variables

   Create a `.env` file:

   ```bash
   cp env.example .env
   ```

   Add your API keys and Home Assistant configuration:

   ```ini
   # AI Service API Keys
   DEEPGRAM_API_KEY=your_deepgram_api_key
   OPENAI_API_KEY=your_openai_api_key
   CARTESIA_API_KEY=your_cartesia_api_key

   # Home Assistant Configuration
   HOME_ASSISTANT_URL=http://homeassistant.local:8123
   HOME_ASSISTANT_TOKEN=your_home_assistant_long_lived_access_token
   ROOM_NAME=bedroom
   ```

3. Install dependencies

   ```bash
   uv python install 3.11
   uv python pin 3.11
   uv sync
   ```

## Running the Bot

```bash
uv run bot.py
```

Open http://localhost:7860/client in your browser and click `Connect` to start talking to your assistant.

To access from other devices on your network, use `http://YOUR_IP:7860/client`

> 💡 First run note: Initial startup takes ~20 seconds as Pipecat downloads required models.

## Voice Commands

### Wake Word

The assistant uses audio-based wake word detection running on the server. Say **"Alexa"** to activate the assistant.

Once activated, the assistant stays awake for 5 seconds. Say "Alexa" again to wake it up for another command.

### Smart Home Control

- "Alexa, turn on the lights"
- "Alexa, turn off the fan"
- "Alexa, what's the temperature?"
- "What's the humidity?" (within 5 seconds of wake)
- "Which lights are on?"
- "Set the thermostat to 72 degrees"

### Timer Management

- "Alexa, set a timer for 10 minutes"
- "Set a pasta timer for 8 minutes"
- "Cancel the pasta timer"
- "List all timers"
- "How much time is left on the timer?"

## Configuration

### Wake Word Settings

Customize wake word in `bot.py` (line 146-151):
```python
wake_processor = OpenWakeWordProcessor(
    wake_words=["alexa"],              # Wake word models to use
    threshold=0.5,                      # Detection confidence (0.0-1.0)
    keepalive_timeout=5.0,              # Seconds to stay awake
    inference_framework="onnx"          # ONNX or tflite
)
```

**Parameters:**
- `wake_words`: List of wake word model names (e.g., `["alexa"]`, `["hey_jarvis"]`)
- `threshold`: Detection confidence threshold (0.0-1.0, higher = more strict)
- `keepalive_timeout`: How long to stay awake after detection (default: 5.0 seconds)
- `inference_framework`: "onnx" (recommended, better Python 3.13 support) or "tflite"

**Available Pre-trained Models:**
- `alexa` - Detects "Alexa"
- `hey_mycroft` - Detects "Hey Mycroft"
- Custom models can be added to `openwakeword/resources/models/`

### VAD Settings

Voice Activity Detection configured in `bot.py`:
- `stop_secs: 0.1` - Fast end-of-turn detection
- `start_secs: 0.1` - Quick speech start detection
- `confidence: 0.6` - Balanced sensitivity

### System Prompt

The assistant is configured to:
- Be concise and direct in responses
- Never hallucinate or make up information
- Always use function calls for real-time data
- Default to current room for all device operations
- Only access other rooms when explicitly requested

## Troubleshooting

### Python Version Issues

If you see `tflite-runtime` wheel errors:
```bash
uv python install 3.11
uv python pin 3.11
uv sync
```

OpenWakeWord requires `tflite-runtime` which only supports Python 3.10-3.12.

### Room Filtering Not Working

- Verify `ROOM_NAME` matches a Home Assistant area name (case-insensitive)
- Check startup logs for "Found area 'bedroom' with ID: xyz"
- Ensure devices are assigned to areas in Home Assistant

### Sensor Not Found

The assistant needs actual entity IDs. Check startup logs for "Key sensors:" to see available sensors in your room.

## License

BSD 2-Clause License (inherited from Pipecat)
