"""
TGTG Sniper CLI

Usage:
  python -m src.universal_agent.tgtg.cli login                    # Authenticate (saves tokens)
  python -m src.universal_agent.tgtg.cli run                      # Start monitor + dashboard
  python -m src.universal_agent.tgtg.cli status                   # Show configuration
  python -m src.universal_agent.tgtg.cli list                     # Live stock check on all targets
  python -m src.universal_agent.tgtg.cli buy <item_id>            # One-shot manual purchase

  # Region scan — populate/refresh the item catalog:
  python -m src.universal_agent.tgtg.cli scan                     # Scan area, save to catalog DB
  python -m src.universal_agent.tgtg.cli scan --radius 10         # Wider search

  # Catalog DB queries:
  python -m src.universal_agent.tgtg.cli db stats                 # Catalog statistics
  python -m src.universal_agent.tgtg.cli db list                  # All active items in catalog
  python -m src.universal_agent.tgtg.cli db list --all            # Include inactive/dead items
  python -m src.universal_agent.tgtg.cli db search pret           # Search by store name

  # Target management:
  python -m src.universal_agent.tgtg.cli target list              # Show all registered targets
  python -m src.universal_agent.tgtg.cli target add <item_id>     # Register a watch target
  python -m src.universal_agent.tgtg.cli target add <item_id> --desire high --count 2 --max-price 5.00
  python -m src.universal_agent.tgtg.cli target remove <item_id>  # Unregister a target
  python -m src.universal_agent.tgtg.cli target set <item_id> --desire high  # Change desire level
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone

from .config import (
    TGTG_AUTO_PURCHASE,
    TGTG_DAILY_BUDGET,
    TGTG_EMAIL,
    TGTG_LATITUDE,
    TGTG_LONGITUDE,
    TGTG_ORDER_COUNT,
    TGTG_PAYMENT_REMINDER_MINUTES,
    TGTG_RADIUS,
    TGTG_SCAN_INTERVAL_HOURS,
    DASHBOARD_PORT,
    load_saved_credentials,
)
from .dashboard import push_item_update, push_log, push_order, start_dashboard
from .db import get_db
from .monitor import build_client, fetch_watched_items, run_monitor
from .notifier import (
    send_dead_item_alert,
    send_new_store_alert,
    send_payment_reminder,
    send_stock_alert,
    send_webhook,
)
from .purchaser import purchase_item
from .scanner import scan_and_store
from .targets import (
    Target,
    all_watched_ids,
    get_target,
    load_targets,
    remove_target,
    upsert_target,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("tgtg.cli")

_DESIRE_ICONS = {"high": "🔥", "watch": "👁 "}


# ── Daily spend cap ───────────────────────────────────────────────────────────

class _DailyBudget:
    """In-memory daily spend tracker; resets at midnight."""

    def __init__(self, limit: float | None):
        self.limit = limit
        self._date: object = None
        self._spent: float = 0.0

    def _maybe_reset(self) -> None:
        today = datetime.now().date()
        if self._date != today:
            self._date = today
            self._spent = 0.0

    def can_spend(self, amount: float) -> bool:
        if self.limit is None:
            return True
        self._maybe_reset()
        return self._spent + amount <= self.limit

    def record(self, amount: float) -> None:
        self._maybe_reset()
        self._spent += amount

    @property
    def remaining(self) -> float | None:
        if self.limit is None:
            return None
        self._maybe_reset()
        return self.limit - self._spent


# ── Payment reminder scheduler ────────────────────────────────────────────────

class _ReminderScheduler:
    """Background thread that fires callbacks at scheduled datetimes."""

    def __init__(self):
        self._pending: deque = deque()
        self._lock = threading.Lock()
        t = threading.Thread(target=self._run, daemon=True, name="reminder-scheduler")
        t.start()

    def schedule(self, fire_at: datetime, callback) -> None:
        with self._lock:
            self._pending.append((fire_at, callback))

    def _run(self) -> None:
        while True:
            time.sleep(30)
            now = datetime.now(timezone.utc)
            with self._lock:
                due = [(t, cb) for t, cb in self._pending if t <= now]
                self._pending = deque((t, cb) for t, cb in self._pending if t > now)
            for _, cb in due:
                try:
                    cb()
                except Exception as exc:
                    log.error("Reminder callback error: %s", exc)


# ── login ─────────────────────────────────────────────────────────────────────

def cmd_login(args):
    email = args.email or TGTG_EMAIL
    if not email:
        print("Error: provide --email or set TGTG_EMAIL in .env")
        sys.exit(1)
    print(f"Requesting magic link for {email} …")
    build_client(email=email)
    creds = load_saved_credentials()
    print("✅ Logged in. Tokens saved to disk.")
    print(f"   access_token:  {creds.get('access_token', '')[:20]}…")


# ── status ────────────────────────────────────────────────────────────────────

def cmd_status(args):
    creds = load_saved_credentials()
    targets = load_targets()
    high = [t for t in targets if t.desire == "high"]
    watch = [t for t in targets if t.desire == "watch"]

    print("── TGTG Sniper Status ──────────────────────────────")
    print(f"Email:            {TGTG_EMAIL or '(not set)'}")
    print(f"Saved tokens:     {'yes' if creds.get('access_token') else 'NO — run login first'}")
    print(f"")
    print(f"🔥 High-desire targets ({len(high)}) — auto-buy on stock:")
    for t in high:
        price_cap = f"  max {t.max_price}" if t.max_price else ""
        print(f"   [{t.item_id}] {t.label or '(unlabelled)'} × {t.order_count}{price_cap}")
    print(f"")
    print(f"👁  Watch-only targets ({len(watch)}) — notify on stock:")
    for t in watch:
        print(f"   [{t.item_id}] {t.label or '(unlabelled)'}")
    if not targets:
        print("   (none — add targets with: target add <item_id>)")
    print(f"")
    print(f"Dashboard:        http://localhost:{DASHBOARD_PORT}")


# ── list ──────────────────────────────────────────────────────────────────────

def cmd_list(args):
    client = build_client(email=TGTG_EMAIL or None)
    pairs = fetch_watched_items(client)
    if not pairs:
        print("No items found. Add targets with: target add <item_id>")
        return
    for item, target in pairs:
        store = item.get("store", {}).get("store_name", "?")
        avail = item.get("items_available", 0)
        item_id = item.get("item", {}).get("item_id", "?")
        desire = target.desire if target else "watch"
        icon = _DESIRE_ICONS.get(desire, "")
        stock = f"✅ {avail} bag(s)" if avail > 0 else "❌ sold out"
        print(f"  {icon} [{item_id}] {store} — {stock}")


# ── buy ───────────────────────────────────────────────────────────────────────

def cmd_buy(args):
    client = build_client(email=TGTG_EMAIL or None)
    item = client.get_item(args.item_id)
    target = get_target(args.item_id)
    store = item.get("store", {}).get("store_name", args.item_id)
    avail = item.get("items_available", 0)
    print(f"Item: {store} | {avail} bag(s) available")
    if avail == 0:
        print("No stock available right now.")
        sys.exit(1)
    order = purchase_item(client, item, target=target)
    if order:
        print(f"✅ Order created: {order.get('id')} | state={order.get('state')}")
    else:
        print("❌ Purchase failed (price guard hit or API error) — check logs.")
        sys.exit(1)


# ── run ───────────────────────────────────────────────────────────────────────

def _item_price_value(item: dict) -> float:
    """Extract the numeric price from an item dict (returns 0.0 on failure)."""
    try:
        p = item["item"]["price_including_taxes"]
        return p["minor_units"] / 10 ** p["decimals"]
    except (KeyError, TypeError, ZeroDivisionError):
        return 0.0


def _schedule_payment_reminder(
    scheduler: _ReminderScheduler,
    order: dict,
    item: dict,
    store: str,
) -> None:
    """Queue a Telegram reminder N minutes before pickup closes."""
    if TGTG_PAYMENT_REMINDER_MINUTES <= 0:
        return
    try:
        window = item["item"]["pickup_interval"]
        pickup_end = datetime.fromisoformat(
            window["end"].replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    except (KeyError, TypeError, ValueError):
        return

    from datetime import timedelta
    fire_at = pickup_end - timedelta(minutes=TGTG_PAYMENT_REMINDER_MINUTES)
    if fire_at <= datetime.now(timezone.utc):
        # Window too close — fire immediately
        fire_at = datetime.now(timezone.utc)

    order_id = str(order.get("id", "?"))
    scheduler.schedule(
        fire_at,
        lambda: send_payment_reminder(order_id, store, pickup_end),
    )
    log.info(
        "Payment reminder scheduled at %s for order %s",
        fire_at.astimezone().strftime("%H:%M"),
        order_id,
    )


def _start_scan_scheduler(client, stop_event: threading.Event) -> None:
    """Background thread: re-scan the region every TGTG_SCAN_INTERVAL_HOURS hours."""
    if TGTG_SCAN_INTERVAL_HOURS <= 0:
        return

    def _loop():
        interval_secs = TGTG_SCAN_INTERVAL_HOURS * 3600
        time.sleep(interval_secs)  # first scan is manual; subsequent are auto
        while not stop_event.is_set():
            log.info("Auto-scan: starting scheduled region rescan …")
            try:
                report = scan_and_store(client, TGTG_LATITUDE, TGTG_LONGITUDE, TGTG_RADIUS)
                push_log(f"🔄 Auto-scan: {report.items_found} items, {report.items_new} new")
                if report.new_item_ids:
                    db = get_db()
                    new_pairs = [
                        (iid, (db.get(iid) or {}).get("store_name", iid))
                        for iid in report.new_item_ids
                    ]
                    send_new_store_alert(new_pairs)
                    send_webhook("scan_new_stores", {
                        "new_count": len(report.new_item_ids),
                        "new_items": [{"item_id": i, "store_name": n} for i, n in new_pairs],
                    })
            except Exception as exc:
                log.error("Auto-scan failed: %s", exc)
            time.sleep(interval_secs)

    t = threading.Thread(target=_loop, daemon=True, name="scan-scheduler")
    t.start()


def cmd_run(args):
    client = build_client(email=TGTG_EMAIL or None)
    stop_event = threading.Event()

    targets = load_targets()
    high_targets = [t for t in targets if t.desire == "high"]

    budget = _DailyBudget(TGTG_DAILY_BUDGET)
    reminders = _ReminderScheduler()

    start_dashboard()
    _start_scan_scheduler(client, stop_event)

    print(f"🌐 Dashboard → http://localhost:{DASHBOARD_PORT}")
    print(f"🔥 High-desire (auto-buy): {len(high_targets)} target(s)")
    print(f"👁  Watch-only (notify):   {len(targets) - len(high_targets)} target(s)")
    if TGTG_AUTO_PURCHASE:
        print("⚡ Global TGTG_AUTO_PURCHASE=true (all items auto-buy regardless of desire level)")
    if TGTG_DAILY_BUDGET:
        print(f"💰 Daily budget cap: {TGTG_DAILY_BUDGET}")
    if TGTG_SCAN_INTERVAL_HOURS > 0:
        print(f"🔄 Auto-scan every {TGTG_SCAN_INTERVAL_HOURS}h")
    print("Press Ctrl+C to stop.\n")

    def on_stock(item: dict, target: Target | None):
        store = item.get("store", {}).get("store_name", "?")
        item_id = str(item.get("item", {}).get("item_id", ""))
        desire = target.desire if target else "watch"
        icon = _DESIRE_ICONS.get(desire, "")

        push_log(f"{icon} STOCK [{desire.upper()}]: {store} — {item.get('items_available')} bag(s)")
        push_item_update(item, target)

        # Record stock appearance for sell-through speed tracking
        get_db().record_stock_appeared(item_id, item.get("items_available", 0))

        # Fire webhook for every stock event
        send_webhook("stock_available", {
            "item_id": item_id,
            "store": store,
            "bags_available": item.get("items_available", 0),
            "desire": desire,
        })

        # Decide whether to auto-buy:
        #   1. Global override (TGTG_AUTO_PURCHASE=true), or
        #   2. This specific target has desire="high"
        should_buy = TGTG_AUTO_PURCHASE or (target is not None and target.auto_buy)

        # Pickup window availability filter (feature 1)
        if should_buy and target is not None and not target.pickup_ok(item):
            log.info("SKIPPED (outside available hours): %s", store)
            push_log(f"⏳ SKIPPED (outside your hours): {store}", level="warning")
            should_buy = False

        # Daily budget cap (feature 6)
        if should_buy:
            price = _item_price_value(item)
            if not budget.can_spend(price):
                log.info(
                    "SKIPPED (daily budget cap %.2f reached, remaining %.2f): %s",
                    budget.limit,
                    budget.remaining or 0,
                    store,
                )
                push_log(
                    f"💸 SKIPPED (budget cap reached, {budget.remaining:.2f} left): {store}",
                    level="warning",
                )
                should_buy = False

        order = None
        if should_buy:
            order = purchase_item(client, item, target=target)
            if order:
                price = _item_price_value(item)
                budget.record(price)
                push_order(order, item)
                push_log(f"✅ ORDERED: {store} | id={order.get('id')}")
                _schedule_payment_reminder(reminders, order, item, store)
                send_webhook("order_placed", {
                    "order_id": order.get("id"),
                    "store": store,
                    "item_id": item_id,
                    "price": price,
                    "daily_spent": budget._spent,
                })
            else:
                push_log(f"❌ Purchase failed or price guard: {store}", level="warning")

        send_stock_alert(item, order=order, target=target)

    def on_dead_item(item_id: str, label: str):
        msg = f"⚰️ GONE: {label} [{item_id}] — no longer in TGTG API"
        log.warning(msg)
        push_log(msg, level="warning")
        send_dead_item_alert(item_id, label)
        send_webhook("store_gone", {"item_id": item_id, "label": label})

    def on_sold_out(item_id: str, store: str):
        get_db().record_stock_sold_out(item_id)
        send_webhook("stock_sold_out", {"item_id": item_id, "store": store})

    try:
        run_monitor(
            client,
            on_stock=on_stock,
            on_dead_item=on_dead_item,
            on_sold_out=on_sold_out,
            stop_event=stop_event,
        )
    except KeyboardInterrupt:
        stop_event.set()
        print("\nStopped.")


# ── scan ──────────────────────────────────────────────────────────────────────

def cmd_scan(args):
    """Run a region scan to populate / refresh the item catalog."""
    lat = args.lat or TGTG_LATITUDE
    lon = args.lon or TGTG_LONGITUDE
    radius = args.radius or TGTG_RADIUS

    print(f"Scanning area: lat={lat}, lon={lon}, radius={radius}km …")
    client = build_client(email=TGTG_EMAIL or None)
    report = scan_and_store(client, lat, lon, radius)
    print(report)

    if report.new_item_ids:
        print(f"\n🆕 New stores found:")
        db = get_db()
        for iid in report.new_item_ids:
            row = db.get(iid)
            name = row["store_name"] if row else iid
            print(f"   [{iid}] {name}")

    if report.dead_item_ids:
        print(f"\n⚠️  Stores gone from API (marked inactive):")
        db = get_db()
        for iid in report.dead_item_ids:
            row = db.get(iid)
            name = row["store_name"] if row else iid
            print(f"   [{iid}] {name}")

    total = get_db().stats()["active"]
    print(f"\nCatalog now has {total} active item(s). Use 'db list' to browse.")


# ── db subcommands ────────────────────────────────────────────────────────────

def _fmt_price(row) -> str:
    if row["price_minor"] is None:
        return "?"
    val = row["price_minor"] / (10 ** row["price_decimals"])
    return f"{val:.2f} {row['price_currency']}"


def cmd_db_stats(args):
    stats = get_db().stats()
    print("── TGTG Catalog Stats ─────────────────────────────────")
    print(f"  Total items ever seen: {stats['total']}")
    print(f"  Currently active:      {stats['active']}")
    print(f"  Gone / inactive:       {stats['dead']}")
    print(f"  Scans run:             {stats['scans_run']}")
    if stats["last_scan_at"]:
        print(f"  Last scan:             {stats['last_scan_at'][:19].replace('T', ' ')} UTC")
        print(f"  Items found in scan:   {stats['last_scan_found']}")
    else:
        print("  Last scan:             (never — run 'scan' first)")


def cmd_db_list(args):
    active_only = not args.all
    rows = get_db().all_items(active_only=active_only, limit=500)
    if not rows:
        label = "active " if active_only else ""
        print(f"No {label}items in catalog. Run 'scan' first.")
        return

    status_label = "ACTIVE" if active_only else "ALL"
    print(f"── Catalog ({status_label}: {len(rows)} item(s)) ─────────────────────────────────────")
    print(f"{'ID':<12} {'Status':<8} {'Price':<10} {'Store name'}")
    print("─" * 70)
    for row in rows:
        status = "✅ live" if row["is_active"] else "⚰️  dead"
        price = _fmt_price(row)
        print(f"{row['item_id']:<12} {status:<10} {price:<10} {row['store_name']}")
    print(f"\n{len(rows)} item(s). Use 'target add <item_id>' to start watching one.")


def cmd_db_search(args):
    rows = get_db().search(query=args.query, active_only=not args.all, limit=100)
    if not rows:
        print(f"No matches for '{args.query}'.")
        return
    print(f"── Search: '{args.query}' ({len(rows)} result(s)) ───────────────────────────────────")
    print(f"{'ID':<12} {'Status':<8} {'Price':<10} {'Store name'}")
    print("─" * 70)
    for row in rows:
        status = "✅" if row["is_active"] else "⚰️ "
        price = _fmt_price(row)
        print(f"{row['item_id']:<12} {status:<6} {price:<10} {row['store_name']}")


def cmd_db_speed(args):
    """Show sold-out speed stats per store (how fast bags disappear)."""
    rows = get_db().speed_stats(limit=args.limit)
    if not rows:
        print("No sold-out speed data yet. Bags need to appear and sell out at least once.")
        return
    print(f"── Sold-out Speed (top {len(rows)}) ────────────────────────────────────────────────")
    print(f"{'ID':<12} {'Events':<8} {'Avg (min)':<12} {'Min (min)':<12} {'Max (min)':<12} {'Store'}")
    print("─" * 80)
    for r in rows:
        avg = r["avg_secs"] / 60 if r["avg_secs"] else 0
        mn  = r["min_secs"] / 60 if r["min_secs"] else 0
        mx  = r["max_secs"] / 60 if r["max_secs"] else 0
        print(
            f"{r['item_id']:<12} {r['events']:<8} {avg:<12.1f} {mn:<12.1f} {mx:<12.1f} "
            f"{r['store_name'] or '?'}"
        )
    print("\nFastest stores sell out quickest — consider sniping poll interval.")


# ── target subcommands ────────────────────────────────────────────────────────

def cmd_target_list(args):
    targets = load_targets()
    if not targets:
        print("No targets registered. Add one with: target add <item_id>")
        return
    print(f"{'ID':<12} {'Desire':<8} {'Count':<6} {'MaxPrice':<10} {'Available':<15} {'Label'}")
    print("─" * 75)
    for t in targets:
        icon = _DESIRE_ICONS.get(t.desire, "")
        price = f"{t.max_price:.2f}" if t.max_price else "—"
        avail = f"{t.available_from or '?'}–{t.available_to or '?'}" if (t.available_from or t.available_to) else "any time"
        print(f"{t.item_id:<12} {icon}{t.desire:<7} {t.order_count:<6} {price:<10} {avail:<15} {t.label}")
    print(f"\n{len(targets)} target(s) — 🔥 = auto-buy on stock, 👁  = notify only")


def cmd_target_add(args):
    # Auto-populate label from live API if possible
    label = args.label or ""
    if not label:
        try:
            client = build_client(email=TGTG_EMAIL or None)
            item = client.get_item(args.item_id)
            label = item.get("store", {}).get("store_name", "")
        except Exception:
            pass  # label stays empty — that's fine

    desire = args.desire
    target = Target(
        item_id=str(args.item_id),
        label=label,
        desire=desire,
        order_count=args.count,
        max_price=args.max_price,
        notes=args.notes or "",
        available_from=args.available_from or None,
        available_to=args.available_to or None,
    )
    upsert_target(target)
    icon = _DESIRE_ICONS.get(desire, "")
    price_info = f" (max price: {args.max_price})" if args.max_price else ""
    hours_info = ""
    if target.available_from or target.available_to:
        hours_info = f" [available {target.available_from or '?'}–{target.available_to or '?'}]"
    print(f"✅ {icon} [{args.item_id}] '{label or args.item_id}' registered as {desire.upper()}{price_info} × {args.count}{hours_info}")
    if desire == "high":
        print("   ⚡ This item will be auto-purchased immediately when stock is detected.")
    else:
        print("   🔔 You will be notified when stock appears (no auto-buy).")


def cmd_target_remove(args):
    removed = remove_target(args.item_id)
    if removed:
        print(f"✅ Target [{args.item_id}] removed.")
    else:
        print(f"Target [{args.item_id}] not found.")


def cmd_target_set(args):
    target = get_target(args.item_id)
    if not target:
        print(f"Target [{args.item_id}] not found. Add it first with: target add {args.item_id}")
        sys.exit(1)
    if args.desire:
        target.desire = args.desire
    if args.count is not None:
        target.order_count = args.count
    if args.max_price is not None:
        target.max_price = args.max_price
    if args.label:
        target.label = args.label
    if args.available_from is not None:
        target.available_from = args.available_from or None
    if args.available_to is not None:
        target.available_to = args.available_to or None
    upsert_target(target)
    icon = _DESIRE_ICONS.get(target.desire, "")
    hours = (
        f", available {target.available_from}–{target.available_to}"
        if target.available_from or target.available_to
        else ""
    )
    print(f"✅ {icon} [{args.item_id}] updated → desire={target.desire}, count={target.order_count}, max_price={target.max_price}{hours}")


# ── argument parser ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="TGTG Sniper — deal monitoring & auto-purchase")
    sub = parser.add_subparsers(dest="cmd")

    # login
    p_login = sub.add_parser("login", help="Authenticate with TGTG (saves tokens)")
    p_login.add_argument("--email", default=None)

    # status / list / run
    sub.add_parser("status", help="Show configuration and targets")
    sub.add_parser("list", help="Check current stock on all registered targets")
    sub.add_parser("run", help="Start monitor daemon + dashboard")

    # buy
    p_buy = sub.add_parser("buy", help="Manually purchase a specific item ID now")
    p_buy.add_argument("item_id")

    # scan
    p_scan = sub.add_parser("scan", help="Scan region and populate item catalog DB")
    p_scan.add_argument("--lat", type=float, default=None, help="Override latitude")
    p_scan.add_argument("--lon", type=float, default=None, help="Override longitude")
    p_scan.add_argument("--radius", type=int, default=None, help="Search radius in km")

    # db
    p_db = sub.add_parser("db", help="Query the item catalog database")
    dbsub = p_db.add_subparsers(dest="db_cmd")

    dbsub.add_parser("stats", help="Show catalog statistics")

    p_db_list = dbsub.add_parser("list", help="List items in catalog")
    p_db_list.add_argument("--all", action="store_true", help="Include inactive/dead items")

    p_db_search = dbsub.add_parser("search", help="Search catalog by store name")
    p_db_search.add_argument("query", help="Substring to search for")
    p_db_search.add_argument("--all", action="store_true", help="Include inactive items")

    p_db_speed = dbsub.add_parser("speed", help="Show sold-out speed stats per store")
    p_db_speed.add_argument("--limit", type=int, default=20, help="Number of stores to show")

    # target
    p_target = sub.add_parser("target", help="Manage snipe targets")
    tsub = p_target.add_subparsers(dest="target_cmd")

    tsub.add_parser("list", help="List all registered targets")

    p_add = tsub.add_parser("add", help="Register a target item")
    p_add.add_argument("item_id", help="TGTG item ID (get from 'list' command)")
    p_add.add_argument(
        "--desire",
        choices=["high", "watch"],
        default="watch",
        help="'high' = auto-buy immediately; 'watch' = notify only (default: watch)",
    )
    p_add.add_argument("--count", type=int, default=1, help="Bags to reserve per trigger (default: 1)")
    p_add.add_argument("--max-price", type=float, default=None, metavar="PRICE",
                       help="Skip auto-buy if bag costs more than this amount")
    p_add.add_argument("--label", default="", help="Friendly name (auto-fetched if omitted)")
    p_add.add_argument("--notes", default="", help="Personal notes")
    p_add.add_argument(
        "--available-from", default="", metavar="HH:MM",
        help="Earliest local time you can collect (e.g. 17:00). Skips auto-buy outside window.",
    )
    p_add.add_argument(
        "--available-to", default="", metavar="HH:MM",
        help="Latest local time you can collect (e.g. 20:00).",
    )

    p_remove = tsub.add_parser("remove", help="Unregister a target")
    p_remove.add_argument("item_id")

    p_set = tsub.add_parser("set", help="Update desire level or settings on an existing target")
    p_set.add_argument("item_id")
    p_set.add_argument("--desire", choices=["high", "watch"], default=None)
    p_set.add_argument("--count", type=int, default=None)
    p_set.add_argument("--max-price", type=float, default=None, metavar="PRICE")
    p_set.add_argument("--label", default=None)
    p_set.add_argument("--available-from", default=None, metavar="HH:MM",
                       help="Set collection start time (empty string to clear)")
    p_set.add_argument("--available-to", default=None, metavar="HH:MM",
                       help="Set collection end time (empty string to clear)")

    args = parser.parse_args()

    # Top-level commands
    top_cmds = {
        "login": cmd_login,
        "status": cmd_status,
        "list": cmd_list,
        "buy": cmd_buy,
        "run": cmd_run,
        "scan": cmd_scan,
    }
    if args.cmd in top_cmds:
        top_cmds[args.cmd](args)
        return

    # target subcommands
    if args.cmd == "target":
        target_cmds = {
            "list": cmd_target_list,
            "add": cmd_target_add,
            "remove": cmd_target_remove,
            "set": cmd_target_set,
        }
        if getattr(args, "target_cmd", None) in target_cmds:
            target_cmds[args.target_cmd](args)
            return
        p_target.print_help()
        return

    # db subcommands
    if args.cmd == "db":
        db_cmds = {
            "stats": cmd_db_stats,
            "list": cmd_db_list,
            "search": cmd_db_search,
            "speed": cmd_db_speed,
        }
        if getattr(args, "db_cmd", None) in db_cmds:
            db_cmds[args.db_cmd](args)
            return
        p_db.print_help()
        return

    parser.print_help()


if __name__ == "__main__":
    main()
