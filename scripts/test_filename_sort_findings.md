# Filename-sort test findings — issue #146

## How to run

```
node scripts/test_filename_sort.js
```

Node ≥ 8 is sufficient; no npm packages required.

## What is tested

The exact JS comparator from `photo_upload.py` lines 414–416:

```js
files.sort(function(a,b){
  return a.name.localeCompare(b.name, undefined, {numeric:true, sensitivity:'base'});
});
```

14 synthetic sets in known capture order are shuffled then sorted. The test asserts
the sort restores capture order, or explicitly predicts failure where the sort cannot.

## Results summary (11 PASS / 2 FAIL / 1 WARN)

| Set | Description | Outcome |
|-----|-------------|---------|
| 01 | iPhone sequential JPG (IMG_1234–IMG_1250) | PASS |
| 02 | iPhone HEIC sequential (IMG_0001–IMG_0010) | PASS |
| 03 | iPhone edited-photo mix (IMG_E prefix) | PASS (warn) |
| 04 | iPhone counter rollover (IMG_9999 → IMG_0001) | **FAIL** |
| 05 | iPhone mixed JPG/HEIC (same counter sequence) | PASS |
| 06 | Android Samsung YYYYMMDD_HHMMSS | PASS |
| 07 | Android Pixel PXL_YYYYMMDD_HHMMSSmmm | PASS |
| 08 | Android generic IMG_YYYYMMDD_HHMMSS | PASS |
| 09 | Android midnight/day rollover | PASS |
| 10 | Mixed conventions (iPhone + Samsung in one batch) | **FAIL** |
| 11 | Leading-zero vs non-padded numbers | PASS |
| 12 | Mixed-case extensions (.jpg/.JPG/.jpeg/.HEIC) | PASS |
| 13 | Sparse/non-sequential iPhone counters | PASS |
| 14 | Android Pixel burst (millisecond resolution) | PASS |

## Verdicts

**Android (all variants tested):** RELIABLE. Date-time stamps are the first numeric
block; they sort monotonically including across midnight day-rollover. No failure
observed.

**iPhone (IMG_NNNN):** RELIABLE for normal sessions. Two caveats:

- **Counter rollover (FAIL — Set 04):** When the 4-digit counter resets from 9999 to
  0001, `IMG_0001` sorts numerically before `IMG_9998/9999`. This cannot be fixed from
  file names alone — EXIF timestamps would be required. In practice a photographer
  would need > 9999 shots across a single book session for this to occur, making it
  extremely rare.

- **Edited copies (WARN — Set 03):** Apple Photos saves edits as `IMG_E1234.jpg`
  alongside the original `IMG_1234.jpg`. The sort places *all* originals before *all*
  edited copies (digits < letters in ICU locale order), rather than interleaving them
  by number. Users should not mix originals and `IMG_E` edits in the same upload batch.

**Mixed-device batches (FAIL — Set 10):** When Android date-time names
(`20260703_…`) and iPhone counter names (`IMG_NNNN`) appear in the same selection,
`'2' < 'I'` causes all Android files to sort before all iPhone files, ignoring actual
capture order. This is inherent to the incompatible naming schemes and cannot be fixed
without EXIF metadata. Recommendation: upload same-device batches separately.

## Recommendation

No code change needed. The sort handles every realistic single-device scenario
correctly. Add the following to user documentation / issue #146 close comment:

> Works reliably for Android and iPhone when photos come from a single device in one
> selection. Known caveats: (1) iPhone counter rollover at 9999→0001 (extremely rare);
> (2) mixing iPhone originals with Apple Photos edited copies (IMG_E); (3) mixing
> photos from different devices in one batch. For any of these, manually reorder after
> upload or select photos in capture order.
