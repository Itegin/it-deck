# Third-party notices

IT-Deck itself is under the PolyForm Noncommercial License 1.0.0 (see
[`LICENSE`](LICENSE)). This file lists what it is built from or talks to that
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
| Tcl/Tk | 8.6 | Tcl/Tk License (BSD-style) | the PC window |
| PyInstaller bootloader | 6.22.3 | GPL-2.0-or-later **with the bootloader exception** | packing everything into one exe |

The PyInstaller exception explicitly allows distributing the built exe under
any licence; the GPL applies to PyInstaller itself, not to IT-Deck.

### SoundVolumeView (NirSoft)

`agents/windows/tools/SoundVolumeView.exe`, by Nir Sofer
(<https://www.nirsoft.net/utils/sound_volume_view.html>). It is used to list
the audio devices and to switch the default one.

It is freeware, not open source. NirSoft allows free redistribution **as
long as nothing is charged for it and it is not part of a commercial
product**, and asks that the distribution package goes out complete and
unmodified. IT-Deck ships it unmodified and free of charge, which those
terms allow. Two consequences:

- **IT-Deck cannot be sold, or be part of a paid product, while this file is
  bundled.** See "Before charging money" below.
- The original download also contains a `readme.txt`. Adding it next to the
  exe in `agents/windows/tools/` is the safe reading of "complete".

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

## Before charging money for IT-Deck

Being non-commercial, IT-Deck is fine as it is. A paid edition, paid
features or ads would first need these three changes:

1. **Replace SoundVolumeView.** Enumerate and switch audio devices directly
   through the Windows Core Audio API. `pycaw`/`comtypes` are already
   bundled, and switching the default device uses the undocumented
   `IPolicyConfig` interface, which is how other open-source tools do it.
2. **Weather.** Move to an Open-Meteo paid plan, or to a provider whose
   terms allow commercial use, and keep the attribution.
3. **Drop the Google favicon fallback.** The site's own
   `apple-touch-icon.png` and `/favicon.ico` are already tried after it.

The logos need no change for a paid product. Charging for them as a
feature ("pay for brand logos") would stop being nominative use, so don't.
