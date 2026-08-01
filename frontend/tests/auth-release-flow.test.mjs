import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const apiSource = await readFile(new URL('../src/services/api.ts', import.meta.url), 'utf8')
const loginSource = await readFile(new URL('../src/views/LoginView.vue', import.meta.url), 'utf8')

assert.doesNotMatch(loginSource, /admin123/, 'the release login page must not expose a default password')
assert.match(apiSource, /getAuthSetupStatus/, 'the login page must discover first-run setup state')
assert.match(apiSource, /setupSystemAdmin/, 'the first administrator must be initialized through the API')
assert.match(loginSource, /setup_token/, 'first-run setup must require the deployment setup token')
assert.match(loginSource, /确认新密码/, 'first-run setup must ask the administrator to confirm the password')
assert.match(loginSource, /setupStatus\?\.required/, 'normal login must be hidden until setup is complete')

console.log('auth release flow contracts passed')
