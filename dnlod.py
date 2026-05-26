#!/usr/bin/env python3
"""Dnlod — paste a YouTube URL, download video/audio, fetch lyrics."""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import (
    Tk,
    Toplevel,
    StringVar,
    BooleanVar,
    END,
    filedialog,
    messagebox,
    ttk,
    Text,
    Scrollbar,
    Canvas,
)

import requests
import sv_ttk
import yt_dlp
from PIL import Image, ImageDraw, ImageTk

try:
    import lyricsgenius
    HAVE_GENIUS = True
except ImportError:
    HAVE_GENIUS = False

def _in_bundle() -> bool:
    return bool(getattr(sys, "frozen", False)) or ".app/Contents/" in str(Path(sys.executable).resolve())


def _user_data_dir() -> Path:
    """Writable location for config + default downloads (the bundle's Resources/ is read-only)."""
    if _in_bundle():
        base = Path.home() / "Library" / "Application Support" / "Dnlod"
    else:
        base = Path(__file__).resolve().parent
    base.mkdir(parents=True, exist_ok=True)
    return base


def _bundled_ffmpeg() -> str | None:
    """If inside the .app, return the dir containing the bundled ffmpeg binary."""
    if not _in_bundle():
        return None
    # sys.executable -> .app/Contents/MacOS/<launcher>
    res_dir = Path(sys.executable).resolve().parent.parent / "Resources"
    ff = res_dir / "ffmpeg" / "ffmpeg"
    return str(ff.parent) if ff.exists() else None


APP_DIR = _user_data_dir()
CONFIG_PATH = APP_DIR / "config.json"
DEFAULT_DOWNLOADS = APP_DIR / "downloads"
FFMPEG_DIR = _bundled_ffmpeg()
THUMB_W, THUMB_H = 200, 112


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {"genius_token": "", "default_output_dir": str(DEFAULT_DOWNLOADS), "theme": "dark"}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


_PAREN_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*")
_SEPARATORS = (" - ", " – ", " — ", "–", "—")


def parse_title(title: str) -> tuple[str, str]:
    cleaned = _PAREN_RE.sub(" ", title).strip()
    cleaned = re.sub(r"\s*-\s*Topic\s*$", "", cleaned, flags=re.IGNORECASE)
    for sep in _SEPARATORS:
        if sep in cleaned:
            artist, song = cleaned.split(sep, 1)
            return artist.strip(), song.strip()
    return "", cleaned


def open_in_finder(path: str | Path) -> None:
    p = Path(path).expanduser()
    if not p.exists():
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    if sys.platform == "darwin":
        subprocess.run(["open", str(p)], check=False)
    elif sys.platform.startswith("win"):
        os.startfile(str(p))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(p)], check=False)


def format_duration(seconds: int | None) -> str:
    if not seconds:
        return "—"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def make_app_icon() -> Image.Image:
    """Procedurally draw a 256x256 Dnlod icon: rounded square with a download arrow."""
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    accent = (88, 166, 255, 255)
    d.rounded_rectangle((8, 8, size - 8, size - 8), radius=46, fill=accent)
    arrow_color = (255, 255, 255, 255)
    cx = size // 2
    d.rectangle((cx - 16, 60, cx + 16, 150), fill=arrow_color)
    d.polygon([(cx - 50, 138), (cx + 50, 138), (cx, 198)], fill=arrow_color)
    d.rounded_rectangle((52, 200, size - 52, 220), radius=8, fill=arrow_color)
    return img


