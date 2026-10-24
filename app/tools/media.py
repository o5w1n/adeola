"""Media and volume control through Windows itself.

Uses the system media controls (the same thing the keyboard's play/pause key and the volume
pop-up use), so it works with whatever is playing: Spotify Web in Brave, the Spotify app,
YouTube, VLC... No browser access needed.

  playback + "what's playing": Windows media sessions (winrt), media keys as a fallback
  volume:                      Windows Core Audio (pycaw), volume keys as a fallback
"""

from __future__ import annotations

import asyncio
import re
import sys

from app import config

# Windows virtual-key codes
VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1,
      "mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF}
VOLUME_STEP = 10            # percent for "turn it up/down"

_FRIENDLY = [("spotify", "Spotify"), ("brave", "Brave"), ("chrome", "Chrome"), ("msedge", "Edge"),
             ("firefox", "Firefox"), ("308046b0af4a39cb", "Firefox"), ("vlc", "VLC"),
             ("zune", "Media Player"), ("music", "Media Player"), ("youtube", "YouTube")]


def parse(command: str) -> tuple[str, int | None]:
    """Free text -> (action, amount). Used for the model's single-argument tool."""
    c = command.lower().strip()
    if m := re.search(r"(\d{1,3})\s*(?:%|percent)?", c):
        if "volume" in c or "set" in c or c.strip("% ").isdigit():
            return "volume_set", max(0, min(100, int(m.group(1))))
    table = [
        (r"unmute", "unmute"), (r"\bmute\b|silence", "mute"),
        (r"up|louder|raise|increase", "volume_up"), (r"down|quieter|softer|lower|decrease", "volume_down"),
        (r"what|which|who|playing\??$|song is", "now_playing"),
        (r"next|skip", "next"), (r"previous|back|last", "previous"),
        (r"pause|stop", "pause"), (r"play|resume|continue|unpause", "play"),
    ]
    for pat, action in table:
        if re.search(pat, c):
            return action, None
    return "play_pause", None


class _Status:      # Windows' own values, used if winrt can't be imported (e.g. in tests)
    PLAYING, PAUSED = 4, 5


def _status_enum():
    try:
        from winrt.windows.media.control import \
            GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status
        return Status
    except ImportError:
        return _Status


def _friendly(app_id: str) -> str:
    low = (app_id or "").lower()
    for needle, name in _FRIENDLY:
        if needle in low:
            return name
    return ""


