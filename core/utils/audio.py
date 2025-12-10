import subprocess
import logging
import os
import time
import numpy as np
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


def _get_astra_version() -> str | None:
    """Return Astra SDK version if available."""
    try:
        with open("/etc/astra_version", "r") as f:
            return f.read().strip()
    except Exception:
        logger.warning(
            "Unable to determine Astra SDK version from '%s'",
            "/etc/astra_version"
        )
    return None


class AsoundDeviceManager:
    """
    Manage ALSA ~/.asoundrc configuration for Astra SDK v1.6 style USB audio usage.

    `dev_id` is a string like "01-00" (card-device). If omitted, the first
    matching USB Audio device with the required capability is used.
    """

    PROC_ASOUND_PCM = Path("/proc/asound/pcm")

    def __init__(self):
        self._pcm_input_name  = "astra_capture"
        self._pcm_output_name = "astra_playback"

    @property
    def capture_device(self):
        return self._pcm_input_name

    @property
    def playback_device(self):
        return self._pcm_output_name

    def create_capture_config(self, dev_id: str | None = None) -> bool:
        """
        Create ~/.asoundrc defining pcm.astra_capture, using a USB Audio
        device that supports capture.
        """
        device = self._find_usb_device(
            require_playback=False,
            require_capture=True,
            dev_id=dev_id,
        )
        if device is None:
            return False

        card, dev, name = device
        content = self._build_single_pcm_config(
            pcm_name=self._pcm_input_name,
            card=card,
            dev=dev,
        )
        ok = self._write_asoundrc(content)
        if ok:
            logger.debug(
                "Created ~/.asoundrc capture config using USB Audio card %d, "
                "device %d (%s)",
                card,
                dev,
                name,
            )
        return ok

    def create_playback_config(self, dev_id: str | None = None) -> bool:
        """
        Create ~/.asoundrc defining pcm.astra_playback, using a USB Audio
        device that supports playback.
        """
        device = self._find_usb_device(
            require_playback=True,
            require_capture=False,
            dev_id=dev_id,
        )
        if device is None:
            return False

        card, dev, name = device
        content = self._build_single_pcm_config(
            pcm_name=self._pcm_output_name,
            card=card,
            dev=dev,
        )
        ok = self._write_asoundrc(content)
        if ok:
            logger.debug(
                "Created ~/.asoundrc playback config using USB Audio card %d, "
                "device %d (%s)",
                card,
                dev,
                name,
            )
        return ok

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _read_proc_asound_pcm(self) -> str:
        """Return the content of /proc/asound/pcm as text."""
        try:
            return self.PROC_ASOUND_PCM.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.error("%s not found; is ALSA available?", self.PROC_ASOUND_PCM)
        except Exception as exc:
            logger.error("Failed to read %s: %s", self.PROC_ASOUND_PCM, exc)
        return ""

    def _parse_alsa_line(
        self,
        line: str,
    ) -> tuple[int, int, str, bool, bool, str] | None:
        """
        Parse a /proc/asound/pcm line.

        Returns:
            (card, dev, name, has_playback, has_capture, dev_id) or None.
        """
        # Example line:
        # "00-01: USB Audio : USB Audio : playback 1 : capture 1"
        if ":" not in line:
            return None

        try:
            hw_id, *rest = line.split(":")
            hw_id = hw_id.strip()
            card_str, dev_str = hw_id.split("-")
            card = int(card_str)
            dev = int(dev_str)

            rest_text = ":".join(rest).strip()
            name_field = rest_text.split(":")[0].strip()
            name = name_field.split()[0] if name_field else f"hw{card}_{dev}"

            line_lower = line.lower()
            has_playback = "playback" in line_lower
            has_capture = "capture" in line_lower

            dev_id = f"{card_str.zfill(2)}-{dev_str.zfill(2)}"
            return card, dev, name, has_playback, has_capture, dev_id
        except Exception as exc:
            logger.debug("Could not parse ALSA line '%s': %s", line, exc)
            return None

    def _find_usb_device(
        self,
        require_playback: bool,
        require_capture: bool,
        dev_id: str | None = None,
    ) -> tuple[int, int, str] | None:
        """
        Find a USB audio device matching requested capabilities.

        If dev_id is provided, only that device is considered.

        Returns:
            (card, dev, name) or None.
        """
        pcm_output = self._read_proc_asound_pcm()
        if not pcm_output:
            return None

        # Normalize dev_id to "CC-DD" format if given
        norm_dev_id: str | None = None
        if dev_id is not None:
            try:
                card_str, dev_str = dev_id.split("-")
                norm_dev_id = f"{int(card_str):02d}-{int(dev_str):02d}"
            except Exception:
                logger.error(
                    "Invalid dev_id format '%s'; expected 'card-device'", dev_id
                )
                return None

        selected: tuple[int, int, str] | None = None

        for raw_line in pcm_output.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            line_lower = line.lower()
            if "usb" not in line_lower or "audio" not in line_lower:
                continue

            parsed = self._parse_alsa_line(line)
            if parsed is None:
                continue

            card, dev, name, has_playback, has_capture, line_dev_id = parsed

            if norm_dev_id is not None and line_dev_id != norm_dev_id:
                continue

            if require_playback and not has_playback:
                continue
            if require_capture and not has_capture:
                continue

            selected = (card, dev, name)
            break

        if selected is None:
            caps: list[str] = []
            if require_playback:
                caps.append("playback")
            if require_capture:
                caps.append("capture")
            caps_str = " & ".join(caps) if caps else "any"
            if norm_dev_id is not None:
                logger.warning(
                    "No USB audio device %s with %s found in %s",
                    norm_dev_id,
                    caps_str,
                    self.PROC_ASOUND_PCM,
                )
            else:
                logger.warning(
                    "No USB audio device with %s found in %s",
                    caps_str,
                    self.PROC_ASOUND_PCM,
                )
        return selected

    def _build_single_pcm_config(self, pcm_name: str, card: int, dev: int) -> str:
        """
        Build a minimal ~/.asoundrc text defining a single PCM.
        """
        return f"""
pcm.{pcm_name} {{
    type plug
    slave {{
        pcm "hw:{card},{dev}"
        period_size 1024
        buffer_size 2048
    }}
}}
""".lstrip()

    def _write_asoundrc(self, content: str) -> bool:
        """
        Write ~/.asoundrc, overwriting existing content.
        """
        path = Path(os.path.expanduser("~/.asoundrc"))
        try:
            path.write_text(content, encoding="utf-8")
            logger.debug("Wrote ALSA configuration to %s", path)
            return True
        except Exception as exc:
            logger.error("Failed to write %s: %s", path, exc)
            return False


