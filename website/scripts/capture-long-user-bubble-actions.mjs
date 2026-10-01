/**
 * Screenshot harness for the one-line pinned-prompt hand-off and the action
 * strip under it.
 *
 * A user prompt taller than the viewport stays on screen as its own row while
 * more than one line of its bubble is above the reply: no pinned card, and the
 * row's own action strip (copy, copy link, pin, timestamp) is reachable there
 * like any other row's. Only once the bubble's last line is all that remains
 * above the reply does the one-line card take over and the row hide; the strip,
 * hanging under that line, is then still below the card and is re-shown in place
 * until it has slid under. This scene drives the REAL transcript (built SPA,
 * `/api/**` fixtures) through both frames and reads the card's and the strip's
 * fate off the live DOM, not the pixels alone:
 *
 *   - `expected=after`  (one-line build): with the tall bubble mostly on screen
 *     there is NO card and the row is not hidden, its strip is visible on hover
 *     and hit-testable, a click on Copy lands (the button flips to its copied
 *     state) and the Edit pencil is present; with one line left the card is
 *     mounted at its resting height, the row is the stand-in, the strip is
 *     visible beneath the card and not covered by it, and the pin control is
 *     there (the fixture messages carry `meta.mid`, which is what makes a
 *     message pinnable).
 *   - `expected=before` (a build that folded the card down the bubble's
 *     remaining height): with the tall bubble mostly on screen the row is
 *     already hidden and a card far taller than one line stands in for it —
 *     recorded so the pair is evidence of a change.
 *
 * Usage, from website/ after `npm run build`:
 *   node scripts/capture-long-user-bubble-actions.mjs [outDir] [--dist DIR] [--expected before|after]
 *   node scripts/capture-long-user-bubble-actions.mjs [outDir] --record   # one webm, both scroll directions
 *
 * `--dist` points a run at another build of the same page (the before/after
 * pair is two runs, two dists, one script).
 */
import { mkdirSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

// The node toolchain injects its own libstdc++ on LD_LIBRARY_PATH, which the
// bundled Chromium then loads in preference to the system one and fails on.
delete process.env.LD_LIBRARY_PATH

const { openTranscriptHarness } = await import('./lib/transcript-harness.mjs')

const argv = process.argv.slice(2)
const flag = (name, fallback) => {
  const i = argv.indexOf(name)
  return i >= 0 && argv[i + 1] ? argv[i + 1] : fallback
}
const OUT = argv.find((a, i) => !a.startsWith('--') && (i === 0 || !argv[i - 1].startsWith('--')))
  || '../temp-screenshots/long-user-bubble-actions'
const DIST = flag('--dist', undefined)
const EXPECTED = flag('--expected', 'after')
if (!['before', 'after'].includes(EXPECTED)) {
  console.error(`FAIL: --expected must be before|after, got ${JSON.stringify(EXPECTED)}`)
  process.exit(2)
}
const SLOT = 'chat-longbubble'
// Derived from this script's own location (scripts/ -> website/ -> repo root),
// never hardcoded: this path RENDERS into the captured screenshot, so a personal
// absolute path both leaks a home directory and misrepresents any other checkout.
const PROJECT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')

mkdirSync(OUT, { recursive: true })

const now = Date.now() / 1000
// Far taller than any viewport: 70 lines at ~23px each is ~1600px of bubble.
const LONG_PROMPT = [
  'Please review the deployment plan below before I hand it to the team.',
  ...Array.from({ length: 68 }, (_, i) => `Step ${i + 1}: check the ${['cache', 'queue', 'index', 'replica', 'gateway'][i % 5]} rollout order, confirm the health check window, and record who signs off.`),
  'That is the whole plan. Tell me what is missing.',
].join('\n')
const REPLY = [
  'Read the plan end to end. Two gaps: the queue drain in step 12 has no owner, and the replica',
  'cutover in step 40 lands inside the health check window it is supposed to wait for.',
  '',
  'Everything else is ordered correctly. Steps 1-11 can run in parallel; the rest is strictly serial.',
].join('\n')
// A prompt short enough to sit whole on screen unpinned, taller than one line.
const MEDIUM_PROMPT = [
  'Three constraints before you touch the plan:',
  'Constraint A: the cache and queue steps must never run in the same window.',
  'Constraint B: every replica cutover needs a named approver on the call.',
  'Constraint C: the gateway steps are frozen until the index rebuild reports green.',
  'Fold those in and send me the revised order.',
].join('\n')
const REPLY_2 = [
  'Folded in. Constraint A splits steps 1-11 into two windows, cache first and queue second.',
  'Constraint B adds an approver line to steps 4, 9, 14 and every later replica step.',
  'Constraint C moves the four gateway steps behind step 43, where the index rebuild reports.',
  '',
  'The revised order is below, with the changed steps marked.',
  // Tall enough that the prompt above it can scroll all the way up to the fold:
  // the transcript's scroll range ends at this reply's bottom.
  ...Array.from({ length: 44 }, (_, i) => `Revised ${i + 1}: unchanged from the original order.`),
].join('\n')
// A steer confirmed into the running turn: badge above an accent bubble, which is
// the row shape whose stand-in height is measured from the row top.
const STEER_PROMPT = [
  'Steering while you work: skip the replica steps entirely for this pass.',
  'The replica team is doing their own cutover tomorrow, so leave 4, 9, 14 and the later ones out.',
  'Everything else stands.',
].join('\n')
const REPLY_3 = [
  'Understood, the replica steps are out of this pass.',
  ...Array.from({ length: 40 }, (_, i) => `Pass ${i + 1}: no replica work, order otherwise unchanged.`),
].join('\n')

const slots = [{
  key: SLOT, title: 'Deployment plan review', running: false,
  last_message: 'The revised order is below.', messages: 6, agent: 'kirocrew',
  memory_mode: 'persistent', project: PROJECT, modified: Math.floor(now),
  source_links: [], source_links_total: 0,
}]
// `meta.mid` is what makes a message pinnable in ChatPage (the pin button is
// gated on it), so every row carries one: the report names the pin icon.
const detail = {
  running: false, has_more: false, total: 6, queue: [], project: PROJECT,
  messages: [
    { role: 'user', ts: now - 3000, content: 'Morning. I have a long one coming.', meta: { mid: 'm-1' } },
    { role: 'assistant', ts: now - 2950, content: 'Go ahead, paste it in full.', meta: { mid: 'm-2' } },
    { role: 'user', ts: now - 900, content: LONG_PROMPT, meta: { mid: 'm-3' } },
    { role: 'assistant', ts: now - 600, content: REPLY, meta: { mid: 'm-4' } },
    { role: 'user', ts: now - 300, content: MEDIUM_PROMPT, meta: { mid: 'm-5' } },
    { role: 'assistant', ts: now - 200, content: REPLY_2, meta: { mid: 'm-6' } },
    // `steer: true` + `steerState: 'consumed'` is the backend-confirmed injection
    // UserMessage draws with the badge and the accent bubble.
    { role: 'user', ts: now - 120, content: STEER_PROMPT, meta: { mid: 'm-7', steer: true, steerState: 'consumed' } },
    { role: 'assistant', ts: now - 30, content: REPLY_3, meta: { mid: 'm-8' } },
  ],
}

let failures = 0
const assert = (label, ok) => {
  console.log(`${ok ? 'PASS' : 'FAIL'}: ${label}`)
  if (!ok) failures += 1
}

/** Geometry + visibility of one prompt's row, bubble, strip and the card. */
async function inspect(page, marker) {
  return page.evaluate((needle) => {
    const rows = [...document.querySelectorAll('[data-display-index]')]
    const row = rows.find(r => r.textContent.includes(needle))
    if (!row) return { error: `row containing ${JSON.stringify(needle)} not mounted` }
    const bubble = row.querySelector('.message-bubble')
    const copy = row.querySelector('button[title="Copy"]')
    const pin = row.querySelector('button[title="Pin message"], button[title="Unpin message"]')
    const edit = row.querySelector('[data-message-edit]') || row.querySelector('button[title="Edit & resend"]')
    const strip = copy ? copy.parentElement : null
    const card = document.querySelector('[data-testid="pinned-prompt"]')
    const scroller = document.querySelector('.chat-container')
    const r = el => { const b = el.getBoundingClientRect(); return { top: b.top, bottom: b.bottom, left: b.left, right: b.right, height: b.height } }
    const mid = el => { const b = el.getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2] }
    let copyHit = null
    if (copy) {
      const [x, y] = mid(copy)
      const hit = document.elementFromPoint(x, y)
      copyHit = hit ? (hit === copy || copy.contains(hit)) : false
    }
    return {
      rowHidden: row.style.visibility === 'hidden',
      rowStandin: row.hasAttribute('data-pinned-standin'),
      rowStandinValue: row.getAttribute('data-pinned-standin'),
      row: r(row),
      bubble: bubble ? r(bubble) : null,
      strip: strip ? { ...r(strip), visibility: getComputedStyle(strip).visibility, opacity: getComputedStyle(strip).opacity } : null,
      copy: copy ? { ...r(copy), visibility: getComputedStyle(copy).visibility, hit: copyHit, label: copy.getAttribute('aria-label') } : null,
      pin: pin ? { visibility: getComputedStyle(pin).visibility, label: pin.getAttribute('aria-label') } : null,
      edit: edit ? { display: getComputedStyle(edit).display, visibility: getComputedStyle(edit).visibility } : null,
      card: card ? { ...r(card), text: (card.textContent || '').slice(0, 40) } : null,
      viewport: { w: innerWidth, h: innerHeight },
      scrollTop: scroller ? scroller.scrollTop : null,
    }
  }, marker)
}

