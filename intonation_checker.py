"""Explicit open-string tuning and fretted-12th intonation with shared live input."""
from __future__ import annotations
import logging
import math
import time
import tkinter as tk
from tkinter import messagebox, ttk
from intonation_analysis import DEFAULT_TUNING, TUNING_PRESETS, ManualIntonationSession, PitchEstimate, cents_difference, cents_position, frequency_for_note, note_for_frequency, pitch_band, pitch_state, tuner_adjustment, tuning_reference_rows, TUNER_COLOR_AMBER, TUNER_COLOR_GREEN, TUNER_COLOR_NEUTRAL, TUNER_COLOR_RED
from tuner_settings import default_settings, load_tuner_settings, save_tuner_settings
from ui_layout import ScrollablePage
from waveform_input import AudioInputDevice, WaveformPitchInput, enumerate_input_devices, preferred_guitar_device
STRING_LABELS = ('String 6 - lowest', 'String 5', 'String 4', 'String 3', 'String 2', 'String 1 - highest')
TUNING_LABEL_TO_NAME = {f"{name} ({' '.join(notes)})": name for name, notes in TUNING_PRESETS.items()}
TUNING_CHOICES = tuple(TUNING_LABEL_TO_NAME)
DEFAULT_TUNING_LABEL = next((label for label, name in TUNING_LABEL_TO_NAME.items() if name == DEFAULT_TUNING))

def tuner_window_size(screen_width, screen_height):
    return (min(880, max(360, int(screen_width) - 60)), min(800, max(300, int(screen_height) - 80)))

def guided_capture_sequence():
    """Legacy sequence description; the current UI never advances automatically."""
    return tuple(((number, stage) for number in range(6, 0, -1) for stage in ('open', 'fret12')))

