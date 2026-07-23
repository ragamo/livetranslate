# LiveTranslate

Real-time audio translation for Google Meet using Gemini Live API.

Captures your microphone, translates speech in real-time (Spanish → English by default), and outputs the translated audio to a virtual audio device that Google Meet can use as a microphone input.

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

Reinicia tu Mac después de instalar.

### 2. Install Python dependencies

```bash
pip3 install -r requirements.txt
```

### 3. Set your API key

Obtén una clave en [Google AI Studio](https://aistudio.google.com/apikey).

```bash
export GOOGLE_API_KEY="your-key-here"
```

## Usage

```bash
python livetranslate.py
```

### Options

| Flag | Description |
|------|-------------|
| `--from LANG` | Source language code (default: `es`) |
| `--to LANG` | Target language code (default: `en`) |
| `--mix` | Send both original audio and translation to the virtual device |
| `--monitor` | Play translated audio on your speakers so you can hear it |
| `--list-devices` | List available audio devices and exit |

### Examples

```bash
# Spanish to English (default)
python livetranslate.py

# English to Spanish
python livetranslate.py --from en --to es

# Mix mode: participants hear your original voice + translation
python livetranslate.py --mix

# Monitor: hear the translation in your own speakers
python livetranslate.py --monitor

# Combine options
python livetranslate.py --from es --to fr --monitor
```

### Supported languages

`es` Spanish, `en` English, `fr` French, `de` German, `pt` Portuguese, `it` Italian, `ja` Japanese, `ko` Korean, `zh` Chinese, `ru` Russian, `ar` Arabic.

## Google Meet configuration

1. Run `livetranslate.py`
2. Open Google Meet
3. Go to **Settings → Audio**
4. Set **Microphone** to **BlackHole 2ch**
5. Keep your regular speakers/headphones as the output device

The participants will hear the translated audio as if it were your microphone.

## How it works

```
Microphone → LiveTranslate → Gemini Live API → Translated Audio → BlackHole → Google Meet
                                                                 ↘ Speakers (--monitor)
```

1. Captures audio from your default microphone
2. Streams it to Gemini Live API with a translation system prompt
3. Receives translated audio in real-time
4. Outputs it to the BlackHole virtual audio device
5. Google Meet picks up the translated audio from BlackHole as a microphone input
