"""py2app setup script for the Nibblet menu bar app.

Build a distributable bundle:

    python3 -m pip install py2app rumps bleak
    python3 tools/setup_app.py py2app

The bundle ends up at ``dist/Nibblet.app``. Drag it into ``/Applications``
or launch it with ``open dist/Nibblet.app``. The first BLE connect will
trigger a macOS Bluetooth permission prompt — grant it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from setuptools import setup

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent

# Make sibling modules importable so py2app can discover them.
sys.path.insert(0, str(HERE))

# Resolve paths relative to the repo root regardless of where the user
# invoked the script from.
os.chdir(REPO_ROOT)

APP = ["tools/nibblet_app.py"]
DATA_FILES: list[str] = []
OPTIONS = {
    "argv_emulation": False,
    "plist": {
        "CFBundleName": "Nibblet",
        "CFBundleDisplayName": "Nibblet",
        "CFBundleIdentifier": "dev.nibblet.bridge",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        # Menu bar only — hide the Dock icon.
        "LSUIElement": True,
        # Required for Core Bluetooth access on macOS 11+.
        "NSBluetoothAlwaysUsageDescription": (
            "Nibblet uses Bluetooth to talk to your M5StickC Plus pet."
        ),
        "NSBluetoothPeripheralUsageDescription": (
            "Nibblet uses Bluetooth to talk to your M5StickC Plus pet."
        ),
    },
    "packages": ["bleak", "rumps"],
    "includes": ["nibblet_bridge", "nibblet_state"],
}

setup(
    app=APP,
    name="Nibblet",
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
