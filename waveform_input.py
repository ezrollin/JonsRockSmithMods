"""Bounded live pitch input using the selected PortAudio Windows backend.

The checker retains only short samples in memory. It never creates an audio
recording or writes captured audio to disk.
"""

from __future__ import annotations

import logging
import math
import queue
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass

from intonation_analysis import PitchEstimate, estimate_pitch, note_for_frequency


class AudioInputError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioInputDevice:
    device_id: int
    name: str
    hostapi: str
    max_input_channels: int
    default_samplerate: float

    @property
    def label(self) -> str:
        return f"{self.device_id}: {self.hostapi} - {self.name}"

    def __str__(self) -> str:
        return self.label


@dataclass(frozen=True)
class AudioOutputDevice:
    device_id: int
    name: str
    hostapi: str
    max_output_channels: int
    default_samplerate: float

    @property
    def label(self) -> str:
        return f"{self.device_id}: {self.hostapi} - {self.name}"

    def __str__(self) -> str:
        return self.label


def _sounddevice_module():
    try:
        import sounddevice as sd
    except ImportError as exc:
        install_command = f'"{sys.executable}" -m pip install --user sounddevice==0.5.6'
        raise AudioInputError(
            f"Live audio support is missing for this Python interpreter ({sys.executable}): {exc}. "
            f"Install it for this interpreter with: {install_command}"
        ) from exc
    return sd


def enumerate_input_devices() -> list[AudioInputDevice]:
    sd = _sounddevice_module()
    try:
        hostapis = sd.query_hostapis()
        result: list[AudioInputDevice] = []
        for device_id, info in enumerate(sd.query_devices()):
            channels = int(info.get("max_input_channels", 0))
            if channels < 1:
                continue
            hostapi_id = int(info.get("hostapi", -1))
            hostapi_name = (
                str(hostapis[hostapi_id].get("name", "Windows audio"))
                if 0 <= hostapi_id < len(hostapis)
                else "Windows audio"
            )
            result.append(
                AudioInputDevice(
                    device_id=device_id,
                    name=str(info.get("name", "Unknown input device")),
                    hostapi=hostapi_name,
                    max_input_channels=channels,
                    default_samplerate=float(info.get("default_samplerate", 48000.0)),
                )
            )
        return result
    except Exception as exc:
        logging.exception("Could not enumerate audio input devices")
        raise AudioInputError(f"Could not read Windows audio input devices: {exc}") from exc


def preferred_guitar_device(
    devices: list[AudioInputDevice],
    preferredname: str | None = None,
    preferredhostapi: str | None = None,
) -> AudioInputDevice | None:
    """Select an explicit saved input or a recognized guitar cable, never a random mic.

    Device IDs change across boots. Saved names and host APIs are matched afresh.
    A caller should warn when the returned identity differs from a saved choice.
    """
    def normalized(value: str) -> str:
        return " ".join(value.casefold().split())

    def rank(device: AudioInputDevice) -> tuple[int, int, str, int]:
        name = normalized(device.name)
        mme = normalized(device.hostapi) == "mme"
        cable_name = name.replace("rocksmith usb ", "rocksmith ")
        exact_user_cable = cable_name.startswith("microphone (5- rocksmith gu")
        rocksmith_gu = "rocksmith gu" in cable_name
        return (0 if mme and exact_user_cable else 1 if mme and rocksmith_gu else 2,
                0 if mme else 1 if "wasapi" in normalized(device.hostapi) else 2,
                name, device.device_id)

    if preferredname:
        matched = [device for device in devices
                   if normalized(device.name) == normalized(preferredname)
                   and (not preferredhostapi or normalized(device.hostapi) == normalized(preferredhostapi))]
        if matched:
            return min(matched, key=rank)
    guitars = [device for device in devices
               if "rocksmith" in normalized(device.name) or "real tone" in normalized(device.name)]
    if preferredname:
        same_cable = [device for device in guitars
                      if normalized(device.name) == normalized(preferredname)]
        if same_cable:
            return min(same_cable, key=rank)
    return min(guitars, key=rank) if guitars else None


def _audio_retry_allowed(error: Exception) -> bool:
    """Retry format negotiation only; known access/ownership failures need user action."""
    if isinstance(error, AudioInputError):
        return False
    text = str(error).casefold()
    return not any(term in text for term in (
        "permission", "access denied", "privacy", "e_accessdenied", "device unavailable",
        "device is busy", "already in use", "exclusive", "-9985", "0x80070005",
    ))


