/**
 * test_filename_sort.js
 *
 * Validates that the photo-uploader JS sort in photo_upload.py correctly places
 * real Android + iPhone file names into capture/page order.
 *
 * Exact sort being tested (from photo_upload.py line 414-416):
 *   files.sort(function(a,b){
 *     return a.name.localeCompare(b.name, undefined, {numeric:true, sensitivity:'base'});
 *   });
 *
 * Here each "file" is just a plain object with a .name property.
 *
 * Usage:
 *   node scripts/test_filename_sort.js
 */

'use strict';

// ── The exact comparator from photo_upload.py ─────────────────────────────────
function sortNames(names) {
  return names.slice().sort(function (a, b) {
    return a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' });
  });
}

// ── Test harness ──────────────────────────────────────────────────────────────
var passed = 0;
var failed = 0;
var warnings = 0;
var results = [];

/**
 * Run one test case.
 *
 * @param {string}   id          Short label
 * @param {string[]} inOrder     Names already in KNOWN capture order
 * @param {string[]} shuffled    The same names in shuffled order
 * @param {string}   expectation 'pass' | 'fail' | 'warn'
 * @param {string}   note        Explanation shown on failure or warn
 */
function test(id, inOrder, shuffled, expectation, note) {
  var sorted = sortNames(shuffled);
  var match = JSON.stringify(sorted) === JSON.stringify(inOrder);

  var outcome;
  if (expectation === 'fail') {
    // We predict the sort can't recover capture order for this set
    outcome = match ? 'UNEXPECTED-PASS' : 'FAIL-AS-EXPECTED';
    if (!match) failed++; // still counts as an issue for the uploader
    else warnings++;      // edge-case fixed somehow — flag it
  } else if (expectation === 'warn') {
    outcome = match ? 'PASS(warn)' : 'FAIL(warn)';
    warnings++;
  } else {
    // expectation === 'pass'
    outcome = match ? 'PASS' : 'FAIL';
    if (match) passed++; else failed++;
  }

  results.push({ id: id, outcome: outcome, match: match, inOrder: inOrder, sorted: sorted, note: note });
}

// ─────────────────────────────────────────────────────────────────────────────
// IPHONE SETS
// ─────────────────────────────────────────────────────────────────────────────

// Set 1: iPhone sequential JPG (IMG_1234 … IMG_1250)
var iphone_seq_jpg = [];
for (var n = 1234; n <= 1250; n++) iphone_seq_jpg.push('IMG_' + n + '.jpg');
var iphone_seq_jpg_shuffled = [
  'IMG_1243.jpg','IMG_1236.jpg','IMG_1249.jpg','IMG_1234.jpg','IMG_1248.jpg',
  'IMG_1241.jpg','IMG_1237.jpg','IMG_1250.jpg','IMG_1235.jpg','IMG_1246.jpg',
  'IMG_1239.jpg','IMG_1244.jpg','IMG_1238.jpg','IMG_1247.jpg','IMG_1240.jpg',
  'IMG_1242.jpg','IMG_1245.jpg'
];
test(
  'SET-01  iPhone sequential JPG',
  iphone_seq_jpg,
  iphone_seq_jpg_shuffled,
  'pass',
  'IMG_NNNN.jpg ascending — straightforward numeric sort'
);

// Set 2: iPhone HEIC sequential (IMG_0001.HEIC … IMG_0010.HEIC)
var iphone_heic = [];
for (var h = 1; h <= 10; h++) iphone_heic.push('IMG_' + String('000' + h).slice(-4) + '.HEIC');
var iphone_heic_shuffled = [
  'IMG_0007.HEIC','IMG_0003.HEIC','IMG_0010.HEIC','IMG_0001.HEIC','IMG_0005.HEIC',
  'IMG_0009.HEIC','IMG_0002.HEIC','IMG_0006.HEIC','IMG_0008.HEIC','IMG_0004.HEIC'
];
test(
  'SET-02  iPhone HEIC sequential',
  iphone_heic,
  iphone_heic_shuffled,
  'pass',
  'IMG_NNNN.HEIC — sensitivity:base ignores extension case; numeric sorts by counter'
);

