# Documentation

Reverse-engineering notes and operational guides for localising a
Xenko/Stride-based VR application. Everything here was verified against
Virtual Desktop 1.34.22.0 on a PICO 4 (Android 10, rooted), with a
fully-localised Quest 2 build as a cross-check reference.

| page | what is in it |
|---|---|
| [01 — Getting started](01-getting-started.md) | prerequisites, first build, install |
| [02 — Bundle format](02-bundle-format.md) | the `.bundle` container, both files, record tables, LZ4 framing |
| [03 — Font format](03-font-format.md) | `OfflineRasterizedSpriteFont`, variable-length dictionary, atlas pairing |
| [04 — Atlas repacking](04-atlas-repacking.md) | adding glyphs without touching existing pixels |
| [05 — String patching](05-string-patching.md) | the exact-varint rule, why equal-length is not the issue |
| [06 — Device testing](06-device-testing.md) | root install, liveness, crash signatures |
| [07 — Pitfalls](07-pitfalls.md) | **read this first** — every trap, with the test matrix |
| [08 — New language](08-new-language.md) | producing your own localisation |
| [09 — APK packaging](09-apk-packaging.md) | swapping a bundle without breaking the signature |

The reference localisation is `locales/zh-CN` (187 entries, on-device verified).
