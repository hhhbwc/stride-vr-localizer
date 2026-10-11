# Getting started

## What this does

It localises the on-device UI of a Xenko/Stride VR application (tested on
Virtual Desktop for PICO 4) by rewriting two files inside its APK:

1. the **UI strings** in the serialised page objects, and
2. the **font atlases**, so your script actually has glyphs to draw.

No proprietary file ships with this repository. You supply the APK and the
fonts.

## Prerequisites

* Python ≥ 3.9, `pip install -r requirements.txt`
* `adb`, a rooted headset, WiFi debugging enabled
* a TTF/OTF covering your script, that you are allowed to use
  ([Noto](https://fonts.google.com/noto) covers essentially every script, and
  is OFL-licensed)
* your own copy of the app's APK

## Layout

    config.json          <- you create this from config.example.json
    work/                <- scratch: your APK, extracted bundles, outputs (gitignored)
    fonts/               <- your TTFs (gitignored)
    locales/<lang>/      <- translation tables
    tools/               <- the toolchain
    tests/               <- self-contained tests (no device, no proprietary data)

## First run

```bash
cp config.example.json config.json
# edit config.json: device.serial, device.installed_apk,
#                   apk.data_entry / apk.index_entry, fonts, ui_pages

mkdir -p work fonts
cp /path/to/your/base.apk work/base.apk
cp /path/to/NotoSansSC-*.ttf fonts/

python tools/extract.py          # APK -> work/data.bundle + work/index.bundle
python tools/probe.py            # survey: which records are fonts / UI pages
```

`probe.py` is the step people skip and then regret: the record numbers for
fonts and UI pages move between app versions. It prints the object type of all
2120 records and lists the interesting ones, so you can update `config.json`
instead of guessing.

```bash
python tools/dump_strings.py --out work/strings.tsv   # harvest English strings
# translate the right-hand column, save as locales/<your-lang>/strings.tsv

python tools/build.py --tag my1                     # patch strings + fonts
python tools/apksurgery.py \
    --src work/base.apk \
    --new-bundle work/my1-data.bundle \
    --entry assets/data/db/bundles/default.<guid>.bundle \
    --dst work/my1.apk
python tools/apksurgery.py \
    --src work/my1.apk \
    --new-bundle work/my1-index.bundle \
    --entry assets/data/db/bundles/default.bundle \
    --dst work/my1-final.apk

tools/devtest.sh work/my1-final.apk my1
```

The APK must be patched **twice**: data bundle first, index bundle second.
See [09 — APK packaging](09-apk-packaging.md) for why.

## Verifying without a device

```bash
python tests/smoke_test.py
```

Nine tests covering varints, the LZ4 framing, the string substitution rules
(including a test that proves the unsafe mode destroys a control name), and a
full synthetic-bundle round trip that asserts both record tables stay
byte-identical. They need no APK and no headset.

## Reading the output of `build.py`

```
[1/3] UI strings
  rec   954: 41 strings / 57 occurrences (130209 -> 130198 B)
[2/3] Font atlases
  rec 861 (bold): dict 224 -> 621, atlas 256x212 -> 256x1235, tail 4B
[3/3] Rewrite bundles (data + index)
```

`130209 -> 130198` means the strings got *shorter* — that is fine, the loader
copes with length changes as long as the substitution was exact
([05 — String patching](05-string-patching.md)).

## If it crashes

1. `tools/devtest.sh` prints the logcat signatures. A
   `FontSystem.Unload` / `ControlPanel.UnloadContent` NRE + SIGSEGV within
   15 s means the font bundle failed to load.
2. The most likely cause by far is a table inconsistency between the two
   bundles ([07 — Pitfalls #1](07-pitfalls.md)).
3. Restore your last known-good APK from `/data/local/tmp/` and bisect: build
   with `--pages none` (fonts only) to separate the font path from the string
   path.
