"""Pitch estimation and guitar intonation comparisons for JonsRockSmithMods."""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from typing import Sequence


NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
DEFAULT_TUNING = "E Standard"
CUSTOM_TUNING = "Custom"
TUNING_PRESETS = {
    "E Standard": ("E2", "A2", "D3", "G3", "B3", "E4"),
    "Drop D": ("D2", "A2", "D3", "G3", "B3", "E4"),
    "Eb Standard": ("Eb2", "Ab2", "Db3", "Gb3", "Bb3", "Eb4"),
    "Drop Db": ("Db2", "Ab2", "Db3", "Gb3", "Bb3", "Eb4"),
    "D Standard": ("D2", "G2", "C3", "F3", "A3", "D4"),
    "Drop C": ("C2", "G2", "C3", "F3", "A3", "D4"),
    "Open D": ("D2", "A2", "D3", "F#3", "A3", "D4"),
    "Open G": ("D2", "G2", "D3", "G3", "B3", "D4"),
    "DADGAD": ("D2", "A2", "D3", "G3", "A3", "D4"),
    "Open E": ("E2", "B2", "E3", "G#3", "B3", "E4"),
    "Open C": ("C2", "G2", "C3", "G3", "C4", "E4"),
}
CUSTOM_NOTE_CHOICES = tuple(
    f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}" for midi in range(23, 89)
)
_NOTE_PATTERN = re.compile(r"^\s*([A-Ga-g])([#b]?)(-?\d+)\s*$")
_NATURAL_SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
TUNER_COLOR_GREEN = "#41d98a"
TUNER_COLOR_AMBER = "#f4c542"
TUNER_COLOR_RED = "#f05d68"
TUNER_COLOR_NEUTRAL = "#708090"

GUIDED_STABILITY_SAMPLES = 3
GUIDED_STABILITY_SPAN_CENTS = 8.0
GUIDED_OPEN_TUNING_TOLERANCE_CENTS = 5.0
GUIDED_FRETTED_12TH_TOLERANCE_CENTS = 5.0


@dataclass(frozen=True)
class PitchEstimate:
    frequency_hz: float
    note_name: str
    midi_number: int
    cents_from_nearest_note: float
    confidence: float
    rms: float
    clipped: bool
    timestamp: float


@dataclass(frozen=True)
class StringAssessment:
    harmonic_octave_cents: float | None
    fretted_12th_cents: float | None
    fretted_1st_cents: float | None
    summary: str
    open_tuning_cents: float | None = None
    fretted_12th_tuning_cents: float | None = None


@dataclass(frozen=True)
class GuidedTuningUpdate:
    """One UI-safe result from the automatic open-string/octave sequence."""

    string_number: int | None
    stage: str | None
    target_frequency_hz: float | None
    measured_frequency_hz: float | None
    cents_from_target: float | None
    status: str
    advanced: bool = False
    next_step: tuple[int, str] | None = None
    completed: bool = False


