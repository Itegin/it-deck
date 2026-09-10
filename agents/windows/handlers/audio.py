import csv
import os
import subprocess
import tempfile
from ctypes import POINTER, cast
from pathlib import Path

from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, EDataFlow, ERole, IAudioEndpointVolume

_DATA_FLOWS = {
    "microphone": EDataFlow.eCapture.value,
    "speaker": EDataFlow.eRender.value,
}

# Bundled next to the agent (see agents/windows/tools/), resolved from this
# file's own location rather than a relative path so it doesn't depend on
# the process's cwd when the agent is launched (e.g. as a scheduled task).
_SOUND_VOLUME_VIEW = Path(__file__).resolve().parent.parent / "tools" / "SoundVolumeView.exe"

# Column names must match SoundVolumeView's own header spelling exactly --
# it silently emits an empty column for anything it doesn't recognise
# rather than erroring, so a typo here shows up as blank fields, not a
# crash. Verified against a real /scomma export (tools/devices.csv).
#
# "Type" is not returned to the caller; it's requested purely so the parse
# below can filter to real endpoints. A full export is mostly noise -- of
# 35 rows on this machine only 4 were Type=Device, the rest being Subunit
# (per-channel controls like "Front"/"Rear") and Application (whatever
# happened to be playing audio). It also disambiguates: "Динамики" appears
# BOTH as the Realtek render Device and as a capture-side Subunit under an
# unrelated fifine Microphone, which is exactly the collision
# handle_audio_switch's comment below warns about.
#
# "Device State" is what tells a plugged-in endpoint from one Windows still
# remembers but cannot reach. Windows keeps a row for every endpoint it has
# ever seen -- every HDMI port on the GPU, the headset from last month -- and
# without this column they are indistinguishable from the speaker currently
# on the desk. Verified against a real /scomma export: the values that appear
# are "Active" and "Inactive" (and empty on the non-Device rows).
_DEVICE_COLUMNS = "Name,Command-Line Friendly ID,Direction,Default,Type,Device State"


def _get_volume_interface(device: str) -> IAudioEndpointVolume:
    # Re-activated on every call rather than cached: the default device for
    # a given role can change (unplugged headset, etc.) between polls.
    #
    # Goes through the raw device enumerator rather than pycaw's
    # AudioUtilities.GetMicrophone() / GetSpeakers() convenience methods --
    # those two are NOT symmetric: GetMicrophone() returns a raw IMMDevice
    # (which has .Activate()), but GetSpeakers() wraps that same kind of
    # result in pycaw's higher-level AudioDevice helper, which has no
    # .Activate() at all (confirmed by hand: calling .Activate() on it
    # raises AttributeError). Enumerating directly gives the same raw
    # IMMDevice type for both roles, so one code path works for both.
    enumerator = AudioUtilities.GetDeviceEnumerator()
    endpoint = enumerator.GetDefaultAudioEndpoint(_DATA_FLOWS[device], ERole.eMultimedia.value)
    interface = endpoint.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def get_muted(device: str) -> bool:
    return bool(_get_volume_interface(device).GetMute())


def set_muted(device: str, muted: bool) -> None:
    _get_volume_interface(device).SetMute(muted, None)


def get_volume(device: str) -> int:
    return round(_get_volume_interface(device).GetMasterVolumeLevelScalar() * 100)


def get_default_output_name() -> str:
    # Not built on _get_volume_interface (that resolves to an
    # IAudioEndpointVolume, which has no name) -- AudioUtilities.CreateDevice
    # wraps the same default-render endpoint into pycaw's AudioDevice,
    # whose .FriendlyName reads the DEVPKEY_Device_FriendlyName property
    # (e.g. "Speakers (Realtek...)"). Re-resolved on every call, same
    # reasoning as _get_volume_interface: the default output can change
    # (switched in Windows sound settings, a device unplugged) between polls.
    enumerator = AudioUtilities.GetDeviceEnumerator()
    endpoint = enumerator.GetDefaultAudioEndpoint(_DATA_FLOWS["speaker"], ERole.eMultimedia.value)
    return AudioUtilities.CreateDevice(endpoint).FriendlyName


def set_volume(device: str, value: int) -> None:
    _get_volume_interface(device).SetMasterVolumeLevelScalar(value / 100, None)


# The default budget for one SoundVolumeView export. handle_list_devices is a
# single round trip, so it can spend the whole thing; handle_audio_switch now
# makes two calls in one command and splits it -- see _SWITCH_STEP_TIMEOUT.
_EXPORT_TIMEOUT = 4

# Two subprocess calls inside one execute, so each gets half. The sum (4s) is
# still under the backend's 5s execute budget for the reason the original
# timeout comment gives at length below: a real hang has to surface as this
# handler's own error rather than as the backend's synthetic timeout.
_SWITCH_STEP_TIMEOUT = 2


