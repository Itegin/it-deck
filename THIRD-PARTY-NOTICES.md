# Third-party notices

IT-Deck itself is under the PolyForm Noncommercial License 1.0.0 (see
[`LICENSE`](LICENSE)): free, and non-commercial for good. Commercial use
only by agreement with the author. This file lists what it is built from or talks to that
belongs to someone else, under which terms, and what each of those terms
means for how IT-Deck may be distributed.

Checked for v0.5.6 (September 2026). Versions are the pinned ones in
`backend/requirements.txt`, `agents/windows/requirements.txt` and
`standalone/requirements.txt`; licences come from each package's own
metadata or bundled licence file.

## Bundled into ITDeck.exe

| Component | Version | Licence | Used for |
|---|---|---|---|
| Python runtime | 3.12 | PSF License | everything |
| FastAPI | 0.115.0 | MIT | backend HTTP/WebSocket API |
| Starlette | (FastAPI's) | BSD-3-Clause | FastAPI's foundation |
| Pydantic | (FastAPI's) | MIT | request validation |
| Uvicorn | 0.32.0 | BSD-3-Clause | backend server |
| websockets | 13.1 | BSD-3-Clause | backend and agent WebSockets |
| python-dotenv | 1.0.1 | BSD-3-Clause | reading `config.env` |
| python-multipart | 0.0.12 | Apache-2.0 | screenshot uploads from the agent |
| psutil | 7.2.2 | BSD-3-Clause | processes, CPU/RAM/network readings |
| Pillow | 12.3.0 | MIT-CMU | screenshots, the PC window's rounded look |
| mss | 10.2.0 | MIT | screenshots |
| pycaw | 20240210 | MIT | volume and mute |
| comtypes | 1.2.1 | MIT | Windows audio COM calls |
| pywin32 | 312 | PSF License | clipboard, Windows APIs |
| qrcode | 8.2 | BSD | the QR code in the PC window |
| cryptography | 46.0.3 | Apache-2.0 OR BSD-3-Clause | IT-Deck's local certificate authority for HTTPS |
| OpenSSL | (cryptography's, 3.5) | Apache-2.0 | cryptography's crypto backend |
| cffi | (cryptography's) | MIT-0 | cryptography's bindings |
| pycparser | (cffi's) | BSD-3-Clause | cffi's C parser |
| Tcl/Tk | 8.6 | Tcl/Tk License (BSD-style) | the PC window |
| PyInstaller bootloader | 6.22.3 | GPL-2.0-or-later **with the bootloader exception** | packing everything into one exe |

The PyInstaller exception explicitly allows distributing the built exe under
any licence; the GPL applies to PyInstaller itself, not to IT-Deck.

### Inside itdeck-cam.exe and itdeck-vcam.dll (the webcam)

Built by `.github/workflows/native.yml` from vcpkg (baseline pinned in
`native/vcpkg.json`), statically linked. Each package's licence file ships
in the build artifact's `licenses/` folder.

| Component | Version | Licence | Used for |
|---|---|---|---|
| libdatachannel | 0.24.6 | MPL-2.0 | WebRTC (ICE, DTLS, SRTP, RTP) and the loopback WebSocket |
| libjuice | 1.7.4 | MPL-2.0 | ICE, libdatachannel's |
| libsrtp | 2.8.1 | BSD-3-Clause | SRTP, libdatachannel's |
| usrsctp | 0.9.5.0 | BSD-3-Clause | SCTP, libdatachannel's |
| OpenSSL | 3.6.5 | Apache-2.0 | DTLS, libdatachannel's |
| plog | 1.1.11 | MIT | logging, libdatachannel's |
| nlohmann/json | 3.12.0 | MIT | the signaling messages |
| VCamSample (Simon Mourier) | commit 58b6d4b | MIT | the virtual camera media source, adapted into itdeck-vcam.dll |
| WIL (Windows Implementation Libraries, Microsoft) | 1.0.260126.7 | MIT | Win32 and COM helpers, itdeck-vcam.dll |
| C++/WinRT (Microsoft) | 3.0.260818.1 | MIT | Windows Runtime helpers, itdeck-vcam.dll |
| Cascadia Mono Bold and SemiBold (Microsoft) | [2407.24](https://github.com/microsoft/cascadia-code) | SIL OFL 1.1 | the `[IT-Deck]` webcam watermark, itdeck-cam.exe; the IT-Deck Camera's placeholder text, itdeck-vcam.dll |

MPL-2.0 is file-level copyleft: the files it covers must stay available in
source form. IT-Deck uses libdatachannel and libjuice unmodified, so their
source is the upstream release named above
(github.com/paullouisageneau/libdatachannel, github.com/paullouisageneau/libjuice);
the rest of IT-Deck is not affected. H.264 is decoded by Windows' own Media
Foundation decoder, which is part of the OS and not distributed with IT-Deck.

The watermark is an image of the word "IT-Deck" set in Cascadia Mono Bold,
rendered once by `native/tools/make_watermark.py` into
`native/itdeck-cam/src/watermark_masks.hpp` (8-bit masks at three sizes,
from the static `CascadiaMono-Bold.ttf` of release 2407.24). The camera's
placeholder hint line is set in Cascadia Mono SemiBold and rendered by
`native/tools/make_placeholder.py` into
`native/itdeck-vcam/src/placeholder_masks.hpp`, from the static
`CascadiaMono-SemiBold.ttf` of the same release. The placeholder's logo is
the colour IT-Deck mark, not a font.
No font file ships, only those pixels. The SIL Open Font License 1.1
(Copyright (c) 2019 - Present, Microsoft Corporation, with Reserved Font
Name Cascadia Code; <https://github.com/microsoft/cascadia-code>) places no
restriction on text or images made with the font, and allows free
redistribution of the font itself.

### SoundVolumeView (NirSoft)

`agents/windows/tools/SoundVolumeView.exe`, by Nir Sofer
(<https://www.nirsoft.net/utils/sound_volume_view.html>). It is used to list
the audio devices and to switch the default one.

It is freeware, not open source. NirSoft allows free redistribution **as
long as nothing is charged for it and it is not part of a commercial
product**, and asks that the distribution package goes out complete and
unmodified. IT-Deck ships it unmodified and free of charge, which those
terms allow. Two consequences:

- **Anyone given commercial rights to IT-Deck by the author has to leave
  this file out**: NirSoft's terms don't follow IT-Deck's licence.
- The original download also contains a `readme.txt`. It sits next to the
  exe in `agents/windows/tools/` and is bundled into `ITDeck.exe` with it,
  the safe reading of "complete". (The `.chm` help file is kept in the repo
  but not bundled.)

## Used over the network, not bundled

| Service | Terms | Used for |
|---|---|---|
| Open-Meteo (`api.open-meteo.com`, `geocoding-api.open-meteo.com`) | The free API is for **non-commercial use** only (up to 10,000 calls a day). Data is CC BY 4.0: credit "Weather data by Open-Meteo.com" | the clock-and-weather widget |
| Google favicon service (`www.google.com/s2/favicons`) | Undocumented, no published terms or SLA | one of the fallbacks when "site icon" is pressed in Studio and the page names no usable icon itself; it learns only the site's domain |

## Logos

The one-colour logos in `frontend/js/brand-icons.js` are path data from
[Simple Icons](https://simpleicons.org) (simple-icons 16.32.0), released
under CC0-1.0. **The marks themselves are trademarks of their owners.**
IT-Deck uses them only to show which app or site a tile opens (nominative
use). It is not affiliated with, sponsored by or endorsed by any of them.
Simple Icons' own disclaimer asks every user to follow each brand's
guidelines. Some brands publish those, for example Epic Games and Roblox.

Some well-known games are not in Simple Icons (Xbox, Minecraft, Overwatch,
Apex Legends, Genshin Impact at the time of writing), usually because the
owner asked for removal. They are not redrawn by hand. Wordmark-only logos
(miHoYo's, for one) are left out because a word can't be read at tile size.
