#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Configuration loader for the VD localizer toolchain.

Everything that used to be hard-coded (device serial, bundle GUID, font
record numbers, TTF paths) lives in a single JSON config file so that the
toolchain can be pointed at a different app version / headset / language
without editing a single line of Python.

Usage
-----
    from vdl_config import Config
    cfg = Config.load('config.json')

Precedence:  defaults < config file < CLI overrides (--set a.b=1).
"""
from __future__ import annotations

import json
import os
import sys

DEFAULTS = {
    # Root of all scratch data. Relative paths in this config resolve here.
    "root": ".",

    # ---- physical device -------------------------------------------------
    "device": {
        "serial": "",                       # adb -s <serial>
        "package": "VirtualDesktop.Android",
        "installed_apk": "",                # /data/app/<pkg>-<hash>/base.apk
    },

    # ---- APK layout ------------------------------------------------------
    "apk": {
        "source": "work/base.apk",          # pristine APK (your own copy)
        "data_entry": "assets/data/db/bundles/default.b65fc36561f156d9d380862e7304cc55.bundle",
        "index_entry": "assets/data/db/bundles/default.bundle",
        "output": "work/out.apk",
    },

    # ---- extracted bundles ----------------------------------------------
    "bundles": {
        "data": "work/data.bundle",         # extracted data bundle
        "index": "work/index.bundle",       # extracted index bundle (224 KB)
        "patched_data": "work/patched-data.bundle",
        "patched_index": "work/patched-index.bundle",
    },

    # ---- record table offsets -------------------------------------------
    # Verified on VD 1.34.22.0 (PICO build). Re-derive with tools/probe.py
    # if you port this to another version.
    "table": {
        "data_header": 28,                  # record table starts here
        "index_header": 45,
        "record_size": 46,
        "index_count_offset": 41,           # i32 record count inside index bundle
    },

    # ---- fonts to rebuild ------------------------------------------------
    # rec = record index of the OfflineRasterizedSpriteFont object.
    # Its atlas texture is always rec + 1.
    "fonts": [
        {"rec": 861, "weight": "bold"},
        {"rec": 865, "weight": "regular"},
        {"rec": 1967, "weight": "regular"},
    ],

    # ---- UI pages to patch ----------------------------------------------
    "ui_pages": [869, 954, 1211, 1405, 1506, 1928, 1963, 1969],

    # ---- language --------------------------------------------------------
    "language": {
        "code": "zh-CN",
        "strings": "locales/zh-CN/strings.tsv",
        "fonts": {
            "regular": "fonts/NotoSansSC-Regular.ttf",
            "bold": "fonts/NotoSansSC-Bold.ttf",
        },
        # Always rasterize these extra glyphs even if no translation uses
        # them (punctuation is easy to forget and missing glyphs crash the UI).
        "extra_glyphs": "\u3002\uff0c\u3001\uff1b\uff1a\uff1f\uff01\uff08\uff09"
                        "\u300a\u300b\u3010\u3011\u201c\u201d\u2018\u2019"
                        "\u2014\u2026\u00b7\uff5e\u3000",
    },
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, data: dict, config_path: str | None = None):
        self.data = _deep_merge(DEFAULTS, data)
        self.config_path = config_path
        self.root = os.path.abspath(self.data.get("root", "."))

    # ---- loading ---------------------------------------------------------
    @classmethod
    def load(cls, path: str = "config.json", overrides: list[str] | None = None) -> "Config":
        data = {}
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        elif path and not os.path.exists(path):
            sys.stderr.write(
                f"[config] {path} not found - falling back to defaults.\n"
                f"[config] Copy config.example.json -> config.json and edit it.\n")
        cfg = cls(data, path)
        for item in overrides or []:
            cfg.set_dotted(item)
        return cfg

    # ---- access ----------------------------------------------------------
    def get(self, dotted: str, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set_dotted(self, assignment: str) -> None:
        """--set device.serial=ABC123"""
        if "=" not in assignment:
            raise ValueError(f"bad override {assignment!r}, expected a.b=value")
        dotted, raw = assignment.split("=", 1)
        try:
            value = json.loads(raw)
        except Exception:
            value = raw
        node = self.data
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    def path(self, dotted_or_rel: str) -> str:
        """Resolve a config value (or literal path) against the repo root."""
        value = self.get(dotted_or_rel, dotted_or_rel)
        if os.path.isabs(value):
            return value
        return os.path.normpath(os.path.join(self.root, value))

    # ---- convenience -----------------------------------------------------
    @property
    def font_recs(self) -> list[tuple[int, str]]:
        return [(f["rec"], f.get("weight", "regular")) for f in self.get("fonts", [])]

    @property
    def ui_pages(self) -> list[int]:
        return list(self.get("ui_pages", []))

    def ttf(self, weight: str) -> str:
        return self.path(f"language.fonts.{weight}")

    def __repr__(self) -> str:
        return f"<Config {self.config_path} root={self.root}>"