def list_devices(timeout: float = _EXPORT_TIMEOUT) -> list[dict]:
    # Exports via SoundVolumeView rather than enumerating through pycaw
    # because the "id" below has to be SoundVolumeView's own
    # "Command-Line Friendly ID" -- the exact string OUTPUT_DEVICE_PRIMARY/
    # SECONDARY hold and /SwitchDefault expects (see handle_audio_switch).
    # Only the tool that defines that ID format can be trusted to produce
    # it; deriving it from pycaw's FriendlyName would mean reimplementing
    # the DriverName\Device\Name\Direction convention by hand.
    #
    # A TemporaryDirectory, not NamedTemporaryFile: on Windows a still-open
    # NamedTemporaryFile can't be written by another process, and
    # SoundVolumeView needs to open this path itself. The context manager
    # deletes the directory and the export inside it on the way out, so
    # there's no cleanup to forget and no fixed path in tools/ for
    # concurrent calls to collide over.
    with tempfile.TemporaryDirectory() as tmpdir:
        export_path = Path(tmpdir) / "devices.csv"

        # The timeout is for the same reason handle_audio_switch documents at
        # length below: this runs synchronously inside the agent's single
        # receive loop, so an unbounded subprocess call here would stall
        # the whole agent, and it stays under the backend's 5s execute
        # timeout so a real hang still surfaces as this handler's own
        # error rather than the backend's synthetic one. Caller-supplied
        # because handle_audio_switch has to fit two calls in that budget.
        subprocess.run(
            [str(_SOUND_VOLUME_VIEW), "/scomma", str(export_path), "/Columns", _DEVICE_COLUMNS],
            check=True,
            timeout=timeout,
        )

        # utf-8-sig, not utf-8: SoundVolumeView writes a UTF-8 BOM (verified
        # -- both tools/devices.csv and a fresh export start EF BB BF), and
        # plain utf-8 would leave it on the first header name, making the
        # key "﻿Name" instead of "Name". Device names here are
        # routinely non-ASCII ("Динамики", "Микрофон"), so the encoding has
        # to be explicit anyway rather than left to the machine's locale.
        # newline="" is the csv module's documented requirement: quoted
        # fields in this export can contain embedded newlines.
        with open(export_path, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))

    return [
        {
            "name": row["Name"],
            "id": row["Command-Line Friendly ID"],
            "direction": row["Direction"],
            # Not a Yes/No field: "Default" is empty for non-defaults and
            # otherwise holds the direction it's default for ("Render" /
            # "Capture"), so this is an emptiness test, not a == "Yes".
            #
            # There are three of these columns, one per Windows audio role:
            # "Default", "Default Multimedia" and "Default Communications".
            # This reads the first, on the understanding that it pairs with the
            # trailing "0" the switch commands pass -- NOT verified here, only
            # taken from SoundVolumeView's documented role numbering, and the
            # two columns happen to hold the same value on this machine so a
            # mismatch would not have shown up. If the cycle ever advances to
            # the wrong device, this pairing is the first thing to check.
            # Don't change one without the other.
            "is_default": bool(row["Default"].strip()),
            # Whether Windows can reach this endpoint right now. Everything
            # else in this dict is true of a device that was unplugged months
            # ago; only this says the speaker is on the desk.
            "is_active": row["Device State"].strip() == "Active",
        }
        for row in rows
        if row["Type"] == "Device"
    ]


def handle_audio_mute_toggle(params: dict) -> dict:
    try:
        device = params["device"]
        set_muted(device, not get_muted(device))
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def handle_audio_volume_set(params: dict) -> dict:
    try:
        set_volume(params["device"], params["value"])
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _switch_default_pair(primary: str, secondary: str) -> None:
    # /SwitchDefault toggles: whichever of the two isn't currently the
    # default becomes it. Trailing 0 selects the role -- the same one
    # list_devices' "is_default" reads out of the "Default" column.
    #
    # The timeout is deliberate, not decorative: this runs synchronously
    # inside the agent's single receive loop (see _receive_loop in
    # agent.py), so an unbounded subprocess call here would stall the
    # entire agent -- not just this command, but poll_loop's ticks and
    # every other message too -- if SoundVolumeView.exe ever wedges (a
    # device name that no longer matches anything popping a GUI dialog
    # instead of exiting is the likely way that happens). Kept under
    # the backend's own 5s execute timeout (see CLAUDE.md) rather than
    # equal to it, so on a real hang this handler's own "error" result
    # has a chance to win the race and reach the client before the
    # backend's synthetic "timeout" result does; if both fire, the
    # client just sees the same req_id resolve twice, which is harmless
    # today but worth knowing about.
    subprocess.run(
        [str(_SOUND_VOLUME_VIEW), "/SwitchDefault", primary, secondary, "0"],
        check=True,
        timeout=_SWITCH_STEP_TIMEOUT,
    )


