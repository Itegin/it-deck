"""Manual probe, not a test: prints the default microphone's mute state once
a second. Run it by hand (`python test_mic.py`) on the Windows PC.

Guarded by __main__ because its name matches pytest's test_*.py pattern: a
bare `pytest` from the repo root would import it and never return.
"""

import time
from ctypes import POINTER, cast

from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume


def main() -> None:
    mic = AudioUtilities.GetMicrophone()
    iface = mic.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    vol = cast(iface, POINTER(IAudioEndpointVolume))

    print("Читаю мут каждую секунду. Ctrl+C для выхода.")
    while True:
        print("muted =", vol.GetMute())
        time.sleep(1)


if __name__ == "__main__":
    main()
