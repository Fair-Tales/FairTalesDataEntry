"""Offline DEV20 alias-value comparison; all generated evidence stays in .cache/.

Run with ../fair-tales-language-analysis/.venv-mig/bin/python
scripts/alias_value_experiment.py. No service clients or sealed paths are read.
"""
from __future__ import annotations

import hashlib
import json
import csv
import pickle
import random
import sqlite3
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.extraction_eval.evidence import evidence_dir
from scripts.extraction_eval.exposure import DEV20
from scripts.extraction_eval.json_sources import JsonSources
from scripts.extraction_eval.store import atomic_json, digest
from scripts.extraction_eval.tuning import TuningLoader, load_selections
from scripts.extraction_eval.reference_inputs import load_reference
from scripts.extraction_eval.scoring import score_character_recall, _attribute_code
from alias_pipeline import build_alias_index, match_page, occurrence_ledger

CACHE = ROOT / '.cache/alias_value_experiment'
ANALYSIS = ROOT.parent / 'fair-tales-language-analysis'
SEED = 20261116
# Agent review of the fixed sample, 16 November 2026; not human validation.
# A digest binds these judgements to the reviewed ids and their order.
REVIEWED_SAMPLE = '924f153bb733822f6d19b54c13f815b98443a86c8ea221d3bb50741e424d2e5a'
WRONG_MENTIONS = {
    5: 'object-type modifier, not a character mention',
    22: 'place, not its inhabitants',
    23: 'generic species, not the named individual',
    25: 'place, not its inhabitants',
    32: 'comparison with a different identity',
    42: 'costume modifier, not the wearer',
    44: 'place, not its inhabitants',
    46: 'material modifier, not a character mention',
}
UNCLEAR_MENTIONS = {11, 26}  # Body-part/voice descriptions: part versus character.
PERSONAL_PRONOUNS = frozenset('''i me my mine myself we us our ours ourselves
you your yours yourself yourselves he him his himself she her hers herself
it its itself they them their theirs themselves'''.split())


def cohort_loader():
    """Use the repository's ids-only exclusions, never open the sealed directory.

    This local guard is not a seal verification or a new cohort selection.
    The experiment additionally restricts every content read to DEV20.
    """
    selections = load_selections()
    assert DEV20 <= set(selections['iterate55_ids'])
    guard = CACHE / 'ids_guard'
    guard.mkdir(parents=True, exist_ok=True)
    holdout = selections['imported80_ids']
    data = (json.dumps(holdout, indent=1) + '\n').encode()
    (guard / 'holdout_ids.json').write_bytes(data)
    atomic_json(guard / 'freeze.json', {
        'imported80_ids': holdout,
        'imported80_ids_sha256': digest(sorted(holdout)),
        'holdout_ids_file_sha256': hashlib.sha256(data).hexdigest(),
        'protected40_ids': selections['protected40_ids'],
        'tune41_ids': selections['tune41_ids'],
        'tune81_ids': sorted(selections['protected40_ids'] + selections['tune41_ids']),
    })
    return TuningLoader(CACHE / 'admission', freeze_dir=guard, cohort='tune')


def inputs(loader):
    snap = evidence_dir() / 'dev20-snap'
    manifest = loader.snapshot(snap, sorted(DEV20))
    with JsonSources(evidence_dir() / 'dev20-run/predictions.json') as source:
        books = source.members(source.members(source.root)['books'])
        assert set(books) == DEV20
        predictions = {
            book: loader.load([book], 'prediction', lambda: source.decode(books[book]))
            for book in sorted(DEV20)
        }
    return manifest['books'], predictions


def book_id(title):
    return title.lower().replace(' ', '_')


def speech_rows(loader):
    """Decode content only after the book identity has passed both gates."""
    with JsonSources(ANALYSIS / 'data/gpt4_speakers_recipients_processed.json') as source:
        fields = {key: source.members(span) for key, span in
                  source.members(source.root).items()}
        rows = []
        for row, span in fields['book'].items():
            identity = book_id(source.decode(span))
            if identity in DEV20:
                rows.append(loader.load([identity], 'speech', lambda: {
                    'row': int(row), 'book_id': identity,
                    **{key: source.decode(values[row]) for key, values in fields.items()},
                }))
    return sorted(rows, key=lambda row: row['row'])


