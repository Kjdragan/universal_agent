"""
TGTG Sniper CLI

Usage:
  python -m src.universal_agent.tgtg.cli login          # Authenticate (saves tokens)
  python -m src.universal_agent.tgtg.cli run            # Start monitor (daemon mode)
  python -m src.universal_agent.tgtg.cli status         # Show configured items + credentials
  python -m src.universal_agent.tgtg.cli list           # List available favourites right now
  python -m src.universal_agent.tgtg.cli buy <item_id>  # One-shot manual purchase
"""

from __future__ import annotations

import argparse
import logging
import sys
import threading

from .config import (
    TGTG_AUTO_PURCHASE,
    TGTG_EMAIL,
    TGTG_WATCHED_ITEMS,
    DASHBOARD_PORT,
    load_saved_credentials,
)
from .dashboard import push_item_update, push_log, push_order, start_dashboard
from .monitor import build_client, fetch_watched_items, run_monitor
from .notifier import send_stock_alert
from .purchaser import purchase_item

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("tgtg.cli")


def cmd_login(args):
    email = args.email or TGTG_EMAIL
    if not email:
        print("Error: provide --email or set TGTG_EMAIL in .env")
        sys.exit(1)
    print(f"Requesting magic link for {email} …")
    client = build_client(email=email)
    creds = load_saved_credentials()
    print("✅ Logged in. Tokens saved to disk.")
    print(f"   access_token:  {creds.get('access_token', '')[:20]}…")


def cmd_status(args):
    creds = load_saved_credentials()
    print("── TGTG Sniper Status ──────────────────────────────")
    print(f"Email:          {TGTG_EMAIL or '(not set)'}")
    print(f"Saved tokens:   {'yes' if creds.get('access_token') else 'NO — run login first'}")
    print(f"Auto-purchase:  {'ENABLED ⚡' if TGTG_AUTO_PURCHASE else 'disabled (notify only)'}")
    print(f"Watched items:  {', '.join(TGTG_WATCHED_ITEMS) if TGTG_WATCHED_ITEMS else 'all favourites'}")
    print(f"Dashboard:      http://localhost:{DASHBOARD_PORT}")


def cmd_list(args):
    client = build_client(email=TGTG_EMAIL or None)
    items = fetch_watched_items(client)
    if not items:
        print("No items found.")
        return
    for item in items:
        store = item.get("store", {}).get("store_name", "?")
        avail = item.get("items_available", 0)
        item_id = item.get("item", {}).get("item_id", "?")
        status = f"{'✅ ' + str(avail) + ' bag(s)' if avail > 0 else '❌ sold out'}"
        print(f"  [{item_id}] {store} — {status}")


def cmd_buy(args):
    client = build_client(email=TGTG_EMAIL or None)
    item = client.get_item(args.item_id)
    store = item.get("store", {}).get("store_name", args.item_id)
    avail = item.get("items_available", 0)
    print(f"Item: {store} | {avail} bag(s) available")
    if avail == 0:
        print("No stock available right now.")
        sys.exit(1)
    order = purchase_item(client, item)
    if order:
        print(f"✅ Order created: {order.get('id')} | state={order.get('state')}")
    else:
        print("❌ Purchase failed — check logs.")
        sys.exit(1)


def cmd_run(args):
    client = build_client(email=TGTG_EMAIL or None)
    stop_event = threading.Event()

    # Start web dashboard
    start_dashboard()
    print(f"🌐 Dashboard → http://localhost:{DASHBOARD_PORT}")
    print(f"⚡ Auto-purchase: {'ENABLED' if TGTG_AUTO_PURCHASE else 'disabled'}")
    print(f"👀 Watching: {', '.join(TGTG_WATCHED_ITEMS) if TGTG_WATCHED_ITEMS else 'all favourites'}")
    print("Press Ctrl+C to stop.\n")

    def on_stock(item: dict):
        store = item.get("store", {}).get("store_name", "?")
        push_log(f"STOCK: {store} — {item.get('items_available')} bag(s)")
        push_item_update(item)

        order = None
        if TGTG_AUTO_PURCHASE:
            order = purchase_item(client, item)
            if order:
                push_order(order, item)
                push_log(f"ORDERED: {store} | id={order.get('id')}")

        send_stock_alert(item, order=order)

    # Also push all item states to dashboard on each poll
    def on_stock_with_refresh(item: dict):
        on_stock(item)

    try:
        run_monitor(client, on_stock=on_stock_with_refresh, stop_event=stop_event)
    except KeyboardInterrupt:
        stop_event.set()
        print("\nStopped.")


def main():
    parser = argparse.ArgumentParser(description="TGTG Sniper — deal monitoring & auto-purchase")
    sub = parser.add_subparsers(dest="cmd")

    p_login = sub.add_parser("login", help="Authenticate with TGTG (saves tokens)")
    p_login.add_argument("--email", default=None)

    sub.add_parser("status", help="Show configuration and credential status")
    sub.add_parser("list", help="List current favourite items and stock levels")

    p_buy = sub.add_parser("buy", help="Manually purchase a specific item ID")
    p_buy.add_argument("item_id", help="TGTG item ID")

    p_run = sub.add_parser("run", help="Start the monitor daemon + dashboard")

    args = parser.parse_args()

    cmds = {
        "login": cmd_login,
        "status": cmd_status,
        "list": cmd_list,
        "buy": cmd_buy,
        "run": cmd_run,
    }

    if args.cmd not in cmds:
        parser.print_help()
        sys.exit(0)

    cmds[args.cmd](args)


if __name__ == "__main__":
    main()
