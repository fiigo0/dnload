#!/usr/bin/env python3
"""Dnlod — paste a YouTube URL, download video/audio, fetch lyrics."""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from tkinter import (
    Tk,
    Toplevel,
    StringVar,
    END,
    DISABLED,
    NORMAL,
    filedialog,
    messagebox,
    ttk,
    Text,
    Scrollbar,
)

import requests
import yt_dlp

try:
    import lyricsgenius
    HAVE_GENIUS = True
except ImportError:
    HAVE_GENIUS = False

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
DEFAULT_DOWNLOADS = APP_DIR / "downloads"


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {"genius_token": "", "default_output_dir": str(DEFAULT_DOWNLOADS)}


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


class DnlodApp:
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("Dnlod — YouTube downloader + lyrics")
        self.root.geometry("720x720")

        self.config = load_config()
        DEFAULT_DOWNLOADS.mkdir(exist_ok=True)

        self.url_var = StringVar()
        self.title_var = StringVar(value="(no video fetched)")
        self.output_var = StringVar(value=self.config.get("default_output_dir", str(DEFAULT_DOWNLOADS)))
        self.artist_var = StringVar()
        self.song_var = StringVar()
        self.source_var = StringVar(value="lyrics.ovh")
        self.status_var = StringVar(value="Ready.")

        self._build_ui()
        self._refresh_genius_radio()

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        url_frame = ttk.Frame(self.root)
        url_frame.pack(fill="x", **pad)
        ttk.Label(url_frame, text="YouTube URL:").pack(side="left")
        ttk.Entry(url_frame, textvariable=self.url_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(url_frame, text="Fetch", command=self.on_fetch).pack(side="left")

        title_frame = ttk.Frame(self.root)
        title_frame.pack(fill="x", **pad)
        ttk.Label(title_frame, text="Title:").pack(side="left")
        ttk.Label(title_frame, textvariable=self.title_var, foreground="#444").pack(side="left", padx=6)

        out_frame = ttk.Frame(self.root)
        out_frame.pack(fill="x", **pad)
        ttk.Label(out_frame, text="Output dir:").pack(side="left")
        ttk.Entry(out_frame, textvariable=self.output_var).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(out_frame, text="Browse…", command=self.on_browse).pack(side="left")

        btn_frame = ttk.Frame(self.root)
        btn_frame.pack(fill="x", **pad)
        self.btn_video = ttk.Button(btn_frame, text="Download Video (MP4)", command=lambda: self.on_download("video"))
        self.btn_video.pack(side="left", padx=4)
        self.btn_audio = ttk.Button(btn_frame, text="Download Audio (MP3)", command=lambda: self.on_download("audio"))
        self.btn_audio.pack(side="left", padx=4)
        ttk.Button(btn_frame, text="⚙ Settings", command=self.open_settings).pack(side="right", padx=4)

        prog_frame = ttk.Frame(self.root)
        prog_frame.pack(fill="x", **pad)
        self.progress = ttk.Progressbar(prog_frame, maximum=100)
        self.progress.pack(fill="x", side="left", expand=True)
        ttk.Label(prog_frame, textvariable=self.status_var).pack(side="left", padx=8)

        ttk.Separator(self.root, orient="horizontal").pack(fill="x", pady=8)
        ttk.Label(self.root, text="Lyrics", font=("TkDefaultFont", 12, "bold")).pack(anchor="w", padx=8)

        lyr_top = ttk.Frame(self.root)
        lyr_top.pack(fill="x", **pad)
        ttk.Label(lyr_top, text="Artist:").pack(side="left")
        ttk.Entry(lyr_top, textvariable=self.artist_var, width=22).pack(side="left", padx=4)
        ttk.Label(lyr_top, text="Song:").pack(side="left")
        ttk.Entry(lyr_top, textvariable=self.song_var, width=22).pack(side="left", padx=4)
        ttk.Button(lyr_top, text="Search Lyrics", command=self.on_search_lyrics).pack(side="left", padx=8)

        src_frame = ttk.Frame(self.root)
        src_frame.pack(fill="x", **pad)
        ttk.Label(src_frame, text="Source:").pack(side="left")
        ttk.Radiobutton(src_frame, text="lyrics.ovh", variable=self.source_var, value="lyrics.ovh").pack(side="left")
        self.genius_radio = ttk.Radiobutton(src_frame, text="Genius", variable=self.source_var, value="genius")
        self.genius_radio.pack(side="left")

        lyr_box = ttk.Frame(self.root)
        lyr_box.pack(fill="both", expand=True, **pad)
        self.lyrics_text = Text(lyr_box, wrap="word", height=14)
        scroll = Scrollbar(lyr_box, command=self.lyrics_text.yview)
        self.lyrics_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.lyrics_text.pack(side="left", fill="both", expand=True)

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

    def on_browse(self) -> None:
        folder = filedialog.askdirectory(initialdir=self.output_var.get() or str(DEFAULT_DOWNLOADS))
        if folder:
            self.output_var.set(folder)
            self.config["default_output_dir"] = folder
            save_config(self.config)

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
            artist, song = parse_title(title)
            self.root.after(0, self._apply_metadata, title, artist, song)
        except Exception as exc:
            self.root.after(0, self._fetch_failed, str(exc))

    def _apply_metadata(self, title: str, artist: str, song: str) -> None:
        self.title_var.set(title or "(untitled)")
        self.artist_var.set(artist)
        self.song_var.set(song)
        self._set_status("Metadata loaded.")

    def _fetch_failed(self, msg: str) -> None:
        self._set_status("Fetch failed.")
        messagebox.showerror("Dnlod — fetch failed", msg)

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
        self.btn_video.state(["disabled"])
        self.btn_audio.state(["disabled"])
        threading.Thread(target=self._download_worker, args=(url, outdir, mode), daemon=True).start()

    def _download_worker(self, url: str, outdir: Path, mode: str) -> None:
        outtmpl = str(outdir / "%(title)s.%(ext)s")

        def hook(d: dict) -> None:
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes", 0)
                if total:
                    pct = done * 100 / total
                    self.root.after(0, self._set_progress, pct)
                    self.root.after(0, self._set_status, f"Downloading… {pct:.1f}%")
            elif d.get("status") == "finished":
                self.root.after(0, self._set_status, "Post-processing…")

        if mode == "audio":
            opts = {
                "format": "bestaudio/best",
                "outtmpl": outtmpl,
                "progress_hooks": [hook],
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "postprocessors": [
                    {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
                ],
            }
        else:
            opts = {
                "format": "bestvideo*+bestaudio/best",
                "merge_output_format": "mp4",
                "outtmpl": outtmpl,
                "progress_hooks": [hook],
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
            }

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            self.root.after(0, self._download_done, mode, outdir)
        except Exception as exc:
            self.root.after(0, self._download_failed, str(exc))

    def _download_done(self, mode: str, outdir: Path) -> None:
        self._set_progress(100)
        self._set_status(f"Done. Saved to {outdir}")
        self.btn_video.state(["!disabled"])
        self.btn_audio.state(["!disabled"])

    def _download_failed(self, msg: str) -> None:
        self._set_status("Download failed.")
        self.btn_video.state(["!disabled"])
        self.btn_audio.state(["!disabled"])
        messagebox.showerror("Dnlod — download failed", msg)

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
        genius = lyricsgenius.Genius(token, verbose=False, remove_section_headers=False, timeout=15)
        genius.skip_non_songs = True
        hit = genius.search_song(song, artist) if artist else genius.search_song(song)
        if hit is None:
            return ""
        return (hit.lyrics or "").strip()

    def _show_lyrics(self, text: str) -> None:
        self.lyrics_text.delete("1.0", END)
        self.lyrics_text.insert(END, text)

    def open_settings(self) -> None:
        dlg = Toplevel(self.root)
        dlg.title("Settings")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        dlg.geometry("520x180")

        token_var = StringVar(value=self.config.get("genius_token", ""))
        dir_var = StringVar(value=self.config.get("default_output_dir", str(DEFAULT_DOWNLOADS)))

        frm = ttk.Frame(dlg, padding=12)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Genius API token:").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=token_var, width=48, show="•").grid(row=0, column=1, columnspan=2, sticky="ew", padx=4)

        ttk.Label(frm, text="Default output dir:").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(frm, textvariable=dir_var, width=40).grid(row=1, column=1, sticky="ew", padx=4)

        def pick():
            d = filedialog.askdirectory(initialdir=dir_var.get() or str(DEFAULT_DOWNLOADS), parent=dlg)
            if d:
                dir_var.set(d)

        ttk.Button(frm, text="Browse…", command=pick).grid(row=1, column=2, padx=4)

        info = "Get a free token at https://genius.com/api-clients"
        ttk.Label(frm, text=info, foreground="#666").grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 4))

        def save_and_close():
            self.config["genius_token"] = token_var.get().strip()
            self.config["default_output_dir"] = dir_var.get().strip() or str(DEFAULT_DOWNLOADS)
            save_config(self.config)
            self.output_var.set(self.config["default_output_dir"])
            self._refresh_genius_radio()
            dlg.destroy()

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=3, sticky="e", pady=(8, 0))
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right", padx=4)
        ttk.Button(btns, text="Save", command=save_and_close).pack(side="right")

        frm.columnconfigure(1, weight=1)


def main() -> None:
    root = Tk()
    DnlodApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