/** Scroll so the marked prompt's bubble BOTTOM sits at `frac` of the viewport. */
async function placeBubbleBottom(page, marker, frac) {
  await page.evaluate(([needle, f]) => {
    const rows = [...document.querySelectorAll('[data-display-index]')]
    const row = rows.find(r => r.textContent.includes(needle))
    const bubble = row.querySelector('.message-bubble')
    const scroller = document.querySelector('.chat-container')
    const target = scroller.getBoundingClientRect().top + scroller.clientHeight * f
    scroller.scrollTop += bubble.getBoundingClientRect().bottom - target
  }, [marker, frac])
  await page.waitForTimeout(500)
}

/** Scroll so the marked prompt's row TOP sits at `frac` of the viewport. */
async function placeRowTop(page, marker, frac) {
  await page.evaluate(([needle, f]) => {
    const rows = [...document.querySelectorAll('[data-display-index]')]
    const row = rows.find(r => r.textContent.includes(needle))
    const scroller = document.querySelector('.chat-container')
    const target = scroller.getBoundingClientRect().top + scroller.clientHeight * f
    scroller.scrollTop += row.getBoundingClientRect().top - target
  }, [marker, frac])
  await page.waitForTimeout(500)
}

/** Scroll so the marked prompt's row TOP sits `px` below the pinned card's fold
 *  line (negative = above it): a small negative value puts a prompt's top just
 *  past the fold with its bubble, and the strip beneath it, still on screen. */
async function placeRowTopFromFold(page, marker, px) {
  await page.evaluate(([needle, dy]) => {
    const rows = [...document.querySelectorAll('[data-display-index]')]
    const row = rows.find(r => r.textContent.includes(needle))
    const scroller = document.querySelector('.chat-container')
    // The card sits ROW_PAD_Y (4px) under the fold; with no card mounted fall
    // back to the scroller's own top edge, which is where a frameless host folds.
    const card = document.querySelector('[data-testid="pinned-prompt"]')
    const fold = card ? card.getBoundingClientRect().top - 4 : scroller.getBoundingClientRect().top
    scroller.scrollTop += row.getBoundingClientRect().top - (fold + dy)
  }, [marker, px])
  await page.waitForTimeout(500)
}

/** Scroll so the marked prompt's bubble BOTTOM sits `px` below the fold line —
 *  small values leave the card at its resting clamp with this prompt still the
 *  pinned one (the next prompt has not reached the fold yet). */
