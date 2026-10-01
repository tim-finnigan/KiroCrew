/**
 * The one frontend copy of the backend's `DENY_REASON_PREFIX` (`security.py`).
 *
 * Held as a REGEX, not a string constant, and exported so `RecoveryCard` builds
 * its own end-anchored matcher from `.source` rather than declaring the literal a
 * second time. This is a WIRE VALUE matched byte-for-byte against a Python
 * constant, never copy: as a string literal inside an ALL-CAPS module constant
 * the i18n gate reads it as untranslated UI text and asks for a catalog key, and
 * translating it would silently stop every deny reason from being found.
 *
 * `test_recovery_card_prefixes.py` asserts the Python constant still appears
 * here, so the two languages cannot drift apart unnoticed.
 */
export const DENY_REASON_MARKER = /Blocked by security policy:/g

/**
 * Pull the human-readable deny reason out of a blocked tool row's content.
 *
 * When a security-policy rule or a PreToolUse hook blocks a tool call, the
 * gateway appends a second tool message sharing the pill's `tool_call_id`:
 *
 *     🚫 <title> — Blocked by security policy: <pattern>
 *     <why the pattern fired, when the match was structural>
 *
 * The Output panel used to discard that content and show a fixed
 * "blocked by security policy" line, because the row could arrive carrying only
 * a bare title: a later `tool_call_update` title refinement rewrote the row as
 * `"<icon> <title>"`, deleting the reason. With that rewrite fixed backend-side
 * the content is dependable, so the reason can be shown instead of a placeholder
 * that tells the user nothing about WHICH rule fired or why.
 *
 * Reads the LAST marker, never the first. `<title>` is model-authored —
 * `_select_tool_title` prefers the tool call's own `description` field — so a
 * model that writes "Blocked by security policy: …" into its description would,
 * under first-match extraction, get its own text rendered to the user AS the
 * security reason. The gateway always appends the real reason after the title,
 * so the final occurrence is the one the host wrote.
 *
 * Returns the marker AND everything after it, so a caller that needs the wire
 * form keeps it; `extractDenyDetail` is the variant for display beside a
 * localized lead. A row without the marker yields "" and the caller keeps its
 * placeholder.
 */
export function extractDenyReason(rowContent: string): string {
  if (!rowContent) return ''
  let last: RegExpMatchArray | null = null
  for (const m of rowContent.matchAll(DENY_REASON_MARKER)) last = m
  if (!last || last.index === undefined) return ''
  const reason = rowContent.slice(last.index).trim()
  // A marker with nothing after it is a placeholder, not a reason — let the
  // caller's own localized placeholder win rather than rendering a bare colon.
  return reason === last[0] ? '' : reason
}

/**
 * The deny reason WITHOUT the English marker — the rule that fired, plus the
 * structural note when there is one.
 *
 * Exists so the Output panel can lead with its own LOCALIZED sentence and follow
 * with the detail, instead of replacing a translated sentence with untranslated
 * English. The marker earns its place on the wire, where three parsers key on it;
 * it earns nothing in front of a reader who is already being told, in their own
 * language, that a safety policy blocked the call.
 */
export function extractDenyDetail(rowContent: string): string {
  const reason = extractDenyReason(rowContent)
  if (!reason) return ''
  return reason.replace(DENY_REASON_MARKER, '').trimStart()
}

/**
 * The gateway writes a blocked row as `🚫 <title> — <reason>`; this is the
 * separator plus the lead word every host-authored refusal that is NOT a
 * denied-command rule starts with (`hooks.py`: `GATE_CRASH_REASON`, the
 * sensitive-path and write-protected-config refusals, the deny-by-default
 * shell message). A WIRE VALUE like `DENY_REASON_MARKER`, held as a regex for
 * the same reason: translating it would stop the reason from being found.
 */
const HOST_NOTICE_MARKER = / — (Blocked: )/g

/**
 * The suffixes the gateway itself appends to a blocked row that carries NO host
 * reason (`chat_runner.py`: hook blocked, hook error, rejected, invalid). On such
 * a row any ` — Blocked: ` text can only have come from the model-authored
 * title, so it must never be shown as the host's reason.
 */
const NON_REASON_ROW_SUFFIX = / \((?:hook blocked|hook error|rejected|invalid: [\s\S]*)\)$/

/**
 * The host's own refusal sentence for a blocked row that carries NO
 * `DENY_REASON_MARKER` — the gate-crash refusal above all.
 *
 * `extractDenyDetail` yields "" for such a row, and the Output panel then falls
 * back to its localized "blocked by security policy" line alone. For the
 * gate-crash row that is exactly backwards: the reason says, in so many words,
 * that NO policy rule fired and the user did nothing, and the panel tells the
 * reader a policy rule fired while the sentence that disclaims it never shows.
 * This returns that sentence so the panel can render it instead of the lead.
 *
 * Reads the LAST separator-plus-lead-word, for the same reason
 * `extractDenyReason` reads the last marker: `<title>` is model-authored, and
 * the host always appends its reason after it. Yields "" for a marker row
 * (`extractDenyDetail` owns those) and for a row whose tail is not a host
 * sentence — a hook-blocked `🚫 <title> (hook blocked)` row keeps the
 * localized line alone, exactly as before.
 */
export function extractDenyNotice(rowContent: string): string {
  if (!rowContent || extractDenyReason(rowContent)) return ''
  if (NON_REASON_ROW_SUFFIX.test(rowContent.trimEnd())) return ''
  let last: RegExpMatchArray | null = null
  for (const m of rowContent.matchAll(HOST_NOTICE_MARKER)) last = m
  if (!last || last.index === undefined) return ''
  const notice = rowContent.slice(last.index + last[0].length - last[1].length).trim()
  // A lead word with nothing after it is a placeholder, not a reason.
  return notice === last[1].trim() ? '' : notice
}
