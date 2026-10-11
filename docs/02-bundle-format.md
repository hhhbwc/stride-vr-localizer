# The bundle format

Two files, one container format. Verified on Virtual Desktop 1.34.22.0
(engine: Stride/Xenko 3.2.0.2, .NET NativeAOT — no managed metadata, so every
offset below came from bytes on disk cross-checked against the Stride sources).

## 1. There are two files

| entry in the APK | size | role |
|---|---|---|
| `assets/data/db/bundles/default.<guid>.bundle` | ~1 GB | payloads |
| `assets/data/db/bundles/default.bundle` | 224 KB | **index** |

The `<guid>` in the data bundle's name also appears inside the index bundle's
header (bytes 25..41), which is how the two are paired.

**The index bundle holds a second copy of the object record table.** The
engine resolves object offsets through it. Patch only the data bundle and the
app crashes with a font-system NRE ~15 s after launch — this is pitfall #1 in
[07-pitfalls.md](07-pitfalls.md), and it is the reason this toolchain rewrites
both tables in one pass.

## 2. File header

```
[0:4]    "XKB1"          (u32 LE 0x31424B58)
[4:12]   i64  total file length
[12:16]  u32  crc        (always 0 — unused)
[16:20]  i32  dependencies count      (0 here)
[20:24]  i32  incremental bundle count (0 here)
[24:28]  i32  object record count     (2120 here)
[28:..]  count × 46-byte object records
...      payload area
```

The index bundle differs only in that its header is 45 bytes long (it carries
a dependency GUID), so its record table starts at 45.

## 3. Object record — 46 bytes

```
+0    u8   has_id                     (always 1)
+1    16B  ObjectId                   (MurmurHash3-128 of the object content)
+17   i64  StartOffset                (byte offset of the compressed payload)
+25   i64  EndOffset                  (exclusive)
+33   i64  SizeNotCompressed
+41   u8   IsCompressed
+42   i32  IncrementalBundleIndex     (1 in the index bundle, 0 in the data one)
```

Properties you can assert:

* `start < end` for every record
* `end[i] == start[i+1]` — records are contiguous, the file is exactly covered
* the two tables agree record-by-record (before *and* after any patch)

Reading `EndOffset` as `StartOffset` is the classic mistake and produces a
long list of fake mysteries.

## 4. Payload framing — Stride `LZ4Stream`

```
chunk := varint(flags) + varint(originalLength)
         + [varint(compressedLength) if flags & 1] + data
flags bit0 = compressed, bit1 = high compression, bits 2-4 = passes (must be 0)
chunk size 1 MiB; compression = plain LZ4 block (python-lz4 compatible)
```

Decode a record's span `[start, end)` by looping chunks until the span is
consumed.

## 5. Object payload — serialised chunk

```
"KNHC" (i32 LE) + version (i32 = 1)
+ TypeName  (7-bit length + UTF-8, assembly-qualified)
+ OffsetToObject (i32) + OffsetToReferences (i32)
+ [reference area] + [object data]
```

The object types that matter for localisation:

| type | what it holds |
|---|---|
| `OfflineRasterizedSpriteFont` | glyph dictionary ([03](03-font-format.md)) |
| `Texture` (record `font_rec + 1`) | the atlas bitmap |
| `UIPage` | UI strings ([05](05-string-patching.md)) |

## 6. Tool map

| file | role |
|---|---|
| `tools/stride_bundle.py` | header/table parsing, varints, LZ4 framing, `BundleIndex` |
| `tools/bundle_writer.py` | rewrite both bundles, keep both tables consistent |
| `tools/probe.py` | survey all records and their types |