async function placeBubbleBottomFromFold(page, marker, px) {
  await page.evaluate(([needle, dy]) => {
    const rows = [...document.querySelectorAll('[data-display-index]')]
    const row = rows.find(r => r.textContent.includes(needle))
    const bubble = row.querySelector('.message-bubble')
    const scroller = document.querySelector('.chat-container')
    const card = document.querySelector('[data-testid="pinned-prompt"]')
    const fold = card ? card.getBoundingClientRect().top - 4 : scroller.getBoundingClientRect().top
    scroller.scrollTop += bubble.getBoundingClientRect().bottom - (fold + dy)
  }, [marker, px])
  await page.waitForTimeout(500)
}

const LONG = 'Step 68:'
const MEDIUM = 'Constraint C:'
const STEER = 'skip the replica steps entirely'

/** Scroll the transcript by `total` px in small steps, so a recording shows the
 *  hand-off, the fold and the strip's appearance as continuous motion. */
async function scrollBy(page, total, step = 14, pauseMs = 28) {
  const dir = Math.sign(total)
  let left = Math.abs(total)
  while (left > 0) {
    const d = Math.min(step, left) * dir
    await page.evaluate((dy) => { document.querySelector('.chat-container').scrollTop += dy }, d)
    await page.waitForTimeout(pauseMs)
    left -= Math.abs(d)
  }
}

/**
 * Recording: the states a still cannot carry. One long prompt scrolled from
 * unpinned, its row staying on screen past the fold, to the one-line hand-off
 * and the card's rest (the strip shows under the card, then re-hides once it has
 * slid under), then back up; then the five-line prompt hovered unpinned (pencil
 * present), scrolled into its stand-in state (pencil gone, the rest of the strip
 * in place) and back. Both directions, dark theme, at the viewport size so the
 * seam stays legible.
 */
async function record() {
  const { mkdirSync: mk } = await import('node:fs')
  const videoDir = `${OUT}/video-${process.pid}`
  mk(videoDir, { recursive: true })
  const h = await openTranscriptHarness({
    slot: SLOT, project: PROJECT, slots, detail,
    viewport: { width: 1280, height: 860 }, deviceScaleFactor: 1,
    recordVideo: { dir: videoDir, size: { width: 1280, height: 860 } },
    dist: DIST,
  })
  await h.load('dark', { selector: 'textarea[data-composer-input]', settle: 1200 })
  // Start with the long prompt's top 260px under the fold: unpinned, its bubble
  // filling the viewport, its bottom (and strip) below the screen.
  await placeRowTopFromFold(h.page, LONG, 260)
  await placeRowTopFromFold(h.page, LONG, 260)
  await h.page.waitForTimeout(1200)
  const g0 = await inspect(h.page, LONG)
  // Down: the row's top crosses the fold with no card yet, the one-line card
  // takes over as the bubble's last line reaches it (strip visible beneath the
  // card), then the strip slides under and hides again. Total travel: row top
  // from fold+260 to the point where the bubble bottom sits 40px under the fold.
  const travel = Math.round(260 + g0.bubble.height + ROW_PAD - 40)
  await scrollBy(h.page, travel)
  await h.page.waitForTimeout(1400)
  const rest = await inspect(h.page, LONG)
  console.log(`record: at rest marker=${JSON.stringify(rest.rowStandinValue)} copy.visibility=${rest.copy?.visibility} card.h=${Math.round(rest.card?.height ?? 0)}`)
  // Back up, the same way.
  await scrollBy(h.page, -travel)
  await h.page.waitForTimeout(1200)
  // The Edit pencil: unpinned and hovered, then pinned, then back.
  await placeRowTopFromFold(h.page, MEDIUM, 200)
  await placeRowTopFromFold(h.page, MEDIUM, 200)
  await h.page.locator('[data-display-index]').filter({ hasText: MEDIUM }).locator('.message-bubble').hover()
  await h.page.waitForTimeout(1400)
  await scrollBy(h.page, 240)
  await h.page.waitForTimeout(1400)
  const pinnedMedium = await inspect(h.page, MEDIUM)
  console.log(`record: medium pinned marker=${JSON.stringify(pinnedMedium.rowStandinValue)} edit.display=${pinnedMedium.edit?.display}`)
  await scrollBy(h.page, -240)
  await h.page.locator('[data-display-index]').filter({ hasText: MEDIUM }).locator('.message-bubble').hover()
  await h.page.waitForTimeout(1400)
  const path = await h.close()
  const { renameSync, rmSync } = await import('node:fs')
  const out = `${OUT}/pin-handoff-both-directions.webm`
  renameSync(path, out)
  rmSync(videoDir, { recursive: true, force: true })
  console.log('wrote', out)
}
const ROW_PAD = 4