def placeholder_thumb() -> Image.Image:
    img = Image.new("RGB", (THUMB_W, THUMB_H), (40, 40, 44))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, THUMB_W - 1, THUMB_H - 1), outline=(70, 70, 76))
    d.text((THUMB_W // 2 - 28, THUMB_H // 2 - 6), "no preview", fill=(140, 140, 148))
    return img


class DnlodApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("Dnlod")
        self.root.geometry("1120x720")
        self.root.minsize(980, 640)

        self.config = load_config()
        DEFAULT_DOWNLOADS.mkdir(exist_ok=True)

        sv_ttk.set_theme(self.config.get("theme", "dark"))
        self._apply_icon()

        # State
        self.url_var = StringVar()
        self.title_var = StringVar(value="No video loaded")
        self.uploader_var = StringVar(value="—")
        self.duration_var = StringVar(value="—")
        self.output_var = StringVar(value=self.config.get("default_output_dir", str(DEFAULT_DOWNLOADS)))
        self.artist_var = StringVar()
        self.song_var = StringVar()
        self.source_var = StringVar(value="lyrics.ovh")
        self.status_var = StringVar(value="Ready.")
        self.theme_var = BooleanVar(value=self.config.get("theme", "dark") == "dark")

        self._thumb_img: ImageTk.PhotoImage | None = None  # keep ref

        self._build_ui()
        self._set_thumbnail(placeholder_thumb())
        self._refresh_genius_radio()
        self._bind_mac_shortcuts()

    # ---------- macOS clipboard shortcuts ----------
    def _bind_mac_shortcuts(self) -> None:
        self.root.event_add("<<Paste>>", "<Command-v>")
        self.root.event_add("<<Copy>>", "<Command-c>")
        self.root.event_add("<<Cut>>", "<Command-x>")
        self.root.event_add("<<SelectAll>>", "<Command-a>")
        self.root.event_add("<<Undo>>", "<Command-z>")

    # ---------- icon ----------
    def _apply_icon(self) -> None:
        try:
            icon = make_app_icon()
            self._icon_photo = ImageTk.PhotoImage(icon)
            self.root.iconphoto(True, self._icon_photo)
        except Exception:
            pass

    # ---------- layout ----------
    def _build_ui(self) -> None:
        # Header bar
        header = ttk.Frame(self.root, padding=(16, 12, 16, 4))
        header.pack(fill="x")
        ttk.Label(header, text="Dnlod", font=("TkDefaultFont", 18, "bold")).pack(side="left")
        ttk.Label(header, text="YouTube downloader + lyrics", foreground="#888").pack(side="left", padx=10, pady=(6, 0))
        ttk.Checkbutton(
            header, text="Dark mode", style="Switch.TCheckbutton",
            variable=self.theme_var, command=self.on_toggle_theme,
        ).pack(side="right")

        ttk.Separator(self.root, orient="horizontal").pack(fill="x", padx=16)

        # Two-column body
        body = ttk.Frame(self.root, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1, uniform="col")
        body.columnconfigure(1, weight=1, uniform="col")
        body.rowconfigure(0, weight=1)

        self._build_left(body)
        self._build_right(body)

        # Footer
        footer = ttk.Frame(self.root, padding=(16, 6, 16, 12))
        footer.pack(fill="x")
        ttk.Label(footer, textvariable=self.status_var, foreground="#999").pack(side="left")
        ttk.Button(footer, text="⚙  Settings", command=self.open_settings).pack(side="right")
        ttk.Button(footer, text="📋  Batch / Setlist", command=self.open_batch).pack(side="right", padx=(0, 8))

    def _build_left(self, parent) -> None:
        card = ttk.LabelFrame(parent, text="  Download  ", padding=14)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        # URL row
        ttk.Label(card, text="YouTube URL").grid(row=0, column=0, sticky="w")
        url_row = ttk.Frame(card)
        url_row.grid(row=1, column=0, sticky="ew", pady=(2, 12))
        url_row.columnconfigure(0, weight=1)
        ttk.Entry(url_row, textvariable=self.url_var).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(url_row, text="Fetch", command=self.on_fetch, style="Accent.TButton").grid(row=0, column=1)
        ttk.Button(url_row, text="Search…", command=self.open_search).grid(row=0, column=2, padx=(6, 0))

        # Metadata block: thumb + text
        meta = ttk.Frame(card)
        meta.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        meta.columnconfigure(1, weight=1)

        self.thumb_canvas = Canvas(meta, width=THUMB_W, height=THUMB_H, highlightthickness=0, bd=0)
        self.thumb_canvas.grid(row=0, column=0, rowspan=3, padx=(0, 12))

        ttk.Label(meta, textvariable=self.title_var, font=("TkDefaultFont", 12, "bold"),
                  wraplength=320, justify="left").grid(row=0, column=1, sticky="w")
        ttk.Label(meta, textvariable=self.uploader_var, foreground="#999").grid(row=1, column=1, sticky="w", pady=(4, 0))
        ttk.Label(meta, textvariable=self.duration_var, foreground="#999").grid(row=2, column=1, sticky="w", pady=(2, 0))

        # Output dir
        ttk.Label(card, text="Output folder").grid(row=3, column=0, sticky="w")
        out_row = ttk.Frame(card)
        out_row.grid(row=4, column=0, sticky="ew", pady=(2, 14))
        out_row.columnconfigure(0, weight=1)
        ttk.Entry(out_row, textvariable=self.output_var).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(out_row, text="Browse…", command=self.on_browse).grid(row=0, column=1)
        ttk.Button(out_row, text="📂 Open", command=self.on_open_output).grid(row=0, column=2, padx=(6, 0))

        # Buttons
        btns = ttk.Frame(card)
        btns.grid(row=5, column=0, sticky="ew", pady=(0, 10))
        btns.columnconfigure((0, 1, 2), weight=1)
        self.btn_video = ttk.Button(btns, text="Video (MP4)", command=lambda: self.on_download("video"))
        self.btn_audio = ttk.Button(btns, text="Audio (MP3)", command=lambda: self.on_download("audio"))
        self.btn_all = ttk.Button(btns, text="All + Lyrics", command=lambda: self.on_download("all"),
                                  style="Accent.TButton")
        self.btn_video.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.btn_audio.grid(row=0, column=1, sticky="ew", padx=4)
        self.btn_all.grid(row=0, column=2, sticky="ew", padx=(4, 0))

        # Progress
        self.progress = ttk.Progressbar(card, maximum=100)
        self.progress.grid(row=6, column=0, sticky="ew", pady=(4, 0))

        card.columnconfigure(0, weight=1)

    def _build_right(self, parent) -> None:
        card = ttk.LabelFrame(parent, text="  Lyrics  ", padding=14)
        card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        card.columnconfigure(0, weight=1)
        card.rowconfigure(3, weight=1)

        # Artist / Song
        fields = ttk.Frame(card)
        fields.grid(row=0, column=0, sticky="ew")
        fields.columnconfigure((1, 3), weight=1)
        ttk.Label(fields, text="Artist").grid(row=0, column=0, sticky="w", padx=(0, 6))
        ttk.Entry(fields, textvariable=self.artist_var).grid(row=0, column=1, sticky="ew", padx=(0, 12))
        ttk.Label(fields, text="Song").grid(row=0, column=2, sticky="w", padx=(0, 6))
        ttk.Entry(fields, textvariable=self.song_var).grid(row=0, column=3, sticky="ew")

        # Source row
        src = ttk.Frame(card)
        src.grid(row=1, column=0, sticky="ew", pady=(10, 6))
        ttk.Label(src, text="Source:").pack(side="left", padx=(0, 8))
        ttk.Radiobutton(src, text="lyrics.ovh", variable=self.source_var, value="lyrics.ovh").pack(side="left", padx=(0, 8))
        self.genius_radio = ttk.Radiobutton(src, text="Genius", variable=self.source_var, value="genius")
        self.genius_radio.pack(side="left")
        ttk.Button(src, text="Search Lyrics", command=self.on_search_lyrics,
                   style="Accent.TButton").pack(side="right")
        ttk.Button(src, text="▶  Teleprompter", command=self.open_teleprompter).pack(side="right", padx=(0, 8))

        ttk.Separator(card, orient="horizontal").grid(row=2, column=0, sticky="ew", pady=(6, 8))

        # Lyrics text area
        box = ttk.Frame(card)
        box.grid(row=3, column=0, sticky="nsew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.lyrics_text = Text(box, wrap="word", relief="flat", borderwidth=0,
                                font=("TkDefaultFont", 11), padx=8, pady=8)
        scroll = Scrollbar(box, command=self.lyrics_text.yview)
        self.lyrics_text.configure(yscrollcommand=scroll.set)
        self.lyrics_text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self._theme_lyrics_text()

    def _theme_lyrics_text(self) -> None:
        if sv_ttk.get_theme() == "dark":
            self.lyrics_text.configure(bg="#1f1f1f", fg="#e6e6e6", insertbackground="#e6e6e6")
        else:
            self.lyrics_text.configure(bg="#ffffff", fg="#202020", insertbackground="#202020")

    # ---------- theme ----------
    def on_toggle_theme(self) -> None:
        theme = "dark" if self.theme_var.get() else "light"
        sv_ttk.set_theme(theme)
        self.config["theme"] = theme
        save_config(self.config)
        self._theme_lyrics_text()

    # ---------- thumbnail ----------
    def _set_thumbnail(self, pil_img: Image.Image) -> None:
        pil_img = pil_img.copy()
        pil_img.thumbnail((THUMB_W, THUMB_H))
        canvas_img = Image.new("RGB", (THUMB_W, THUMB_H), (28, 28, 32))
        ox = (THUMB_W - pil_img.width) // 2
        oy = (THUMB_H - pil_img.height) // 2
        canvas_img.paste(pil_img, (ox, oy))
        self._thumb_img = ImageTk.PhotoImage(canvas_img)
        self.thumb_canvas.delete("all")
        self.thumb_canvas.create_image(0, 0, anchor="nw", image=self._thumb_img)

    # ---------- helpers ----------
    def _refresh_genius_radio(self) -> None:
        token = self.config.get("genius_token", "").strip()
        if token and HAVE_GENIUS:
            self.genius_radio.state(["!disabled"])
        else:
            self.genius_radio.state(["disabled"])
            if self.source_var.get() == "genius":
                self.source_var.set("lyrics.ovh")

    def _set_status(self, text: str) -> None:
        self.status_var.set(text)

    def _set_progress(self, value: float) -> None:
        self.progress["value"] = value

    def _set_buttons_enabled(self, enabled: bool) -> None:
        state = ["!disabled"] if enabled else ["disabled"]
        self.btn_video.state(state)
        self.btn_audio.state(state)
        self.btn_all.state(state)

    # ---------- browse / fetch ----------
    def on_browse(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.output_var.get() or str(DEFAULT_DOWNLOADS))
        if folder:
            self.output_var.set(folder)
            self.config["default_output_dir"] = folder
            save_config(self.config)

    def on_open_output(self) -> None:
        open_in_finder(self.output_var.get() or str(DEFAULT_DOWNLOADS))

    def on_fetch(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror("Dnlod", "Paste a YouTube URL first.")
            return
        self._set_status("Fetching metadata…")
        threading.Thread(target=self._fetch_worker, args=(url,), daemon=True).start()

    def _fetch_worker(self, url: str) -> None:
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}) as ydl:
                info = ydl.extract_info(url, download=False)
            title = info.get("title", "")
            uploader = info.get("uploader") or info.get("channel") or "—"
            duration = format_duration(info.get("duration"))
            thumb_url = info.get("thumbnail")
            artist, song = parse_title(title)
            self.root.after(0, self._apply_metadata, title, uploader, duration, artist, song)
            if thumb_url:
                try:
                    r = requests.get(thumb_url, timeout=10)
                    r.raise_for_status()
                    img = Image.open(io.BytesIO(r.content)).convert("RGB")
                    self.root.after(0, self._set_thumbnail, img)
                except Exception:
                    pass
        except Exception as exc:
            self.root.after(0, self._fetch_failed, str(exc))

    def _apply_metadata(self, title: str, uploader: str, duration: str, artist: str, song: str) -> None:
        self.title_var.set(title or "(untitled)")
        self.uploader_var.set(f"👤  {uploader}")
        self.duration_var.set(f"⏱  {duration}")
        self.artist_var.set(artist)
        self.song_var.set(song)
        self._set_status("Metadata loaded.")
        if song:
            self.lyrics_text.delete("1.0", END)
            self.lyrics_text.insert(END, "Searching lyrics…")
            threading.Thread(target=self._auto_lyrics_worker, args=(artist, song), daemon=True).start()

    def _fetch_failed(self, msg: str) -> None:
        self._set_status("Fetch failed.")
        messagebox.showerror("Dnlod — fetch failed", msg)

    # ---------- download ----------
    def on_download(self, mode: str) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror("Dnlod", "Paste a YouTube URL first.")
            return
        outdir = Path(self.output_var.get()).expanduser()
        try:
            outdir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Dnlod", f"Cannot create output dir:\n{exc}")
            return
        self._set_progress(0)
        self._set_status(f"Starting {mode} download…")
        self._set_buttons_enabled(False)
        threading.Thread(target=self._download_worker, args=(url, outdir, mode), daemon=True).start()

    def _download_worker(self, url: str, outdir: Path, mode: str) -> None:
        outtmpl = str(outdir / "%(title)s.%(ext)s")

        def make_hook(label: str):
            def hook(d: dict) -> None:
                if d.get("status") == "downloading":
                    total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                    done = d.get("downloaded_bytes", 0)
                    if total:
                        pct = done * 100 / total
                        self.root.after(0, self._set_progress, pct)
                        self.root.after(0, self._set_status, f"{label}… {pct:.1f}%")
                elif d.get("status") == "finished":
                    self.root.after(0, self._set_status, f"{label}: post-processing…")
            return hook

        audio_opts = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "progress_hooks": [make_hook("Audio")],
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
            ],
            **({"ffmpeg_location": FFMPEG_DIR} if FFMPEG_DIR else {}),
        }
        video_opts = {
            "format": "bestvideo*+bestaudio/best",
            "merge_output_format": "mp4",
            "outtmpl": outtmpl,
            "progress_hooks": [make_hook("Video")],
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            **({"ffmpeg_location": FFMPEG_DIR} if FFMPEG_DIR else {}),
        }

        try:
            title = None
            if mode == "audio":
                with yt_dlp.YoutubeDL(audio_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    title = info.get("title")
            elif mode == "video":
                with yt_dlp.YoutubeDL(video_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    title = info.get("title")
            elif mode == "all":
                self.root.after(0, self._set_status, "Downloading video…")
                with yt_dlp.YoutubeDL(video_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    title = info.get("title")
                self.root.after(0, self._set_progress, 0)
                self.root.after(0, self._set_status, "Downloading audio…")
                with yt_dlp.YoutubeDL(audio_opts) as ydl:
                    ydl.extract_info(url, download=True)
                self.root.after(0, self._set_status, "Fetching lyrics…")
                self._save_lyrics_file(outdir, title)
            self.root.after(0, self._download_done, mode, outdir)
        except Exception as exc:
            self.root.after(0, self._download_failed, str(exc))

    def _save_lyrics_file(self, outdir: Path, title: str | None) -> None:
        artist = self.artist_var.get().strip()
        song = self.song_var.get().strip()
        if not song and title:
            artist, song = parse_title(title)
        if not song:
            return
        source = self.source_var.get()
        try:
            lyrics = self._fetch_genius(artist, song) if source == "genius" else self._fetch_lyrics_ovh(artist, song)
        except Exception as exc:
            lyrics = f"(Lyrics fetch failed: {exc})"
        base = title or song
        safe = re.sub(r'[\\/:*?"<>|]+', "_", base).strip() or "lyrics"
        path = outdir / f"{safe}.txt"
        header = f"{artist} - {song}\n" if artist else f"{song}\n"
        path.write_text(header + ("=" * len(header.strip())) + "\n\n" + (lyrics or "Lyrics not found."))
        self.root.after(0, self.lyrics_text.delete, "1.0", END)
        self.root.after(0, self.lyrics_text.insert, END, lyrics or "Lyrics not found.")

    def _download_done(self, mode: str, outdir: Path) -> None:
        self._set_progress(100)
        label = "All (MP4 + MP3 + lyrics.txt)" if mode == "all" else mode
        self._set_status(f"Done ({label}). Saved to {outdir}")
        self._set_buttons_enabled(True)

    def _download_failed(self, msg: str) -> None:
        self._set_status("Download failed.")
        self._set_buttons_enabled(True)
        messagebox.showerror("Dnlod — download failed", msg)

    # ---------- lyrics ----------
    def _auto_lyrics_worker(self, artist: str, song: str) -> None:
        use_genius = bool(self.config.get("genius_token", "").strip()) and HAVE_GENIUS
        lyrics = ""
        if use_genius:
            try:
                lyrics = self._fetch_genius(artist, song)
            except Exception:
                pass
        if not lyrics:
            try:
                lyrics = self._fetch_lyrics_ovh(artist, song)
            except Exception as exc:
                self.root.after(0, self._show_lyrics, f"Lyrics fetch failed: {exc}")
                return
        self.root.after(0, self._show_lyrics, lyrics or "Lyrics not found.")

    def on_search_lyrics(self) -> None:
        artist = self.artist_var.get().strip()
        song = self.song_var.get().strip()
        if not song:
            messagebox.showerror("Dnlod", "Enter at least a song title.")
            return
        self.lyrics_text.delete("1.0", END)
        self.lyrics_text.insert(END, "Searching lyrics…")
        source = self.source_var.get()
        threading.Thread(target=self._lyrics_worker, args=(artist, song, source), daemon=True).start()

    def _lyrics_worker(self, artist: str, song: str, source: str) -> None:
        try:
            if source == "genius":
                lyrics = self._fetch_genius(artist, song)
            else:
                lyrics = self._fetch_lyrics_ovh(artist, song)
        except Exception as exc:
            self.root.after(0, self._show_lyrics, f"Lyrics fetch failed: {exc}")
            return
        self.root.after(0, self._show_lyrics, lyrics or "Lyrics not found.")

    def _fetch_lyrics_ovh(self, artist: str, song: str) -> str:
        artist_q = requests.utils.quote(artist or " ")
        song_q = requests.utils.quote(song)
        resp = requests.get(f"https://api.lyrics.ovh/v1/{artist_q}/{song_q}", timeout=15)
        if resp.status_code == 404:
            return ""
        resp.raise_for_status()
        return (resp.json().get("lyrics") or "").strip()

    def _fetch_genius(self, artist: str, song: str) -> str:
        token = self.config.get("genius_token", "").strip()
        if not token or not HAVE_GENIUS:
            return ""
        genius = lyricsgenius.Genius(token, remove_section_headers=False, skip_non_songs=True, timeout=15)
        hit = genius.search_song(song, artist) if artist else genius.search_song(song)
        if hit is None:
            return ""
        return (hit.lyrics or "").strip()

    def _show_lyrics(self, text: str) -> None:
        self.lyrics_text.delete("1.0", END)
        self.lyrics_text.insert(END, text)

    # ---------- settings ----------
    def open_settings(self) -> None:
        dlg = Toplevel(self.root)
        dlg.title("Settings")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        dlg.geometry("560x220")
        dlg.grab_set()

        token_var = StringVar(value=self.config.get("genius_token", ""))
        dir_var = StringVar(value=self.config.get("default_output_dir", str(DEFAULT_DOWNLOADS)))

        frm = ttk.Frame(dlg, padding=16)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Genius API token", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Entry(frm, textvariable=token_var, show="•").grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 4))
        ttk.Label(frm, text="Get one free at genius.com/api-clients", foreground="#888").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(0, 12)
        )

        ttk.Label(frm, text="Default output folder", font=("TkDefaultFont", 10, "bold")).grid(row=3, column=0, sticky="w")
        path_row = ttk.Frame(frm)
        path_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        path_row.columnconfigure(0, weight=1)
        ttk.Entry(path_row, textvariable=dir_var).grid(row=0, column=0, sticky="ew", padx=(0, 8))

        def pick():
            d = filedialog.askdirectory(initialdir=dir_var.get() or str(DEFAULT_DOWNLOADS), parent=dlg)
            if d:
                dir_var.set(d)

        ttk.Button(path_row, text="Browse…", command=pick).grid(row=0, column=1)
        ttk.Button(path_row, text="📂 Open", command=lambda: open_in_finder(dir_var.get())).grid(row=0, column=2, padx=(6, 0))

        def save_and_close():
            self.config["genius_token"] = token_var.get().strip()
            self.config["default_output_dir"] = dir_var.get().strip() or str(DEFAULT_DOWNLOADS)
            save_config(self.config)
            self.output_var.set(self.config["default_output_dir"])
            self._refresh_genius_radio()
            dlg.destroy()

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="Save", command=save_and_close, style="Accent.TButton").pack(side="right")

        frm.columnconfigure(0, weight=1)


    # ---------- teleprompter ----------
    def open_teleprompter(self) -> None:
        lyrics = self.lyrics_text.get("1.0", END).strip()
        if not lyrics or lyrics.lower().startswith("searching"):
            messagebox.showinfo("Teleprompter", "Search lyrics first, then open the teleprompter.")
            return
        Teleprompter(self.root, lyrics, dark=(sv_ttk.get_theme() == "dark"))

    # ---------- batch / setlist ----------
    def open_batch(self) -> None:
        BatchDialog(self.root, self)

    # ---------- search ----------
    def open_search(self) -> None:
        SearchDialog(self.root, self)


class Teleprompter(Toplevel):
    """Full-screen-ish scrolling lyrics view for live performance practice."""

    def __init__(self, parent: Tk, lyrics: str, dark: bool = True) -> None:
        super().__init__(parent)
        self.title("Teleprompter — Dnlod")
        self.geometry("900x700")
        self.minsize(560, 400)

        self.playing = False
        self.speed = 25  # 0..100, scroll units per tick
        self.font_size = 32
        self.mirror = False
        self.bg = "#0a0a0a" if dark else "#fafafa"
        self.fg = "#f0f0f0" if dark else "#101010"

        self.configure(bg=self.bg)

        # Controls bar
        bar = ttk.Frame(self, padding=(12, 10))
        bar.pack(fill="x")
        self.btn_play = ttk.Button(bar, text="▶  Play", command=self.toggle_play, style="Accent.TButton")
        self.btn_play.pack(side="left")

        ttk.Label(bar, text="Speed").pack(side="left", padx=(16, 4))
        self.speed_scale = ttk.Scale(bar, from_=0, to=100, value=self.speed,
                                     length=160, command=lambda v: self._set_speed(float(v)))
        self.speed_scale.pack(side="left")

        ttk.Label(bar, text="Font").pack(side="left", padx=(16, 4))
        self.font_scale = ttk.Scale(bar, from_=14, to=80, value=self.font_size,
                                    length=160, command=lambda v: self._set_font(float(v)))
        self.font_scale.pack(side="left")

        self.mirror_var = BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Mirror", variable=self.mirror_var,
                        command=self._apply_mirror).pack(side="left", padx=(16, 0))

        ttk.Button(bar, text="Restart", command=self.restart).pack(side="right")
        ttk.Button(bar, text="✕  Close", command=self.destroy).pack(side="right", padx=(0, 8))

        # Lyrics text
        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True, padx=24, pady=(8, 24))
        self.text = Text(wrap, wrap="word", relief="flat", borderwidth=0,
                         padx=24, pady=24, spacing1=6, spacing3=6)
        self.text.pack(fill="both", expand=True)
        self.text.insert(END, "\n" * 8 + lyrics + "\n" * 20)
        self._apply_font()
        self._apply_colors()
        self.text.configure(state="disabled")

        # Keyboard
        self.bind("<space>", lambda e: self.toggle_play())
        self.bind("<Up>", lambda e: self._nudge_speed(+5))
        self.bind("<Down>", lambda e: self._nudge_speed(-5))
        self.bind("<plus>", lambda e: self._nudge_font(+2))
        self.bind("<minus>", lambda e: self._nudge_font(-2))
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<F11>", lambda e: self._toggle_fullscreen())
        self.focus_set()

        self._tick()

    def _apply_font(self) -> None:
        self.text.configure(font=("TkDefaultFont", int(self.font_size), "bold"))

    def _apply_colors(self) -> None:
        self.text.configure(bg=self.bg, fg=self.fg, insertbackground=self.fg)

    def _apply_mirror(self) -> None:
        self.mirror = self.mirror_var.get()

    def _set_speed(self, v: float) -> None:
        self.speed = max(0, min(100, int(v)))

    def _nudge_speed(self, delta: int) -> None:
        self.speed = max(0, min(100, self.speed + delta))
        self.speed_scale.set(self.speed)

    def _set_font(self, v: float) -> None:
        self.font_size = int(v)
        self._apply_font()

    def _nudge_font(self, delta: int) -> None:
        self.font_size = max(10, min(100, self.font_size + delta))
        self.font_scale.set(self.font_size)
        self._apply_font()

    def toggle_play(self) -> None:
        self.playing = not self.playing
        self.btn_play.configure(text="❚❚  Pause" if self.playing else "▶  Play")

    def restart(self) -> None:
        self.text.yview_moveto(0.0)

    def _toggle_fullscreen(self) -> None:
        try:
            self.attributes("-fullscreen", not bool(self.attributes("-fullscreen")))
        except Exception:
            pass

    def _tick(self) -> None:
        if self.playing and self.speed > 0:
            frac = self.speed / 12000.0  # tuned for ~60fps feel
            cur = self.text.yview()[0]
            new = cur + frac
            if new < 1.0:
                self.text.yview_moveto(new)
            else:
                self.playing = False
                self.btn_play.configure(text="▶  Play")
        if self.winfo_exists():
            self.after(33, self._tick)


