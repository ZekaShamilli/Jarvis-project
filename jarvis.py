"""Jarvis: floating bar at the top of the screen. Run: python jarvis.py"""
import contextlib
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import webview

import brain

HERE = Path(__file__).parent
WIDTH = 720
ORB_SIZE = 64               # folded: just the orb
TOP_MARGIN = 24
ANIM_FRAMES = 20            # fold/unfold animation, ~300 ms total
ANIM_FRAME_S = 0.015
SPEAK = os.getenv("JARVIS_SPEAK", "1") != "0"
MAX_SPOKEN_CHARS = 400

# Voice capture. Tune SILENCE_RMS for your mic: raise it if a noisy room never stops recording.
SAMPLE_RATE = 16000
BLOCK = 1600                # 0.1 s per read
SILENCE_RMS = 0.012
END_SILENCE_S = 1.2         # stop this long after you stop talking
NO_SPEECH_TIMEOUT_S = 6
LEVEL_GAIN = 12             # mic RMS -> orb pulse (0..1); raise for a quiet mic
MAX_RECORD_S = 20
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base")

# Neural voices via edge-tts (online, free). List others: edge-tts --list-voices
VOICES = {"en-US": os.getenv("JARVIS_VOICE", "en-GB-RyanNeural"), "tr-TR": "tr-TR-AhmetNeural"}
VOICE_RATE = 24000
# Offline fallback: built-in Windows SAPI voice.
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
        self._voice_stop = threading.Event()
        self._anim_gen = 0
        self._hwnd = None
        self._geo = None            # (center_x, top, dpi_scale) in physical pixels
        self._size = (ORB_SIZE, ORB_SIZE)

    # ---- called from JS ----
    def send(self, text):
        self._spawn(lambda: text)

    def listen(self):
        self._spawn(self._hear)

    def layout(self, width, height):
        """Animate the window to width x height (CSS px), staying centered at the top."""
        self._anim_gen += 1
        threading.Thread(target=self._animate, args=(int(width), int(height), self._anim_gen),
                         daemon=True).start()

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

    def _animate(self, width, height, gen):
        if not self._geo:
            return
        (w0, h0), growing = self._size, width > self._size[0]
        for i in range(1, ANIM_FRAMES + 1):
            if gen != self._anim_gen:
                return  # a newer animation took over
            p = i / ANIM_FRAMES
            # Unfold with a soft spring overshoot (Siri-like); fold with a plain ease-out.
            t = 1 + 2.2 * (p - 1) ** 3 + 1.2 * (p - 1) ** 2 if growing else 1 - (1 - p) ** 3
            self._apply(w0 + (width - w0) * t, h0 + (height - h0) * t)
            time.sleep(ANIM_FRAME_S)
        self._apply(width, height)

    def _apply(self, width, height):
        """Set size, keeping the window's top-center anchored. Win32, physical pixels."""
        import ctypes
        self._size = (width, height)
        cx, top, scale = self._geo
        w, h = round(width * scale), round(height * scale)
        ctypes.windll.user32.SetWindowPos(self._hwnd, 0, cx - w // 2, top, w, h, 0x0004 | 0x0010)  # NOZORDER|NOACTIVATE

    def activate(self):
        """Global hotkey: bring Jarvis forward, unfold and listen."""
        import ctypes
        ctypes.windll.user32.SetForegroundWindow(self._hwnd)
        self._js("unfold")
        self.listen()

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
            self._load_whisper()
        segments, _ = self._whisper.transcribe(audio)
        text = " ".join(s.text for s in segments).strip()
        self._js("heard", text)
        return text

    def _load_whisper(self):
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        from faster_whisper import WhisperModel
        self._whisper = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")

    def _record(self):
        import numpy as np
        import sounddevice as sd
        frames, heard, quiet = [], False, 0.0
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=BLOCK) as stream:
            for _ in range(int(MAX_RECORD_S * SAMPLE_RATE / BLOCK)):
                block, _ = stream.read(BLOCK)
                frames.append(block[:, 0])
                rms = float(np.sqrt(np.mean(block ** 2)))
                self._js("level", rms * LEVEL_GAIN)
                loud = rms > SILENCE_RMS
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
        self._voice_stop.set()  # cut off whatever is still talking
        if self._voice and self._voice.poll() is None:
            self._voice.kill()
        stop = self._voice_stop = threading.Event()
        threading.Thread(target=self._say, args=(text[:MAX_SPOKEN_CHARS], stop), daemon=True).start()

    def _say(self, text, stop):
        import asyncio
        lang = "tr-TR" if re.search("[çğıöşüÇĞİÖŞÜ]", text) else "en-US"
        try:
            asyncio.run(stream_voice(text, VOICES[lang], stop))
            return
        except Exception:
            if stop.is_set():
                return
            # offline or edge-tts missing: fall back to SAPI below
        self._voice = subprocess.Popen(
            ["powershell", "-NoProfile", "-Command", SPEAK_PS], stdin=subprocess.PIPE,
            env={**os.environ, "JARVIS_LANG": lang}, creationflags=subprocess.CREATE_NO_WINDOW)
        self._voice.stdin.write(text.encode("utf-8"))
        self._voice.stdin.close()


