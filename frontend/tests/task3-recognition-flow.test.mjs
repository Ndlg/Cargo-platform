import assert from 'node:assert/strict'

import {
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

const missingPack = parserIssueFor('rule_pack_missing')
assert.equal(missingPack?.action, 'ai-recognition')
assert.equal(parserIssueRoute(missingPack?.action ?? 'refresh'), '/admin/ai-recognition')

const unsupportedFingerprint = parserIssueFor('fingerprint_adapter_required')
assert.equal(unsupportedFingerprint?.action, 'refresh')
assert.equal(parserIssueRoute(unsupportedFingerprint?.action ?? 'refresh'), null)

assert.equal(parserIssueFor('parsed'), null)

const unknownFailure = parserIssueFor('new_parser_failure', '', true)
assert.equal(unknownFailure?.action, 'ai-recognition')
assert.equal(parserIssueRoute(unknownFailure?.action ?? 'refresh'), '/admin/ai-recognition')

console.log('task3 recognition flow contracts passed')