class BatchDialog(Toplevel):
    """Batch downloader: paste many URLs, pick a mode, run sequentially."""

    def __init__(self, parent: Tk, app: "DnlodApp") -> None:
        super().__init__(parent)
        self.app = app
        self.title("Batch / Setlist — Dnlod")
        self.geometry("820x600")
        self.minsize(680, 520)

        self.mode_var = StringVar(value="all")
        self.outdir_var = StringVar(value=app.output_var.get())
        self._stop = threading.Event()
        self._running = False

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)

        # URL input
        ttk.Label(body, text="URLs  (one per line)", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.url_text = Text(body, height=7, wrap="none", font=("TkDefaultFont", 10))
        self.url_text.grid(row=1, column=0, sticky="ew", pady=(4, 10))
        self._theme_text(self.url_text)

        # Options row
        opts = ttk.Frame(body)
        opts.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        opts.columnconfigure(4, weight=1)
        ttk.Label(opts, text="Mode:").grid(row=0, column=0, padx=(0, 6))
        ttk.Radiobutton(opts, text="Video", variable=self.mode_var, value="video").grid(row=0, column=1, padx=(0, 6))
        ttk.Radiobutton(opts, text="Audio", variable=self.mode_var, value="audio").grid(row=0, column=2, padx=(0, 6))
        ttk.Radiobutton(opts, text="All + Lyrics", variable=self.mode_var, value="all").grid(row=0, column=3, padx=(0, 16))
        ttk.Label(opts, text="Output:").grid(row=0, column=4, sticky="e", padx=(0, 6))
        ttk.Entry(opts, textvariable=self.outdir_var, width=32).grid(row=0, column=5, sticky="ew")
        ttk.Button(opts, text="Browse…", command=self._browse).grid(row=0, column=6, padx=(6, 0))
        ttk.Button(opts, text="📂 Open", command=lambda: open_in_finder(self.outdir_var.get())).grid(row=0, column=7, padx=(6, 0))

        # Queue table
        cols = ("status", "progress", "title")
        self.tree = ttk.Treeview(body, columns=cols, show="headings", height=12)
        self.tree.heading("status", text="Status")
        self.tree.heading("progress", text="%")
        self.tree.heading("title", text="URL / Title")
        self.tree.column("status", width=110, anchor="w")
        self.tree.column("progress", width=60, anchor="e")
        self.tree.column("title", width=520, anchor="w")
        self.tree.grid(row=3, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        vsb.grid(row=3, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=vsb.set)

        # Buttons
        btns = ttk.Frame(body)
        btns.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.btn_start = ttk.Button(btns, text="Start", command=self.start, style="Accent.TButton")
        self.btn_start.pack(side="right")
        self.btn_stop = ttk.Button(btns, text="Stop after current", command=self.request_stop)
        self.btn_stop.pack(side="right", padx=(0, 8))
        self.btn_stop.state(["disabled"])
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="left")

    def _theme_text(self, widget: Text) -> None:
        if sv_ttk.get_theme() == "dark":
            widget.configure(bg="#1f1f1f", fg="#e6e6e6", insertbackground="#e6e6e6", relief="flat", borderwidth=0)
        else:
            widget.configure(bg="#ffffff", fg="#202020", insertbackground="#202020", relief="flat", borderwidth=0)

    def _browse(self) -> None:
        d = filedialog.askdirectory(initialdir=self.outdir_var.get() or str(DEFAULT_DOWNLOADS), parent=self)
        if d:
            self.outdir_var.set(d)

    def request_stop(self) -> None:
        self._stop.set()
        self.btn_stop.state(["disabled"])

    def start(self) -> None:
        if self._running:
            return
        raw = self.url_text.get("1.0", END)
        urls = [u.strip() for u in raw.splitlines() if u.strip()]
        if not urls:
            messagebox.showerror("Batch", "Paste at least one URL.")
            return
        outdir = Path(self.outdir_var.get()).expanduser()
        try:
            outdir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Batch", f"Cannot create output dir:\n{exc}")
            return

        self.tree.delete(*self.tree.get_children())
        self._rows = {}
        for u in urls:
            iid = self.tree.insert("", END, values=("Queued", "0", u))
            self._rows[iid] = u

        self._running = True
        self._stop.clear()
        self.btn_start.state(["disabled"])
        self.btn_stop.state(["!disabled"])
        threading.Thread(target=self._run, args=(outdir, self.mode_var.get()), daemon=True).start()

    def _run(self, outdir: Path, mode: str) -> None:
        for iid, url in list(self._rows.items()):
            if self._stop.is_set():
                self.after(0, self._update_row, iid, "Skipped", 0, url)
                continue
            self.after(0, self._update_row, iid, "Running", 0, url)
            try:
                title = self._download_one(url, outdir, mode, iid)
                self.after(0, self._update_row, iid, "Done", 100, title or url)
            except Exception as exc:
                self.after(0, self._update_row, iid, f"Failed", 0, f"{url}  —  {exc}")
        self.after(0, self._finish)

    def _finish(self) -> None:
        self._running = False
        self.btn_start.state(["!disabled"])
        self.btn_stop.state(["disabled"])

    def _update_row(self, iid: str, status: str, pct: int, title: str) -> None:
        if self.tree.exists(iid):
            self.tree.item(iid, values=(status, int(pct), title))

    def _download_one(self, url: str, outdir: Path, mode: str, iid: str) -> str | None:
        outtmpl = str(outdir / "%(title)s.%(ext)s")

        def hook(d: dict) -> None:
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes", 0)
                if total:
                    pct = done * 100 / total
                    self.after(0, self._update_row, iid, "Running", pct, d.get("info_dict", {}).get("title", ""))

        audio_opts = {
            "format": "bestaudio/best", "outtmpl": outtmpl, "progress_hooks": [hook],
            "quiet": True, "no_warnings": True, "noplaylist": True,
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
            **({"ffmpeg_location": FFMPEG_DIR} if FFMPEG_DIR else {}),
        }
        video_opts = {
            "format": "bestvideo*+bestaudio/best", "merge_output_format": "mp4",
            "outtmpl": outtmpl, "progress_hooks": [hook],
            "quiet": True, "no_warnings": True, "noplaylist": True,
            **({"ffmpeg_location": FFMPEG_DIR} if FFMPEG_DIR else {}),
        }

        title = None
        if mode in ("video", "all"):
            with yt_dlp.YoutubeDL(video_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get("title")
        if mode in ("audio", "all"):
            with yt_dlp.YoutubeDL(audio_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = title or info.get("title")
        if mode == "all":
            self._save_lyrics(outdir, title)
        return title

    def _save_lyrics(self, outdir: Path, title: str | None) -> None:
        if not title:
            return
        artist, song = parse_title(title)
        if not song:
            return
        token = self.app.config.get("genius_token", "").strip()
        source = self.app.source_var.get()
        try:
            if source == "genius" and token and HAVE_GENIUS:
                lyrics = self.app._fetch_genius(artist, song)
            else:
                lyrics = self.app._fetch_lyrics_ovh(artist, song)
        except Exception as exc:
            lyrics = f"(Lyrics fetch failed: {exc})"
        safe = re.sub(r'[\\/:*?"<>|]+', "_", title).strip() or "lyrics"
        header = f"{artist} - {song}\n" if artist else f"{song}\n"
        (outdir / f"{safe}.txt").write_text(
            header + ("=" * len(header.strip())) + "\n\n" + (lyrics or "Lyrics not found.")
        )


class SearchDialog(Toplevel):
    """Search YouTube by name, pick a result, and load it into the main window."""

    def __init__(self, parent: Tk, app: "DnlodApp") -> None:
        super().__init__(parent)
        self.app = app
        self.title("Search YouTube — Dnlod")
        self.geometry("760x460")
        self.minsize(600, 380)
        self.transient(parent)
        self.grab_set()

        self._results: list[dict] = []

        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        # Search input row
        search_row = ttk.Frame(body)
        search_row.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        search_row.columnconfigure(0, weight=1)
        self.query_var = StringVar()
        self.entry = ttk.Entry(search_row, textvariable=self.query_var, font=("TkDefaultFont", 12))
        self.entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.btn_search = ttk.Button(search_row, text="Search", command=self._do_search, style="Accent.TButton")
        self.btn_search.grid(row=0, column=1)
        self.entry.bind("<Return>", lambda e: self._do_search())

        self.status_lbl = ttk.Label(body, text="Type a song or video name and press Search.", foreground="#888")
        self.status_lbl.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 6))

        # Results list
        cols = ("title", "uploader", "duration")
        self.tree = ttk.Treeview(body, columns=cols, show="headings", height=12, selectmode="browse")
        self.tree.heading("title", text="Title")
        self.tree.heading("uploader", text="Channel")
        self.tree.heading("duration", text="Duration")
        self.tree.column("title", width=420, anchor="w")
        self.tree.column("uploader", width=180, anchor="w")
        self.tree.column("duration", width=80, anchor="e")
        self.tree.grid(row=2, column=0, sticky="nsew")
        vsb = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        vsb.grid(row=2, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.bind("<Double-1>", lambda e: self._select())
        self.tree.bind("<Return>", lambda e: self._select())

        # Bottom buttons
        btns = ttk.Frame(body)
        btns.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left")
        self.btn_select = ttk.Button(btns, text="Select & Load", command=self._select, style="Accent.TButton")
        self.btn_select.pack(side="right")
        self.btn_select.state(["disabled"])

        self.entry.focus_set()

    def _do_search(self) -> None:
        query = self.query_var.get().strip()
        if not query:
            return
        self.btn_search.state(["disabled"])
        self.btn_select.state(["disabled"])
        self.tree.delete(*self.tree.get_children())
        self._results = []
        self.status_lbl.configure(text="Searching…")
        threading.Thread(target=self._search_worker, args=(query,), daemon=True).start()

    def _search_worker(self, query: str) -> None:
        try:
            opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "noplaylist": True}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"ytsearch10:{query}", download=False)
            entries = list((info or {}).get("entries") or [])
            self.after(0, self._populate_results, entries)
        except Exception as exc:
            self.after(0, self._search_failed, str(exc))

    def _populate_results(self, entries: list) -> None:
        self.btn_search.state(["!disabled"])
        if not entries:
            self.status_lbl.configure(text="No results found.")
            return
        self._results = entries
        for e in entries:
            title = e.get("title") or "(untitled)"
            uploader = e.get("uploader") or e.get("channel") or "—"
            duration = format_duration(e.get("duration"))
            self.tree.insert("", END, values=(title, uploader, duration))
        self.status_lbl.configure(text=f"{len(entries)} result(s). Double-click or press Select & Load.")
        first = self.tree.get_children()[0]
        self.tree.selection_set(first)
        self.tree.focus(first)
        self.btn_select.state(["!disabled"])

    def _search_failed(self, msg: str) -> None:
        self.btn_search.state(["!disabled"])
        self.status_lbl.configure(text=f"Search failed: {msg}")

    def _select(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        idx = self.tree.index(sel[0])
        if idx >= len(self._results):
            return
        entry = self._results[idx]
        vid_id = entry.get("id", "")
        url = entry.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else "")
        if not url:
            return
        self.app.url_var.set(url)
        self.destroy()
        self.app.on_fetch()


def main() -> None:
    root = Tk()
    DnlodApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
