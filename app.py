"""JonsRockSmithMods Windows desktop app."""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import Menu, filedialog, messagebox, simpledialog, ttk

from archive_details import (
    archive_search_text, archive_table_values, archive_track_rows, duplicate_song_keys,
    format_archive_move_line, metadata_song_records,
)
from audio_preview import PreviewError, cleanup_preview_directory, prepare_preview
from favorites import (
    FavoriteUpdateResult,
    PROFILE_BACKUP_DIRECTORY,
    clear_all_song_favorites,
    read_difficulty_settings,
    set_difficulty_settings,
    set_all_song_difficulty,
    set_song_difficulty,
    SongDifficultyUpdateResult,
    set_song_favorites,
    song_keys_from_metadata,
)
from game_song_requests import (
    InGameSongRequest,
    REQUEST_DIRECTORY as IN_GAME_REQUEST_DIRECTORY,
    discard_game_song_request,
    load_pending_game_song_requests,
    resolve_active_archive_for_song_key,
)
from game_tuning_settings import (
    DEFAULT_PITCH_REFERENCE,
    DEFAULT_SKIP_FIRST_TUNER,
    PITCH_REFERENCE_A440,
    PITCH_REFERENCE_ROCKSMITH,
    read_game_tuning_settings,
    write_game_tuning_settings,
)
from difficulty_batch import BulkDifficultyResult, collect_global_difficulty
from intonation_checker import IntonationCheckerWindow
from library_refresh import (
    LibraryRefreshResult,
    library_signature,
    profile_file_signature,
    refresh_library,
    validate_move_snapshot,
)
from metadata.metadata import find_toolkit_path, extract_difficulty_metadata
from metadata_cache import MetadataCache
from native_install import install as install_native, restore as restore_native, support_status
from pending_moves import (
    PendingMoveResult,
    cancel_pending_moves,
    load_pending_moves,
    load_last_completed_moves,
    process_pending_moves,
    queue_song_moves,
    undo_last_completed_moves,
)
from song_store import SongFile, SongStore, StoreError, game_is_running, normalize_review_root
from song_table import context_actions, context_selection, next_sort_state, removal_row_status, song_sort_key
from split_pack import (
    SplitPackError, selected_song_payload, split_and_install_pack,
    split_pack_unavailable_reason, update_visible_song_selection,
    resolve_rocksmith_dlc_directory, validate_split_metadata,
)
from speed_config import (
    DEFAULT_SPEED_PERCENT, read_speed_config, write_speed_config,
    read_song_speed_config, write_song_speed_config, write_song_speed_configs,
)
from startup_config import read_startup_config, write_startup_config
from ui_layout import ScrollablePage, initial_window_size


APP_VERSION = "0.24.0"
DEFAULT_DIFFICULTY_PERCENT = 50
DEFAULT_GAME = next((parent for parent in Path(__file__).resolve().parents if (parent / "Rocksmith2014.exe").is_file()), Path(r"C:\Program Files (x86)\Steam\steamapps\common\Rocksmith2014"))
DEFAULT_REVIEW = Path.home() / "Documents" / "JonsRockSmithMods" / "DoNotWant"
LOG_PATH = Path.home() / "Documents" / "JonsRockSmithMods" / "JonsRockSmithMods.log"
METADATA_PATH = LOG_PATH.with_name("metadata_cache.json")
PENDING_MOVE_QUEUE_PATH = LOG_PATH.with_name("pending_song_moves.json")
LAST_COMPLETED_MOVE_PATH = LOG_PATH.with_name("last_completed_song_removal.json")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logging.info(
    "Startup app_version=%s python=%s executable=%s frozen=%s",
    APP_VERSION,
    sys.version.split()[0],
    sys.executable,
    bool(getattr(sys, "frozen", False)),
)


class HoverTooltip:
    """Show short help beside a widget after a brief hover."""

    def __init__(self, widget, text: str, delay_ms: int = 500) -> None:
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id = None
        self._window = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Destroy>", self._on_destroy, add="+")

    def _schedule(self, _event=None) -> None:
        self._hide()
        try:
            if hasattr(self.widget, "instate") and self.widget.instate(["disabled"]):
                return
            self._after_id = self.widget.after(self.delay_ms, self._show)
        except tk.TclError:
            self._after_id = None

    def _show(self) -> None:
        self._after_id = None
        try:
            if not self.widget.winfo_exists():
                return
            window = tk.Toplevel(self.widget)
            window.wm_overrideredirect(True)
            label = tk.Label(
                window,
                text=self.text,
                justify="left",
                wraplength=380,
                background="#fff7cc",
                foreground="#202020",
                relief="solid",
                borderwidth=1,
                padx=8,
                pady=5,
            )
            label.pack()
            window.update_idletasks()
            x = self.widget.winfo_rootx() + 8
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 3
            window.wm_geometry(f"+{x}+{y}")
            self._window = window
        except tk.TclError:
            self._window = None

    def _hide(self, _event=None) -> None:
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None

    def _on_destroy(self, event) -> None:
        if event.widget is self.widget:
            self._hide()


def _validate_worker_result(description: str, result) -> None:
    """Reject bad inventory results before Tk code tries to render/count them."""

    if description == "Scanning songs":
        if not isinstance(result, list):
            raise StoreError(
                f"Song inventory returned {type(result).__name__}; expected a list. "
                "Check the selected game and Do Not Want paths, then refresh."
            )
        if any(not isinstance(row, SongFile) for row in result):
            raise StoreError("Song inventory contains an invalid row; check the app log and refresh.")
    if description == "Full library refresh":
        if not isinstance(result, LibraryRefreshResult):
            raise StoreError(
                f"Full library refresh returned {type(result).__name__}; expected a validated refresh result."
            )
        if not isinstance(result.rows, list) or any(not isinstance(row, SongFile) for row in result.rows):
            raise StoreError("Full library refresh contains invalid song rows; check the app log and refresh.")
        if not isinstance(result.favorite_states, dict):
            raise StoreError("Full library refresh returned invalid favorite states; automatic move is locked.")
        if not isinstance(result.library_signature, tuple):
            raise StoreError("Full library refresh returned no verified archive snapshot; automatic move is locked.")
    if description in ("Updating song favorites", "Unfavoriting all songs") and not isinstance(result, FavoriteUpdateResult):
        raise StoreError(
            f"Favorite update returned {type(result).__name__}; expected a verified profile update result."
        )
    if description in ("Queueing selected archives", "Canceling queued song removals"):
        if type(result) is not int or result < 0:
            raise StoreError(f"{description} returned an invalid archive count")
    if description == "Saving difficulty setting" and not isinstance(result, BulkDifficultyResult):
        raise StoreError("Global difficulty update did not return a verified batch result")
    if description == "Saving selected song difficulty" and not isinstance(result, SongDifficultyUpdateResult):
        raise StoreError("Difficulty update did not return a verified profile result")
    if description == "Resolving in-game song selection" and not isinstance(result, SongFile):
        raise StoreError("In-game song request did not resolve to one verified archive")
    if description == "Queueing in-game selected archive" and (type(result) is not int or result < 1):
        raise StoreError("In-game song removal returned an invalid archive count")
    if description == "Processing queued song removals" and not isinstance(result, PendingMoveResult):
        raise StoreError("Queued-removal worker returned an invalid result")
    if description == "Undoing last completed removal" and not isinstance(result, PendingMoveResult):
        raise StoreError("Removal undo returned an invalid result")


def favorite_scan_allows_automatic_move(rows, favorites, states, scanned_signature, current_signature) -> bool:
    """Require fresh profile data and a favorite proven inside the active DLC library."""

    return bool(
        favorites is not None
        and favorites.available
        and favorites.favorite_song_keys
        and scanned_signature == current_signature
        and any(row.location == "Active" and states.get(row.relative_path) == "Favorite" for row in rows)
    )