class IntonationCheckerWindow:

    def __init__(self, master):
        self.master = master
        self.window = tk.Toplevel(master)
        self.window.title('JonsRockSmithMods - Open tuning / 12th-fret intonation')
        width, height = tuner_window_size(self.window.winfo_screenwidth(), self.window.winfo_screenheight())
        self.window.geometry(f'{width}x{height}')
        self.window.minsize(min(width, 420), min(height, 360))
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.closed = False
        self.paused = False
        self.poll_id = None
        self.discovery_id = None
        self.startup_id = None
        self.capture = None
        self.device_map = {}
        self.devices = []
        self.flow = ManualIntonationSession()
        self.current_pitch = None
        self.last_update = None
        self.last_observed_timestamp = None
        self._pending_input_label = ''
        self.current_live_message = 'PLAY A NOTE'
        self.settings_error = ''
        try:
            self.settings = load_tuner_settings()
        except Exception as exc:
            logging.exception('Could not load tuner preferences')
            self.settings = default_settings()
            self.settings_error = f'Preferences could not load: {exc}'
        preset = self.settings['tuning']
        self.device_var = tk.StringVar(value='')
        self.tuning_var = tk.StringVar(value=next((label for label, name in TUNING_LABEL_TO_NAME.items() if name == preset)))
        self.string_var = tk.StringVar(value=STRING_LABELS[0])
        self.stage_var = tk.StringVar(value='open')
        self.input_status_var = tk.StringVar(value='Finding guitar cable; audio stays in memory and is never recorded.')
        self.instruction_var = tk.StringVar(value='')
        self.live_detail_var = tk.StringVar(value='Choose the string and OPEN or FRET 12. The app never switches them automatically.')
        self.preference_status_var = tk.StringVar(value=self.settings_error)
        self.reference_table = None
        self._close_wait_id = None
        self._close_wait_started = None
        self._close_callbacks = []
        self.closing = False
        self._build()
        self._update_instruction()
        self.startup_id = self.window.after(100, self.refresh_devices)
        self.discovery_id = self.window.after(5000, self._discover_missing_cable)

    def _build(self):
        host = ttk.Frame(self.window)
        host.pack(fill='both', expand=True)
        host.columnconfigure(0, weight=1)
        host.rowconfigure(0, weight=1)
        self.scroll_page = ScrollablePage(host)
        self.scroll_page.grid(row=0, column=0, sticky='nsew')
        root = self.scroll_page.content
        root.columnconfigure(0, weight=1)
        ttk.Label(root, text='Choose a string and stage. OPEN and FRET 12 use the selected tuning chart’s standard A440 frequencies.', wraplength=780, font=('Segoe UI', 11, 'bold')).grid(row=0, column=0, sticky='ew', pady=(0, 10))
        setup = ttk.LabelFrame(root, text='Input and selected string', padding=10)
        setup.grid(row=1, column=0, sticky='ew')
        setup.columnconfigure(1, weight=1)
        ttk.Label(setup, text='Guitar input').grid(row=0, column=0, sticky='w')
        self.device_combo = ttk.Combobox(setup, textvariable=self.device_var, state='readonly')
        self.device_combo.grid(row=0, column=1, sticky='ew', padx=8)
        self.device_combo.bind('<<ComboboxSelected>>', self._device_selected)
        self.refresh_button = ttk.Button(setup, text='Refresh inputs', command=self.refresh_devices)
        self.refresh_button.grid(row=0, column=2)
        ttk.Label(setup, text='Tuning').grid(row=1, column=0, sticky='w', pady=6)
        self.tuning_combo = ttk.Combobox(setup, textvariable=self.tuning_var, values=TUNING_CHOICES, state='readonly')
        self.tuning_combo.grid(row=1, column=1, columnspan=2, sticky='ew', padx=8)
        self.tuning_combo.bind('<<ComboboxSelected>>', lambda _event: self._on_tuning_changed())
        ttk.Label(setup, text='String to check').grid(row=2, column=0, sticky='w')
        self.string_combo = ttk.Combobox(setup, textvariable=self.string_var, values=STRING_LABELS, state='readonly')
        self.string_combo.grid(row=2, column=1, columnspan=2, sticky='ew', padx=8)
        self.string_combo.bind('<<ComboboxSelected>>', lambda _event: self._selection_changed())
        stages = ttk.Frame(setup)
        stages.grid(row=3, column=0, columnspan=3, sticky='ew', pady=8)
        ttk.Radiobutton(stages, text='OPEN string', value='open', variable=self.stage_var, command=self._selection_changed).pack(side='left', padx=(0, 16))
        ttk.Radiobutton(stages, text='FRET 12 (press the string)', value='fret12', variable=self.stage_var, command=self._selection_changed).pack(side='left')
        chart = ttk.LabelFrame(root, text='Nominal A440 reference frequencies for all strings in selected tuning', padding=8)
        chart.grid(row=2, column=0, sticky='ew', pady=(0, 8))
        chart.columnconfigure(0, weight=1)
        self.reference_table = ttk.Treeview(
            chart, columns=('string', 'open', 'open_hz', 'fret12_hz'),
            show='headings', height=6
        )
        self.reference_table.bind('<<TreeviewSelect>>', self._reference_row_selected, add=True)
        for key, title, width in (
            ('string', 'String', 130), ('open', 'Open note', 110),
            ('open_hz', 'Open (Hz)', 130), ('fret12_hz', '12th fret (Hz)', 150),
        ):
            self.reference_table.heading(key, text=title)
            self.reference_table.column(key, width=width, anchor='center', stretch=True)
        self.reference_table.grid(row=0, column=0, sticky='ew')
        self._refresh_reference_table()
        ttk.Label(chart, text='OPEN and FRET 12 live targets match these A440 chart values. Tune OPEN first; FRET 12 must be within 5 cents of its listed target.',
                  wraplength=760).grid(row=1, column=0, sticky='w', pady=(4, 0))
        live = ttk.LabelFrame(root, text='Live reading - selected stage stays fixed', padding=10)
        live.grid(row=3, column=0, sticky='ew')
        self.tuner_canvas = tk.Canvas(live, height=285, background='#101923', highlightthickness=0, borderwidth=0)
        self.tuner_canvas.pack(fill='x', expand=True)
        self.tuner_canvas.bind('<Configure>', self._draw_tuner)
        ttk.Label(live, textvariable=self.live_detail_var, wraplength=760).pack(anchor='w', pady=6)
        for row, variable in ((4, self.instruction_var), (5, self.input_status_var), (6, self.preference_status_var)):
            ttk.Label(root, textvariable=variable, wraplength=760, padding=(0, 4)).grid(row=row, column=0, sticky='ew')
        self.footer_frame = ttk.Frame(host, padding=(12, 7))
        self.footer_frame.grid(row=1, column=0, sticky='ew')
        self.footer_frame.columnconfigure(0, weight=1)
        buttons = ttk.Frame(self.footer_frame)
        buttons.grid(row=0, column=0, sticky='ew')
        self.start_button = ttk.Button(buttons, text='Retry / resume audio', command=self.start_tuning)
        self.start_button.grid(row=0, column=0, sticky='w', padx=(0, 8))
        self.stop_button = ttk.Button(buttons, text='Stop listening', command=self.stop_listening)
        self.stop_button.grid(row=0, column=1, sticky='w')
        self.return_button = ttk.Button(buttons, text='Return to main', command=self.close)
        self.return_button.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(6, 0))

    def _tuning_name(self):
        return TUNING_LABEL_TO_NAME.get(self.tuning_var.get(), DEFAULT_TUNING)

    def _tuning_target(self, number):
        note = TUNING_PRESETS[self._tuning_name()][6 - number]
        return (note, frequency_for_note(note))

    def _refresh_reference_table(self):
        if self.reference_table is None:
            return
        for item in self.reference_table.get_children(''):
            self.reference_table.delete(item)
        for number, note, open_hz, fret12_hz in tuning_reference_rows(self._tuning_name()):
            item = f'string-{number}'
            self.reference_table.insert('', 'end', iid=item, values=(
                f'String {number}', note, f'{open_hz:.2f}', f'{fret12_hz:.2f}'
            ))
        self._select_reference_row(getattr(getattr(self, 'flow', None), 'string_number', 6))

    def _select_reference_row(self, number):
        if self.reference_table is None:
            return
        item = f'string-{number}'
        if item not in self.reference_table.get_children(''):
            return
        if self.reference_table.selection() != (item,):
            self.reference_table.selection_set(item)
        self.reference_table.focus(item)
        self.reference_table.see(item)

    def _reference_row_selected(self, _event=None):
        if self.reference_table is None:
            return
        selection = self.reference_table.selection()
        if not selection:
            return
        try:
            number = int(selection[0].removeprefix('string-'))
        except ValueError:
            return
        self.string_var.set(STRING_LABELS[6 - number])
        self._selection_changed()

    def _save_settings(self):
        try:
            save_tuner_settings(self.settings)
            self.preference_status_var.set('Cable identity and tuning selection saved for this Windows user.')
        except Exception as exc:
            logging.exception('Could not save tuner preferences')
            self.preference_status_var.set(f'Preferences NOT saved: {exc}')

    def _on_tuning_changed(self):
        self.flow.reset()
        self.settings['tuning'] = self._tuning_name()
        self._refresh_reference_table()
        self._save_settings()
        self._selection_changed()

    def _selection_changed(self):
        number = 6 - STRING_LABELS.index(self.string_var.get())
        self.flow.select(number, self.stage_var.get())
        self._select_reference_row(number)
        self._clear_live_reading('PLAY A NOTE: selected string/stage changed; wait for fresh stable readings.')
        self._retarget_input()
        self._update_instruction()

    def _clear_live_reading(self, message):
        self.current_pitch = None
        self.last_update = None
        self.last_observed_timestamp = None
        self.current_live_message = message
        self.live_detail_var.set(message)
        self._draw_tuner()

    def _display_target(self):
        return self.flow.target_frequency(self._tuning_target(self.flow.string_number)[1])

    def _retarget_input(self):
        """Guide detection with a preset octave if needed; comparisons still require captured open."""
        if self.capture is not None:
            target = self._display_target()
            if target is None:
                target = self._tuning_target(self.flow.string_number)[1] * 2.0
            self.capture.set_expected_frequency(target)

    def refresh_devices(self):
        if self.closed:
            return
        try:
            devices = enumerate_input_devices()
            self.devices = devices
            self.device_map = {device.label: device for device in devices}
            self.device_combo.configure(values=tuple(self.device_map))
            current = self.device_map.get(self.device_var.get())
            preferred = next((d for d in devices if d.name == self.settings['device_name'] and d.hostapi == self.settings['device_hostapi']), None)
            chosen = current or preferred or preferred_guitar_device(devices)
            if chosen is None:
                if self.capture is not None:
                    self.capture.stop()
                self.device_var.set('')
                self._clear_live_reading('INPUT UNAVAILABLE: reconnect the guitar cable.')
                self.input_status_var.set('Rocksmith guitar cable not listed. Reconnect it and check Windows microphone permission; no unrelated microphone was selected.')
                return
            self.device_var.set(chosen.label)
            if self.settings['device_name'] and preferred is None:
                self.preference_status_var.set('Saved input is not listed. Using another recognized Rocksmith cable; select explicitly if needed.')
            elif not any((d.hostapi.casefold() == 'mme' and 'rocksmith gu' in d.name.casefold().replace('rocksmith usb gu', 'rocksmith gu') and ('5-' in d.name) for d in devices)):
                self.preference_status_var.set('Preferred MME Microphone (5- Rocksmith GU...) is not listed. Check/reconnect that cable if this alternate does not work.')
            changed_input = self.capture is not None and self.capture.device != chosen
            if changed_input:
                self.flow.reset()
                self._clear_live_reading('Input changed; capture a fresh open reference.')
            if (changed_input or not self._audio_active()) and (not self.paused):
                self.start_tuning()
        except Exception as exc:
            logging.exception('Could not enumerate guitar cable')
            self.input_status_var.set(f'Input discovery failed: {exc}')

    def _device_selected(self, _event=None):
        device = self.device_map.get(self.device_var.get())
        if device is None:
            return
        self.settings['device_name'] = device.name
        self.settings['device_hostapi'] = device.hostapi
        self._save_settings()
        self.flow.reset()
        self._pending_input_label = ''
        self.paused = False
        self.start_tuning()

    def _discover_missing_cable(self):
        self.discovery_id = None
        if self.closed:
            return
        if not self.paused and (not self.device_var.get()):
            self.refresh_devices()
        self.discovery_id = self.window.after(5000, self._discover_missing_cable)

    def _audio_active(self):
        return self.capture is not None and self.capture.status not in ('Stopped', 'Input error')

    def start_tuning(self):
        if self.closed:
            return
        self.paused = False
        device = self.device_map.get(self._pending_input_label) or self.device_map.get(self.device_var.get())
        if self.capture is not None:
            self.capture.stop()
            self.capture.join(timeout=1.5)
            if self.capture.is_alive:
                self._pending_input_label = device.label if device is not None else ''
                self.device_var.set(self.capture.device.label)
                self._clear_live_reading('Previous input is still stopping; no new reading is available.')
                self.input_status_var.set('Previous input is still stopping. Wait, then retry; no second audio stream was opened.')
                return
        if self.poll_id is not None:
            self.window.after_cancel(self.poll_id)
            self.poll_id = None
        if device is None:
            self._clear_live_reading('INPUT UNAVAILABLE: select a detected guitar cable.')
            self.input_status_var.set('Select a detected guitar cable first.')
            return
        target = self._display_target() or self._tuning_target(self.flow.string_number)[1] * 2.0
        self.flow.select(self.flow.string_number, self.flow.stage)
        self._clear_live_reading('Opening input; wait for three fresh stable readings.')
        self._pending_input_label = ''
        self.device_var.set(device.label)
        self.capture = WaveformPitchInput(device, expected_frequency_hz=target)
        logging.info('Tuner input start: name=%s hostapi=%s string=%s stage=%s target=%s', device.name, device.hostapi, self.flow.string_number, self.flow.stage, target)
        self.capture.start()
        self.input_status_var.set('Opening selected input in shared mode; Rocksmith may remain running.')
        self._poll_input()

    def stop_listening(self):
        self.paused = True
        if self.capture is not None:
            self.capture.stop()
        self.flow.select(self.flow.string_number, self.flow.stage)
        self._clear_live_reading('LISTENING PAUSED')
        self.input_status_var.set('Listening paused. Retry / resume starts the selected cable again.')
        self._draw_tuner()

    def _poll_input(self):
        self.poll_id = None
        if self.closed or self.capture is None:
            return
        capture = self.capture
        status = capture.status
        if capture.error or status == 'Input error':
            self._clear_live_reading('INPUT UNAVAILABLE')
            self.input_status_var.set(capture.error or 'Input unavailable; refresh devices or retry.')
        elif status == 'Stopped':
            self._clear_live_reading('LISTENING PAUSED' if self.paused else 'INPUT STOPPED')
            if not self.paused:
                self.input_status_var.set('Input stopped. Refresh the cable or retry audio.')
        else:
            self.input_status_var.set(status)
            latest = capture.latest
            if not self.paused and latest is not None and (latest.timestamp + 0.45 >= time.monotonic()):
                if self.last_observed_timestamp is None or latest.timestamp > self.last_observed_timestamp:
                    self.current_pitch = latest
                    self.last_observed_timestamp = latest.timestamp
                    self.last_update = self.flow.observe(latest, self._tuning_target(self.flow.string_number)[1])
                    self._update_live_detail(self.last_update)
            else:
                self._clear_live_reading('LISTENING PAUSED' if self.paused else 'PLAY ONE STRING: wait for fresh stable readings.')
            self._update_instruction()
        self._draw_tuner()
        if status not in ('Stopped', 'Input error'):
            self.poll_id = self.window.after(100, self._poll_input)

    def _update_instruction(self):
        number, stage = self.flow.current_step
        note, nominal = self._tuning_target(number)
        if stage == 'open':
            captured = self.flow.measurements[number].get('open')
            state = f'Captured {captured:.2f} Hz; choose FRET 12 when ready.' if captured else 'Pluck open and settle within +/-5 cents to confirm tuning.'
            text = f'SELECTED: String {number} OPEN - {note}, target {nominal:.2f} Hz. {state} Stage never changes automatically.'
        else:
            target = self._display_target()
            text = f'SELECTED: String {number} FRET 12 - target {target:.2f} Hz from the A440 tuning chart. Press at fret 12 with normal playing pressure. If sharp, lengthen the saddle speaking length; if flat, shorten it. Retune OPEN after adjustment and recheck.' if target else f"SELECTED: String {number} FRET 12. Tune and confirm this string's OPEN pitch first."
        self.instruction_var.set(text)

    def _update_live_detail(self, update):
        messages = {'wrong_octave': 'Wrong octave detected. Check chosen string and OPEN / FRET 12; do not retune by an octave.', 'out_of_range': 'Pitch outside this string/stage range. Check selection; no adjustment cue.', 'missing_open_reference': 'Choose OPEN and capture a stable reference first.', 'invalid_pitch': 'Pitch unclear. Pluck one string cleanly and let it settle.', 'clipped': 'Input clipping. Lower Windows input level and pluck again.', 'unstable': 'Pitch moving. Let the note settle before comparing.', 'waiting': 'Waiting for three fresh, stable readings.'}
        if update.status in messages:
            self.current_live_message = messages[update.status]
            self.live_detail_var.set(messages[update.status])
            return
        cents = update.cents_from_target
        _, action = tuner_adjustment(cents, 5)
        if self.flow.stage == 'open':
            self.live_detail_var.set(f'Open pitch: {update.measured_frequency_hz:.2f} Hz vs {update.target_frequency_hz:.2f} Hz A440 target; {cents:+.1f} cents. {action}. ' + ('OPEN confirmed; manually choose FRET 12.' if update.status == 'open_tuned' else 'Adjust the open-string tuning.'))
        else:
            guidance = '12th fret matches the A440 target.' if abs(cents) <= 5 else 'Lengthen the saddle speaking length, then retune open.' if cents > 0 else 'Shorten the saddle speaking length, then retune open.'
            self.live_detail_var.set(f'12th fret: {update.measured_frequency_hz:.2f} Hz vs {update.target_frequency_hz:.2f} Hz target; {cents:+.1f} cents. {action}. {guidance}')

    def _live_comparison(self):
        number, stage = self.flow.current_step
        target = self._display_target()
        pitch = self.current_pitch
        cents = None
        state = 'WAITING'
        band = 'neutral'
        update = self.last_update
        if pitch is not None and target is not None:
            raw = cents_difference(pitch.frequency_hz, target)
            accepted = update is not None and update.status in ('tune_open', 'open_tuned', 'tune_fret12', 'fret12_measured')
            if accepted and update.cents_from_target is not None and not pitch.clipped and pitch.confidence >= 0.65 and (abs(raw) <= 100):
                cents = update.cents_from_target
                state = pitch_state(cents, 5)
                band = pitch_band(cents, 5)
            elif pitch.clipped or pitch.confidence < 0.65 or abs(raw) > 100:
                state = 'CHECK STRING / STAGE'
            else:
                state = 'WAIT FOR STABLE NOTE'
        elif target is None:
            state = 'CAPTURE OPEN FIRST'
        return {'pitch': pitch, 'measured': update.measured_frequency_hz if cents is not None else None, 'note': pitch.note_name if pitch else self.current_live_message, 'cents': cents, 'target': target, 'string': STRING_LABELS[6 - number], 'stage': stage, 'state': state, 'band': band}

    def _draw_tuner(self, _event=None):
        canvas = self.tuner_canvas
        if not canvas.winfo_exists():
            return
        canvas.delete('all')
        width = max(260, canvas.winfo_width())
        reading = self._live_comparison()
        colors = {'green': TUNER_COLOR_GREEN, 'amber': TUNER_COLOR_AMBER, 'red': TUNER_COLOR_RED, 'neutral': TUNER_COLOR_NEUTRAL}
        color = colors[reading['band']]
        center = width / 2
        stage = 'OPEN STRING' if reading['stage'] == 'open' else 'FRETTED AT 12'
        canvas.create_text(14, 18, anchor='w', text=f"{reading['string']} | {stage}", fill='#d7e3ef', font=('Segoe UI', 12, 'bold'))
        note = reading['pitch'].note_name if reading['pitch'] else 'PLUCK SELECTED STRING'
        canvas.create_text(center, 60, text=note, fill=color, font=('Segoe UI', 28, 'bold'))
        canvas.create_text(center, 99, text=reading['state'], fill=color, font=('Segoe UI', 12, 'bold'))
        if reading['cents'] is not None:
            canvas.create_text(center, 124, text=f"{reading['measured']:.2f} Hz actual | {reading['target']:.2f} Hz target | {reading['cents']:+.1f} cents", fill='#f3f6fa', font=('Segoe UI', 10))
        arrow, action = tuner_adjustment(reading['cents'], 5)
        scale_left, scale_right = max(24, width * .12), min(width - 24, width * .88)
        scale_width = max(1, scale_right - scale_left)
        zones = ((-50, -15, TUNER_COLOR_RED), (-15, -5, TUNER_COLOR_AMBER),
                 (-5, 5, TUNER_COLOR_GREEN), (5, 15, TUNER_COLOR_AMBER),
                 (15, 50, TUNER_COLOR_RED))
        for low, high, zone_color in zones:
            left = scale_left + (low + 50) / 100 * scale_width
            right = scale_left + (high + 50) / 100 * scale_width
            canvas.create_rectangle(left, 137, right, 153, fill=zone_color, outline='')
        canvas.create_line(center, 132, center, 158, fill='#f3f6fa', width=2)
        if reading['cents'] is not None:
            meter_x = scale_left + (max(-50, min(50, reading['cents'])) + 50) / 100 * scale_width
            canvas.create_polygon(meter_x - 6, 130, meter_x + 6, 130, meter_x, 137,
                                  fill=color, outline='#f3f6fa')
        canvas.create_text(scale_left, 164, anchor='w', text='<< FLAT', fill=TUNER_COLOR_RED, font=('Segoe UI', 9, 'bold'))
        canvas.create_text(center, 164, text='IN TUNE', fill=TUNER_COLOR_GREEN, font=('Segoe UI', 9, 'bold'))
        canvas.create_text(scale_right, 164, anchor='e', text='SHARP >>', fill=TUNER_COLOR_RED, font=('Segoe UI', 9, 'bold'))
        canvas.create_text(center, 202, text=arrow, fill=color, font=('Segoe UI', 38, 'bold'))
        canvas.create_text(center, 241, text=action, fill=color, font=('Segoe UI', 12, 'bold'))
        if reading['target'] is not None:
            canvas.create_text(center, 271, text=f"Target {reading['target']:.2f} Hz  |  < lower     > higher", fill='#b7c6d7', font=('Segoe UI', 10))

    def close(self):
        if self.closed or self.closing:
            return
        self.closing = True
        self._close_wait_started = time.monotonic()
        for token in (self.poll_id, self.discovery_id, getattr(self, "startup_id", None)):
            if token is not None:
                try:
                    self.window.after_cancel(token)
                except tk.TclError:
                    pass
        self.poll_id = None
        self.discovery_id = None
        if self.capture is not None:
            try:
                self.capture.stop()
            except Exception:
                logging.exception('Could not stop tuner capture on close')
                self.closing = False
                return
            self.input_status_var.set('Closing tuner: waiting for the guitar input to release...')
            self._wait_for_audio_release()
            return
        self._finish_close()

    def close_and_then(self, callback):
        if callback is not None:
            if self.closed:
                callback()
                return True
            self._close_callbacks.append(callback)
        self.close()
        return self.closed

    def _wait_for_audio_release(self):
        self._close_wait_id = None
        if self.closed:
            return
        if self.capture is not None:
            try:
                self.capture.join(timeout=0)
                still_running = self.capture.is_alive
            except Exception:
                logging.exception('Could not confirm tuner audio release')
                still_running = True
            if still_running:
                elapsed = time.monotonic() - self._close_wait_started
                self.input_status_var.set(
                    'Closing tuner: waiting for the guitar input to release...'
                    if elapsed < 10 else
                    'Audio release is taking longer than expected. The tuner will stay open until the input thread stops.'
                )
                self._close_wait_id = self.window.after(100 if elapsed < 10 else 1000, self._wait_for_audio_release)
                return
        self._finish_close()

    def _finish_close(self):
        if self.closed:
            return
        self.closed = True
        self.closing = False
        try:
            if self.window.winfo_exists():
                self.window.destroy()
        except tk.TclError:
            pass
        callbacks, self._close_callbacks = self._close_callbacks, []
        for callback in callbacks:
            try:
                callback()
            except Exception:
                logging.exception('Tuner close callback failed')
