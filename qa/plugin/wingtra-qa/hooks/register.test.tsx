import { expect, test } from 'claude-code/testing'

const SNAPSHOT = {
  refreshed: '2026-10-21T09:00:58+00:00',
  tablets: [
    { serial: 'TA5SERIAL01', tag: 'TA5', model: 'SM-X350', android: '16', adb: 'unauthorized', registered: true, cdp_port: 9341,
      focus: null, app_pid: null, forward: null, lock: null, case: null, state: 'LOST', why: 'USB debugging not allowed on the tablet: tap Allow' },
  ],
  webpc044: { state: 'DEGRADED', link: true, ssh: true, chrome_9222: 'down',
    full_check: { at: '2026-10-21T08:57:13.703Z', ok: false, failing: ['only_sshd_listening', 'tunnel'], browser: null, clock_offset_ms: 20 } },
  ledger: { seq: 1062, head: '84a388edf117' },
}

function engine(on: Parameters<Parameters<typeof test>[1]>[1], reads: string[] = [], runs: string[][] = []) {
  on('command.register', () => ({ value: { command: 'qa-status' } }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('clock.every', () => ({ value: undefined }))
  let cwd = '/'
  on('session.start', (_, e) => {
    cwd = e.cwd
    return { cwd: e.cwd }
  })
  on('session.cwd', () => ({ value: cwd }))
  on('fs.read', (_, e) => {
    reads.push(String(e.path))
    return { value: JSON.stringify(SNAPSHOT) }
  })
  on('process.run', (_, e) => {
    runs.push(e.argv)
    return { value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('tool.call', () => ({ result: { ran: true } }))
}

test('tool calls no longer go through the mod: the gate lives in .claude/settings.json', async ($, on) => {
  const runs: string[][] = []
  engine(on, [], runs)
  await $.session.start({ cwd: '/ws', surface: 'desktop', isInteractive: true })
  const r = (await $.tool.call({ tool: 'Bash', command: 'git -C WingtraCloud stash list' })) as { deny?: string; isError?: boolean }
  expect(r.deny).toBeUndefined()
  expect(r.isError === true).toBe(false)
  expect(runs.some(a => a.some(x => x.endsWith('gate_hook.py') || x.endsWith('ledger_hook.py')))).toBe(false)
})

test('the pane reads the engine under the session folder, not under the install cache', async ($, on) => {
  const reads: string[] = []
  const runs: string[][] = []
  engine(on, reads, runs)
  await $.session.start({ cwd: '/ws', surface: 'desktop', isInteractive: true })
  await $.command.run({ command: 'qa-status', args: '' })
  expect(reads).toContain('/ws/qa/runner/state/status.json')
  expect(runs.some(a => a.includes('/ws/qa/runner/status.py'))).toBe(true)
})

test('the pane shows each tablet with its state, on every surface', async ($, on) => {
  engine(on)
  await $.session.start({ cwd: '/', surface: 'desktop', isInteractive: true })
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({
      plugin: 'wingtra-qa', component: 'Pane', requestId: 'qa-status', surface,
      props: { title: 'QA engine', isFocused: false, bodyColumns: 80, placement: 'dock', scroll: { offset: 0, bodyRows: 30 }, view: {} },
    })
    expect((await ui.find({ type: 'Text', text: /TA5/ }))?.text).toContain('TA5')
    expect((await ui.find({ type: 'Text', text: /tap Allow/ }))?.text).toContain('tap Allow')
    expect((await ui.find({ type: 'Text', text: /failing only_sshd_listening/ }))?.text).toContain('tunnel')
    await ui.unmount()
  }
})
