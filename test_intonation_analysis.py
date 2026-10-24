import math
import unittest

import numpy as np

from intonation_analysis import (
    assess_string,
    cents_difference,
    cents_position,
    CUSTOM_TUNING,
    TUNING_PRESETS,
    expected_tuning_frequency,
    estimate_pitch,
    expected_frequency,
    frequency_for_note,
    midi_for_note,
    note_for_frequency,
    pitch_band,
    pitch_state,
    tuner_adjustment,
    tuning_reference_rows,
    tuning_notes_for,
)


class IntonationAnalysisTests(unittest.TestCase):
    sample_rate = 48_000
    sample_count = 8192

    def test_reference_table_covers_all_strings_and_matches_requested_standard_frequencies(self):
        rows = tuning_reference_rows("E Standard")
        self.assertEqual(6, len(rows))
        self.assertEqual((6, "E2"), rows[0][:2])
        self.assertAlmostEqual(82.41, rows[0][2], places=2)
        self.assertAlmostEqual(164.81, rows[0][3], places=2)
        self.assertEqual((5, "A2"), rows[1][:2])
        self.assertAlmostEqual(110.00, rows[1][2], places=2)
        self.assertAlmostEqual(220.00, rows[1][3], places=2)
        for preset in TUNING_PRESETS:
            with self.subTest(preset=preset):
                rows = tuning_reference_rows(preset)
                self.assertEqual(tuple(range(6, 0, -1)), tuple(row[0] for row in rows))
                for _, _, open_hz, fret12_hz in rows:
                    self.assertAlmostEqual(open_hz * 2, fret12_hz)

    def tone(self, frequency, *, noise=0.0, clipped=False):
        sample_times = np.arange(self.sample_count, dtype=np.float64) / self.sample_rate
        signal = (
            0.48 * np.sin(2 * math.pi * frequency * sample_times)
            + 0.22 * np.sin(2 * math.pi * frequency * 2 * sample_times + 0.17)
            + 0.09 * np.sin(2 * math.pi * frequency * 3 * sample_times + 0.41)
        )
        if noise:
            rng = np.random.default_rng(20261003)
            signal += rng.normal(0.0, noise, size=signal.size)
        if clipped:
            signal = np.clip(signal * 4.0, -1.0, 1.0)
        return signal

    def test_detects_low_e_open_and_fret_one_with_harmonics(self):
        for target in (82.4069, 87.3071, 164.8138, 329.6276, 659.255):
            with self.subTest(target=target):
                reading = estimate_pitch(self.tone(target, noise=0.003), self.sample_rate)
                self.assertIsNotNone(reading)
                self.assertAlmostEqual(reading.frequency_hz, target, delta=target * 0.001)

    def test_expected_frequency_repairs_supported_octave_alias(self):
        target = 82.4069
        sample_times = np.arange(self.sample_count, dtype=np.float64) / self.sample_rate
        signal = (
            0.10 * np.sin(2 * math.pi * target * sample_times)
            + 1.00 * np.sin(2 * math.pi * target * 2 * sample_times + 0.17)
            + 0.20 * np.sin(2 * math.pi * target * 3 * sample_times + 0.41)
        )
        ordinary = estimate_pitch(signal, self.sample_rate)
        guided = estimate_pitch(signal, self.sample_rate, expected_frequency_hz=target)
        self.assertGreater(ordinary.frequency_hz, target * 1.9)
        self.assertAlmostEqual(guided.frequency_hz, target, delta=target * 0.002)

    def test_expected_frequency_does_not_treat_pure_octave_up_note_as_target(self):
        target = 82.4069
        reading = estimate_pitch(
            self.tone(target * 2), self.sample_rate, expected_frequency_hz=target
        )
        self.assertIsNotNone(reading)
        self.assertAlmostEqual(reading.frequency_hz, target * 2, delta=target * 0.004)

    def test_expected_frequency_rejects_invalid_values(self):
        with self.assertRaisesRegex(ValueError, "Expected tuner frequency"):
            estimate_pitch(self.tone(82.4069), self.sample_rate, expected_frequency_hz=float("nan"))

    def test_silence_and_unusable_windows_are_ignored(self):
        self.assertIsNone(estimate_pitch(np.zeros(self.sample_count), self.sample_rate))
        self.assertIsNone(estimate_pitch(np.ones(512), self.sample_rate))

    def test_clipping_is_reported_for_capture_gate(self):
        reading = estimate_pitch(self.tone(87.3071, clipped=True), self.sample_rate)
        self.assertIsNotNone(reading)
        self.assertTrue(reading.clipped)

    def test_analysis_boost_recovers_quiet_tone_without_hiding_raw_clipping(self):
        quiet = self.tone(82.4069) * 0.001
        self.assertIsNone(estimate_pitch(quiet, self.sample_rate))
        boosted = estimate_pitch(quiet, self.sample_rate, analysis_gain=4.0)
        self.assertIsNotNone(boosted)
        self.assertAlmostEqual(boosted.frequency_hz, 82.4069, delta=0.1)
        self.assertLess(boosted.rms, 0.0012)
        self.assertFalse(boosted.clipped)

        clipped = estimate_pitch(self.tone(82.4069, clipped=True), self.sample_rate, analysis_gain=8.0)
        self.assertIsNotNone(clipped)
        self.assertTrue(clipped.clipped)

        with self.assertRaisesRegex(ValueError, "1.0x to 8.0x"):
            estimate_pitch(self.tone(82.4069), self.sample_rate, analysis_gain=9.0)

    def test_int16_capture_samples_are_scaled_and_signed_cents_are_preserved(self):
        target = 82.4069 * (2.0 ** (-11.0 / 1200.0))
        pcm = np.round(self.tone(target, noise=0.001) * 32767).astype(np.int16)
        reading = estimate_pitch(pcm, self.sample_rate)
        self.assertIsNotNone(reading)
        self.assertAlmostEqual(reading.frequency_hz, target, delta=target * 0.001)
        self.assertLess(reading.cents_from_nearest_note, 0)

    def test_note_and_cents_math_uses_signed_sharp_flat_convention(self):
        a4 = 440.0
        sharp = a4 * (2.0 ** (8.0 / 1200.0))
        flat = a4 * (2.0 ** (-8.0 / 1200.0))
        self.assertAlmostEqual(cents_difference(sharp, a4), 8.0, places=8)
        self.assertAlmostEqual(cents_difference(flat, a4), -8.0, places=8)
        self.assertEqual(note_for_frequency(440.0), ("A4", 69, 0.0))

    def test_first_fret_sharp_is_reported_when_twelfth_fret_matches(self):
        open_hz = 82.4069
        harmonic = open_hz * 2.0
        first_fret_target = open_hz * (2.0 ** (1.0 / 12.0))
        first_fret_measured = first_fret_target * (2.0 ** (12.0 / 1200.0))
        result = assess_string(
            {"open": open_hz, "harmonic12": harmonic, "fret12": harmonic, "fret1": first_fret_measured},
            tolerance_cents=5.0,
        )
        self.assertAlmostEqual(result.fretted_12th_cents, 0.0, places=8)
        self.assertAlmostEqual(result.fretted_1st_cents, 12.0, places=8)
        self.assertIn("1st fret", result.summary)
        self.assertIn("nut/action", result.summary)

    def test_twelfth_fret_sharp_is_reported_against_harmonic(self):
        open_hz = 82.4069
        harmonic = open_hz * 2.0
        measured = harmonic * (2.0 ** (9.0 / 1200.0))
        result = assess_string(
            {"open": open_hz, "harmonic12": harmonic, "fret12": measured},
            tolerance_cents=5.0,
        )
        self.assertAlmostEqual(result.fretted_12th_cents, 9.0, places=8)
        self.assertIn("sharp", result.summary)
        self.assertIn("saddle", result.summary)

    def test_first_fret_comparison_uses_open_reference_for_alternate_tuning(self):
        open_d2 = 73.4162
        fret_one = open_d2 * (2.0 ** (1.0 / 12.0))
        result = assess_string({"open": open_d2, "fret1": fret_one}, tolerance_cents=5.0)
        self.assertAlmostEqual(result.fretted_1st_cents, 0.0, places=8)

    def test_invalid_pitch_values_are_rejected(self):
        with self.assertRaises(ValueError):
            cents_difference(0.0, 440.0)
        with self.assertRaises(ValueError):
            assess_string({"open": float("nan")})
        with self.assertRaises(ValueError):
            assess_string({}, tolerance_cents=30.0)
        self.assertIn("not enough readings", assess_string({}).summary)

    def test_live_tuner_references_follow_captured_string_and_reading_stage(self):
        open_hz = 82.4069
        harmonic = open_hz * 2
        self.assertIsNone(expected_frequency("open", {"open": open_hz}))
        self.assertAlmostEqual(expected_frequency("harmonic12", {"open": open_hz}), harmonic)
        self.assertAlmostEqual(expected_frequency("fret12", {"open": open_hz}), open_hz * 2.0)
        self.assertAlmostEqual(
            expected_frequency("fret12", {"open": open_hz, "harmonic12": harmonic}),
            harmonic,
        )
        self.assertAlmostEqual(
            expected_frequency("fret1", {"open": open_hz}),
            open_hz * 2 ** (1 / 12),
        )

    def test_tuner_direction_points_toward_the_target_pitch(self):
        self.assertEqual(tuner_adjustment(-8, 5), (">", "RAISE PITCH"))
        self.assertEqual(tuner_adjustment(8, 5), ("<", "LOWER PITCH"))
        self.assertEqual(tuner_adjustment(0, 5), ("✓", "IN TUNE"))
        self.assertEqual(tuner_adjustment(None, 5), ("•", "PLAY A NOTE"))

    def test_live_tuner_color_and_position_match_signed_cents(self):
        self.assertEqual(pitch_state(-6, 5), "FLAT")
        self.assertEqual(pitch_state(0, 5), "IN TUNE")
        self.assertEqual(pitch_state(6, 5), "SHARP")
        self.assertEqual(pitch_band(3, 5), "green")
        self.assertEqual(pitch_band(12, 5), "amber")
        self.assertEqual(pitch_band(30, 5), "red")
        self.assertAlmostEqual(cents_position(-25), -0.5)
        self.assertEqual(cents_position(70), 1.0)

    def test_tuning_presets_cover_standard_drop_and_alternate_string_order(self):
        self.assertEqual(tuning_notes_for("E Standard"), ("E2", "A2", "D3", "G3", "B3", "E4"))
        self.assertEqual(tuning_notes_for("Drop D")[0], "D2")
        self.assertEqual(tuning_notes_for("Drop D")[5], "E4")
        self.assertAlmostEqual(expected_tuning_frequency("Drop D", 6), frequency_for_note("D2"))
        self.assertAlmostEqual(expected_tuning_frequency("Drop D", 1), frequency_for_note("E4"))

    def test_note_parser_accepts_sharps_flats_and_custom_six_string_tuning(self):
        self.assertEqual(midi_for_note("C#3"), midi_for_note("Db3"))
        self.assertAlmostEqual(frequency_for_note("Eb2"), frequency_for_note("D#2"))
        custom = ("C2", "G2", "C3", "G3", "C4", "D4")
        self.assertEqual(tuning_notes_for(CUSTOM_TUNING, custom), custom)
        with self.assertRaisesRegex(ValueError, "six notes"):
            tuning_notes_for(CUSTOM_TUNING, ("E2",))
        with self.assertRaisesRegex(ValueError, "Invalid guitar tuning note"):
            midi_for_note("H2")

    def test_guided_two_readings_separate_uniform_open_offset_from_saddle_error(self):
        open_target = frequency_for_note("Eb2")
        open_measured = open_target * (2.0 ** (-40.0 / 1200.0))
        same_offset_fret12 = open_measured * 2.0
        result = assess_string(
            {"open": open_measured, "fret12": same_offset_fret12},
            target_open_hz=open_target,
            target_fretted_12th_hz=open_target * 2.0,
        )
        self.assertAlmostEqual(result.open_tuning_cents, -40.0, places=7)
        self.assertAlmostEqual(result.fretted_12th_cents, 0.0, places=7)
        self.assertIn("tune it to the target and repeat", result.summary)

        sharp_fret12 = same_offset_fret12 * (2.0 ** (8.0 / 1200.0))
        result = assess_string(
            {"open": open_measured, "fret12": sharp_fret12},
            target_open_hz=open_target,
            target_fretted_12th_hz=open_target * 2.0,
        )
        self.assertAlmostEqual(result.fretted_12th_cents, 8.0, places=7)
        self.assertIn("saddle away from the nut", result.summary)

    def test_assessment_separates_open_tuning_from_twelfth_fret_intonation(self):
        open_target = frequency_for_note("D2")
        open_measured = open_target * (2 ** (7.0 / 1200.0))
        harmonic = open_measured * 2
        fret12 = (open_target * 2) * (2 ** (-8.0 / 1200.0))
        result = assess_string(
            {"open": open_measured, "harmonic12": harmonic, "fret12": fret12},
            target_open_hz=open_target,
            target_fretted_12th_hz=open_target * 2,
        )
        self.assertAlmostEqual(result.open_tuning_cents, 7.0, places=7)
        self.assertAlmostEqual(result.fretted_12th_tuning_cents, -8.0, places=7)
        self.assertAlmostEqual(result.fretted_12th_cents, -15.0, places=7)
        self.assertIn("selected game tuning", result.summary)
        self.assertIn("saddle toward the nut", result.summary)




