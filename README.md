# Voice Assistant with Home Assistant Integration

A voice-controlled AI assistant built with Pipecat that integrates with Home Assistant for smart home control. Features room-aware device control, timer management, and real-time sensor monitoring.

## Features

- **Smart Home Control**: Control lights, switches, thermostats, and other Home Assistant devices via voice
- **Room-Aware**: Automatically scopes device control to the configured room (e.g., "turn off the lights" only affects bedroom lights)
- **Real-time Sensors**: Query temperature, humidity, CO2, and other sensor data
- **Timer Management**: Set, cancel, and check voice-controlled timers with automatic notifications
- **WebSocket Integration**: Efficient persistent connection to Home Assistant for area/device registry access
- **Anti-Hallucination**: Configured to only provide verified data through function calls

## Prerequisites

### Environment

- Python 3.10-3.13 (3.14 not yet supported due to onnxruntime dependencies)
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
   uv python install 3.13
   uv python pin 3.13
   uv sync
   ```

## Running the Bot

```bash
uv run bot.py
```

Open http://localhost:7860 in your browser and click `Connect` to start talking to your assistant.

> 💡 First run note: Initial startup takes ~20 seconds as Pipecat downloads required models.

## Voice Commands

### Smart Home Control

- "Turn on the lights"
- "Turn off the fan"
- "What's the temperature?"
- "What's the humidity?"
- "Which lights are on?"
- "Set the thermostat to 72 degrees"

### Timer Management

- "Set a timer for 10 minutes"
- "Set a pasta timer for 8 minutes"
- "Cancel the pasta timer"
- "List all timers"
- "How much time is left on the timer?"

## Architecture

### Components

- **bot.py**: Main entry point, pipeline configuration, and event handlers
- **home_assistant.py**: Home Assistant API client with WebSocket support and function definitions
- **timer_manager.py**: Asyncio-based timer management with TTS announcements

### Home Assistant Integration

The assistant uses:
- **REST API** for entity states and service calls
- **WebSocket API** for area/device/entity registry data (persistent connection)
- **Area filtering** to scope device control to the configured room

### Room-Aware Control

Devices are filtered by Home Assistant areas:
1. Fetches area registry via WebSocket to find the configured room's area_id
2. Fetches device and entity registries to map entities to areas
3. Filters all queries/controls to only include entities in the configured room
4. Only accesses other rooms when explicitly requested by the user

## Configuration

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

If you see `onnxruntime` wheel errors:
```bash
uv python install 3.13
uv python pin 3.13
uv sync
```

### Room Filtering Not Working

- Verify `ROOM_NAME` matches a Home Assistant area name (case-insensitive)
- Check startup logs for "Found area 'bedroom' with ID: xyz"
- Ensure devices are assigned to areas in Home Assistant

### Sensor Not Found

The assistant needs actual entity IDs. Check startup logs for "Key sensors:" to see available sensors in your room.

## License

BSD 2-Clause License (inherited from Pipecat)