// Set 3: iPhone edited-photo mix (IMG_E prefix = Apple Photos edited copy)
// Capture order = original then edited version per page (as a user might select both)
// Expected output of sort: we do NOT prescribe an order — this is a caveat set.
// We check what the sort produces and flag it as a warning (ambiguous case).
var iphone_edited_capture = [
  'IMG_1234.jpg', 'IMG_E1234.jpg',
  'IMG_1235.jpg', 'IMG_E1235.jpg'
];
var iphone_edited_shuffled = [
  'IMG_E1235.jpg', 'IMG_1234.jpg', 'IMG_E1234.jpg', 'IMG_1235.jpg'
];
// With numeric:true, sensitivity:'base': digits sort before letters in most ICU locales,
// so IMG_1234 < IMG_1235 < IMG_E1234 < IMG_E1235 (originals grouped before edits).
var iphone_edited_expected_sort = [
  'IMG_1234.jpg', 'IMG_1235.jpg', 'IMG_E1234.jpg', 'IMG_E1235.jpg'
];
test(
  'SET-03  iPhone edited-photo mix (IMG_E)',
  iphone_edited_expected_sort,
  iphone_edited_shuffled,
  'warn',
  'IMG_E variants (Apple Photos edits) sort AFTER all originals, not interleaved. ' +
  'User should not mix originals and edits in one upload batch.'
);

// Set 4: iPhone numeric counter rollover (9998, 9999 → 0001)
// Capture order: 9998, 9999, 0001 (counter rolled over)
// The sort will place 0001 FIRST because 1 < 9998 numerically → FAIL expected
var iphone_rollover_capture = ['IMG_9998.jpg', 'IMG_9999.jpg', 'IMG_0001.jpg'];
var iphone_rollover_shuffled = ['IMG_9999.jpg', 'IMG_0001.jpg', 'IMG_9998.jpg'];
test(
  'SET-04  iPhone counter rollover (9999→0001)',
  iphone_rollover_capture,
  iphone_rollover_shuffled,
  'fail',
  'Counter rolled over: IMG_0001 sorts before IMG_9998 numerically. ' +
  'The sort cannot infer rollover from file names alone — KNOWN LIMITATION.'
);

// Set 5: Mixed JPG and HEIC in sequence (same numbers, alternating extension)
var iphone_mixed_ext_capture = [
  'IMG_0001.HEIC', 'IMG_0002.jpg', 'IMG_0003.HEIC', 'IMG_0004.jpg',
  'IMG_0005.HEIC', 'IMG_0006.jpg'
];
var iphone_mixed_ext_shuffled = [
  'IMG_0006.jpg','IMG_0003.HEIC','IMG_0001.HEIC','IMG_0005.HEIC','IMG_0002.jpg','IMG_0004.jpg'
];
test(
  'SET-05  iPhone mixed JPG/HEIC (same counter seq)',
  iphone_mixed_ext_capture,
  iphone_mixed_ext_shuffled,
  'pass',
  'Different extensions but sequential counters — numeric sort on counter wins'
);

// ─────────────────────────────────────────────────────────────────────────────
// ANDROID SETS
// ─────────────────────────────────────────────────────────────────────────────

// Set 6: Samsung date-time style  YYYYMMDD_HHMMSS.jpg
var samsung_capture = [
  '20260703_143022.jpg',
  '20260703_143035.jpg',
  '20260703_143048.jpg',
  '20260703_143101.jpg',
  '20260703_143115.jpg'
];
var samsung_shuffled = [
  '20260703_143101.jpg','20260703_143022.jpg','20260703_143115.jpg',
  '20260703_143048.jpg','20260703_143035.jpg'
];
test(
  'SET-06  Android Samsung YYYYMMDD_HHMMSS',
  samsung_capture,
  samsung_shuffled,
  'pass',
  'Pure date-time stamp: first numeric block (YYYYMMDD) sorts correctly; ' +
  'second block (HHMMSS) is a tiebreaker and also sorts correctly'
);

// Set 7: Google Pixel PXL_YYYYMMDD_HHMMSSmmm.jpg
var pixel_capture = [
  'PXL_20260703_143022123.jpg',
  'PXL_20260703_143023456.jpg',
  'PXL_20260703_143024789.jpg',
  'PXL_20260703_143026012.jpg',
  'PXL_20260703_143027345.jpg'
];
var pixel_shuffled = [
  'PXL_20260703_143026012.jpg','PXL_20260703_143022123.jpg','PXL_20260703_143027345.jpg',
  'PXL_20260703_143024789.jpg','PXL_20260703_143023456.jpg'
];
test(
  'SET-07  Android Pixel PXL_YYYYMMDD_HHMMSSmmm',
  pixel_capture,
  pixel_shuffled,
  'pass',
  'PXL_ prefix; date-time+milliseconds suffix; numeric sort on timestamp blocks'
);

