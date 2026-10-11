# The font format

`Xenko.Rendering.Fonts.OfflineRasterizedSpriteFont`, as shipped in the
bundle. All offsets below were asserted across the eight fonts in
VD 1.34.22.0.

## 1. Object data layout

`od` = decompressed_group[OffsetToObject:]

```
od[0]        1 byte  root SerializeClassFlags (== 0)
od[1:5]      float   BaseOffsetY
od[5:9]      float   DefaultLineSpacing
od[9]        1 byte  KerningMap presence flag
od[10:...]            KerningMap = Dictionary<int,float>:
                         int32 count, then count × (int32 key, float value)
...                  Size (float)
...                  CharacterToGlyph count (int32)      <- offset VARIES
...                  entries:
                         [ UTF-8(codepoint) + 0x00 ][ 36-byte Glyph ]
...                  StaticTextures = List<Texture>: int32 count, then per
                         texture: 9 bytes flags/props, 7-bit-len+utf8 typename,
                         7-bit-len+utf8 url
```

### Glyph (36 bytes)

| offset | size | field | notes |
|---|---|---|---|
| +0 | 4 | `Character` (i32) | equals the dictionary key |
| +4 | 4 | `Subrect.X` (i32) | pixel column in the atlas |
| +8 | 4 | `Subrect.Y` (i32) | pixel row |
| +12 | 4 | `Subrect.Width` (i32) | |
| +16 | 4 | `Subrect.Height` (i32) | |
| +20 | 4 | `Offset.X` (f32) | pen offset from the origin |
| +24 | 4 | `Offset.Y` (f32) | **negative is up**, relative to the baseline |
| +28 | 4 | `XAdvance` (f32) | |
| +32 | 4 | `BitmapIndex` (i32) | always 0 in practice |

## 2. Two traps

**The entry count is not at a fixed offset.** It shifts with the KerningMap
size. Locate the dictionary by anchoring on the first entry — always the space
glyph, whose bytes are `20 00 20 00 00 00` (key, NUL, then `Character` ==
0x20 as i32).

**Entries are variable length.** The key is `UTF-8(codepoint) + 0x00`: one
byte for ASCII, three for CJK. A fixed 38-byte stride works up to U+007F and
then silently desynchronises. Use the self-synchronising walk in
`stride_font.walk_entries()`, which re-validates every step against the
`Character` field.

## 3. The atlas lives in a *different* record

The font object only stores a **reference list** (URLs). The pixels are in the
paired `Texture` record: `font_rec + 1`.

```
[0]      bool isStreamable (== 0)
[1:5]    "TKTX"
[5:33]   ImageDescription, SEVEN int32:
             Dimension, Width, Height, Depth, ArraySize, MipLevels, Format
[33:37]  int32 totalSizeInBytes     (== W × H × 4 for R8G8B8A8_UNorm)
[37:..]  RGBA32 pixels, then a few trailing bytes
```

Seven int32, not eight — eight shifts every pixel by 4 bytes and produces an
atlas that looks plausible and renders garbage.

The trailing bytes differ between builds (4 on PICO, 8 on Quest). Copy
whatever was there.

## 4. What a rebuilt font looks like

| | original | after adding ~600 CJK glyphs |
|---|---|---|
| entries | 224 | 621 |
| atlas | 256 × 212 | 256 × 1235 |
| record 954 span | 130,209 B | 130,198 B (strings) |

The dictionary is kept **sorted by codepoint** (matching the reference Quest
build), the atlas grows only downwards, and the original Latin glyphs keep
their original Subrects.
