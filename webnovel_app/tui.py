from __future__ import annotations

import contextlib
import io
import logging
import shlex
import threading
from datetime import datetime

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.theme import Theme
from textual.widgets import (
    ContentSwitcher,
    Footer,
    Header,
    Input,
    OptionList,
    RichLog,
    Static,
)
from textual.widgets.option_list import Option

from recsys.cli import Context
from recsys.embed import DEFAULT_MODEL
from recsys.search import Filters
from recsys.store import NovelRecord
from scripts.repo_paths import CATEGORIES
from scraper import set_progress_callback
from webnovel_app.downloads import download_categories, download_novel
from webnovel_app.jobs import JobManager, JobSnapshot, JobState
from webnovel_app.library import (
    Chapter,
    list_library,
    live_first_chapter,
    local_chapters,
    local_path,
)
from webnovel_app.targets import resolve_target


HELP = """[b]Commands[/b]
Plain text searches by semantic description.
/like TITLE                 recommend books similar to TITLE
/query DESCRIPTION          semantic recommendation query
/tags TAG[,TAG]             list books carrying all tags
/library [TEXT]             browse local metadata/downloads
/info [N|TITLE|URL]         show metadata for a result or target
/read [N|TITLE|URL]         read local text, or fetch a first-chapter preview
/download [N|TITLE|URL]     download one novel (pauses the active fetch job)
/crawl [cats] [limit]       update metadata/catalogues
/download-all [cats] [limit] download pending novels in selected categories
/job  /pause  /resume  /stop
/help  /clear  /quit

Result numbers are accepted by /info, /read, and /download. Select a result with
the arrow keys and Enter. In the reader use [ and ] for chapters; Esc returns."""


class _EventWriter(io.TextIOBase):
    def __init__(self, emit) -> None:
        self.emit = emit
        self.buffer = ""

    def write(self, text: str) -> int:
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                self.emit(line.rstrip())
        return len(text)

    def flush(self) -> None:
        if self.buffer.strip():
            self.emit(self.buffer.rstrip())
        self.buffer = ""