def _set_default(device_id: str) -> None:
    # The cycle's counterpart to _switch_default_pair: "make this one the
    # default" rather than "toggle between these two". Same trailing role
    # argument, so both paths move the same role. /SetDefault is present in
    # the SoundVolumeView build bundled in tools/ (checked against the
    # binary's own switch table, not just the documentation).
    subprocess.run(
        [str(_SOUND_VOLUME_VIEW), "/SetDefault", device_id, "0"],
        check=True,
        timeout=_SWITCH_STEP_TIMEOUT,
    )


def handle_audio_switch(params: dict) -> dict:
    # Two behaviours, and which one applies is decided by whether this item
    # was configured with an explicit pair of devices.
    #
    # The device IDs below hold SoundVolumeView's full "Command-Line Friendly
    # ID" (DriverName\Device\Name\Direction), not a bare device Name -- a bare
    # Name isn't a safe identifier here. E.g. "Динамики" is both the actual
    # Realtek render device AND a capture-side subunit name that shows up
    # under an unrelated fifine Microphone device in the same SoundVolumeView
    # listing; passing just "Динамики" would be ambiguous. They are passed
    # through to subprocess exactly as read -- do not shorten, split, or
    # otherwise parse these strings, the full ID is what disambiguates.
    try:
        # 1. An explicit pair in the item's params, written by Studio's device
        #    picker, is somebody having chosen these two devices on purpose.
        #    Unchanged from before: a straight A/B toggle between them.
        #
        #    Read all-or-nothing rather than per-key: these two IDs are the two
        #    ends of one toggle, so pairing a configured primary with a
        #    fallback secondary would silently toggle between a pair the user
        #    never configured. A half-filled params row means the picker didn't
        #    finish, so it falls through to the cycle below.
        params_primary = params.get("output_device_primary")
        params_secondary = params.get("output_device_secondary")
        if params_primary and params_secondary:
            _switch_default_pair(params_primary, params_secondary)
            return {"status": "ok"}

        # 2. Otherwise: cycle the default through whatever output devices the
        #    PC has *right now*. Enumerating here, at press time, is the whole
        #    fix for "I plugged in a speaker and it never showed up" -- there
        #    is no cached list to go stale because the scan and the switch are
        #    the same action. A device plugged in one second before the press
        #    is in the cycle for that press.
        #
        #    Filtered to is_active so the cycle only visits endpoints Windows
        #    can actually reach: a machine's export also carries every HDMI
        #    port on the GPU and every headset it has ever seen, and stepping
        #    through those would make the tile useless.
        outputs = [
            device
            for device in list_devices(timeout=_SWITCH_STEP_TIMEOUT)
            if device["direction"] == "Render" and device["is_active"]
        ]
        if outputs:
            # Sorted by ID, not left in export order: the cycle has to be
            # stable and repeatable, and SoundVolumeView's own row order is
            # not something this code should depend on. Plugging a device in
            # can still change where it lands in the sequence -- that is
            # inherent to cycling a list that grew -- but the sequence itself
            # is then fixed until the device set changes again.
            outputs.sort(key=lambda device: device["id"])
            current = next(
                (index for index, device in enumerate(outputs) if device["is_default"]),
                None,
            )
            # current is None when the default is an endpoint that isn't in
            # this list at all (an inactive one, or a capture device holding
            # the role): start the cycle from the top rather than give up.
            target = outputs[0] if current is None else outputs[(current + 1) % len(outputs)]
            _set_default(target["id"])
            return {"status": "ok"}

        # 3. Nothing enumerated -- SoundVolumeView ran but reported no active
        #    output. The environment pair is the last thing left to try, and
        #    it is exactly what this handler used to do unconditionally, so a
        #    machine where enumeration fails behaves as it always has.
        primary = os.getenv("OUTPUT_DEVICE_PRIMARY")
        secondary = os.getenv("OUTPUT_DEVICE_SECONDARY")
        if not primary or not secondary:
            return {
                "status": "error",
                "message": "no active output devices found and OUTPUT_DEVICE_PRIMARY/SECONDARY not set",
            }
        _switch_default_pair(primary, secondary)
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def handle_list_devices(params: dict) -> dict:
    # Takes params for signature parity with every other handler (agent.py
    # dispatches them all the same way) -- there is nothing to configure.
    try:
        return {"status": "ok", "devices": list_devices()}
    except Exception as e:
        return {"status": "error", "message": str(e)}