async def stream_voice(text, voice, stop):
    """Play edge-tts mp3 as it arrives, so speech starts before synthesis finishes.
    Raises only if nothing was played, so the caller can fall back to SAPI."""
    import av
    import edge_tts
    import numpy as np
    import sounddevice as sd
    codec = av.CodecContext.create("mp3", "r")
    resample = av.AudioResampler(format="flt", layout="mono", rate=VOICE_RATE)
    chunks = edge_tts.Communicate(text, voice).stream()
    played = False
    try:
        async with contextlib.aclosing(chunks):  # closes the connection if we stop mid-stream
            with sd.OutputStream(samplerate=VOICE_RATE, channels=1, dtype="float32") as out:
                async for msg in chunks:
                    if msg["type"] != "audio":
                        continue
                    for p in codec.parse(msg["data"]):
                        for frame in (f for d in codec.decode(p) for f in resample.resample(d)):
                            if stop.is_set():
                                out.abort()
                                return
                            out.write(np.ascontiguousarray(frame.to_ndarray().reshape(-1, 1)))
                            played = True
    except Exception:
        if not played:
            raise


def style_window(api):
    """Windows 11 acrylic glass, dark mode, folded orb centered at top.
    Win32 works in physical pixels, so this is correct on scaled displays where pywebview's x/y are not."""
    window = api._window
    import ctypes
    from ctypes import wintypes
    user32, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
    hwnd = user32.FindWindowW(None, "Jarvis")
    if not hwnd:
        return

    def set_attr(attr, value):
        v = ctypes.c_int(value)
        dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), 4)
    set_attr(20, 1)   # DWMWA_USE_IMMERSIVE_DARK_MODE
    # ponytail: Windows can't antialias-clip WebView2 to a true pill (SetWindowRgn left
    # stale paint + square acrylic), so we use native rounded corners; folded = rounded square.
    set_attr(33, 2)   # DWMWA_WINDOW_CORNER_PREFERENCE = round
    set_attr(34, -2)  # DWMWA_BORDER_COLOR = none (0xFFFFFFFE), so the glow owns the edge
    set_attr(38, 3)   # DWMWA_SYSTEMBACKDROP_TYPE = acrylic (Win11 22H2+; ignored elsewhere)
    margins = (ctypes.c_int * 4)(-1, -1, -1, -1)
    dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
    # Black client pixels let the glass show through the transparent page.
    from System import Action
    from System.Drawing import Color
    from webview.platforms.winforms import BrowserView
    form = BrowserView.instances[window.uid]
    form.Invoke(Action(lambda: setattr(form, "BackColor", Color.Black)))

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info))
    work = info.rcWork
    scale = user32.GetDpiForWindow(hwnd) / 96
    api._hwnd = hwnd
    api._geo = ((work.left + work.right) // 2, work.top + round(TOP_MARGIN * scale), scale)
    api._apply(ORB_SIZE, ORB_SIZE)
    threading.Thread(target=hotkey_loop, args=(api,), daemon=True).start()
    threading.Thread(target=api._load_whisper, daemon=True).start()  # first voice command stays fast


def hotkey_loop(api):
    """Ctrl+Space from any app wakes Jarvis. RegisterHotKey needs its own message loop."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, 1, 0x0002 | 0x4000, 0x20):  # MOD_CONTROL|MOD_NOREPEAT, VK_SPACE
        return  # another app owns Ctrl+Space; hover/click still work
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == 0x0312:  # WM_HOTKEY
            api.activate()


if __name__ == "__main__":
    api = Api()
    api._window = webview.create_window(
        "Jarvis", str(HERE / "ui.html"), js_api=api, width=ORB_SIZE, height=ORB_SIZE,
        min_size=(ORB_SIZE, ORB_SIZE), frameless=True, on_top=True, easy_drag=False, transparent=True)
    api._window.events.shown += lambda: style_window(api)
    webview.start()