class GuidedTuningFlow:
    """Walk through open and fret-12 stages using the tuning chart targets."""

    def __init__(
        self,
        *,
        stable_samples: int = GUIDED_STABILITY_SAMPLES,
        stability_span_cents: float = GUIDED_STABILITY_SPAN_CENTS,
        open_tolerance_cents: float = GUIDED_OPEN_TUNING_TOLERANCE_CENTS,
    ):
        if stable_samples < 2:
            raise ValueError("Guided tuning needs at least two stable pitch samples")
        if not math.isfinite(stability_span_cents) or not 0.5 <= stability_span_cents <= 50.0:
            raise ValueError("Guided stability span must be from 0.5 to 50 cents")
        if not math.isfinite(open_tolerance_cents) or not 0.5 <= open_tolerance_cents <= 25.0:
            raise ValueError("Guided open-string tolerance must be from 0.5 to 25 cents")
        self.stable_samples = int(stable_samples)
        self.stability_span_cents = float(stability_span_cents)
        self.open_tolerance_cents = float(open_tolerance_cents)
        self.fretted_12th_tolerance_cents = GUIDED_FRETTED_12TH_TOLERANCE_CENTS
        self.sequence = tuple(
            (number, stage)
            for number in range(6, 0, -1)
            for stage in ("open", "fret12")
        )
        self.measurements: dict[int, dict[str, float]] = {}
        self._index: int | None = None
        self._recent: list[PitchEstimate] = []
        self._last_timestamp: float | None = None
        self.completed = False

    @property
    def active(self) -> bool:
        return self._index is not None and not self.completed

    @property
    def current_step(self) -> tuple[int, str] | None:
        if not self.active:
            return None
        return self.sequence[self._index]

    def start(self) -> None:
        self.measurements = {number: {} for number in range(6, 0, -1)}
        self._index = 0
        self._recent.clear()
        self._last_timestamp = None
        self.completed = False

    def stop(self) -> None:
        self._index = None
        self._recent.clear()

    def target_frequency(self, tuning_open_hz: float) -> float | None:
        """Use the selected tuning's nominal open target and nominal octave target."""

        if not math.isfinite(tuning_open_hz) or tuning_open_hz <= 0:
            raise ValueError("Open-string tuning target must be finite and greater than zero")
        step = self.current_step
        if step is None:
            return None
        number, stage = step
        if stage == "open":
            return tuning_open_hz
        return tuning_open_hz * 2.0

    def observe(self, pitch: PitchEstimate, tuning_open_hz: float) -> GuidedTuningUpdate:
        """Advance only on fresh stable notes; compare FRET 12 to its nominal chart target."""

        step = self.current_step
        if step is None:
            return GuidedTuningUpdate(None, None, None, None, None, "complete", completed=self.completed)
        number, stage = step
        target = self.target_frequency(tuning_open_hz)
        if target is None:
            return GuidedTuningUpdate(number, stage, None, None, None, "missing_open_reference")
        if not math.isfinite(pitch.frequency_hz) or pitch.frequency_hz <= 0:
            self._recent.clear()
            return GuidedTuningUpdate(number, stage, target, None, None, "invalid_pitch")
        if self._last_timestamp is not None and pitch.timestamp <= self._last_timestamp:
            return GuidedTuningUpdate(number, stage, target, None, None, "waiting")
        if self._last_timestamp is not None and pitch.timestamp - self._last_timestamp > 0.45:
            self._recent.clear()
        self._last_timestamp = pitch.timestamp
        if pitch.clipped:
            self._recent.clear()
            return GuidedTuningUpdate(number, stage, target, pitch.frequency_hz, None, "clipped")

        self._recent.append(pitch)
        self._recent = self._recent[-self.stable_samples :]
        if len(self._recent) < self.stable_samples:
            return GuidedTuningUpdate(number, stage, target, pitch.frequency_hz, None, "waiting")
        frequencies = sorted(sample.frequency_hz for sample in self._recent)
        span_cents = abs(cents_difference(frequencies[-1], frequencies[0]))
        if span_cents > self.stability_span_cents:
            self._recent = [pitch]
            return GuidedTuningUpdate(number, stage, target, pitch.frequency_hz, None, "unstable")

        measured = frequencies[len(frequencies) // 2]
        cents = cents_difference(measured, target)
        if stage == "open" and abs(cents) > self.open_tolerance_cents:
            return GuidedTuningUpdate(number, stage, target, measured, cents, "tune_open")
        if stage == "fret12" and abs(cents) > self.fretted_12th_tolerance_cents + 1e-9:
            return GuidedTuningUpdate(number, stage, target, measured, cents, "tune_fret12")

        self.measurements[number][stage] = measured
        self._index += 1
        self._recent.clear()
        if self._index >= len(self.sequence):
            self._index = None
            self.completed = True
            next_step = None
        else:
            next_step = self.sequence[self._index]
        accepted_status = "open_tuned" if stage == "open" else "fret12_measured"
        return GuidedTuningUpdate(
            number,
            stage,
            target,
            measured,
            cents,
            accepted_status,
            advanced=True,
            next_step=next_step,
            completed=self.completed,
        )



class ManualIntonationSession:
    """Explicit string/stage selection with fixed equal-tempered targets.

    OPEN and FRET 12 compare to the selected tuning's A440 chart values.
    Either stage can be checked first. FRET 12 always compares to twice the
    selected tuning's nominal open frequency. No observation changes the
    selected string or stage.
    """

    def __init__(self, *, stable_samples: int = 3, stability_span_cents: float = 8.0,
                 open_tolerance_cents: float = 5.0, fret_tolerance_cents: float = 5.0):
        if type(stable_samples) is not int or not 2 <= stable_samples <= 20:
            raise ValueError("Stable samples must be from 2 to 20")
        for value, maximum in ((stability_span_cents, 50), (open_tolerance_cents, 25),
                               (fret_tolerance_cents, 25)):
            if not math.isfinite(value) or not 0.5 <= value <= maximum:
                raise ValueError("Invalid manual intonation stability/tolerance")
        self.stable_samples = stable_samples
        self.stability_span_cents = float(stability_span_cents)
        self.open_tolerance_cents = float(open_tolerance_cents)
        self.fret_tolerance_cents = float(fret_tolerance_cents)
        self.measurements = {number: {} for number in range(6, 0, -1)}
        self.string_number = 6
        self.stage = "open"
        self._recent: list[PitchEstimate] = []
        self._last_timestamp: float | None = None

    @property
    def current_step(self) -> tuple[int, str]:
        return self.string_number, self.stage

    def select(self, string_number: int, stage: str) -> None:
        if type(string_number) is not int or string_number not in range(1, 7):
            raise ValueError("String number must be from 1 to 6")
        if stage not in ("open", "fret12"):
            raise ValueError("Select open or fret12")
        self.string_number, self.stage = string_number, stage
        self._recent.clear()
        self._last_timestamp = None

    def reset(self) -> None:
        for readings in self.measurements.values():
            readings.clear()
        self._recent.clear()
        self._last_timestamp = None

    def target_frequency(self, tuning_open_hz: float) -> float | None:
        if not math.isfinite(tuning_open_hz) or tuning_open_hz <= 0:
            raise ValueError("Open-string target must be finite and positive")
        if self.stage == "open":
            return tuning_open_hz
        return tuning_open_hz * 2.0

    def observe(self, pitch: PitchEstimate, tuning_open_hz: float) -> GuidedTuningUpdate:
        target = self.target_frequency(tuning_open_hz)
        def result(status, measured=None, cents=None):
            return GuidedTuningUpdate(self.string_number, self.stage, target, measured, cents, status)
        if (not math.isfinite(pitch.frequency_hz) or pitch.frequency_hz <= 0
                or not math.isfinite(pitch.timestamp) or not math.isfinite(pitch.confidence)
                or pitch.confidence < 0.65):
            self._recent.clear()
            return result("invalid_pitch")
        if self._last_timestamp is not None and pitch.timestamp <= self._last_timestamp:
            return result("waiting")
        if self._last_timestamp is not None and pitch.timestamp - self._last_timestamp > 0.45:
            self._recent.clear()
        self._last_timestamp = pitch.timestamp
        if pitch.clipped:
            self._recent.clear()
            return result("clipped", pitch.frequency_hz)
        cents = cents_difference(pitch.frequency_hz, target)
        if abs(cents) > 100.0:
            self._recent.clear()
            wrong_octave = abs(abs(cents) - 1200.0) <= 150.0
            return result("wrong_octave" if wrong_octave else "out_of_range", pitch.frequency_hz)
        self._recent.append(pitch)
        self._recent = self._recent[-self.stable_samples:]
        if len(self._recent) < self.stable_samples:
            return result("waiting", pitch.frequency_hz, cents)
        frequencies = sorted(item.frequency_hz for item in self._recent)
        if abs(cents_difference(frequencies[-1], frequencies[0])) > self.stability_span_cents:
            self._recent = [pitch]
            return result("unstable", pitch.frequency_hz)
        measured = frequencies[len(frequencies) // 2]
        cents = cents_difference(measured, target)
        if self.stage == "open":
            if abs(cents) > self.open_tolerance_cents + 1e-9:
                return result("tune_open", measured, cents)
            # Re-capturing open invalidates any earlier fret reading for this string.
            self.measurements[self.string_number] = {"open": measured}
            return result("open_tuned", measured, cents)
        self.measurements[self.string_number]["fret12"] = measured
        return result("fret12_measured" if abs(cents) <= self.fret_tolerance_cents + 1e-9
                      else "tune_fret12", measured, cents)


def midi_for_note(note_name: str) -> int:
    """Convert a note such as E2, F#3, or Bb2 to its MIDI note number."""

    match = _NOTE_PATTERN.fullmatch(str(note_name))
    if match is None:
        raise ValueError(f"Invalid guitar tuning note: {note_name!r}")
    letter, accidental, octave_text = match.groups()
    semitone = _NATURAL_SEMITONES[letter.upper()]
    if accidental == "#":
        semitone += 1
    elif accidental == "b":
        semitone -= 1
    midi_number = (int(octave_text) + 1) * 12 + semitone
    if not 0 <= midi_number <= 127:
        raise ValueError(f"Guitar tuning note is outside MIDI range: {note_name!r}")
    return midi_number


def frequency_for_note(note_name: str) -> float:
    """Return equal-tempered A440 frequency for a written note name."""

    return 440.0 * (2.0 ** ((midi_for_note(note_name) - 69) / 12.0))


def tuning_notes_for(preset_name: str, custom_notes: Sequence[str] | None = None) -> tuple[str, ...]:
    """Return six open-string notes ordered from string 6 (lowest) to string 1."""

    if preset_name == CUSTOM_TUNING:
        if custom_notes is None or len(custom_notes) != 6:
            raise ValueError("Custom tuning needs six notes, ordered from string 6 to string 1")
        notes = tuple(str(note).strip() for note in custom_notes)
        for note in notes:
            midi_for_note(note)
        return notes
    try:
        return TUNING_PRESETS[preset_name]
    except KeyError as exc:
        raise ValueError(f"Unknown guitar tuning preset: {preset_name}") from exc


def expected_tuning_frequency(
    preset_name: str,
    string_number: int,
    custom_notes: Sequence[str] | None = None,
) -> float:
    """Return expected open-string frequency for guitar string number 1 through 6."""

    if string_number not in range(1, 7):
        raise ValueError("Guitar string number must be from 1 to 6")
    notes = tuning_notes_for(preset_name, custom_notes)
    return frequency_for_note(notes[6 - string_number])


def tuning_reference_rows(preset_name: str) -> tuple[tuple[int, str, float, float], ...]:
    """Return string number, open note/Hz, and 12th-fret Hz for selected preset."""
    notes = tuning_notes_for(preset_name, TUNING_PRESETS.get(preset_name))
    return tuple(
        (number, notes[6 - number], frequency_for_note(notes[6 - number]),
         frequency_for_note(notes[6 - number]) * 2.0)
        for number in range(6, 0, -1)
    )


def cents_difference(actual_hz: float, target_hz: float) -> float:
    """Return signed cents from target to actual; positive means actual is sharp."""

    if not math.isfinite(actual_hz) or not math.isfinite(target_hz) or actual_hz <= 0 or target_hz <= 0:
        raise ValueError("Pitch frequencies must be finite values greater than zero")
    return 1200.0 * math.log2(actual_hz / target_hz)


def note_for_frequency(frequency_hz: float) -> tuple[str, int, float]:
    if not math.isfinite(frequency_hz) or frequency_hz <= 0:
        raise ValueError("Pitch frequency must be finite and greater than zero")
    midi_float = 69.0 + 12.0 * math.log2(frequency_hz / 440.0)
    midi_number = int(round(midi_float))
    cents = (midi_float - midi_number) * 100.0
    octave = midi_number // 12 - 1
    name = f"{NOTE_NAMES[midi_number % 12]}{octave}"
    return name, midi_number, cents


def expected_frequency(stage: str, measurements: dict[str, float]) -> float | None:
    """Return current stage reference, based on this string's captured open pitch.

    Open pitch has no prior reference and returns ``None`` so the tuner can show
    a chromatic guide without assuming Standard tuning. Other stages compare
    against the open octave, the captured harmonic, or one semitone above open.
    """

    if stage == "open":
        return None
    open_hz = measurements.get("open")
    if open_hz is None:
        return None
    if not math.isfinite(open_hz) or open_hz <= 0:
        raise ValueError("Open pitch must be finite and greater than zero")
    if stage == "harmonic12":
        return open_hz * 2.0
    if stage == "fret12":
        harmonic_hz = measurements.get("harmonic12")
        if harmonic_hz is not None:
            if not math.isfinite(harmonic_hz) or harmonic_hz <= 0:
                raise ValueError("12th-fret harmonic must be finite and greater than zero")
            return harmonic_hz
        return open_hz * 2.0
    if stage == "fret1":
        return open_hz * (2.0 ** (1.0 / 12.0))
    raise ValueError(f"Unknown intonation reading stage: {stage}")


def pitch_state(cents: float | None, tolerance_cents: float) -> str:
    """Classify cents for the tuner text and center band."""

    if not math.isfinite(tolerance_cents) or not 0.5 <= tolerance_cents <= 25.0:
        raise ValueError("Tolerance must be from 0.5 to 25 cents")
    if cents is None:
        return "WAITING"
    if not math.isfinite(cents):
        return "WAITING"
    if cents < -tolerance_cents:
        return "FLAT"
    if cents > tolerance_cents:
        return "SHARP"
    return "IN TUNE"


def tuner_adjustment(cents: float | None, tolerance_cents: float) -> tuple[str, str]:
    """Return large tuner cue: direction to move the measured pitch toward target."""

    state = pitch_state(cents, tolerance_cents)
    if state == "FLAT":
        return ">", "RAISE PITCH"
    if state == "SHARP":
        return "<", "LOWER PITCH"
    if state == "IN TUNE":
        return "✓", "IN TUNE"
    return "•", "PLAY A NOTE"


def pitch_band(cents: float | None, tolerance_cents: float) -> str:
    """Return semantic color band; UI also displays the state text."""

    state = pitch_state(cents, tolerance_cents)
    if state == "WAITING":
        return "neutral"
    if abs(cents) <= tolerance_cents:
        return "green"
    if abs(cents) <= min(50.0, tolerance_cents + 10.0):
        return "amber"
    return "red"


def cents_position(cents: float | None, span_cents: float = 50.0) -> float | None:
    """Map cents to a normalized tuner position; -1=flat, 0=center, +1=sharp."""

    if not math.isfinite(span_cents) or span_cents <= 0:
        raise ValueError("Tuner range must be finite and greater than zero")
    if cents is None or not math.isfinite(cents):
        return None
    return max(-1.0, min(1.0, cents / span_cents))


def estimate_pitch(
    samples: Sequence[float],
    sample_rate: int,
    *,
    min_frequency_hz: float = 55.0,
    max_frequency_hz: float = 1000.0,
    minimum_rms: float = 0.0012,
    analysis_gain: float = 1.0,
    expected_frequency_hz: float | None = None,
    timestamp: float | None = None,
) -> PitchEstimate | None:
    """Estimate a guitar fundamental with a bounded YIN-style difference curve.

    ``samples`` may contain normalized float audio or signed 16-bit PCM values.
    The analyzer rejects silence and out-of-range estimates and reports clipping
    so the GUI can avoid accepting an overloaded signal.
    """

    try:
        import numpy as np
    except ImportError as exc:  # Keep the song manager usable if NumPy is absent.
        raise RuntimeError("NumPy is not installed; install the packages in requirements.txt") from exc
    if sample_rate <= 0 or min_frequency_hz <= 0 or max_frequency_hz <= min_frequency_hz:
        raise ValueError("Invalid sample rate or pitch range")
    if not math.isfinite(minimum_rms) or minimum_rms <= 0:
        raise ValueError("Minimum RMS must be finite and greater than zero")
    if not math.isfinite(analysis_gain) or not 1.0 <= analysis_gain <= 8.0:
        raise ValueError("Tuner analysis gain must be from 1.0x to 8.0x")
    if expected_frequency_hz is not None and (
        not math.isfinite(expected_frequency_hz) or expected_frequency_hz <= 0
    ):
        raise ValueError("Expected tuner frequency must be finite and greater than zero")

    data = np.asarray(samples)
    if data.ndim != 1 or data.size < 2048:
        return None
    if np.issubdtype(data.dtype, np.integer):
        signal = data.astype(np.float64) / 32768.0
    else:
        signal = data.astype(np.float64, copy=False)
    if not np.all(np.isfinite(signal)):
        return None

    peak = float(np.max(np.abs(signal)))
    rms = float(np.sqrt(np.mean(signal * signal)))
    if rms * analysis_gain < minimum_rms:
        return None
    clipped = peak >= 0.985

    # Boost only the analysis copy. Clipping is measured before this software gain.
    signal = signal * analysis_gain
    signal = signal - float(np.mean(signal))
    sample_count = int(signal.size)
    min_lag = max(2, int(sample_rate / max_frequency_hz))
    max_lag = min(sample_count // 2, int(sample_rate / min_frequency_hz))
    if max_lag <= min_lag + 2:
        return None

    fft_size = 1 << (2 * sample_count - 1).bit_length()
    spectrum = np.fft.rfft(signal, n=fft_size)
    autocorrelation = np.fft.irfft(spectrum * np.conjugate(spectrum), n=fft_size)[: max_lag + 1]
    energy_prefix = np.empty(sample_count + 1, dtype=np.float64)
    energy_prefix[0] = 0.0
    np.cumsum(signal * signal, out=energy_prefix[1:])
    lags = np.arange(1, max_lag + 1, dtype=np.int64)
    difference = (
        energy_prefix[sample_count - lags]
        + energy_prefix[sample_count]
        - energy_prefix[lags]
        - 2.0 * autocorrelation[lags]
    )
    difference = np.maximum(difference, 0.0)
    cumulative = np.cumsum(difference)
    cmndf = np.ones(max_lag + 1, dtype=np.float64)
    valid = cumulative > 1e-18
    cmndf[1:][valid] = difference[valid] * lags[valid] / cumulative[valid]

    search = cmndf[min_lag : max_lag + 1]
    below = np.flatnonzero(search < 0.16)
    if below.size:
        lag = min_lag + int(below[0])
        while lag < max_lag and cmndf[lag + 1] < cmndf[lag]:
            lag += 1
    else:
        lag = min_lag + int(np.argmin(search))

    if lag <= min_lag or lag >= max_lag:
        return None
    left = float(cmndf[lag - 1])
    center = float(cmndf[lag])
    right = float(cmndf[lag + 1])
    denominator = left - 2.0 * center + right
    shift = 0.5 * (left - right) / denominator if abs(denominator) > 1e-12 else 0.0
    shift = min(1.0, max(-1.0, shift))
    refined_lag = lag + shift
    frequency = float(sample_rate / refined_lag)
    if not min_frequency_hz <= frequency <= max_frequency_hz:
        return None
    confidence = min(1.0, max(0.0, 1.0 - center))
    if confidence < 0.45:
        return None

    # YIN can lock onto a strong second harmonic and report an octave too high.
    # Retry near the stage target only when the spectrum contains real energy at
    # the proposed subharmonic; a pure octave-up tone must remain octave-up.
    if expected_frequency_hz is not None and abs(
        cents_difference(frequency, expected_frequency_hz * 2.0)
    ) <= 300.0:
        lower = max(min_frequency_hz, expected_frequency_hz * (2.0 ** (-300.0 / 1200.0)))
        upper = min(max_frequency_hz, expected_frequency_hz * (2.0 ** (300.0 / 1200.0)))
        if upper > lower:
            corrected = estimate_pitch(
                samples,
                sample_rate,
                min_frequency_hz=lower,
                max_frequency_hz=upper,
                minimum_rms=minimum_rms,
                analysis_gain=analysis_gain,
                timestamp=timestamp,
            )
            if corrected is not None:
                spectrum = np.abs(np.fft.rfft(signal * np.hanning(sample_count), n=fft_size))

                def peak_near(frequency_hz: float) -> float:
                    center_bin = int(round(frequency_hz * fft_size / sample_rate))
                    radius = max(1, int(round(center_bin * 0.015)))
                    first = max(0, center_bin - radius)
                    last = min(spectrum.size, center_bin + radius + 1)
                    return float(np.max(spectrum[first:last])) if first < last else 0.0

                fundamental_energy = peak_near(corrected.frequency_hz)
                octave_energy = peak_near(frequency)
                # A restricted lag search can find a multiple of a pure tone's
                # period. Require an independently visible fundamental peak,
                # above the surrounding spectral floor, before using that lag.
                center_bin = int(round(corrected.frequency_hz * fft_size / sample_rate))
                first = max(1, int(center_bin * 0.65))
                last = min(spectrum.size, int(center_bin * 1.35) + 1)
                neighborhood = [float(spectrum[index]) for index in range(first, last)
                                if abs(index - center_bin) > 3]
                local_floor = float(np.median(neighborhood)) if neighborhood else float("inf")
                if (fundamental_energy >= octave_energy * 0.04
                        and fundamental_energy >= max(1e-12, local_floor * 6.0)):
                    return corrected

    note_name, midi_number, cents = note_for_frequency(frequency)
    return PitchEstimate(
        frequency_hz=frequency,
        note_name=note_name,
        midi_number=midi_number,
        cents_from_nearest_note=cents,
        confidence=confidence,
        rms=rms,
        clipped=clipped,
        timestamp=time.monotonic() if timestamp is None else timestamp,
    )


def assess_string(
    measurements: dict[str, float],
    tolerance_cents: float = 5.0,
    *,
    target_open_hz: float | None = None,
    target_fretted_12th_hz: float | None = None,
) -> StringAssessment:
    """Compare a captured string to itself, including the user's fret-1 case.

    Keys: ``open``, ``harmonic12``, ``fret12``, and ``fret1``. The open note is
    the reference so this works for Rocksmith alternate tunings too.
    """

    if not math.isfinite(tolerance_cents) or not 0.5 <= tolerance_cents <= 25.0:
        raise ValueError("Tolerance must be from 0.5 to 25 cents")
    values: dict[str, float] = {}
    for key in ("open", "harmonic12", "fret12", "fret1"):
        value = measurements.get(key)
        if value is not None:
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{key} pitch must be finite and greater than zero")
            values[key] = float(value)

    target_open_delta = None
    if target_open_hz is not None:
        if not math.isfinite(target_open_hz) or target_open_hz <= 0:
            raise ValueError("Target open pitch must be finite and greater than zero")
        if "open" in values:
            target_open_delta = cents_difference(values["open"], target_open_hz)

    target_fretted_12th_delta = None
    if target_fretted_12th_hz is not None:
        if not math.isfinite(target_fretted_12th_hz) or target_fretted_12th_hz <= 0:
            raise ValueError("Target fretted 12th-fret pitch must be finite and greater than zero")
        if "fret12" in values:
            target_fretted_12th_delta = cents_difference(values["fret12"], target_fretted_12th_hz)

    octave_delta = None
    if "open" in values and "harmonic12" in values:
        octave_delta = cents_difference(values["harmonic12"], values["open"] * 2.0)

    fret12_delta = None
    if "fret12" in values and "harmonic12" in values:
        fret12_delta = cents_difference(values["fret12"], values["harmonic12"])
    elif "fret12" in values and "open" in values:
        # Two-reading guided mode uses captured open pitch as the octave reference.
        fret12_delta = cents_difference(values["fret12"], values["open"] * 2.0)

    fret1_delta = None
    if "open" in values and "fret1" in values:
        expected_first_fret = values["open"] * (2.0 ** (1.0 / 12.0))
        fret1_delta = cents_difference(values["fret1"], expected_first_fret)

    notes: list[str] = []
    if target_open_delta is not None and abs(target_open_delta) > tolerance_cents:
        direction = "sharp" if target_open_delta > 0 else "flat"
        notes.append(f"open string is {abs(target_open_delta):.1f} cents {direction} of the selected game tuning")
    if octave_delta is not None and abs(octave_delta) > tolerance_cents:
        direction = "sharp" if octave_delta > 0 else "flat"
        notes.append(f"12th-fret harmonic is {abs(octave_delta):.1f} cents {direction} of the open-string octave")
    if fret12_delta is not None and abs(fret12_delta) > tolerance_cents:
        direction = "sharp" if fret12_delta > 0 else "flat"
        reference = "its 12th-fret harmonic" if "harmonic12" in values else "twice the captured open pitch"
        if fret12_delta > 0:
            saddle_advice = "move the saddle away from the nut to lengthen the string"
        else:
            saddle_advice = "move the saddle toward the nut to shorten the string"
        notes.append(
            f"fretted 12th fret is {abs(fret12_delta):.1f} cents {direction} of {reference}; "
            f"if the open string is tuned correctly, {saddle_advice} in small steps and recheck"
        )
    if (
        target_open_delta is not None
        and abs(target_open_delta) > tolerance_cents
        and fret12_delta is not None
    ):
        notes.append(
            "open string is outside the selected tuning; tune it to the target and repeat before changing saddle position"
        )
    if target_fretted_12th_delta is not None and abs(target_fretted_12th_delta) > tolerance_cents:
        direction = "sharp" if target_fretted_12th_delta > 0 else "flat"
        notes.append(
            f"fretted 12th fret is {abs(target_fretted_12th_delta):.1f} cents {direction} of the selected tuning's octave"
        )
    if fret1_delta is not None and abs(fret1_delta) > tolerance_cents:
        direction = "sharp" if fret1_delta > 0 else "flat"
        if fret1_delta > 0 and fret12_delta is not None and abs(fret12_delta) <= tolerance_cents:
            notes.append(
                f"1st fret is {abs(fret1_delta):.1f} cents sharp while the 12th fret is close; check light fretting first, then ask a guitar tech to inspect nut/action"
            )
        else:
            notes.append(f"1st fret is {abs(fret1_delta):.1f} cents {direction} of the expected semitone above open")

    if not notes:
        comparisons = (target_open_delta, octave_delta, fret12_delta, target_fretted_12th_delta, fret1_delta)
        if not any(value is not None for value in comparisons):
            notes.append("not enough readings for a pitch comparison yet")
        elif all(value is not None for value in comparisons):
            notes.append(f"all captured comparisons are within the app's ±{tolerance_cents:.1f}-cent review tolerance")
        else:
            notes.append(f"available comparisons are within the app's ±{tolerance_cents:.1f}-cent review tolerance")
    return StringAssessment(
        harmonic_octave_cents=octave_delta,
        fretted_12th_cents=fret12_delta,
        fretted_1st_cents=fret1_delta,
        summary="; ".join(notes),
        open_tuning_cents=target_open_delta,
        fretted_12th_tuning_cents=target_fretted_12th_delta,
    )