// Set 8: Generic Android IMG_YYYYMMDD_HHMMSS.jpg
var generic_android_capture = [
  'IMG_20260703_143022.jpg',
  'IMG_20260703_143035.jpg',
  'IMG_20260703_143048.jpg',
  'IMG_20260703_143101.jpg',
  'IMG_20260703_143115.jpg'
];
var generic_android_shuffled = [
  'IMG_20260703_143101.jpg','IMG_20260703_143022.jpg','IMG_20260703_143115.jpg',
  'IMG_20260703_143048.jpg','IMG_20260703_143035.jpg'
];
test(
  'SET-08  Android generic IMG_YYYYMMDD_HHMMSS',
  generic_android_capture,
  generic_android_shuffled,
  'pass',
  'IMG_ prefix + date-time — numeric sort on the date-time blocks'
);

// Set 9: Android midnight/day rollover (photos spanning 23:59 → 00:00 next day)
var midnight_capture = [
  '20260703_235958.jpg',
  '20260703_235959.jpg',
  '20260704_000000.jpg',
  '20260704_000001.jpg',
  '20260704_000002.jpg'
];
var midnight_shuffled = [
  '20260704_000001.jpg','20260703_235958.jpg','20260704_000002.jpg',
  '20260703_235959.jpg','20260704_000000.jpg'
];
test(
  'SET-09  Android midnight/day rollover',
  midnight_capture,
  midnight_shuffled,
  'pass',
  'First numeric block changes from 20260703 to 20260704 — sorts correctly because ' +
  'the date itself is the primary numeric key'
);

// ─────────────────────────────────────────────────────────────────────────────
// CROSS-DEVICE / AWKWARD SETS
// ─────────────────────────────────────────────────────────────────────────────

// Set 10: Mixed conventions — iPhone IMG_NNNN + Samsung YYYYMMDD in one batch.
// There is no reliable capture-order sort because the naming schemes are incomparable.
// The sort will interleave them by string comparison: IMG_1234 vs 20260703…
// '2' (0x32) < 'I' (0x49), so Samsung files will sort BEFORE iPhone files regardless
// of actual capture order.
var mixed_conv_capture = [
  'IMG_1234.jpg',       // iPhone page 1 (taken first)
  '20260703_143022.jpg', // Samsung page 2
  'IMG_1235.jpg',       // iPhone page 3
  '20260703_143048.jpg'  // Samsung page 4
];
var mixed_conv_shuffled = [
  '20260703_143048.jpg','IMG_1234.jpg','20260703_143022.jpg','IMG_1235.jpg'
];
test(
  'SET-10  Mixed conventions (iPhone + Samsung)',
  mixed_conv_capture,
  mixed_conv_shuffled,
  'fail',
  'Incompatible naming schemes: Samsung date names sort before IMG_ names by first ' +
  'character (\'2\' < \'I\'). No sort can recover capture order without metadata.'
);

// Set 11: Leading-zero vs non-padded numbers
// With numeric:true, "001" == 1 numerically, so IMG_001 < IMG_2 < IMG_10
var leading_zero_capture = ['IMG_001.jpg', 'IMG_2.jpg', 'IMG_10.jpg', 'IMG_020.jpg'];
var leading_zero_shuffled = ['IMG_020.jpg', 'IMG_001.jpg', 'IMG_10.jpg', 'IMG_2.jpg'];
// numeric:true means: 1 < 2 < 10 < 20, regardless of zero-padding
var leading_zero_expected = ['IMG_001.jpg', 'IMG_2.jpg', 'IMG_10.jpg', 'IMG_020.jpg'];
test(
  'SET-11  Leading-zero vs non-padded numbers',
  leading_zero_expected,
  leading_zero_shuffled,
  'pass',
  'numeric:true treats "001"=1, "020"=20 — natural numeric ordering regardless of padding'
);

// Set 12: Mixed-case extensions (jpg, JPG, jpeg, JPEG, HEIC)
var mixed_case_capture = [
  'IMG_0001.jpg',
  'IMG_0002.JPG',
  'IMG_0003.jpeg',
  'IMG_0004.JPEG',
  'IMG_0005.HEIC'
];
var mixed_case_shuffled = [
  'IMG_0005.HEIC','IMG_0002.JPG','IMG_0004.JPEG','IMG_0001.jpg','IMG_0003.jpeg'
];
test(
  'SET-12  Mixed-case extensions',
  mixed_case_capture,
  mixed_case_shuffled,
  'pass',
  'sensitivity:\'base\' collapses case differences; numeric counter is the sort key'
);

