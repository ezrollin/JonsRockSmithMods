"""Read-only library checks with a responsive review and export window."""
from __future__ import annotations

import json
import logging
import os
import queue
import tempfile
import threading
import tkinter as tk
import traceback
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from library_health import scan_library
from song_store import SongFile
from ui_layout import initial_window_size


STATUS_LABELS = {
    "issue_found": "Issue found",
    "needs_review": "Needs review",
    "no_issue_found": "No structural issue found",
}
LIMITATION = (
    "Read-only checks cannot prove playback works. Missing dependencies need review; "
    "file size alone does not show whether audio or notes work. Queueing removal requires "
    "confirmation and moves the entire archive only after Rocksmith closes. "
    "Use a trusted original or backup to replace missing assets."
)


def result_details(result: dict) -> str:
    """Keep every diagnostic field available, including future helper details."""
    return json.dumps(result, indent=2, ensure_ascii=False, default=str)


def selected_active_rows(results: list[dict], rows: list[SongFile]) -> list[SongFile]:
    """Resolve reports to the original snapshot; never create move candidates from reports."""
    by_path = {os.path.normcase(str(row.path.resolve())): row for row in rows}
    selected = []
    for result in results:
        value = result.get("archive_path", result.get("path", ""))
        row = by_path.get(os.path.normcase(str(Path(value).resolve()))) if value else None
        if row is None or row.location != "Active":
            raise ValueError("An archive no longer matches the active scanned library. "
                             "Close this review and rescan the library.")
        if row not in selected:
            selected.append(row)
    return selected


def health_report(results: list[dict], rows: list[SongFile], started: str,
                  completed: bool) -> dict:
    return {
        "schema_version": 1,
        "scan_started_utc": started,
        "report_created_utc": datetime.now(timezone.utc).isoformat(),
        "scan_completed": completed,
        "requested_archive_count": len(rows),
        "checked_archive_count": len(results),
        "limitation": LIMITATION,
        "source_snapshots": [{"archive_path": str(row.path),
                              "identity": list(row.identity) if row.identity else None,
                              "location": row.location} for row in rows],
        "results": results,
    }


def export_report(path: Path, report: dict) -> None:
    """Write reports atomically so an interrupted export preserves the old report."""
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".song-health-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def scan_worker(rows, toolkit, messages, cancel_event, log_path):
    """Only plain data crosses the worker boundary; Tk is main-thread only."""
    try:
        logging.info("Library health scan started: toolkit=%s paths=%s log=%s",
                     toolkit, [str(row.path) for row in rows], log_path)
        results = scan_library(
            rows, toolkit,
            lambda done, total, path: messages.put(("progress", (done, total, str(path)))),
            cancel_event,
        )
        logging.info("Library health scan finished: checked=%s requested=%s cancelled=%s",
                     len(results), len(rows), cancel_event.is_set())
        messages.put(("done", [result.as_dict() for result in results]))
    except Exception:
        detail = traceback.format_exc()
        logging.exception("Library health scan failed: toolkit=%s archives=%s log=%s",
                          toolkit, len(rows), log_path)
        messages.put(("error", detail))


