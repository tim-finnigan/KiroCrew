/**
 * Recording harness for the Crewmates floating-roster feature.
 *
 * Runs the REAL built SPA (website/dist) behind serveDist + stubDashboardApi
 * (no gateway/auth/kiro-cli, so it works on a host whose kernel refuses the
 * sandbox). Records a VIDEO of the wide-viewport interaction the UX Review lens
 * 13 needs to see as a recording, not a still:
 *   - a member open on a >1-crewmate roster shows the hamburger (card closed)
 *   - clicking it morphs the floating roster card IN; the hamburger steps aside
 *   - clicking the card's X morphs it OUT; the hamburger returns
 * Then a separate 1-crewmate context asserts the gate: no hamburger, no card.
 *
 * Usage: node scripts/capture-floating-roster.mjs [outDir]
 */
import { chromium } from 'playwright'
import { mkdirSync, renameSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

import { json } from './lib/boot-api.mjs'
import { serveDist } from './lib/serve-dist.mjs'
import { stubDashboardApi, logPageProblems } from './lib/stub-dashboard-api.mjs'

const OUT = process.argv[2] || join(process.env.KIROCREW_SCRATCH || '/tmp', 'floating-roster')
mkdirSync(OUT, { recursive: true })

const member = (name, extra = {}) => ({
  name, slug: name, bound: true, slot_key: `member-${name}`, running: false,
  kiro_agent: 'kirocrew', workspace: 'default', memory_store: `member-${name}`,
  model: '', last_active_ts: Math.floor(Date.now() / 1000) - 300,
  last_message: 'On it.', ...extra,
})
const MANY = [member('radar'), member('scribe'), member('courier')]
const ONE = [member('radar')]

let failed = false
const check = (name, ok, detail = '') => { console.log(`${name}: ${ok ? 'OK' : 'MISMATCH'} ${detail}`); if (!ok) failed = true; return ok }

const extraFor = (members) => async (path, route) => {
  if (path === '/api/members') { await json(route, { members, default_agent: 'kirocrew' }); return true }
  if (path === '/api/crons') { await json(route, { jobs: [] }); return true }
  if (path === '/api/cron-folders') { await json(route, []); return true }
  if (path === '/api/default-agent') { await json(route, { default_agent: 'kirocrew' }); return true }
  const thread = path.match(/^\/api\/members\/([^/]+)\/thread$/)
  if (thread) { const slug = decodeURIComponent(thread[1]); await json(route, { slot_key: `member-${slug}`, slug, member: slug, created: false }); return true }
  if (/^\/api\/members\/[^/]+\/activity$/.test(path)) { await json(route, { slug: '', member: '', capped: false, entries: [] }); return true }
  if (/^\/api\/members\/[^/]+\/briefing$/.test(path)) { await json(route, { slug: '', member: '', supported: true, text: '', updated_ts: null, redacted: false, truncated: false }); return true }
  if (/^\/api\/members\/[^/]+\/panel$/.test(path)) { await json(route, { panel: null, html: null }); return true }
  if (path === '/api/autonudge') { await json(route, { enabled: true, loops: [] }); return true }
  if (path === '/api/teams') { await json(route, { teams: [] }); return true }
  return false
}

const { srv, base } = await serveDist()
const browser = await chromium.launch()

async function newCtx(members, record) {
  const context = await browser.newContext({
    viewport: { width: 1500, height: 940 }, deviceScaleFactor: 1, colorScheme: 'dark',
    ...(record ? { recordVideo: { dir: OUT, size: { width: 1500, height: 940 } } } : {}),
  })
  const page = await context.newPage()
  logPageProblems(page)
  await stubDashboardApi(page, {
    theme: 'dark', extra: extraFor(members),
    localStorageEntries: { 'mc-lang': 'en', 'mc-crewmates-onboarded': '1', 'mc-members-roster-open': 'false' },
  })
  await page.goto(`${base}/members?member=radar`, { waitUntil: 'domcontentloaded' })
  await page.getByTestId('member-thread-header').waitFor({ state: 'visible', timeout: 30000 })
  return { context, page }
}

// ── Recording: open + close continuity on a >1-crewmate roster ───────────────
{
  const { context, page } = await newCtx(MANY, true)
  const toggle = page.getByTestId('member-roster-toggle')
  await toggle.waitFor({ state: 'visible', timeout: 20000 })
  check('hamburger shows when >1 mate and card closed', await toggle.count() === 1)
  check('no card yet', await page.getByTestId('member-roster-card').count() === 0)
  await page.waitForTimeout(700)
  await toggle.click()
  const card = page.getByTestId('member-roster-card')
  await card.waitFor({ state: 'visible', timeout: 10000 })
  check('card appears on toggle', await card.count() === 1)
  await page.waitForTimeout(400)
  check('hamburger hidden while card open', await page.getByTestId('member-roster-toggle').count() === 0)
  // Shot of the open state for a still reference beside the video.
  await page.screenshot({ path: join(OUT, 'card-open.png') })
  await page.waitForTimeout(600)
  await page.getByTestId('member-roster-card-close').click()
  await card.waitFor({ state: 'detached', timeout: 10000 }).catch(() => {})
  await page.waitForTimeout(500)
  check('hamburger returns after close', await page.getByTestId('member-roster-toggle').count() === 1)
  await page.screenshot({ path: join(OUT, 'card-closed.png') })
  await page.waitForTimeout(400)
  await context.close() // flushes the video
}

// ── Gate: a 1-crewmate roster shows neither toggle nor card ──────────────────
{
  const { context, page } = await newCtx(ONE, false)
  await page.waitForTimeout(500)
  check('no hamburger with a single crewmate', await page.getByTestId('member-roster-toggle').count() === 0)
  check('no card with a single crewmate', await page.getByTestId('member-roster-card').count() === 0)
  await context.close()
}

await browser.close()
srv.close()

// Rename the recorded video to a stable name.
try {
  const vids = readdirSync(OUT).filter((f) => f.endsWith('.webm'))
  if (vids.length) { renameSync(join(OUT, vids[0]), join(OUT, 'floating-roster.webm')); console.log('video:', join(OUT, 'floating-roster.webm')) }
} catch (e) { console.log('video rename skipped:', e.message) }

console.log(failed ? 'RESULT: MISMATCH' : 'RESULT: OK')
process.exit(failed ? 1 : 0)
