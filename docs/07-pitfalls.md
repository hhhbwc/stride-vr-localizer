# Pitfalls

Everything in this document cost real debugging cycles on a real headset.
Each entry states the symptom, the cause, and the rule that prevents it. If
you only read one page of this repository, read this one.

---

## 1. The APK contains TWO bundles — and the engine uses the small one

**Symptom.** Every build that moved a single object offset crashed ~15 seconds
after launch, with a fixed signature:

```
E VRD  : System.NullReferenceException
          at Xenko.Graphics.Font.FontSystem.Unload()
          at Xenko.Rendering.Fonts.GameFontSystem.UnloadContent()
          ...
F libc : Fatal signal 11 (SIGSEGV) ... (Game Thread)
```

**Cause.** The APK ships two files:

| entry | size | role |
|---|---|---|
| `assets/data/db/bundles/default.<guid>.bundle` | ~1 GB | object payloads |
| `assets/data/db/bundles/default.bundle` | 224 KB | **index** |

The 224 KB file starts with a 45-byte header, then holds a **second, complete
copy of the 2120 × 46-byte object record table** (verified identical
record-by-record), then a URL → ObjectId map. The engine resolves object
offsets through *this* table. Patching only the data bundle leaves every
offset the loader reads pointing at the old layout.

**Rule.** Whenever a payload moves, rewrite **both** record tables in the same
run. `bundle_writer.build()` does this and asserts the two tables agree before
touching anything.

