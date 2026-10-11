# Case study: localising Virtual Desktop for the PICO 4

A full account of how a 113-entry Chinese translation table went from
"written into the file but 80 % of the glyphs render blank" to a working
localised build — including every wrong turn, because the wrong turns are the
transferable part.

Read this if you want to understand *why* the tools are shaped the way they
are. Read [07-pitfalls.md](07-pitfalls.md) if you just want the rules.

---

## Stage 0 — The starting point

A translation table of 113 English → Chinese entries had already been written
into the app's data bundle, using a byte-level in-place patch. On screen:

* every Latin label was translated correctly,
* roughly **80 % of Chinese characters rendered as blank boxes**.

The failure mode was consistent and silent: no crash, no log line, nothing.

## Stage 1 — Why 80 % of the glyphs were blank

The app uses `OfflineRasterizedSpriteFont`: glyphs are **pre-baked bitmaps in
an atlas**, not vectors. The Latin fonts shipped with the app contain only the
glyphs the original UI needed — Latin letters, digits, punctuation. There is no
CJK in there at all.

A glyph that is not in the dictionary does not produce a "missing glyph" box.
The font lookup returns `null`, the text run produces nothing, and the label is
simply blank. That is why the failure was silent.

**The fix is not a config flag.** The atlas has to gain ~400 CJK bitmaps, the
dictionary has to gain the matching entries, and both have to stay consistent
with each other. Hence `tools/atlas_repack.py`.

*Corollary that shaped the whole design:* the atlas may only grow **downwards**,
below the original canvas. Existing glyphs keep their Subrects, so nothing that
already renders can move. See [04-atlas-repacking.md](04-atlas-repacking.md).

## Stage 2 — Reverse-engineering the font format

Three findings, each of which invalidated a plausible assumption:

**The dictionary is variable-length.** An entry is
`UTF-8(codepoint) + 0x00 + 36-byte glyph`, so a fixed 38-byte stride parses
fine up to U+007F and desynchronises silently afterwards. A decoder built on
the fixed stride reported that the "CJK fonts" were corrupt — they were not;
the decoder was.

**The entry count is not at a fixed offset.** `od[28]` holds it only because
this build's KerningMap is empty. The parser anchors on the first entry (the
space glyph, `20 00 20 00 00 00`) instead.

**The atlas lives in a different record.** The font object holds a reference
list (URLs); the pixels are in `font_rec + 1`, a `Texture` object whose image
description is **seven** int32, not eight. Eight shifts every pixel by four
bytes and produces an atlas that looks plausible and renders garbage.

A cross-check made all of this much faster: a Quest 2 headset with an
officially localised copy of the same app version. Its fonts were rebuilt the
same way (dictionary sorted by codepoint, larger atlases, new glyphs), which
proved the *approach* was sound and the problem was purely in the engineering.

## Stage 3 — The first crash wave, and a hypothesis matrix

The first working atlas-repacking build crashed ~15 seconds after launch:

```
E VRD  : System.NullReferenceException at Xenko.Graphics.Font.FontSystem.Unload()
F libc : Fatal signal 11 (SIGSEGV) ... (Game Thread)
```

That signature looks like a font problem. It is not: it is what the app does
*after* any content load fails, and the original exception is swallowed without
being logged. Everything downstream of a failed load looks like this.

So the failures were bisected with single-variable experiments:

| build | what changed | result |
|---|---|---|
| T3 | dictionary +1 glyph, offsets moved | crash |
| T6 | identical bytes, recompressed, offsets moved | crash |
| T8 | identical bytes, recompressed, **span padded, no offset change** | alive |
| A | dictionary +349 glyphs, atlas untouched | crash |
| B | dictionary rebuilt, atlas grown (wrong `size_nc`) | crash |
| v3 | both, `size_nc` correct, span exact | crash |
| v4 | v3 + 1 zero byte per span | crash |

Two hypotheses survived T8:

* **H1** — something else in the bundle references the old absolute offsets.
* **H2** — the loader needs a stream terminator (the zero padding).

Both were consistent with the data, and T8 satisfied both. The experiments
that followed were designed to *separate* them, and the answer turned out to be
neither (Stage 4).