class Media:
    """All methods return a short reply for Adeola to say."""

    # -- playback -------------------------------------------------------------------
    def _sessions(self):
        from winrt.windows.media.control import \
            GlobalSystemMediaTransportControlsSessionManager as Manager

        async def get():
            mgr = await Manager.request_async()
            return mgr, list(mgr.get_sessions()), mgr.get_current_session()
        return asyncio.run(get())

    def _pick(self, want_playing: bool | None):
        """The session to act on: a playing one for pause, a paused one for play."""
        Status = _status_enum()
        _, sessions, current = self._sessions()
        def status(s):
            return s.get_playback_info().playback_status
        if want_playing is True:
            for s in sessions:
                if status(s) == Status.PLAYING:
                    return s
        if want_playing is False:
            for s in [current, *sessions]:
                if s is not None and status(s) == Status.PAUSED:
                    return s
        return current or (sessions[0] if sessions else None)

    def _act(self, action: str):
        """Run a playback action. Returns (ok, session_or_None)."""
        want = {"pause": True, "play": False}.get(action)
        session = self._pick(want)
        if session is None:
            return False, None
        method = {"play_pause": "try_toggle_play_pause_async", "pause": "try_pause_async",
                  "play": "try_play_async", "next": "try_skip_next_async",
                  "previous": "try_skip_previous_async"}[action]

        async def run():
            return await getattr(session, method)()
        return bool(asyncio.run(run())), session

    def _title(self, session) -> tuple[str, str]:
        async def get():
            return await session.try_get_media_properties_async()
        props = asyncio.run(get())
        return (props.title or "", props.artist or "") if props else ("", "")

    def playback(self, action: str) -> str:
        done = {"pause": "Paused.", "play": "Playing.", "play_pause": "Done.",
                "next": "Next track.", "previous": "Previous track."}[action]
        if config.DRY_RUN:
            print(f"   [dry run] media: {action}")
            return done
        if sys.platform != "win32":
            return "Media control only works on Windows."
        try:
            ok, session = self._act(action)
        except ImportError:
            ok, session = self._key(VK["play_pause" if action in ("pause", "play") else action]), None
            return done if ok else "I couldn't reach the media controls."
        except Exception:
            ok = False
        if session is None and not ok:
            return "Nothing is playing right now."
        return done if ok else "That player didn't respond. Try clicking it once, then ask again."

    def now_playing(self) -> str:
        if config.DRY_RUN:
            print("   [dry run] media: now playing")
            return "Nothing is playing right now."
        if sys.platform != "win32":
            return "Media control only works on Windows."
        try:
            Status = _status_enum()
            session = self._pick(True)
            if session is None:
                return "Nothing is playing right now."
            title, artist = self._title(session)
            playing = session.get_playback_info().playback_status == Status.PLAYING
            app = _friendly(session.source_app_user_model_id)
        except ImportError:
            return "I can control playback, but I can't see what's playing without the winrt packages."
        except Exception:
            return "I couldn't read what's playing."
        if not title:
            return f"Something is {'playing' if playing else 'paused'}{' in ' + app if app else ''}, but it has no title."
        song = f"{title} by {artist}" if artist else title
        return f"{'Playing' if playing else 'Paused'}: {song}{' in ' + app if app else ''}."

    # -- volume ---------------------------------------------------------------------
    def volume(self, action: str, amount: int | None = None) -> str:
        if config.DRY_RUN:
            print(f"   [dry run] volume: {action} {amount or ''}")
            return {"mute": "Muted.", "unmute": "Unmuted."}.get(action, "Volume changed.")
        if sys.platform != "win32":
            return "Volume control only works on Windows."
        try:
            return self._volume_core_audio(action, amount)
        except Exception:
            return self._volume_keys(action, amount)

    def _volume_core_audio(self, action: str, amount: int | None) -> str:
        import comtypes
        from pycaw.pycaw import AudioUtilities
        comtypes.CoInitialize()        # needed on the voice threads
        try:
            ep = AudioUtilities.GetSpeakers().EndpointVolume
            now = round(ep.GetMasterVolumeLevelScalar() * 100)
            if action == "mute":
                ep.SetMute(1, None)
                return "Muted."
            if action == "unmute":
                ep.SetMute(0, None)
                return f"Unmuted. Volume is {now}%."
            target = {"volume_up": now + VOLUME_STEP, "volume_down": now - VOLUME_STEP,
                      "volume_set": amount if amount is not None else now}[action]
            target = max(0, min(100, target))
            ep.SetMute(0, None)
            ep.SetMasterVolumeLevelScalar(target / 100, None)
            return f"Volume {target}%."
        finally:
            comtypes.CoUninitialize()

    def _volume_keys(self, action: str, amount: int | None) -> str:
        if action in ("mute", "unmute"):     # the mute key toggles
            self._key(VK["mute"])
            return "Muted." if action == "mute" else "Unmuted."
        if action == "volume_set":
            for _ in range(50):              # each key press is 2%: go to 0, then up
                self._key(VK["volume_down"])
            for _ in range((amount or 0) // 2):
                self._key(VK["volume_up"])
            return f"Volume {amount}%."
        for _ in range(VOLUME_STEP // 2):
            self._key(VK[action])
        return "Turned it up." if action == "volume_up" else "Turned it down."

    @staticmethod
    def _key(vk: int) -> bool:
        import ctypes
        user32 = ctypes.windll.user32
        user32.keybd_event(vk, 0, 1, 0)          # KEYEVENTF_EXTENDEDKEY
        user32.keybd_event(vk, 0, 1 | 2, 0)      # ... | KEYEVENTF_KEYUP
        return True

    # -- one entry point for the model -------------------------------------------------
    def command(self, text: str) -> str:
        action, amount = parse(text)
        if action == "now_playing":
            return self.now_playing()
        if action.startswith("volume") or action in ("mute", "unmute"):
            return self.volume(action, amount)
        return self.playback(action)
