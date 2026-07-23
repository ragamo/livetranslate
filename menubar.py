"""
LiveTranslate Menu Bar App

System tray interface for LiveTranslate.
Run with: python3 menubar.py

Mic -> BlackHole is always active. Start/Stop controls translation only.
"""

import asyncio
import threading

import pyaudio
import rumps

from livetranslate import LiveTranslator, find_blackhole_device

LANGUAGES = {
    "Spanish": "es",
    "English": "en",
    "French": "fr",
    "German": "de",
    "Portuguese": "pt",
    "Italian": "it",
    "Japanese": "ja",
    "Korean": "ko",
    "Chinese": "zh",
}


def get_output_devices():
    """Return list of (index, name) for output-capable devices."""
    p = pyaudio.PyAudio()
    devices = []
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info["maxOutputChannels"] > 0 and "blackhole" not in info["name"].lower():
            devices.append((i, info["name"]))
    p.terminate()
    return devices


class LiveTranslateApp(rumps.App):
    def __init__(self):
        super().__init__("LT", quit_button=None)

        self.source_lang = "es"
        self.target_lang = "en"
        self.voice = "Zephyr"
        self.monitor = False
        self.monitor_device_index = None
        self.monitor_device_name = "Default"

        self.translator = None
        self.forward_thread = None
        self.forward_loop = None

        self._build_menu()
        self._start_forwarding()

    def _build_menu(self):
        self.start_stop = rumps.MenuItem("Start Translation", callback=self.toggle_translation)
        self.status_item = rumps.MenuItem("Forwarding mic -> BlackHole", callback=None)
        self.status_item.set_callback(None)

        self.source_menu = rumps.MenuItem("From")
        for name, code in LANGUAGES.items():
            item = rumps.MenuItem(name, callback=self.set_source)
            item.state = code == self.source_lang
            self.source_menu.add(item)

        self.target_menu = rumps.MenuItem("To")
        for name, code in LANGUAGES.items():
            item = rumps.MenuItem(name, callback=self.set_target)
            item.state = code == self.target_lang
            self.target_menu.add(item)

        self.voice_menu = rumps.MenuItem("Voice")
        for v in LiveTranslator.VOICES:
            item = rumps.MenuItem(v, callback=self.set_voice)
            item.state = v == self.voice
            self.voice_menu.add(item)

        self.output_menu = rumps.MenuItem("Output Device")
        self._populate_output_devices()

        self.monitor_item = rumps.MenuItem("Monitor (hear translation)", callback=self.toggle_monitor)
        self.monitor_item.state = self.monitor

        self.menu = [
            self.start_stop,
            self.status_item,
            None,
            self.source_menu,
            self.target_menu,
            self.voice_menu,
            None,
            self.output_menu,
            self.monitor_item,
            None,
            rumps.MenuItem("Refresh Devices", callback=self.refresh_devices),
            rumps.MenuItem("Quit", callback=self.quit_app),
        ]

    def _populate_output_devices(self):
        self.output_menu = rumps.MenuItem("Output Device")
        default_item = rumps.MenuItem("Default", callback=self.set_output_device)
        default_item.state = self.monitor_device_index is None
        self.output_menu.add(default_item)

        devices = get_output_devices()
        for idx, name in devices:
            item = rumps.MenuItem(name, callback=self.set_output_device)
            item.representedObject = idx
            item.state = idx == self.monitor_device_index
            self.output_menu.add(item)

    def refresh_devices(self, _):
        self._populate_output_devices()

    def set_voice(self, sender):
        self.voice = sender.title
        for item in self.voice_menu.values():
            item.state = item.title == sender.title

    def set_output_device(self, sender):
        if sender.title == "Default":
            self.monitor_device_index = None
            self.monitor_device_name = "Default"
        else:
            self.monitor_device_index = sender.representedObject
            self.monitor_device_name = sender.title

        for item in self.output_menu.values():
            item.state = item.title == sender.title

    def set_source(self, sender):
        self.source_lang = LANGUAGES[sender.title]
        for item in self.source_menu.values():
            item.state = item.title == sender.title

    def set_target(self, sender):
        self.target_lang = LANGUAGES[sender.title]
        for item in self.target_menu.values():
            item.state = item.title == sender.title

    def toggle_monitor(self, sender):
        self.monitor = not self.monitor
        sender.state = self.monitor

    def _start_forwarding(self):
        """Start mic -> BlackHole forwarding on app launch."""
        self.translator = LiveTranslator(
            source_lang=self.source_lang,
            target_lang=self.target_lang,
            monitor=self.monitor,
            monitor_device_index=self.monitor_device_index,
            voice=self.voice,
        )

        blackhole_idx, blackhole_name = find_blackhole_device()
        if blackhole_idx is None:
            self.status_item.title = "ERROR: BlackHole not found"
            return

        self.translator._blackhole_idx = blackhole_idx

        def run_forward():
            self.forward_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.forward_loop)
            self.forward_loop.run_until_complete(self.translator.run_forward_only())
            self.forward_loop.close()

        self.forward_thread = threading.Thread(target=run_forward, daemon=True)
        self.forward_thread.start()
        self.status_item.title = f"Mic -> {blackhole_name} (active)"

    def toggle_translation(self, sender):
        if self.translator and self.translator.translating:
            self._stop_translation()
        else:
            self._start_translation()

    def _start_translation(self):
        """Start Gemini translation session in the forwarding event loop."""
        self.translator.source_lang = self.source_lang
        self.translator.target_lang = self.target_lang
        self.translator.monitor = self.monitor
        self.translator.monitor_device_index = self.monitor_device_index
        self.translator.voice = self.voice

        if self.forward_loop:
            self.forward_loop.call_soon_threadsafe(
                lambda: self.forward_loop.create_task(self.translator.start_translation())
            )

        self.start_stop.title = "Stop Translation"
        self.status_item.title = f"Translating: {self.source_lang} -> {self.target_lang}"

    def _stop_translation(self):
        """Stop translation, keep forwarding."""
        if self.translator:
            self.translator.stop_translation()

        self.start_stop.title = "Start Translation"
        blackhole_idx, blackhole_name = find_blackhole_device()
        self.status_item.title = f"Mic -> {blackhole_name or 'BlackHole'} (active)"

    def quit_app(self, _):
        if self.translator:
            self.translator.request_stop()
        if self.forward_thread:
            self.forward_thread.join(timeout=3)
        rumps.quit_application()


if __name__ == "__main__":
    LiveTranslateApp().run()
