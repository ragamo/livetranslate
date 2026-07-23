"""
LiveTranslate - Real-time audio translation for Google Meet

Captures your microphone, translates speech via Gemini Live API,
and outputs translated audio to a virtual device (BlackHole).

## Setup

1. Install BlackHole virtual audio driver:
   brew install blackhole-2ch

2. Install Python dependencies:
   pip install -r requirements.txt

3. Set your Google API key:
   export GOOGLE_API_KEY="your-key-here"

## Usage

   python livetranslate.py                    # Spanish → English, translation only
   python livetranslate.py --mix              # Mix original + translation
   python livetranslate.py --from en --to es  # English → Spanish

In Google Meet: select "BlackHole 2ch" as your microphone input.
"""

import asyncio
import argparse
import os
import sys
import traceback

import pyaudio
from dotenv import load_dotenv

from google import genai
from google.genai import types

load_dotenv()

if sys.version_info < (3, 11, 0):
    import taskgroup, exceptiongroup

    asyncio.TaskGroup = taskgroup.TaskGroup
    asyncio.ExceptionGroup = exceptiongroup.ExceptionGroup

FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_SAMPLE_RATE = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

MODEL = "gemini-3.1-flash-live-preview"

pya = pyaudio.PyAudio()


def find_blackhole_device():
    """Find BlackHole virtual audio device index."""
    for i in range(pya.get_device_count()):
        info = pya.get_device_info_by_index(i)
        if "blackhole" in info["name"].lower() and info["maxOutputChannels"] > 0:
            return i, info["name"]
    return None, None


def list_audio_devices():
    """List all available audio devices."""
    print("\nAvailable audio devices:")
    print("-" * 60)
    for i in range(pya.get_device_count()):
        info = pya.get_device_info_by_index(i)
        direction = []
        if info["maxInputChannels"] > 0:
            direction.append("IN")
        if info["maxOutputChannels"] > 0:
            direction.append("OUT")
        print(f"  [{i}] {info['name']} ({'/'.join(direction)})")
    print()