class App(tk.Tk):
    def report_callback_exception(self, exception_type, exception, trace) -> None:
        """Preserve the first full traceback and bound repeated callback failures."""
        state = self.__dict__
        count = state.get("_callback_failure_count", 0) + 1
        state["_callback_failure_count"] = count
        seen = state.setdefault("_callback_failure_signatures", set())
        last_frame = traceback.extract_tb(trace)[-1] if trace is not None else None
        signature = (exception_type.__module__, exception_type.__qualname__,
                     str(exception)[:500],
                     (last_frame.filename, last_frame.lineno) if last_frame else None)
        distinct = signature not in seen and len(seen) < 64
        if distinct:
            seen.add(signature)
        # Log every distinct episode (up to 64), then powers of two for repeats.
        # The first record retains the complete chained traceback.
        should_log = distinct or (count & (count - 1)) == 0
        if should_log:
            logging.error("JonsRockSmithMods %s UI callback failed count=%s: %s",
                          APP_VERSION, count, exception,
                          exc_info=(exception_type, exception, trace))
        status = state.get("status_var")
        if status is not None:
            try:
                status.set(f"UI action failed: {exception_type.__name__}: {exception}. Details: {LOG_PATH}")
            except Exception:
                if should_log:
                    logging.exception("JonsRockSmithMods %s could not display callback failure status", APP_VERSION)
        # Avoid an endless sequence of modal dialogs on repeated Tk events.
        if distinct and state.get("_callback_failure_dialog_count", 0) < 3:
            state["_callback_failure_dialog_count"] = state.get("_callback_failure_dialog_count", 0) + 1
            try:
                detail = "".join(traceback.format_exception(exception_type, exception, trace))
                messagebox.showerror(
                    "JonsRockSmithMods action failed",
                    f"The current UI action failed. Review the error before retrying.\n\n"
                    f"{detail}\nFull log: {LOG_PATH}\n"
                    "Repeated errors are kept in the log without repeated dialogs.", parent=self,
                )
            except Exception:
                logging.exception("JonsRockSmithMods %s could not open callback failure dialog", APP_VERSION)

    def __init__(self) -> None:
        super().__init__()
        self.title(f"JonsRockSmithMods {APP_VERSION}")
        width, height = initial_window_size(self.winfo_screenwidth(), self.winfo_screenheight())
        self.geometry(f"{width}x{height}")
        self.minsize(min(720, width), min(480, height))
        self.game_var = tk.StringVar(value=str(DEFAULT_GAME))
        self.review_var = tk.StringVar(value=str(DEFAULT_REVIEW))
        self.profile_var = tk.StringVar(value=self._discover_profile())
        self.filter_var = tk.StringVar()
        self.speed_enabled_var = tk.BooleanVar(value=False)
        self.speed_percent_var = tk.StringVar(value=str(DEFAULT_SPEED_PERCENT))
        self.speed_status_var = tk.StringVar(value="Checking Jons RSMods extension...")
        self.tuning_pitch_reference_var = tk.StringVar(value="")
        self.skip_first_tuner_var = tk.BooleanVar(value=DEFAULT_SKIP_FIRST_TUNER)
        self.tuning_status_var = tk.StringVar(value="Loading game tuning settings...")
        self.difficulty_percent_var = tk.StringVar(value=str(DEFAULT_DIFFICULTY_PERCENT))
        self.difficulty_level_var = tk.StringVar(value="5")
        self.startup_enabled_var = tk.BooleanVar(value=False)
        self.startup_profile_var = tk.StringVar()
        self.startup_status_var = tk.StringVar(value="Choose an exact local Rocksmith profile name.")
        self.allow_automatic_difficulty_var = tk.BooleanVar(value=True)
        self.difficulty_status_var = tk.StringVar(value="Select a Rocksmith profile to load its difficulty setting.")
        self.last_difficulty_report = ""
        self.status_var = tk.StringVar(value="Starting full refresh. Close Rocksmith before moving songs.")
        self.footer_status_var = tk.StringVar(value=self.status_var.get())
        self.destination_var = tk.StringVar(value="")
        self.rows: list[SongFile] = []
        self.metadata = MetadataCache(METADATA_PATH)
        self.favorite_result = None
        self.favorite_states: dict[str, str] = {}
        self.favorite_profile_signature = None
        self.favorite_library_signature = None
        self.visible_rows: dict[str, SongFile] = {}
        self.checked_paths: set[str] = set()
        self.pending_move_paths: set[str] = set()
        self.pending_game_song_keys: set[str] = set()
        self.sort_column: str | None = None
        self.sort_descending = False
        self.preview_working_directory: Path | None = None
        self.preview_active = False
        self.intonation_window: IntonationCheckerWindow | None = None
        self.health_window = None
        self.preview_status_var = tk.StringVar(value="No song preview playing.")
        self.favorite_write_status_var = tk.StringVar(
            value="Select verified song rows. Rocksmith must be closed to change profile favorites."
        )
        self.favorite_scan_summary = "Favorites have not been verified. Automatic move is locked."
        self.library_revision = 0
        self.pending_move_count = 0
        self.pending_move_check_active = False
        self.pending_move_retry_paused = False
        self.pending_move_check_error: str | None = None
        self.undo_last_removal_button = None
        self.active_game_song_request: InGameSongRequest | None = None
        self.game_song_request_error: str | None = None
        self.deferred_game_song_requests: set[str] = set()
        self.events: queue.Queue = queue.Queue()
        self._operation_busy = False
        self.tooltips: list[HoverTooltip] = []
        self.split_progress_dialog = None
        self.split_progress_status_var = None
        self.split_progress_bar = None
        self._build()
        self.progress.pack_forget()
        self._load_speed()
        self._load_tuning_settings()
        self._load_profile_difficulty()
        self.after(100, self._poll)
        self.after(1200, self._check_pending_move_queue)
        self.after(1500, self._check_in_game_song_requests)
        self.after(250, self.refresh)

    @staticmethod
    def _discover_profile(steam_users: Path | None = None) -> str:
        if steam_users is None:
            program_files_x86 = os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
            steam_users = Path(program_files_x86) / "Steam" / "userdata"
        profiles = (
            sorted(path for path in steam_users.glob("*/221680/remote/*_PRFLDB") if path.is_file())
            if steam_users.is_dir() else []
        )
        return str(profiles[0]) if len(profiles) == 1 else ""

    def _build(self) -> None:
        self.path_controls = []
        self.toolbar_buttons = []
        self.operation_buttons = []
        self.action_buttons = []
        self.pages = ttk.Notebook(self)
        self.pages.pack(fill="both", expand=True, padx=8, pady=(8, 0))
        library = ttk.Frame(self.pages)
        practice_page = ScrollablePage(self.pages)
        setup_page = ScrollablePage(self.pages)
        self.pages.add(library, text="Songs")
        self.pages.add(practice_page, text="Difficulty & speed")
        self.pages.add(setup_page, text="Setup & startup")
        practice = practice_page.content
        setup = setup_page.content

        toolbar = ttk.Frame(library, padding=8)
        toolbar.pack(fill="x")
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, text="Search songs").grid(row=0, column=0, sticky="w")
        ttk.Entry(toolbar, textvariable=self.filter_var).grid(row=0, column=1, sticky="ew", padx=8)
        self.filter_var.trace_add("write", lambda *_: self._render())
        tools = ttk.Frame(toolbar)
        tools.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        for column, (text, command) in enumerate((
            ("Full refresh", self.refresh),
            ("Scan favorites", self._scan_favorites),
            ("Tune guitar", self._open_intonation_checker),
            ("Check EVERY song's health", self._open_song_health),
        )):
            tools.columnconfigure(column, weight=1, uniform="library_tools")
            button = ttk.Button(tools, text=text, command=command)
            button.grid(row=0, column=column, sticky="ew", padx=2)
            self.toolbar_buttons.append(button)
            if column == 3:
                self.intonation_button = button

        body = ttk.Frame(library, padding=(8, 0))
        body.pack(fill="both", expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            body, columns=("move", "removal", "location", "artist", "song", "favorite", "name", "size"),
            show="headings", selectmode="extended", height=7,
        )
        self.column_titles = {
            "move": "Remove?", "removal": "Removal status", "location": "Location", "artist": "Artist",
            "song": "Song name", "favorite": "Favorite status",
            "name": "Archive filename", "size": "MiB",
        }
        for column, width in (("move", 72), ("removal", 170), ("location", 100), ("artist", 175),
                              ("song", 230), ("favorite", 125), ("name", 260), ("size", 70)):
            self.tree.heading(column, text=self.column_titles[column],
                              command=lambda selected_column=column: self._sort_tree(selected_column))
            self.tree.column(column, width=width, stretch=column in ("artist", "song"),
                             anchor="center" if column == "move" else ("e" if column == "size" else "w"))
        self.tree.bind("<Button-1>", self._toggle_move_checkbox, add=True)
        self.tree.bind("<Button-3>", self._show_row_context_menu, add=True)
        self.tree.bind("<Control-Button-1>", self._show_row_context_menu, add=True)
        self.tree.bind("<Double-1>", self._open_archive_details_event, add=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._update_action_states(), add=True)
        self.row_menu = Menu(self, tearoff=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(body, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)

        actions = ttk.Notebook(library, height=172)
        self.checked_count_var = tk.StringVar(value="No archives checked for removal.")
        ttk.Label(library, textvariable=self.checked_count_var, padding=(8, 2)).pack(fill="x")
        actions.pack(fill="x", padx=8, pady=6)
        remove_page, pack_page, favorites_page = [ScrollablePage(actions) for _ in range(3)]
        actions.add(remove_page, text="Remove & restore")
        actions.add(pack_page, text="Split & preview")
        actions.add(favorites_page, text="Favorites")
        remove, packs, favorites = remove_page.content, pack_page.content, favorites_page.content

        def action(parent, attribute, text, command, row, column=0):
            parent.columnconfigure(column, weight=1, uniform="actions")
            button = ttk.Button(parent, text=text, command=command)
            button.grid(row=row, column=column, sticky="ew", padx=3, pady=3)
            setattr(self, attribute, button)
            self.action_buttons.append(button)
            return button

        action(remove, "check_visible_button", "Check all matching active archives", self._check_visible_active, 0)
        action(remove, "clear_checks_button", "Clear removal checks", self._clear_move_checks, 0, 1)
        action(remove, "uncheck_favorites_button", "Uncheck favorite archives", self._uncheck_favorited_archives, 0, 2)
        action(remove, "move_checked_button", "Move checked to Do Not Want", self._move_checked, 1)
        action(remove, "queue_checked_button", "Queue checked until game closes", self._queue_checked_after_close, 1, 1)
        action(remove, "restore_button", "Restore selected archives", self._restore, 2)
        action(remove, "move_nonfavorites_button", "Move verified non-favorites", self._move_nonfavorites, 2, 1)
        action(remove, "cancel_queued_moves_button", "Undo queued removals", self._cancel_queued_moves, 3)
        action(remove, "retry_queued_moves_button", "Retry queued removals", self._retry_queued_moves, 3, 1)
        action(remove, "undo_last_removal_button", "Undo last completed removal", self._undo_last_completed_removal, 4)
        action(remove, "review_marks_button", "Retry saved F10 marks", self._review_saved_game_marks, 5)
        action(remove, "forget_marks_button", "Forget saved in-game marks", self._forget_saved_game_marks, 5, 1)
        self.pending_moves_status_var = tk.StringVar(value="No queued removals.")
        ttk.Label(remove, textvariable=self.pending_moves_status_var, wraplength=700).grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=4)
        ttk.Label(remove, text=(
            "WARNING: Queueing moves each checked PSARC out of active DLC into Do Not Want after Rocksmith closes. "
            "It is not permanently deleted. A song pack moves as one archive unless you split it first. "
            "Use Split up whole song pack to replace the complete pack with individual song files, then check "
            "only the individual songs you want moved. "
            "Undo queued removals before they run, or use Undo last completed removal afterward."
        ), foreground="#9b1c1c", wraplength=700, justify="left").grid(
            row=7, column=0, columnspan=2, sticky="ew", pady=(8, 2)
        )
        action(remove, "in_game_help_button", "In-game removal: setup and controls", self._show_in_game_removal_help, 8)

        action(packs, "split_pack_button", "Split up whole song pack", self._split_pack_selected, 0)
        practice_button = ttk.Button(packs, text="Edit difficulty / speed for selected pack songs", command=self._edit_song_practice)
        practice_button.grid(row=0, column=1, sticky="ew", padx=3, pady=3)
        self.operation_buttons.append(practice_button)
        action(packs, "preview_button", "Preview selected archive (30 sec)", self._preview_selected, 1)
        action(packs, "stop_preview_button", "Stop preview", self._stop_preview, 1, 1)
        self.split_pack_status_var = tk.StringVar(value="Select one verified multi-song archive.")
        ttk.Label(packs, textvariable=self.split_pack_status_var, wraplength=700).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=3)
        ttk.Label(packs, textvariable=self.preview_status_var, wraplength=700).grid(
            row=3, column=0, columnspan=2, sticky="ew")

        action(favorites, "add_favorites_button", "Add selected to favorites", lambda: self._update_selected_favorites(True), 0)
        action(favorites, "remove_favorites_button", "Remove selected from favorites", lambda: self._update_selected_favorites(False), 0, 1)
        action(favorites, "unfavorite_all_button", "Unfavorite every song", self._unfavorite_all, 1)
        ttk.Label(favorites, textvariable=self.favorite_write_status_var, wraplength=700).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=4)

        self.tooltips.extend((
            HoverTooltip(self.check_visible_button, "Checks every active archive matching the search, including scrolled rows. "
                         "Nothing moves until you choose a move or queue action."),
            HoverTooltip(self.clear_checks_button, "Clears removal checks; does not change selected songs or move files."),
            HoverTooltip(self.uncheck_favorites_button, "After a verified favorite scan, clears removal checks only for archives marked Favorite. It does not change profile favorites or move files."),
            HoverTooltip(self.move_checked_button, "Moves whole checked PSARCs reversibly into Do Not Want. Close Rocksmith first."),
            HoverTooltip(self.queue_checked_button, "Saves a removal request now and moves the whole archive after Rocksmith closes."),
            HoverTooltip(self.split_pack_button, "Creates and verifies every song separately, installs singles into dlc, "
                         "then moves the original pack to Do Not Want. Close Rocksmith first."),
        ))

        for parent in (practice, setup):
            parent.columnconfigure(0, weight=1)
        difficulty = ttk.LabelFrame(practice, text="Difficulty for every song", padding=10)
        difficulty.pack(fill="x", pady=(0, 16), ipadx=4, ipady=4)
        ttk.Label(difficulty, text="Sight Reading Level (1 easiest, 10 hardest)").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(difficulty, from_=1, to=10, increment=1,
                    textvariable=self.difficulty_level_var, width=5).grid(row=0, column=1, padx=8)
        ttk.Checkbutton(difficulty, text="Automatic difficulty changes",
                        variable=self.allow_automatic_difficulty_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=8)
        report_difficulty = ttk.Button(difficulty, text="View last difficulty result", command=self._show_difficulty_report)
        report_difficulty.grid(row=2, column=1, sticky="w", padx=8, pady=4)
        self.operation_buttons.append(report_difficulty)
        save_difficulty = ttk.Button(difficulty, text="Apply level to every song", command=self._save_profile_difficulty)
        save_difficulty.grid(row=2, column=0, sticky="w")
        self.operation_buttons.append(save_difficulty)
        ttk.Label(difficulty, textvariable=self.difficulty_status_var, wraplength=700).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Label(difficulty, text="Close Rocksmith before changing saved difficulty. A verified profile backup is made first. "
                  "Restart the game to load changes.", wraplength=700).grid(row=4, column=0, columnspan=2, sticky="ew")
        ttk.Separator(practice, orient="horizontal").pack(fill="x", pady=(0, 16))
        speed = ttk.LabelFrame(practice, text="Default speed for songs without an override", padding=10)
        speed.pack(fill="x", pady=(0, 12), ipadx=4, ipady=4)
        ttk.Checkbutton(speed, text="Enable default speed for every song", variable=self.speed_enabled_var).grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(speed, text="Speed % (new-setting default 85%)").grid(row=1, column=0, sticky="w", pady=8)
        ttk.Spinbox(speed, from_=25, to=400, textvariable=self.speed_percent_var, width=6).grid(row=1, column=1)
        button = ttk.Button(speed, text="Save default speed", command=self._save_speed)
        button.grid(row=2, column=0, sticky="w")
        self.operation_buttons.append(button)
        ttk.Label(speed, textvariable=self.speed_status_var, wraplength=700).grid(
            row=3, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Label(speed, text="Individual song overrides take priority. The Jons RSMods extension must be installed.",
                  wraplength=700).grid(row=4, column=0, columnspan=2, sticky="ew")

        running_path = Path(sys.executable) if getattr(sys, "frozen", False) else Path(__file__).resolve()
        ttk.Label(setup, text=f"Running version {APP_VERSION} ({'EXE' if getattr(sys, 'frozen', False) else 'Python'})\n{running_path}", wraplength=700).pack(anchor="w", pady=(0, 8))
        ttk.Label(setup, text="Verify file links on THIS computer before changing anything: Rocksmith folder, Do Not Want backup folder, and your Steam *_PRFLDB profile. Browse to the correct paths if these defaults differ. Toolkit must also be installed separately for archive features.", wraplength=700).pack(anchor="w", pady=(0, 10))
        paths = ttk.LabelFrame(setup, text="Folders and profile", padding=10)
        paths.pack(fill="x", pady=(0, 12))
        paths.columnconfigure(0, weight=1)
        for row, (label, variable, browse) in enumerate((
            ("Rocksmith 2014 folder", self.game_var, self._browse_game),
            ("Do Not Want backup folder", self.review_var, self._browse_review),
            ("Rocksmith profile (*_PRFLDB)", self.profile_var, self._browse_profile),
        )):
            ttk.Label(paths, text=label).grid(row=row * 2, column=0, columnspan=2, sticky="w", pady=(6, 0))
            entry = ttk.Entry(paths, textvariable=variable)
            entry.grid(row=row * 2 + 1, column=0, sticky="ew", pady=3)
            browse_button = ttk.Button(paths, text="Browse", command=browse)
            browse_button.grid(row=row * 2 + 1, column=1, padx=(8, 0))
            self.path_controls.extend((entry, browse_button))
        self.game_var.trace_add("write", lambda *_: self._invalidate_library_scan())
        self.review_var.trace_add("write", lambda *_: self._invalidate_library_scan())
        self.profile_var.trace_add("write", lambda *_: self._invalidate_favorites_scan())
        self.destination_var.set(self._store_destination_label())
        self.review_var.trace_add("write", lambda *_: self.destination_var.set(self._store_destination_label()))
        ttk.Label(paths, textvariable=self.destination_var, wraplength=700).grid(row=6, column=0, columnspan=2, sticky="ew", pady=5)
        self.open_songs_folder_button = ttk.Button(paths, text="Open Do Not Want songs folder", command=self._open_do_not_want_folder)
        self.open_songs_folder_button.grid(row=7, column=0, sticky="w")
        self.operation_buttons.append(self.open_songs_folder_button)
        tuning = ttk.LabelFrame(setup, text="Game pitch detection and first song", padding=10)
        tuning.pack(fill="x", pady=(0, 12))
        ttk.Label(tuning, text="Note-detection pitch reference").grid(row=0, column=0, sticky="w")
        pitch_choices = (
            "A440 standard (keep chart tuning such as Drop D)",
            "Rocksmith chart-authored pitch reference",
        )
        self.tuning_pitch_choice_to_value = {
            pitch_choices[0]: PITCH_REFERENCE_A440,
            pitch_choices[1]: PITCH_REFERENCE_ROCKSMITH,
        }
        self.tuning_pitch_value_to_choice = {value: label for label, value in self.tuning_pitch_choice_to_value.items()}
        self.tuning_pitch_reference_var.set(self.tuning_pitch_value_to_choice[DEFAULT_PITCH_REFERENCE])
        pitch_selector = ttk.Combobox(
            tuning, textvariable=self.tuning_pitch_reference_var,
            values=pitch_choices, state="readonly", width=54,
        )
        pitch_selector.grid(row=1, column=0, sticky="ew", pady=(3, 8))
        tuning.columnconfigure(0, weight=1)
        ttk.Checkbutton(
            tuning,
            text="Skip the first pre-song tuner after each Rocksmith launch",
            variable=self.skip_first_tuner_var,
        ).grid(row=2, column=0, sticky="w", pady=3)
        save_tuning = ttk.Button(tuning, text="Save game tuning settings", command=self._save_tuning_settings)
        save_tuning.grid(row=3, column=0, sticky="w", pady=(5, 3))
        self.operation_buttons.append(save_tuning)
        ttk.Label(tuning, textvariable=self.tuning_status_var, wraplength=700).grid(
            row=4, column=0, sticky="ew", pady=4
        )
        ttk.Label(
            tuning,
            text=(
                "A440 changes Rocksmith's note-detection reference while keeping the chart's semitone tuning; "
                "it does not retune the backing audio. If a recording is intentionally detuned, it may sound "
                "out of tune against A440. The skip applies once per game launch; later songs still show their tuner. "
                "Requires the compatible Jons RSMods extension. Close Rocksmith to save, then restart it."
            ),
            wraplength=700,
        ).grid(row=5, column=0, sticky="ew", pady=(3, 0))
        extension = ttk.LabelFrame(setup, text="RSMods integration", padding=10)
        extension.pack(fill="x", pady=(0, 12))
        ttk.Label(extension, textvariable=self.speed_status_var, wraplength=700).pack(anchor="w")
        for text, command in (
            ("Install / update Jons RSMods extension", self._install_speed_support),
            ("Restore backed-up RSMods DLL", self._restore_original),
        ):
            button = ttk.Button(extension, text=text, command=command)
            button.pack(anchor="w", pady=5)
            self.operation_buttons.append(button)
        startup = ttk.LabelFrame(setup, text="Startup and local profile", padding=10)
        startup.pack(fill="x")
        ttk.Checkbutton(startup, text="Auto-advance startup and load my profile",
                        variable=self.startup_enabled_var).pack(anchor="w")
        ttk.Label(startup, text="Exact Rocksmith profile name shown in the game").pack(anchor="w", pady=(8, 2))
        ttk.Entry(startup, textvariable=self.startup_profile_var).pack(fill="x")
        button = ttk.Button(startup, text="Save startup preference", command=self._save_startup)
        button.pack(anchor="w", pady=8)
        self.operation_buttons.append(button)
        ttk.Label(startup, textvariable=self.startup_status_var, wraplength=700).pack(anchor="w")
        ttk.Label(startup, text="Uses RSMods auto-profile loading. Loading/cinematic time may remain. "
                  "This selects a local Rocksmith profile; Steam login is handled by Steam.", wraplength=700).pack(anchor="w", pady=6)
        ttk.Label(setup, text=f"Detailed errors and progress log: {LOG_PATH}", wraplength=700).pack(
            anchor="w", pady=10)

        footer = ttk.Frame(self, padding=(8, 4))
        footer.pack(fill="x")
        self.progress = ttk.Progressbar(footer, mode="indeterminate", name="progress")
        self.progress.pack(fill="x")
        status = ttk.Label(footer, textvariable=self.footer_status_var, wraplength=700)
        status.pack(fill="x", pady=(4, 0))
        status.bind("<Configure>", lambda event: status.configure(wraplength=max(220, event.width - 8)))
        self.status_var.trace_add("write", lambda *_: self._update_footer_status())
        self.protocol("WM_DELETE_WINDOW", self._close_app)
        self._update_action_states()

    def _update_footer_status(self) -> None:
        text = " ".join(self.status_var.get().split())
        self.footer_status_var.set(text[:280] + ("..." if len(text) > 280 else ""))

    def _browse_game(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.game_var.get(), title="Select Rocksmith 2014 folder")
        if selected:
            self.game_var.set(selected)
            self._load_speed()
            self._load_tuning_settings()
            self.refresh()

    def _browse_review(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.review_var.get(), title="Select Do Not Want folder")
        if selected:
            try:
                root = normalize_review_root(Path(selected))
            except StoreError as exc:
                messagebox.showwarning("Do Not Want folder", str(exc))
                return
            self.review_var.set(str(root))
            self.refresh()

    def _browse_profile(self) -> None:
        default_userdata = Path(
            os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)"
        ) / "Steam" / "userdata"
        initial = str(Path(self.profile_var.get()).parent) if self.profile_var.get() else str(default_userdata)
        selected = filedialog.askopenfilename(
            initialdir=initial,
            title="Select Rocksmith profile for favorites",
            filetypes=(("Rocksmith profiles", "*_PRFLDB"), ("All files", "*.*")),
        )
        if selected:
            self.profile_var.set(selected)
            self._load_profile_difficulty()
            self.refresh()

    def _invalidate_favorites_scan(self) -> None:
        self.favorite_result = None
        self.favorite_profile_signature = None
        self.favorite_library_signature = None
        self.favorite_states.clear()
        self.favorite_scan_summary = "Selected profile changed. Scan favorites again; automatic move is locked."
        if hasattr(self, "tree"):
            self._render()
            self._update_action_states()

    def _invalidate_library_scan(self) -> None:
        self.library_revision += 1
        self.checked_paths.clear()
        self._invalidate_favorites_scan()
        if hasattr(self, "status_var"):
            self.status_var.set("Game or Do Not Want path changed. Run Full refresh before automatic moves.")

    def _normalized_review_root(self) -> Path:
        root = normalize_review_root(Path(self.review_var.get()))
        entered = Path(self.review_var.get()).expanduser().resolve(strict=False)
        if root != entered:
            self.review_var.set(str(root))
        return root

    def _store(self) -> SongStore:
        return SongStore(Path(self.game_var.get()), self._normalized_review_root())

    def _load_speed(self) -> None:
        game = Path(self.game_var.get())
        try:
            enabled, percent = read_speed_config(game / "JonsRockSmithMods.ini")
            self.speed_enabled_var.set(enabled)
            self.speed_percent_var.set(str(percent))
            self.speed_status_var.set(support_status(game))
        except (OSError, ValueError, StoreError) as exc:
            logging.exception("Cannot load speed or extension status")
            self.speed_status_var.set(f"Speed / extension status unavailable: {exc}")
        try:
            enabled, profile_name = read_startup_config(game / "RSMods.ini")
            self.startup_enabled_var.set(enabled)
            self.startup_profile_var.set(profile_name)
            self.startup_status_var.set(
                f"Automatic local profile: {profile_name}" if enabled
                else "Automatic local profile selection is off."
            )
        except (OSError, ValueError, StoreError) as exc:
            self.startup_status_var.set(f"Startup preference unavailable: {exc}")

    def _load_tuning_settings(self) -> None:
        game = Path(self.game_var.get())
        try:
            pitch_reference, skip_first_tuner = read_game_tuning_settings(
                game / "JonsRockSmithMods.ini"
            )
            self.tuning_pitch_reference_var.set(
                self.tuning_pitch_value_to_choice[pitch_reference]
            )
            self.skip_first_tuner_var.set(skip_first_tuner)
            pitch_name = "A440 standard" if pitch_reference == PITCH_REFERENCE_A440 else "Rocksmith-authored"
            skip_name = "on" if skip_first_tuner else "off"
            self.tuning_status_var.set(
                f"Loaded: {pitch_name} note reference; first-song tuner skip {skip_name}. "
                "Requires the compatible Jons extension and a game restart to apply."
            )
        except (OSError, ValueError, StoreError, KeyError) as exc:
            logging.exception("Cannot load game tuning settings")
            self.tuning_status_var.set(f"Game tuning settings unavailable: {exc}")

    def _save_tuning_settings(self) -> None:
        choice = self.tuning_pitch_reference_var.get()
        pitch_reference = self.tuning_pitch_choice_to_value.get(choice)
        if pitch_reference is None:
            messagebox.showwarning(
                "Game tuning settings", "Choose a note-detection pitch reference before saving."
            )
            return
        game = Path(self.game_var.get())
        self._run(
            "Saving game tuning settings",
            lambda: write_game_tuning_settings(
                game / "JonsRockSmithMods.ini",
                pitch_reference,
                self.skip_first_tuner_var.get(),
            ),
        )

    def _save_startup(self) -> None:
        enabled, profile_name = self.startup_enabled_var.get(), self.startup_profile_var.get().strip()
        if enabled and not profile_name:
            messagebox.showwarning("Startup profile", "Enter the exact local Rocksmith profile name shown in the game.")
            return
        game = Path(self.game_var.get())
        self._run("Saving startup preference", lambda: write_startup_config(
            game / "RSMods.ini", enabled, profile_name
        ))

    def _load_profile_difficulty(self) -> None:
        profile_path = Path(self.profile_var.get())
        if not profile_path.is_file():
            self.difficulty_percent_var.set(str(DEFAULT_DIFFICULTY_PERCENT))
            self.difficulty_level_var.set("5")
            self.difficulty_status_var.set(
                "No valid profile selected. Default level is 5/10; use Browse on Setup & startup "
                "to select this PC's *_PRFLDB. Nothing is written until Apply is clicked."
            )
            return
        try:
            settings = read_difficulty_settings(profile_path)
        except (OSError, ValueError, ImportError) as exc:
            self.difficulty_status_var.set(f"Difficulty unavailable: {exc}")
            return
        level = settings.sightreading_level
        if level >= 0:
            self.difficulty_level_var.set(str(max(1, min(10, int(level + 0.5)))))
            self.difficulty_percent_var.set(str(round(level * 10)))
        self.allow_automatic_difficulty_var.set(settings.automatic_leveling_enabled)
        saved = "Auto" if level < 0 else f"{level:g}/10"
        automatic = "on" if settings.automatic_leveling_enabled else "off"
        self.difficulty_status_var.set(
            f"Saved starting level: {saved}. Automatic difficulty: {automatic}. "
            "Apply updates the starting level and saved phrases for every installed song. "
            "Individual songs can use different saved levels."
        )

    def _difficulty_snapshot(self):
        profile = Path(self.profile_var.get()).expanduser()
        if game_is_running():
            guidance = (
                "Close Rocksmith, return here and click Apply again, then restart Rocksmith. "
                "The running game's profile cannot be safely edited."
            )
            self.difficulty_status_var.set(guidance)
            messagebox.showinfo("Close Rocksmith to save", guidance)
            return None
        signature = profile_file_signature(profile)
        if signature is None:
            messagebox.showwarning("Rocksmith profile", "Choose a valid *_PRFLDB file on Setup & startup first.")
            return None
        return profile, signature

    def _difficulty_metadata_for(self, rows, game_root, selected_ids=None):
        before = library_signature(rows)
        toolkit = find_toolkit_path(game_root)
        wanted = None if selected_ids is None else {key.casefold() for key in selected_ids}
        result = {}
        for index, row in enumerate(rows, start=1):
            self.events.put(("progress", "Reading song difficulty", f"{index}/{len(rows)}: {row.path.name}"))
            document = extract_difficulty_metadata(row.path, toolkit=toolkit)
            if not isinstance(document, dict) or document.get("schema_version") != 1:
                raise StoreError(f"Invalid difficulty metadata for {row.path.name}. Rebuild the metadata helper.")
            if document.get("warnings"):
                raise StoreError(f"Difficulty metadata incomplete for {row.path.name}: " + "; ".join(document["warnings"][:5]))
            records = document.get("arrangements")
            if not isinstance(records, list):
                raise StoreError(f"No arrangement metadata for {row.path.name}.")
            for item in records:
                key = item["arrangement_id"]
                if wanted is not None and key.casefold() not in wanted:
                    continue
                existing = next((value for name, value in result.items() if name.casefold() == key.casefold()), None)
                if existing is not None and existing != item["phrase_max_difficulties"]:
                    raise StoreError(f"Arrangement {key} has conflicting installed charts; resolve duplicate archives first.")
                result[key] = item["phrase_max_difficulties"]
        if library_signature(rows) != before:
            raise StoreError("Song archives changed while reading difficulty. Refresh and retry.")
        if not result:
            raise StoreError("No instrumental arrangements with saved difficulty were found for these songs.")
        return result

    def _save_profile_difficulty(self) -> None:
        try:
            level = int(self.difficulty_level_var.get())
            if not 1 <= level <= 10:
                raise ValueError()
            snapshot = self._difficulty_snapshot()
        except (OSError, ValueError, StoreError) as exc:
            messagebox.showwarning("Sight Reading Level", f"Enter a whole level from 1 to 10. {exc}")
            return
        if snapshot is None:
            return
        profile, signature = snapshot
        rows = [row for row in self.rows if row.location == "Active"]
        game_root = Path(self.game_var.get())
        try:
            store = self._store()
            expected_library = library_signature(rows)
        except (OSError, StoreError) as exc:
            messagebox.showwarning("Song library", f"{exc}. Refresh and retry.")
            return
        automatic = self.allow_automatic_difficulty_var.get()
        if not messagebox.askyesno(
            "Apply difficulty to every installed song",
            f"Set Sight Reading Level to {level}/10 and update saved phrase levels for every installed song?\n\n"
            f"Automatic difficulty changes: {'ON' if automatic else 'OFF'}.\nProfile: {profile}\n\n"
            "Each phrase is scaled against its chart's own maximum difficulty. Unreadable or ambiguous archives "
            "are skipped; their saved phrase values stay unchanged. The global starting level and automatic "
            "difficulty option still apply to every song. Unplayed songs use that starting level. "
            "A verified backup is made before writing. A final report lists overall success and all skips. Restart Rocksmith after saving.",
        ):
            return

        def worker():
            def title_lookup(path):
                document = self.metadata.get(path)
                tracks = metadata_song_records(document) if document else []
                return "; ".join(f"{item.get('artist', '')} - {item.get('title', '')}" for item in tracks)

            try:
                batch = collect_global_difficulty(
                    rows, find_toolkit_path(game_root), extract_difficulty_metadata,
                    progress=lambda index, total, row: self.events.put(
                        ("progress", "Reading song difficulty", f"{index}/{total}: {row.path.name}")
                    ), title_lookup=title_lookup,
                )
                logging.info("Global difficulty preflight: level=%s automatic=%s\n%s", level, automatic, batch.details())
                if not batch.arrangements:
                    raise StoreError("No verified arrangements available.\n" + batch.details())
                if profile_file_signature(profile) != signature:
                    raise StoreError("Profile changed after confirmation. Refresh and retry.")
                if library_signature([row for row in store.inventory() if row.location == "Active"]) != expected_library:
                    raise StoreError("Installed song library changed after confirmation. Refresh and retry.")
            except Exception as exc:
                raise StoreError(f"FAILED: no profile changes were made by this operation.\n{exc}") from exc
            try:
                update = set_all_song_difficulty(
                    profile, batch.arrangements, tuple(batch.arrangements), level, automatic,
                    expected_profile_sha256=signature[3],
                )
            except Exception as exc:
                raise StoreError(
                    "FAILED: overall apply was not confirmed. Check the profile/backup and full error before retrying.\n"
                    + str(exc) + "\n" + batch.details()
                ) from exc
            return BulkDifficultyResult(update, batch)

        self._run("Saving difficulty setting", worker)

    def _show_difficulty_report(self) -> None:
        report = self.last_difficulty_report
        if not report:
            messagebox.showinfo("Difficulty result", "No difficulty operation has completed in this session.", parent=self)
            return
        dialog = tk.Toplevel(self)
        dialog.title("Overall difficulty result")
        dialog.geometry("880x560")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        ttk.Label(dialog, text=report.splitlines()[0], wraplength=820, padding=10).grid(row=0, column=0, sticky="ew")
        text = tk.Text(dialog, wrap="word")
        text.insert("1.0", report)
        text.configure(state="disabled")
        text.grid(row=1, column=0, sticky="nsew", padx=(10, 0))
        scroll = ttk.Scrollbar(dialog, orient="vertical", command=text.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        text.configure(yscrollcommand=scroll.set)
        ttk.Button(dialog, text="Close", command=dialog.destroy).grid(row=2, column=0, sticky="e", padx=10, pady=10)

    def _edit_song_practice(self) -> None:
        if self._operation_busy:
            return
        rows = [self.visible_rows[item] for item in self.tree.selection() if item in self.visible_rows]
        if not rows:
            messagebox.showinfo("Individual song settings", "Select one or more archives in Songs first, then choose this action.")
            self.pages.select(0)
            return
        tracks = []
        seen = set()
        try:
            library_signature(rows)
            for row in rows:
                document = self.metadata.get(row.path)
                song_keys_from_metadata(document)
                for song in document["songs"]:
                    key = str(song["song_key"])
                    if key.casefold() in seen:
                        continue
                    ids = tuple(song.get("arrangement_ids") or ())
                    seen.add(key.casefold())
                    tracks.append((row, song, ids))
            if not tracks:
                raise StoreError("No verified songs found in these archives.")
            overrides = read_song_speed_config(Path(self.game_var.get()) / "JonsRockSmithMods.ini")
        except (OSError, ValueError, StoreError) as exc:
            messagebox.showwarning("Individual song settings", f"{exc}\n\nRun Full refresh first.")
            return
        window = tk.Toplevel(self)
        window.title("Individual song difficulty and speed")
        width, height = initial_window_size(self.winfo_screenwidth(), self.winfo_screenheight())
        window.geometry(f"{min(width, 900)}x{min(height, 650)}")
        window.minsize(min(650, width), min(430, height))
        window.rowconfigure(0, weight=1)
        window.columnconfigure(0, weight=1)
        page = ScrollablePage(window)
        page.grid(row=0, column=0, sticky="nsew")
        panel = page.content
        panel.columnconfigure(0, weight=1)
        ttk.Label(panel, text="Choose exact songs. Ctrl/Shift selects several; settings apply only to selected tracks.",
                  wraplength=700).pack(anchor="w", pady=(0, 8))
        list_frame = ttk.Frame(panel)
        list_frame.pack(fill="x")
        list_frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(list_frame, columns=("artist", "song", "speed"), show="headings", selectmode="extended", height=6)
        for name, text, colwidth in (("artist", "Artist", 200), ("song", "Song", 350), ("speed", "Saved speed", 110)):
            tree.heading(name, text=text)
            tree.column(name, width=colwidth)
        tree.grid(row=0, column=0, sticky="ew")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(list_frame, orient="horizontal", command=tree.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        for index, (_, song, _) in enumerate(tracks):
            override = overrides.get(song["song_key"].casefold())
            tree.insert("", "end", iid=str(index), values=(
                song["artist"], song["title"], f"{override}%" if override is not None else "Default"
            ))
        tree.selection_set("0")
        level_var = tk.StringVar(window, value=self.difficulty_level_var.get())
        speed_var = tk.StringVar(window, value=str(overrides.get(tracks[0][1]["song_key"].casefold(), DEFAULT_SPEED_PERCENT)))

        def selected():
            indices = [int(item) for item in tree.selection()]
            if not indices:
                raise StoreError("Select at least one song.")
            return tuple(tracks[index] for index in indices)

        def apply_difficulty():
            try:
                chosen = selected()
                level = int(level_var.get())
                if not 1 <= level <= 10:
                    raise ValueError("Choose a level from 1 to 10.")
                snapshot = self._difficulty_snapshot()
                if snapshot is None:
                    return
                profile, signature = snapshot
                source_rows = list({str(row.path): row for row, _, _ in chosen}.values())
                game_root = Path(self.game_var.get())
                ids = tuple(dict.fromkeys(key for _, _, arrangement_ids in chosen for key in arrangement_ids))
                if not ids:
                    raise StoreError("No arrangement IDs available. Run Full refresh before changing difficulty; song speed only needs SongKey.")
            except (OSError, ValueError, StoreError) as exc:
                messagebox.showwarning("Song difficulty", str(exc), parent=window)
                return
            if not messagebox.askyesno("Apply selected song difficulty",
                f"Apply level {level}/10 to {len(chosen)} selected song(s)?\n\n"
                "Saved arrangements will be updated. Unplayed arrangements use the global starting level; "
                "play a new song once to initialize its saved phrase levels. "
                "Automatic leveling keeps the global setting. Restart Rocksmith after saving.",
                parent=window):
                return
            window.destroy()

            def worker():
                maps = self._difficulty_metadata_for(source_rows, game_root, ids)
                if profile_file_signature(profile) != signature:
                    raise StoreError("Profile changed after confirmation. Refresh and retry.")
                return set_song_difficulty(profile, maps, tuple(maps), level, expected_profile_sha256=signature[3])

            self._run("Saving selected song difficulty", worker)

        def apply_speed(reset=False):
            try:
                chosen = selected()
                percent = None if reset else int(speed_var.get())
                if percent is not None and not 25 <= percent <= 400:
                    raise ValueError("Choose a speed from 25% to 400%.")
                values = {song["song_key"]: percent for _, song, _ in chosen}
                ini = Path(self.game_var.get()) / "JonsRockSmithMods.ini"
            except (ValueError, StoreError) as exc:
                messagebox.showwarning("Song speed", str(exc), parent=window)
                return
            window.destroy()
            self._run("Saving individual song speed", lambda: write_song_speed_configs(ini, values))

        difficulty = ttk.LabelFrame(panel, text="Difficulty for selected songs", padding=8)
        difficulty.pack(fill="x", pady=10)
        ttk.Label(difficulty, text="Level 1-10").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(difficulty, from_=1, to=10, textvariable=level_var, width=5).grid(row=0, column=1, padx=8)
        ttk.Button(difficulty, text="Apply song difficulty", command=apply_difficulty).grid(row=0, column=2)
        ttk.Label(difficulty, text="Close Rocksmith before saving. Turn automatic difficulty off in the main "
                  "Difficulty & speed tab to keep saved levels fixed.", wraplength=700).grid(
                      row=1, column=0, columnspan=3, sticky="ew", pady=8)
        speed = ttk.LabelFrame(panel, text="Speed for selected songs", padding=8)
        speed.pack(fill="x")
        ttk.Label(speed, text="Speed %").grid(row=0, column=0)
        ttk.Spinbox(speed, from_=25, to=400, textvariable=speed_var, width=6).grid(row=0, column=1, padx=8)
        ttk.Button(speed, text="Apply song speed", command=apply_speed).grid(row=0, column=2)
        ttk.Button(speed, text="Use default speed", command=lambda: apply_speed(True)).grid(row=1, column=0, columnspan=3, sticky="w", pady=8)
        ttk.Label(speed, text="Requires the updated Jons RSMods extension and song previews enabled. "
                  "Changes reload in about one second during supported Learn a Song / Riff Repeater sessions.",
                  wraplength=700).grid(row=2, column=0, columnspan=3, sticky="ew")
        footer = ttk.Frame(window, padding=8)
        footer.grid(row=1, column=0, sticky="ew")
        ttk.Button(footer, text="Return to Songs", command=window.destroy).pack(side="right")

    def _save_speed(self) -> None:
        try:
            percent = int(self.speed_percent_var.get())
        except ValueError:
            messagebox.showwarning("JonsRockSmithMods", "Enter a whole number from 25 to 400")
            return
        game = Path(self.game_var.get())
        enabled = self.speed_enabled_var.get()
        self._run("Saving speed setting", lambda: write_speed_config(game / "JonsRockSmithMods.ini", enabled, percent))

    def _install_speed_support(self) -> None:
        game = Path(self.game_var.get())
        if not messagebox.askyesno(
            "Install Jons RSMods extension",
            "Replace the compatible RSMods DLL with the Jons extension for fixed speed and in-game song requests?\n"
            "A verified timestamped backup will be kept. Close Rocksmith first. Restart Rocksmith after install.",
        ):
            return
        self._run("Installing speed support", lambda: install_native(game))

    def _restore_original(self) -> None:
        game = Path(self.game_var.get())
        if not messagebox.askyesno(
            "Restore RSMods",
            "Restore the exact pre-install RSMods DLL backup? It may be an earlier Jons extension. Close Rocksmith first.",
        ):
            return
        self._run("Restoring original RSMods", lambda: restore_native(game))

    def _selected(self, required_location: str) -> list[str]:
        selected = [self.visible_rows[item] for item in self.tree.selection() if item in self.visible_rows]
        wrong = [row for row in selected if row.location != required_location]
        if wrong:
            raise StoreError(f"Select only {required_location.lower()} songs for this action")
        if not selected:
            raise StoreError("Select one or more songs first")
        return [row.relative_path for row in selected]

    @staticmethod
    def _path_key(row: SongFile) -> str:
        return str(row.path.resolve(strict=False)).casefold()

    def _checked_active(self) -> list[SongFile]:
        return [row for row in self.rows
                if row.location == "Active" and self._path_key(row) in self.checked_paths]

    def _store_destination_label(self) -> str:
        try:
            return str(normalize_review_root(Path(self.review_var.get())) / "songs")
        except StoreError as exc:
            return f"Invalid Do Not Want folder: {exc}"

    def _open_do_not_want_folder(self) -> None:
        songs_folder: Path | None = None
        try:
            store = self._store()
            songs_folder = store.songs_root
            songs_folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(songs_folder))
            self.status_var.set(f"Opened Do Not Want songs folder: {songs_folder}")
        except (AttributeError, OSError, StoreError) as exc:
            logging.exception("Could not open Do Not Want songs folder")
            target = str(songs_folder) if songs_folder is not None else self._store_destination_label()
            messagebox.showerror("Do Not Want songs folder", f"Could not open this folder:\n{target}\n\n{exc}")

    def _profile_signature(self):
        return profile_file_signature(self.profile_var.get())

    def _favorites_move_is_safe(self) -> bool:
        return self.favorite_library_signature is not None and favorite_scan_allows_automatic_move(
            self.rows,
            self.favorite_result,
            self.favorite_states,
            self.favorite_profile_signature,
            self._profile_signature(),
        )

    def _update_action_states(self) -> None:
        if not hasattr(self, "action_buttons"):
            return
        state = "disabled" if self._operation_busy else "normal"
        for button in self.action_buttons:
            button.configure(state=state)
        if self._operation_busy:
            return
        self.clear_checks_button.configure(state="normal")
        checked_favorites = any(
            row.location == "Active"
            and self._path_key(row) in self.checked_paths
            and self.favorite_states.get(row.relative_path) == "Favorite"
            for row in self.rows
        )
        verified_favorites = (
            self.favorite_result is not None
            and self.favorite_result.available
            and self.favorite_library_signature is not None
            and self.favorite_profile_signature == self._profile_signature()
        )
        self.uncheck_favorites_button.configure(
            state="normal" if checked_favorites and verified_favorites else "disabled"
        )
        self.move_checked_button.configure(state="normal" if self._checked_active() else "disabled")
        self.queue_checked_button.configure(state="normal" if self._checked_active() else "disabled")
        self.cancel_queued_moves_button.configure(
            state="normal" if self.pending_move_count and not self._operation_busy else "disabled"
        )
        self.retry_queued_moves_button.configure(
            state="normal" if self.pending_move_count and self.pending_move_retry_paused and not self._operation_busy else "disabled"
        )
        self.move_nonfavorites_button.configure(
            state="normal" if self._favorites_move_is_safe() else "disabled"
        )
        self.stop_preview_button.configure(state="normal" if self.preview_active else "disabled")
        selected_rows = [
            self.visible_rows[item]
            for item in self.tree.selection()
            if item in self.visible_rows
        ]
        favorite_state = "normal" if selected_rows else "disabled"
        split_state = "disabled"
        if not selected_rows:
            split_status = "Select one verified multi-song archive."
        elif len(selected_rows) != 1:
            split_status = "Select one archive only; this action splits one pack at a time."
        else:
            split_reason = split_pack_unavailable_reason(
                self.metadata.get(selected_rows[0].path)
            )
            if split_reason is None:
                split_state = "normal"
                split_status = "Select tracks in next window, or choose Select all songs to split entire pack."
            else:
                split_status = split_reason
        self.split_pack_button.configure(state=split_state)
        self.split_pack_status_var.set(split_status)
        self.add_favorites_button.configure(state=favorite_state)
        self.remove_favorites_button.configure(state=favorite_state)
        favorite_result = self.favorite_result
        can_unfavorite_all = bool(
            favorite_result is not None
            and favorite_result.available
            and favorite_result.favorite_song_keys
            and self.favorite_profile_signature is not None
        )
        self.unfavorite_all_button.configure(
            state="normal" if can_unfavorite_all else "disabled"
        )

    def _sort_tree(self, column: str) -> None:
        if column not in self.column_titles:
            return
        self.sort_column, self.sort_descending = next_sort_state(
            self.sort_column, self.sort_descending, column
        )
        self._apply_tree_sort()

    def _apply_tree_sort(self) -> None:
        if self.sort_column is None:
            return
        column_order = tuple(self.column_titles)
        column_index = column_order.index(self.sort_column)
        items = []
        for item in self.tree.get_children(""):
            row = self.visible_rows.get(item)
            if row is None:
                continue
            values = self.tree.item(item, "values")
            value = row.size if self.sort_column == "size" else values[column_index]
            key = song_sort_key(
                self.sort_column,
                value,
                checked=self._path_key(row) in self.checked_paths,
                location=row.location,
                stable_path=row.relative_path,
            )
            items.append((key, item))
        items.sort(key=lambda pair: pair[0], reverse=self.sort_descending)
        for index, (_key, item) in enumerate(items):
            self.tree.move(item, "", index)
        for column, title in self.column_titles.items():
            marker = " ▼" if column == self.sort_column and self.sort_descending else (
                " ▲" if column == self.sort_column else ""
            )
            self.tree.heading(
                column,
                text=title + marker,
                command=lambda selected_column=column: self._sort_tree(selected_column),
            )

    def _toggle_move_checkbox(self, event) -> str | None:
        if event.state & 0x0004:  # Preserve Control-click for the row context menu.
            return None
        if self.tree.identify_region(event.x, event.y) != "cell":
            return None
        if self.tree.identify_column(event.x) != "#1":
            return None
        item = self.tree.identify_row(event.y)
        row = self.visible_rows.get(item)
        if row is None:
            return None
        self.tree.selection_set(item)
        self.tree.focus(item)
        if row.location == "Active":
            key = self._path_key(row)
            if key in self.checked_paths:
                self.checked_paths.remove(key)
            else:
                self.checked_paths.add(key)
            self._render()
            self._update_action_states()
        return "break"

    def _show_row_context_menu(self, event) -> str | None:
        if self._operation_busy:
            return "break"
        item = self.tree.identify_row(event.y)
        row = self.visible_rows.get(item)
        if row is None:
            return None
        selection = context_selection(item, self.tree.selection())
        self.tree.selection_set(selection)
        self.tree.focus(item)
        selected_rows = [self.visible_rows[iid] for iid in selection if iid in self.visible_rows]
        actions = context_actions(selected.location for selected in selected_rows)
        menu = self.row_menu
        menu.delete(0, "end")
        if "toggle_move" in actions:
            selected_keys = {self._path_key(selected) for selected in selected_rows}
            all_checked = all(key in self.checked_paths for key in selected_keys)
            check_label = (
                "Clear move checks for selected active songs"
                if all_checked
                else "Check selected active songs for move"
            )
            menu.add_command(
                label=check_label,
                command=lambda rows=tuple(selected_rows): self._toggle_context_move_checks(rows),
            )
            menu.add_command(
                label=f"Move selected active song(s) to Do Not Want ({len(selected_rows)})",
                command=lambda rows=tuple(selected_rows): self._move_active_rows(list(rows), origin="selected"),
            )
        elif "restore" in actions:
            menu.add_command(label="Restore selected Do Not Want song(s)", command=self._restore)
        elif len({selected.location for selected in selected_rows}) > 1:
            menu.add_command(
                label="Select only Active or only Do Not Want songs for move/restore",
                state="disabled",
            )
        menu.add_separator()
        selected_snapshot = tuple(selected_rows)
        menu.add_command(
            label=f"Add selected track(s) to favorites ({len(selected_snapshot)} archive(s))",
            command=lambda rows=selected_snapshot: self._update_selected_favorites(True, rows),
        )
        menu.add_command(
            label=f"Remove selected track(s) from favorites ({len(selected_snapshot)} archive(s))",
            command=lambda rows=selected_snapshot: self._update_selected_favorites(False, rows),
        )
        menu.add_separator()
        detail_track_count = len(metadata_song_records(self.metadata.get(row.path)))
        menu.add_command(
            label="Archive details...",
            command=lambda selected=row: self._show_archive_details(selected),
            state="normal" if detail_track_count > 1 else "disabled",
        )
        if len(selected_rows) == 1:
            split_reason = split_pack_unavailable_reason(self.metadata.get(row.path))
            menu.add_command(
                label="Split up song pack...",
                command=lambda selected=row: self._open_pack_split_dialog(
                    self, selected, self.metadata.get(selected.path)
                ),
                state="normal" if split_reason is None else "disabled",
            )
        else:
            menu.add_command(
                label="Split up song pack (select one multi-song archive)",
                state="disabled",
            )
        menu.add_command(label="Preview this archive (30 sec)", command=lambda selected=row: self._preview_row(selected))
        menu.add_command(label="Open containing folder", command=lambda selected=row: self._open_song_folder(selected))
        menu.add_command(label="Copy archive path", command=lambda selected=row: self._copy_song_path(selected))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                menu.grab_release()
            except tk.TclError:
                pass
        return "break"

    def _open_archive_details_event(self, event):
        if self._operation_busy:
            return "break"
        item = self.tree.identify_row(event.y)
        row = self.visible_rows.get(item)
        if row is not None:
            if len(metadata_song_records(self.metadata.get(row.path))) > 1:
                self._show_archive_details(row)
            return "break"
        return None

    def _split_pack_selected(self) -> None:
        if self._operation_busy:
            return
        selected = [
            self.visible_rows[item]
            for item in self.tree.selection()
            if item in self.visible_rows
        ]
        if len(selected) != 1:
            messagebox.showinfo(
                "Split up song pack",
                "Select exactly one multi-song archive. Split action handles one PSARC at a time.",
                parent=self,
            )
            return
        row = selected[0]
        metadata = self.metadata.get(row.path)
        reason = split_pack_unavailable_reason(metadata)
        if reason is not None:
            self.split_pack_status_var.set(reason)
            return
        self._open_pack_split_dialog(self, row, metadata)

    def _archive_details_selected(self) -> None:
        selected = [self.visible_rows[item] for item in self.tree.selection() if item in self.visible_rows]
        if len(selected) != 1:
            messagebox.showinfo("Archive details", "Select exactly one archive row to inspect its tracks.")
            return
        self._show_archive_details(selected[0])

    def _current_detail_favorite_keys(self):
        favorite_result = self.favorite_result
        if favorite_result is None or not favorite_result.available:
            return None
        try:
            if self.favorite_profile_signature is None or self.favorite_profile_signature != self._profile_signature():
                return None
        except (OSError, ValueError, StoreError):
            return None
        return favorite_result.favorite_song_keys

    def _show_archive_details(self, row: SongFile) -> None:
        metadata = self.metadata.get(row.path)
        if metadata is None:
            messagebox.showinfo(
                "Archive details",
                f"No current title metadata is available for:\n{row.path}\n\n"
                "Run Full refresh, then open Archive details again.",
            )
            return
        favorite_keys = self._current_detail_favorite_keys()
        tracks = archive_track_rows(metadata, favorite_keys)
        if len(tracks) <= 1:
            return
        detail = tk.Toplevel(self)
        detail.title(f"Pack details — {row.path.name}")
        detail.transient(self)
        detail.geometry("900x620")
        detail.minsize(680, 440)
        detail.columnconfigure(0, weight=1)
        detail.rowconfigure(2, weight=1)
        profile_note = (
            "Favorite status uses the current selected profile."
            if favorite_keys is not None
            else "Favorite status is Unknown until the selected profile is freshly verified."
        )
        summary = (
            f"Archive: {row.relative_path}\n"
            f"Location: {row.location} | {len(tracks)} metadata-listed song record(s)\n"
            f"File: {row.path}\n"
            "This is one PSARC file. Move and restore actions affect the entire archive; "
            "selected songs can be copied to individual PSARCs when their audio is embedded. "
            "The source pack stays untouched; split files are not installed or deleted.\n"
            f"{profile_note}"
        )
        ttk.Label(detail, text=summary, justify="left", wraplength=860, padding=10).grid(
            row=0, column=0, sticky="ew"
        )
        warnings = metadata.get("warnings", []) if isinstance(metadata, dict) else []
        notices = []
        if isinstance(warnings, list) and warnings:
            warning_text = "; ".join(str(value) for value in warnings[:5])
            if len(warnings) > 5:
                warning_text += f"; and {len(warnings) - 5} more"
            notices.append(f"Metadata warnings: {warning_text}")
        else:
            notices.append("Metadata warnings: none reported.")
        repeated_keys = duplicate_song_keys(metadata)
        if repeated_keys:
            repeated_text = ", ".join(f"{key} ({count} records)" for key, count in repeated_keys[:8])
            if len(repeated_keys) > 8:
                repeated_text += f"; and {len(repeated_keys) - 8} more"
            notices.append(f"Repeated profile SongKeys share one favorite status: {repeated_text}")
        ttk.Label(detail, text="\n".join(notices), wraplength=860,
                  foreground="#8a4b08" if repeated_keys or warnings else "#333333",
                  padding=(10, 0, 10, 6)).grid(row=1, column=0, sticky="ew")
        list_frame = ttk.Frame(detail, padding=(10, 0, 10, 0))
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.rowconfigure(1, weight=1)
        list_frame.columnconfigure(0, weight=1)
        search_frame = ttk.Frame(list_frame)
        search_frame.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        ttk.Label(search_frame, text="Search tracks").pack(side="left", padx=(0, 8))
        search_var = tk.StringVar(master=detail)
        ttk.Entry(search_frame, textvariable=search_var).pack(side="left", fill="x", expand=True)
        tree = ttk.Treeview(
            list_frame,
            columns=("artist", "title", "favorite", "song_key"),
            show="headings",
            selectmode="browse",
        )
        for column, title, width in (
            ("artist", "Artist", 220),
            ("title", "Song name", 330),
            ("favorite", "Favorite status", 130),
            ("song_key", "Profile SongKey", 200),
        ):
            tree.heading(column, text=title)
            tree.column(column, width=width, stretch=column in ("artist", "title"), anchor="w")
        tree.grid(row=1, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
        vertical.grid(row=1, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(list_frame, orient="horizontal", command=tree.xview)
        horizontal.grid(row=2, column=0, sticky="ew")
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)

        def populate(*_args) -> None:
            term = search_var.get().casefold().strip()
            tree.delete(*tree.get_children())
            if not tracks and not term:
                tree.insert("", "end", iid="no_records", values=(
                    "Unknown", "No metadata-listed song records found", "Unknown", "",
                ))
                return
            for index, track in enumerate(tracks):
                searchable = f"{track.artist} {track.title} {track.song_key}".casefold()
                if term and term not in searchable:
                    continue
                tree.insert("", "end", iid=str(index), values=(
                    track.artist, track.title, track.favorite_status, track.song_key,
                ))

        search_var.trace_add("write", populate)
        populate()
        buttons = ttk.Frame(detail, padding=(10, 8))
        buttons.grid(row=3, column=0, sticky="ew")
        ttk.Button(buttons, text="Open containing folder", command=lambda: self._open_song_folder(row)).pack(side="left")
        if len(tracks) > 1:
            try:
                validate_split_metadata(metadata)
                split_state = "normal"
                split_note = ""
            except SplitPackError as exc:
                split_state = "disabled"
                split_note = str(exc)
            ttk.Button(
                buttons, text="Split up song pack…",
                command=lambda: self._open_pack_split_dialog(detail, row, metadata),
                state=split_state,
            ).pack(side="left", padx=(8, 0))
            if split_note:
                ttk.Label(buttons, text=split_note, wraplength=420, foreground="#8a4b08").pack(side="left", padx=8)
        ttk.Button(buttons, text="Close", command=detail.destroy).pack(side="right")

    def _open_pack_split_dialog(self, owner, row: SongFile, metadata: dict) -> None:
        if self._operation_busy:
            return
        try:
            songs = validate_split_metadata(metadata)
        except SplitPackError as exc:
            messagebox.showerror("Split up song pack", str(exc), parent=owner)
            return
        dialog = tk.Toplevel(owner)
        dialog.title(f"Split up song pack — {row.path.name}")
        dialog.transient(owner)
        dialog.geometry("960x650")
        dialog.minsize(720, 480)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(2, weight=1)
        ttk.Label(
            dialog,
            text=(
                f"Source archive: {row.path}\n"
                f"{len(songs)} tracks. Select one or more tracks with Ctrl/Shift-click, or use "
                "Select all songs to split the whole pack. Each selected track becomes its own PSARC "
                "in the configured Rocksmith folder's dlc directory. Select ALL tracks to replace the pack: "
                "verified singles are installed first, then the original moves to Do Not Want so duplicates do not remain. "
                "Missing required audio prevents splitting; it cannot reconstruct recordings. Restart Rocksmith afterward."
            ),
            wraplength=920,
            justify="left",
            padding=10,
        ).grid(row=0, column=0, sticky="ew")

        search_frame = ttk.Frame(dialog, padding=(10, 0, 10, 6))
        search_frame.grid(row=1, column=0, sticky="ew")
        search_frame.columnconfigure(1, weight=1)
        ttk.Label(search_frame, text="Search artist, title, or SongKey").grid(row=0, column=0, padx=(0, 8))
        search_var = tk.StringVar(master=dialog)
        ttk.Entry(search_frame, textvariable=search_var).grid(row=0, column=1, sticky="ew")

        table_frame = ttk.Frame(dialog, padding=(10, 0, 10, 0))
        table_frame.grid(row=2, column=0, sticky="nsew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(
            table_frame,
            columns=("artist", "title", "song_key"),
            show="headings",
            selectmode="extended",
        )
        for column, title, width in (
            ("artist", "Artist", 230),
            ("title", "Song name", 390),
            ("song_key", "SongKey", 250),
        ):
            tree.heading(column, text=title)
            tree.column(column, width=width, stretch=column in ("artist", "title"), anchor="w")
        tree.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=vertical.set)

        selection_var = tk.StringVar(master=dialog, value="")
        selected_indices: set[int] = set(range(len(songs)))
        syncing_selection = False
        split_button = None

        def refresh_selection_summary() -> None:
            selected_count = len(selected_indices)
            visible_indices = {int(item) for item in tree.get_children("")}
            hidden_count = len(selected_indices - visible_indices)
            if selected_count == 0:
                note = "Use Ctrl/Shift-click to select tracks, or Select all songs to split entire pack."
            elif selected_count == len(songs):
                note = "Whole pack selected; every track will get its own PSARC."
            elif hidden_count:
                note = f"{hidden_count} selected track(s) hidden by search remain selected."
            else:
                note = "Select all songs to replace the pack without losing any unselected tracks."
            selection_var.set(f"{selected_count} of {len(songs)} tracks selected. {note}")
            if split_button is not None:
                split_button.configure(
                    text=f"Replace pack with {len(songs)} individual songs",
                    state="normal" if selected_count == len(songs) else "disabled",
                )

        def refresh_rows(*_args) -> None:
            nonlocal syncing_selection
            term = search_var.get().casefold().strip()
            syncing_selection = True
            try:
                tree.delete(*tree.get_children(""))
                visible_ids = []
                for index, song in enumerate(songs):
                    searchable = " ".join(
                        str(song.get(key) or "")
                        for key in ("artist", "title", "song_key")
                    ).casefold()
                    if term and term not in searchable:
                        continue
                    item = str(index)
                    visible_ids.append(item)
                    tree.insert(
                        "",
                        "end",
                        iid=item,
                        values=(song["artist"], song["title"], song["song_key"]),
                    )
                selected_visible = [
                    item for item in visible_ids if int(item) in selected_indices
                ]
                if selected_visible:
                    tree.selection_set(*selected_visible)
                else:
                    tree.selection_remove(*tree.selection())
            finally:
                syncing_selection = False
            refresh_selection_summary()

        def track_selection_changed(_event=None) -> None:
            nonlocal selected_indices
            if syncing_selection:
                return
            visible = {int(item) for item in tree.get_children("")}
            selected_visible = {int(item) for item in tree.selection()}
            try:
                selected_indices = update_visible_song_selection(
                    selected_indices,
                    visible,
                    selected_visible,
                )
            except SplitPackError as exc:
                logging.exception("Could not reconcile selected pack tracks")
                selection_var.set(str(exc))
                return
            refresh_selection_summary()

        def select_all_songs() -> None:
            selected_indices.clear()
            selected_indices.update(range(len(songs)))
            refresh_rows()

        def clear_selection() -> None:
            selected_indices.clear()
            refresh_rows()

        tree.bind("<<TreeviewSelect>>", track_selection_changed, add="+")
        search_var.trace_add("write", refresh_rows)

        output_frame = ttk.Frame(dialog, padding=(10, 8, 10, 0))
        output_frame.grid(row=3, column=0, sticky="ew")
        output_frame.columnconfigure(0, weight=1)
        configured_game_directory = Path(self.game_var.get()).expanduser()
        configured_dlc_directory = configured_game_directory / "dlc"
        ttk.Label(
            output_frame,
            text=(
                f"Install destination: {configured_dlc_directory}\n"
                "This follows the Rocksmith 2014 folder configured at the top of the app; "
                "set that folder to use a different Steam library or Windows account."
            ),
            wraplength=900,
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            output_frame,
            textvariable=selection_var,
            padding=(0, 8, 0, 0),
            wraplength=900,
            justify="left",
        ).grid(row=1, column=0, sticky="w")

        footer = ttk.Frame(dialog, padding=10)
        footer.grid(row=4, column=0, sticky="ew")
        ttk.Button(footer, text="Select all songs", command=select_all_songs).pack(side="left")
        ttk.Button(footer, text="Clear selection", command=clear_selection).pack(side="left", padx=6)

        def start_split() -> None:
            if self._operation_busy:
                return
            selected = tuple(sorted(selected_indices))
            try:
                selected_song_payload(songs, selected)
            except SplitPackError as exc:
                messagebox.showinfo("Split up song pack", str(exc), parent=dialog)
                return
            if len(selected) != len(songs):
                messagebox.showinfo("Whole pack required", "Select all songs so the original pack can be backed up without losing tracks.", parent=dialog)
                return
            source = row.path
            try:
                if game_is_running():
                    messagebox.showinfo("Close Rocksmith", "Close Rocksmith before replacing a pack with individual songs. Restart after splitting.", parent=dialog)
                    return
                review_root = self._normalized_review_root()
                game_root = Path(self.game_var.get()).expanduser().resolve(strict=True)
                destination = resolve_rocksmith_dlc_directory(game_root)
            except (OSError, RuntimeError, SplitPackError) as exc:
                messagebox.showerror("Rocksmith DLC folder", str(exc), parent=dialog)
                return
            confirmed = messagebox.askyesno(
                "Install split songs",
                f"Create and install {len(selected)} individual song PSARC file(s) directly in:\n\n"
                f"{destination}\n\n"
                f"After every replacement is verified, move the original pack to:\n{review_root / 'songs' / row.relative_path}\n\n"
                "Every song in the pack will remain available as one individual PSARC. "
                "Existing files will not be overwritten. Restart Rocksmith after splitting. Continue?",
                parent=dialog,
            )
            if not confirmed:
                return
            dialog.destroy()
            self._show_split_progress_dialog(destination, len(selected))

            def worker():
                toolkit = find_toolkit_path(game_root)
                return split_and_install_pack(
                    source,
                    metadata,
                    selected,
                    toolkit_directory=toolkit,
                    game_directory=game_root,
                    review_directory=review_root,
                    progress=lambda text: self.events.put(("progress", "Splitting song pack", text)),
                )

            self._run("Splitting song pack", worker)

        split_button = ttk.Button(
            footer,
            text="Split and install selected songs",
            command=start_split,
            state="disabled",
        )
        split_button.pack(side="right")
        ttk.Button(footer, text="Cancel", command=dialog.destroy).pack(side="right", padx=(0, 8))
        refresh_rows()

    def _show_split_progress_dialog(self, destination: Path, selected_count: int) -> None:
        self._close_split_progress_dialog()
        window = tk.Toplevel(self)
        window.title("Splitting and installing songs")
        window.transient(self)
        window.resizable(False, False)
        window.geometry("620x220")
        window.columnconfigure(0, weight=1)
        ttk.Label(
            window,
            text=(
                f"Working on {selected_count} selected song(s). The app is building, verifying, "
                "and installing each PSARC. This can take a few minutes."
            ),
            wraplength=580,
            justify="left",
            padding=(14, 14, 14, 8),
        ).grid(row=0, column=0, sticky="ew")
        self.split_progress_status_var = tk.StringVar(
            master=window,
            value="Preparing temporary split workspace...",
        )
        ttk.Label(
            window,
            textvariable=self.split_progress_status_var,
            wraplength=580,
            justify="left",
            padding=(14, 4),
        ).grid(row=1, column=0, sticky="ew")
        self.split_progress_bar = ttk.Progressbar(
            window,
            mode="indeterminate",
            length=560,
        )
        self.split_progress_bar.grid(row=2, column=0, sticky="ew", padx=14, pady=8)
        self.split_progress_bar.start(12)
        ttk.Label(
            window,
            text=f"Destination: {destination}\nRocksmith may need a restart before new songs appear.",
            wraplength=580,
            justify="left",
            padding=(14, 4, 14, 8),
        ).grid(row=3, column=0, sticky="ew")
        ttk.Button(
            window,
            text="Hide progress (work continues)",
            command=window.withdraw,
        ).grid(row=4, column=0, sticky="e", padx=14, pady=(0, 12))
        window.protocol("WM_DELETE_WINDOW", window.withdraw)
        self.split_progress_dialog = window
        window.lift()

    def _update_split_progress_dialog(self, text: str) -> None:
        if self.split_progress_dialog is None or self.split_progress_status_var is None:
            return
        try:
            if self.split_progress_dialog.winfo_exists():
                self.split_progress_status_var.set(text)
        except tk.TclError:
            self._close_split_progress_dialog()

    def _close_split_progress_dialog(self) -> None:
        if self.split_progress_bar is not None:
            try:
                self.split_progress_bar.stop()
            except tk.TclError:
                pass
        if self.split_progress_dialog is not None:
            try:
                self.split_progress_dialog.destroy()
            except tk.TclError:
                pass
        self.split_progress_dialog = None
        self.split_progress_status_var = None
        self.split_progress_bar = None

    def _toggle_context_move_checks(self, rows: tuple[SongFile, ...]) -> None:
        active = [row for row in rows if row.location == "Active"]
        keys = [self._path_key(row) for row in active]
        if keys and all(key in self.checked_paths for key in keys):
            self.checked_paths.difference_update(keys)
        else:
            self.checked_paths.update(keys)
        self._render()
        self._update_action_states()

    def _open_song_folder(self, row: SongFile) -> None:
        if not row.path.is_file():
            messagebox.showwarning("Song location", f"Archive is no longer present at:\n{row.path}\n\nRun Full refresh.")
            return
        try:
            os.startfile(str(row.path.parent))
        except (AttributeError, OSError) as exc:
            logging.exception("Could not open song containing folder: %s", row.path)
            messagebox.showerror("Song location", f"Could not open this folder:\n{row.path.parent}\n\n{exc}")

    def _copy_song_path(self, row: SongFile) -> None:
        self.clipboard_clear()
        self.clipboard_append(str(row.path))
        self.update()
        self.status_var.set(f"Copied archive path: {row.path}")

    def _check_visible_active(self) -> None:
        for row in self.visible_rows.values():
            if row.location == "Active":
                self.checked_paths.add(self._path_key(row))
        self._render()
        self._update_action_states()

    def _uncheck_favorited_archives(self) -> None:
        if not (
            self.favorite_result is not None
            and self.favorite_result.available
            and self.favorite_library_signature is not None
            and self.favorite_profile_signature == self._profile_signature()
        ):
            messagebox.showinfo(
                "Uncheck favorite archives",
                "Run Scan favorites first. No song checks were changed.", parent=self,
            )
            return
        favorite_paths = {
            self._path_key(row)
            for row in self.rows
            if row.location == "Active" and self.favorite_states.get(row.relative_path) == "Favorite"
        }
        changed = len(self.checked_paths.intersection(favorite_paths))
        self.checked_paths.difference_update(favorite_paths)
        self._render()
        self._update_action_states()
        self.status_var.set(
            f"Cleared removal checks for {changed} favorited archive(s). Profile favorites and song files were unchanged."
        )

    def _clear_move_checks(self) -> None:
        self.checked_paths.clear()
        self._render()
        self._update_action_states()

    def _run(self, description: str, worker) -> None:
        if self._operation_busy:
            return
        self._operation_busy = True
        self.status_var.set(description)
        self.progress.pack(fill="x", padx=12)
        self.progress.start(12)
        for button in self.path_controls + self.toolbar_buttons + self.operation_buttons:
            button.configure(state="disabled")
        for button in self.action_buttons:
            button.configure(state="disabled")

        def task() -> None:
            try:
                result = worker()
                _validate_worker_result(description, result)
                self.events.put(("done", description, result))
            except Exception as exc:
                logging.exception("%s failed", description)
                if isinstance(exc, PreviewError) and exc.diagnostics:
                    logging.error("Preview diagnostics for %s:\n%s", exc.archive, exc.diagnostics)
                self.events.put(("error", description, str(exc)))

        threading.Thread(target=task, daemon=True).start()

    def _check_pending_move_queue(self) -> None:
        if not self.pending_move_check_active:
            self.pending_move_check_active = True

            def worker() -> None:
                try:
                    pending = load_pending_moves(PENDING_MOVE_QUEUE_PATH)
                    game_running = game_is_running() if pending else False
                    result = (
                        len(pending), game_running, None, None,
                        tuple(item.relative_path.casefold() for item in pending),
                    )
                except Exception as exc:
                    result = (None, None, str(exc), traceback.format_exc(), ())
                self.events.put(("pending_moves_check", "Checking queued removals", result))

            threading.Thread(target=worker, daemon=True).start()
        self.after(5000, self._check_pending_move_queue)

    def _show_in_game_removal_help(self) -> None:
        status = support_status(Path(self.game_var.get()))
        messagebox.showinfo(
            "In-game removal: F10 in the song list",
            "1. Install the compatible Jons native extension while Rocksmith is closed, then restart Rocksmith.\n"
            "2. In Learn a Song, wait for the selected song preview. F10 hint appears only while a live preview is selected.\n"
            "3. Press the configured mark key (default F10). After Rocksmith closes, this app automatically moves a verified single-song archive to Do Not Want. Keep this app open, or reopen it after the game exits.\n"
            "4. Use Undo last completed removal to restore a moved archive. A multi-song pack is never moved from one F10 mark; split it into single-song files first, then retry the saved mark.\n\n"
            "No archive moves while Rocksmith runs. F10 identifies the current preview, not an arrangement already playing. If the mark cannot be matched safely, it stays saved and the app explains how to retry.\n\n"
            "Public downloads do not bundle the optional modified RSMods DLL because upstream does not state a redistribution license. The local build instructions are in native/README.md in the developer repository. Python and EXE app workflows both work without that optional extension for manual song selection.\n\n"
            f"Native status: {status}\nRunning app: {APP_VERSION}", parent=self,
        )

    def _open_song_health(self) -> None:
        window = self.health_window
        if window is not None and window.winfo_exists():
            window.lift()
            return
        if self._operation_busy:
            return
        rows = [row for row in self.rows if row.location == "Active"]
        if not rows:
            messagebox.showinfo("Song health", "Run Full refresh to load active archives first.", parent=self)
            return
        try:
            toolkit = find_toolkit_path(Path(self.game_var.get()))
            from library_health_view import LibraryHealthWindow
            self.health_window = LibraryHealthWindow(self, rows, toolkit, self._queue_health_rows, LOG_PATH)
        except Exception as exc:
            logging.exception("Could not open song health review")
            messagebox.showerror("Song health", f"{exc}\n\nDetails: {LOG_PATH}", parent=self)

    def _queue_health_rows(self, rows) -> None:
        if self._operation_busy:
            raise StoreError("Another operation is running. Wait, then queue again.")
        store = self._store()
        candidates = [row for row in rows if row.location == "Active"]
        if not candidates:
            raise StoreError("Select an active archive first.")
        if not self._confirm_archive_moves(
            "Review health candidates for reversible removal", candidates, store.songs_root,
            context="Health checks do not prove playback failure. Review every track: this moves complete archives to Do Not Want.",
            queue_after_close=True, show_all_tracks=True,
        ):
            return
        self._run("Queueing selected archives", lambda: queue_song_moves(PENDING_MOVE_QUEUE_PATH, store, candidates))

    def _forget_saved_game_marks(self) -> None:
        if self._operation_busy or self.active_game_song_request is not None:
            return
        try:
            batch = load_pending_game_song_requests()
            if not batch.requests:
                messagebox.showinfo("Saved in-game marks", "No saved marks.", parent=self)
                return
            keys = "\n".join(request.song_key for request in batch.requests)
            if not messagebox.askyesno(
                "Forget saved marks",
                f"Forget these {len(batch.requests)} marks? Song files and confirmed removal queues are unchanged.\n\n{keys}", parent=self,
            ):
                return
            for request in batch.requests:
                discard_game_song_request(request)
            self.deferred_game_song_requests.clear()
            self.pending_game_song_keys.clear()
            self._render()
            self.pending_moves_status_var.set("Saved marks forgotten. Song files and confirmed queues are unchanged.")
        except StoreError as exc:
            logging.exception("Could not forget saved marks")
            messagebox.showerror("Saved in-game marks", str(exc), parent=self)

    def _review_saved_game_marks(self) -> None:
        if game_is_running():
            messagebox.showinfo("Saved in-game marks", "Marks are saved. Close Rocksmith before reviewing removals.", parent=self)
            return
        self.deferred_game_song_requests.clear()
        self._check_in_game_song_requests(schedule=False)

    def _check_in_game_song_requests(self, *, schedule: bool = True) -> None:
        try:
            if self._operation_busy or self.active_game_song_request is not None:
                return
            batch = load_pending_game_song_requests()
            pending_keys = {request.song_key.casefold() for request in batch.requests}
            previous_pending_keys = self.__dict__.get("pending_game_song_keys")
            if previous_pending_keys is None:
                self.pending_game_song_keys = pending_keys
            elif pending_keys != previous_pending_keys:
                self.pending_game_song_keys = pending_keys
                self._render()
            if batch.expired_count:
                logging.info("Ignored %s expired in-game song request(s)", batch.expired_count)
                self.pending_moves_status_var.set(
                    f"Ignored {batch.expired_count} expired in-game song request(s); no archives changed."
                )
            if batch.invalid_count:
                logging.warning("Discarded %s malformed in-game song request(s)", batch.invalid_count)
            if batch.requests and game_is_running():
                self.pending_moves_status_var.set(
                    f"{len(batch.requests)} F10 mark(s) saved. Keep playing; verified single-song archives move after Rocksmith exits."
                )
                return
            eligible = [request for request in batch.requests
                        if request.request_id not in self.deferred_game_song_requests]
            if eligible and not self.rows:
                self.pending_moves_status_var.set("F10 marks saved. Run Full refresh to identify their archives; no files moved.")
                return
            if eligible:
                request = eligible[0]
                self.active_game_song_request = request
                rows_snapshot = tuple(self.rows)
                self._run(
                    "Resolving in-game song selection",
                    lambda: resolve_active_archive_for_song_key(
                        rows_snapshot, request.song_key, self.metadata.get,
                        require_single_song_archive=True,
                    ),
                )
        except StoreError as exc:
            error = str(exc)
            if error != self.game_song_request_error:
                logging.error("Could not read in-game song request: %s", error, exc_info=True)
            self.game_song_request_error = error
            self.pending_moves_status_var.set(
                f"In-game song request unavailable; no archive changed. Details: {LOG_PATH}"
            )
        else:
            self.game_song_request_error = None
        finally:
            if schedule:
                self.after(1000, self._check_in_game_song_requests)

    def _finish_in_game_song_request(self, message: str, *, show_warning: bool = False) -> None:
        request = self.active_game_song_request
        if request is not None:
            self.deferred_game_song_requests.add(request.request_id)
            message += "\nMark remains saved. Use Review saved in-game marks to retry."
        self.active_game_song_request = None
        self.pending_moves_status_var.set(message)
        if show_warning:
            messagebox.showwarning("In-game song removal", message, parent=self)

    def _queue_in_game_song_archive(self, row: SongFile) -> None:
        request = self.active_game_song_request
        if request is None:
            return
        if game_is_running():
            self._finish_in_game_song_request(
                "Rocksmith started again before removal. No archive moved; close the game and retry the saved F10 mark."
            )
            return
        try:
            store = self._store()
        except StoreError as exc:
            self._finish_in_game_song_request(
                f"In-game song removal refused: {exc}. No archive was queued.", show_warning=True
            )
            return
        self._run(
            "Queueing in-game selected archive",
            lambda: queue_song_moves(PENDING_MOVE_QUEUE_PATH, store, [row]),
        )

    def _process_pending_move_queue(self) -> None:
        if (
            self._operation_busy or self.active_game_song_request is not None
            or self.pending_move_retry_paused or not self.pending_move_count
        ):
            return

        def worker() -> PendingMoveResult:
            result = process_pending_moves(PENDING_MOVE_QUEUE_PATH)
            try:
                for source, target in result.moved_paths:
                    self.metadata.relocate(source, target, save=False)
                if result.moved_paths:
                    self.metadata.save()
            except Exception:
                logging.exception("Queued archives moved, but metadata cache relocation failed")
            return result

        self._run("Processing queued song removals", worker)

    def _queue_checked_after_close(self) -> None:
        candidates = self._checked_active()
        if not candidates:
            messagebox.showwarning("Queue song removal", "Check one or more active archive rows first.")
            return
        try:
            store = self._store()
        except StoreError as exc:
            messagebox.showwarning("Queue song removal", str(exc))
            return
        if not self._confirm_archive_moves(
            "Queue archives for removal after Rocksmith closes",
            candidates,
            store.songs_root,
            context=(
                "The selected archives are queued now and will move only after Rocksmith is confirmed closed. "
                "If Rocksmith is already closed, the queue will process shortly."
            ),
            queue_after_close=True,
        ):
            self.status_var.set("Queue cancelled; no archives were changed.")
            return

        def worker() -> int:
            return queue_song_moves(PENDING_MOVE_QUEUE_PATH, store, candidates)

        self._run("Queueing selected archives", worker)

    def _cancel_queued_moves(self) -> None:
        if not self.pending_move_count:
            return
        if not messagebox.askyesno(
            "Cancel queued removals",
            f"Cancel the {self.pending_move_count} remaining queued archive move(s)? "
            "Already moved archives stay in Do Not Want and can be restored.",
        ):
            return
        self._run("Canceling queued song removals", lambda: cancel_pending_moves(PENDING_MOVE_QUEUE_PATH))

    def _retry_queued_moves(self) -> None:
        if not self.pending_move_count:
            return
        self.pending_move_retry_paused = False
        self.pending_moves_status_var.set("Retrying queued removals; Rocksmith must be closed.")
        self._process_pending_move_queue()

    def _undo_last_completed_removal(self) -> None:
        if self._operation_busy:
            return
        try:
            items = load_last_completed_moves(LAST_COMPLETED_MOVE_PATH)
        except StoreError as exc:
            messagebox.showerror("Undo last removal", f"Undo history could not be verified. No files changed.\n\n{exc}")
            return
        if not items:
            messagebox.showinfo(
                "Undo last removal",
                "No completed queued removal is available. Use Undo queued removals before queued files move, "
                "or select a Do Not Want archive and choose Restore selected archives.",
            )
            return
        names = "\n".join(f"- {item.relative_path}" for item in items[:12])
        if len(items) > 12:
            names += f"\n...and {len(items) - 12} more archive(s)"
        if not messagebox.askyesno(
            "Undo last completed removal",
            f"Restore {len(items)} archive(s) from Do Not Want to active DLC?\n\n{names}\n\n"
            "Rocksmith must be closed. Undo verifies each archive has not changed. Nothing is deleted.",
        ):
            return

        game_root = Path(self.game_var.get()).expanduser()
        review_root = Path(self.review_var.get()).expanduser()

        def worker() -> PendingMoveResult:
            store = SongStore(game_root, review_root, game_is_running)
            result = undo_last_completed_moves(LAST_COMPLETED_MOVE_PATH, store)
            for source, target in result.moved_paths:
                self.metadata.relocate(source, target, save=False)
            self.metadata.save()
            return result

        self._run("Undoing last completed removal", worker)

    def _poll(self) -> None:
        try:
            while True:
                kind, description, result = self.events.get_nowait()
                if kind == "pending_moves_check":
                    self.pending_move_check_active = False
                    count, game_running, error, error_details, pending_paths = result
                    if error:
                        self.pending_moves_status_var.set(
                            f"Cannot check queued removals; no archives moved. Details: {LOG_PATH}"
                        )
                        if error != self.pending_move_check_error:
                            logging.error(
                                "Queued-removal process check failed: %s\n%s", error, error_details
                            )
                        self.pending_move_check_error = error
                    else:
                        self.pending_move_check_error = None
                        old_paths = self.pending_move_paths
                        self.pending_move_paths = set(pending_paths)
                        self.pending_move_count = count
                        if old_paths != self.pending_move_paths:
                            self._render()
                        if count == 0:
                            self.pending_move_retry_paused = False
                            self.pending_moves_status_var.set("No queued removals.")
                        elif self.pending_move_retry_paused:
                            self.pending_moves_status_var.set(
                                f"{count} archive(s) remain queued. Fix the issue, then choose Retry or Cancel."
                            )
                        elif game_running:
                            self.pending_moves_status_var.set(
                                f"{count} archive(s) queued. They move after Rocksmith closes; keep app open or reopen it later."
                            )
                        elif self.active_game_song_request is not None:
                            self.pending_moves_status_var.set(
                                f"{count} archive(s) queued. Confirm or cancel in-game selection first."
                            )
                        elif self._operation_busy:
                            self.pending_moves_status_var.set(
                                f"Rocksmith is closed. {count} queued archive(s) will move after the current app task."
                            )
                        else:
                            self.pending_moves_status_var.set(
                                f"Rocksmith is closed. Moving {count} queued archive(s) to Do Not Want."
                            )
                            self._process_pending_move_queue()
                    self._update_action_states()
                    continue
                if kind == "progress":
                    if description == "Splitting song pack":
                        self._update_split_progress_dialog(str(result))
                    self.status_var.set(f"{description}: {result}")
                    continue
                if kind == "done":
                    try:
                        _validate_worker_result(description, result)
                    except StoreError as exc:
                        logging.error("Invalid worker result for %s: %s", description, exc)
                        kind, result = "error", str(exc)
                self._operation_busy = False
                self.progress.stop()
                self.progress.pack_forget()
                for button in self.path_controls + self.toolbar_buttons + self.operation_buttons:
                    button.configure(state="normal")
                self._update_action_states()
                if description == "Splitting song pack":
                    self._close_split_progress_dialog()
                if kind == "error":
                    self.status_var.set(f"{description} failed: {result}. Details: {LOG_PATH}")
                    if description == "Resolving in-game song selection":
                        self._finish_in_game_song_request(
                            f"No safe archive match: {result}. No archive was queued. "
                            "Run Full refresh, fix the issue shown, then retry the saved F10 mark.",
                            show_warning=True,
                        )
                        self._update_action_states()
                        continue
                    if description == "Queueing in-game selected archive":
                        self._finish_in_game_song_request(
                            f"Could not save removal queue: {result}. No request was discarded.", show_warning=True
                        )
                    if description == "Processing queued song removals":
                        self.pending_move_retry_paused = True
                        try:
                            self.pending_move_count = len(load_pending_moves(PENDING_MOVE_QUEUE_PATH))
                        except StoreError:
                            pass
                        self.pending_moves_status_var.set(
                            f"Queued move stopped safely: {result}. Queue remains saved; Retry or Cancel."
                        )
                        self._update_action_states()
                    elif description == "Canceling queued song removals":
                        self.pending_moves_status_var.set(f"Queue cancellation failed: {result}")
                    if description in ("Saving difficulty setting", "Saving selected song difficulty"):
                        overall = f"FAILED: overall difficulty apply did not complete.\n{result}"
                        self.last_difficulty_report = overall + f"\n\nLog: {LOG_PATH}"
                        self.difficulty_status_var.set(overall)
                    if description in ("Updating song favorites", "Unfavoriting all songs"):
                        self.favorite_write_status_var.set(f"Favorite update failed: {result}")
                    if description == "Saving game tuning settings":
                        self.tuning_status_var.set(f"Save failed: {result}")
                    if description == "Saving difficulty setting":
                        self._show_difficulty_report()
                    elif description not in ("Scanning songs", "Full library refresh"):
                        messagebox.showerror("JonsRockSmithMods", f"{description} failed:\n{result}\n\nDetails: {LOG_PATH}")
                    if description not in (
                        "Scanning songs", "Full library refresh", "Reading titles", "Scanning favorites",
                        "Preparing song preview", "Splitting song pack", "Resolving in-game song selection"
                    ):
                        self.refresh()
                    elif description == "Splitting song pack" and "Partial install:" in str(result):
                        self.refresh()
                    if description in ("Saving speed setting", "Installing speed support", "Restoring original RSMods"):
                        self.speed_status_var.set(support_status(Path(self.game_var.get())))
                    continue

                if description == "Scanning songs":
                    self.favorite_result = None
                    self.favorite_profile_signature = None
                    self.favorite_library_signature = None
                    self.favorite_states.clear()
                    self.rows = result
                    active_paths = {self._path_key(row) for row in result if row.location == "Active"}
                    self.checked_paths.intersection_update(active_paths)
                    self._render()
                    self.status_var.set(f"Found {len(result)} archives. Scan titles and favorites to review by song.")
                elif description == "Reading titles":
                    self._render()
                    self.status_var.set(result)
                elif description == "Full library refresh":
                    self.rows = result.rows
                    self.favorite_result = result.favorites
                    self.favorite_states = result.favorite_states
                    self.favorite_profile_signature = result.profile_signature
                    self.favorite_library_signature = result.library_signature
                    active_matches = sum(
                        row.location == "Active" and self.favorite_states.get(row.relative_path) == "Favorite"
                        for row in self.rows
                    )
                    active_nonfavorites = sum(
                        row.location == "Active" and self.favorite_states.get(row.relative_path) == "Not favorite"
                        for row in self.rows
                    )
                    active_unknown = sum(
                        row.location == "Active" and self.favorite_states.get(row.relative_path, "Unknown") == "Unknown"
                        for row in self.rows
                    )
                    do_not_want_count = sum(row.location == "Do Not Want" for row in self.rows)
                    if not result.favorites.available:
                        self.favorite_scan_summary = (
                            f"Favorites are Unknown ({result.favorites.diagnostics}). Automatic move is locked."
                        )
                    elif not result.favorites.favorite_song_keys:
                        self.favorite_scan_summary = (
                            f"Profile keys: 0; active favorite matches: {active_matches}; "
                            f"active confirmed non-favorites: {active_nonfavorites}; active unknown: {active_unknown}. "
                            "Automatic move is locked because the profile favorite list is empty."
                        )
                    elif active_matches == 0:
                        self.favorite_scan_summary = (
                            f"Profile keys: {len(result.favorites.favorite_song_keys)}; active favorite matches: 0; "
                            f"active confirmed non-favorites: {active_nonfavorites}; active unknown: {active_unknown}. "
                            "Automatic move is locked because no active favorite was verified."
                        )
                    else:
                        self.favorite_scan_summary = (
                            f"Profile keys: {len(result.favorites.favorite_song_keys)}; active favorite matches: "
                            f"{active_matches}; active confirmed non-favorites: {active_nonfavorites}; "
                            f"active unknown: {active_unknown}. Verified non-favorites can be reviewed for a reversible move."
                        )
                    self.checked_paths.intersection_update(
                        self._path_key(row) for row in self.rows if row.location == "Active"
                    )
                    self._render()
                    toolkit_note = (
                        f" Toolkit: {result.toolkit_path}." if result.toolkit_path
                        else " Toolkit lookup was not needed; cached titles were validated."
                    )
                    metadata_note = (
                        f" Metadata detail: {result.metadata_diagnostic}." if result.metadata_diagnostic else ""
                    )
                    self.status_var.set(
                        f"Full refresh complete: {len(self.rows)} archives; {result.metadata_ready}/{len(self.rows)} "
                        f"titles ready ({result.titles_read} newly read, {result.metadata_failures} errors); "
                        f"{do_not_want_count} in Do Not Want. {self.favorite_scan_summary} "
                        f"Destination: {self._store_destination_label()}.{toolkit_note}{metadata_note}"
                    )
                elif description == "Saving speed setting":
                    self.speed_status_var.set(support_status(Path(self.game_var.get())))
                    self.status_var.set("Speed setting saved. Restart Rocksmith to apply it.")
                elif description == "Saving game tuning settings":
                    self._load_tuning_settings()
                    backup = f" Verified backup: {result}" if result is not None else ""
                    self.tuning_status_var.set(
                        f"Game tuning settings saved. Restart Rocksmith to apply them.{backup}"
                    )
                    self.status_var.set("Game tuning settings saved. Restart Rocksmith to apply them.")
                elif description == "Installing speed support":
                    self.speed_status_var.set(support_status(Path(self.game_var.get())))
                    self.status_var.set(
                        "Jons RSMods extension is already current; installation is complete."
                        if result is None else
                        "Jons RSMods extension installed. Restart Rocksmith before testing."
                    )
                elif description == "Saving startup preference":
                    self._load_speed()
                    self.startup_status_var.set("Startup preference saved. Restart Rocksmith to apply.")
                    self.status_var.set("Startup preference saved. Restart Rocksmith to apply.")
                elif description == "Saving individual song speed":
                    self.status_var.set("Individual song speed saved. Jons extension reloads it within about one second.")
                elif description == "Restoring original RSMods":
                    self.speed_status_var.set(support_status(Path(self.game_var.get())))
                    self.status_var.set("Original RSMods restored.")
                elif description == "Saving difficulty setting":
                    update = result.update
                    if Path(self.profile_var.get()).resolve(strict=False) == update.profile_path:
                        self._load_profile_difficulty()
                    report = result.details() + "\nRestart Rocksmith to load saved changes."
                    self.last_difficulty_report = report
                    logging.info("Global difficulty outcome:\n%s", report)
                    self.difficulty_status_var.set(result.summary())
                    self.status_var.set(result.summary())
                    self._show_difficulty_report()
                elif description == "Saving selected song difficulty":
                    if Path(self.profile_var.get()).resolve(strict=False) == result.profile_path:
                        self._load_profile_difficulty()
                    skipped = len(result.unavailable_arrangement_ids)
                    if result.backup_path is None:
                        text = "Profile already matches these difficulty settings."
                    else:
                        text = (
                            f"Saved level {result.level}/10 for {len(result.changed_arrangement_ids)} arrangement(s), "
                            f"{result.changed_phrase_count} phrase(s). Restart Rocksmith. Backup: {result.backup_path}"
                        )
                    if skipped:
                        text += f" {skipped} unplayed arrangement(s) still use the global starting level."
                    self.difficulty_status_var.set(text)
                    self.status_var.set(text)
                elif description == "Queueing selected archives":
                    self.pending_move_count = result
                    self._refresh_pending_move_paths()
                    self.pending_move_retry_paused = False
                    self.pending_moves_status_var.set(
                        f"{result} archive(s) queued. They will move reversibly after Rocksmith closes."
                    )
                    self.status_var.set(
                        "Selected archives queued. Rocksmith stays untouched until its process closes."
                    )
                elif description == "Resolving in-game song selection":
                    self._queue_in_game_song_archive(result)
                elif description == "Queueing in-game selected archive":
                    request = self.active_game_song_request
                    if request is not None:
                        try:
                            discard_game_song_request(request)
                        except StoreError:
                            logging.exception("Queue saved but mark acknowledgement failed; mark retained")
                            self.deferred_game_song_requests.add(request.request_id)
                    self.active_game_song_request = None
                    self.pending_move_count = result
                    self._refresh_pending_move_paths()
                    self.pending_move_retry_paused = False
                    self.pending_moves_status_var.set(
                        "F10 mark matched. Single-song archive queued for automatic reversible move to Do Not Want."
                    )
                    self.status_var.set(
                        "F10 removal queued. Undo last completed removal restores the song."
                    )
                elif description == "Processing queued song removals":
                    if not isinstance(result, PendingMoveResult):
                        raise StoreError("Queued-removal worker returned an invalid result")
                    self.pending_move_count = result.remaining_count
                    self._refresh_pending_move_paths()
                    self.pending_move_retry_paused = False
                    self.checked_paths.clear()
                    self.pending_moves_status_var.set(
                        "No queued removals." if not result.remaining_count else
                        f"{result.remaining_count} archive(s) remain queued."
                    )
                    self.status_var.set(
                        f"Moved {result.moved_count} archive(s) to Do Not Want after Rocksmith closed. "
                        f"{result.completed_count} queue item(s) completed; nothing was deleted. Use Undo last completed removal to restore this batch."
                    )
                    self.refresh()
                elif description == "Undoing last completed removal":
                    if not isinstance(result, PendingMoveResult):
                        raise StoreError("Removal undo returned an invalid result")
                    self.pending_moves_status_var.set(
                        f"Undo complete: restored {result.moved_count} archive(s) to active DLC."
                    )
                    self.status_var.set(
                        f"Restored {result.moved_count} archive(s) to active DLC. No archives were deleted."
                    )
                    self.refresh()
                elif description == "Canceling queued song removals":
                    self.pending_move_count = 0
                    self.pending_move_paths.clear()
                    self._render()
                    self.pending_move_retry_paused = False
                    self.pending_moves_status_var.set("Queued removals cancelled; song files were untouched.")
                    self.status_var.set(f"Cancelled {result} queued archive move(s). No files were moved.")
                elif description == "Updating song favorites":
                    changed = len(result.changed_song_keys)
                    if result.backup_path is None:
                        state = "already favorites" if result.favorited else "not favorites"
                        self.favorite_write_status_var.set(
                            f"No profile change needed; selected tracks are {state}."
                        )
                    else:
                        action = "added to" if result.favorited else "removed from"
                        self.favorite_write_status_var.set(
                            f"{changed} track(s) {action} favorites. Verified backup: {result.backup_path}"
                        )
                    status = (
                        f"{changed} Rocksmith profile favorite(s) updated. Refreshing song list."
                        if changed else "No Rocksmith profile favorite changes were needed. Refreshing song list."
                    )
                    self.status_var.set(status)
                    self.refresh()
                elif description == "Unfavoriting all songs":
                    changed = len(result.changed_song_keys)
                    if result.backup_path is None:
                        self.favorite_write_status_var.set(
                            "No profile change needed; the favorite list was already empty."
                        )
                    else:
                        self.favorite_write_status_var.set(
                            f"Removed {changed} favorite entries. Verified backup: {result.backup_path}"
                        )
                    self.status_var.set(
                        f"Unfavorited {changed} Rocksmith profile entries. Refreshing song list."
                        if changed
                        else "No Rocksmith profile favorite changes were needed. Refreshing song list."
                    )
                    self.refresh()
                elif description == "Preparing song preview":
                    asset, label = result
                    try:
                        import winsound

                        winsound.PlaySound(str(asset.wav_path), winsound.SND_FILENAME | winsound.SND_ASYNC)
                    except Exception as exc:
                        cleanup_preview_directory(asset.working_directory)
                        logging.exception("Could not start in-app audio preview")
                        self.preview_status_var.set(f"Preview could not start: {exc}")
                        self.status_var.set(f"Preview failed: {exc}. Details: {LOG_PATH}")
                        messagebox.showerror("Song preview", f"Could not start audio playback:\n{exc}")
                        self._update_action_states()
                        continue
                    self.preview_working_directory = asset.working_directory
                    self.preview_active = True
                    self.preview_status_var.set(f"Playing 30-second preview: {label}")
                    self.status_var.set(f"Audio preview prepared from: {asset.wav_path}")
                    self._update_action_states()
                elif description == "Splitting song pack":
                    examples = "\n".join(f"{item.artist} - {item.title}" for item in result.outputs[:5])
                    more = f"\n…and {len(result.outputs) - 5} more" if len(result.outputs) > 5 else ""
                    self.status_var.set(
                        f"Installed {len(result.outputs)} verified song PSARC(s) in {result.output_directory}. "
                        "Restart Rocksmith to refresh the song list."
                    )
                    messagebox.showinfo(
                        "Song pack split and installed",
                        f"Installed {len(result.outputs)} verified individual PSARC file(s):\n{examples}{more}\n\n"
                        f"Rocksmith DLC folder:\n{result.output_directory}\n\n"
                        f"Source hash unchanged: {result.source_sha256}\n"
                        f"Original pack backup:\n{result.source_backup}\n\n"
                        "Restart Rocksmith to refresh its song list. Existing files were not overwritten.",
                        parent=self,
                    )
                    self.refresh()
                else:
                    self.status_var.set(f"{description}: {result}")
                    self.refresh()
                self._update_action_states()
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _render(self) -> None:
        term = self.filter_var.get().casefold().strip()
        pending_move_paths = self.__dict__.get("pending_move_paths", set())
        pending_game_song_keys = self.__dict__.get("pending_game_song_keys", set())
        selected_before = tuple(self.tree.selection())
        focus_before = self.tree.focus()
        try:
            scroll_position = self.tree.yview()[0]
        except (tk.TclError, IndexError):
            scroll_position = 0.0
        self.tree.delete(*self.tree.get_children())
        self.visible_rows.clear()
        for row in self.rows:
            metadata = self.metadata.get(row.path)
            artist, title = archive_table_values(metadata)
            favorite = self.favorite_states.get(row.relative_path, "Unknown")
            search_text = f"{row.relative_path} {archive_search_text(metadata)} {artist} {title}".casefold()
            if term and term not in search_text:
                continue
            item = self._path_key(row)
            checked = row.location == "Active" and self._path_key(row) in self.checked_paths
            f10_marked = False
            if row.location == "Active":
                if metadata:
                    metadata_song_keys = {
                        str(song.get("song_key") or "").strip().casefold()
                        for song in metadata_song_records(metadata)
                        if isinstance(song, dict) and str(song.get("song_key") or "").strip()
                    }
                    f10_marked = bool(metadata_song_keys.intersection(pending_game_song_keys))
            removal_status = removal_row_status(
                checked=checked,
                queued=row.relative_path.casefold() in pending_move_paths,
                f10_marked=f10_marked,
            )
            self.tree.insert("", "end", iid=item,
                             values=("☑" if checked else ("☐" if row.location == "Active" else ""),
                                     removal_status, row.location, artist or "Unknown", title or "Unknown", favorite,
                                     row.relative_path, f"{row.size / 1048576:.1f}"))
            self.visible_rows[item] = row
        self._apply_tree_sort()
        retained_selection = [item for item in selected_before if item in self.visible_rows]
        if retained_selection:
            self.tree.selection_set(retained_selection)
        if focus_before in self.visible_rows:
            self.tree.focus(focus_before)
        if scroll_position > 0:
            self.tree.yview_moveto(scroll_position)
        all_checked = sum(1 for row in self.rows
                          if row.location == "Active" and self._path_key(row) in self.checked_paths)
        self.checked_count_var.set(
            f"{all_checked} archives checked for removal; {len(pending_move_paths)} queued after close; "
            f"{len(pending_game_song_keys)} saved F10 mark(s)"
        )

    def _refresh_pending_move_paths(self) -> None:
        try:
            pending = load_pending_moves(PENDING_MOVE_QUEUE_PATH)
        except StoreError:
            logging.exception("Could not refresh queued-removal row markers")
            return
        self.pending_move_paths = {item.relative_path.casefold() for item in pending}
        self.pending_move_count = len(pending)
        self._render()

    def _scan_metadata(self) -> None:
        selected = [self.visible_rows[item] for item in self.tree.selection() if item in self.visible_rows]
        archives = selected or self.rows
        if not archives:
            return
        game_root = self.game_var.get()

        def worker() -> str:
            toolkit_path = None
            if any(self.metadata.get(row.path) is None for row in archives):
                toolkit_path = find_toolkit_path(game_root)
            read = 0
            failures = 0
            for index, row in enumerate(archives, start=1):
                if self.metadata.get(row.path) is None:
                    try:
                        self.metadata.scan(row.path, toolkit_path, save=False)
                        read += 1
                    except Exception:
                        failures += 1
                        logging.exception("Metadata scan failed: %s", row.path)
                if index % 10 == 0 or index == len(archives):
                    self.events.put(("progress", "Reading titles", f"{index}/{len(archives)} archives; {failures} errors"))
            if read:
                self.metadata.save()
            toolkit_note = f" Toolkit: {toolkit_path}." if toolkit_path else " Cached titles used."
            return f"{read} newly read; {failures} errors. Search artist or title now.{toolkit_note}"

        self._run("Reading titles", worker)

    def _scan_favorites(self) -> None:
        self.refresh()

    def _song_labels(self, rows: list[SongFile], limit: int = 8) -> str:
        labels = []
        for row in rows[:limit]:
            metadata = self.metadata.get(row.path) or {}
            songs = metadata.get("songs", [])
            label = "; ".join(
                f"{song.get('artist') or 'Unknown'} - {song.get('title') or 'Unknown'}"
                for song in songs[:3]
            )
            labels.append(label or row.relative_path)
        suffix = f"\n... and {len(rows) - limit} more" if len(rows) > limit else ""
        return "\n".join(labels) + suffix

    def _update_selected_favorites(
        self,
        favorited: bool,
        rows: tuple[SongFile, ...] | list[SongFile] | None = None,
    ) -> None:
        """Change selected archive tracks in the selected, freshly scanned profile."""

        if self._operation_busy:
            return
        selected = list(rows) if rows is not None else [
            self.visible_rows[item]
            for item in self.tree.selection()
            if item in self.visible_rows
        ]
        if not selected:
            messagebox.showinfo("Rocksmith favorites", "Select one or more song rows first.")
            return

        profile_path = Path(self.profile_var.get()).expanduser()
        expected_profile_signature = self.favorite_profile_signature
        expected_library_signature = self.favorite_library_signature
        favorite_result = self.favorite_result
        if (favorite_result is None or not favorite_result.available
                or expected_profile_signature is None or expected_library_signature is None):
            messagebox.showwarning(
                "Rocksmith favorites",
                "Selected profile and song library are not verified. Run Full refresh, then try again.",
            )
            return
        if profile_file_signature(profile_path) != expected_profile_signature:
            self.favorite_write_status_var.set(
                "Profile changed since refresh. Run Full refresh before changing favorites."
            )
            messagebox.showwarning(
                "Rocksmith favorites",
                "Selected Rocksmith profile changed after the last refresh. No profile changes were made. "
                "Run Full refresh and try again.",
            )
            self.refresh()
            return

        requested_keys: dict[str, str] = {}
        try:
            # Verify each selected archive still matches the scanned identity and
            # has complete, warning-free metadata before asking for confirmation.
            library_signature(selected)
            for row in selected:
                if row.identity is None:
                    raise StoreError(f"Archive identity is missing for {row.relative_path}; run Full refresh.")
                keys = song_keys_from_metadata(self.metadata.get(row.path))
                for key in keys:
                    requested_keys.setdefault(key.casefold(), key)
            store = self._store()
        except (OSError, ValueError, StoreError) as exc:
            messagebox.showwarning(
                "Rocksmith favorites",
                f"Selected songs cannot be safely matched to Rocksmith profile tracks:\n{exc}\n\n"
                "No profile changes were made. Run Full refresh, then try again.",
            )
            return

        song_keys = tuple(requested_keys.values())
        current_favorites = favorite_result.favorite_song_keys
        if favorited:
            changes = tuple(key for key in song_keys if key.casefold() not in current_favorites)
            action = "add"
            past_action = "added to"
        else:
            changes = tuple(key for key in song_keys if key.casefold() in current_favorites)
            action = "remove"
            past_action = "removed from"
        if not changes:
            state = "already favorites" if favorited else "not favorites"
            self.favorite_write_status_var.set(f"No profile change needed; selected tracks are {state}.")
            return

        profile_display = str(profile_path.resolve(strict=False))
        confirmation = (
            f"{action.title()} {len(changes)} verified Rocksmith track(s) {past_action} favorites?\n\n"
            f"Selected archive(s): {len(selected)}\n"
            f"Profile:\n{profile_display}\n\n"
            f"Tracks:\n{self._song_labels(selected)}\n\n"
            f"Rocksmith must be closed. Verified timestamped backup folder:\n{PROFILE_BACKUP_DIRECTORY}\n\n"
            "This changes only the selected profile's favorite list; it does not move or delete song files."
        )
        if not messagebox.askyesno("Confirm Rocksmith favorites", confirmation):
            self.favorite_write_status_var.set("Favorite change cancelled; profile unchanged.")
            return

        self._stop_preview()

        def worker() -> FavoriteUpdateResult:
            if profile_file_signature(profile_path) != expected_profile_signature:
                raise StoreError("Rocksmith profile changed after confirmation; no profile changes were made.")
            try:
                current_library_signature = library_signature(store.inventory())
            except Exception as exc:
                raise StoreError(f"Could not revalidate song library; no profile changes were made: {exc}") from exc
            if current_library_signature != expected_library_signature:
                raise StoreError(
                    "Song library or archive identity changed after confirmation; no profile changes were made. "
                    "Run Full refresh and try again."
                )
            return set_song_favorites(
                profile_path,
                song_keys,
                favorited,
                expected_profile_sha256=expected_profile_signature[3],
            )

        self._run("Updating song favorites", worker)

    def _unfavorite_all(self) -> None:
        """Clear every favorite key in the verified selected profile."""

        if self._operation_busy:
            return
        favorite_result = self.favorite_result
        expected_profile_signature = self.favorite_profile_signature
        if (
            favorite_result is None
            or not favorite_result.available
            or expected_profile_signature is None
        ):
            messagebox.showwarning(
                "Unfavorite every song",
                "The selected Rocksmith profile is not verified. Run Full refresh, then try again.",
            )
            return

        favorite_count = len(favorite_result.favorite_song_keys)
        if favorite_count == 0:
            self.favorite_write_status_var.set("The verified profile has no favorite songs.")
            return

        profile_path = Path(self.profile_var.get()).expanduser()
        if profile_file_signature(profile_path) != expected_profile_signature:
            self.favorite_write_status_var.set(
                "Profile changed since refresh. Run Full refresh before changing favorites."
            )
            messagebox.showwarning(
                "Unfavorite every song",
                "The selected Rocksmith profile changed after the last refresh. No changes were made. "
                "Run Full refresh and try again.",
            )
            self.refresh()
            return

        profile_display = str(profile_path.resolve(strict=False))
        typed = simpledialog.askstring(
            "Confirm: unfavorite every song",
            f"Remove all {favorite_count} favorite entries from this Rocksmith profile?\n\n"
            f"Profile:\n{profile_display}\n\n"
            "This includes profile favorites whose songs are missing or stored in Do Not Want. "
            "It changes only the selected profile's favorite list; no song files are moved or deleted.\n\n"
            "Rocksmith must be closed. A verified timestamped profile backup will be saved in:\n"
            f"{PROFILE_BACKUP_DIRECTORY}\n\n"
            "Type UNFAVORITE ALL to continue:",
            parent=self,
        )
        if typed is None or typed.strip().upper() != "UNFAVORITE ALL":
            self.favorite_write_status_var.set("Unfavorite all cancelled; profile unchanged.")
            return

        self._stop_preview()

        def worker() -> FavoriteUpdateResult:
            if profile_file_signature(profile_path) != expected_profile_signature:
                raise StoreError("Rocksmith profile changed after confirmation; no profile changes were made.")
            return clear_all_song_favorites(
                profile_path,
                expected_profile_sha256=expected_profile_signature[3],
            )

        self._run("Unfavoriting all songs", worker)

    def _confirm_archive_moves(
        self,
        title: str,
        rows: list[SongFile],
        destination: Path,
        *,
        context: str,
        queue_after_close: bool = False,
        show_all_tracks: bool = False,
    ) -> bool:
        """Show exact PSARC paths and require typed confirmation before moving or queueing."""
        if not rows:
            return False
        known_records = 0
        unknown_archives = 0
        review_lines = []
        for row in rows:
            metadata = self.metadata.get(row.path)
            songs = metadata_song_records(metadata)
            if songs:
                known_records += len(songs)
            else:
                unknown_archives += 1
            review_lines.append(format_archive_move_line(row.path, row.size, metadata))
            if show_all_tracks:
                for song in songs:
                    artist = " ".join(str(song.get("artist") or "Unknown").split()) or "Unknown"
                    title = " ".join(str(song.get("title") or "Unknown title").split()) or "Unknown title"
                    song_key = " ".join(str(song.get("song_key") or "Unknown SongKey").split())
                    review_lines.append(f"  TRACK: {artist} - {title} | SongKey: {song_key}")
        total_mib = sum(row.size for row in rows) / 1048576

        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.transient(self)
        dialog.geometry("1040x700")
        dialog.minsize(720, 500)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        operation_note = (
            "WARNING: Each listed PSARC leaves active DLC and moves to Do Not Want only after Rocksmith closes. "
            "No archive is permanently deleted. Undo the queue before it runs, or use Undo last completed removal afterward."
            if queue_after_close else
            "Moving is reversible through Restore; no song archive is deleted. Close Rocksmith before continuing."
        )
        summary = (
            f"{len(rows)} complete archive(s), {total_mib:.1f} MiB; {known_records} metadata-listed song record(s); "
            f"{unknown_archives} archive(s) with unknown track lists.\n"
            f"DESTINATION: {destination}\n"
            f"{context}\n"
            "Each line is a complete PSARC. This manager cannot remove one song from inside a PSARC. "
            f"{operation_note}"
        )
        ttk.Label(dialog, text=summary, justify="left", wraplength=1000, padding=10).grid(
            row=0, column=0, sticky="ew"
        )
        list_frame = ttk.Frame(dialog, padding=(10, 0, 10, 0))
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        candidates_text = tk.Text(list_frame, wrap="none", height=24, width=130)
        candidates_text.insert("1.0", "\n".join(review_lines))
        candidates_text.configure(state="disabled")
        candidates_text.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(list_frame, orient="vertical", command=candidates_text.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(list_frame, orient="horizontal", command=candidates_text.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        candidates_text.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)

        confirmation = tk.StringVar(master=dialog)
        confirmation_word = "QUEUE" if queue_after_close else "MOVE"
        action_verb = "Queue" if queue_after_close else "Move"
        ttk.Label(
            dialog,
            text=f"Type {confirmation_word} to confirm the complete archives listed above:",
            padding=(10, 8, 10, 2),
        ).grid(
            row=2, column=0, sticky="w"
        )
        entry = ttk.Entry(dialog, textvariable=confirmation)
        entry.grid(row=3, column=0, sticky="ew", padx=10)
        result = {"confirmed": False}
        buttons = ttk.Frame(dialog, padding=10)
        buttons.grid(row=4, column=0, sticky="e")
        move_button = ttk.Button(buttons, text=f"{action_verb} {len(rows)} archive(s)", state="disabled")
        move_button.pack(side="right", padx=(8, 0))
        cancel_button = ttk.Button(buttons, text="Cancel", command=dialog.destroy)
        cancel_button.pack(side="right")

        def update_confirmation(*_args) -> None:
            move_button.configure(state="normal" if confirmation.get() == confirmation_word else "disabled")

        def accept() -> None:
            if confirmation.get() == confirmation_word:
                result["confirmed"] = True
                dialog.destroy()

        confirmation.trace_add("write", update_confirmation)
        move_button.configure(command=accept)
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.grab_set()
        entry.focus_set()
        self.wait_window(dialog)
        return result["confirmed"]

    def _move_nonfavorites(self) -> None:
        favorites = self.favorite_result
        if favorites is None or not favorites.available:
            messagebox.showwarning(
                "Move verified non-favorites",
                "Automatic move is locked. Scan the selected Rocksmith profile and song manifests first.",
            )
            return
        if self.favorite_profile_signature != self._profile_signature():
            self.favorite_result = None
            self.favorite_profile_signature = None
            self.favorite_states.clear()
            self.favorite_library_signature = None
            self.favorite_scan_summary = "Selected profile changed. Rescan favorites; automatic move is locked."
            self._render()
            self._update_action_states()
            messagebox.showwarning("Move verified non-favorites", self.favorite_scan_summary)
            return
        active_favorites = [
            row for row in self.rows
            if row.location == "Active" and self.favorite_states.get(row.relative_path) == "Favorite"
        ]
        if not favorites.favorite_song_keys or not active_favorites:
            reason = (
                "The selected profile contains no favorite keys."
                if not favorites.favorite_song_keys
                else "No selected-profile favorite matched an active song archive."
            )
            explanation = (
                f"{reason}\nAutomatic move is locked so a bad or incomplete profile match cannot mark the whole library unwanted. "
                "Use the row checkboxes to move only songs you choose, or select the correct profile and scan again."
            )
            self.status_var.set(explanation)
            messagebox.showwarning("Move locked for safety", explanation)
            return

        candidates = [
            row for row in self.rows
            if row.location == "Active" and self.favorite_states.get(row.relative_path) == "Not favorite"
        ]
        unknown = sum(
            row.location == "Active" and self.favorite_states.get(row.relative_path, "Unknown") == "Unknown"
            for row in self.rows
        )
        if not candidates:
            messagebox.showinfo(
                "Move verified non-favorites",
                f"No confirmed non-favorite archives found. {unknown} unknown archive(s) remain in the game folder.\n\n"
                f"Destination: {self._store_destination_label()}",
            )
            return
        if self.favorite_library_signature is None:
            messagebox.showwarning(
                "Move verified non-favorites",
                "No verified archive snapshot is available. Run Full refresh before moving songs.",
            )
            return
        if not self._game_closed_for_immediate_move():
            return
        try:
            store = self._store()
        except StoreError as exc:
            messagebox.showwarning("Move verified non-favorites", str(exc))
            return
        if not self._confirm_archive_moves(
            "Review verified non-favorites",
            candidates,
            store.songs_root,
            context=(
                f"{len(active_favorites)} confirmed favorite archive(s) and {unknown} unknown archive(s) stay in the game folder. "
                "These candidates were classified non-favorite only after profile and archive verification."
            ),
        ):
            self.status_var.set("Automatic move cancelled; no songs were moved.")
            return

        self._stop_preview()
        expected_profile_signature = self.favorite_profile_signature
        expected_library_signature = self.favorite_library_signature
        profile_path = self.profile_var.get()

        def worker() -> int:
            try:
                current_rows = store.inventory()
                current_library_signature = library_signature(current_rows)
            except Exception as exc:
                raise StoreError(f"Could not revalidate the library after confirmation; no songs moved: {exc}") from exc
            validate_move_snapshot(
                expected_profile_signature,
                profile_file_signature(profile_path),
                expected_library_signature,
                current_library_signature,
            )

            moved = 0
            try:
                for index, row in enumerate(candidates, start=1):
                    source = row.path
                    target = store.songs_root / row.relative_path
                    if profile_file_signature(profile_path) != expected_profile_signature:
                        raise StoreError(
                            f"Rocksmith profile changed before archive {index}; stopped after moving {moved}. "
                            "Run Full refresh before another automatic move."
                        )
                    if row.identity is None:
                        raise StoreError(
                            f"Archive identity missing for {row.relative_path}; stopped after moving {moved}. "
                            "Run Full refresh."
                        )
                    try:
                        store.move_to_review(
                            [row.relative_path],
                            expected_identities={row.relative_path: row.identity},
                        )
                    except Exception as exc:
                        raise StoreError(
                            f"Stopped before moving {row.relative_path} after moving {moved} archive(s): {exc}"
                        ) from exc
                    self.metadata.relocate(source, target, save=False)
                    moved += 1
                    self.events.put(("progress", "Moving verified non-favorites", f"{index}/{len(candidates)}"))
                return moved
            finally:
                if moved:
                    self.metadata.save()

        self._run("Moving verified non-favorites to Do Not Want", worker)

    def _move_checked(self) -> None:
        self._move_active_rows(self._checked_active(), origin="checked")

    def _game_closed_for_immediate_move(self) -> bool:
        try:
            if not game_is_running():
                return True
        except StoreError as exc:
            messagebox.showwarning("Move songs", f"Cannot verify Rocksmith state; no archives moved. {exc}")
            return False
        messagebox.showinfo(
            "Rocksmith is running",
            "Close Rocksmith before an immediate move. For checked archives, use Queue checked until Rocksmith closes.",
        )
        return False

    def _move_active_rows(self, candidates: list[SongFile], *, origin: str) -> None:
        if not candidates:
            messagebox.showwarning("Move songs", "Select or check one or more active songs first.")
            return
        if any(row.location != "Active" for row in candidates):
            messagebox.showwarning("Move songs", "Only Active songs can be moved to Do Not Want.")
            return
        if not self._game_closed_for_immediate_move():
            return
        try:
            store = self._store()
        except StoreError as exc:
            messagebox.showwarning("Move songs", str(exc))
            return
        if not self._confirm_archive_moves(
            "Move selected archives to Do Not Want",
            candidates,
            store.songs_root,
            context=f"These {origin} archives were selected explicitly. Each listed PSARC moves as one complete file.",
        ):
            self.status_var.set("Move cancelled; no songs were moved.")
            return
        self._stop_preview()

        def worker() -> int:
            moved = 0
            try:
                for index, row in enumerate(candidates, start=1):
                    source = row.path
                    target = store.songs_root / row.relative_path
                    if row.identity is None:
                        raise StoreError(f"Archive identity is missing for {row.relative_path}; refresh before moving.")
                    store.move_to_review(
                        [row.relative_path],
                        expected_identities={row.relative_path: row.identity},
                    )
                    self.metadata.relocate(source, target, save=False)
                    moved += 1
                    self.events.put(("progress", "Moving selected songs", f"{index}/{len(candidates)}"))
                return moved
            finally:
                if moved:
                    self.metadata.save()

        self._run("Moving selected songs to Do Not Want", worker)

    def _preview_selected(self) -> None:
        if self._operation_busy:
            return
        selected = [self.visible_rows[item] for item in self.tree.selection() if item in self.visible_rows]
        if len(selected) != 1:
            messagebox.showinfo("Preview song", "Select exactly one archive row, then click Preview selected archive.")
            return
        self._preview_row(selected[0])

    def _preview_row(self, row: SongFile) -> None:
        if self._operation_busy:
            return
        if self.preview_active:
            self._stop_preview()
        metadata = self.metadata.get(row.path) or {}
        songs = metadata_song_records(metadata)
        if len(songs) == 1:
            only = songs[0]
            label = f"{only.get('artist') or 'Unknown'} - {only.get('title') or 'Unknown'}"
        elif songs:
            label = f"{row.path.name} ({len(songs)} song records)"
        else:
            label = row.relative_path
        game_root = self.game_var.get()
        track_count = len(songs) if songs else None

        def worker():
            try:
                toolkit = find_toolkit_path(game_root)
            except Exception as exc:
                raise PreviewError(
                    f"Could not locate RocksmithToolkit for the selected game folder: {exc}",
                    archive=row.path,
                    stage="Toolkit discovery",
                    diagnostics=f"configured_game_folder={game_root}\nerror={type(exc).__name__}: {exc}",
                ) from exc
            asset = prepare_preview(row.path, toolkit, track_count=track_count)
            return asset, label

        self._run("Preparing song preview", worker)

    def _stop_preview(self) -> None:
        try:
            import winsound

            winsound.PlaySound(None, winsound.SND_PURGE)
        except (ImportError, RuntimeError):
            logging.exception("Could not stop song preview audio")
        self.preview_active = False
        if self.preview_working_directory is not None:
            cleanup_preview_directory(self.preview_working_directory)
            self.preview_working_directory = None
        self.preview_status_var.set("No song preview playing.")
        self._update_action_states()

    def _open_intonation_checker(self) -> None:
        if self._operation_busy:
            return
        if self.intonation_window is not None:
            try:
                if self.intonation_window.window.winfo_exists():
                    self.intonation_window.window.lift()
                    return
            except Exception:
                logging.exception("Could not raise the existing intonation checker window")
        if self.intonation_window is not None:
            previous_capture = self.intonation_window.capture
            if previous_capture is not None and previous_capture.is_alive:
                messagebox.showwarning("Guitar input stopping", "The previous guitar input is still closing. Wait, then reopen the tuner; no second stream was opened.")
                return
        try:
            self.intonation_window = IntonationCheckerWindow(self)
        except Exception as exc:
            logging.exception("Could not open the guitar intonation checker")
            messagebox.showerror(
                "Guitar intonation checker",
                f"Could not open the checker:\n{exc}\n\nDetails: {LOG_PATH}",
            )

    def _close_app(self) -> None:
        if self._operation_busy:
            messagebox.showinfo("JonsRockSmithMods", "Wait for the current task to finish before closing the app.")
            return
        self._stop_preview()
        if self.intonation_window is not None:
            self.intonation_window.close_and_then(self.destroy)
            return
        self.destroy()

    def refresh(self) -> None:
        if self._operation_busy:
            return
        try:
            review_root = self._normalized_review_root()
        except StoreError as exc:
            self.status_var.set(str(exc))
            messagebox.showwarning("Do Not Want folder", str(exc))
            return
        self.favorite_result = None
        self.favorite_profile_signature = None
        self.favorite_library_signature = None
        self.favorite_states.clear()
        self.favorite_scan_summary = "Favorites are being refreshed; automatic move is locked until verification completes."
        self._render()
        self._update_action_states()
        game_root = self.game_var.get()
        review_root = str(review_root)
        profile_path = self.profile_var.get()

        def worker() -> LibraryRefreshResult:
            store = SongStore(Path(game_root), Path(review_root))
            return refresh_library(
                store,
                self.metadata,
                game_root,
                profile_path,
                progress=lambda stage, detail: self.events.put(("progress", stage, detail)),
            )

        self._run("Full library refresh", worker)

    def _restore(self) -> None:
        try:
            paths = self._selected("Do Not Want")
            store = self._store()
        except StoreError as exc:
            messagebox.showwarning("JonsRockSmithMods", str(exc))
            return
        self._stop_preview()
        def worker() -> int:
            restored = 0
            for index, rel in enumerate(paths, start=1):
                source = store.songs_root / rel
                target = store.dlc_root / rel
                store.restore([rel])
                self.metadata.relocate(source, target)
                restored += 1
                self.events.put(("progress", "Restoring songs", f"{index}/{len(paths)}"))
            return restored
        self._run("Restoring songs", worker)

def runtime_self_check(report_path: Path) -> None:
    """Check packaged modules without creating windows or opening audio capture."""
    import json
    import platform
    from types import SimpleNamespace
    import numpy as np
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from intonation_analysis import estimate_pitch, frequency_for_note, cents_difference
    report_path = Path(report_path)
    if report_path.suffix.lower() != ".json" or not report_path.parent.is_dir():
        raise ValueError("Self-check needs a new JSON file in an existing directory")
    with report_path.open("x", encoding="utf-8") as output:
        checks = {}
        try:
            plain = b"0123456789abcdef"
            cipher = Cipher(algorithms.AES(bytes(32)), modes.CBC(bytes(16)))
            enc = cipher.encryptor()
            encrypted = enc.update(plain) + enc.finalize()
            dec = cipher.decryptor()
            if dec.update(encrypted) + dec.finalize() != plain:
                raise RuntimeError("Crypto roundtrip failed")
            checks["crypto_roundtrip"] = True
            for note in ("E2", "E3"):
                hz = frequency_for_note(note)
                samples = .2 * np.sin(2 * np.pi * hz * np.arange(8192) / 48000)
                pitch = estimate_pitch(samples, 48000, expected_frequency_hz=hz / 2)
                if pitch is None or abs(cents_difference(pitch.frequency_hz, hz)) > 5:
                    raise RuntimeError("Pitch check failed: " + note)
                checks["pitch_" + note] = True
            page = ScrollablePage.__new__(ScrollablePage)
            root = SimpleNamespace(master=None, children={})
            page.master, page._w, page.children = root, ".page", {}
            root.children["page"] = page
            if page._root() is not root or page.nametowidget(".page") is not page:
                raise RuntimeError("Tk dispatch check failed")
            checks["tk_widget_dispatch"] = True
            base = Path(__file__).resolve().parent
            for directory, name in (("metadata", "MetadataExtractor"), ("split_helper", "SplitPackHelper")):
                for suffix in ("", "-x86", "-x64"):
                    if not (base / directory / "bin" / (name + suffix + ".exe")).is_file():
                        raise RuntimeError("Packaged helper missing")
            checks["six_helpers_present"] = True
        except Exception:
            json.dump({"version": APP_VERSION, "status": "failed", "checks": checks,
                       "error": traceback.format_exc()}, output, indent=2)
            raise
        json.dump({"version": APP_VERSION, "status": "passed", "frozen": bool(getattr(sys, "frozen", False)),
                   "python": platform.python_version(), "checks": checks,
                   "audio_capture_opened": False, "gui_opened": False}, output, indent=2)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--self-check":
        runtime_self_check(Path(sys.argv[2]))
    elif len(sys.argv) != 1:
        raise SystemExit("Unknown arguments")
    else:
        App().mainloop()
