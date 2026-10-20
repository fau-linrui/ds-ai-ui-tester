#!/usr/bin/env python
"""稳定 key 的页面快照 + 增量 diff（Python 版）。

为什么不直接用 MCP：MCP 的返回是给模型看的，格式会变、ref 是运行时序号，
没法当断言数据源，也没法在 CI 里跑。这个脚本产出的是你自己的契约。

用法：
    python tools/snapshot.py --url http://localhost:3000/demo/login.html --out .snap/login.json
    python tools/snapshot.py --url ... --diff .snap/login.prev.json --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROLE_BY_TAG = {
    "a": "link",
    "button": "button",
    "select": "combobox",
    "textarea": "textbox",
    "summary": "button",
    "h1": "heading",
    "h2": "heading",
    "h3": "heading",
    "h4": "heading",
    "nav": "navigation",
    "form": "form",
    "dialog": "dialog",
    "table": "table",
    "ul": "list",
    "li": "listitem",
    "img": "img",
}

# 在页面里执行：只收集有语义的节点，生成稳定 key。
COLLECT_JS = """
(roleMap) => {
  const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim().slice(0, 80);
  const esc = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : String(s));

  const nameOf = (el) => {
    const aria = el.getAttribute('aria-label');
    if (aria) return norm(aria);
    const lb = el.getAttribute('aria-labelledby');
    if (lb) {
      const t = lb.split(/\\s+/).map((id) => document.getElementById(id))
        .filter(Boolean).map((n) => n.textContent).join(' ');
      if (norm(t)) return norm(t);
    }
    if (el.id) {
      const l = document.querySelector(`label[for="${esc(el.id)}"]`);
      if (l) return norm(l.textContent);
    }
    const ph = el.getAttribute('placeholder');
    if (ph) return norm(ph);
    if (el.tagName === 'INPUT' && (el.type === 'submit' || el.type === 'button')) return norm(el.value);
    return norm(el.textContent);
  };

  const roleOf = (el) => {
    const explicit = (el.getAttribute('role') || '').trim();
    if (explicit) return explicit.split(/\\s+/)[0];
    const tag = el.tagName.toLowerCase();
    if (tag === 'input') {
      const t = (el.type || 'text').toLowerCase();
      if (t === 'checkbox' || t === 'radio') return t;
      if (t === 'submit' || t === 'button' || t === 'reset') return 'button';
      if (t === 'search') return 'searchbox';
      return 'textbox';
    }
    return roleMap[tag] || tag;
  };

  const pathOf = (el) => {
    const parts = [];
    let n = el;
    while (n && n.nodeType === 1 && parts.length < 5) {
      const tag = n.tagName.toLowerCase();
      let idx = 1;
      if (n.parentElement) {
        const same = Array.from(n.parentElement.children).filter((c) => c.tagName === n.tagName);
        if (same.length > 1) idx = same.indexOf(n) + 1;
      }
      parts.unshift(idx > 1 ? `${tag}:nth-of-type(${idx})` : tag);
      n = n.parentElement;
    }
    return parts.join('>');
  };

  const SEL = 'a,button,input,select,textarea,summary,[role],[data-testid],[aria-label],[aria-live],h1,h2,h3,h4,nav,form,dialog';
  const counter = new Map();
  const nodes = [];

  for (const el of document.querySelectorAll(SEL)) {
    if (el.tagName === 'INPUT' && el.type === 'hidden') continue;
    if (typeof el.checkVisibility === 'function' && !el.checkVisibility()) continue;

    const role = roleOf(el);
    const name = nameOf(el);
    const testid = el.getAttribute('data-testid') || '';
    if (!name && !testid) continue;

    // 稳定 key 的优先级：testid > role+name。绝不用 DOM 序号做 key。
    const base = testid ? `tid:${testid}` : `${role}:${name}`;
    const n = (counter.get(base) || 0) + 1;
    counter.set(base, n);

    nodes.push({
      key: n === 1 ? base : `${base}#${n}`,
      role,
      name,
      ...(testid ? { testid } : {}),
      ...(typeof el.value === 'string' && el.value ? { value: el.value } : {}),
      ...(el.disabled || el.getAttribute('aria-disabled') === 'true' ? { disabled: true } : {}),
      ...(typeof el.checked === 'boolean' ? { checked: el.checked } : {}),
      path: pathOf(el),
    });
  }

  return { title: document.title, nodes };
}
"""


def diff_snapshots(prev: dict, nxt: dict) -> dict:
    old = {n["key"]: n for n in prev.get("nodes", [])}
    new = {n["key"]: n for n in nxt.get("nodes", [])}

    added = [v for k, v in new.items() if k not in old]
    removed = [v for k, v in old.items() if k not in new]
    changed = []
    for key, value in new.items():
        if key not in old:
            continue
        before = old[key]
        fields = [f for f in ("name", "value", "disabled", "checked") if before.get(f) != value.get(f)]
        if fields:
            changed.append(
                {
                    "key": key,
                    "fields": fields,
                    "before": {f: before.get(f) for f in fields},
                    "after": {f: value.get(f) for f in fields},
                }
            )
    return {"added": added, "removed": removed, "changed": changed}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", default="")
    parser.add_argument("--diff", default="")
    parser.add_argument("--wait", default="")
    parser.add_argument("--timeout", type=int, default=15000)
    parser.add_argument("--state", default="", help="storage_state 文件（登录态）")
    parser.add_argument("--channel", default="", help="chrome / msedge，复用系统浏览器")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--json", action="store_true", help="输出机读摘要")
    args = parser.parse_args()

    launch_kwargs = {"headless": not args.headed}
    if args.channel:
        launch_kwargs["channel"] = args.channel

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(**({"storage_state": args.state} if args.state else {}))
        page = context.new_page()
        page.goto(args.url, wait_until="domcontentloaded")
        if args.wait:
            page.wait_for_selector(args.wait, state="visible", timeout=args.timeout)
        else:
            try:
                page.wait_for_load_state("networkidle", timeout=args.timeout)
            except Exception:
                pass

        data = page.evaluate(COLLECT_JS, ROLE_BY_TAG)
        data["url"] = page.url
        browser.close()

    from datetime import datetime, timezone

    data["takenAt"] = datetime.now(timezone.utc).isoformat()

    diff = None
    if args.diff:
        prev = json.loads(Path(args.diff).read_text(encoding="utf-8"))
        diff = diff_snapshots(prev, data)
        data["diff"] = diff

    out = Path(args.out) if args.out else Path(".snap") / f"{int(datetime.now().timestamp())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[snapshot] {len(data['nodes'])} 个节点 -> {out}")
    if diff:
        print(f"[diff] +{len(diff['added'])} -{len(diff['removed'])} ~{len(diff['changed'])}")
        for item in diff["added"]:
            print(f"  + {item['key']}")
        for item in diff["removed"]:
            print(f"  - {item['key']}")
        for item in diff["changed"]:
            print(f"  ~ {item['key']} ({','.join(item['fields'])})")

    if args.json:
        print(
            json.dumps(
                {
                    "ok": True,
                    "url": data["url"],
                    "nodes": len(data["nodes"]),
                    "out": str(out),
                    "diff": {
                        "added": [x["key"] for x in diff["added"]],
                        "removed": [x["key"] for x in diff["removed"]],
                        "changed": [x["key"] for x in diff["changed"]],
                    }
                    if diff
                    else None,
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