def enumerate_output_devices() -> list[AudioOutputDevice]:
    sd = _sounddevice_module()
    try:
        hostapis = sd.query_hostapis()
        result: list[AudioOutputDevice] = []
        for device_id, info in enumerate(sd.query_devices()):
            channels = int(info.get("max_output_channels", 0))
            if channels < 1:
                continue
            hostapi_id = int(info.get("hostapi", -1))
            hostapi_name = (
                str(hostapis[hostapi_id].get("name", "Windows audio"))
                if 0 <= hostapi_id < len(hostapis)
                else "Windows audio"
            )
            result.append(
                AudioOutputDevice(
                    device_id=device_id,
                    name=str(info.get("name", "Unknown output device")),
                    hostapi=hostapi_name,
                    max_output_channels=channels,
                    default_samplerate=float(info.get("default_samplerate", 48000.0)),
                )
            )
        return result
    except Exception as exc:
        logging.exception("Could not enumerate audio output devices")
        raise AudioInputError(f"Could not read Windows audio output devices: {exc}") from exc


def preferred_output_device_id() -> int | None:
    """Return the current Windows default output ID when PortAudio reports one."""

    sd = _sounddevice_module()
    try:
        device = sd.default.device
        if isinstance(device, (tuple, list)) and len(device) > 1:
            value = device[1]
        else:
            value = device
        return int(value) if value is not None and int(value) >= 0 else None
    except (TypeError, ValueError, AttributeError):
        return None


def cents_distance(first_hz: float, second_hz: float) -> float:
    if first_hz <= 0 or second_hz <= 0 or not math.isfinite(first_hz) or not math.isfinite(second_hz):
        return math.inf
    return abs(1200.0 * math.log2(first_hz / second_hz))


