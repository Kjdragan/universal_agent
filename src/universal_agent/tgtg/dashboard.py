"""
FastAPI dashboard for live TGTG deal monitoring.

Serves a simple real-time web UI at http://localhost:8765
with a REST API and Server-Sent Events (SSE) for live updates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from .config import DASHBOARD_HOST, DASHBOARD_PORT

log = logging.getLogger(__name__)

app = FastAPI(title="TGTG Sniper Dashboard")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Shared state updated by the monitor thread
_state: dict[str, Any] = {
    "items": {},       # item_id -> {"item": ..., "desire": "high"|"watch"|None}
    "orders": [],      # list of placed orders
    "events": [],      # recent log events (capped at 100)
    "started_at": datetime.now().isoformat(),
    "poll_count": 0,
}
_state_lock = threading.Lock()
_sse_queues: list[asyncio.Queue] = []


# ── State helpers (called from monitor thread) ────────────────────────────────

def push_item_update(item: dict, target=None) -> None:
    item_id = str(item.get("item", {}).get("item_id", ""))
    entry = {
        "item": item,
        "desire": target.desire if target else None,
        "max_price": target.max_price if target else None,
        "order_count": target.order_count if target else 1,
    }
    with _state_lock:
        _state["items"][item_id] = entry
        _state["poll_count"] += 1
    _broadcast({"type": "item_update", "item_id": item_id, "data": entry})


def push_order(order: dict, item: dict) -> None:
    record = {
        "order_id": order.get("id"),
        "store": item.get("store", {}).get("store_name", "?"),
        "state": order.get("state"),
        "created_at": datetime.now().isoformat(),
    }
    with _state_lock:
        _state["orders"].insert(0, record)
    _broadcast({"type": "order_created", "order": record})


def push_log(message: str, level: str = "info") -> None:
    entry = {"ts": datetime.now().isoformat(), "level": level, "msg": message}
    with _state_lock:
        _state["events"].insert(0, entry)
        if len(_state["events"]) > 100:
            _state["events"].pop()
    _broadcast({"type": "log", "entry": entry})


def _broadcast(data: dict) -> None:
    payload = json.dumps(data)
    for q in list(_sse_queues):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


# ── API routes ────────────────────────────────────────────────────────────────

@app.get("/api/state")
def get_state():
    with _state_lock:
        return dict(_state)


@app.get("/api/items")
def get_items():
    with _state_lock:
        return list(_state["items"].values())


@app.get("/api/orders")
def get_orders():
    with _state_lock:
        return _state["orders"]


@app.get("/api/events")
async def sse_events():
    """Server-Sent Events endpoint for live updates."""
    q: asyncio.Queue = asyncio.Queue(maxsize=50)
    _sse_queues.append(q)

    async def generator():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _sse_queues.remove(q)

    return StreamingResponse(generator(), media_type="text/event-stream")


@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(content=_DASHBOARD_HTML)


# ── HTML Dashboard ─────────────────────────────────────────────────────────────

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TGTG Sniper Dashboard</title>
<style>
  :root {
    --green: #22c55e; --red: #ef4444; --yellow: #eab308;
    --orange: #f97316; --fire: #ff6b2b;
    --bg: #0f172a; --card: #1e293b; --text: #e2e8f0; --muted: #64748b;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; padding: 1.5rem; }
  h1 { font-size: 1.4rem; margin-bottom: 1rem; color: var(--green); }
  h2 { font-size: 0.85rem; margin-bottom: 0.75rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.07em; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; }
  .card { background: var(--card); border-radius: 0.75rem; padding: 1.25rem; }

  /* Deal cards — base */
  .deal {
    border-left: 4px solid var(--muted);
    padding: 0.75rem 1rem;
    margin-bottom: 0.75rem;
    border-radius: 0 0.5rem 0.5rem 0;
    background: #0f172a;
    position: relative;
  }
  /* Watch — in stock */
  .deal.in-stock { border-color: var(--green); }
  /* High desire — always highlighted differently */
  .deal.desire-high { border-color: var(--fire); background: #1a0f00; }
  .deal.desire-high.in-stock { border-color: var(--fire); background: #1f1000; box-shadow: 0 0 12px rgba(255,107,43,0.25); }

  .desire-badge {
    display: inline-block; padding: 0.1rem 0.45rem;
    border-radius: 4px; font-size: 0.7rem; font-weight: 700;
    letter-spacing: 0.04em; margin-right: 0.35rem; vertical-align: middle;
  }
  .badge-fire  { background: #7c2d12; color: var(--fire); }
  .badge-watch { background: #1e293b; color: var(--muted); }

  .deal .store { font-weight: 600; font-size: 0.95rem; }
  .deal .meta { font-size: 0.8rem; color: var(--muted); margin-top: 0.3rem; display: flex; gap: 1rem; flex-wrap: wrap; }
  .deal .meta .price-cap { color: #94a3b8; }

  .badge { display: inline-block; padding: 0.15rem 0.5rem; border-radius: 9999px; font-size: 0.75rem; font-weight: 600; }
  .badge-green { background: #14532d; color: var(--green); }
  .badge-muted { background: #1e293b; color: var(--muted); }
  .badge-orange { background: #431407; color: var(--orange); }

  .log-entry { font-size: 0.78rem; padding: 0.3rem 0; border-bottom: 1px solid #1e293b; color: var(--muted); }
  .log-entry .ts { color: #334155; margin-right: 0.5rem; }
  .order-row { padding: 0.5rem 0; border-bottom: 1px solid #1e293b; font-size: 0.85rem; }

  .status { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1rem; font-size: 0.85rem; color: var(--muted); }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
  a { color: var(--green); text-decoration: none; }

  .legend { display: flex; gap: 1rem; margin-bottom: 1rem; font-size: 0.8rem; color: var(--muted); }
  .legend span { display: flex; align-items: center; gap: 0.3rem; }
  .dot-fire { width:10px;height:10px;border-radius:2px;background:var(--fire); }
  .dot-green { width:10px;height:10px;border-radius:2px;background:var(--green); }
  .dot-muted { width:10px;height:10px;border-radius:2px;background:var(--muted); }
</style>
</head>
<body>
<h1>🎯 TGTG Sniper Dashboard</h1>
<div class="status">
  <div class="dot" id="dot"></div>
  <span id="status-text">Connecting…</span>
  <span style="margin-left:auto;font-size:0.8rem;color:var(--muted)" id="poll-count">–</span>
</div>

<div class="legend">
  <span><div class="dot-fire"></div> High-desire (auto-buy)</span>
  <span><div class="dot-green"></div> Watch (notify only)</span>
  <span><div class="dot-muted"></div> Unregistered</span>
</div>

<div class="grid">
  <div>
    <div class="card">
      <h2>Live Deals</h2>
      <div id="deals-list">Loading…</div>
    </div>
  </div>
  <div>
    <div class="card" style="margin-bottom:1.5rem">
      <h2>Orders Placed</h2>
      <div id="orders-list">None yet.</div>
    </div>
    <div class="card">
      <h2>Event Log</h2>
      <div id="log-list" style="max-height:300px;overflow-y:auto">–</div>
    </div>
  </div>
</div>

<script>
const fmt = (entry) => {
  const item = entry.item ?? entry;  // support both wrapped and bare item dicts
  const desire = entry.desire ?? null;
  const maxPrice = entry.max_price ?? null;
  const orderCount = entry.order_count ?? 1;

  const id = item?.item?.item_id ?? '';
  const store = item?.store?.store_name ?? id;
  const avail = item?.items_available ?? 0;

  const pickup = (() => {
    try {
      const w = item.item.pickup_interval;
      const s = new Date(w.start), e = new Date(w.end);
      return s.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}) + '–' + e.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});
    } catch { return 'unknown'; }
  })();

  const price = (() => {
    try {
      const p = item.item.price_including_taxes;
      return (p.minor_units / 10 ** p.decimals).toFixed(2) + ' ' + p.code;
    } catch { return '?'; }
  })();

  const isHigh = desire === 'high';
  const cardCls = ['deal', isHigh ? 'desire-high' : '', avail > 0 ? 'in-stock' : ''].filter(Boolean).join(' ');

  const desireBadge = desire === 'high'
    ? '<span class="desire-badge badge-fire">🔥 AUTO-BUY</span>'
    : desire === 'watch'
      ? '<span class="desire-badge badge-watch">👁 WATCH</span>'
      : '';

  const stockBadge = avail > 0
    ? (isHigh
        ? `<span class="badge badge-orange">⚡ ${avail} bag(s) — buying</span>`
        : `<span class="badge badge-green">${avail} left</span>`)
    : `<span class="badge badge-muted">Sold out</span>`;

  const priceCap = maxPrice ? `<span class="price-cap">cap: ${maxPrice}</span>` : '';
  const countNote = isHigh && orderCount > 1 ? `<span>×${orderCount} bags</span>` : '';

  return `<div class="${cardCls}">
    <div class="store">${desireBadge}${store} ${stockBadge}</div>
    <div class="meta">
      <span>💰 ${price}</span>
      <span>🕐 ${pickup}</span>
      ${priceCap}${countNote}
      <span><a href="https://share.toogoodtogo.com/item/${id}" target="_blank">Open →</a></span>
    </div>
  </div>`;
};

async function loadState() {
  const r = await fetch('/api/state');
  const s = await r.json();
  const entries = Object.values(s.items);
  // Sort: high-desire first, then in-stock, then the rest
  entries.sort((a, b) => {
    const da = a.desire === 'high' ? 0 : 1;
    const db = b.desire === 'high' ? 0 : 1;
    if (da !== db) return da - db;
    const sa = (a.item?.items_available ?? 0) > 0 ? 0 : 1;
    const sb = (b.item?.items_available ?? 0) > 0 ? 0 : 1;
    return sa - sb;
  });
  document.getElementById('deals-list').innerHTML = entries.length
    ? entries.map(fmt).join('')
    : 'No items yet. Waiting for first poll…';
  renderOrders(s.orders);
  renderLog(s.events);
  document.getElementById('poll-count').textContent = `${s.poll_count} polls`;
}

function renderOrders(orders) {
  const el = document.getElementById('orders-list');
  el.innerHTML = orders.length
    ? orders.map(o => `<div class="order-row">✅ <strong>${o.store}</strong> — <code>${o.order_id}</code> <span style="color:var(--muted);font-size:0.75rem">${o.state}</span></div>`).join('')
    : 'None yet.';
}

function renderLog(events) {
  const el = document.getElementById('log-list');
  el.innerHTML = events.map(e => `<div class="log-entry"><span class="ts">${e.ts.slice(11,19)}</span>${e.msg}</div>`).join('') || '–';
}

loadState();

const es = new EventSource('/api/events');
es.onopen = () => {
  document.getElementById('status-text').textContent = 'Live — monitoring active';
  document.getElementById('dot').style.background = 'var(--green)';
};
es.onerror = () => {
  document.getElementById('status-text').textContent = 'Disconnected — retrying…';
  document.getElementById('dot').style.background = 'var(--red)';
};
es.onmessage = () => { loadState(); };
</script>
</body>
</html>
"""


def start_dashboard(host: str = DASHBOARD_HOST, port: int = DASHBOARD_PORT) -> None:
    """Start the dashboard in a background thread."""
    import uvicorn

    def _run():
        uvicorn.run(app, host=host, port=port, log_level="warning")

    t = threading.Thread(target=_run, daemon=True, name="tgtg-dashboard")
    t.start()
    log.info("Dashboard started at http://%s:%d", host, port)
