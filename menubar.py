"""
LiveTranslate Menu Bar App

System tray interface for LiveTranslate.
Run with: python3 menubar.py
"""

import asyncio
import threading

import pyaudio
import rumps

from livetranslate import LiveTranslator

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
        self.mix_mode = False
        self.monitor = False
        self.monitor_all = False
        self.monitor_device_index = None
        self.monitor_device_name = "Default"

        self.translator = None
        self.loop = None
        self.thread = None

        self._build_menu()

    def _build_menu(self):
        self.start_stop = rumps.MenuItem("Start", callback=self.toggle)
        self.status_item = rumps.MenuItem("Idle", callback=None)
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

        self.mix_item = rumps.MenuItem("Mix (voice + translation)", callback=self.toggle_mix)
        self.mix_item.state = self.mix_mode

        self.monitor_item = rumps.MenuItem("Monitor (translation)", callback=self.toggle_monitor)
        self.monitor_item.state = self.monitor

        self.monitor_all_item = rumps.MenuItem("Monitor All (voice + translation)", callback=self.toggle_monitor_all)
        self.monitor_all_item.state = self.monitor_all

        self.menu = [
            self.start_stop,
            self.status_item,
            None,
            self.source_menu,
            self.target_menu,
            self.voice_menu,
            None,
            self.output_menu,
            self.mix_item,
            self.monitor_item,
            self.monitor_all_item,
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

    def toggle_mix(self, sender):
        self.mix_mode = not self.mix_mode
        sender.state = self.mix_mode

    def toggle_monitor(self, sender):
        self.monitor = not self.monitor
        sender.state = self.monitor
        if self.monitor:
            self.monitor_all = False
            self.monitor_all_item.state = False

    def toggle_monitor_all(self, sender):
        self.monitor_all = not self.monitor_all
        sender.state = self.monitor_all
        if self.monitor_all:
            self.monitor = False
            self.monitor_item.state = False

    def toggle(self, sender):
        if self.thread and self.thread.is_alive():
            self.stop_translation()
        else:
            self.start_translation()

    def start_translation(self):
        self.translator = LiveTranslator(
            source_lang=self.source_lang,
            target_lang=self.target_lang,
            mix_mode=self.mix_mode,
            monitor=self.monitor or self.monitor_all,
            monitor_all=self.monitor_all,
            monitor_device_index=self.monitor_device_index,
            voice=self.voice,
        )

        def run_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.translator.run())
            loop.close()

        self.thread = threading.Thread(target=run_loop, daemon=True)
        self.thread.start()

        self.title = "LT"
        self.start_stop.title = "Stop"
        self.status_item.title = f"{self.source_lang}->{self.target_lang} | {self.monitor_device_name}"

    def stop_translation(self):
        if self.translator:
            self.translator.request_stop()
        if self.thread:
            self.thread.join(timeout=5)
            self.thread = None
        self.translator = None
        self._on_stopped()

    def _on_stopped(self):
        self.title = "LT"
        self.start_stop.title = "Start"
        self.status_item.title = "Idle"

    def quit_app(self, _):
        self.stop_translation()
        rumps.quit_application()


if __name__ == "__main__":
    LiveTranslateApp().run()
