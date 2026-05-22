"""py2app setup for Dnlod.

Build:
    source .venv/bin/activate
    python build_icon.py          # produces Dnlod.icns
    python setup.py py2app        # produces dist/Dnlod.app
"""
from setuptools import setup

APP = ["dnlod.py"]

OPTIONS = {
    "argv_emulation": False,
    "iconfile": "Dnlod.icns",
    "resources": ["build_resources/ffmpeg"],
    "packages": [
        "yt_dlp",
        "requests",
        "sv_ttk",
        "PIL",
    ],
    "includes": [
        "lyricsgenius",
        "bs4",
        "soupsieve",
        "certifi",
        "charset_normalizer",
        "idna",
        "urllib3",
    ],
    "excludes": [
        "tkinter.test",
        "py2app",
        "pip",
        "setuptools",
        "wheel",
    ],
    "plist": {
        "CFBundleName": "Dnlod",
        "CFBundleDisplayName": "Dnlod",
        "CFBundleIdentifier": "com.alexdiaz.dnlod",
        "CFBundleVersion": "0.2.0",
        "CFBundleShortVersionString": "0.2.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "11.0",
        "NSHumanReadableCopyright": "© 2026 Alex Diaz",
    },
}

setup(
    app=APP,
    name="Dnlod",
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
