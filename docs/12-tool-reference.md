# Tool reference

Every tool takes `--config <file>` and `--set a.b=value` overrides, so nothing
needs to be edited to run against another app version.

    python tools/<tool>.py --config config.json --set device.serial=ABC123

---

## `extract.py` — APK → bundles

```
python tools/extract.py [--apk work/base.apk]
```

Copies the two bundle entries out of the APK (they are STORED, so this is a
byte-range copy — nothing is recompressed and the archive is not touched). It
also lists every `.bundle` entry it finds with sizes and compression method,
which is how you confirm `apk.data_entry` / `apk.index_entry` for a new version.

```
APK entries: 4096; bundles found: 2
  assets/data/db/bundles/default.b65fc36561f156d9d380862e7304cc55.bundle  (1058412330 bytes, STORED)
  assets/data/db/bundles/default.bundle  (224007 bytes, STORED)
extracted -> work/data.bundle (1058412330 bytes)
extracted -> work/index.bundle (224007 bytes)
data bundle: {'records': 2120, 'start_lt_end': True, 'discontinuities': 0, 'tail_ok': True}
```

## `probe.py` — what is in this bundle

```
python tools/probe.py [--scan-fonts] [--filter Texture]
```

Decompresses only the first few KB of every record, so it is fast on a 1 GB
bundle. Prints the object-type histogram, the notable records, and (with
`--scan-fonts`) the details of every `OfflineRasterizedSpriteFont`:

```
sanity: {'records': 2120, 'start_lt_end': True, 'discontinuities': 0, 'tail_ok': True}

object types (2120 records):
    826  <not a chunk>
    525  Sprite
    517  Texture
     68  Buffer
     42  Sound
     39  <undecodable>
     37  Material
     36  Model
     12  SpriteSheet
      8  OfflineRasterizedSpriteFont
      7  UIPage
      1  RuntimeRasterizedSpriteFont
      1  UILibrary
      1  GameSettings

font details (atlas is always font rec + 1):
  rec   857 Fonts/Verdana32/gen/Texture_1     size= 32.0 glyphs= 7491 cjk=4786 atlas=4096x1592 px_ok=True
  rec   861 Fonts/Verdana24Bold/gen/Texture_1 size= 24.0 glyphs=  224 cjk=   0 atlas=256x320  px_ok=True
  rec   865 Fonts/Verdana20/gen/Texture_1     size= 20.0 glyphs=  224 cjk=   0 atlas=256x212  px_ok=True
  rec   867 Fonts/Verdana24/gen/Texture_1     size= 24.0 glyphs= 7429 cjk=4786 atlas=2048x1860 px_ok=True
  rec  1967 Fonts/Verdana17/gen/Texture_1     size= 17.0 glyphs=  224 cjk=   0 atlas=256x160  px_ok=True
```

`cjk=0` on a font you intend to extend is the signal that this font has no CJK
glyphs yet. `<undecodable>` records are undecodable in the stock bundle too —
that is a property of the data, not of the tool.

Use `--filter` to narrow the type histogram (e.g. `--filter UIPage`).

## `dump_strings.py` — harvest translatable strings

```
python tools/dump_strings.py --out work/strings.tsv [--pages all] [--min-len 3]
```

Output is a TSV ready to translate, with an occurrence count in the third
column. The left column is a heuristic extraction: expect 10–30 % noise and
delete identifier-looking rows before translating
([08 §1](08-new-language.md)).

## `build.py` — the whole pipeline

```
python tools/build.py --tag my1 --pages all --mode strict
```

| flag | meaning |
|---|---|
| `--tag` | output tag → `work/<tag>-data.bundle`, `work/<tag>-index.bundle` |
| `--pages` | `all` \| `none` \| `954,869` — which UI page records to patch |
| `--mode` | `strict` (default) \| `strict-field` \| `field` \| `equal` \| `padded` |

Real output from a full build:

```
translations: 187 entries from locales/zh-CN/strings.tsv
codepoints required: 445
bundle sanity: {'records': 2120, 'start_lt_end': True, 'discontinuities': 0, 'tail_ok': True}

[1/3] UI strings
  rec   954: 40 strings / 50 occurrences (130209 -> 130101 B)
  total substitutions: 50

[2/3] Font atlases
  rec861 (bold): dict 224 -> 619, atlas 256x320 -> 256x1235, tail 4B
  rec865 (regular): dict 224 -> 619, atlas 256x212 -> 256x869, tail 4B
  rec1967 (regular): dict 224 -> 619, atlas 256x160 -> 256x626, tail 4B

[3/3] Rewrite bundles (data + index)
precondition OK: 2120 records, both tables identical
  note rec954: size_nc formula off by 1 -> using relative delta
  rec954: span 51702 -> 37033, snc 128351 -> 128243
  ...
written: data 1058763459 (+351129), index 224007
  verify: table_mismatch=0 discontinuities=0 decompressible=2074/2120 tail_ok=True
    rec954: decoded 130101 expected 130101 identical=True

Read-back
  rec861: glyphs=619 cjk=375 atlas=256x1235 pixels_ok=True out_of_bounds=0
  rec865: glyphs=619 cjk=375 atlas=256x869 pixels_ok=True out_of_bounds=0
  rec1967: glyphs=619 cjk=375 atlas=256x626 pixels_ok=True out_of_bounds=0
```

Read the read-back section every time. `identical=True` per changed record and
`out_of_bounds=0` are the two numbers that mean the build is sound.

## `apksurgery.py` — swap a bundle inside the APK

```
python tools/apksurgery.py --src work/base.apk \
    --new-bundle work/my1-data.bundle \
    --entry assets/data/db/bundles/default.<guid>.bundle \
    --dst work/my1.apk
```

Run it twice — data entry first, index entry second ([09](09-apk-packaging.md)).
It re-opens the result with Python's `zipfile` afterwards and verifies every
entry's CRC plus the target payload's byte-identity.

## `devtest.sh` — install and check

```
tools/devtest.sh work/my1-final.apk my1
```

Push → root copy → wake screen → launch → poll `pidof` for 60 s → dump logcat
signatures. Exit code 0 = survived. Keeps the APK at
`/data/local/tmp/<tag>.apk` as your rollback.

## `stride_font.py` — inspect one font directly

```
python tools/stride_font.py work/data.bundle 865
```

Prints the font's summary and its atlas, without building anything. Useful when
you want to check a single font after an experiment.