**Why it took so long to find.** The crash is a *font-system* NRE, so it looks
like a font problem. The real failure is upstream and the engine swallows the
original exception without logging it. The trail pointed at glyphs, atlas
formats, compression details, header alignment and padding — all of which were
innocent. A pure control experiment ("recompress the same bytes, change only
the offsets") is what isolated it.

---

## 2. The object record table is 46 bytes, and EndOffset is not StartOffset

An early parse of the table assumed the layout

```
+0  ObjectId (16B)   +16  ??? (9B)   +25  StartOffset   +33  SizeNotCompressed
```

The actual layout is:

```
+0    u8   has_id              (always 1)
+1    16B  ObjectId
+17   i64  StartOffset
+25   i64  EndOffset           <- this was being read as StartOffset
+33   i64  SizeNotCompressed
+41   u8   IsCompressed
+42   i32  IncrementalBundleIndex
```

With the correct layout all 2120 records satisfy `start < end` and
`end[i] == start[i+1]` — the records are contiguous and the file is covered
exactly. That property is the fastest sanity check you have; if it fails, your
table offsets are wrong.

**Rule.** Before believing any parse, assert contiguity (`tools/probe.py`
prints it).

---

## 3. UI strings: length is free, precision is not

This is the single most expensive mistake of the project, and the reason this
repository exists.

**Symptom.** Two builds (T16, T17, T20) crashed with

```
E VRD : System.NullReferenceException
   at VirtualDesktop.Mobile.ControlPanel.UnloadContent()
   at Xenko.Games.GameSystemBase.Xenko.Games.IContentable.UnloadContent()
   at VirtualDesktop.Mobile.Game.OnGraphicsDeviceDisposing(EventArgs e)
   at VirtualDesktop.Mobile.Game.ReleaseDevice()
F libc : Fatal signal 11 (SIGSEGV), fault addr 0x0 in tid (Game Thread)
```

**Wrong conclusion reached first.** "UIPage strings must be replaced at
*equal byte length*" — because the builds that changed lengths crashed and the
equal-length ones lived. That correlation was real but the causation was not.

**Actual cause.** A plain `bytes.replace("Environment", "环境")` also rewrites

* `itemEnvironment`, `itemEnvironmentDLC` — control names
* `UI/ControlPanel/EnvironmentSheet` — asset URL

The control lookup then fails, `ControlPanel.UnloadContent()` throws, and the
process dies. `Settings` → `btnSettings` / `gridSettings` is the same story.

**Rule.** Replace the whole `varint(length) + text` pattern, never the bare
text:

```python
pattern = wr_vint(len(en.encode())) + en.encode()
```

The length prefix is what distinguishes a label from a control name that
happens to contain it. **With that done, length changes are perfectly safe** —
verified by a build that moved record 954 from 130,209 to 130,198 bytes and
survived.

The `equal`/`padded` modes still exist in `string_patcher.py` and are still
dangerous; `tests/smoke_test.py::test_padded_mode_is_unsafe_by_design`
demonstrates exactly how they destroy a control name.

---

## 4. Dictionary entries are variable length

A font dictionary entry is

```
[ UTF-8(codepoint) + 0x00 ][ 36-byte Glyph ]
```

so 1 byte of key for ASCII, 3 bytes for CJK. A fixed 38-byte stride parses
correctly up to U+007F and silently desynchronises after that — which is
exactly what happened when decoding "CJK fonts" that turned out to be fine.

**Rule.** Walk with the self-synchronising `walk_entries()`: read up to the
NUL, then assert that the following 36-byte glyph starts with an `i32
Character` equal to the key. One bad step and the walk stops instead of
drifting.

---

## 5. The dictionary count is NOT at a fixed offset

`od[28]` happens to hold the entry count on VD 1.34.22.0 because the
KerningMap is empty. With a non-empty kerning table everything after `od[10]`
shifts.

**Rule.** Locate the dictionary by anchoring on the first entry — always the
space glyph, bytes `20 00 20 00 00 00`. Do not hard-code 28, and do not
hard-code 32 either (the anchor lands wherever it lands).

---

## 6. ImageDescription has SEVEN int32, not eight

The texture object is

```
[0] bool isStreamable (0)   [1:5] "TKTX"
[5:33]  Dimension, Width, Height, Depth, ArraySize, MipLevels, Format   <- 7 fields
[33:37] totalSizeInBytes
[37:..] RGBA32 pixels
```

Reading eight fields shifts every pixel by 4 bytes. The result still "looks
like" an atlas, so nothing complains — it just renders garbage.

---

## 7. SizeNotCompressed: use a relative delta, not the formula

```
SizeNotCompressed = len(decompressed_group) - len(reference_area)
```

holds for 1254 of 1255 records. The exception (record 954) is off by one
because the group holds several objects and `group_offsets()` only reads the
first `KNHC` header.

**Rule.** Compute `new_snc = old_snc + (len(new_group) - len(old_group))`.
Equivalent where the formula holds, and it preserves the correct value on the
odd record out.

---

## 8. Trailing bytes after the pixel array are not a constant

PICO builds append 4 zero bytes after the atlas pixels; Quest builds append 8.
Whatever the original object had, copy it verbatim. Synthesising it produces a
loader-side mismatch.

---

## 9. Offset.y is relative to the baseline and negative upwards

PIL gives you a positive top coordinate; the engine wants
`signed_y = pil_y - ascent`. Inverted, every new glyph lands two lines too
low.

---

## 10. Missing punctuation glyphs crash the UI

A glyph that is not in the dictionary makes the font lookup return null, and
that null propagates into the same `UnloadContent()` NRE chain as pitfall #3.
The first full build translated 349 Chinese *characters* and forgot the
punctuation — `，。、；：？！（）` — and paid for it.

**Rule.** Derive the glyph set from the *translation table* (all codepoints of
all target strings) plus a hard-coded punctuation set. Never from "the set of
CJK characters in my dictionary".

---

## 11. Test with the screen ON

With the display off the app never initialises its font system, so "the
process is alive after 60 s" proves nothing. Wake the device
(`input keyevent KEYCODE_WAKEUP`) before launching.

---

## 12. screencap cannot see VR content

`adb shell screencap` only captures the system layer — under a VR application
you get the PICO system dialog, never the app. Do not use screenshots to judge
rendering; use process liveness plus logcat, and let a human look through the
headset.

---

## 13. Never `adb install` the patched APK

Virtual Desktop validates its licence. A re-signed APK is rejected. The way
through is to keep the original signature and payload bytes and swap only the
target entry, then install by root-copying over the app's `base.apk`. The
copied signing block therefore carries **stale content digests** — fine for
root replacement (the platform does not re-hash on launch), fatal for the
normal installer.

---

## 14. Beware experiments that change two variables at once

The "offsets must not move" hypothesis survived for several rounds because the
control experiment (T8: recompress identical bytes, pad to the original span,
change no offsets) differed from the failing builds (T3/T6/v3/v4: new content
*and* moved offsets) in **two** ways at once. Only a design that varies one
factor separates the hypotheses — and when the truth came out (pitfall #1), the
whole hypothesis was void anyway.

Corollary: a crash signature that looks application-specific
(`ControlPanel.UnloadContent`) can still be caused by a *loader* problem three
layers down.

---

## 15. Test matrix actually executed

All on the same PICO 4, same base APK, same install procedure.

| build | content | index table patched | result |
|---|---|---|---|
| T3 | dictionary +1 glyph, offsets moved | no | crash |
| T6 | identical bytes, recompressed, offsets moved | no | crash |
| T8 | identical bytes, recompressed, span padded, no offset change | no | **alive** |
| T13 | identical bytes, recompressed, offsets moved | **yes** | **alive** |
| T14 | Verdana20 rebuilt (573 glyphs, atlas 212→822 px) | **yes** | **alive** |
| T15 | all three fonts rebuilt | **yes** | **alive** |
| T18/T19 | strings at equal byte length | yes | alive |
| T16/T17/T20 | strings, variable length, bare replace | yes | **crash** (pitfall #3) |
| T21/T22 | strings, variable length, **exact varint** | yes | **alive** |

The T6→T13 pair is the cleanest evidence for pitfall #1: same bytes, same
offsets, only difference = the index bundle was patched.

---

## 16. Small practical ones

* The same English string appears many times in a UI object. A patch driven by
  a list of label offsets misses the duplicates — do a full-group
  `bytes.replace`.
* `screencap`/`logcat` runs: use `adb -s <serial>`; multiple headsets on the
  same WiFi will confuse you.
* Root access on these devices needs retrying: alternate `su -c` and
  `su -mm -c` up to six times.
* A 1 GB APK means every experiment costs minutes. Keep the pristine APK, the
  extracted bundles and a last-known-good APK on the device before you start.
