# Adding your own language

The short version: you touch one TSV file and one font config. Everything else
in the repository is language-agnostic.

## 1. Harvest the strings

```bash
python tools/dump_strings.py --out work/strings.tsv
```

This walks the UI page objects and extracts every candidate label. It is a
heuristic (length-prefix + printable-ASCII + boundary check), so expect
10–30 % noise: fragments of control names, URLs, shader-ish tokens.

**Delete from the table anything that is an identifier:**

* control names — `btnSettings`, `gridGames`, `tbResolution`, `itemEnvironment`
* asset URLs — `UI/ControlPanel/EnvironmentSheet`
* anything that looks like code

Leaving them in is not just useless, it is fatal
([05 — String patching](05-string-patching.md)).

## 2. Translate

```bash
mkdir -p locales/ru-RU
cp work/strings.tsv locales/ru-RU/strings.tsv
# edit the second column
```

| rule | why |
|---|---|
| any target length is fine | the engine handles length changes |
| keep punctuation | a glyph that is missing makes the font lookup return null, and that null is one of the ways you reach the `UnloadContent()` NRE |
| prefer short, natural labels | the original UI was laid out for English |

## 3. Choose fonts

```json
// locales/ru-RU/fonts.json
{ "regular": "fonts/NotoSans-Regular.ttf",
  "bold":    "fonts/NotoSans-Bold.ttf" }
```

and in `config.json`:

```json
"language": {
  "code": "ru-RU",
  "strings": "locales/ru-RU/strings.tsv",
  "fonts": { "regular": "fonts/NotoSans-Regular.ttf",
             "bold":    "fonts/NotoSans-Bold.ttf" },
  "extra_glyphs": "…punctuation your script needs…"
}
```

The glyph set is derived from your translation table, so anything you actually
use is guaranteed to be rasterised. `extra_glyphs` is the belt-and-braces
addition (all the Chinese punctuation lives there for `zh-CN`).

## 4. Build, package, test

```bash
python tools/build.py --tag ru1
python tools/apksurgery.py --src work/base.apk \
    --new-bundle work/ru1-data.bundle \
    --entry assets/data/db/bundles/default.<guid>.bundle --dst work/ru1.apk
python tools/apksurgery.py --src work/ru1.apk \
    --new-bundle work/ru1-index.bundle \
    --entry assets/data/db/bundles/default.bundle --dst work/ru1-final.apk
tools/devtest.sh work/ru1-final.apk ru1
```

Two passes: data bundle first, index bundle second — the second pass fixes the
offsets that the first pass moved ([09 — APK packaging](09-apk-packaging.md)).

## 5. Script-specific notes

**Cyrillic / Greek / Latin extended.** Nothing special. Noto covers it.

**Arabic / Hebrew.** The engine does not shape RTL. Use a Pillow built with
libraqm (`pip install raqm` requires the libraqm system library) so the
shaping happens at rasterisation time; all UI labels here are static, so that
is enough.

**Japanese / Korean.** Same pipeline as Chinese. Watch out for full-width
punctuation in `extra_glyphs`.

**Thai / Indic.** Complex shaping and above/below marks. Rasterising
individual codepoints will not produce correct text; these scripts need a
pre-shaping step before rasterisation.

## 6. Contributing the table back

A `locales/<lang>/strings.tsv` plus a two-line `fonts.json` is a complete,
self-contained contribution. Open a pull request — the licence is WTFPL, so
translations are free to reuse, fork, or fold into anything.