def cast(records, ids, *, names_only=False):
    # Only the requested names/aliases; no later schema metadata or overrides.
    result = []
    for row, identity in zip(records, ids):
        aliases = [] if names_only else row.get('aliases', [])
        aliases = [a if isinstance(a, str) else a['name'] for a in aliases]
        # A lexical hit on a saved "he" alias is not pronoun resolution.
        # Exclude standalone personal pronouns symmetrically; keep possessors
        # inside nominal phrases, as required by DECISIONS 014.
        result.append({'entity_id': identity,
                       'name': '' if row['name'].casefold() in PERSONAL_PRONOUNS else row['name'],
                       'aliases': [a for a in aliases if a.casefold() not in PERSONAL_PRONOUNS]})
    return result


def candidates(index, text):
    return sorted({entity for hit in match_page(index, text or '')
                   for entity in hit['candidate_entity_ids']})


def crosscheck(books, loader):
    """Cross-check every DEV20 reference row, using read-only, book-filtered SQL."""
    counts, differences = Counter(), []
    connection = sqlite3.connect(
        f'file:{ANALYSIS / "character_database.db"}?mode=ro', uri=True)
    try:
        for book in books:
            def read():
                chars = connection.execute(
                    'SELECT name, gender FROM characters WHERE book = ?',
                    (book['title'],)).fetchall()
                aliases = connection.execute(
                    'SELECT character, alias FROM aliases WHERE book = ?',
                    (book['title'],)).fetchall()
                return chars, aliases
            chars, aliases = loader.load([book['id']], 'sqlite_crosscheck', read)
            for ref in book['reference']['characters']['value']:
                same = [(name, gender) for name, gender in chars if name == ref['name']]
                actual = sorted(alias.strip() for name, alias in aliases if name == ref['name'])
                expected = sorted(a if isinstance(a, str) else a['name']
                                  for a in ref.get('aliases', []))
                gender = _attribute_code(ref, 'gender', recorded=True)
                okay = (len(same) == 1 and
                        {'F': 'Female', 'M': 'Male', 'NGS': 'Non-specific'}.get(same[0][1]) == gender
                        and actual == expected)
                counts['checked'] += 1
                counts['agree' if okay else 'disagree'] += 1
                if not okay:
                    differences.append({'book_id': book['id'], 'character': ref['name'],
                                        'database_alias_count': len(actual),
                                        'manifest_alias_count': len(expected)})
    finally:
        connection.close()
    return {**counts, 'differences': differences}