class WaveformPitchInput:
    """Capture one selected Windows input and publish recent stable pitch."""

    sample_rates = (48000, 44100)
    block_frames = 4096
    analysis_frames = 8192
    queue_blocks = 4
    no_frames_timeout_seconds = 3.0

    def __init__(
        self,
        device: AudioInputDevice | int,
        *,
        monitor_output: AudioOutputDevice | None = None,
        monitor_gain: float = 0.3,
        analysis_gain: float = 1.0,
        expected_frequency_hz: float | None = None,
    ):
        if not math.isfinite(monitor_gain) or not 0.0 <= monitor_gain <= 1.0:
            raise ValueError("Monitor gain must be from 0.0 to 1.0")
        if not math.isfinite(analysis_gain) or not 1.0 <= analysis_gain <= 8.0:
            raise ValueError("Tuner analysis gain must be from 1.0x to 8.0x")
        if expected_frequency_hz is not None and (
            not math.isfinite(expected_frequency_hz) or expected_frequency_hz <= 0
        ):
            raise ValueError("Expected tuner frequency must be finite and greater than zero")
        if isinstance(device, AudioInputDevice):
            self.device = device
        else:
            self.device = AudioInputDevice(
                device_id=int(device),
                name=f"Windows audio input {device}",
                hostapi="",
                max_input_channels=1,
                default_samplerate=48000.0,
            )
        self.monitor_output = monitor_output
        self.monitor_gain = float(monitor_gain)
        self.analysis_gain = float(analysis_gain)
        self._expected_frequency_hz = expected_frequency_hz
        self._expected_generation = 0
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._latest: PitchEstimate | None = None
        self._recent: deque[PitchEstimate] = deque(maxlen=8)
        self._status = "Stopped"
        self._error: str | None = None
        self._sample_rate: int | None = None
        self._blocks: queue.Queue = queue.Queue(maxsize=self.queue_blocks)
        self._overflow = threading.Event()

    @property
    def is_alive(self) -> bool:
        """True until the owned capture worker has fully released its stream."""
        with self._lock:
            thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def status(self) -> str:
        with self._lock:
            return self._status

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def latest(self) -> PitchEstimate | None:
        with self._lock:
            return self._latest

    @property
    def expected_frequency_hz(self) -> float | None:
        with self._lock:
            return self._expected_frequency_hz

    def set_expected_frequency(self, frequency_hz: float | None) -> None:
        """Switch pitch analysis target and discard readings from the previous step."""

        if frequency_hz is not None and (
            not math.isfinite(frequency_hz) or frequency_hz <= 0
        ):
            raise ValueError("Expected tuner frequency must be finite and greater than zero")
        with self._lock:
            self._expected_frequency_hz = frequency_hz
            self._expected_generation += 1
            self._latest = None
            self._recent.clear()
        self._clear_blocks()

    def _estimate(self, samples, sample_rate: int, expected_frequency_hz: float | None):
        return estimate_pitch(
            samples,
            sample_rate,
            analysis_gain=self.analysis_gain,
            expected_frequency_hz=expected_frequency_hz,
        )

    def stable_reading(self, *, max_age_seconds: float = 0.45) -> PitchEstimate:
        with self._lock:
            recent = list(self._recent)
            latest = self._latest
        now = time.monotonic()
        if latest is None or now - latest.timestamp > max_age_seconds:
            raise AudioInputError("No fresh guitar note detected. Pluck one string and hold it steady.")
        if len(recent) < 3 or any(now - item.timestamp > max_age_seconds for item in recent[-3:]):
            raise AudioInputError("Hold one clear note for a moment, then capture it again.")
        if any(item.clipped for item in recent[-3:]):
            raise AudioInputError("The input is clipping. Lower the Windows input level or instrument output, then try again.")
        if min(item.rms for item in recent[-3:]) * self.analysis_gain < 0.0012:
            raise AudioInputError(
                f"The guitar signal is too quiet at {self.analysis_gain:g}x tuner boost. "
                "Choose a higher tuner input boost or raise the Windows input level."
            )

        frequencies = sorted(item.frequency_hz for item in recent[-5:])
        center = frequencies[len(frequencies) // 2]
        if cents_distance(frequencies[0], frequencies[-1]) > 35.0:
            raise AudioInputError("Pitch is moving too much to measure. Pluck cleanly without bending or vibrato.")
        note_name, midi_number, cents = note_for_frequency(center)
        confidence = sorted(item.confidence for item in recent[-3:])[1]
        rms = sorted(item.rms for item in recent[-3:])[1]
        return PitchEstimate(
            frequency_hz=center,
            note_name=note_name,
            midi_number=midi_number,
            cents_from_nearest_note=cents,
            confidence=confidence,
            rms=rms,
            clipped=False,
            timestamp=latest.timestamp,
        )

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._overflow.clear()
            self._clear_blocks()
            self._latest = None
            self._recent.clear()
            self._error = None
            self._status = "Starting input..."
            self._thread = threading.Thread(target=self._run, name="JonsIntonationAudio", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float = 1.5) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _clear_blocks(self) -> None:
        while True:
            try:
                self._blocks.get_nowait()
            except queue.Empty:
                return

    def _publish_status(self, status: str, error: str | None = None) -> None:
        with self._lock:
            self._status = status
            self._error = error

    def _run(self) -> None:
        sd = None
        errors: list[str] = []
        try:
            sd = _sounddevice_module()
            rates = list(self.sample_rates)
            device_rate = int(round(self.device.default_samplerate)) if math.isfinite(self.device.default_samplerate) else 0
            if 8000 <= device_rate <= 192000 and device_rate not in rates:
                rates.append(device_rate)
            if self.monitor_output is not None:
                output_rate = int(round(self.monitor_output.default_samplerate)) if math.isfinite(self.monitor_output.default_samplerate) else 0
                if 8000 <= output_rate <= 192000 and output_rate not in rates:
                    rates.append(output_rate)
            for rate in rates:
                if self._stop.is_set():
                    break
                try:
                    self._capture_rate(sd, rate)
                    errors.clear()
                    break
                except Exception as exc:
                    errors.append(f"{rate} Hz: {exc}")
                    if self._stop.is_set() or not _audio_retry_allowed(exc):
                        break
            if errors and not self._stop.is_set():
                detail = " | ".join(errors)
                route = "guitar input and speaker output" if self.monitor_output is not None else "guitar input"
                monitor_help = (
                    " Choose input and output devices on the same Windows audio system, or turn off speaker monitoring. "
                    if self.monitor_output is not None else " "
                )
                raise AudioInputError(
                    f"Could not use selected {route}: {self.device.name} ({self.device.hostapi or 'Windows audio'}). "
                    "Check Windows Settings > Privacy & security > Microphone and allow desktop apps. "
                    "If another app owns this cable exclusively, release its exclusive access or close that app, then retry. "
                    "The tuner does not change devices automatically. "
                    f"{monitor_help}Details: {detail}"
                )
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            logging.exception("JonsRockSmithMods intonation input failed")
            self._publish_status("Input error", message)
        finally:
            self._clear_blocks()
            with self._lock:
                if self._status not in ("Input error", "Closed"):
                    self._status = "Stopped"
                self._sample_rate = None

    def _capture_rate(self, sd, sample_rate: int) -> None:
        import numpy as np

        ring = np.empty(0, dtype=np.float64)
        target_generation = -1

        def queue_input(indata, status) -> None:
            if status and getattr(status, "input_overflow", False):
                self._overflow.set()
            block = np.asarray(indata[:, 0], dtype=np.float64).copy()
            if np.issubdtype(indata.dtype, np.integer):
                block /= 32768.0
            try:
                self._blocks.put_nowait(block)
            except queue.Full:
                try:
                    self._blocks.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._blocks.put_nowait(block)
                except queue.Full:
                    pass

        if self.monitor_output is None:
            def callback(indata, _frames, _time_info, status) -> None:
                if not self._stop.is_set():
                    queue_input(indata, status)
        else:
            def callback(indata, outdata, _frames, _time_info, status) -> None:
                if self._stop.is_set():
                    outdata.fill(0)
                    return
                route_monitor_audio(indata, outdata, self.monitor_gain, numpy_module=np)
                queue_input(indata, status)

        stream = self._make_stream(sd, sample_rate, callback)
        try:
            with stream:
                with self._lock:
                    self._sample_rate = sample_rate
                    input_host = self.device.hostapi or "Windows audio"
                    if self.monitor_output is None:
                        self._status = (
                            f"Listening through {input_host} at {sample_rate:,} Hz; "
                            f"tuner analysis boost {self.analysis_gain:g}x"
                        )
                    else:
                        self._status = (
                            f"Monitoring to {self.monitor_output.name} at {sample_rate:,} Hz "
                            f"(speaker level {self.monitor_gain:.0%}); input {input_host}; "
                            f"tuner analysis boost {self.analysis_gain:g}x"
                        )
                last_block_at = time.monotonic()
                while not self._stop.is_set():
                    try:
                        block = self._blocks.get(timeout=0.2)
                    except queue.Empty:
                        if time.monotonic() - last_block_at > self.no_frames_timeout_seconds:
                            raise AudioInputError("Selected input opened but supplied no audio frames. Check the cable, driver and microphone access, then restart input.")
                        continue
                    last_block_at = time.monotonic()
                    if self._overflow.is_set():
                        self._overflow.clear()
                        self._clear_blocks()
                        ring = np.empty(0, dtype=np.float64)
                        with self._lock:
                            self._latest = None
                            self._recent.clear()
                            self._status = (
                                f"Listening through {self.device.hostapi or 'Windows audio'} at {sample_rate:,} Hz; "
                                "input dropped audio, pluck again for a fresh reading"
                            )
                        continue
                    ring = np.concatenate((ring, block))[-self.analysis_frames :]
                    with self._lock:
                        expected_frequency_hz = self._expected_frequency_hz
                        expected_generation = self._expected_generation
                    if expected_generation != target_generation:
                        ring = np.empty(0, dtype=np.float64)
                        target_generation = expected_generation
                        continue
                    if ring.size < self.analysis_frames:
                        continue
                    estimate = self._estimate(ring, sample_rate, expected_frequency_hz)
                    if estimate is not None:
                        with self._lock:
                            if expected_generation == self._expected_generation:
                                self._latest = estimate
                                self._recent.append(estimate)
        finally:
            # __enter__ can fail after opening a device; then __exit__ never runs.
            # Close only our stream, including that partial-start failure case.
            if not getattr(stream, "closed", False):
                try:
                    stream.close()
                except Exception as error:
                    raise AudioInputError("Could not release selected audio stream; restart the tuner before another attempt.") from error

    def _make_stream(self, sd, sample_rate: int, callback):
        input_settings = None
        if "wasapi" in self.device.hostapi.casefold():
            input_settings = sd.WasapiSettings(exclusive=False, auto_convert=True)
        if self.monitor_output is None:
            return sd.InputStream(
                device=self.device.device_id,
                samplerate=sample_rate,
                blocksize=self.block_frames,
                channels=1,
                dtype="int16",
                latency="high",
                extra_settings=input_settings,
                callback=callback,
            )

        if (self.device.hostapi and self.monitor_output.hostapi
                and self.device.hostapi.casefold() != self.monitor_output.hostapi.casefold()):
            raise AudioInputError("Input and speaker monitoring must use the same Windows audio backend. Choose matching devices or turn off monitoring.")
        output_settings = None
        if "wasapi" in self.monitor_output.hostapi.casefold():
            output_settings = sd.WasapiSettings(exclusive=False, auto_convert=True)
        return sd.Stream(
            device=(self.device.device_id, self.monitor_output.device_id),
            samplerate=sample_rate,
            blocksize=0,
            channels=(1, min(2, self.monitor_output.max_output_channels)),
            dtype=("float32", "float32"),
            latency=("low", "low"),
            extra_settings=(input_settings, output_settings),
            callback=callback,
        )


def route_monitor_audio(indata, outdata, gain: float, *, numpy_module=None) -> None:
    """Copy first guitar input channel to each speaker channel at bounded gain."""

    if numpy_module is None:
        import numpy as np
    else:
        np = numpy_module

    if not math.isfinite(gain) or not 0.0 <= gain <= 1.0:
        raise ValueError("Monitor gain must be from 0.0 to 1.0")
    outdata.fill(0)
    if gain == 0.0 or indata.ndim != 2 or outdata.ndim != 2 or indata.shape[1] < 1:
        return
    for channel in range(outdata.shape[1]):
        np.multiply(indata[:, 0], gain, out=outdata[:, channel], casting="unsafe")