class ManualIntonationSessionTests(unittest.TestCase):
    def pitch(self, frequency, timestamp=1., **kwargs):
        from intonation_analysis import PitchEstimate
        name, midi, cents = note_for_frequency(frequency)
        return PitchEstimate(frequency, name, midi, cents, kwargs.get('confidence', .99), .1,
                             kwargs.get('clipped', False), timestamp)

    def stable(self, session, frequency, target, timestamp=1.):
        update = None
        for index in range(3):
            update = session.observe(self.pitch(frequency, timestamp + index * .1), target)
        return update

    def test_manual_selection_does_not_advance_on_open_or_fret12(self):
        from intonation_analysis import ManualIntonationSession
        for string, target in ((6, frequency_for_note('E2')), (5, 110.)):
            session = ManualIntonationSession()
            session.select(string, 'open')
            update = self.stable(session, target, target)
            self.assertEqual('open_tuned', update.status)
            self.assertFalse(update.advanced)
            self.assertEqual((string, 'open'), session.current_step)
            session.select(string, 'fret12')
            update = self.stable(session, target * 2, target, 2.)
            self.assertEqual('fret12_measured', update.status)
            self.assertAlmostEqual(0., update.cents_from_target, places=8)
            self.assertEqual((string, 'fret12'), session.current_step)

    def test_manual_fret12_target_stays_at_nominal_a440_even_if_open_is_within_tolerance(self):
        from intonation_analysis import ManualIntonationSession
        session = ManualIntonationSession()
        standard = frequency_for_note('G3')
        actual = standard * 2 ** (4. / 1200.)
        session.select(3, 'open')
        self.stable(session, actual, standard)
        session.select(3, 'fret12')
        target = standard * 2
        self.assertAlmostEqual(target, session.target_frequency(standard))
        measured = 397.0
        update = self.stable(session, measured, standard, 2.)
        self.assertAlmostEqual(1200 * math.log2(measured / target), update.cents_from_target)
        self.assertAlmostEqual(target, update.target_frequency_hz)
        self.assertEqual(measured, update.measured_frequency_hz)

    def test_user_reported_fret12_values_are_compared_to_nominal_chart_values(self):
        from intonation_analysis import ManualIntonationSession
        cases = ((5, 'B3', 500.0), (1, 'E4', 662.0))
        for string_number, open_note, measured in cases:
            with self.subTest(string=string_number):
                session = ManualIntonationSession()
                open_target = frequency_for_note(open_note)
                session.select(string_number, 'open')
                self.stable(session, open_target, open_target)
                session.select(string_number, 'fret12')
                target = open_target * 2
                update = self.stable(session, measured, open_target, 2.)
                self.assertAlmostEqual(target, update.target_frequency_hz)
                self.assertAlmostEqual(1200 * math.log2(measured / target), update.cents_from_target)

    def test_wrong_octave_never_emits_1200_cent_adjustment(self):
        from intonation_analysis import ManualIntonationSession
        session = ManualIntonationSession()
        target = frequency_for_note('E2')
        for index, measured in enumerate((target / 2, target * 2)):
            update = session.observe(self.pitch(measured, 1. + index * .1), target)
            self.assertEqual('wrong_octave', update.status)
            self.assertIsNone(update.cents_from_target)
        self.assertEqual({}, session.measurements[6])
        self.stable(session, target, target, 2.)
        session.select(6, 'fret12')
        update = session.observe(self.pitch(target, 3.), target)
        self.assertEqual('wrong_octave', update.status)
        self.assertIsNone(update.cents_from_target)

    def test_fret12_requires_open_and_other_string_references_are_separate(self):
        from intonation_analysis import ManualIntonationSession
        session = ManualIntonationSession()
        self.stable(session, 82.4069, 82.4069)
        session.select(5, 'fret12')
        update = session.observe(self.pitch(220., 2.), 110.)
        self.assertEqual('missing_open_reference', update.status)
        session.select(6, 'fret12')
        update = self.stable(session, 164.8138 * 2 ** (12. / 1200.), 82.4069, 3.)
        self.assertEqual('tune_fret12', update.status)
        self.assertAlmostEqual(12., update.cents_from_target)

    def test_clipped_low_confidence_and_out_of_range_are_not_captured(self):
        from intonation_analysis import ManualIntonationSession
        session = ManualIntonationSession()
        for reading, status in ((self.pitch(82.4069, clipped=True), 'clipped'),
                                (self.pitch(82.4069, confidence=.4), 'invalid_pitch'),
                                (self.pitch(110., 2.), 'out_of_range')):
            update = session.observe(reading, 82.4069)
            self.assertEqual(status, update.status)
            self.assertIsNone(update.cents_from_target)
        self.assertEqual({}, session.measurements[6])

    def test_actual_open_and_twelfth_tones_keep_octave_despite_wrong_hint(self):
        sample_rate = 48_000
        t = np.arange(8192) / sample_rate
        for fundamental in (82.4069, 110., 164.8138, 220.):
            signal = .3 * np.sin(2 * math.pi * fundamental * t)
            for expected in (fundamental / 2., fundamental * 2.):
                reading = estimate_pitch(signal, sample_rate, expected_frequency_hz=expected)
                self.assertIsNotNone(reading)
                self.assertAlmostEqual(fundamental, reading.frequency_hz, delta=fundamental*.002)


if __name__ == "__main__":
    unittest.main()
