import assert from 'node:assert/strict'

import {
  captureRoundRoute,
  selectCaptureRoundId,
} from '../src/views/workbench/captureRoundSelection.ts'
import {
  parserIssueFor,
  parserIssueRoute,
} from '../src/views/workbench/parserIssues.ts'

const rounds = [{ id: 91 }, { id: 14 }, { id: 58 }]

assert.equal(selectCaptureRoundId(rounds, '14'), 14)
assert.equal(selectCaptureRoundId(rounds, 'not-a-number'), 91)
assert.equal(selectCaptureRoundId(rounds, '999'), 91)
assert.equal(selectCaptureRoundId([], '14'), null)
assert.deepEqual(captureRoundRoute('/exceptions', 14), {
  path: '/exceptions',
  query: { task_id: '14' },
})
assert.deepEqual(captureRoundRoute('/exports', 14, { status: 'pending' }), {
  path: '/exports',
  query: { status: 'pending', task_id: '14' },
})
assert.deepEqual(captureRoundRoute('/exports', null, { task_id: '14' }), {
  path: '/exports',
  query: {},
})

const missingPack = parserIssueFor('rule_pack_missing')
assert.equal(missingPack?.action, 'format-learning')
assert.equal(parserIssueRoute(missingPack?.action ?? 'refresh'), '/admin/format-learning')

const unsupportedFingerprint = parserIssueFor('fingerprint_adapter_required')
assert.equal(unsupportedFingerprint?.action, 'refresh')
assert.equal(parserIssueRoute(unsupportedFingerprint?.action ?? 'refresh'), null)

const ambiguousProfile = parserIssueFor('profile_ambiguous')
assert.equal(ambiguousProfile?.action, 'format-learning')

assert.equal(parserIssueFor('parsed'), null)

const unknownFailure = parserIssueFor('new_parser_failure', '', true)
assert.equal(unknownFailure?.action, 'format-learning')
assert.equal(parserIssueRoute(unknownFailure?.action ?? 'refresh'), '/admin/format-learning')

console.log('task3 recognition flow contracts passed')
