#!/usr/bin/env node
/**
 * 环境自检。任何 agent 接手这个仓库的第一条命令。
 * 退出码: 0 = 就绪, 1 = 有阻塞项。
 *
 *   node tools/preflight.mjs
 *   node tools/preflight.mjs --json
 */
import { mkdir } from 'node:fs/promises'

const asJson = process.argv.includes('--json')
const checks = []
const add = (name, ok, detail) => checks.push({ name, ok, detail })

// 1. Node
const major = Number(process.versions.node.split('.')[0])
add('node>=18', major >= 18, process.version)

// 2. playwright 包
let pw = null
try {
  pw = await import('playwright')
  add('playwright 包', true, '已安装')
} catch {
  add('playwright 包', false, '未安装：npm i -D playwright')
}

// 3. 浏览器能不能真的拉起来（比检查文件存在更可靠）
if (pw) {
  try {
    const browser = await pw.chromium.launch({ headless: true })
    const version = browser.version()
    await browser.close()
    add('浏览器可启动', true, version)
  } catch (e) {
    const first = String(e.message).split('\n')[0].slice(0, 110)
    add('浏览器可启动', false, `${first} → 跑 npx playwright install chromium，或在 config 里用 channel: 'chrome'`)
  }
}

// 4. 目标站点可达
const base = process.env.BASE_URL
if (base) {
  try {
    const ctrl = new AbortController()
    const timer = setTimeout(() => ctrl.abort(), 3000)
    const res = await fetch(base, { signal: ctrl.signal, redirect: 'manual' })
    clearTimeout(timer)
    add('BASE_URL 可达', res.status < 500, `${base} -> HTTP ${res.status}`)
  } catch (e) {
    add('BASE_URL 可达', false, `${base} -> ${e.message}`)
  }
} else {
  add('BASE_URL 可达', false, '未设置环境变量 BASE_URL')
}

// 5. 工作区可写（agent 没有写权限的话，整个流程跑不起来）
try {
  await mkdir('.snap', { recursive: true })
  add('工作区可写', true, '.snap/')
} catch (e) {
  add('工作区可写', false, e.message)
}

const ok = checks.every((c) => c.ok)

if (asJson) {
  console.log(JSON.stringify({ ok, checks }, null, 2))
} else {
  for (const c of checks) {
    console.log(`${c.ok ? 'PASS' : 'FAIL'}  ${c.name.padEnd(14)} ${c.detail}`)
  }
  console.log(ok ? '\n环境就绪，下一步：node tools/snapshot.mjs --url $BASE_URL/...' : '\n有阻塞项，先修完再往下走')
}

process.exit(ok ? 0 : 1)
