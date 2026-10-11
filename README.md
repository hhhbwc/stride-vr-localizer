**English** · [简体中文](README.zh-CN.md) · [Русский](README.ru-RU.md)

# Stride VR Localizer

**A toolchain for localising the on-device UI of Xenko/Stride VR applications.**

Built while localising [Virtual Desktop](https://www.vrdesktop.net/) for the
PICO 4 headset — Chinese, 187 UI strings plus a font atlas rebuilt from
scratch. Everything learned along the way is documented here so that anyone can
do the same for their own language, their own app, or both.

```
PICO 4 (Android 10, rooted)
  Virtual Desktop 1.34.22.0  —  Stride (Xenko) 3.2.0.2, .NET NativeAOT
  2120 serialised objects in two .bundle files  ·  8 UI page objects  ·  8 fonts
```

[Documentation](docs/index.md) · [Pitfalls](docs/07-pitfalls.md) ·
[Case study](docs/10-case-study.md) · [Troubleshooting](docs/11-troubleshooting.md) ·
[Adding a language](docs/08-new-language.md) · [Tests](tests/smoke_test.py)

---

## What it does

Two files inside the APK get rewritten:

| | original | after a Chinese build |
|---|---|---|
| UI strings in 8 `UIPage` objects | English | 187 entries translated |
| 3 font objects | 224 glyphs, atlas 256×212 | 621 glyphs, atlas 256×1235 |

The fonts are rebuilt by rasterising new glyphs from a TTF you supply and
packing them *below* the original atlas, so nothing that already renders can
move.

## The three findings that made it possible

**1. The APK contains two bundles, and the engine reads the small one.**

```
default.b65fc36561f156d9d380862e7304cc55.bundle   1 GB    payloads
default.bundle                                    224 KB  index
```

The 224 KB file holds a **second copy of the 2120-entry object record table**.
The loader resolves offsets through it. Patch only the big bundle and the app
dies 15 seconds after launch with a font-system `NullReferenceException` and
`SIGSEGV`. Every failed build in this project's history traces back to that
one fact. `bundle_writer.py` keeps both tables consistent and asserts they
agree before touching anything.

**2. UIPage string length is free. Precision is not.**

Replace the whole `varint(length) + text` pattern and you may change the
length as much as you like. Do a bare `bytes.replace("Environment", "环境")`
and you also rewrite `itemEnvironment`, `btnSettings` and
`UI/ControlPanel/EnvironmentSheet` — the control lookup fails and the process
crashes. (An earlier iteration of this project concluded "must be equal
length"; that was wrong, and the correction is documented with its evidence.)

`--mode strict-field` adds a second, equally anchored pass for labels that are
padded inside their own field; measured on the reference build it caught the
same 40 strings as plain strict, because every residual there turned out to be
a control-name substring.

**3. Font dictionary entries are variable length, and the count is not at a
fixed offset.**

Keys are `UTF-8(codepoint) + 0x00`, so a fixed 38-byte stride desynchronises
the moment you pass U+007F. The parser anchors on the space glyph instead.

Full write-ups: [bundle format](docs/02-bundle-format.md),
[font format](docs/03-font-format.md),
[string patching](docs/05-string-patching.md), and
[pitfalls](docs/07-pitfalls.md) — read the pitfalls page first if you are
about to modify any binary format by hand.

## Quick start

```bash
pip install -r requirements.txt

cp config.example.json config.json          # edit for your device/app version
mkdir -p work fonts
cp /path/to/base.apk work/base.apk          # your own copy of the app
cp /path/to/NotoSans*.ttf fonts/            # any licence you may use

python tools/extract.py                     # APK -> work/*.bundle
python tools/probe.py                       # survey: fonts and UI page records

python tools/dump_strings.py --out work/strings.tsv    # harvest English labels
# ...translate, save as locales/<your-lang>/strings.tsv...

python tools/build.py --tag my1
python tools/apksurgery.py --src work/base.apk \
    --new-bundle work/my1-data.bundle  --entry <data entry>  --dst work/my1.apk
python tools/apksurgery.py --src work/my1.apk \
    --new-bundle work/my1-index.bundle --entry <index entry> --dst work/my1-final.apk
tools/devtest.sh work/my1-final.apk my1
```

Two `apksurgery.py` passes are required — data bundle first, index bundle
second. Details: [APK packaging](docs/09-apk-packaging.md).

No device? `python tests/smoke_test.py` runs nine tests against a synthetic
bundle, including the dual-table rewrite and a test that demonstrates the
unsafe substitution mode destroying a control name.

## Repository layout

```
tools/
  stride_bundle.py     bundle container: header, record tables, LZ4 framing
  stride_font.py       OfflineRasterizedSpriteFont + atlas reader
  bundle_writer.py     rewrite both bundles, keep both tables consistent
  atlas_repack.py      rasterise + shelf-pack new glyphs below the atlas
  string_patcher.py    exact-varint substitution (and the unsafe modes, for study)
  build.py             the whole pipeline
  extract.py           APK -> bundles
  probe.py             survey all object records and their types
  dump_strings.py      harvest translatable strings into a TSV
  apksurgery.py        swap a bundle inside the APK, signature intact
  devtest.sh           install on the headset and check it survives
locales/
  zh-CN/               the reference translation (187 entries)
  _template/           starting point for a new language
docs/                  12 reverse-engineering and how-to pages, with the test matrix
tests/                 synthetic-data tests
```

## Adding a language

You need one TSV file and one font config. See
[docs/08-new-language.md](docs/08-new-language.md).

Cyrillic, Greek and Latin-extended work out of the box with Noto. Arabic and
Hebrew need pre-shaped bitmaps (the engine does not shape RTL). Thai and Indic
scripts need a real shaping step — individual codepoints will not render
correctly.

## Scope and limits

* Tested on Virtual Desktop 1.34.22.0 / PICO 4 / Android 10. The record
  numbers move between app versions — `tools/probe.py` finds them for you.
* The engine is assumed to be the same Stride/Xenko serialisation. Other
  engines are out of scope.
* UI text only. In-app rendered 3D text and the desktop-side server app are
  not covered.

## Legal

This repository contains **no proprietary material**: no APK, no bundle, no
game assets, no commercial font. It is tooling and documentation. You supply
your own copy of the application and fonts you are allowed to use, and you
modify your own device.

Modifying an application may violate its terms of service and, in some
jurisdictions, law. You are responsible for what you do with this.

WTFPL v2 — do what the fuck you want to. See [LICENSE](LICENSE).
