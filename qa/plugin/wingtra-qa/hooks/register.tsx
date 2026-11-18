import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { QaStatus, QaTablet } from '../types'

// The QA engine's mod: the status pane only. The gate (qa/runner/gate_hook.py) and the ledger hook
// (qa/runner/ledger_hook.py) are command hooks in the workspace's .claude/settings.json, so they run from the
// workspace even though sessions load this plugin from the install cache.
const PANE = 'qa-status'
const EMPTY: QaStatus = { refreshed: '', tablets: [], webpc044: null, ledger: { seq: 0, head: '' } }
const status = atom({ plugin: 'wingtra-qa', key: 'status' } as const, EMPTY)

const STATE_COLOR: Record<string, string> = {
  READY: 'green',
  BUSY: 'yellow',
  HANDED_OVER: 'cyan',
  OFFLINE_EXPECTED: 'gray',
  DEGRADED: 'yellow',
  LOST: 'red',
}

// qa/runner of the workspace: the session's folder or a parent of it; the plugin's own folder when it is loaded
// from source (qa/plugin/wingtra-qa/../../runner). An installed copy lives in the cache, away from the workspace.
let runnerCache: { cwd: string; dir: string } | undefined

async function runner($: EngineInterface): Promise<string> {
  const cwd = await $.session.cwd()
  if (runnerCache?.cwd === cwd) return runnerCache.dir
  const candidates: string[] = []
  let dir = cwd.replace(/\/+$/, '')
  for (let i = 0; i < 6; i++) {
    candidates.push(`${dir}/qa/runner`)
    if (!dir) break
    dir = dir.slice(0, dir.lastIndexOf('/'))
  }
  candidates.push(`${$.plugin.root}/../../runner`)
  for (const c of candidates) {
    try {
      await $.fs.read(`${c}/engine.json`)
      runnerCache = { cwd, dir: c }
      return c
    } catch {
      // not the engine folder
    }
  }
  return candidates[0]
}

async function load($: EngineInterface): Promise<void> {
  try {
    const text = await $.fs.read(`${await runner($)}/state/status.json`)
    const snap = JSON.parse(text) as QaStatus
    await update($, status, old => ({ ...snap, busy: old.busy }))
  } catch {
    await update($, status, old => ({ ...old, error: 'No status yet: press Refresh.' }))
  }
}

async function runThenRefresh($: EngineInterface, label: string, script: string): Promise<void> {
  const dir = await runner($)
  await update($, status, old => ({ ...old, busy: label }))
  try {
    await $.process.run(['python3', '-I', `${dir}/${script}`], { timeoutMs: 120000 })
  } finally {
    await $.process.run(['python3', '-I', `${dir}/status.py`], { timeoutMs: 30000 })
    await update($, status, old => ({ ...old, busy: undefined }))
    await load($)
  }
}

export const register: Register = on => {

  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'qa-status', description: 'Open the WingtraApp QA engine status pane' })
    $.clock.every(5000, () => void load($).catch(() => undefined))
    void load($).catch(() => undefined)
    return next(e)
  })

  on('command.run', { command: 'qa-status' }, async $ => {
    await $.ui.open({ id: PANE, title: 'QA engine' })
    await $.process.run(['python3', '-I', `${await runner($)}/status.py`], { timeoutMs: 30000 })
    await load($)
    return { text: 'QA engine pane opened.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const s = await read($, status)

    const tabletRow = (t: QaTablet) => (
      <Box key={t.serial} flexDirection="column">
        <Text>
          <Text bold>{t.tag}</Text>
          <Text color={STATE_COLOR[t.state] ?? 'white'}> {t.state}</Text>
          <Text dimColor>
            {'  '}
            {t.model ?? '?'} · Android {t.android ?? '?'} · {t.serial}
          </Text>
        </Text>
        <Text dimColor>
          {'   '}adb {t.adb}
          {t.focus ? ` · focus ${t.focus}` : ''}
          {t.app_pid ? ` · app pid ${t.app_pid}` : ''}
          {t.cdp_port ? ` · CDP ${t.cdp_port}${t.forward ? ' (forwarded)' : ''}` : ''}
          {t.registered ? '' : ' · not registered'}
        </Text>
        {t.lock && <Text dimColor>{'   '}lock: {t.lock}{t.case ? ` · case ${t.case}` : ''}</Text>}
        {t.why && <Text color={t.state === 'READY' ? 'yellow' : 'red'}>{'   '}{t.why}</Text>}
      </Box>
    )

    const w = s.webpc044
    return (
      <Box flexDirection="column" gap={1}>
        <Box flexDirection="column">
          <Text bold>Tablets</Text>
          {s.tablets.length === 0 && <Text dimColor>{s.error ?? 'Loading…'}</Text>}
          {s.tablets.map(tabletRow)}
        </Box>
        <Box flexDirection="column">
          <Text bold>webpc044 (web)</Text>
          {w ? (
            <Box flexDirection="column">
              <Text>
                <Text color={STATE_COLOR[w.state] ?? 'white'}>{w.state}</Text>
                <Text dimColor>
                  {'  '}cable {w.link ? 'up' : 'down'} · ssh {w.ssh ? 'ok' : 'no'} · Chrome 9222 {w.chrome_9222 ?? '?'}
                </Text>
              </Text>
              <Text dimColor>
                {'   '}full check {w.full_check.at ? w.full_check.at.slice(0, 16).replace('T', ' ') : 'never'}:{' '}
                {w.full_check.ok ? 'all pass' : `failing ${w.full_check.failing.join(', ') || '?'}`}
                {w.full_check.clock_offset_ms !== null ? ` · clock ${w.full_check.clock_offset_ms} ms` : ''}
              </Text>
            </Box>
          ) : (
            <Text dimColor>No status yet.</Text>
          )}
        </Box>
        <Text dimColor>
          Ledger seq {s.ledger.seq} · head {s.ledger.head} · refreshed {s.refreshed ? s.refreshed.slice(11, 19) : '-'} UTC
          {s.busy ? ` · running ${s.busy}…` : ''}
        </Text>
        <Box flexDirection="row" gap={2}>
          <Button variant="primary" onPress={() => void runThenRefresh($, 'status', 'status.py')}>
            Refresh
          </Button>
          <Button onPress={() => void runThenRefresh($, 'webpc044 check', 'check_webpc044.py')}>
            Check webpc044
          </Button>
        </Box>
      </Box>
    )
  })
}