class LiveTranslator:
    def __init__(self, source_lang="es", target_lang="en", mix_mode=False, monitor=False, monitor_all=False, monitor_device_index=None):
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.mix_mode = mix_mode
        self.monitor = monitor or monitor_all
        self.monitor_all = monitor_all
        self.monitor_device_index = monitor_device_index

        self.audio_in_queue = asyncio.Queue()
        self.out_queue = asyncio.Queue(maxsize=5)

        self.session = None
        self.audio_stream = None
        self.running = False
        self.model_speaking = False

    def _build_config(self):
        lang_names = {
            "es": "Spanish", "en": "English", "fr": "French",
            "de": "German", "pt": "Portuguese", "it": "Italian",
            "ja": "Japanese", "ko": "Korean", "zh": "Chinese",
            "ru": "Russian", "ar": "Arabic",
        }
        src = lang_names.get(self.source_lang, self.source_lang)
        tgt = lang_names.get(self.target_lang, self.target_lang)

        system_instruction = (
            f"You are a real-time interpreter. Listen to speech in {src} and "
            f"immediately translate it to {tgt}. Speak ONLY the translation — "
            f"do not add commentary, do not repeat the original, do not explain. "
            f"Translate naturally and fluently as a professional simultaneous interpreter would. "
            f"If you hear silence or non-speech sounds, remain silent."
        )

        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=types.Content(
                parts=[types.Part(text=system_instruction)]
            ),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Zephyr")
                )
            ),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            context_window_compression=types.ContextWindowCompressionConfig(
                trigger_tokens=25600,
                sliding_window=types.SlidingWindow(target_tokens=12800),
            ),
        )

    async def listen_audio(self):
        """Capture microphone audio and queue it for sending."""
        mic_info = pya.get_default_input_device_info()
        self.audio_stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            input=True,
            input_device_index=mic_info["index"],
            frames_per_buffer=CHUNK_SIZE,
        )

        if __debug__:
            kwargs = {"exception_on_overflow": False}
        else:
            kwargs = {}

        try:
            while self.running:
                data = await asyncio.to_thread(self.audio_stream.read, CHUNK_SIZE, **kwargs)
                if self.monitor_all:
                    try:
                        self.monitor_mic_queue.put_nowait(data)
                    except asyncio.QueueFull:
                        _ = self.monitor_mic_queue.get_nowait()
                        self.monitor_mic_queue.put_nowait(data)
                if self.model_speaking:
                    continue
                payload = {"data": data, "mime_type": "audio/pcm;rate=16000"}
                try:
                    self.out_queue.put_nowait(payload)
                except asyncio.QueueFull:
                    _ = self.out_queue.get_nowait()
                    self.out_queue.put_nowait(payload)
        except asyncio.CancelledError:
            pass
        finally:
            if self.audio_stream:
                self.audio_stream.stop_stream()
                self.audio_stream.close()

    async def output_to_blackhole(self, blackhole_index):
        """Write translated audio to BlackHole virtual device."""
        stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=RECEIVE_SAMPLE_RATE,
            output=True,
            output_device_index=blackhole_index,
        )
        try:
            while self.running:
                bytestream = await self.audio_in_queue.get()
                await asyncio.to_thread(stream.write, bytestream)
        except asyncio.CancelledError:
            pass
        finally:
            if stream:
                stream.stop_stream()
                stream.close()

    async def monitor_audio(self):
        """Play translated audio to default speakers so user can hear it."""
        kwargs = {}
        if self.monitor_device_index is not None:
            kwargs["output_device_index"] = self.monitor_device_index
        stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=RECEIVE_SAMPLE_RATE,
            output=True,
            **kwargs,
        )
        try:
            while self.running:
                bytestream = await self.monitor_queue.get()
                await asyncio.to_thread(stream.write, bytestream)
        except asyncio.CancelledError:
            pass
        finally:
            if stream:
                stream.stop_stream()
                stream.close()

    async def monitor_mic_audio(self):
        """Play original mic audio to default speakers (--monitor-all)."""
        kwargs = {}
        if self.monitor_device_index is not None:
            kwargs["output_device_index"] = self.monitor_device_index
        stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            output=True,
            **kwargs,
        )
        try:
            while self.running:
                bytestream = await self.monitor_mic_queue.get()
                await asyncio.to_thread(stream.write, bytestream)
        except asyncio.CancelledError:
            pass
        finally:
            if stream:
                stream.stop_stream()
                stream.close()

    async def mix_original_to_blackhole(self, blackhole_index):
        """In mix mode, also send original mic audio to BlackHole."""
        stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            output=True,
            output_device_index=blackhole_index,
        )
        try:
            while self.running:
                data = await self.mix_queue.get()
                await asyncio.to_thread(stream.write, data)
        except asyncio.CancelledError:
            pass
        finally:
            if stream:
                stream.stop_stream()
                stream.close()

    async def receive_audio(self):
        """Receive translated audio from Gemini and route it."""
        try:
            while self.running:
                async for response in self.session.receive():
                    server_content = response.server_content
                    if server_content is None:
                        continue

                    if server_content.interrupted:
                        self.model_speaking = False
                        while not self.audio_in_queue.empty():
                            self.audio_in_queue.get_nowait()

                    if server_content.model_turn:
                        self.model_speaking = True
                        for part in server_content.model_turn.parts:
                            if part.inline_data:
                                self.audio_in_queue.put_nowait(part.inline_data.data)
                                if self.monitor:
                                    self.monitor_queue.put_nowait(part.inline_data.data)

                    if server_content.turn_complete:
                        self.model_speaking = False
                        # Flush stale mic audio that accumulated while model was speaking
                        while not self.out_queue.empty():
                            self.out_queue.get_nowait()

                    if server_content.input_transcription:
                        print(f"\r[YOU] {server_content.input_transcription.text}", end="", flush=True)

                    if server_content.output_transcription:
                        print(f"\r[TR]  {server_content.output_transcription.text}", end="", flush=True)
        except asyncio.CancelledError:
            pass

    async def send_realtime(self):
        """Send queued audio/video to the Gemini session."""
        try:
            while self.running:
                msg = await self.out_queue.get()
                blob = types.Blob(data=msg["data"], mime_type=msg["mime_type"])
                await self.session.send_realtime_input(audio=blob)

                if self.mix_mode:
                    try:
                        self.mix_queue.put_nowait(msg["data"])
                    except asyncio.QueueFull:
                        _ = self.mix_queue.get_nowait()
                        self.mix_queue.put_nowait(msg["data"])
        except asyncio.CancelledError:
            pass

    async def wait_for_quit(self):
        """Wait for user to press Enter or type 'q' to quit."""
        try:
            while self.running:
                text = await asyncio.to_thread(input, "")
                if text.lower() in ("q", "quit", "exit", ""):
                    self.running = False
                    break
        except (asyncio.CancelledError, EOFError):
            pass

    async def run(self):
        """Main loop: connect to Gemini and run all audio tasks."""
        blackhole_idx, blackhole_name = find_blackhole_device()
        if blackhole_idx is None:
            print("ERROR: BlackHole audio device not found.")
            print("Install it with: brew install blackhole-2ch")
            print("\nAlternatively, available devices:")
            list_audio_devices()
            return

        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            print("ERROR: GEMINI_API_KEY not found in .env or environment.")
            print("Get a key from https://aistudio.google.com/apikey")
            return

        client = genai.Client(api_key=api_key)
        config = self._build_config()

        self.running = True
        self.audio_in_queue = asyncio.Queue()
        self.out_queue = asyncio.Queue(maxsize=5)
        self.monitor_queue = asyncio.Queue()
        self.monitor_mic_queue = asyncio.Queue(maxsize=5)
        self.mix_queue = asyncio.Queue(maxsize=5)

        print(f"LiveTranslate")
        print(f"  Translation: {self.source_lang} -> {self.target_lang}")
        print(f"  Mode: {'mix (original + translation)' if self.mix_mode else 'translation only'}")
        print(f"  Output device: {blackhole_name} [index {blackhole_idx}]")
        monitor_status = "all (voice + translation)" if self.monitor_all else ("translation only" if self.monitor else "off")
        print(f"  Monitor (speakers): {monitor_status}")
        print(f"\n  In Google Meet, select '{blackhole_name}' as your microphone.")
        print(f"\n  Press Enter or type 'q' to stop.\n")

        try:
            async with (
                client.aio.live.connect(model=MODEL, config=config) as session,
                asyncio.TaskGroup() as tg,
            ):
                self.session = session

                quit_task = tg.create_task(self.wait_for_quit())
                tg.create_task(self.send_realtime())
                tg.create_task(self.listen_audio())
                tg.create_task(self.receive_audio())
                tg.create_task(self.output_to_blackhole(blackhole_idx))

                if self.mix_mode:
                    tg.create_task(self.mix_original_to_blackhole(blackhole_idx))

                if self.monitor:
                    tg.create_task(self.monitor_audio())

                if self.monitor_all:
                    tg.create_task(self.monitor_mic_audio())

                await quit_task
                raise asyncio.CancelledError("User requested exit")

        except asyncio.CancelledError:
            pass
        except ExceptionGroup as EG:
            traceback.print_exception(EG)
        finally:
            self.running = False
            print("\nStopped.")


def main():
    parser = argparse.ArgumentParser(
        description="Real-time audio translation for Google Meet via virtual audio device"
    )
    parser.add_argument(
        "--from", dest="source_lang", type=str, default="es",
        help="Source language code (default: es)"
    )
    parser.add_argument(
        "--to", dest="target_lang", type=str, default="en",
        help="Target language code (default: en)"
    )
    parser.add_argument(
        "--mix", action="store_true",
        help="Mix original audio with translation (both go to virtual device)"
    )
    parser.add_argument(
        "--monitor", action="store_true",
        help="Also play translated audio on your speakers (so you can hear it)"
    )
    parser.add_argument(
        "--monitor-all", action="store_true",
        help="Play both your original voice and translation on your speakers"
    )
    parser.add_argument(
        "--list-devices", action="store_true",
        help="List available audio devices and exit"
    )
    args = parser.parse_args()

    if args.list_devices:
        list_audio_devices()
        return

    translator = LiveTranslator(
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        mix_mode=args.mix,
        monitor=args.monitor,
        monitor_all=args.monitor_all,
    )
    asyncio.run(translator.run())


if __name__ == "__main__":
    main()
