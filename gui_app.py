#!/usr/bin/env python3
"""News Catcher desktop GUI — portrait layout for users without Telegram."""

from __future__ import annotations

import logging
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import messagebox, ttk

import config
import gui_service as svc
from gui_audio import GuiAudioPlayer, format_time

logger = logging.getLogger(__name__)

# Brand colors sampled from docs/NewsCatcher_logo_large.png
DARK_BG = "#00112E"
LIGHT_BG = "#72C1FD"
DARK_FG = "#FFFFFF"
LIGHT_FG = "#000000"
# Input/button surfaces: same blue family, one step lighter than the window (dark) or richer (light)
DARK_FIELD = "#0A2245"
LIGHT_FIELD = "#9AD4FE"

WINDOW_WIDTH = 500
WINDOW_HEIGHT = 860
LOGO_PATH = Path(__file__).resolve().parent / "docs" / "NewsCatcher_logo_large.png"


class NewsCatcherGui:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("News Catcher")
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.root.minsize(320, 620)

        self.dark_mode = True
        self._busy = False
        self._trial_domain: str | None = None
        self._theme_widgets: list[tk.Widget] = []
        self._field_widgets: list[tk.Widget] = []
        self._player = GuiAudioPlayer()
        self._seeking = False
        self._progress_var = tk.DoubleVar(value=0.0)

        self._logo_image: tk.PhotoImage | None = None
        self._build_ui()
        self._apply_theme()
        self._poll_playback()

    def _bg(self) -> str:
        return DARK_BG if self.dark_mode else LIGHT_BG

    def _fg(self) -> str:
        return DARK_FG if self.dark_mode else LIGHT_FG

    def _field_bg(self) -> str:
        return DARK_FIELD if self.dark_mode else LIGHT_FIELD

    def _build_ui(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        header = tk.Frame(self.root, padx=10, pady=8)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        if LOGO_PATH.is_file():
            raw = tk.PhotoImage(file=str(LOGO_PATH))
            scale = max(1, raw.width() // 220)
            self._logo_image = raw.subsample(scale, scale)
            self.logo_label = tk.Label(header, image=self._logo_image, borderwidth=0)
        else:
            self.logo_label = tk.Label(header, text="News Catcher", font=("Segoe UI", 16, "bold"))
        self.logo_label.grid(row=0, column=0, sticky="w")

        self.theme_btn = tk.Button(
            header,
            text="☀️",
            width=3,
            relief="flat",
            borderwidth=0,
            command=self._toggle_theme,
        )
        self.theme_btn.grid(row=0, column=1, sticky="e")
        self._theme_widgets.extend([header, self.logo_label, self.theme_btn])

        url_frame = tk.Frame(self.root, padx=10, pady=4)
        url_frame.grid(row=1, column=0, sticky="ew")
        url_frame.columnconfigure(0, weight=1)

        self.url_var = tk.StringVar()
        self.url_entry = tk.Entry(url_frame, textvariable=self.url_var)
        self.url_entry.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.url_entry.bind("<Return>", lambda _e: self._on_fetch())

        self.fetch_btn = tk.Button(url_frame, text="Go", width=5, command=self._on_fetch)
        self.fetch_btn.grid(row=0, column=1, padx=(0, 4))

        self.research_btn = tk.Button(url_frame, text="Research", width=8, command=self._on_research)
        self.research_btn.grid(row=0, column=2)
        self._field_widgets.extend([self.url_entry, self.fetch_btn, self.research_btn])

        text_frame = tk.Frame(self.root, padx=10, pady=4)
        text_frame.grid(row=2, column=0, sticky="nsew")
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)

        self.article_text = tk.Text(
            text_frame,
            wrap="word",
            undo=True,
            padx=8,
            pady=8,
            borderwidth=1,
            relief="solid",
        )
        self.article_text.grid(row=0, column=0, sticky="nsew")
        self._field_widgets.append(self.article_text)

        scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=self.article_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.article_text.configure(yscrollcommand=scrollbar.set)

        self.status_var = tk.StringVar(
            value="Paste a news URL and tap Go, or enter a topic and tap Research."
        )
        self.status_label = tk.Label(self.root, textvariable=self.status_var, anchor="w", padx=10)
        self.status_label.grid(row=3, column=0, sticky="ew")

        self._build_playback_bar(row=4)
        self._build_action_buttons(row=5)

        self._theme_widgets.extend(
            [
                url_frame,
                text_frame,
                self.status_label,
                self.playback_frame,
                self.action_frame,
            ]
        )

    def _build_playback_bar(self, *, row: int) -> None:
        self.playback_frame = tk.Frame(self.root, padx=10, pady=6)
        self.playback_frame.grid(row=row, column=0, sticky="ew")
        self.playback_frame.columnconfigure(1, weight=1)

        self.elapsed_var = tk.StringVar(value="0:00")
        self.total_var = tk.StringVar(value="0:00")

        self.elapsed_label = tk.Label(self.playback_frame, textvariable=self.elapsed_var, width=5)
        self.elapsed_label.grid(row=0, column=0, padx=(0, 4))

        self.progress_scale = tk.Scale(
            self.playback_frame,
            from_=0,
            to=1000,
            orient="horizontal",
            variable=self._progress_var,
            showvalue=False,
            sliderlength=14,
            command=self._on_seek_drag,
        )
        self.progress_scale.grid(row=0, column=1, sticky="ew")
        self.progress_scale.bind("<ButtonPress-1>", self._on_seek_press)
        self.progress_scale.bind("<ButtonRelease-1>", self._on_seek_release)
        self._field_widgets.append(self.progress_scale)

        self.total_label = tk.Label(self.playback_frame, textvariable=self.total_var, width=5)
        self.total_label.grid(row=0, column=2, padx=(4, 0))

        controls = tk.Frame(self.playback_frame)
        controls.grid(row=1, column=0, columnspan=3, pady=(6, 0))
        for col in range(3):
            controls.columnconfigure(col, weight=1)

        default_font = tkfont.nametofont("TkDefaultFont")
        try:
            base_size = int(default_font.cget("size"))
        except tk.TclError:
            base_size = 12
        if base_size == 0:
            base_size = 12
        play_size = int(base_size * 1.5)
        if base_size > 0:
            play_size = max(14, play_size)
        self._play_icon_font = tkfont.Font(
            family=default_font.cget("family"),
            size=play_size,
        )

        self.back_btn = tk.Button(controls, text="⏪ 10s", command=self._on_back_10)
        self.back_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.play_btn = tk.Button(
            controls,
            text="▶",
            font=self._play_icon_font,
            command=self._on_play_pause,
        )
        self.play_btn.grid(row=0, column=1, sticky="ew", padx=4)

        self.fwd_btn = tk.Button(controls, text="10s ⏩", command=self._on_forward_10)
        self.fwd_btn.grid(row=0, column=2, sticky="ew", padx=(4, 0))

        self._field_widgets.extend([self.back_btn, self.fwd_btn])
        self._theme_widgets.append(controls)

    def _build_action_buttons(self, *, row: int) -> None:
        self.action_frame = tk.Frame(self.root, padx=10, pady=8)
        self.action_frame.grid(row=row, column=0, sticky="ew")
        for col in range(3):
            self.action_frame.columnconfigure(col, weight=1)

        self.test_fix_btn = tk.Button(self.action_frame, text="Test & Fix", command=self._on_test_fix)
        self.test_fix_btn.grid(row=0, column=0, sticky="ew", padx=(0, 4))

        self.speak_btn = tk.Button(self.action_frame, text="speak", command=self._on_speak)
        self.speak_btn.grid(row=0, column=1, sticky="ew", padx=4)

        self.cancel_btn = tk.Button(self.action_frame, text="cancel", command=self._on_cancel)
        self.cancel_btn.grid(row=0, column=2, sticky="ew", padx=(4, 0))

        self.block_btn = tk.Button(
            self.action_frame,
            text="Block website",
            command=self._on_block_site,
            state=tk.DISABLED,
        )
        self.block_btn.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))

        self._field_widgets.extend(
            [self.test_fix_btn, self.speak_btn, self.cancel_btn, self.block_btn]
        )

    def _style_field_widget(self, widget: tk.Widget) -> None:
        widget.configure(
            bg=self._field_bg(),
            fg=self._fg(),
            activebackground=self._field_bg(),
            activeforeground=self._fg(),
            highlightthickness=0,
        )

    def _apply_theme(self) -> None:
        bg = self._bg()
        fg = self._fg()
        field_bg = self._field_bg()

        self.root.configure(bg=bg)
        self.theme_btn.configure(
            text="☀️" if self.dark_mode else "🌙",
            bg=bg,
            fg=fg,
            activebackground=bg,
            activeforeground=fg,
        )
        for widget in self._theme_widgets:
            try:
                widget.configure(bg=bg)
            except tk.TclError:
                pass
        for widget in (self.logo_label, self.status_label, self.elapsed_label, self.total_label):
            try:
                widget.configure(fg=fg, bg=bg)
            except tk.TclError:
                pass

        for widget in self._field_widgets:
            if isinstance(widget, tk.Text):
                widget.configure(
                    bg=field_bg,
                    fg=fg,
                    insertbackground=fg,
                )
            elif isinstance(widget, tk.Scale):
                widget.configure(
                    bg=bg,
                    fg=fg,
                    troughcolor=field_bg,
                    activebackground=field_bg,
                    highlightthickness=0,
                )
            elif isinstance(widget, tk.Entry):
                widget.configure(bg=field_bg, fg=fg, insertbackground=fg)
            else:
                self._style_field_widget(widget)

        self._style_field_widget(self.play_btn)
        self.play_btn.configure(font=self._play_icon_font)

    def _toggle_theme(self) -> None:
        self.dark_mode = not self.dark_mode
        self._apply_theme()

    def _load_and_play(self, path: Path) -> None:
        self._player.load(path)
        duration = self._player.get_duration()
        self.total_var.set(format_time(duration))
        self.elapsed_var.set("0:00")
        self._progress_var.set(0.0)
        self._player.play()

    def _poll_playback(self) -> None:
        if self._player.path is not None and not self._seeking:
            pos = self._player.get_position()
            duration = self._player.get_duration()
            if duration > 0:
                self._progress_var.set((pos / duration) * 1000.0)
            self.elapsed_var.set(format_time(pos))
            self.total_var.set(format_time(duration))
        self.play_btn.configure(text="⏸" if self._player.is_playing() else "▶")
        self.root.after(200, self._poll_playback)

    def _on_seek_press(self, _event=None) -> None:
        self._seeking = True

    def _on_seek_release(self, _event=None) -> None:
        duration = self._player.get_duration()
        if duration > 0 and self._player.path is not None:
            fraction = self._progress_var.get() / 1000.0
            self._player.set_position(fraction * duration)
        self._seeking = False

    def _on_seek_drag(self, _value: str) -> None:
        if not self._seeking:
            return
        duration = self._player.get_duration()
        if duration > 0:
            fraction = self._progress_var.get() / 1000.0
            self.elapsed_var.set(format_time(fraction * duration))

    def _on_play_pause(self) -> None:
        if self._player.path is None:
            messagebox.showinfo("News Catcher", "Generate audio with speak or Test & Fix first.")
            return
        self._player.toggle_pause()

    def _on_back_10(self) -> None:
        if self._player.path is None:
            return
        self._player.seek_relative(-10.0)

    def _on_forward_10(self) -> None:
        if self._player.path is None:
            return
        self._player.seek_relative(10.0)

    def _set_busy(self, busy: bool, status: str | None = None) -> None:
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for widget in (
            self.fetch_btn,
            self.research_btn,
            self.test_fix_btn,
            self.speak_btn,
            self.url_entry,
        ):
            widget.configure(state=state)
        if status is not None:
            self.status_var.set(status)

    def _set_article(self, text: str) -> None:
        self.article_text.configure(state=tk.NORMAL)
        self.article_text.delete("1.0", tk.END)
        self.article_text.insert("1.0", text)

    def _run_bg(self, work, on_success, on_error, *, set_busy: bool = True) -> None:
        def runner() -> None:
            try:
                result = work()
            except Exception as exc:
                logger.exception("background task failed")
                self.root.after(0, lambda: on_error(exc))
                return
            self.root.after(0, lambda: on_success(result))

        if set_busy:
            self._set_busy(True)
        threading.Thread(target=runner, daemon=True).start()

    def _finish_task(self, status: str) -> None:
        self._set_busy(False, status)

    def _set_trial_domain(self, domain: str | None) -> None:
        self._trial_domain = domain
        self.block_btn.configure(state=tk.NORMAL if domain else tk.DISABLED)

    def _on_block_site(self) -> None:
        if self._busy or not self._trial_domain:
            return
        domain = self._trial_domain
        if not messagebox.askyesno(
            "Block website?",
            f"Add {domain} to domains_bad.json and remove it from the approved list?",
        ):
            return

        err = svc.mark_domain_bad(domain)
        if err:
            messagebox.showerror("News Catcher", err)
            return
        self._set_trial_domain(None)
        self._finish_task(f"Blocked {domain}.")

    def _on_fetch(self) -> None:
        if self._busy:
            return
        self._set_trial_domain(None)
        raw = self.url_var.get().strip()
        url = svc.extract_first_url(raw) or raw
        if not url:
            messagebox.showinfo("News Catcher", "Enter a news article URL.")
            return

        def work():
            return svc.fetch_article(url)

        def on_success(outcome: svc.FetchArticleOutcome) -> None:
            if outcome.kind == "ok" and outcome.article:
                self._set_article(outcome.article.display_text)
                if self._trial_domain:
                    self._finish_task(
                        f"Article loaded. Block website is available for {self._trial_domain}."
                    )
                else:
                    self._set_trial_domain(None)
                    self._finish_task("Article loaded. Tap speak or Test & Fix when ready.")
                return
            if outcome.kind == "domain_prompt" and outcome.domain_prompt:
                self._finish_task("Waiting for your answer…")
                prompt = outcome.domain_prompt
                if messagebox.askyesno("Add domain?", prompt.message):
                    err = svc.add_approved_domain(prompt.domain)
                    if err:
                        messagebox.showerror("News Catcher", err)
                        return
                    self._set_trial_domain(prompt.domain)
                    self._set_busy(True, f"Added {prompt.domain}. Fetching…")

                    def refetch_work():
                        return svc.fetch_article(prompt.url)

                    self._run_bg(
                        refetch_work,
                        on_success,
                        lambda exc: (
                            messagebox.showerror("News Catcher", str(exc)),
                            self._finish_task("Could not fetch article."),
                        ),
                    )
                else:
                    self._set_trial_domain(None)
                    self._finish_task("Domain not added.")
                return
            if outcome.kind == "bad_domain_prompt" and outcome.bad_domain_prompt:
                self._finish_task("Waiting for your answer…")
                prompt = outcome.bad_domain_prompt
                if messagebox.askyesno(
                    "Temporarily override?",
                    f"{prompt.message}\n\n"
                    f"Temporarily override {prompt.domain} for this fetch "
                    "(does not change domains_bad.json)?",
                ):
                    self._set_busy(True, f"Override for {prompt.domain}. Fetching…")

                    def override_work():
                        return svc.fetch_article(prompt.url, ignore_bad_domain=True)

                    self._run_bg(
                        override_work,
                        on_success,
                        lambda exc: (
                            messagebox.showerror("News Catcher", str(exc)),
                            self._finish_task("Could not fetch article."),
                        ),
                    )
                else:
                    self._finish_task("Fetch cancelled (bad domain).")
                return
            if outcome.kind == "oversize" and outcome.oversize_prompt:
                self._finish_task("Waiting for your answer…")
                prompt = outcome.oversize_prompt
                if messagebox.askyesno("Large download", prompt.message):
                    self._set_busy(True, "Downloading with raised limit…")

                    def oversize_work():
                        return svc.fetch_article(
                            prompt.url, byte_limit=config.FETCH_HARD_MAX_BYTES
                        )

                    self._run_bg(
                        oversize_work,
                        on_success,
                        lambda exc: (
                            messagebox.showerror("News Catcher", str(exc)),
                            self._finish_task("Download failed."),
                        ),
                    )
                else:
                    self._finish_task("Download cancelled.")
                return
            messagebox.showerror("News Catcher", outcome.error or "Could not fetch article.")
            if self._trial_domain:
                self._finish_task(
                    f"Fetch failed. Block website is available for {self._trial_domain}."
                )
            else:
                self._finish_task("Fetch failed.")

        def on_error(exc: Exception) -> None:
            messagebox.showerror("News Catcher", str(exc))
            self._finish_task("Fetch failed.")

        self._set_busy(True, "Fetching article…")
        self._run_bg(work, on_success, on_error)

    def _on_research(self) -> None:
        if self._busy:
            return
        self._set_trial_domain(None)
        query = self.url_var.get().strip()
        if not query:
            messagebox.showinfo(
                "News Catcher",
                "Enter a topic (e.g. Apple smart glasses launch) or a Google News Full Coverage URL.",
            )
            return

        def work():
            return svc.deep_research(
                query,
                on_progress=lambda msg: self.root.after(0, lambda m=msg: self.status_var.set(m)),
            )

        def on_success(outcome: svc.DeepResearchOutcome) -> None:
            if outcome.kind == "ok" and outcome.article:
                self._set_article(outcome.article.display_text)
                if outcome.warning:
                    messagebox.showwarning("News Catcher", outcome.warning)
                self._finish_task("Research article ready. Tap speak to generate audio.")
                return
            messagebox.showerror("News Catcher", outcome.error or "Research failed.")
            self._finish_task("Research failed.")

        def on_error(exc: Exception) -> None:
            messagebox.showerror("News Catcher", str(exc))
            self._finish_task("Research failed.")

        self._set_busy(True, "Starting research…")
        self._run_bg(work, on_success, on_error)

    def _on_speak(self) -> None:
        if self._busy:
            return

        def work():
            return svc.speak_last_article()

        def on_success(path: Path) -> None:
            self._load_and_play(path)
            self._finish_task("Playing article audio.")

        def on_error(exc: Exception) -> None:
            messagebox.showerror("News Catcher", str(exc))
            self._finish_task("Could not generate audio.")

        self._set_busy(True, "Generating audio… this may take a few minutes.")
        self._run_bg(work, on_success, on_error)

    def _on_test_fix(self) -> None:
        if self._busy:
            return
        self._open_test_fix_dialog()

    def _default_test_text(self) -> str:
        cached = svc.get_cached_article()
        if cached is None:
            return ""
        paras = [p.strip() for p in cached.text.split("\n\n") if p.strip()]
        return "\n\n".join(paras[:2])

    def _open_test_fix_dialog(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("Test & Fix")
        win.geometry("380x520")
        win.transient(self.root)
        win.grab_set()

        bg = self._bg()
        fg = self._fg()
        field_bg = self._field_bg()
        win.configure(bg=bg)
        font = tkfont.Font(family="Segoe UI", size=10)

        def style_btn(btn: tk.Button) -> None:
            btn.configure(
                bg=field_bg,
                fg=fg,
                activebackground=field_bg,
                activeforeground=fg,
                relief="raised",
            )

        def style_entry(entry: tk.Entry) -> None:
            entry.configure(bg=field_bg, fg=fg, insertbackground=fg)

        tk.Label(
            win,
            text="Paste a sentence or two to hear with current pronunciation rules.",
            wraplength=340,
            justify="left",
            bg=bg,
            fg=fg,
            font=font,
        ).pack(padx=12, pady=(12, 6), anchor="w")

        text_frame = tk.Frame(win, bg=bg)
        text_frame.pack(fill="both", expand=True, padx=12, pady=4)
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)

        test_text = tk.Text(
            text_frame,
            wrap="word",
            height=10,
            padx=8,
            pady=8,
            font=font,
            bg=field_bg,
            fg=fg,
            insertbackground=fg,
        )
        test_text.grid(row=0, column=0, sticky="nsew")
        initial = self._default_test_text()
        if initial:
            test_text.insert("1.0", initial)

        from_row = tk.Frame(win, bg=bg)
        from_row.pack(fill="x", padx=12, pady=4)
        tk.Label(from_row, text="From:", width=6, anchor="w", bg=bg, fg=fg, font=font).pack(
            side="left"
        )
        from_var = tk.StringVar()
        from_entry = tk.Entry(from_row, textvariable=from_var)
        from_entry.pack(side="left", fill="x", expand=True)
        style_entry(from_entry)

        to_row = tk.Frame(win, bg=bg)
        to_row.pack(fill="x", padx=12, pady=4)
        tk.Label(to_row, text="To:", width=6, anchor="w", bg=bg, fg=fg, font=font).pack(side="left")
        to_var = tk.StringVar()
        to_entry = tk.Entry(to_row, textvariable=to_var)
        to_entry.pack(side="left", fill="x", expand=True)
        style_entry(to_entry)

        btn_row = tk.Frame(win, bg=bg)
        btn_row.pack(fill="x", padx=12, pady=8)
        for col in range(4):
            btn_row.columnconfigure(col, weight=1)

        suggest_sample_paths: list[Path] = []

        def cleanup_suggest_samples() -> None:
            for path in suggest_sample_paths:
                path.unlink(missing_ok=True)
            suggest_sample_paths.clear()

        def on_ai_suggest() -> None:
            from_text = from_var.get().strip()
            if not from_text:
                messagebox.showinfo("Test & Fix", "Enter the word or phrase in From first.", parent=win)
                return
            context = svc.test_fix_context_snippet(test_text.get("1.0", tk.END), from_text)
            suggest_btn.configure(state=tk.DISABLED, text="…")
            win.configure(cursor="watch")

            def work():
                return svc.suggest_test_fix_samples(from_text, context=context)

            def done(result: tuple[list[svc.PronunciationSample], str | None, str | None]) -> None:
                suggest_btn.configure(state=tk.NORMAL, text="AI Suggest")
                win.configure(cursor="")
                samples, warning, error = result
                if error:
                    messagebox.showerror("Test & Fix", error, parent=win)
                    return
                if warning:
                    messagebox.showwarning("Test & Fix", warning, parent=win)
                cleanup_suggest_samples()
                suggest_sample_paths.extend(s.path for s in samples)
                self._show_test_fix_suggestions(
                    win,
                    samples,
                    from_text,
                    on_use=to_var.set,
                    on_close=cleanup_suggest_samples,
                )

            def err(exc: Exception) -> None:
                suggest_btn.configure(state=tk.NORMAL, text="AI Suggest")
                win.configure(cursor="")
                messagebox.showerror("Test & Fix", str(exc), parent=win)

            self._run_bg(work, done, err, set_busy=False)

        def on_test() -> None:
            phrase = test_text.get("1.0", tk.END).strip()
            if not phrase:
                messagebox.showinfo("Test & Fix", "Paste some text to test.", parent=win)
                return
            test_btn.configure(state=tk.DISABLED, text="…")
            win.configure(cursor="watch")

            def work():
                return svc.synthesize_test_phrase(phrase)

            def done(path: Path) -> None:
                test_btn.configure(state=tk.NORMAL, text="Test")
                win.configure(cursor="")
                self._load_and_play(path)
                self.status_var.set("Playing test audio from Test & Fix.")

            def err(exc: Exception) -> None:
                test_btn.configure(state=tk.NORMAL, text="Test")
                win.configure(cursor="")
                messagebox.showerror("Test & Fix", str(exc), parent=win)

            self._run_bg(work, done, err, set_busy=False)

        def on_save() -> None:
            from_text = from_var.get().strip()
            to_text = to_var.get().strip()
            if not from_text or not to_text:
                messagebox.showinfo(
                    "Test & Fix",
                    "Enter both From and To before saving.",
                    parent=win,
                )
                return
            added = svc.save_pronunciation(from_text, to_text)
            verb = "Saved" if added else "Updated"
            messagebox.showinfo(
                "Test & Fix",
                f"{verb} {from_text!r} → {to_text!r} in tts_replacements.json.",
                parent=win,
            )

        suggest_btn = tk.Button(btn_row, text="AI Suggest", command=on_ai_suggest)
        suggest_btn.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        test_btn = tk.Button(btn_row, text="Test", command=on_test)
        test_btn.grid(row=0, column=1, sticky="ew", padx=3)
        save_btn = tk.Button(btn_row, text="Save", command=on_save)
        save_btn.grid(row=0, column=2, sticky="ew", padx=3)
        def on_close_dialog() -> None:
            cleanup_suggest_samples()
            win.destroy()

        close_btn = tk.Button(btn_row, text="Close", command=on_close_dialog)
        close_btn.grid(row=0, column=3, sticky="ew", padx=(3, 0))
        for btn in (suggest_btn, test_btn, save_btn, close_btn):
            style_btn(btn)
        win.protocol("WM_DELETE_WINDOW", on_close_dialog)

    def _show_test_fix_suggestions(
        self,
        parent: tk.Toplevel,
        samples: list[svc.PronunciationSample],
        from_text: str,
        *,
        on_use,
        on_close,
    ) -> None:
        picker = tk.Toplevel(parent)
        picker.title("AI Suggestions")
        picker.geometry("360x300")
        picker.transient(parent)
        picker.grab_set()

        bg = self._bg()
        fg = self._fg()
        field_bg = self._field_bg()
        picker.configure(bg=bg)
        font = tkfont.Font(family="Segoe UI", size=10)

        tk.Label(
            picker,
            text=f"Pick a spelling for {from_text!r}. Tap Play to hear, Use to fill To.",
            wraplength=320,
            justify="left",
            bg=bg,
            fg=fg,
            font=font,
        ).pack(padx=12, pady=(12, 6), anchor="w")

        list_frame = tk.Frame(picker, bg=bg)
        list_frame.pack(fill="both", expand=True, padx=12, pady=4)

        for sample in samples:
            row = tk.Frame(list_frame, bg=bg)
            row.pack(fill="x", pady=3)
            tk.Label(
                row,
                text=f"{sample.spelling!r}",
                bg=bg,
                fg=fg,
                font=font,
                anchor="w",
            ).pack(side="left", fill="x", expand=True)

            def play(path=sample.path) -> None:
                self._load_and_play(path)

            def use(spelling=sample.spelling) -> None:
                on_use(spelling)

            for label, command in (("Play", play), ("Use", use)):
                tk.Button(
                    row,
                    text=label,
                    command=command,
                    bg=field_bg,
                    fg=fg,
                    activebackground=field_bg,
                    activeforeground=fg,
                ).pack(side="right", padx=2)

        def close_picker() -> None:
            on_close()
            picker.destroy()

        tk.Button(picker, text="Close", command=close_picker, bg=bg, fg=fg).pack(pady=8)
        picker.protocol("WM_DELETE_WINDOW", close_picker)

    def _on_cancel(self) -> None:
        self._player.stop()
        self.elapsed_var.set("0:00")
        self.total_var.set("0:00")
        self._progress_var.set(0.0)
        self._finish_task("Cancelled. Paste a news article URL when you're ready.")

    def run(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self) -> None:
        self._player.stop()
        self.root.destroy()


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
    )
    NewsCatcherGui().run()


if __name__ == "__main__":
    main()
