import assert from 'node:assert/strict'

import {
  selectCaptureRoundId,
} from '../src/views/workbench/captureRoundSelection.ts'
import {
  parserIssueFor,
  parserIssueRoute,
} from '../src/views/workbench/parserIssues.ts'
import {
  learningRecordForProfile,
  withoutProfileLearningRecords,
} from '../src/views/workbench/recognitionProfileLearning.ts'

const rounds = [{ id: 91 }, { id: 14 }, { id: 58 }]

assert.equal(selectCaptureRoundId(rounds, '14'), 14)
assert.equal(selectCaptureRoundId(rounds, 'not-a-number'), 91)
assert.equal(selectCaptureRoundId(rounds, '999'), 91)
assert.equal(selectCaptureRoundId([], '14'), null)

const missingPack = parserIssueFor('rule_pack_missing')
assert.equal(missingPack?.action, 'ai-recognition')
assert.equal(parserIssueRoute(missingPack?.action ?? 'refresh'), '/admin/ai-recognition')

const unsupportedFingerprint = parserIssueFor('fingerprint_adapter_required')
assert.equal(unsupportedFingerprint?.action, 'refresh')
assert.equal(parserIssueRoute(unsupportedFingerprint?.action ?? 'refresh'), null)

const ambiguousProfile = parserIssueFor('profile_ambiguous')
assert.equal(ambiguousProfile?.action, 'ai-recognition')

assert.equal(parserIssueFor('parsed'), null)

const unknownFailure = parserIssueFor('new_parser_failure', '', true)
assert.equal(unknownFailure?.action, 'ai-recognition')
assert.equal(parserIssueRoute(unknownFailure?.action ?? 'refresh'), '/admin/ai-recognition')

const sharedFormat = {
  fingerprint: 'v2:CN-PRINT-XML:shared',
  grammar_signature: 'same-grammar',
  strategy: 'text_pipeline_v1',
}
const profileA = {
  ...sharedFormat,
  provenance: { source: 'confirmed_ai_rule', learning_session_id: 'session-a' },
}
const profileB = {
  ...sharedFormat,
  selected_fields: ['task.documents[].contents[].printXML'],
  provenance: { source: 'confirmed_ai_rule', learning_session_id: 'session-b' },
}
const learningRecords = [
  { fingerprint: sharedFormat.fingerprint, grammar_signature: sharedFormat.grammar_signature, session_id: 'session-a' },
  { fingerprint: sharedFormat.fingerprint, grammar_signature: sharedFormat.grammar_signature, session_id: 'session-b' },
]

assert.equal(learningRecordForProfile(profileA, learningRecords)?.session_id, 'session-a')
assert.deepEqual(
  withoutProfileLearningRecords(profileA, learningRecords)?.map((record) => record.session_id),
  ['session-b'],
)
assert.equal(
  learningRecordForProfile(profileB, learningRecords)?.session_id,
  'session-b',
  'profiles sharing a fingerprint must bind by provenance session, not inferred fields',
)

const profileWithoutProvenance = { ...sharedFormat }
assert.equal(learningRecordForProfile(profileWithoutProvenance, learningRecords), undefined)
assert.equal(
  withoutProfileLearningRecords(profileWithoutProvenance, learningRecords),
  null,
  'missing provenance must fail closed instead of deleting a guessed learning record',
)

console.log('task3 recognition flow contracts passed')