class _PaneLogHandler(logging.Handler):
    def __init__(self, emit) -> None:
        super().__init__(logging.INFO)
        self.emit_line = emit
        self.setFormatter(logging.Formatter("%(levelname)s %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.emit_line(self.format(record))
        except Exception:
            self.handleError(record)


# A calm, cohesive dark terminal palette (Tokyo Night derived). Replaces the
# default high-contrast accent scheme so panes read as one quiet surface.
WEBNOVEL_THEME = Theme(
    name="webnovel-dark",
    primary="#7aa2f7",
    secondary="#bb9af7",
    accent="#7dcfff",
    foreground="#c0caf5",
    background="#16161e",
    surface="#1a1b26",
    panel="#1f2335",
    success="#9ece6a",
    warning="#e0af68",
    error="#f7768e",
    dark=True,
    variables={
        "block-cursor-text-style": "none",
        "footer-key-foreground": "#7dcfff",
        "footer-description-foreground": "#a9b1d6",
        "input-cursor-background": "#7aa2f7",
        "input-selection-background": "#33467c 60%",
    },
)


class WebnovelApp(App[None]):
    TITLE = "Webnovel"
    SUB_TITLE = "recommend · read · crawl · download"
    COMMAND_PALETTE_BINDING = "ctrl+backslash"

    CSS = """
    Screen {
        layout: vertical;
        background: $background;
    }

    /* Main pane: transcript + results, or the reader. */
    #upper {
        height: 1fr;
        background: $surface;
        border: round $primary 60%;
        border-title-color: $accent;
        border-title-style: bold;
    }
    #interaction { height: 100%; }

    #transcript {
        height: 1fr;
        padding: 0 1;
        background: $surface;
        scrollbar-background: $surface;
        scrollbar-color: $primary 50%;
        scrollbar-color-hover: $primary;
    }

    #results {
        height: auto;
        max-height: 45%;
        padding: 0 1;
        background: $surface;
        border-top: solid $primary 30%;
    }
    #results > .option-list--option {
        padding: 0 1;
    }
    #results > .option-list--option-highlighted {
        background: $primary 25%;
        color: $text;
        text-style: bold;
    }

    #reader {
        height: 100%;
        padding: 1 2;
        background: $surface;
    }

    /* Live operational log. */
    #events {
        height: 32%;
        min-height: 6;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
        border: round $secondary 50%;
        border-title-color: $secondary;
        scrollbar-background: $panel;
        scrollbar-color: $secondary 50%;
    }

    #job-status {
        height: 1;
        padding: 0 1;
        background: $background;
        color: $text-muted;
    }

    /* Command input — flows above the footer so it is never clipped. */
    #command {
        height: 3;
        margin: 0 0 0 0;
        padding: 0 1;
        background: $surface;
        color: $foreground;
        border: round $primary 50%;
    }
    #command:focus {
        border: round $accent;
        background: $panel;
    }

    Footer {
        background: $panel;
    }
    """

    BINDINGS = [
        Binding("ctrl+k", "focus_command", "Command", show=True),
        Binding("ctrl+s", "stop_job", "Stop fetch", show=True),
        Binding("ctrl+p", "toggle_pause", "Pause/resume", show=True, priority=True),
        Binding("ctrl+l", "focus_logs", "Logs", show=False),
        Binding("ctrl+q", "graceful_quit", "Quit", show=True),
        Binding("escape", "leave_reader", "Back", show=False),
        Binding("[", "previous_chapter", "Previous", show=False),
        Binding("]", "next_chapter", "Next", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.context = Context(DEFAULT_MODEL, "auto")
        self.result_records: list[NovelRecord] = []
        self.reader_record: NovelRecord | None = None
        self.reader_chapters: list[Chapter] = []
        self.reader_position = 0
        self._root_handlers: list[logging.Handler] = []
        self._pane_handler = _PaneLogHandler(self.emit_event)
        self.jobs = JobManager(self.emit_event, self.job_state_changed)

    def compose(self) -> ComposeResult:
        yield Header()
        with ContentSwitcher(initial="interaction", id="upper"):
            with Vertical(id="interaction"):
                yield RichLog(id="transcript", markup=True, wrap=True)
                yield OptionList(id="results")
            with VerticalScroll(id="reader"):
                yield Static(id="reader-content", markup=False)
        yield RichLog(id="events", markup=False, wrap=True)
        yield Static("fetch: idle", id="job-status")
        yield Input(placeholder="Describe a book, or type /help", id="command")
        yield Footer()

    def on_mount(self) -> None:
        self.register_theme(WEBNOVEL_THEME)
        self.theme = "webnovel-dark"
        self.query_one("#upper").border_title = "Library"
        self.query_one("#events").border_title = "Activity"
        root = logging.getLogger()
        self._root_handlers = list(root.handlers)
        for handler in self._root_handlers:
            root.removeHandler(handler)
        root.addHandler(self._pane_handler)
        set_progress_callback(self.emit_event)
        self.query_one("#transcript", RichLog).write(
            "[b #7dcfff]Webnovel is ready.[/] "
            "Enter a reading description, or type [b]/help[/b]."
        )
        self.set_interval(0.25, self.jobs.sync_state)
        self.query_one("#command", Input).focus()

    def on_unmount(self) -> None:
        set_progress_callback(None)
        root = logging.getLogger()
        root.removeHandler(self._pane_handler)
        for handler in self._root_handlers:
            root.addHandler(handler)

    def emit_event(self, message: str) -> None:
        if not message.strip():
            return

        def append() -> None:
            stamp = datetime.now().strftime("%H:%M:%S")
            self.query_one("#events", RichLog).write(f"{stamp} {message}")

        try:
            self.call_from_thread(append)
        except RuntimeError:
            append()

    def job_state_changed(self, snapshot: JobSnapshot) -> None:
        def update() -> None:
            name = f" - {snapshot.name}" if snapshot.name else ""
            self.query_one("#job-status", Static).update(
                f"fetch: {snapshot.state.value}{name}"
            )

        try:
            self.call_from_thread(update)
        except RuntimeError:
            update()

    def write(self, text: str) -> None:
        self.query_one("#transcript", RichLog).write(text)

    def set_results(self, records: list[NovelRecord], heading: str) -> None:
        self.result_records = records
        options = self.query_one("#results", OptionList)
        options.clear_options()
        options.add_options(
            Option(
                f"{index}. 《{record.title}》 - {record.author or '?'} "
                f"[{record.category}] [{'full' if record.downloaded else 'meta'}]",
                id=str(index - 1),
            )
            for index, record in enumerate(records, 1)
        )
        self.write(f"\n[b]{heading}[/b] ({len(records)} results)")
        if records:
            options.highlighted = 0
            options.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        index = int(event.option_id or "0")
        if 0 <= index < len(self.result_records):
            self.show_record(self.result_records[index])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        command = event.value.strip()
        event.input.value = ""
        if not command:
            return
        self.write(f"\n[bold cyan]> {command}[/bold cyan]")
        self.dispatch_command(command)

    def dispatch_command(self, command: str) -> None:
        if not command.startswith("/"):
            self.run_query(command)
            return
        try:
            parts = shlex.split(command[1:])
        except ValueError as exc:
            self.write(f"[red]{exc}[/red]")
            return
        if not parts:
            return
        name, args = parts[0].lower(), parts[1:]
        handlers = {
            "help": lambda: self.write(HELP),
            "clear": self.clear_transcript,
            "like": lambda: self.run_like(" ".join(args)),
            "query": lambda: self.run_query(" ".join(args)),
            "tags": lambda: self.run_tags(" ".join(args)),
            "library": lambda: self.browse_library(" ".join(args)),
            "info": lambda: self.command_info(" ".join(args)),
            "read": lambda: self.command_read(" ".join(args)),
            "preview": lambda: self.command_read(" ".join(args), force_live=True),
            "download": lambda: self.command_download(" ".join(args)),
            "crawl": lambda: self.command_crawl(args),
            "download-all": lambda: self.command_download_all(args),
            "job": self.command_job,
            "pause": self.action_pause_job,
            "resume": self.action_resume_job,
            "stop": self.action_stop_job,
            "quit": self.action_graceful_quit,
            "exit": self.action_graceful_quit,
        }
        handler = handlers.get(name)
        if handler is None:
            self.write(f"[red]Unknown command /{name}. Type /help.[/red]")
            return
        handler()

    def _run_thread(self, task, *, name: str) -> None:
        threading.Thread(target=task, name=f"webnovel-{name}", daemon=True).start()

    @contextlib.contextmanager
    def _capture_output(self):
        writer = _EventWriter(self.emit_event)
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            yield
        writer.flush()

    def run_like(self, title: str) -> None:
        if not title:
            self.write("[yellow]Usage: /like TITLE[/yellow]")
            return

        def task() -> None:
            try:
                engine = self.context.engine
                seed = engine.resolve_title(title)
                if seed is None:
                    self.call_from_thread(self.write, f"[red]No novel matched {title!r}.[/red]")
                    return
                results = engine.search(
                    engine.vector_for_url(seed.url),
                    ref_tags=set(seed.tags),
                    filters=Filters(),
                    exclude_urls={seed.url},
                    n=10,
                )
                self.call_from_thread(
                    self.set_results,
                    [result.record for result in results],
                    f"Similar to 《{seed.title}》",
                )
            except Exception as exc:
                self.call_from_thread(self.write, f"[red]{exc}[/red]")

        self._run_thread(task, name="recommend-like")

    def run_query(self, text: str) -> None:
        if not text:
            self.write("[yellow]Enter a description to search.[/yellow]")
            return

        def task() -> None:
            try:
                with self._capture_output():
                    vector = self.context.embedder.encode_query(text)
                results = self.context.engine.search(vector, filters=Filters(), n=10)
                self.call_from_thread(
                    self.set_results,
                    [result.record for result in results],
                    f"Recommendations for: {text}",
                )
            except Exception as exc:
                self.call_from_thread(self.write, f"[red]{exc}[/red]")

        self.emit_event("Running semantic recommendation query.")
        self._run_thread(task, name="recommend-query")

    def run_tags(self, text: str) -> None:
        wanted = {item.strip() for item in text.split(",") if item.strip()}
        if not wanted:
            self.write("[yellow]Usage: /tags TAG[,TAG][/yellow]")
            return

        def task() -> None:
            try:
                records = [
                    record
                    for record in self.context.engine.records
                    if wanted.issubset(set(record.tags))
                ]
                records.sort(key=lambda record: record.year_month, reverse=True)
                self.call_from_thread(
                    self.set_results,
                    records[:50],
                    f"Tags: {' / '.join(sorted(wanted))}",
                )
            except Exception as exc:
                self.call_from_thread(self.write, f"[red]{exc}[/red]")

        self._run_thread(task, name="recommend-tags")

    def browse_library(self, query: str) -> None:
        self.set_results(list_library(query=query, limit=50), "Library")

    def _resolve_record(self, target: str) -> tuple[NovelRecord | None, str | None]:
        if target.isdigit():
            index = int(target) - 1
            if 0 <= index < len(self.result_records):
                record = self.result_records[index]
                return record, record.url
        if not target and self.result_records:
            options = self.query_one("#results", OptionList)
            index = options.highlighted or 0
            record = self.result_records[index]
            return record, record.url
        if not target:
            self.write("[yellow]Select a result or provide a title/URL.[/yellow]")
            return None, None
        resolution = resolve_target(target)
        if resolution.record and resolution.url:
            return resolution.record, resolution.url
        if resolution.url:
            return None, resolution.url
        if resolution.candidates:
            self.set_results(resolution.candidates, f"Matches for: {target}")
        else:
            self.write(f"[red]No novel matched {target!r}.[/red]")
        return None, None

    def show_record(self, record: NovelRecord) -> None:
        details = [
            f"[b]《{record.title}》[/b] - {record.author or '?'}",
            f"{record.category} | {record.status or '?'} | {record.upload_date or '?'}",
            f"tags: {' / '.join(record.tags) if record.tags else '(none)'}",
            f"source: {record.url}",
            f"local: {record.file or '(metadata only)'}",
        ]
        if record.synopsis:
            details.extend(("", record.synopsis))
        self.write("\n".join(details))

    def command_info(self, target: str) -> None:
        record, _url = self._resolve_record(target)
        if record:
            self.show_record(record)

    def command_read(self, target: str, force_live: bool = False) -> None:
        record, url = self._resolve_record(target)
        if not url:
            return
        path = local_path(record) if record else None
        if path and path.exists() and not force_live:
            self.open_reader(record, local_chapters(path))
            return

        def task() -> None:
            try:
                text, truncated = self.jobs.run_exclusive(
                    f"preview {url}",
                    lambda: live_first_chapter(url),
                )
                title = record.title if record else url
                preview = [Chapter(f"Preview: {title}", text)]
                if truncated:
                    preview[0].body += "\n\n[Preview truncated at the page safety limit.]"
                self.call_from_thread(self.open_reader, record, preview)
            except Exception as exc:
                self.call_from_thread(self.write, f"[red]Preview failed: {exc}[/red]")

        self._run_thread(task, name="live-preview")

    def open_reader(
        self,
        record: NovelRecord | None,
        chapters: list[Chapter],
    ) -> None:
        if not chapters:
            self.write("[red]No readable chapters found.[/red]")
            return
        self.reader_record = record
        self.reader_chapters = chapters
        self.reader_position = 0
        self.query_one("#upper", ContentSwitcher).current = "reader"
        self.render_chapter()

    def render_chapter(self) -> None:
        chapter = self.reader_chapters[self.reader_position]
        title = self.reader_record.title if self.reader_record else "Live preview"
        self.query_one("#reader-content", Static).update(
            f"《{title}》\n"
            f"[{self.reader_position + 1}/{len(self.reader_chapters)}] {chapter.text()}"
        )
        self.query_one("#reader", VerticalScroll).scroll_home(animate=False)

    def command_download(self, target: str) -> None:
        record, url = self._resolve_record(target)
        if not url:
            return
        label = record.title if record else url

        def task() -> None:
            try:
                code, novel = self.jobs.run_exclusive(
                    f"download {label}",
                    lambda: download_novel(url, event_callback=self.emit_event),
                )
                status = "complete" if code == 0 else "failed"
                self.call_from_thread(self.write, f"Download {status}: {label}")
            except Exception as exc:
                self.call_from_thread(self.write, f"[red]Download failed: {exc}[/red]")

        self._run_thread(task, name="download-novel")

    @staticmethod
    def _categories_and_limit(args: list[str]) -> tuple[list[str], int]:
        categories = list(CATEGORIES)
        limit = 0
        if args:
            raw = args[0]
            if raw.lower() != "all":
                categories = [item.strip() for item in raw.split(",") if item.strip()]
        if len(args) > 1:
            limit = int(args[1])
        unknown = [category for category in categories if category not in CATEGORIES]
        if unknown:
            raise ValueError(f"Unknown categories: {', '.join(unknown)}")
        return categories, limit

    def command_crawl(self, args: list[str]) -> None:
        try:
            categories, limit = self._categories_and_limit(args)
        except ValueError as exc:
            self.write(f"[red]{exc}[/red]")
            return

        def target(controls) -> None:
            from recsys.crawl import crawl

            stats = crawl(categories, limit=limit, controls=controls)
            self.emit_event(
                "Metadata crawl complete: "
                f"{stats['fetched']} fetched, {stats['not_found']} missing, "
                f"{stats['errors']} errors."
            )

        if not self.jobs.start(f"metadata: {','.join(categories)}", target):
            self.write("[yellow]A site-fetch job is already active.[/yellow]")

    def command_download_all(self, args: list[str]) -> None:
        try:
            categories, limit = self._categories_and_limit(args)
        except ValueError as exc:
            self.write(f"[red]{exc}[/red]")
            return

        def target(controls) -> None:
            download_categories(
                categories,
                limit=limit,
                controls=controls,
                event_callback=self.emit_event,
            )

        if not self.jobs.start(f"downloads: {','.join(categories)}", target):
            self.write("[yellow]A site-fetch job is already active.[/yellow]")

    def command_job(self) -> None:
        snapshot = self.jobs.sync_state()
        self.write(
            f"Fetch job: {snapshot.state.value}"
            + (f" - {snapshot.name}" if snapshot.name else "")
        )

    def clear_transcript(self) -> None:
        self.query_one("#transcript", RichLog).clear()

    def action_focus_command(self) -> None:
        self.query_one("#command", Input).focus()

    def action_focus_logs(self) -> None:
        self.query_one("#events", RichLog).focus()

    def action_pause_job(self) -> None:
        if not self.jobs.pause():
            self.write("[yellow]No running fetch job.[/yellow]")

    def action_resume_job(self) -> None:
        if not self.jobs.resume():
            self.write("[yellow]No paused fetch job.[/yellow]")

    def action_toggle_pause(self) -> None:
        state = self.jobs.sync_state().state
        if state in {JobState.PAUSED, JobState.PAUSE_REQUESTED}:
            self.action_resume_job()
        else:
            self.action_pause_job()

    def action_stop_job(self) -> None:
        if not self.jobs.stop():
            self.write("[yellow]No running fetch job.[/yellow]")

    def action_leave_reader(self) -> None:
        upper = self.query_one("#upper", ContentSwitcher)
        if upper.current == "reader":
            upper.current = "interaction"
            self.query_one("#command", Input).focus()

    def action_previous_chapter(self) -> None:
        if self.query_one("#upper", ContentSwitcher).current != "reader":
            return
        self.reader_position = max(0, self.reader_position - 1)
        self.render_chapter()

    def action_next_chapter(self) -> None:
        if self.query_one("#upper", ContentSwitcher).current != "reader":
            return
        self.reader_position = min(
            len(self.reader_chapters) - 1,
            self.reader_position + 1,
        )
        self.render_chapter()

    def action_graceful_quit(self) -> None:
        if not self.jobs.busy:
            self.exit()
            return
        self.jobs.stop()
        self.write("[yellow]Stopping and checkpointing the active fetch job...[/yellow]")

        def wait_and_exit() -> None:
            self.jobs.wait_all()
            self.call_from_thread(self.exit)

        self._run_thread(wait_and_exit, name="graceful-quit")


def run_tui() -> int:
    WebnovelApp().run()
    return 0