async function main() {
  const h = await openTranscriptHarness({
    slot: SLOT, project: PROJECT, slots, detail,
    viewport: { width: 1280, height: 860 },
    dist: DIST,
  })
  const shot = async (name, clip) => {
    const path = `${OUT}/${name}.png`
    await h.page.screenshot({ path, ...(clip ? { clip } : {}) })
    console.log('wrote', path)
  }
  const rowLocator = marker => h.page.locator('[data-display-index]').filter({ hasText: marker })

  for (const theme of ['dark', 'light']) {
    await h.load(theme, { selector: 'textarea[data-composer-input]', settle: 1200 })
    // The transcript boots at its bottom. Bring the long prompt's bottom to
    // mid-viewport: its top is then far above the fold, with half a viewport of
    // bubble still above the reply — far more than the one line the card shows,
    // so the row stays on screen as itself and no card stands in for it.
    await placeBubbleBottom(h.page, LONG, 0.5)
    await placeBubbleBottom(h.page, LONG, 0.5) // second pass: heights re-measured after the first
    let g = await inspect(h.page, LONG)
    if (g.error) { assert(g.error, false); break }
    assert(`${theme}: bubble bottom is on screen (${Math.round(g.bubble?.bottom ?? -1)}px of ${g.viewport.h})`,
      !!g.bubble && g.bubble.bottom > 0 && g.bubble.bottom < g.viewport.h)
    assert(`${theme}: the action strip renders inside the row, with a pin control`, !!g.strip && !!g.copy && !!g.pin)
    if (EXPECTED === 'after') {
      // The strip is the row's own hover-revealed one here, so rest the pointer on
      // the visible part of the bubble (a locator hover would scroll the row into
      // view and undo the placement) and wait out the reveal's delay + transition.
      await h.page.mouse.move((g.bubble.left + g.bubble.right) / 2, g.bubble.bottom - 30)
      await h.page.waitForTimeout(900)
      g = await inspect(h.page, LONG)
      console.log(`${theme}: ${JSON.stringify(g)}`)
      assert(`${theme}: no pinned card while the tall bubble is mostly on screen (card: ${JSON.stringify(g.card?.text ?? null)})`, g.card == null)
      assert(`${theme}: long prompt row is on screen as itself, not the stand-in`, g.rowHidden === false && g.rowStandin === false)
      assert(`${theme}: the row's own strip is visible on hover (computed ${g.strip?.visibility}, opacity ${g.strip?.opacity})`,
        g.strip?.visibility === 'visible' && g.strip?.opacity === '1')
      assert(`${theme}: pin control is visible (computed ${g.pin?.visibility})`, g.pin?.visibility === 'visible')
      assert(`${theme}: Edit is present while the row is not standing in (computed display ${g.edit?.display})`, !!g.edit && g.edit.display !== 'none')
      assert(`${theme}: Copy is the element under its own centre (hit-testable)`, g.copy?.hit === true)
    } else {
      console.log(`${theme}: ${JSON.stringify(g)}`)
      assert(`${theme}: [folding build] row is already the hidden stand-in`, g.rowHidden === true)
      assert(`${theme}: [folding build] a card taller than one line stands in (${Math.round(g.card?.height ?? 0)}px)`, !!g.card && g.card.height > 80)
    }
    await shot(`${EXPECTED}-01-tall-prompt-on-screen-${theme}`)
    // Zoom on the seam: the bubble's bottom edge and the strip beneath it.
    const seamTop = Math.max(0, Math.round((g.bubble?.bottom ?? 300) - 220))
    await shot(`${EXPECTED}-02-strip-under-bubble-${theme}`, { x: 0, y: seamTop, width: g.viewport.w, height: 320 })
    if (EXPECTED === 'after' && theme === 'dark') {
      // The click lands on the row's own button; there is no card overlay to
      // intercept it. Either outcome label proves the click reached it; the
      // clipboard grant is what lets the successful one show.
      await h.page.context().grantPermissions(['clipboard-read', 'clipboard-write'], { origin: h.base })
      await rowLocator(LONG).locator('button[title="Copy"]').click({ timeout: 5000 })
      await h.page.waitForTimeout(250)
      const after = await inspect(h.page, LONG)
      assert(`dark: Copy click landed (label now "${after.copy?.label}")`, /copied|copy failed/i.test(after.copy?.label || ''))
      await shot(`${EXPECTED}-03-copy-clicked-${theme}`, { x: 0, y: seamTop, width: g.viewport.w, height: 320 })
      await h.page.mouse.move(5, 5)
      // One line left: scroll on until the long prompt's bubble bottom sits 40px
      // under the fold. Less than the resting line of the bubble is above the
      // reply now, so the one-line card takes over and the row hides — but the
      // 28px strip (4px under the bubble) still pokes out below the card's
      // bottom, so it must stay shown and uncovered — hiding it here is the
      // abrupt vanish and blank band the occlusion rule exists to remove.
      await placeBubbleBottomFromFold(h.page, LONG, 40)
      await placeBubbleBottomFromFold(h.page, LONG, 40)
      const clamp = await inspect(h.page, LONG)
      console.log(`dark one line left, strip uncovered: ${JSON.stringify({ rowHidden: clamp.rowHidden, marker: clamp.rowStandinValue, copy: clamp.copy, card: clamp.card, strip: clamp.strip })}`)
      assert('dark: long prompt row is the pinned stand-in (hidden by visibility)', clamp.rowHidden === true)
      assert(`dark: the one-line card is mounted at its resting height (${Math.round(clamp.card?.height ?? 0)}px)`, !!clamp.card && clamp.card.height < 80)
      assert(`dark: strip still uncovered keeps the marker (value ${JSON.stringify(clamp.rowStandinValue)})`, clamp.rowStandinValue === 'folding')
      assert(`dark: strip still uncovered stays visible (computed ${clamp.copy?.visibility})`, clamp.copy?.visibility === 'visible')
      assert(`dark: card bottom (${Math.round(clamp.card?.bottom ?? 0)}) does not cover the strip bottom (${Math.round(clamp.strip?.bottom ?? 0)})`,
        !!clamp.card && !!clamp.strip && clamp.strip.bottom > clamp.card.bottom + 0.5)
      assert(`dark: Edit is dropped while standing in (computed display ${clamp.edit?.display})`, clamp.edit?.display === 'none')
      assert('dark: Copy under the card is hit-testable', clamp.copy?.hit === true)
      await shot(`${EXPECTED}-08-one-line-card-strip-uncovered-${theme}`, { x: 0, y: Math.max(0, Math.round((clamp.card?.top ?? 60) - 30)), width: clamp.viewport.w, height: 200 })
      // Fully at rest: the strip's bottom has passed the card's resting bottom.
      // A visible strip there is one nobody can see but Tab still stops on, so it
      // must go back to hidden.
      await placeBubbleBottomFromFold(h.page, LONG, 10)
      await placeBubbleBottomFromFold(h.page, LONG, 10)
      const rest = await inspect(h.page, LONG)
      console.log(`dark at rest: ${JSON.stringify({ rowHidden: rest.rowHidden, marker: rest.rowStandinValue, copy: rest.copy, card: rest.card })}`)
      assert('dark: long prompt row is still the stand-in at rest', rest.rowHidden === true)
      assert(`dark: marker is bare at rest (value ${JSON.stringify(rest.rowStandinValue)})`, rest.rowStandinValue === '')
      assert(`dark: strip is hidden again at rest (computed ${rest.copy?.visibility})`, rest.copy?.visibility === 'hidden')
      assert(`dark: card is at its resting clamp (${Math.round(rest.card?.height ?? 0)}px)`, !!rest.card && rest.card.height < 80)
    }

    if (EXPECTED === 'after' && theme === 'dark') {
      // The Edit pencil pair. Unpinned first: the five-line prompt whole on
      // screen, hovered so its hover-revealed strip shows — pencil included.
      await placeRowTop(h.page, MEDIUM, 0.3)
      await rowLocator(MEDIUM).locator('.message-bubble').hover()
      await h.page.waitForTimeout(700)
      const un = await inspect(h.page, MEDIUM)
      console.log(`dark unpinned medium: ${JSON.stringify({ rowHidden: un.rowHidden, edit: un.edit, pin: un.pin, row: un.row })}`)
      assert('dark: unpinned prompt row is not hidden', un.rowHidden === false && un.rowStandin === false)
      assert(`dark: unpinned strip shows Edit (computed display ${un.edit?.display})`, !!un.edit && un.edit.display !== 'none')
      await shot(`${EXPECTED}-04-unpinned-strip-with-edit-${theme}`, { x: 0, y: Math.max(0, Math.round(un.row.top - 40)), width: un.viewport.w, height: Math.min(un.viewport.h, Math.round(un.row.height + 80)) })
      // Then pinned: its bubble's bottom 40px under the fold, so only its last
      // line is left above the reply and the one-line card stands in for it.
      await placeBubbleBottomFromFold(h.page, MEDIUM, 40)
      await placeBubbleBottomFromFold(h.page, MEDIUM, 40)
      const pinned = await inspect(h.page, MEDIUM)
      console.log(`dark pinned medium: ${JSON.stringify({ rowHidden: pinned.rowHidden, edit: pinned.edit, pin: pinned.pin, card: pinned.card, strip: pinned.strip })}`)
      assert('dark: medium prompt row is the pinned stand-in', pinned.rowHidden && pinned.rowStandin)
      assert(`dark: pinned strip drops Edit (computed display ${pinned.edit?.display})`, pinned.edit?.display === 'none')
      assert(`dark: pinned strip keeps Copy visible (computed ${pinned.copy?.visibility})`, pinned.copy?.visibility === 'visible')
      const top = Math.max(0, Math.round((pinned.card?.top ?? 60) - 30))
      await shot(`${EXPECTED}-05-pinned-strip-without-edit-${theme}`, { x: 0, y: top, width: pinned.viewport.w, height: Math.min(pinned.viewport.h - top, Math.round((pinned.strip?.bottom ?? 300) - top + 60)) })

      // A STEER: badge above an accent bubble, measured through the same
      // `.message-bubble` hook. With its row top just past the fold the whole
      // accent bubble is still above the reply, so no card stands in yet; once
      // only its last line remains the one-line card takes over, with the strip
      // right beneath.
      await placeRowTopFromFold(h.page, STEER, -2)
      await placeRowTopFromFold(h.page, STEER, -2)
      const steerTopPast = await inspect(h.page, STEER)
      console.log(`dark steer top past the fold: ${JSON.stringify({ rowHidden: steerTopPast.rowHidden, marker: steerTopPast.rowStandinValue, bubble: steerTopPast.bubble, card: steerTopPast.card })}`)
      assert(`dark: steer with its bubble still on screen shows no card (card: ${JSON.stringify(steerTopPast.card?.text ?? null)})`, steerTopPast.card == null)
      assert('dark: steer row is on screen as itself, not the stand-in', steerTopPast.rowHidden === false && steerTopPast.rowStandin === false)
      await placeBubbleBottomFromFold(h.page, STEER, 40)
      await placeBubbleBottomFromFold(h.page, STEER, 40)
      const steer = await inspect(h.page, STEER)
      console.log(`dark pinned steer: ${JSON.stringify({ rowHidden: steer.rowHidden, marker: steer.rowStandinValue, bubble: steer.bubble, card: steer.card, strip: steer.strip })}`)
      assert('dark: steer row is the pinned stand-in', steer.rowHidden && steer.rowStandinValue === 'folding')
      assert(`dark: steer card is one line (${Math.round(steer.card?.height ?? 0)}px)`, !!steer.card && steer.card.height < 80)
      assert(`dark: steer strip visible under the card (computed ${steer.strip?.visibility})`, steer.strip?.visibility === 'visible')
      assert(`dark: steer strip bottom (${Math.round(steer.strip?.bottom ?? 0)}) is below the card bottom (${Math.round(steer.card?.bottom ?? 0)})`,
        !!steer.card && !!steer.strip && steer.strip.bottom > steer.card.bottom + 0.5)
      const steerTop = Math.max(0, Math.round((steer.card?.top ?? 60) - 30))
      await shot(`${EXPECTED}-06-pinned-steer-one-line-${theme}`, { x: 0, y: steerTop, width: steer.viewport.w, height: Math.min(steer.viewport.h - steerTop, Math.round((steer.strip?.bottom ?? 300) - steerTop + 60)) })

      // An editing row never pins: open Edit on the five-line prompt while it is
      // unpinned, then scroll its top past the fold — no card, editor still shown.
      await placeRowTopFromFold(h.page, MEDIUM, 200)
      await rowLocator(MEDIUM).locator('.message-bubble').hover()
      await h.page.waitForTimeout(400)
      await rowLocator(MEDIUM).locator('[data-message-edit]').click({ timeout: 5000 })
      await h.page.waitForTimeout(400)
      await placeRowTopFromFold(h.page, MEDIUM, -60)
      await placeRowTopFromFold(h.page, MEDIUM, -60)
      const editing = await h.page.evaluate((needle) => {
        const rows = [...document.querySelectorAll('[data-display-index]')]
        const row = rows.find(r => r.textContent.includes(needle))
        const ta = row?.querySelector('textarea')
        const send = row ? [...row.querySelectorAll('button')].find(b => /send/i.test(b.textContent || '')) : null
        const card = document.querySelector('[data-testid="pinned-prompt"]')
        const rb = row?.getBoundingClientRect()
        return {
          found: !!row, editing: !!row?.querySelector('[data-message-editing]'),
          rowHidden: row?.style.visibility === 'hidden', marker: row?.getAttribute('data-pinned-standin'),
          rowTop: rb?.top, rowBottom: rb?.bottom,
          textarea: ta ? getComputedStyle(ta).visibility : null,
          send: send ? getComputedStyle(send).visibility : null,
          card: card ? (card.textContent || '').slice(0, 30) : null,
          viewportH: innerHeight,
        }
      }, MEDIUM)
      console.log(`dark editing row past the fold: ${JSON.stringify(editing)}`)
      assert('dark: the prompt is in edit mode', editing.found && editing.editing)
      assert(`dark: editing row top (${Math.round(editing.rowTop ?? 0)}) is above the fold`, (editing.rowTop ?? 999) < 90)
      assert('dark: editing row is not hidden and carries no stand-in marker', editing.rowHidden === false && editing.marker == null)
      assert(`dark: no pinned card stands in for it (card: ${JSON.stringify(editing.card)})`, editing.card == null)
      assert(`dark: editor textarea and Send are visible (${editing.textarea}, ${editing.send})`, editing.textarea === 'visible' && editing.send === 'visible')
      await shot(`${EXPECTED}-07-editing-row-past-fold-unpinned-${theme}`, { x: 0, y: 0, width: 1280, height: Math.min(editing.viewportH, Math.round((editing.rowBottom ?? 400) + 40)) })
      // Leave edit mode so the light pass starts clean.
      await h.page.keyboard.press('Escape')
    }
  }

  await h.close()
  console.log(failures === 0 ? 'ALL ASSERTIONS PASSED' : `${failures} ASSERTION(S) FAILED`)
  process.exit(failures === 0 ? 0 : 1)
}

const RECORD = argv.includes('--record')
;(RECORD ? record() : main()).catch(err => { console.error(err); process.exit(1) })