class USBAudioDeviceManager:

    def __init__(
        self,
        record_device_name: str | None = None,
        playback_device_name: str | None = None
    ):
        self._astra_version = _get_astra_version()
        self._input_device  = self._wait_for_usb_audio_device("input", dev_name=record_device_name)
        self._output_device = self._wait_for_usb_audio_device("output", dev_name=playback_device_name)
        self._asound_manager = AsoundDeviceManager()
        if self._astra_version == "1.6.0":
            if self._asound_manager.create_capture_config(self._input_device):
                self._input_device = self._asound_manager.capture_device
            else:
                self._input_device = None
            if self._asound_manager.create_playback_config(self._output_device):
                self._output_device = self._asound_manager.playback_device
            else:
                self._output_device = None

    @property
    def input_device(self) -> str | None:
        return self._input_device

    @input_device.setter
    def input_device(self, device_id: str):
        logger.warning(
            "Input device manually overridden to '%s'. "
            "This bypasses ALSA device detection and configuration; "
            "use set_record_device() to select and configure a USB input device.",
            device_id,
        )
        self._input_device = device_id

    @property
    def output_device(self) -> str | None:
        return self._output_device

    @output_device.setter
    def output_device(self, device_id: str):
        logger.warning(
            "Output device manually overridden to '%s'. "
            "This bypasses ALSA device detection and configuration; "
            "use set_playback_device() to select and configure a USB output device.",
            device_id,
        )
        self._output_device = device_id

    def can_record(self) -> bool:
        return self._input_device is not None

    def can_playback(self) -> bool:
        return self._output_device is not None

    def set_record_device(self, device_name: str):
        input_device = self._wait_for_usb_audio_device("input", device_name)
        valid = (
            self._asound_manager.create_capture_config(input_device)
            if self._astra_version == "1.6.0"
            else input_device is not None
        )
        if not valid:
            raise ValueError(f"'{device_name}' is not a valid ALSA record device")
        if self._astra_version != "1.6.0":
            self._input_device = input_device

    def set_playback_device(self, device_name: str):
        output_device = self._wait_for_usb_audio_device("output", device_name)
        valid = (
            self._asound_manager.create_playback_config(output_device)
            if self._astra_version == "1.6.0"
            else output_device is not None
        )
        if not valid:
            raise ValueError(f"'{device_name}' is not a valid ALSA playback device")
        if self._astra_version != "1.6.0":
            self._output_device = output_device

    def _run_alsa_cmd(
        self,
        dev_type: Literal["input", "output"]
    ) -> str | None:
        if dev_type == "input":
            dev_cmd = "arecord"
        elif dev_type == "output":
            dev_cmd = "aplay"
        else:
            raise ValueError(
                f"Invalid USB audio device type '{dev_type}', "
                "must be 'input' or 'output'"
            )
        try:
            return subprocess.check_output(
                [dev_cmd, "-l"],
                text=True
            )
        except (OSError, subprocess.CalledProcessError) as e:
            logger.error("Failed to run `%s -l`: %s", dev_cmd, e)
        return None

    def _get_usb_audio_devices(
        self,
        dev_type: Literal["input", "output"],
        dev_name: str | None = None
    ) -> list[str]:

        def _line_to_dev_id(line: str) -> str:
            info = line.split()
            card_idx: int = int(info[info.index("card") + 1][:-1])
            dev_idx: int  = int(info[info.index("device") + 1][:-1])
            return f"plughw:{card_idx},{dev_idx}"

        devices: list[str] = []
        output = self._run_alsa_cmd(dev_type)
        if output is None:
            return devices
        lines = output.splitlines()
        for line in lines:
            if isinstance(dev_name, str) and dev_name in line:
                return [_line_to_dev_id(line)]
            if "USB Audio" in line:
                devices.append(_line_to_dev_id(line))
        return devices

    def _wait_for_usb_audio_device(
        self,
        dev_type: Literal["input", "output"],
        dev_name: str | None = None,
        timeout: float = 5.0
    ) -> str | None:
        """Wait until a USB audio device is available."""
        if self._astra_version == "1.6.0":
            if self._create_asoundrc_for_sdk_1_6():
                logger.info("Using 'plugplay' device for Astra SDK v1.6.0")
                return "plugplay"
        logger.debug("Waiting for USB audio %s device...", dev_type)
        start = time.time()
        while True:
            dev_ids = self._get_usb_audio_devices(dev_type, dev_name)
            if dev_ids:
                return dev_ids[0]
            if time.time() - start > timeout:
                logger.warning(
                    "Timed out after %.1fs waiting for a USB audio %s device. "
                    "Ensure it's connected and recognized by the system (see `%s -l`).",
                    timeout, dev_type, "arecord" if dev_type == "input" else "aplay"
                )
                return None
            time.sleep(0.5) # avoid busy-loop


