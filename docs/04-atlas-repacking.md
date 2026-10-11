# Atlas repacking

Adding glyphs for a new script to a font that was shipped with only Latin.

## 1. The constraint that shapes the whole design

The existing glyphs already have Subrects pointing into the existing atlas, and
those Subrects must keep working. So the atlas may only **grow downwards**:
the original rows are copied verbatim, new glyphs are packed strictly below
them starting at `start_y = original_height + 1`.

Nothing that already renders can break, because nothing that already exists
moves.

## 2. Pipeline

```
codepoints = all codepoints of every target string  +  hard-coded punctuation
missing    = codepoints - already-in-dictionary
render     = Pillow rasterises each missing codepoint, cropped to its ink box
pack       = shelf packing into the free area below the original canvas
emit       = new atlas bitmap + new dictionary entries
```

`tools/atlas_repack.py` implements this; `tools/build.py` wires it into the
bundle rewrite.

## 3. The details that matter

**Offset sign.** Pillow reports a positive top coordinate; the engine wants
`signed_y = pil_y - ascent`, i.e. negative upwards. Get it backwards and every
new glyph lands two lines too low.

**Packing.** Simple shelf packing with a 1-pixel border so glyphs never bleed
into each other. If a row overflows the atlas width, start a new shelf. The
atlas height grows to fit.

**BitmapIndex** stays 0. **Subrect** is in absolute atlas pixels.

**Keep the original trailing bytes** of the Texture object (4 on PICO, 8 on
Quest).

## 4. Choosing a font

Bring any TTF/OTF you are allowed to use:

| script | suggestion |
|---|---|
| Chinese / Japanese / Korean | Noto Sans SC / JP / KR |
| Cyrillic, Greek, Latin | Noto Sans / Noto Serif |
| Arabic, Hebrew | Noto Naskh Arabic / Noto Sans Hebrew (see RTL note below) |
| Thai, Devanagari, others | Noto Sans Thai, Noto Sans Devanagari, … |

Rasterise at the size the original font object declares (`font.size`), so the
metrics stay comparable.

## 5. Right-to-left scripts

The engine draws bitmaps left-to-right; it does not shape text. Arabic and
Hebrew labels therefore need **pre-shaped** bitmaps. Pillow ≥ 8 built against
libraqm does the shaping at rasterisation time, which is sufficient for static
UI labels (and all UI labels here are static).

## 6. Sanity checks after a build

`tools/build.py` prints, per font:

* glyph count (original + new)
* atlas size before/after
* `pixels_ok` — the `totalSizeInBytes` field matches W × H × 4
* `out_of_bounds` — must be 0

plus a residual scan of the UI strings. Anything non-zero means stop and look.
