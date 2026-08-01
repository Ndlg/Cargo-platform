import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const exceptionsView = readFileSync(
  new URL('../src/views/workbench/ExceptionsView.vue', import.meta.url),
  'utf8',
)
const apiTypes = readFileSync(new URL('../src/services/api.ts', import.meta.url), 'utf8')

test('collection-source exceptions route to collector connections', () => {
  for (const code of ['timestamp_invalid_fallback', 'source_history_ambiguous']) {
    assert.match(exceptionsView, new RegExp(`${code}:[\\s\\S]*?target: 'collector-connections'`))
  }
  assert.match(exceptionsView, /path: '\/admin\/collector-connections'/)
  assert.match(apiTypes, /exception_code\?: string \| null/)
})
