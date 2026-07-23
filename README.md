# LiveTranslate

Real-time audio translation for Google Meet using Gemini Live Translate API.

Your microphone always forwards to a virtual audio device (BlackHole). When translation is active, the translated audio is mixed in. By default only translation goes to Meet; with `--mix`, participants hear your original voice plus the translation. Use it from the command line or the macOS menu bar app.

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

### 2. Install Python dependencies

```bash
pip3 install -r requirements.txt
```

### 3. Set your API key

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
| `--mix` | Send both your voice and translation to BlackHole (default: translation only) |
| `--voice NAME` | Voice for translation (default: `Zephyr`) |
| `--monitor` | Play translated audio on your headphones so you can hear it |
| `--list-devices` | List available audio devices and exit |

### Examples

```bash
# Spanish to English (default, translation only to Meet)
python3 livetranslate.py

# English to Spanish
python3 livetranslate.py --from en --to es

# Mix mode: participants hear your voice + translation
python3 livetranslate.py --mix

# Hear the translation yourself
python3 livetranslate.py --monitor

# Use a different voice
python3 livetranslate.py --voice Charon

# Combine options
python3 livetranslate.py --from es --to fr --mix --monitor --voice Aoede
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

By default, participants hear only the translation. With `--mix`, they hear your original voice plus the translation.

## How it works

```
                              ┌─────────────────────────┐
                              │   Gemini Live Translate  │
                              │   (es → en)             │
                              └────────┬────────────────┘
                                       │ translated audio (24kHz)
                                       ▼ resampled to 48kHz
Microphone ──→ sounddevice callback ──→ BlackHole 2ch ──→ Google Meet
  (48kHz)        (single stream)           (48kHz)
                    │
                    └──→ Headphones (--monitor, 24kHz native)
```

1. A single `sounddevice.Stream` handles both mic input and BlackHole output at 48kHz (BlackHole's native rate)
2. The callback mixes mic audio and translation audio into a single buffer — no dual-stream conflicts
3. Mic audio is downsampled to 16kHz and sent to Gemini Live Translate API asynchronously
4. Translated audio (24kHz) is upsampled to 48kHz and fed into the translation buffer
5. The callback consumes the translation buffer each cycle, mixing or substituting as configured
6. With `--monitor`, translated audio plays on your headphones at native 24kHz quality

## Menu Bar App features

- **Start/Stop Translation** — toggle translation without interrupting mic forwarding
- **From / To** — change source and target language
- **Voice** — select from 9 available voices
- **Mix** — toggle between translation-only or voice+translation output
- **Output Device** — choose where monitor audio plays (headphones, speakers, etc.)
- **Monitor** — hear the translation in your headphones
- **Refresh Devices** — rescan audio devices if you plug/unplug something

## Notes

- Use headphones when using `--monitor` to avoid feedback loops
- Mic → BlackHole is always active regardless of translation state
- Uses the dedicated `gemini-3.5-live-translate-preview` model optimized for real-time translation
- Audio runs at 48kHz (BlackHole native) to avoid CoreAudio sample rate conversion artifacts
- Uses `sounddevice` with a single callback stream — no dual-stream conflicts on virtual devices