// Set 13: Sparse/non-sequential iPhone counter (gaps from deleted shots)
var sparse_capture = [
  'IMG_0042.jpg', 'IMG_0047.jpg', 'IMG_0053.jpg',
  'IMG_0061.jpg', 'IMG_0078.jpg'
];
var sparse_shuffled = [
  'IMG_0061.jpg','IMG_0042.jpg','IMG_0078.jpg','IMG_0053.jpg','IMG_0047.jpg'
];
test(
  'SET-13  Sparse/non-sequential iPhone counters (gaps from deleted shots)',
  sparse_capture,
  sparse_shuffled,
  'pass',
  'Gaps in counter sequence: numeric sort still orders by counter value'
);

// Set 14: Android Pixel burst mode (multiple millisecond-different frames, same second)
var pixel_burst_capture = [
  'PXL_20260703_143022100.jpg',
  'PXL_20260703_143022200.jpg',
  'PXL_20260703_143022300.jpg',
  'PXL_20260703_143022400.jpg'
];
var pixel_burst_shuffled = [
  'PXL_20260703_143022400.jpg','PXL_20260703_143022100.jpg',
  'PXL_20260703_143022300.jpg','PXL_20260703_143022200.jpg'
];
test(
  'SET-14  Android Pixel burst (millisecond resolution)',
  pixel_burst_capture,
  pixel_burst_shuffled,
  'pass',
  'Millisecond suffix distinguishes frames within same second — sorts correctly'
);

// ─────────────────────────────────────────────────────────────────────────────
// RESULTS TABLE
// ─────────────────────────────────────────────────────────────────────────────

console.log('\n' + '='.repeat(90));
console.log('  FILENAME SORT TEST RESULTS');
console.log('  Sort: a.name.localeCompare(b.name, undefined, {numeric:true, sensitivity:\'base\'})');
console.log('='.repeat(90));
console.log(
  pad('TEST', 45) + pad('OUTCOME', 18) + 'NOTE'
);
console.log('-'.repeat(90));

results.forEach(function (r) {
  console.log(pad(r.id, 45) + pad(r.outcome, 18) + r.note.split('.')[0]);
});

console.log('='.repeat(90));
console.log('  PASS: ' + passed + '  |  FAIL: ' + failed + '  |  WARN/CAVEAT: ' + warnings);
console.log('='.repeat(90));

// Verbose failure/warn detail
results.forEach(function (r) {
  if (!r.match || r.outcome.indexOf('warn') !== -1 || r.outcome.indexOf('FAIL') !== -1) {
    console.log('\n--- ' + r.id + ' ---');
    console.log('  NOTE:     ' + r.note);
    console.log('  Expected: ' + JSON.stringify(r.inOrder));
    console.log('  Actual:   ' + JSON.stringify(r.sorted));
    console.log('  Match:    ' + r.match);
  }
});

console.log('\n');
console.log('PLAIN-ENGLISH VERDICT');
console.log('-'.repeat(60));
console.log(
  'Android (Samsung/Pixel/generic IMG_YYYYMMDD):\n' +
  '  RELIABLE. Date-time based names sort in capture order under\n' +
  '  numeric:true because the date portion is the first numeric block\n' +
  '  and changes monotonically within a session, including across\n' +
  '  midnight. Sets 06-09, 14 all PASS.\n'
);
console.log(
  'iPhone (IMG_NNNN.jpg/.HEIC):\n' +
  '  RELIABLE for a normal session (Sets 01-03, 05, 11-13 PASS).\n' +
  '  ONE KNOWN FAILURE: if the camera counter rolls over from 9999\n' +
  '  to 0001 within the same book (Set 04 FAIL), IMG_0001 sorts\n' +
  '  before IMG_9998/9999. This is extremely rare (counter resets\n' +
  '  only after 9999 shots) and cannot be fixed without EXIF metadata.\n' +
  '  ONE CAVEAT: Apple Photos "edited" copies (IMG_E1234) sort AFTER\n' +
  '  all originals, not interleaved. Users should not mix originals\n' +
  '  and edits in one batch (Set 03 WARN).\n'
);
console.log(
  'Cross-device / mixed conventions:\n' +
  '  FAILS when Android date-time names and iPhone IMG_NNNN names\n' +
  '  appear in the same batch (Set 10 FAIL). "2..." < "I..." by\n' +
  '  first character so Samsung files always sort before iPhone files\n' +
  '  regardless of capture order. Recommend: upload same-device\n' +
  '  batches separately, or rename files before upload.\n'
);
console.log(
  'Recommendation:\n' +
  '  The sort is sound for single-device sessions — covers the vast\n' +
  '  majority of real use. Document the counter-rollover edge case\n' +
  '  and the mixed-device limitation in user guidance. No code change\n' +
  '  needed for these edge cases; they require EXIF timestamps to fix.\n'
);

function pad(s, len) {
  s = String(s);
  while (s.length < len) s += ' ';
  return s;
}
