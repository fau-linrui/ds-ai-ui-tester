#!/usr/bin/env node
/**
 * 稳定 key 的页面快照 + 增量 diff。
 *
 * 为什么不直接用 MCP：MCP 的返回是给模型看的，格式会变、ref 是运行时序号、
 * 你没法把它当断言数据源，也没法在 CI 里跑。这个脚本产出的是你自己的契约。
 *
 * 用法：
 *   node tools/snapshot.mjs --url http://localhost:3000/orders --out .snap/orders.json
 *   node tools/snapshot.mjs --url ... --wait "[data-testid=order-list]" --diff .snap/orders.prev.json
 */
import { chromium } from 'playwright'
import { readFile, writeFile, mkdir } from 'node:fs/promises'
import { dirname } from 'node:path'

const ROLE_BY_TAG = {
  a: 'link',
  button: 'button',
  select: 'combobox',
  textarea: 'textbox',
  summary: 'button',
  h1: 'heading',
  h2: 'heading',
  h3: 'heading',
  h4: 'heading',
  nav: 'navigation',
  form: 'form',
  dialog: 'dialog',
  table: 'table',
  ul: 'list',
  li: 'listitem',
  img: 'img',
}

function parseArgs(argv) {
  const out = { headed: false }
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a === '--url') out.url = argv[++i]
    else if (a === '--out') out.out = argv[++i]
    else if (a === '--diff') out.diff = argv[++i]
    else if (a === '--wait') out.wait = argv[++i]
    else if (a === '--timeout') out.timeout = Number(argv[++i])
    else if (a === '--state') out.state = argv[++i]
    else if (a === '--json') out.json = true
    else if (a === '--headed') out.headed = true
  }
  return out
}

/** 在页面里执行：只收集有语义的节点，生成稳定 key。 */
const collectInPage = (roleMap) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim().slice(0, 80)
  const esc = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : String(s))

  const nameOf = (el) => {
    const aria = el.getAttribute('aria-label')
    if (aria) return norm(aria)
    const lb = el.getAttribute('aria-labelledby')
    if (lb) {
      const t = lb
        .split(/\s+/)
        .map((id) => document.getElementById(id))
        .filter(Boolean)
        .map((n) => n.textContent)
        .join(' ')
      if (norm(t)) return norm(t)
    }
    if (el.id) {
      const l = document.querySelector(`label[for="${esc(el.id)}"]`)
      if (l) return norm(l.textContent)
    }
    const ph = el.getAttribute('placeholder')
    if (ph) return norm(ph)
    if (el.tagName === 'INPUT' && (el.type === 'submit' || el.type === 'button')) return norm(el.value)
    return norm(el.textContent)
  }

  const roleOf = (el) => {
    const explicit = (el.getAttribute('role') || '').trim()
    if (explicit) return explicit.split(/\s+/)[0]
    const tag = el.tagName.toLowerCase()
    if (tag === 'input') {
      const t = (el.type || 'text').toLowerCase()
      if (t === 'checkbox' || t === 'radio') return t
      if (t === 'submit' || t === 'button' || t === 'reset') return 'button'
      if (t === 'search') return 'searchbox'
      return 'textbox'
    }
    return roleMap[tag] || tag
  }

  const pathOf = (el) => {
    const parts = []
    let n = el
    while (n && n.nodeType === 1 && parts.length < 5) {
      const tag = n.tagName.toLowerCase()
      let idx = 1
      if (n.parentElement) {
        const same = Array.from(n.parentElement.children).filter((c) => c.tagName === n.tagName)
        if (same.length > 1) idx = same.indexOf(n) + 1
      }
      parts.unshift(idx > 1 ? `${tag}:nth-of-type(${idx})` : tag)
      n = n.parentElement
    }
    return parts.join('>')
  }

  const SEL =
    'a,button,input,select,textarea,summary,[role],[data-testid],[aria-label],[aria-live],h1,h2,h3,h4,nav,form,dialog'
  const counter = new Map()
  const nodes = []

  for (const el of document.querySelectorAll(SEL)) {
    if (el.tagName === 'INPUT' && el.type === 'hidden') continue
    if (typeof el.checkVisibility === 'function' && !el.checkVisibility()) continue

    const role = roleOf(el)
    const name = nameOf(el)
    const testid = el.getAttribute('data-testid') || ''
    if (!name && !testid) continue

    // 稳定 key 的优先级：testid > role+name。绝不用 DOM 序号做 key。
    const base = testid ? `tid:${testid}` : `${role}:${name}`
    const n = (counter.get(base) || 0) + 1
    counter.set(base, n)

    nodes.push({
      key: n === 1 ? base : `${base}#${n}`,
      role,
      name,
      ...(testid ? { testid } : {}),
      ...(typeof el.value === 'string' && el.value ? { value: el.value } : {}),
      ...(el.disabled || el.getAttribute('aria-disabled') === 'true' ? { disabled: true } : {}),
      ...(typeof el.checked === 'boolean' ? { checked: el.checked } : {}),
      path: pathOf(el),
    })
  }

  return { title: document.title, nodes }
}

