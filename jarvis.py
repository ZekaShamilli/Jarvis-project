"""Jarvis: floating bar at the top of the screen. Run: python jarvis.py"""
import json
import os
import re
import subprocess
import threading
from pathlib import Path

import webview

import brain

HERE = Path(__file__).parent
WIDTH = 720
TOP_MARGIN = 24
BG = "#0e111a"  # must match --bg in ui.html
SPEAK = os.getenv("JARVIS_SPEAK", "1") != "0"
MAX_SPOKEN_CHARS = 400

# Voice capture. Tune SILENCE_RMS for your mic: raise it if a noisy room never stops recording.
SAMPLE_RATE = 16000
BLOCK = 1600                # 0.1 s per read
SILENCE_RMS = 0.012
END_SILENCE_S = 1.2         # stop this long after you stop talking
NO_SPEECH_TIMEOUT_S = 6
MAX_RECORD_S = 20
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")

SPEAK_PS = (
    "[Console]::InputEncoding=[Text.Encoding]::UTF8;"
    "Add-Type -AssemblyName System.Speech;"
    "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "$v=$s.GetInstalledVoices()|?{$_.VoiceInfo.Culture.Name -eq $env:JARVIS_LANG}|select -First 1;"
    "if($v){$s.SelectVoice($v.VoiceInfo.Name)};"
    "$s.Speak([Console]::In.ReadToEnd())"
)


class Api:
    """Methods without a leading underscore are callable from ui.html."""

    def __init__(self):
        self._window = None
        self._lock = threading.Lock()
        self._whisper = None
        self._voice = None

    # ---- called from JS ----
    def send(self, text):
        self._spawn(lambda: text)

    def listen(self):
        self._spawn(self._hear)

    def fit(self, height):
        self._window.resize(WIDTH, int(height))

    def quit(self):
        self._window.destroy()

    # ---- hooks used by brain.ask ----
    def status(self, text):
        self._js("state", "busy", text)

    def set_visible(self, visible):
        self._window.show() if visible else self._window.hide()

    # ---- internals ----
    def _js(self, fn, *args):
        self._window.evaluate_js(f"jarvis.{fn}({','.join(json.dumps(a) for a in args)})")

    def _spawn(self, get_text):
        threading.Thread(target=self._work, args=(get_text,), daemon=True).start()

    def _work(self, get_text):
        if not self._lock.acquire(blocking=False):
            return  # already busy; ignore extra clicks
        try:
            text = get_text()
            if text:
                self._js("state", "busy", "Thinking…")
                try:
                    reply = brain.ask(text, self)
                except Exception as e:
                    reply = f"Error: {e}. Is Ollama running with {brain.LOCAL_MODEL}?"
                self._js("show", text, reply)
                self._speak(reply)
        except Exception as e:
            self._js("show", "", f"Error: {e}")
        finally:
            self._js("state", "", "")
            self._lock.release()

    def _hear(self):
        self._js("state", "listening", "Listening…")
        audio = self._record()
        if audio is None:
            return None
        self._js("state", "busy", "…")
        if self._whisper is None:
            from faster_whisper import WhisperModel
            self._whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
        segments, _ = self._whisper.transcribe(audio)
        text = " ".join(s.text for s in segments).strip()
        self._js("heard", text)
        return text

    def _record(self):
        import numpy as np
        import sounddevice as sd
        frames, heard, quiet = [], False, 0.0
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=BLOCK) as stream:
            for _ in range(int(MAX_RECORD_S * SAMPLE_RATE / BLOCK)):
                block, _ = stream.read(BLOCK)
                frames.append(block[:, 0])
                loud = float(np.sqrt(np.mean(block ** 2))) > SILENCE_RMS
                heard = heard or loud
                quiet = 0.0 if loud else quiet + BLOCK / SAMPLE_RATE
                if heard and quiet >= END_SILENCE_S:
                    break
                if not heard and quiet >= NO_SPEECH_TIMEOUT_S:
                    return None
        return np.concatenate(frames)

    def _speak(self, text):
        if not SPEAK:
            return
        if self._voice and self._voice.poll() is None:
            self._voice.kill()
        lang = "tr-TR" if re.search("[çğıöşüÇĞİÖŞÜ]", text) else "en-US"
        self._voice = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", SPEAK_PS], stdin=subprocess.PIPE,
            env={**os.environ, "JARVIS_LANG": lang}, creationflags=subprocess.CREATE_NO_WINDOW)
        self._voice.stdin.write(text[:MAX_SPOKEN_CHARS].encode("utf-8"))
        self._voice.stdin.close()


def style_window():
    """Native Windows 11 rounded corners + center at top. Win32 works in physical pixels,
    so this is correct on scaled displays where pywebview's x/y are not."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, "Jarvis")
    if not hwnd:
        return
    round_corners = ctypes.c_int(2)  # DWMWCP_ROUND
    ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(round_corners), 4)

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    win = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(win))
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info))
    work = info.rcWork
    x = work.left + (work.right - work.left - (win.right - win.left)) // 2
    scale = user32.GetDpiForWindow(hwnd) / 96
    user32.SetWindowPos(hwnd, 0, x, work.top + round(TOP_MARGIN * scale), 0, 0, 0x0001 | 0x0004 | 0x0010)


if __name__ == "__main__":
    api = Api()
    api._window = webview.create_window(
        "Jarvis", str(HERE / "ui.html"), js_api=api, width=WIDTH, height=64, min_size=(WIDTH, 64),
        frameless=True, on_top=True, easy_drag=False, background_color=BG)
    api._window.events.shown += style_window
    webview.start()
