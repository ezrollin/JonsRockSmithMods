from __future__ import annotations

import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace
from waveform_input import AudioInputDevice
from tuner_settings import default_settings

from intonation_analysis import (
    GuidedTuningFlow,
    ManualIntonationSession,
    PitchEstimate,
    TUNING_PRESETS,
    cents_difference,
    frequency_for_note,
    note_for_frequency,
    TUNER_COLOR_RED,
    TUNER_COLOR_AMBER,
    TUNER_COLOR_GREEN,
)
from intonation_checker import (
    DEFAULT_TUNING_LABEL,
    STRING_LABELS,
    TUNING_LABEL_TO_NAME,
    IntonationCheckerWindow,
    guided_capture_sequence,
    tuner_window_size,
)


class Variable:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def pitch(frequency: float, timestamp: float, *, clipped: bool = False) -> PitchEstimate:
    note, midi, cents = note_for_frequency(frequency)
    return PitchEstimate(
        frequency_hz=frequency,
        note_name=note,
        midi_number=midi,
        cents_from_nearest_note=cents,
        confidence=0.95,
        rms=0.1,
        clipped=clipped,
        timestamp=timestamp,
    )


class GuidedTuningFlowTests(unittest.TestCase):
    def feed_stable(self, flow, frequency, open_target, start_time):
        updates = []
        for offset in range(3):
            updates.append(flow.observe(pitch(frequency, start_time + offset * 0.1), open_target))
        return updates[-1]

    def test_sequence_visits_open_then_octave_for_strings_six_to_one(self):
        self.assertEqual(
            guided_capture_sequence(),
            tuple((string, stage) for string in range(6, 0, -1) for stage in ("open", "fret12")),
        )

    def test_open_stage_requires_stability_and_tuning_tolerance(self):
        flow = GuidedTuningFlow()
        flow.start()
        target = frequency_for_note("E2")
        off_target = target * 2 ** (12.0 / 1200.0)
        update = self.feed_stable(flow, off_target, target, 1.0)
        self.assertEqual(update.status, "tune_open")
        self.assertFalse(update.advanced)
        self.assertEqual(flow.current_step, (6, "open"))
        self.assertNotIn("open", flow.measurements[6])

        update = self.feed_stable(flow, target * 2 ** (2.0 / 1200.0), target, 2.0)
        self.assertEqual(update.status, "open_tuned")
        self.assertTrue(update.advanced)
        self.assertEqual(update.next_step, (6, "fret12"))
        self.assertAlmostEqual(flow.target_frequency(target), flow.measurements[6]["open"] * 2.0)

    def test_fret12_advances_only_at_exact_open_octave(self):
        flow = GuidedTuningFlow()
        flow.start()
        open_target = frequency_for_note("E2")
        self.feed_stable(flow, open_target, open_target, 1.0)
        fret_target = flow.measurements[6]["open"] * 2.0
        update = self.feed_stable(flow, fret_target, open_target, 2.0)
        self.assertEqual(update.status, "fret12_measured")
        self.assertTrue(update.advanced)
        self.assertEqual(update.next_step, (5, "open"))
        self.assertAlmostEqual(cents_difference(flow.measurements[6]["fret12"], fret_target), 0.0, places=5)

    def test_stable_flat_fret12_pitch_does_not_advance(self):
        flow = GuidedTuningFlow()
        flow.start()
        open_target = frequency_for_note("E2")
        self.feed_stable(flow, open_target, open_target, 1.0)
        fret_target = flow.measurements[6]["open"] * 2.0
        measured = fret_target * 2 ** (-7.0 / 1200.0)
        update = self.feed_stable(flow, measured, open_target, 2.0)
        self.assertEqual(update.status, "tune_fret12")
        self.assertAlmostEqual(update.cents_from_target, -7.0, places=5)
        self.assertFalse(update.advanced)
        self.assertEqual(flow.current_step, (6, "fret12"))

    def test_stable_sharp_fret12_pitch_does_not_advance(self):
        flow = GuidedTuningFlow()
        flow.start()
        open_target = frequency_for_note("E2")
        self.feed_stable(flow, open_target, open_target, 1.0)
        fret_target = flow.measurements[6]["open"] * 2.0
        measured = fret_target * 2 ** (7.0 / 1200.0)
        update = self.feed_stable(flow, measured, open_target, 2.0)
        self.assertEqual(update.status, "tune_fret12")
        self.assertAlmostEqual(update.cents_from_target, 7.0, places=5)
        self.assertFalse(update.advanced)
        self.assertEqual(flow.current_step, (6, "fret12"))

    def test_stable_wrong_note_does_not_advance_fret12(self):
        flow = GuidedTuningFlow()
        flow.start()
        open_target = frequency_for_note("E2")
        self.feed_stable(flow, open_target, open_target, 1.0)
        update = self.feed_stable(flow, flow.measurements[6]["open"], open_target, 2.0)
        self.assertEqual(update.status, "tune_fret12")
        self.assertAlmostEqual(update.cents_from_target, -1200.0, places=5)
        self.assertFalse(update.advanced)
        self.assertEqual(flow.current_step, (6, "fret12"))

    def test_fret12_tolerance_boundary_is_inclusive(self):
        flow = GuidedTuningFlow()
        flow.start()
        open_target = frequency_for_note("E2")
        self.feed_stable(flow, open_target, open_target, 1.0)
        fret_target = flow.measurements[6]["open"] * 2.0
        update = self.feed_stable(flow, fret_target * 2 ** (5.0 / 1200.0), open_target, 2.0)
        self.assertEqual(update.status, "fret12_measured")
        self.assertTrue(update.advanced)

    def test_unstable_or_stale_pitch_does_not_auto_advance(self):
        flow = GuidedTuningFlow()
        flow.start()
        target = frequency_for_note("E2")
        flow.observe(pitch(target, 1.0), target)
        flow.observe(pitch(target, 1.1), target)
        update = flow.observe(pitch(target, 2.0), target)
        self.assertEqual(update.status, "waiting")
        self.assertFalse(update.advanced)
        update = flow.observe(pitch(target, 2.1), target)
        self.assertEqual(update.status, "waiting")
        update = flow.observe(pitch(target, 2.2), target)
        self.assertEqual(update.status, "open_tuned")
        self.assertTrue(update.advanced)

    def test_full_sequence_completes_and_retains_six_open_and_fret_readings(self):
        flow = GuidedTuningFlow()
        flow.start()
        timestamp = 1.0
        last_update = None
        for number, stage in flow.sequence:
            open_target = frequency_for_note(TUNING_PRESETS["E Standard"][6 - number])
            target = flow.target_frequency(open_target)
            if stage == "open":
                measured = open_target
            else:
                measured = target * 2 ** (-4.0 / 1200.0)
            last_update = self.feed_stable(flow, measured, open_target, timestamp)
            timestamp += 1.0
        self.assertTrue(last_update.completed)
        self.assertFalse(flow.active)
        self.assertEqual(sum("open" in row for row in flow.measurements.values()), 6)
        self.assertEqual(sum("fret12" in row for row in flow.measurements.values()), 6)