class LibraryHealthWindow(tk.Toplevel):
    def __init__(self, parent, rows: list[SongFile], toolkit: Path | None,
                 queue_callback, log_path: Path):
        super().__init__(parent)
        self.title("Song health review")
        width, height = initial_window_size(self.winfo_screenwidth(), self.winfo_screenheight())
        self.geometry(f"{width}x{height}")
        self.rows = list(rows)
        self.toolkit = toolkit
        self.queue_callback = queue_callback
        self.log_path = Path(log_path)
        self.results: list[dict] = []
        self.started = datetime.now(timezone.utc).isoformat()
        self.messages = queue.Queue()
        self.cancel_event = threading.Event()
        self.running = False
        self.completed = False
        self.closed = False
        self.after_id = None
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Destroy>", self._destroyed, add="+")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        introduction = ttk.Label(self, text=LIMITATION, wraplength=max(360, width - 40), padding=10)
        introduction.grid(row=0, column=0, sticky="ew")
        self.bind("<Configure>", lambda event: introduction.configure(
            wraplength=max(180, event.width - 40)) if event.widget is self else None)
        self.status = tk.StringVar(value=f"Ready to check {len(self.rows)} archives.")
        ttk.Label(self, textvariable=self.status, padding=(10, 0)).grid(row=1, column=0, sticky="ew")
        self.progress = ttk.Progressbar(self, maximum=max(1, len(self.rows)), mode="determinate")
        self.progress.grid(row=2, column=0, padx=10, pady=8, sticky="ew")

        panes = ttk.Panedwindow(self, orient="vertical")
        panes.grid(row=3, column=0, padx=10, sticky="nsew")
        table_frame = ttk.Frame(panes)
        table_frame.rowconfigure(1, weight=1)
        table_frame.columnconfigure(0, weight=1)
        self.filter = tk.StringVar(value="All results")
        filter_box = ttk.Combobox(table_frame, textvariable=self.filter, state="readonly",
                                 values=("All results", "Issues and review", *STATUS_LABELS.values()))
        filter_box.grid(row=0, column=0, sticky="w", pady=(0, 6))
        filter_box.bind("<<ComboboxSelected>>", lambda _event: self._populate())
        self.tree = ttk.Treeview(table_frame, columns=("status", "archive", "summary"),
                                 show="headings", selectmode="extended", height=10)
        for key, label, column_width in (("status", "Status", 175), ("archive", "Archive", 270),
                                         ("summary", "Finding", 400)):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=column_width, minwidth=80)
        self.tree.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        horizontal.grid(row=2, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        self.tree.bind("<<TreeviewSelect>>", self._details)
        panes.add(table_frame, weight=3)

        detail_frame = ttk.Frame(panes)
        detail_frame.rowconfigure(1, weight=1)
        detail_frame.columnconfigure(0, weight=1)
        ttk.Label(detail_frame, text="Selected archive details (Ctrl+C to copy)").grid(row=0, column=0, sticky="w")
        self.details = tk.Text(detail_frame, height=8, wrap="word", state="disabled")
        self.details.grid(row=1, column=0, sticky="nsew")
        detail_scrollbar = ttk.Scrollbar(detail_frame, orient="vertical", command=self.details.yview)
        detail_scrollbar.grid(row=1, column=1, sticky="ns")
        self.details.configure(yscrollcommand=detail_scrollbar.set)
        panes.add(detail_frame, weight=2)

        footer = ttk.Frame(self, padding=10)
        footer.grid(row=4, column=0, sticky="ew")
        self.start_button = ttk.Button(footer, text="Start checks", command=self._start)
        self.start_button.grid(row=0, column=0, sticky="w", pady=3)
        self.stop_button = ttk.Button(footer, text="Stop after current archive", command=self._stop, state="disabled")
        self.stop_button.grid(row=0, column=1, sticky="w", padx=6, pady=3)
        self.export_button = ttk.Button(footer, text="Export JSON report", command=self._export, state="disabled")
        self.export_button.grid(row=1, column=0, sticky="w", pady=3)
        self.queue_button = ttk.Button(footer, text="Queue selected removal…", command=self._queue, state="disabled")
        self.queue_button.grid(row=1, column=1, sticky="w", padx=6, pady=3)
        footer.columnconfigure(2, weight=1)
        ttk.Button(footer, text="Close", command=self._close).grid(row=0, column=2, rowspan=2, sticky="e")

    def _start(self):
        if self.running:
            return
        self.results = []
        self._populate()
        self.started = datetime.now(timezone.utc).isoformat()
        self.completed = False
        self.cancel_event.clear()
        self.running = True
        self.progress.configure(value=0)
        self.status.set(f"Checking 0 / {len(self.rows)} archives…")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.export_button.configure(state="disabled")
        self.queue_button.configure(state="disabled")
        threading.Thread(target=scan_worker,
                         args=(self.rows, self.toolkit, self.messages, self.cancel_event, self.log_path),
                         daemon=True).start()
        self.after_id = self.after(100, self._poll)

    def _stop(self):
        self.cancel_event.set()
        self.stop_button.configure(state="disabled")
        self.status.set("Stopping after the current archive finishes. No files are being changed.")

    def _poll(self):
        self.after_id = None
        if self.closed:
            return
        try:
            while True:
                event, payload = self.messages.get_nowait()
                if event == "progress":
                    done, total, path = payload
                    self.progress.configure(value=done)
                    if not self.cancel_event.is_set():
                        self.status.set(f"Checking {done} / {total}: {Path(path).name}")
                elif event in ("done", "error"):
                    self.running = False
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    if event == "error":
                        self.status.set(f"Checks failed. Full error below and in {self.log_path}")
                        self._set_details(payload)
                    else:
                        self.results = payload
                        self.completed = not self.cancel_event.is_set() and len(payload) == len(self.rows)
                        counts = {status: sum(item.get("status") == status for item in payload)
                                  for status in STATUS_LABELS}
                        state = "Finished" if self.completed else "Stopped"
                        self.status.set(f"{state}: {len(payload)} / {len(self.rows)} checked; "
                                        f"{counts['issue_found']} with issues, {counts['needs_review']} need review. "
                                        "Playback remains unverified.")
                        self.export_button.configure(state="normal")
                        self._populate()
        except queue.Empty:
            pass
        if self.running:
            self.after_id = self.after(100, self._poll)

    def _populate(self):
        self.tree.delete(*self.tree.get_children())
        selected_filter = self.filter.get()
        for index, result in enumerate(self.results):
            status = result.get("status", "needs_review")
            label = STATUS_LABELS.get(status, "Needs review")
            if selected_filter == "Issues and review" and status == "no_issue_found":
                continue
            if selected_filter not in ("All results", "Issues and review", label):
                continue
            path = result.get("archive_path", result.get("path", ""))
            self.tree.insert("", "end", iid=str(index), values=(label, path, result.get("summary", "")))
        self.queue_button.configure(state="disabled")
        self._set_details("")

    def _set_details(self, text):
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def _details(self, _event=None):
        selected = self.tree.selection()
        self.queue_button.configure(state="normal" if selected and not self.running else "disabled")
        detail = "\n\n".join(result_details(self.results[int(index)]) for index in selected[:20])
        if len(selected) > 20:
            detail += f"\n\nShowing 20 of {len(selected)} selected archives. Export the full JSON report for all findings."
        self._set_details(detail)

    def _queue(self):
        if self.running:
            return
        try:
            selected = selected_active_rows([self.results[int(index)] for index in self.tree.selection()], self.rows)
            if selected:
                self.queue_callback(selected)
        except Exception:
            detail = traceback.format_exc()
            logging.exception("Cannot queue health-review selection")
            self._set_details(detail)
            messagebox.showerror("Removal queue failed", f"{detail}\nLog: {self.log_path}", parent=self)

    def _export(self):
        filename = filedialog.asksaveasfilename(parent=self, title="Export song health report",
                                               defaultextension=".json", filetypes=[("JSON report", "*.json")],
                                               initialfile="song-health-report.json")
        if not filename:
            return
        try:
            if Path(filename).suffix.casefold() != ".json":
                raise ValueError("Choose a .json report filename. Song archives cannot be overwritten by a report.")
            export_report(Path(filename), health_report(self.results, self.rows, self.started, self.completed))
        except Exception:
            detail = traceback.format_exc()
            logging.exception("Cannot export song health report: path=%s", filename)
            self._set_details(detail)
            messagebox.showerror("Report export failed", f"{detail}\nLog: {self.log_path}", parent=self)

    def _close(self):
        self.closed = True
        self.cancel_event.set()
        if self.after_id:
            self.after_cancel(self.after_id)
            self.after_id = None
        self.destroy()

    def _destroyed(self, event):
        if event.widget is self:
            self.closed = True
            self.cancel_event.set()
            if self.after_id:
                self.after_cancel(self.after_id)
                self.after_id = None
