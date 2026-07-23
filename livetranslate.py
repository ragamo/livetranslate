"""
LiveTranslate - Real-time audio translation for Google Meet

Captures your microphone and forwards it to BlackHole virtual device.
When translation is active, also sends translated audio to BlackHole.

## Setup

1. Install BlackHole virtual audio driver:
   brew install blackhole-2ch

2. Install Python dependencies:
   pip3 install -r requirements.txt

3. Set your Google API key in .env:
   GEMINI_API_KEY="your-key-here"

## Usage

   python livetranslate.py                    # Spanish → English
   python livetranslate.py --from en --to es  # English → Spanish
   python livetranslate.py --monitor          # Hear translation in your speakers

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
SEND_SAMPLE_RATE = 24000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE = 1024

MODEL = "gemini-3.5-live-translate-preview"

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
    VOICES = ["Zephyr", "Puck", "Charon", "Kore", "Fenrir", "Aoede", "Leda", "Orus", "Perseus"]

    def __init__(self, source_lang="es", target_lang="en", mix=False, monitor=False, monitor_device_index=None, voice="Zephyr"):
        self.source_lang = source_lang
        self.target_lang = target_lang
        self.mix = mix
        self.monitor = monitor
        self.monitor_device_index = monitor_device_index
        self.voice = voice

        self.session = None
        self.audio_stream = None
        self.running = False
        self.translating = False
        self._stop_event = None
        self._loop = None
        self._blackhole_idx = None

        self.blackhole_queue = None
        self.gemini_queue = None
        self.monitor_queue = None

    def _build_config(self):
        return types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice)
                )
            ),
            translation_config=types.TranslationConfig(
                target_language_code=self.target_lang,
            ),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )

    async def listen_audio(self):
        """Capture mic audio and send to BlackHole queue + Gemini queue."""
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
                if self.mix or not self.translating:
                    self.blackhole_queue.put_nowait(data)
                if self.translating:
                    payload = {"data": data, "mime_type": "audio/pcm;rate=24000"}
                    try:
                        self.gemini_queue.put_nowait(payload)
                    except asyncio.QueueFull:
                        _ = self.gemini_queue.get_nowait()
                        self.gemini_queue.put_nowait(payload)
        except asyncio.CancelledError:
            pass
        finally:
            try:
                if self.audio_stream:
                    self.audio_stream.stop_stream()
                    self.audio_stream.close()
            except OSError:
                pass

    async def write_to_blackhole(self):
        """Single stream writing all audio (mic + translation) to BlackHole at 16kHz."""
        stream = await asyncio.to_thread(
            pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=SEND_SAMPLE_RATE,
            output=True,
            output_device_index=self._blackhole_idx,
        )
        try:
            while self.running:
                data = await self.blackhole_queue.get()
                await asyncio.to_thread(stream.write, data)
        except asyncio.CancelledError:
            pass
        finally:
            try:
                if stream:
                    stream.stop_stream()
                    stream.close()
            except OSError:
                pass

    async def monitor_audio(self):
        """Play translated audio to speakers so user can hear it."""
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
            try:
                if stream:
                    stream.stop_stream()
                    stream.close()
            except OSError:
                pass

    async def receive_audio(self):
        """Receive translated audio from Gemini, resample, and route."""
        try:
            while self.translating:
                async for response in self.session.receive():
                    if not self.translating:
                        break
                    server_content = response.server_content
                    if server_content is None:
                        continue

                    if server_content.interrupted:
                        pass

                    if server_content.model_turn:
                        for part in server_content.model_turn.parts:
                            if part.inline_data:
                                self.blackhole_queue.put_nowait(part.inline_data.data)
                                if self.monitor:
                                    self.monitor_queue.put_nowait(part.inline_data.data)

                    if server_content.input_transcription:
                        print(f"\r[YOU] {server_content.input_transcription.text}", end="", flush=True)

                    if server_content.output_transcription:
                        print(f"\r[TR]  {server_content.output_transcription.text}", end="", flush=True)
        except asyncio.CancelledError:
            pass

    async def send_realtime(self):
        """Send queued audio to the Gemini session."""
        try:
            while self.translating:
                msg = await self.gemini_queue.get()
                if self.translating and self.session:
                    blob = types.Blob(data=msg["data"], mime_type=msg["mime_type"])
                    await self.session.send_realtime_input(audio=blob)
        except asyncio.CancelledError:
            pass

    def request_stop(self):
        """Signal the app to stop completely (thread-safe)."""
        self.running = False
        self.translating = False
        if self._stop_event and self._loop:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    async def wait_for_quit(self):
        """Wait for stop signal."""
        await self._stop_event.wait()

    async def run(self):
        """CLI mode: forward mic to BlackHole + translate immediately."""
        blackhole_idx, blackhole_name = find_blackhole_device()
        if blackhole_idx is None:
            print("ERROR: BlackHole audio device not found.")
            print("Install it with: brew install blackhole-2ch")
            list_audio_devices()
            return

        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            print("ERROR: GEMINI_API_KEY not found in .env or environment.")
            print("Get a key from https://aistudio.google.com/apikey")
            return

        self._blackhole_idx = blackhole_idx
        client = genai.Client(api_key=api_key)
        config = self._build_config()

        self.running = True
        self.translating = True
        self._stop_event = asyncio.Event()
        self._loop = asyncio.get_event_loop()
        self.blackhole_queue = asyncio.Queue()
        self.gemini_queue = asyncio.Queue(maxsize=5)
        self.monitor_queue = asyncio.Queue()

        print(f"LiveTranslate")
        print(f"  Translation: {self.source_lang} -> {self.target_lang}")
        print(f"  Voice: {self.voice}")
        print(f"  Mix (voice + translation): {'on' if self.mix else 'off (translation only)'}")
        print(f"  Output device: {blackhole_name} [index {blackhole_idx}]")
        print(f"  Monitor (speakers): {'on' if self.monitor else 'off'}")
        print(f"  In Google Meet, select '{blackhole_name}' as your microphone.")
        print(f"\n  Press Enter or type 'q' to stop.\n")

        try:
            async with (
                client.aio.live.connect(model=MODEL, config=config) as session,
                asyncio.TaskGroup() as tg,
            ):
                self.session = session

                quit_task = tg.create_task(self.wait_for_quit())
                tg.create_task(self.listen_audio())
                tg.create_task(self.write_to_blackhole())
                tg.create_task(self.send_realtime())
                tg.create_task(self.receive_audio())

                if self.monitor:
                    tg.create_task(self.monitor_audio())

                await quit_task
                raise asyncio.CancelledError("User requested exit")

        except asyncio.CancelledError:
            pass
        except ExceptionGroup as EG:
            traceback.print_exception(EG)
        finally:
            self.running = False
            self.translating = False
            print("\nStopped.")

    async def run_forward_only(self):
        """Menubar mode: forward mic to BlackHole, translation controlled separately."""
        blackhole_idx, blackhole_name = find_blackhole_device()
        if blackhole_idx is None:
            print("ERROR: BlackHole audio device not found.")
            return

        self._blackhole_idx = blackhole_idx
        self.running = True
        self.translating = False
        self._stop_event = asyncio.Event()
        self._loop = asyncio.get_event_loop()
        self.blackhole_queue = asyncio.Queue()
        self.gemini_queue = asyncio.Queue(maxsize=5)
        self.monitor_queue = asyncio.Queue()

        print(f"LiveTranslate (forwarding only)")
        print(f"  Mic -> {blackhole_name}: active")
        print(f"  Translation: off (use Start to begin)")

        try:
            async with asyncio.TaskGroup() as tg:
                quit_task = tg.create_task(self.wait_for_quit())
                tg.create_task(self.listen_audio())
                tg.create_task(self.write_to_blackhole())

                await quit_task
                raise asyncio.CancelledError("exit")
        except asyncio.CancelledError:
            pass
        except ExceptionGroup as EG:
            traceback.print_exception(EG)
        finally:
            self.running = False
            print("\nStopped.")

    async def start_translation(self):
        """Start the Gemini translation session (called while forwarding is active)."""
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            return

        client = genai.Client(api_key=api_key)
        config = self._build_config()

        self._translate_stop = asyncio.Event()

        try:
            async with client.aio.live.connect(model=MODEL, config=config) as session:
                self.session = session
                self.translating = True
                print(f"\n  Translation started: {self.source_lang} -> {self.target_lang}")

                async with asyncio.TaskGroup() as tg:
                    tg.create_task(self.send_realtime())
                    tg.create_task(self.receive_audio())

                    if self.monitor:
                        tg.create_task(self.monitor_audio())

                    await self._translate_stop.wait()
                    raise asyncio.CancelledError("translation stopped")

        except asyncio.CancelledError:
            pass
        except ExceptionGroup as EG:
            traceback.print_exception(EG)
        finally:
            self.translating = False
            self.session = None
            print("\n  Translation stopped.")

    def stop_translation(self):
        """Stop only the translation, keep forwarding."""
        self.translating = False
        if hasattr(self, '_translate_stop') and self._translate_stop:
            if self._loop:
                self._loop.call_soon_threadsafe(self._translate_stop.set)


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
        help="Send both your voice and translation to BlackHole (default: translation only)"
    )
    parser.add_argument(
        "--monitor", action="store_true",
        help="Also play translated audio on your speakers (so you can hear it)"
    )
    parser.add_argument(
        "--voice", type=str, default="Zephyr",
        choices=LiveTranslator.VOICES,
        help="Voice for translation (default: Zephyr)"
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
        mix=args.mix,
        monitor=args.monitor,
        voice=args.voice,
    )

    import threading

    def stdin_listener():
        try:
            while translator.running:
                text = input("")
                if text.lower() in ("q", "quit", "exit", ""):
                    translator.request_stop()
                    break
        except EOFError:
            translator.request_stop()

    t = threading.Thread(target=stdin_listener, daemon=True)
    t.start()
    asyncio.run(translator.run())


if __name__ == "__main__":
    main()