def main():
    import spacy

    random.seed(SEED)
    spacy.util.fix_random_seed(SEED)
    nlp = spacy.load('en_core_web_lg', disable=['ner'])
    loader = cohort_loader()
    summaries = {c: Counter() for c in ('A', 'B', 'B_names')}
    per_character, mention_pool, speech_pool, speech_results = [], [], [], []
    by_gender = {c: {g: Counter() for g in ('Female', 'Male', 'Non-specific')}
                 for c in summaries}
    pairing, inventories, indexes, labels, texts = [], {}, {}, {}, {}
    verb_rows = Counter()
    alias_counts = Counter()
    with loader.activate():
        books, predictions = inputs(loader)
        db_check = crosscheck(books, loader)
        rows = speech_rows(loader)
        for book in books:
            bid = book['id']
            refs = book['reference']['characters']['value']
            preds = predictions[bid]['characters']['value']
            alias_counts['A'] += sum(len(r.get('aliases', [])) for r in refs)
            alias_counts['B'] += sum(len(r.get('aliases', [])) for r in preds)
            matched = score_character_recall(refs, preds)
            pairs = matched['matches']
            pairing.append({'book_id': bid, 'reference': len(refs), 'prediction': len(preds),
                            'pairs': len(pairs), 'ambiguous_pairs': matched['ambiguous_matches']})
            rids = [f'{bid}:r{i}' for i in range(len(refs))]
            pids = [f'{bid}:p{i}' for i in range(len(preds))]
            paired = set()
            for pair in pairs:
                identity = rids[pair['reference']]
                pids[pair['prediction']] = identity
                paired.add(identity)
            dictionaries = {'A': cast(refs, rids), 'B': cast(preds, pids),
                            'B_names': cast(preds, pids, names_only=True)}
            indexes[bid] = {c: build_alias_index(v, book_id=bid, diagnostic=True)
                            for c, v in dictionaries.items()}
            inventories[bid] = {'paired': sorted(paired), 'reference_ids': rids}
            labels.update({i: r['name'] for r, i in zip(refs, rids)})
            labels.update({i: p['name'] for p, i in zip(preds, pids) if i not in labels})
            char_rows = {}
            for pair in pairs:
                r, p = pair['reference'], pair['prediction']
                record = {'book_id': bid, 'character_id': rids[r], 'name_A': refs[r]['name'],
                          'name_B': preds[p]['name'],
                          'gender': _attribute_code(refs[r], 'gender', recorded=True)}
                for c in summaries:
                    record.update({f'{c}_mentions': 0, f'{c}_unique': 0, f'{c}_verbs': 0})
                char_rows[rids[r]] = record
            reference = load_reference(bid, evidence_dir() / 'dev20-snap')
            # Replace PDF layout whitespace only; retain all other content.
            pages = [' '.join(page.split()) for page in reference['pages']]
            texts[bid] = pages
            for page_number, (text, doc) in enumerate(zip(pages, nlp.pipe(pages)), 1):
                hits = {c: match_page(index, text, page_id=str(page_number))
                        for c, index in indexes[bid].items()}
                # One A/B/B_names overlap universe avoids artificial gains when
                # different-length names refer to the same physical occurrence.
                union = occurrence_ledger([h for hs in hits.values() for h in hs], text)
                found = {c: [] for c in summaries}
                for occurrence in union:
                    start, end = occurrence['start'], occurrence['end']
                    cs = {c: sorted({e for h in hs if start <= h['start'] and h['end'] <= end
                                    for e in h['candidate_entity_ids']}) for c, hs in hits.items()}
                    for c, entities in cs.items():
                        chosen = paired.intersection(entities)
                        if not chosen:
                            continue
                        summaries[c]['mentions'] += 1
                        summaries[c]['ambiguous'] += len(entities) > 1
                        for e in chosen:
                            char_rows[e][f'{c}_mentions'] += 1
                        if len(entities) == 1:
                            summaries[c]['unique'] += 1
                            char_rows[entities[0]][f'{c}_unique'] += 1
                            found[c].append((start, end, entities[0]))
                            span = doc.char_span(start, end, alignment_mode='expand')
                            if span is not None and len(span) == 1 and span[0].pos_ == 'PRON':
                                summaries[c]['literal_pronoun_hits'] += 1
                    # Sample genuinely new occurrences, not an A hit reassigned
                    # to a different identity (notably singular/group worms).
                    if len(cs['B']) == 1 and cs['B'][0] in paired and not cs['A']:
                        mention_pool.append({
                            'id': f'{bid}:{page_number}:{start}:{end}', 'book_id': bid,
                            'page': page_number, 'start': start, 'end': end,
                            'character': next(r['name'] for r in dictionaries['B']
                                              if r['entity_id'] == cs['B'][0]),
                            'reference_character': labels[cs['B'][0]], 'character_id': cs['B'][0],
                            'surface': text[start:end], 'A_candidates': cs['A'],
                            'context': text[max(0, start - 250):end + 250],
                            'page_text': text,
                        })
                    for c, other in (('B', 'A'), ('A', 'B')):
                        if len(cs[c]) == 1 and cs[c][0] in paired and not cs[other]:
                            summaries[c]['only_mentions'] += 1
                # Direct nominal subjects only; no pronoun/coreference or
                # coordination propagation. Count lexical VERB, not AUX.
                for c, occurrences in found.items():
                    events = set()
                    for token in doc:
                        if (token.dep_ not in {'nsubj', 'nsubjpass'} or token.pos_ == 'PRON'
                                or token.head.pos_ != 'VERB'):
                            continue
                        for start, end, identity in occurrences:
                            if start <= token.idx and token.idx + len(token) <= end:
                                events.add((token.head.i, identity, token.head.lemma_.lower()))
                    for _, identity, lemma in sorted(events):
                        char_rows[identity][f'{c}_verbs'] += 1
                        summaries[c]['verbs'] += 1
                        by_gender[c][char_rows[identity]['gender']][lemma] += 1
                        verb_rows[(bid, identity, lemma, c)] += 1
            for record in char_rows.values():
                for c in summaries:
                    summaries[c]['zero_mentions'] += record[f'{c}_mentions'] == 0
                    summaries[c]['zero_unique'] += record[f'{c}_unique'] == 0
            per_character.extend(char_rows.values())
        for row in rows:
            bid = row['book_id']
            result = {'row': row['row'], 'book_id': bid,
                      'section_id': row['speech_section_id']}
            for role in ('speaker', 'recipient'):
                resolved = {c: candidates(index, row[role]) for c, index in indexes[bid].items()}
                result[role] = resolved
                for c, entities in resolved.items():
                    status = 'unresolved' if not entities else 'unique' if len(entities) == 1 else 'ambiguous'
                    summaries[c][f'{role}_{status}'] += 1
                    if len(entities) == 1 and entities[0] in inventories[bid]['paired']:
                        summaries[c][f'{role}_paired_unique'] += 1
                if len(resolved['B']) == 1 and len(resolved['A']) != 1:
                    entity = resolved['B'][0]
                    speech_pool.append({
                        'id': f'{row["row"]}:{role}', 'book_id': bid, 'row': row['row'],
                        'role': role, 'character': labels[entity], 'character_id': entity,
                        'paired': entity in inventories[bid]['paired'], 'raw': row[role],
                        'speaker': row['speaker'], 'recipient': row['recipient'],
                        'speech_text': row['speech_text'], 'A_candidates': resolved['A'],
                    })
            speech_results.append(result)

    # The notebook samples the processed table's index and leaves the last id
    # awaiting judgement: zip to 199 completed flags, not 200 sampled ids.
    with (ANALYSIS / 'results/cm_validation_newrun_190924_100_42.pickle').open('rb') as file:
        validation = pickle.load(file)  # Local published file: ids and binary flags only.
    flags = {row: {k: v[i] for k, v in validation.items() if k != 'validation_sample_id'}
             for i, row in enumerate(validation['validation_sample_id'][:-1])}
    assert all(len(v) == len(flags) for k, v in validation.items() if k != 'validation_sample_id')
    validation_counts = Counter()
    for row, resolved in zip(rows, speech_results):
        if row['row'] not in flags:
            continue
        validation_counts['joined'] += 1
        flag = flags[row['row']]
        if flag['Speaker_matched wrong'] or flag['Speaker ambiguity'] or flag[
                'Error (e.g. speech section not found or not highlighted)']:
            validation_counts['no_accepted_identity'] += 1
            continue
        target = candidates(indexes[row['book_id']]['A'], row['speaker_matched'])
        if len(target) != 1:
            validation_counts['accepted_identity_not_unique'] += 1
            continue
        validation_counts['usable'] += 1
        for c in summaries:
            actual = resolved['speaker'][c]
            validation_counts[f'{c}_resolved'] += len(actual) == 1
            validation_counts[f'{c}_correct'] += actual == target

    rng = random.Random(SEED)
    samples = {
        'mentions': rng.sample(mention_pool, min(50, len(mention_pool))),
        'speech': rng.sample(speech_pool, min(30, len(speech_pool))),
    }
    sample_ids = {k: [r['id'] for r in v] for k, v in samples.items()}
    assert digest(sample_ids) == REVIEWED_SAMPLE, 'Sample changed: requires a fresh contextual review'
    for i, sample in enumerate(samples['mentions']):
        sample['judgement'] = ('wrong' if i in WRONG_MENTIONS else
                               'unclear' if i in UNCLEAR_MENTIONS else 'right')
        sample['reason'] = (WRONG_MENTIONS[i] if i in WRONG_MENTIONS else
                            'body-part/voice description: part versus character' if i in UNCLEAR_MENTIONS else
                            'context supports the attributed character')
    for sample in samples['speech']:
        sample['judgement'] = 'right'
        sample['reason'] = 'supported by speech section and adjoining Word-export pages'
    atomic_json(CACHE / 'samples.json', samples)
    atomic_json(CACHE / 'speech_results.json', speech_results)
    atomic_json(CACHE / 'reference_pages.json', texts)
    with (CACHE / 'per_character.csv').open('w') as file:
        writer = csv.DictWriter(file, fieldnames=list(per_character[0]))
        writer.writeheader()
        writer.writerows(per_character)
    with (CACHE / 'per_character_verbs.csv').open('w') as file:
        writer = csv.writer(file)
        writer.writerow(['book_id', 'character_id', 'name', 'lemma', 'A', 'B', 'B_names'])
        for bid, identity, lemma in sorted({key[:3] for key in verb_rows}):
            writer.writerow([bid, identity, labels[identity], lemma,
                             *(verb_rows[(bid, identity, lemma, c)] for c in summaries)])
    gender_summary = {}
    for gender in by_gender['A']:
        a, b = by_gender['A'][gender], by_gender['B'][gender]
        ranked = {c: sorted(by_gender[c][gender], key=lambda v: (-by_gender[c][gender][v], v))[:10]
                  for c in summaries}
        gender_summary[gender] = {
            c: {'verbs': sum(by_gender[c][gender].values()),
                'share': sum(by_gender[c][gender].values()) / summaries[c]['verbs'],
                'top10': [(v, by_gender[c][gender][v]) for v in ranked[c]]}
            for c in summaries}
        gender_summary[gender]['top10_overlap'] = len(set(ranked['A']) & set(ranked['B']))
        gender_summary[gender]['total_variation'] = 0.5 * sum(
            abs(a[v] / sum(a.values()) - b[v] / sum(b.values())) for v in sorted(a.keys() | b.keys()))
    sensitivity = {}
    stable = [r for r in per_character if r['book_id'] != 'wiggling_worms_at_work']
    for c in summaries:
        assert sum(r[f'{c}_unique'] for r in per_character) == summaries[c]['unique']
        assert sum(r[f'{c}_verbs'] for r in per_character) == summaries[c]['verbs']
        assert sum(sum(v.values()) for v in by_gender[c].values()) == summaries[c]['verbs']
        sensitivity[c] = {
            'paired_characters': len(stable),
            'unique_mentions': sum(r[f'{c}_unique'] for r in stable),
            'verbs': sum(r[f'{c}_verbs'] for r in stable),
            'gender_verbs': {g: sum(r[f'{c}_verbs'] for r in stable if r['gender'] == g)
                            for g in by_gender[c]},
        }
    transitions = {}
    for role in ('speaker', 'recipient'):
        transitions[role] = dict(Counter(
            ('both_same' if r[role]['A'] == r[role]['B'] else 'both_different')
            if len(r[role]['A']) == len(r[role]['B']) == 1 else
            'A_only' if len(r[role]['A']) == 1 else
            'B_only' if len(r[role]['B']) == 1 else 'neither'
            for r in speech_results))
    with JsonSources(ANALYSIS / 'results/CM_SPEECH_DETECTION_VALIDATION_NEWRUN_190924.json') as source:
        detection = source.members(source.root)
        detection_labels = len(source.members(detection['SID']))
        assert set(detection) == {'SID', 'DIRECT_SPEECH', 'INDIRECT_SPEECH', 'FOUND', 'JUNK'}
    summary = {
        'seed': SEED, 'spacy': spacy.__version__, 'model': nlp.meta['version'],
        'books': len(books), 'speech_books': len({r['book_id'] for r in rows}),
        'speech_sections': len(rows), 'paired_characters': len(per_character),
        'pairing': pairing, 'counts': summaries, 'gender': gender_summary,
        'database_crosscheck': db_check, 'validation': validation_counts,
        'detection_labels': detection_labels,
        'detection_join': 'Unavailable: SID is a sentence index, without book/section crosswalk',
        'alias_counts': alias_counts, 'excluding_ambiguous_worm_pair': sensitivity,
        'speech_transitions': transitions,
        'agent_review': {k: {label: sum(r['judgement'] == label for r in v)
                             for label in ('right', 'wrong', 'unclear')} for k, v in samples.items()},
        'B_only_mention_pool': len(mention_pool), 'B_only_speech_pool': len(speech_pool),
        'sample_ids_sha256': digest(sample_ids),
    }
    atomic_json(CACHE / 'summary.json', summary)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
