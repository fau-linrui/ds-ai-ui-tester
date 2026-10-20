#!/usr/bin/env node
/**
 * 零依赖静态服务器，给 demo 页和本地调试用（内网没有 npm 也能跑）。
 * 用法: node tools/serve.mjs [--root .] [--port 3000]
 */
import { createServer } from 'node:http'
import { readFile, stat } from 'node:fs/promises'
import { extname, join, normalize, resolve } from 'node:path'

const argv = process.argv.slice(2)
const arg = (name, fallback) => {
  const i = argv.indexOf(name)
  return i === -1 ? fallback : argv[i + 1]
}

const root = resolve(arg('--root', '.'))
const port = Number(arg('--port', 3000))

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.woff2': 'font/woff2',
}

createServer(async (req, res) => {
  try {
    const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname)
    let file = join(root, normalize(pathname).replace(/^(\.\.[/\\])+/, ''))
    if (!file.startsWith(root)) {
      res.writeHead(403).end('forbidden')
      return
    }
    const info = await stat(file).catch(() => null)
    if (info?.isDirectory()) file = join(file, 'index.html')
    const body = await readFile(file)
    res.writeHead(200, { 'content-type': MIME[extname(file).toLowerCase()] ?? 'application/octet-stream' })
    res.end(body)
  } catch {
    res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' }).end('404')
  }
}).listen(port, '127.0.0.1', () => {
  console.log(`[serve] http://127.0.0.1:${port}  root=${root}`)
})