class _FakeWidget:
    created = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.destroyed = False
        type(self).created.append(self)

    def pack(self, **_kwargs):
        return None

    def grid(self, **_kwargs):
        return None

    def columnconfigure(self, *_args, **_kwargs):
        return None

    def rowconfigure(self, *_args, **_kwargs):
        return None

    def bind(self, *_args, **_kwargs):
        return None

    def configure(self, **_kwargs):
        self.kwargs.update(_kwargs)

    def protocol(self, *_args, **_kwargs):
        return None

    def title(self, *_args, **_kwargs):
        return None

    def geometry(self, *_args, **_kwargs):
        return None

    def minsize(self, *_args, **_kwargs):
        return None

    def winfo_exists(self):
        return True

    def destroy(self):
        self.destroyed = True

    def grab_set(self):
        raise AssertionError("Tuner must not take a modal grab")

    def winfo_width(self):
        return 700

    def winfo_height(self):
        return 280

    def delete(self, *_args):
        return None


class _FakeScrollablePage(_FakeWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.content = _FakeWidget(self)


class _FakeTreeview(_FakeWidget):
    def heading(self, *_args, **_kwargs):
        return None

    def column(self, *_args, **_kwargs):
        return None

    def get_children(self, *_args):
        return []

    def insert(self, *_args, **_kwargs):
        return "row"


class TunerViewTests(unittest.TestCase):
    def test_window_size_is_screen_bounded_for_small_displays(self):
        self.assertEqual(tuner_window_size(800, 600), (740, 520))
        self.assertEqual(tuner_window_size(500, 400), (440, 320))

    def test_reference_table_shows_all_six_open_and_octave_frequencies_for_selected_tuning(self):
        checker = IntonationCheckerWindow.__new__(IntonationCheckerWindow)
        checker.reference_table = Mock()
        checker.reference_table.get_children.side_effect = [(), tuple(f"string-{n}" for n in range(6, 0, -1))]
        checker.reference_table.selection.return_value = ()
        checker.flow = ManualIntonationSession()
        checker.flow.select(3, "open")
        checker._tuning_name = Mock(return_value="E Standard")
        checker._refresh_reference_table()
        rows = [call.kwargs["values"][1:] for call in checker.reference_table.insert.call_args_list]
        self.assertEqual(6, len(rows))
        self.assertEqual(("E2", "82.41", "164.81"), rows[0])
        self.assertEqual(("A2", "110.00", "220.00"), rows[1])
        self.assertEqual("string-3", checker.reference_table.selection_set.call_args.args[0])
        checker.reference_table.insert.reset_mock()
        checker.reference_table.get_children.side_effect = [
            tuple(f"string-{n}" for n in range(6, 0, -1)),
            tuple(f"string-{n}" for n in range(6, 0, -1)),
        ]
        checker._tuning_name.return_value = "Drop D"
        checker._refresh_reference_table()
        self.assertEqual("D2", checker.reference_table.insert.call_args_list[0].kwargs["values"][1])

    def test_clicking_reference_row_selects_the_same_live_string(self):
        checker = IntonationCheckerWindow.__new__(IntonationCheckerWindow)
        checker.reference_table = Mock()
        checker.reference_table.selection.return_value = ("string-3",)
        checker.string_var = Variable("String 4")
        checker._selection_changed = Mock()
        checker._reference_row_selected()
        self.assertEqual("String 3", checker.string_var.get())
        checker._selection_changed.assert_called_once()

    def test_fret12_live_target_uses_nominal_chart_frequency_and_shows_both_values(self):
        checker = self.make_checker(string_number=3, stage="fret12")
        checker.flow.measurements[3]["open"] = frequency_for_note("G3") * 2 ** (4 / 1200)
        self.assertAlmostEqual(frequency_for_note("G3") * 2, checker._display_target())
        checker.instruction_var = Variable()
        checker._update_instruction()
        self.assertIn("target 392.00 Hz", checker.instruction_var.get())

    def test_close_keeps_window_until_audio_thread_releases(self):
        checker = IntonationCheckerWindow.__new__(IntonationCheckerWindow)
        checker.closed = False
        checker.closing = False
        checker.poll_id = None
        checker.discovery_id = None
        checker.startup_id = None
        checker._close_wait_id = None
        checker._close_wait_started = None
        checker._close_callbacks = []
        checker.capture = Mock()
        checker.capture.is_alive = True
        checker.window = Mock()
        checker.window.winfo_exists.return_value = True
        scheduled = []
        checker.window.after.side_effect = lambda delay, callback: scheduled.append((delay, callback)) or "wait-id"
        checker.input_status_var = Variable()
        finished = Mock()

        self.assertFalse(checker.close_and_then(finished))
        checker.capture.stop.assert_called_once()
        checker.window.destroy.assert_not_called()
        finished.assert_not_called()
        self.assertEqual(1, len(scheduled))

        checker.capture.is_alive = False
        scheduled[0][1]()
        self.assertTrue(checker.closed)
        checker.window.destroy.assert_called_once()
        finished.assert_called_once()

    def test_live_meter_draws_red_amber_green_distance_bands_and_pitch_marker(self):
        checker = IntonationCheckerWindow.__new__(IntonationCheckerWindow)
        checker.tuner_canvas = Mock()
        checker.tuner_canvas.winfo_exists.return_value = True
        checker.tuner_canvas.winfo_width.return_value = 700
        checker._live_comparison = Mock(return_value={
            "band": "red", "string": "String 6 - lowest", "stage": "open",
            "pitch": None, "state": "SHARP", "cents": 22.0, "measured": 83.46, "target": 82.41,
        })
        checker._draw_tuner()
        fills = [call.kwargs["fill"] for call in checker.tuner_canvas.create_rectangle.call_args_list]
        self.assertEqual([TUNER_COLOR_RED, TUNER_COLOR_AMBER, TUNER_COLOR_GREEN,
                          TUNER_COLOR_AMBER, TUNER_COLOR_RED], fills)
        checker.tuner_canvas.create_polygon.assert_called_once()

    def make_checker(self, *, string_number=6, tuning="E Standard", stage="open"):
        checker = IntonationCheckerWindow.__new__(IntonationCheckerWindow)
        label = next(label for label, name in TUNING_LABEL_TO_NAME.items() if name == tuning)
        checker.flow = ManualIntonationSession()
        checker.flow.select(string_number,stage)
        checker.display_step = (string_number, stage)
        checker.tuning_var = Variable(label)
        checker.current_pitch = None
        checker.last_update = None
        checker.last_observed_timestamp = None
        checker._pending_input_label = ''
        checker.current_live_message = "PLAY A NOTE"
        checker.display_target_hz = None
        checker.reference_table = None
        checker.capture = None
        checker.closed = False
        checker.closing = False
        checker._close_wait_id = None
        checker._close_wait_started = None
        checker._close_callbacks = []
        return checker

    def observe(self, checker, frequency, timestamp):
        checker.current_pitch = pitch(frequency, timestamp)
        checker.last_update = checker.flow.observe(checker.current_pitch, checker._tuning_target(checker.flow.string_number)[1])
        return checker.last_update

    def prepare_lifecycle(self, checker):
        checker.closed = False
        checker.closing = False
        checker.paused = False
        checker.poll_id = None
        checker.discovery_id = None
        checker.startup_id = None
        checker._close_wait_id = None
        checker._close_wait_started = None
        checker._close_callbacks = []
        checker.window = Mock()
        checker.reference_table = None
        checker.capture = None
        checker.input_status_var = Variable('')
        checker.live_detail_var = Variable('OLD saddle adjustment')
        checker.instruction_var = Variable('')
        checker.device_var = Variable('')
        checker.device_combo = Mock()
        checker.preference_status_var = Variable('')
        checker.settings = default_settings()
        checker._draw_tuner = Mock()

    def test_open_target_uses_selected_tuning(self):
        checker = self.make_checker(tuning="Drop D")
        for timestamp in (1.0, 1.1, 1.2):
            self.observe(checker, frequency_for_note("D2"), timestamp)
        reading = IntonationCheckerWindow._live_comparison(checker)
        self.assertAlmostEqual(reading["cents"], 0.0, places=7)
        self.assertEqual(reading["string"], STRING_LABELS[0])
        self.assertEqual(reading["stage"], "open")

    def test_fretted_target_uses_nominal_chart_octave_not_captured_open(self):
        checker = self.make_checker(tuning="Eb Standard", stage="fret12")
        open_target = frequency_for_note("Eb2")
        measured_open = open_target * 2 ** (-30.0 / 1200.0)
        checker.flow.measurements[6] = {"open": measured_open}
        for timestamp in (1.0, 1.1, 1.2):
            self.observe(checker, measured_open * 2 * 2 ** (8.0 / 1200.0), timestamp)
        reading = IntonationCheckerWindow._live_comparison(checker)
        self.assertAlmostEqual(reading["cents"], -22.0, places=6)
        self.assertAlmostEqual(reading["target"], open_target * 2.0)

    def test_waiting_and_unstable_readings_have_no_adjustment_or_green(self):
        checker = self.make_checker()
        target = frequency_for_note('E2')
        self.observe(checker, target, 1.0)
        self.assertEqual('waiting', checker.last_update.status)
        self.assertIsNone(checker._live_comparison()['cents'])
        self.assertEqual('neutral', checker._live_comparison()['band'])
        self.observe(checker, target * 2 ** (-8 / 1200), 1.1)
        self.observe(checker, target * 2 ** (8 / 1200), 1.2)
        self.assertEqual('unstable', checker.last_update.status)
        self.assertIsNone(checker._live_comparison()['cents'])
        self.assertEqual('neutral', checker._live_comparison()['band'])

    def test_accepted_median_cents_override_in_tune_last_sample(self):
        for cents in (-8, 8):
            checker = self.make_checker()
            # Give the exact 8-cent fixture room for binary float rounding.
            checker.flow.stability_span_cents = 8.01
            target = frequency_for_note('E2')
            for timestamp, offset in ((1.0, cents), (1.1, cents), (1.2, 0)):
                self.observe(checker, target * 2 ** (offset / 1200), timestamp)
            self.assertEqual('tune_open', checker.last_update.status)
            reading = checker._live_comparison()
            self.assertAlmostEqual(cents, reading['cents'])
            self.assertNotEqual('green', reading['band'])
            self.assertAlmostEqual(target * 2 ** (cents / 1200), reading['measured'])

    def test_duplicate_polls_preserve_accepted_update(self):
        checker = self.make_checker()
        self.prepare_lifecycle(checker)
        checker.capture = SimpleNamespace(status='Listening', error=None, latest=None)
        with patch('intonation_checker.time.monotonic', return_value=1.2):
            for timestamp in (1.0, 1.1, 1.2):
                checker.capture.latest = pitch(frequency_for_note('E2'), timestamp)
                checker._poll_input()
            accepted = checker.last_update
            checker._poll_input()
        self.assertEqual('open_tuned', accepted.status)
        self.assertIs(accepted, checker.last_update)
        self.assertEqual('green', checker._live_comparison()['band'])

    def test_selection_and_pause_immediately_clear_old_saddle_cue(self):
        checker = self.make_checker()
        self.prepare_lifecycle(checker)
        checker.capture = None
        checker.string_var = Variable(STRING_LABELS[1])
        checker.stage_var = Variable('open')
        checker.offset_var = Variable('0')
        checker._selection_changed()
        self.assertNotIn('saddle', checker.live_detail_var.get())
        checker._draw_tuner.assert_called()
        checker.live_detail_var.set('OLD saddle cue')
        checker.stop_listening()
        self.assertEqual('LISTENING PAUSED', checker.live_detail_var.get())

    def test_error_and_stale_capture_clear_previous_details(self):
        for status, error, latest in (('Input error', 'unplugged', None),
                                      ('Listening', None, pitch(frequency_for_note('E2'), 1.0))):
            checker = self.make_checker()
            self.prepare_lifecycle(checker)
            checker.capture = SimpleNamespace(status=status, error=error, latest=latest)
            with patch('intonation_checker.time.monotonic', return_value=5.0):
                checker._poll_input()
            self.assertNotIn('saddle', checker.live_detail_var.get())
            self.assertIsNone(checker.last_update)
            self.assertIsNone(checker.current_pitch)

    def test_refresh_restarts_changed_input_after_old_stream_releases(self):
        checker = self.make_checker()
        self.prepare_lifecycle(checker)
        old = AudioInputDevice(1, 'Rocksmith old', 'MME', 1, 48000)
        new = AudioInputDevice(5, 'Rocksmith new', 'MME', 1, 48000)
        previous = Mock(device=old, status='Listening', is_alive=False)
        checker.capture = previous
        checker.device_var.set(old.label)
        replacement = SimpleNamespace(status='Opening', error=None, latest=None, start=Mock())
        with patch('intonation_checker.enumerate_input_devices', return_value=[new]), \
                patch('intonation_checker.WaveformPitchInput', return_value=replacement) as constructor:
            checker.refresh_devices()
        previous.stop.assert_called_once()
        previous.join.assert_called_once_with(timeout=1.5)
        self.assertEqual(new, constructor.call_args.args[0])
        self.assertEqual(new.label, checker.device_var.get())
        replacement.start.assert_called_once()

    def test_unreleased_stream_keeps_old_label_and_retry_remembers_new_input(self):
        checker = self.make_checker()
        self.prepare_lifecycle(checker)
        old = AudioInputDevice(1, 'Rocksmith old', 'MME', 1, 48000)
        new = AudioInputDevice(5, 'Rocksmith new', 'MME', 1, 48000)
        previous = Mock(device=old, status='Listening', is_alive=True)
        checker.capture = previous
        checker.device_var.set(old.label)
        replacement = SimpleNamespace(status='Opening', error=None, latest=None, start=Mock())
        with patch('intonation_checker.enumerate_input_devices', return_value=[new]), \
                patch('intonation_checker.WaveformPitchInput', return_value=replacement) as constructor:
            checker.refresh_devices()
            constructor.assert_not_called()
            self.assertEqual(old.label, checker.device_var.get())
            self.assertEqual(new.label, checker._pending_input_label)
            previous.is_alive = False
            checker.start_tuning()
            self.assertEqual(new, constructor.call_args.args[0])
            self.assertEqual(new.label, checker.device_var.get())

    def test_normal_tuner_view_has_only_setup_sequence_and_navigation_controls(self):
        checker = IntonationCheckerWindow.__new__(IntonationCheckerWindow)
        checker.master = object()
        checker.closed = False
        checker.discovery_id = None
        checker.window = _FakeWidget()
        checker.device_var = Variable("")
        checker.tuning_var = Variable(DEFAULT_TUNING_LABEL)
        checker.instruction_var = Variable("")
        checker.input_status_var = Variable("")
        checker.live_detail_var = Variable("")
        checker.flow = ManualIntonationSession()
        checker.string_var = Variable(STRING_LABELS[0])
        checker.stage_var = Variable("open")
        checker.offset_var = Variable("0")
        checker.preference_status_var = Variable("")
        checker.display_step = (6, "open")

        _FakeWidget.created.clear()
        replacements = {
            "intonation_checker.ttk.Frame": _FakeWidget,
            "intonation_checker.ttk.LabelFrame": _FakeWidget,
            "intonation_checker.ttk.Label": _FakeWidget,
            "intonation_checker.ttk.Combobox": _FakeWidget,
            "intonation_checker.ttk.Button": _FakeWidget,
            "intonation_checker.ttk.Radiobutton": _FakeWidget,
            "intonation_checker.ttk.Spinbox": _FakeWidget,
            "intonation_checker.ttk.Treeview": _FakeTreeview,
            "intonation_checker.tk.Canvas": _FakeWidget,
            "intonation_checker.ScrollablePage": _FakeScrollablePage,
        }
        patches = [patch(target, replacement) for target, replacement in replacements.items()]
        for active_patch in patches:
            active_patch.start()
        try:
            checker._build()
        finally:
            for active_patch in reversed(patches):
                active_patch.stop()

        labels = [widget.kwargs.get("text") for widget in _FakeWidget.created]
        self.assertIn("Guitar input", labels)
        self.assertIn("String to check", labels)
        self.assertIn("OPEN string", labels)
        self.assertIn("FRET 12 (press the string)", labels)
        self.assertIn("Tuning", labels)
        self.assertIn("Retry / resume audio", labels)
        self.assertIn("Stop listening", labels)
        self.assertIn("Return to main", labels)
        self.assertIs(checker.return_button.args[0].args[0], checker.footer_frame)
        for removed in (
            "Speaker monitoring",
            "Monitor gain",
            "Tuner input boost",
            "Capture this reading",
            "Fretted 1st",
            "12th-fret harmonic",
            "Review tolerance ± cents",
        ):
            self.assertNotIn(removed, labels)

    def test_wrong_octave_never_becomes_a_1200_cent_adjustment(self):
        for stage,frequency in (("open",frequency_for_note("E3")),("fret12",frequency_for_note("E2"))):
            checker=self.make_checker(stage=stage)
            checker.flow.measurements[6]={"open":frequency_for_note("E2")}
            checker.current_pitch=pitch(frequency,1.0)
            reading=checker._live_comparison()
            self.assertIsNone(reading["cents"])
            self.assertEqual(reading["state"],"CHECK STRING / STAGE")

    def test_fret12_without_open_never_shows_an_invented_reference(self):
        checker=self.make_checker(stage="fret12")
        checker.current_pitch=pitch(frequency_for_note("E3"),1.0)
        reading=checker._live_comparison()
        self.assertIsNone(reading["target"])
        self.assertIsNone(reading["cents"])
        self.assertEqual(reading["state"],"CAPTURE OPEN FIRST")

    def test_return_button_closes_tuner_without_modal_grab(self):
        checker = self.make_checker()
        checker.master = object()
        checker.closed = False
        checker.discovery_id = None
        checker.window = _FakeWidget()
        checker.capture = None
        checker.poll_id = None
        checker.window.protocol("WM_DELETE_WINDOW", checker.close)
        checker.close()
        self.assertTrue(checker.window.destroyed)


if __name__ == "__main__":
    unittest.main()
