export type QaTablet = {
  serial: string
  tag: string
  model: string | null
  android: string | null
  adb: string
  registered: boolean
  cdp_port: number | null
  focus: string | null
  app_pid: string | null
  forward: boolean | null
  lock: string | null
  case: string | null
  state: string
  why: string
}

export type QaWebpc044 = {
  state: string
  link: boolean
  ssh: boolean
  chrome_9222: string | null
  full_check: {
    at: string | null
    ok: boolean | null
    failing: string[]
    browser: string | null
    clock_offset_ms: number | null
  }
}

export type QaStatus = {
  refreshed: string
  tablets: QaTablet[]
  webpc044: QaWebpc044 | null
  ledger: { seq: number; head: string }
  error?: string
  busy?: string
}

declare module 'claude-code' {
  interface PluginState {
    'wingtra-qa': { status: QaStatus }
  }
}