function pick(obj, keys) {
  const out = {}
  for (const k of keys) if (k in obj) out[k] = obj[k]
  return out
}

function diffSnapshots(prev, next) {
  const p = new Map(prev.nodes.map((n) => [n.key, n]))
  const n = new Map(next.nodes.map((x) => [x.key, x]))
  const added = []
  const removed = []
  const changed = []

  for (const [k, v] of n) if (!p.has(k)) added.push(v)
  for (const [k, v] of p) if (!n.has(k)) removed.push(v)
  for (const [k, v] of n) {
    if (!p.has(k)) continue
    const before = p.get(k)
    const fields = ['name', 'value', 'disabled', 'checked'].filter(
      (f) => JSON.stringify(before[f]) !== JSON.stringify(v[f]),
    )
    if (fields.length) changed.push({ key: k, fields, before: pick(before, fields), after: pick(v, fields) })
  }
  return { added, removed, changed }
}

const args = parseArgs(process.argv.slice(2))
if (!args.url) {
  console.error('用法: node tools/snapshot.mjs --url <URL> [--out 文件] [--diff 上次快照] [--wait 选择器] [--headed]')
  process.exit(1)
}

const browser = await chromium.launch({ headless: !args.headed })
const context = await browser.newContext(args.state ? { storageState: args.state } : {})
const page = await context.newPage()
await page.goto(args.url, { waitUntil: 'domcontentloaded' })
if (args.wait) {
  await page.waitForSelector(args.wait, { state: 'visible', timeout: args.timeout ?? 15000 })
} else {
  await page.waitForLoadState('networkidle').catch(() => {})
}

const data = await page.evaluate(collectInPage, ROLE_BY_TAG)
data.url = page.url()
data.takenAt = new Date().toISOString()
await browser.close()

let diff = null
if (args.diff) {
  const prev = JSON.parse(await readFile(args.diff, 'utf8'))
  diff = diffSnapshots(prev, data)
}

const out = args.out || `.snap/${Date.now()}.json`
await mkdir(dirname(out), { recursive: true })
await writeFile(out, JSON.stringify({ ...data, ...(diff ? { diff } : {}) }, null, 2), 'utf8')

console.log(`[snapshot] ${data.nodes.length} 个节点 -> ${out}`)
if (diff) {
  console.log(`[diff] +${diff.added.length} -${diff.removed.length} ~${diff.changed.length}`)
  for (const a of diff.added) console.log(`  + ${a.key}`)
  for (const r of diff.removed) console.log(`  - ${r.key}`)
  for (const c of diff.changed) console.log(`  ~ ${c.key} (${c.fields.join(',')})`)
}

if (args.json) {
  console.log(
    JSON.stringify({
      ok: true,
      url: data.url,
      nodes: data.nodes.length,
      out,
      diff: diff
        ? {
            added: diff.added.map((x) => x.key),
            removed: diff.removed.map((x) => x.key),
            changed: diff.changed.map((x) => x.key),
          }
        : null,
    }),
  )
}
