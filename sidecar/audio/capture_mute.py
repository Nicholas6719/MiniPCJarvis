"""Is the microphone muted at the system level?

A muted endpoint is indistinguishable, from where he sits, from a broken
wake word: the device opens, reports itself healthy, and delivers frames of
exact zeros forever. Every watch judged the microphone by frames ARRIVING
(diagnostics: "capturing"; the device watch: "no frames for 6 s"), so a
mute looked like a dead wake word and nothing said a word.

The read is COM (IAudioEndpointVolume on the capture endpoint) and runs on
a thread of its own with its own apartment, the way output_watch does it:
COM is per-thread, the UI Automation behind "click Send" needs the default
apartment, and a diagnostic that breaks the thing it is diagnosing is worse
than none. None when it cannot tell - a missing pycaw, a device mid
re-enumeration, a read that hangs - and the caller falls back to listening.
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("jarvis.audio.mute")

READ_TIMEOUT_S = 8.0


def _match(want: str, name: str) -> bool:
    """sounddevice names are the MME names, cut at 31 characters; the endpoint's
    FriendlyName is the whole thing. Either may be the prefix of the other."""
    w, n = want.lower().strip(), name.lower().strip()
    if not w or not n:
        return False
    cut = min(len(w), len(n), 28)
    return w[:cut] == n[:cut]


def _read(device_name: str | None) -> bool | None:
    """The COM work. Never called on the loop thread."""
    import comtypes
    from comtypes import CLSCTX_ALL, CoCreateInstance
    from pycaw.constants import CLSID_MMDeviceEnumerator, DEVICE_STATE, EDataFlow, ERole
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume, IMMDeviceEnumerator

    try:
        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
    except OSError:
        pass
    try:
        enumerator = CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator,
                                      comtypes.CLSCTX_INPROC_SERVER)
        device = None
        want = (device_name or "").strip()
        if want and want.lower() not in ("system default", "not started"):
            # THE DEVICE WE ARE LISTENING ON, not whatever Windows calls the
            # default: he talks to the webcam's microphone, and the default
            # capture endpoint can be a headset nobody is wearing.
            endpoints = enumerator.EnumAudioEndpoints(EDataFlow.eCapture.value,
                                                      DEVICE_STATE.ACTIVE.value)
            for i in range(endpoints.GetCount()):
                dev = endpoints.Item(i)
                try:
                    name = str(AudioUtilities.CreateDevice(dev).FriendlyName or "")
                except Exception:
                    continue
                if _match(want, name):
                    device = dev
                    break
        if device is None:
            device = enumerator.GetDefaultAudioEndpoint(EDataFlow.eCapture.value,
                                                        ERole.eMultimedia.value)
        iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = iface.QueryInterface(IAudioEndpointVolume)
        if bool(volume.GetMute()):
            return True
        # Level dragged to nothing is a mute by another name.
        try:
            return float(volume.GetMasterVolumeLevelScalar()) <= 0.001
        except Exception:
            return False
    finally:
        try:
            comtypes.CoUninitialize()
        except Exception:
            pass


def capture_muted(device_name: str | None = None,
                  timeout: float = READ_TIMEOUT_S) -> bool | None:
    """True if the capture endpoint is muted, False if not, None if it cannot be told.

    Blocking for up to `timeout`; call it from a worker (asyncio.to_thread).
    The COM work itself always runs on a fresh thread, so no apartment is
    ever inherited from, or left behind on, the caller's.
    """
    box: list = []

    def work() -> None:
        try:
            box.append(_read(device_name))
        except Exception:
            log.debug("could not read the microphone's mute state", exc_info=True)
            box.append(None)

    t = threading.Thread(target=work, name="mic-mute-read", daemon=True)
    t.start()
    t.join(timeout)
    if not box:
        log.debug("microphone mute read did not answer within %.0fs", timeout)
        return None
    return box[0]