**Lesson worth keeping:** T8 and T6 differed in *two* variables at once
(content and offsets), which is why the hypothesis space stayed wide for so
long. When you design a control experiment, vary one thing.

## Stage 4 — The root cause

The APK contains **two** bundles:

```
default.b65fc36561f156d9d380862e7304cc55.bundle   1 GB    payloads
default.bundle                                    224 KB  index
```

The small one holds a **complete second copy of the 2120-entry object record
table**. The engine resolves object offsets through *it*. Every build that
moved a payload had patched only the big bundle's table, so the loader kept
using stale offsets — for every build, including the ones whose content was
byte-identical.

The decisive experiment was T13: identical bytes to T6, identical offsets, the
only difference being that the index bundle's table was patched too. It lived.

```
T6  : identical bytes, offsets moved, index NOT patched  -> crash
T13 : identical bytes, offsets moved, index patched      -> alive
```

That pair is the cleanest evidence in the whole project, and it is why
`bundle_writer.build()` refuses to run unless both tables agree beforehand.

## Stage 5 — Fonts, for real

With both tables patched, the font rebuilds worked on the first try:

| build | content | result |
|---|---|---|
| T14 | Verdana20 rebuilt, 573 glyphs, atlas 212 → 822 px | alive |
| T15 | all three fonts rebuilt | alive |

The glyph set came from the translation table itself — every codepoint of every
target string, **including punctuation** (Stage 1's blank boxes had taught that
lesson; the first build that translated 349 *characters* and forgot `，。、；：？！（）`
crashed for exactly the "missing glyph" reason).

## Stage 6 — The remaining 10 %, and a rule that had to be un-learned

With fonts working, ~10 % of the UI was still English. Filling it in produced
three crashes in a row (T16, T17, T20), all with a *different* signature:

```
E VRD : System.NullReferenceException
   at VirtualDesktop.Mobile.ControlPanel.UnloadContent()
   ...
F libc : Fatal signal 11 (SIGSEGV), fault addr 0x0 in tid (Game Thread)
```

The builds that changed string length crashed; the ones that padded to equal
byte length survived. The conclusion drawn was: **UIPage strings must be
replaced at equal length.** It was written into the handover document as a rule.

It was wrong.

The actual cause: the "safe" builds used a bare `bytes.replace("Environment",
"环境")`, which also rewrites `itemEnvironment`, `itemEnvironmentDLC` and
`UI/ControlPanel/EnvironmentSheet`. The control lookup fails, `UnloadContent()`
throws, and the process dies. The length had nothing to do with it.

The fix is to replace the whole `varint(length) + text` pattern — and once that
is done, **length changes are free**. T22 changed record 954 from 130,209 to
130,198 bytes and ran normally, with the category labels (COMPUTERS / GAMES /
INPUT / SETTINGS / STREAMING / VIDEOS / ENVIRONMENT) all translated.

**This is the most useful thing in this repository.** A rule that is 80 % right
is more dangerous than no rule, because it gets written down and followed. The
evidence for the correction is in [05-string-patching.md](05-string-patching.md).

## Stage 7 — Where it ended up

* 8 UI page objects, 94 substitutions, 187-entry translation table
* 3 fonts grown from 224 to 621 glyphs each (375 CJK + punctuation)
* atlases grown from 256×212/320/160 to 256×869/1235/626
* residual English: control-name substrings only, zero untranslated labels
* process alive, no `E VRD` lines, verified through the headset

## What transferred to other projects

1. **Silent failure means look at the data, not the logs.** Blank glyphs and a
   swallowed exception are the same class of problem: the engine chose not to
   tell you.
2. **A crash signature localises the symptom, not the cause.**
   `FontSystem.Unload` NRE meant "content failed to load", not "font broken".
3. **Bisect with one variable at a time.** Two-variable control experiments
   keep the hypothesis space artificially wide.
4. **Check for a second copy of any index you are editing.** Formats that ship
   an index bundle, a manifest, or a sidecar table will bite you exactly once.
5. **Never rewrite a serialised field without its length prefix.** The prefix
   is what makes the match unambiguous.
6. **A rule that survives one experiment is a hypothesis, not a rule.** The
   "equal length" rule survived four builds before it was disproved.
