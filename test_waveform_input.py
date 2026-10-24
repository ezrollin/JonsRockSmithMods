import sys
import time
import unittest
from unittest.mock import patch, Mock

import numpy as np

from intonation_analysis import PitchEstimate
from waveform_input import (
    AudioInputDevice,
    AudioInputError,
    AudioOutputDevice,
    WaveformPitchInput,
    cents_distance,
    enumerate_input_devices,
    enumerate_output_devices,
    preferred_output_device_id,
    preferred_guitar_device,
    route_monitor_audio,
)


def reading(frequency, *, offset=0.0, clipped=False, rms=0.1):
    return PitchEstimate(
        frequency_hz=frequency,
        note_name="E2",
        midi_number=40,
        cents_from_nearest_note=0.0,
        confidence=0.95,
        rms=rms,
        clipped=clipped,
        timestamp=time.monotonic() - offset,
    )


class WaveformInputModelTests(unittest.TestCase):
    def make_capture(self, pitches):
        capture = WaveformPitchInput(0)
        with capture._lock:
            capture._recent.extend(pitches)
            capture._latest = pitches[-1] if pitches else None
        return capture

    def test_stable_reading_returns_median_frequency(self):
        capture = self.make_capture([reading(82.40), reading(82.41), reading(82.42)])
        result = capture.stable_reading()
        self.assertAlmostEqual(result.frequency_hz, 82.41, places=5)
        self.assertEqual(result.note_name, "E2")

    def test_stage_target_is_passed_to_pitch_estimator(self):
        capture = WaveformPitchInput(0, expected_frequency_hz=164.8138)
        samples = np.zeros(capture.analysis_frames, dtype=np.float64)
        with patch("waveform_input.estimate_pitch", return_value="estimate") as estimate:
            result = capture._estimate(samples, 48000, capture.expected_frequency_hz)
        self.assertEqual(result, "estimate")
        estimate.assert_called_once_with(
            samples,
            48000,
            analysis_gain=1.0,
            expected_frequency_hz=164.8138,
        )

    def test_changing_stage_target_clears_previous_readings(self):
        capture = self.make_capture([reading(82.4), reading(82.41)])
        capture.set_expected_frequency(164.8)
        self.assertEqual(capture.expected_frequency_hz, 164.8)
        self.assertIsNone(capture.latest)
        self.assertEqual(tuple(capture._recent), ())
        capture.set_expected_frequency(None)
        self.assertIsNone(capture.expected_frequency_hz)

    def test_stage_target_must_be_positive_finite_frequency(self):
        for value in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "Expected tuner frequency"):
                WaveformPitchInput(0, expected_frequency_hz=value)
        capture = WaveformPitchInput(0)
        with self.assertRaisesRegex(ValueError, "Expected tuner frequency"):
            capture.set_expected_frequency(float("nan"))

    def test_tuner_boost_can_accept_quiet_signal_but_reports_unboosted_clipping(self):
        quiet = self.make_capture([reading(82.4, rms=0.0004), reading(82.4, rms=0.0004), reading(82.4, rms=0.0004)])
        with self.assertRaisesRegex(AudioInputError, "too quiet"):
            quiet.stable_reading()
        boosted = WaveformPitchInput(0, analysis_gain=4.0)
        with boosted._lock:
            boosted._recent.extend([reading(82.4, rms=0.0004)] * 3)
            boosted._latest = boosted._recent[-1]
        self.assertAlmostEqual(boosted.stable_reading().frequency_hz, 82.4)

        clipped = WaveformPitchInput(0, analysis_gain=8.0)
        with clipped._lock:
            clipped._recent.extend([reading(82.4, clipped=True)] * 3)
            clipped._latest = clipped._recent[-1]
        with self.assertRaisesRegex(AudioInputError, "clipping"):
            clipped.stable_reading()

    def test_missing_audio_dependency_names_active_python_and_install_command(self):
        original_import = __import__

        def import_without_sounddevice(name, *args, **kwargs):
            if name == "sounddevice":
                raise ModuleNotFoundError("No module named 'sounddevice'")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=import_without_sounddevice):
            with self.assertRaises(AudioInputError) as raised:
                enumerate_input_devices()

        message = str(raised.exception)
        self.assertIn(sys.executable, message)
        self.assertIn(
            f'"{sys.executable}" -m pip install --user sounddevice==0.5.6',
            message,
        )

    def test_moving_pitch_is_not_accepted(self):
        capture = self.make_capture([reading(82.4), reading(84.0), reading(85.0)])
        with self.assertRaisesRegex(AudioInputError, "moving too much"):
            capture.stable_reading()

    def test_clipped_or_stale_pitch_is_not_accepted(self):
        clipped = self.make_capture([reading(82.4), reading(82.4), reading(82.4, clipped=True)])
        with self.assertRaisesRegex(AudioInputError, "clipping"):
            clipped.stable_reading()
        stale = self.make_capture([reading(82.4, offset=2.0), reading(82.4, offset=1.0), reading(82.4, offset=1.0)])
        with self.assertRaisesRegex(AudioInputError, "No fresh guitar note"):
            stale.stable_reading()

    def test_cents_distance_is_unsigned_for_spread_check(self):
        self.assertAlmostEqual(cents_distance(440.0, 440.0), 0.0)
        self.assertAlmostEqual(cents_distance(440.0 * 2 ** (20 / 1200), 440.0), 20.0, places=8)

    def test_monitor_routes_mono_guitar_to_all_speaker_channels_at_selected_gain(self):
        guitar = np.array([[0.25], [-0.5], [1.0]], dtype=np.float32)
        speakers = np.empty((3, 2), dtype=np.float32)
        route_monitor_audio(guitar, speakers, 0.3)
        np.testing.assert_allclose(speakers[:, 0], guitar[:, 0] * 0.3)
        np.testing.assert_allclose(speakers[:, 1], guitar[:, 0] * 0.3)

    def test_zero_monitor_gain_silences_output_and_invalid_gain_is_rejected(self):
        guitar = np.ones((4, 1), dtype=np.float32)
        speakers = np.full((4, 2), 1.0, dtype=np.float32)
        route_monitor_audio(guitar, speakers, 0.0)
        np.testing.assert_array_equal(speakers, np.zeros((4, 2), dtype=np.float32))
        with self.assertRaisesRegex(ValueError, "Monitor gain"):
            route_monitor_audio(guitar, speakers, 1.1)

    def test_duplex_stream_uses_selected_devices_and_bounded_channels(self):
        class FakeSoundDevice:
            def __init__(self):
                self.stream_args = None

            def WasapiSettings(self, **kwargs):
                return kwargs

            def Stream(self, **kwargs):
                self.stream_args = kwargs
                return kwargs

        input_device = AudioInputDevice(2, "Real Tone Cable", "Windows WASAPI", 1, 48000.0)
        output_device = AudioOutputDevice(7, "Speakers", "Windows WASAPI", 6, 48000.0)
        capture = WaveformPitchInput(input_device, monitor_output=output_device, monitor_gain=0.25)
        sd = FakeSoundDevice()
        callback = lambda *_args: None
        result = capture._make_stream(sd, 48000, callback)
        self.assertIs(result, sd.stream_args)
        self.assertEqual(result["device"], (2, 7))
        self.assertEqual(result["channels"], (1, 2))
        self.assertEqual(result["dtype"], ("float32", "float32"))
        self.assertEqual(result["latency"], ("low", "low"))
        self.assertIs(result["callback"], callback)
        self.assertEqual(result["extra_settings"][0], {"exclusive": False, "auto_convert": True})
        self.assertEqual(result["extra_settings"][1], {"exclusive": False, "auto_convert": True})

    def test_tuner_only_input_keeps_existing_input_only_stream(self):
        class FakeSoundDevice:
            def __init__(self):
                self.stream_args = None

            def InputStream(self, **kwargs):
                self.stream_args = kwargs
                return kwargs

        input_device = AudioInputDevice(3, "Real Tone Cable", "Windows DirectSound", 1, 48000.0)
        capture = WaveformPitchInput(input_device)
        sd = FakeSoundDevice()
        callback = lambda *_args: None
        result = capture._make_stream(sd, 48000, callback)
        self.assertIs(result, sd.stream_args)
        self.assertEqual(result["device"], 3)
        self.assertEqual(result["channels"], 1)
        self.assertEqual(result["dtype"], "int16")
        self.assertEqual(result["blocksize"], capture.block_frames)
        self.assertEqual(result["latency"], "high")
        self.assertIs(result["callback"], callback)

    def test_output_enumeration_and_default_selection_are_mockable(self):
        class FakeDefault:
            device = (1, 4)

        class FakeSoundDevice:
            default = FakeDefault()

            @staticmethod
            def query_hostapis():
                return [{"name": "Windows WASAPI"}]

            @staticmethod
            def query_devices():
                return [
                    {"name": "Real Tone Cable", "hostapi": 0, "max_input_channels": 1, "max_output_channels": 0},
                    {"name": "Speakers", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2,
                     "default_samplerate": 44100.0},
                ]

        with patch("waveform_input._sounddevice_module", return_value=FakeSoundDevice()):
            outputs = enumerate_output_devices()
            default_id = preferred_output_device_id()
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0].label, "1: Windows WASAPI - Speakers")
        self.assertEqual(default_id, 4)

    def test_monitor_and_analysis_gain_ranges_are_bounded(self):
        for gain in (-0.1, 1.1, float("nan"), float("inf")):
            with self.subTest(gain=gain), self.assertRaisesRegex(ValueError, "Monitor gain"):
                WaveformPitchInput(0, monitor_gain=gain)
        for gain in (0.5, 8.1, float("nan"), float("inf")):
            with self.subTest(gain=gain), self.assertRaisesRegex(ValueError, "Tuner analysis gain"):
                WaveformPitchInput(0, analysis_gain=gain)