class AudioManager:

    def __init__(
        self,
        record_device: str | None = None,
        playback_device: str | None = None,
        sample_rate: int = 16000,
        channels: int = 2
    ):
        self._astra_version = _get_astra_version()
        self._device_man = USBAudioDeviceManager(record_device, playback_device)
        self._sample_rate = sample_rate
        self._channels = channels
        self.arecord_process = None
        
        # If Astra SDK 1.6 and specific headset detected -> force 48kHz
        if self._astra_version == "1.6.0":
            try:
                aplay_output = subprocess.check_output("aplay -l", shell=True, text=True)
                if any(name in aplay_output for name in ["H3 [INZONE H3]", "SPACE [SPACE]"]):
                    self._sample_rate = 48000
                    self._channels = 1
                    logger.info("Assigned 48000 Hz sample rate for device on Astra SDK v%s", self._astra_version)
            except Exception as e:
                logger.warning("Failed to parse aplay output for headset detection: %s", str(e))

    @property
    def record_device_id(self) -> str:
        """Get the current audio record device ID."""
        return self._device_man.input_device

    @property
    def playback_device_id(self):
        """Get the current audio playback device ID."""
        return self._device_man.output_device

    @property
    def sample_rate(self):
        """Get the current sample rate."""
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, new_sample_rate):
        """Set a new sample rate."""
        self._sample_rate = new_sample_rate

    def set_playback_device(self, device_name: str):
        self._device_man.set_playback_device(device_name)

    def set_record_device(self, device_name: str):
        self._device_man.set_record_device(device_name)

    def play(self, filename):
        """Play the audio file using the specified audio device."""
        if not self._device_man.can_playback():
            raise RuntimeError("Audio playback device not set.")
        
        logger.debug(f"Playing through: {self.playback_device_id}")
        subprocess.run(["aplay", "-q", "-D", self.playback_device_id, filename], check=True)

    def start_arecord(self, chunk_size=512):
        """Start the arecord subprocess."""
        if not self._device_man.can_record():
            raise RuntimeError("Audio record device not set.")
        if self.arecord_process:
            self.stop_arecord()
        command = f"arecord -D {self.record_device_id} -f S16_LE -r {self._sample_rate} -c {self._channels}"
        self.arecord_process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=chunk_size, shell=True
        )

    def stop_arecord(self):
        """Stop the arecord subprocess."""
        if self.arecord_process:
            self.arecord_process.terminate()
            self.arecord_process.wait()
            self.arecord_process = None

    def read(self, chunk_size=512):
        """Read audio data from the arecord subprocess."""
        if not self.arecord_process:
            raise RuntimeError("arecord process not running.")

        while True:
            data = self.arecord_process.stdout.read(chunk_size * 4)
            if not data:
                break
            yield np.frombuffer(data, dtype=np.int16)[::2].astype(np.float32) / 32768.0


if __name__ == "__main__":
    pass
