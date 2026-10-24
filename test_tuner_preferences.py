"""Automatic selection and persistence tests; never open an audio device."""
import json
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from intonation_checker import IntonationCheckerWindow
from tuner_settings import default_settings, load_tuner_settings, save_tuner_settings
from waveform_input import AudioInputDevice

class Value:
    def __init__(self,value=""):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value

class TunerPreferenceTests(unittest.TestCase):
    def setUp(self):
        self.cable=AudioInputDevice(1,"Microphone (5- Rocksmith USB Gu","MME",1,48000)
        self.mic=AudioInputDevice(3,"Laptop microphone","MME",1,48000)
    def checker(self):
        return SimpleNamespace(closed=False,settings=default_settings(),devices=[],device_map={},
            device_var=Value(),device_combo=Mock(),input_status_var=Value(),preference_status_var=Value(),
            capture=None,paused=False,_audio_active=lambda:False,start_tuning=Mock(),
            _pending_input_label='',_clear_live_reading=Mock())
    def test_default_cable_automatically_starts_without_a_start_click(self):
        checker=self.checker()
        with patch('intonation_checker.enumerate_input_devices',return_value=[self.mic,self.cable]):
            IntonationCheckerWindow.refresh_devices(checker)
        self.assertEqual(checker.device_var.get(),self.cable.label)
        checker.start_tuning.assert_called_once()
        self.assertNotIn('not listed',checker.preference_status_var.get())
    def test_unrelated_microphone_never_autostarts_without_explicit_preference(self):
        checker=self.checker()
        with patch('intonation_checker.enumerate_input_devices',return_value=[self.mic]):
            IntonationCheckerWindow.refresh_devices(checker)
        self.assertEqual(checker.device_var.get(),'')
        checker.start_tuning.assert_not_called()
        self.assertIn('not listed',checker.input_status_var.get())
    def test_explicit_saved_device_survives_id_change_and_can_be_a_selected_mic(self):
        checker=self.checker();checker.settings.update(device_name=self.mic.name,device_hostapi=self.mic.hostapi)
        with patch('intonation_checker.enumerate_input_devices',return_value=[self.cable,self.mic]):
            IntonationCheckerWindow.refresh_devices(checker)
        self.assertEqual(checker.device_var.get(),self.mic.label)
        checker.start_tuning.assert_called_once()
    def test_stop_prevents_discovery_from_restarting_audio(self):
        checker=self.checker();checker.paused=True
        with patch('intonation_checker.enumerate_input_devices',return_value=[self.cable]):
            IntonationCheckerWindow.refresh_devices(checker)
        checker.start_tuning.assert_not_called()
    def test_unfinished_old_capture_prevents_a_second_stream(self):
        from intonation_analysis import ManualIntonationSession
        checker=self.checker();checker.capture=Mock();checker.capture.is_alive=True;checker.capture.device=self.cable
        checker.poll_id=None;checker.flow=ManualIntonationSession();checker.window=Mock()
        with patch('intonation_checker.WaveformPitchInput') as capture:
            IntonationCheckerWindow.start_tuning(checker)
        capture.assert_not_called()
        self.assertIn('no second',checker.input_status_var.get())

    def test_tuner_can_open_with_rocksmith_running_without_a_game_closed_gate(self):
        from app import App
        app=SimpleNamespace(_operation_busy=False,intonation_window=None)
        with patch('app.game_is_running',return_value=True) as game_check,patch('app.IntonationCheckerWindow') as create:
            App._open_intonation_checker(app)
        create.assert_called_once_with(app)
        game_check.assert_not_called()

    def test_preferences_roundtrip_in_another_users_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'AnotherWindowsUser/Documents/JonsRockSmithMods/tuner_settings.json'
            settings=default_settings();settings.update(device_name=self.cable.name,device_hostapi='MME')
            settings['reference_offsets']={'E Standard':{'6':-20,'5':-18.5}}
            save_tuner_settings(settings,path)
            actual=load_tuner_settings(path)
            self.assertEqual(actual['device_name'],self.cable.name)
            self.assertEqual(actual['version'],2)
            self.assertNotIn('reference_offsets',actual)
            self.assertNotIn('reference_offsets',json.loads(path.read_text(encoding='utf-8')))
            self.assertEqual(list(path.parent.glob('.tuner-*')),[])

    def test_legacy_personal_offsets_are_ignored_when_loading_v1_preferences(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'tuner_settings.json'
            path.write_text(json.dumps({
                'version':1,'device_name':self.cable.name,'device_hostapi':'MME',
                'tuning':'E Standard','reference_offsets':{'E Standard':{'6':-20}},
            }),encoding='utf-8')
            loaded=load_tuner_settings(path)
            self.assertEqual(loaded['device_name'],self.cable.name)
            self.assertEqual(loaded['tuning'],'E Standard')
            self.assertNotIn('reference_offsets',loaded)
    def test_bad_or_oversized_settings_are_visible_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'tuner_settings.json';path.write_text('{bad')
            with self.assertRaises(ValueError):load_tuner_settings(path)
            path.write_text('x'*65537)
            with self.assertRaisesRegex(ValueError,'oversized'):load_tuner_settings(path)

if __name__=='__main__':unittest.main()
