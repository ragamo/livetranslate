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
import collections
import os
import sys
import traceback
import threading

import numpy as np
import sounddevice as sd
from dotenv import load_dotenv

from google import genai
from google.genai import types

load_dotenv()

if sys.version_info < (3, 11, 0):
    import taskgroup, exceptiongroup

    asyncio.TaskGroup = taskgroup.TaskGroup
    asyncio.ExceptionGroup = exceptiongroup.ExceptionGroup

SAMPLE_RATE = 48000
GEMINI_INPUT_RATE = 16000
GEMINI_OUTPUT_RATE = 24000
BLOCK_SIZE = 1024

MODEL = "gemini-3.5-live-translate-preview"


def find_blackhole_device():
    """Find BlackHole virtual audio device index."""
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if "blackhole" in d["name"].lower() and d["max_output_channels"] > 0:
            return i, d["name"]
    return None, None


def list_audio_devices():
    """List all available audio devices."""
    print("\nAvailable audio devices:")
    print("-" * 60)
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        direction = []
        if d["max_input_channels"] > 0:
            direction.append("IN")
        if d["max_output_channels"] > 0:
            direction.append("OUT")
        print(f"  [{i}] {d['name']} ({'/'.join(direction)})")
    print()


def _resample(data_np, from_rate, to_rate):
    """Resample numpy float32 audio array between sample rates."""
    if from_rate == to_rate:
        return data_np
    ratio = to_rate / from_rate
    n_out = int(len(data_np) * ratio)
    indices = np.arange(n_out) / ratio
    idx = indices.astype(int)
    frac = indices - idx
    idx = np.clip(idx, 0, len(data_np) - 2)
    return data_np[idx] * (1 - frac) + data_np[idx + 1] * frac


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
        self.running = False
        self.translating = False
        self._stop_event = None
        self._loop = None
        self._blackhole_idx = None

        self.gemini_queue = None
        self.monitor_queue = None
        self._translation_buffer = np.array([], dtype=np.float32)
        self._buf_lock = threading.Lock()
        self._mic_buffer = collections.deque(maxlen=50)

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

    def _audio_callback(self, indata, outdata, frames, time, status):
        """sounddevice callback: fast, no heavy processing."""
        mic = indata[:, 0].copy()

        # Buffer mic for async Gemini sender
        if self.translating:
            self._mic_buffer.append(mic.copy())

        # Build output for BlackHole
        with self._buf_lock:
            available = len(self._translation_buffer)
            if available > 0:
                take = min(available, frames)
                chunk = self._translation_buffer[:take]
                self._translation_buffer = self._translation_buffer[take:]
                if take < frames:
                    translation = np.zeros(frames, dtype=np.float32)
                    translation[:take] = chunk
                else:
                    translation = chunk
            else:
                translation = None

        if not self.translating:
            outdata[:, 0] = mic
        elif self.mix:
            if translation is not None:
                outdata[:, 0] = mic * 0.5 + translation * 0.7
            else:
                outdata[:, 0] = mic
        else:
            if translation is not None:
                outdata[:, 0] = translation
            else:
                outdata[:, 0] = 0.0

    async def capture_and_send(self):
        """Read mic buffer, resample to 16kHz, send to Gemini."""
        try:
            while self.translating:
                if self._mic_buffer:
                    mic_48k = self._mic_buffer.popleft()
                    mic_16k = _resample(mic_48k, SAMPLE_RATE, GEMINI_INPUT_RATE)
                    pcm_bytes = (mic_16k * 32767).astype(np.int16).tobytes()
                    payload = {"data": pcm_bytes, "mime_type": "audio/pcm;rate=16000"}
                    try:
                        self.gemini_queue.put_nowait(payload)
                    except asyncio.QueueFull:
                        self.gemini_queue.get_nowait()
                        self.gemini_queue.put_nowait(payload)
                else:
                    await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass

    def _append_translation(self, pcm_bytes):
        """Convert Gemini PCM16 output to float32, resample to 48kHz, append to buffer."""
        samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32767.0
        samples_48k = _resample(samples, GEMINI_OUTPUT_RATE, SAMPLE_RATE)
        with self._buf_lock:
            self._translation_buffer = np.concatenate([self._translation_buffer, samples_48k])

    async def monitor_audio(self):
        """Play translated audio to speakers so user can hear it."""
        monitor_device = self.monitor_device_index
        stream = sd.OutputStream(
            device=monitor_device,
            samplerate=GEMINI_OUTPUT_RATE,
            channels=1,
            dtype="float32",
        )
        stream.start()
        try:
            while self.running:
                pcm_bytes = await self.monitor_queue.get()
                samples = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32767.0
                stream.write(samples.reshape(-1, 1))
        except asyncio.CancelledError:
            pass
        finally:
            stream.stop()
            stream.close()

    async def receive_audio(self):
        """Receive translated audio from Gemini and route it."""
        try:
            while self.translating:
                async for response in self.session.receive():
                    if not self.translating:
                        break
                    server_content = response.server_content
                    if server_content is None:
                        continue

                    if server_content.interrupted:
                        with self._buf_lock:
                            self._translation_buffer = np.array([], dtype=np.float32)

                    if server_content.model_turn:
                        for part in server_content.model_turn.parts:
                            if part.inline_data:
                                self._append_translation(part.inline_data.data)
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
        self._translation_buffer = np.array([], dtype=np.float32)
        self.gemini_queue = asyncio.Queue(maxsize=20)
        self.monitor_queue = asyncio.Queue()

        mic_idx = sd.default.device[0]

        print(f"LiveTranslate")
        print(f"  Translation: {self.source_lang} -> {self.target_lang}")
        print(f"  Voice: {self.voice}")
        print(f"  Mix (voice + translation): {'on' if self.mix else 'off (translation only)'}")
        print(f"  Output device: {blackhole_name} [index {blackhole_idx}]")
        print(f"  Monitor (speakers): {'on' if self.monitor else 'off'}")
        print(f"  In Google Meet, select '{blackhole_name}' as your microphone.")
        print(f"\n  Press Enter or type 'q' to stop.\n")

        audio_stream = sd.Stream(
            device=(mic_idx, blackhole_idx),
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_SIZE,
            channels=1,
            dtype="float32",
            callback=self._audio_callback,
        )
        audio_stream.start()

        try:
            async with (
                client.aio.live.connect(model=MODEL, config=config) as session,
                asyncio.TaskGroup() as tg,
            ):
                self.session = session

                quit_task = tg.create_task(self.wait_for_quit())
                tg.create_task(self.capture_and_send())
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
            audio_stream.stop()
            audio_stream.close()
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
        self._translation_buffer = np.array([], dtype=np.float32)
        self.gemini_queue = asyncio.Queue(maxsize=20)
        self.monitor_queue = asyncio.Queue()

        mic_idx = sd.default.device[0]

        print(f"LiveTranslate (forwarding only)")
        print(f"  Mic -> {blackhole_name}: active")
        print(f"  Translation: off (use Start to begin)")

        self._audio_stream = sd.Stream(
            device=(mic_idx, blackhole_idx),
            samplerate=SAMPLE_RATE,
            blocksize=BLOCK_SIZE,
            channels=1,
            dtype="float32",
            callback=self._audio_callback,
        )
        self._audio_stream.start()

        try:
            async with asyncio.TaskGroup() as tg:
                quit_task = tg.create_task(self.wait_for_quit())
                await quit_task
                raise asyncio.CancelledError("exit")
        except asyncio.CancelledError:
            pass
        except ExceptionGroup as EG:
            traceback.print_exception(EG)
        finally:
            self._audio_stream.stop()
            self._audio_stream.close()
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
        self._translation_buffer = np.array([], dtype=np.float32)

        try:
            async with client.aio.live.connect(model=MODEL, config=config) as session:
                self.session = session
                self.translating = True
                print(f"\n  Translation started: {self.source_lang} -> {self.target_lang}")

                async with asyncio.TaskGroup() as tg:
                    tg.create_task(self.capture_and_send())
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
