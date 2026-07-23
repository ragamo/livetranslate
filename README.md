# LiveTranslate

Real-time audio translation for Google Meet using Gemini Live Translate API.

Your microphone always forwards to a virtual audio device (BlackHole). When translation is active, the translated audio is mixed in — participants hear your original voice plus the translation. Use it from the command line or the macOS menu bar app.

## Requirements

- macOS
- Python 3.11+
- [BlackHole](https://existential.audio/blackhole/) virtual audio driver
- Google API key with Gemini access

## Setup

### 1. Install BlackHole

```bash
brew install blackhole-2ch
```

Restart your Mac after installing.

### 2. Install PortAudio (required for PyAudio)

```bash
brew install portaudio
```

### 3. Install Python dependencies

```bash
pip3 install -r requirements.txt
```

### 4. Set your API key

Get a key at [Google AI Studio](https://aistudio.google.com/apikey).

Create a `.env` file:

```
GEMINI_API_KEY=your-key-here
```

## Usage

### CLI

```bash
python3 livetranslate.py
```

Starts forwarding mic to BlackHole and translates immediately.

### Menu Bar App

```bash
python3 menubar.py
```

Shows "LT" in your macOS menu bar. Mic forwarding starts automatically. Use the menu to start/stop translation, change languages, pick a voice, and select output device.

### Options (CLI)

| Flag | Description |
|------|-------------|
| `--from LANG` | Source language code (default: `es`) |
| `--to LANG` | Target language code (default: `en`) |
| `--voice NAME` | Voice for translation (default: `Zephyr`) |
| `--monitor` | Play translated audio on your headphones so you can hear it |
| `--list-devices` | List available audio devices and exit |

### Examples

```bash
# Spanish to English (default)
python3 livetranslate.py

# English to Spanish
python3 livetranslate.py --from en --to es

# Hear the translation yourself
python3 livetranslate.py --monitor

# Use a different voice
python3 livetranslate.py --voice Charon

# Combine options
python3 livetranslate.py --from es --to fr --monitor --voice Aoede
```

### Supported languages

| Code | Language |
|------|----------|
| `es` | Spanish |
| `en` | English |
| `fr` | French |
| `de` | German |
| `pt` | Portuguese |
| `it` | Italian |
| `ja` | Japanese |
| `ko` | Korean |
| `zh` | Chinese |
| `ru` | Russian |
| `ar` | Arabic |

### Available voices

`Zephyr` (default), `Puck`, `Charon`, `Kore`, `Fenrir`, `Aoede`, `Leda`, `Orus`, `Perseus`

## Google Meet configuration

1. Run `python3 menubar.py` (or `livetranslate.py`)
2. Open Google Meet
3. Go to **Settings → Audio**
4. Set **Microphone** to **BlackHole 2ch**
5. Keep your regular speakers/headphones as the output device

Participants will hear your original voice. When you start translation, they'll also hear the translated version.

## How it works

```
                              ┌─────────────────────────┐
                              │   Gemini Live Translate  │
                              │   (es → en)             │
                              └────────┬────────────────┘
                                       │ translated audio
                                       ▼
Microphone ──→ LiveTranslate ──→ BlackHole 2ch ──→ Google Meet
                    │
                    └──→ Headphones (--monitor)
```

1. Mic audio is captured and always forwarded to BlackHole (your virtual mic in Meet)
2. When translation is active, the same audio is sent to Gemini Live Translate API
3. Translated audio comes back and is also written to BlackHole
4. Google Meet picks up everything from BlackHole as your microphone input
5. With `--monitor`, translated audio also plays on your headphones

## Menu Bar App features

- **Start/Stop Translation** — toggle translation without interrupting mic forwarding
- **From / To** — change source and target language
- **Voice** — select from 9 available voices
- **Output Device** — choose where monitor audio plays (headphones, speakers, etc.)
- **Monitor** — hear the translation in your headphones
- **Refresh Devices** — rescan audio devices if you plug/unplug something

## Notes

- Use headphones when using `--monitor` to avoid feedback loops
- Mic → BlackHole is always active regardless of translation state
- The app uses the dedicated `gemini-3.5-live-translate-preview` model optimized for real-time translation
- All audio runs at 24kHz for optimal quality