class PreferredCaptureTests(unittest.TestCase):
    def device(self, identifier, name="Microphone (5- Rocksmith GU", host="MME"):
        return AudioInputDevice(identifier, name, host, 1, 48000.0)

    def test_default_prefers_user_mme_guitar_not_first_mic(self):
        generic=self.device(0,"Laptop microphone")
        other=self.device(1,"Microphone (3- Rocksmith GU")
        wasapi=self.device(2,"Microphone (5- Rocksmith GU","Windows WASAPI")
        wanted=self.device(3)
        self.assertIs(preferred_guitar_device([generic,other,wasapi,wanted]),wanted)
        self.assertIsNone(preferred_guitar_device([generic]))

    def test_actual_usb_name_prefers_number_five_mme_cable(self):
        other=self.device(2,"Microphone (2- Rocksmith USB Gu")
        wanted=self.device(1,"Microphone (5- Rocksmith USB Gu")
        wasapi=self.device(34,"Microphone (5- Rocksmith USB Guitar Adapter)","Windows WASAPI")
        self.assertIs(preferred_guitar_device([other,wasapi,wanted]),wanted)
        self.assertIs(preferred_guitar_device([wanted,other],other.name,other.hostapi),other)

    def test_explicit_saved_identity_wins_and_device_ids_can_change(self):
        generic=self.device(20,"User selected microphone","Windows WASAPI")
        cable=self.device(21)
        self.assertIs(preferred_guitar_device([generic,cable],generic.name,generic.hostapi),generic)
        self.assertIs(preferred_guitar_device([cable],cable.name,"MME"),cable)

    def test_missing_mme_can_select_recognized_same_cable_other_backend(self):
        wasapi=self.device(7,"Microphone (5- Rocksmith GU","Windows WASAPI")
        self.assertIs(preferred_guitar_device([wasapi],wasapi.name,"MME"),wasapi)
        self.assertIsNone(preferred_guitar_device([self.device(8,"Laptop mic")],"Missing guitar","MME"))

    def test_wasapi_input_requests_shared_mode(self):
        sd=Mock()
        sd.WasapiSettings.side_effect=lambda **kwargs: kwargs
        capture=WaveformPitchInput(self.device(5,host="Windows WASAPI"))
        capture._make_stream(sd,48000,lambda *_:None)
        self.assertEqual(sd.InputStream.call_args.kwargs["extra_settings"], {"exclusive":False,"auto_convert":True})
        self.assertEqual(sd.InputStream.call_args.kwargs["device"],5)

    def test_mme_capture_never_requests_wasapi_or_exclusive_settings(self):
        sd=Mock()
        capture=WaveformPitchInput(self.device(5))
        capture._make_stream(sd,48000,lambda *_:None)
        sd.WasapiSettings.assert_not_called()
        self.assertIsNone(sd.InputStream.call_args.kwargs["extra_settings"])

    def test_mismatched_duplex_backends_fail_before_open(self):
        sd=Mock()
        capture=WaveformPitchInput(self.device(5),monitor_output=AudioOutputDevice(6,"Speakers","Windows WASAPI",2,48000))
        with self.assertRaisesRegex(AudioInputError,"same Windows audio backend"):
            capture._make_stream(sd,48000,lambda *_:None)
        sd.Stream.assert_not_called()

    def test_busy_or_privacy_failure_stops_retries_and_preserves_selected_device(self):
        for failure in [RuntimeError("Device unavailable -9985"),PermissionError("Access denied by microphone privacy")]:
            capture=WaveformPitchInput(self.device(9))
            with patch("waveform_input._sounddevice_module",return_value=Mock()), patch.object(capture,"_capture_rate",side_effect=failure) as attempt, patch("waveform_input.logging.exception"):
                capture._run()
            self.assertEqual(attempt.call_count,1)
            self.assertEqual(capture.device.device_id,9)
            self.assertEqual(capture.status,"Input error")
            self.assertIn("Microphone",capture.error)
            self.assertIn("does not change devices automatically",capture.error)
            self.assertIn("Privacy",capture.error)

    def test_format_retries_are_bounded_to_four_rates_on_same_device(self):
        capture=WaveformPitchInput(AudioInputDevice(9,"Guitar","MME",1,32000),monitor_output=AudioOutputDevice(10,"Speakers","MME",2,96000))
        with patch("waveform_input._sounddevice_module",return_value=Mock()), patch.object(capture,"_capture_rate",side_effect=RuntimeError("Invalid sample rate")) as attempt, patch("waveform_input.logging.exception"):
            capture._run()
        self.assertEqual([call.args[1] for call in attempt.call_args_list],[48000,44100,32000,96000])
        self.assertEqual(capture.device.device_id,9)

    def test_failed_stream_start_closes_owned_handle(self):
        capture=WaveformPitchInput(self.device(9))
        stream=Mock()
        stream.closed=False
        stream.__enter__=Mock(side_effect=RuntimeError("start failed"))
        stream.__exit__=Mock(return_value=False)
        with patch.object(capture,"_make_stream",return_value=stream):
            with self.assertRaisesRegex(RuntimeError,"start failed"):
                capture._capture_rate(Mock(),48000)
        stream.close.assert_called_once_with()
        stream.__exit__.assert_not_called()

    def test_successful_context_closes_once_and_does_not_touch_global_streams(self):
        capture=WaveformPitchInput(self.device(9))
        class Stream:
            closed=False
            closes=0
            def __enter__(self):
                capture.stop()
                return self
            def __exit__(self,*args):
                self.close()
            def close(self):
                self.closes+=1
                self.closed=True
        stream=Stream()
        sd=Mock()
        with patch.object(capture,"_make_stream",return_value=stream):
            capture._capture_rate(sd,48000)
        self.assertEqual(stream.closes,1)
        sd.stop.assert_not_called()

    def test_start_is_idempotent_while_worker_owns_stream(self):
        capture=WaveformPitchInput(self.device(9))
        thread=Mock()
        thread.is_alive.return_value=True
        with patch("waveform_input.threading.Thread",return_value=thread) as factory:
            capture.start()
            capture.start()
        self.assertEqual(factory.call_count,1)
        thread.start.assert_called_once()
        self.assertTrue(capture.is_alive)
        thread.is_alive.return_value=False
        self.assertFalse(capture.is_alive)


    def test_no_audio_frames_reports_failure_and_releases_stream(self):
        import queue
        capture=WaveformPitchInput(self.device(9))
        stream=Mock()
        stream.closed=False
        stream.__enter__=Mock(return_value=stream)
        stream.__exit__=Mock(return_value=False)
        with patch.object(capture,"_make_stream",return_value=stream), patch.object(capture._blocks,"get",side_effect=queue.Empty), patch("waveform_input.time.monotonic",side_effect=[0.0,4.0]):
            with self.assertRaisesRegex(AudioInputError,"no audio frames"):
                capture._capture_rate(Mock(),48000)
        stream.close.assert_called_once_with()

    def test_cleanup_failure_requires_explicit_restart(self):
        capture=WaveformPitchInput(self.device(9))
        stream=Mock()
        stream.closed=False
        stream.__enter__=Mock(side_effect=RuntimeError("start failed"))
        stream.__exit__=Mock(return_value=False)
        stream.close.side_effect=OSError("driver refused close")
        with patch.object(capture,"_make_stream",return_value=stream):
            with self.assertRaisesRegex(AudioInputError,"Could not release"):
                capture._capture_rate(Mock(),48000)
        stream.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
