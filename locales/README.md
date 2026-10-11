# Adding a language

This directory is the only place you need to touch to produce a new
localisation. Everything else in the repository is language-agnostic.

## Layout

    locales/
      _template/      empty starting point (copy this)
      zh-CN/          the reference translation (187 entries, verified on device)

Each language directory holds two files:

| file          | purpose                                              |
|---------------|------------------------------------------------------|
| `strings.tsv` | the translation table                                |
| `fonts.json`  | which font files to rasterise this script with       |

## 1. Create the table

    cp -r locales/_template locales/ru-RU
    python tools/dump_strings.py --out locales/ru-RU/strings.tsv

`dump_strings.py` fills the left column with every English UI string it can
find in the UI page objects. **Check the output by hand** - it is a heuristic
and produces some noise (fragments of control names, URLs, shader-ish
tokens). Delete anything that is:

* a control name (`btnSettings`, `gridGames`, `tbResolution`, `itemEnvironment`)
* an asset URL or path (`UI/ControlPanel/EnvironmentSheet`)
* an identifier that appears verbatim in code

Translating those is not merely useless - it is *fatal* (see
`docs/07-pitfalls.md`, pitfall #3: the control lookup fails and the process
crashes ~15 s after launch).

Rules for the right-hand column:

* Any length is fine. The engine handles length changes; only the *precision*
  of the match matters (`docs/05-string-patching.md`).
* Prefer a font that covers your script - Noto Sans has full coverage for
  Latin/Cyrillic/Greek/Arabic/Hebrew/Thai/Devanagari and Noto Sans CJK for
  Chinese/Japanese/Korean.
* Remember punctuation: it needs a glyph too.

## 2. Point the fonts at it

    # locales/ru-RU/fonts.json
    { "regular": "fonts/NotoSans-Regular.ttf",
      "bold":    "fonts/NotoSans-Bold.ttf" }

Weights map to `language.fonts` in `config.json`. Put the actual files in
`fonts/` (gitignored - you supply them yourself).

## 3. Build and test

    python tools/build.py --config config.json --tag ru1
    python tools/apksurgery.py --src work/base.apk \
        --new-bundle work/ru1-data.bundle --entry <data bundle entry> \
        --dst work/ru1.apk
    python tools/apksurgery.py --src work/ru1.apk \
        --new-bundle work/ru1-index.bundle --entry <index bundle entry> \
        --dst work/ru1-final.apk
    tools/devtest.sh work/ru1-final.apk ru1

The index bundle must be patched **after** the data bundle, in a second pass,
because swapping the data bundle moves the offsets that the index bundle's
central directory must then follow. (This is the two-pass detail that cost the
most debugging time in the original reverse engineering - see
`docs/07-pitfalls.md`, pitfall #1.)

## 4. Right-to-left scripts

Noto Naskh Arabic / Noto Sans Hebrew are laid out left-to-right as bitmaps;
the engine does not shape RTL text. If you need Arabic/Hebrew UI, expect to
supply pre-shaped bitmaps (raqm/Pillow >= 8 with libraqm does the shaping at
rasterisation time, which is enough for static labels).
