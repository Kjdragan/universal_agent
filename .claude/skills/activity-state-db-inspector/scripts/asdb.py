#!/usr/bin/env python3
"""asdb — read-only inspector for Universal Agent state SQLite databases.

Replaces hand-built `sqlite3 -header ...` one-liners (and their guessed table
or column names) with introspection plus canned queries. Read-only by
construction: every connection opens with `mode=ro`.

Usage:
  asdb.py dbs                      # known databases and whether they exist
  asdb.py tables [--db activity]   # tables with row counts
  asdb.py schema [TABLE] [--db X]  # full schema, or one table's
  asdb.py q "SELECT ..." [--db X]  # ad-hoc read-only SQL
  asdb.py tasks [-n 20]            # recent task_hub_items      (activity)
  asdb.py events [-n 20]           # recent activity_events     (activity)
  asdb.py missions [-n 20]         # recent vp_missions         (vp)
Add --json to any query-ish command for full untruncated output.
"""
import argparse
import json
import os
import sqlite3
import sys

ROOT = os.environ.get("UA_STATE_ROOT", "/opt/universal_agent")
ALIASES = {
    "activity": "AGENT_RUN_WORKSPACES/activity_state.db",
    "vp": "AGENT_RUN_WORKSPACES/coder_vp_state.db",
}


def db_path(name: str) -> str:
    return os.path.join(ROOT, ALIASES[name]) if name in ALIASES else name


def connect(name: str) -> sqlite3.Connection:
    path = db_path(name)
    if not os.path.exists(path):
        sys.exit(f"asdb: no such db: {path} (aliases: {', '.join(ALIASES)}, or pass a path)")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def run(conn, sql, params=()):
    try:
        cur = conn.execute(sql, params)
    except sqlite3.Error as e:
        hint = " (hint: run `asdb.py tables` / `asdb.py schema TABLE` — don't guess names)"
        sys.exit(f"asdb: {e}{hint}")
    cols = [d[0] for d in cur.description] if cur.description else []
    return cols, cur.fetchall()


def emit(cols, rows, as_json, width=60):
    if as_json:
        print(json.dumps([dict(zip(cols, r)) for r in rows], indent=2, default=str))
        return
    if not rows:
        print("(no rows)")
        return
    cells = [[("" if v is None else str(v).replace("\n", " "))[:width] for v in r] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(map(str, cols))]
    print(" | ".join(str(c).ljust(w) for c, w in zip(cols, widths)))
    print("-+-".join("-" * w for w in widths))
    for row in cells:
        print(" | ".join(v.ljust(w) for v, w in zip(row, widths)))


CANNED = {
    "tasks": ("activity",
              "SELECT task_id, status, priority, project_key, title, updated_at "
              "FROM task_hub_items ORDER BY updated_at DESC LIMIT ?"),
    "events": ("activity",
               "SELECT id, source_domain, kind, severity, status, title, created_at "
               "FROM activity_events ORDER BY created_at DESC LIMIT ?"),
    "missions": ("vp",
                 "SELECT mission_id, vp_id, status, mission_type, priority_tier, objective, updated_at "
                 "FROM vp_missions ORDER BY updated_at DESC LIMIT ?"),
}


def main():
    ap = argparse.ArgumentParser(prog="asdb.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["dbs", "tables", "schema", "q", *CANNED, "selfcheck"],
                    nargs="?", default="dbs")
    ap.add_argument("arg", nargs="?", help="table name (schema) or SQL (q)")
    ap.add_argument("--db", default=None, help=f"db alias ({', '.join(ALIASES)}) or path")
    ap.add_argument("-n", type=int, default=20, help="row limit for canned queries")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.cmd == "selfcheck":
        return selfcheck()
    if a.cmd == "dbs":
        for alias, rel in ALIASES.items():
            p = os.path.join(ROOT, rel)
            print(f"{alias:10} {p}  {'OK' if os.path.exists(p) else 'MISSING'}")
        return
    if a.cmd in CANNED:
        alias, sql = CANNED[a.cmd]
        conn = connect(a.db or alias)
        emit(*run(conn, sql, (a.n,)), a.json)
        return

    conn = connect(a.db or "activity")
    if a.cmd == "tables":
        _, names = run(conn, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        rows = []
        for (t,) in names:
            (_, ((count,),)) = run(conn, f'SELECT COUNT(*) FROM "{t}"')
            rows.append((t, count))
        emit(["table", "rows"], rows, a.json)
    elif a.cmd == "schema":
        where, params = ("AND name = ?", (a.arg,)) if a.arg else ("", ())
        _, rows = run(conn, f"SELECT sql FROM sqlite_master WHERE type='table' {where} ORDER BY name", params)
        print("\n\n".join(r[0] for r in rows if r[0]) or f"(no such table: {a.arg})")
    elif a.cmd == "q":
        if not a.arg:
            sys.exit("asdb: q needs a SQL string")
        emit(*run(conn, a.arg), a.json)


def selfcheck():
    import tempfile
    global ROOT
    with tempfile.TemporaryDirectory() as d:
        ROOT = d
        os.makedirs(os.path.join(d, "AGENT_RUN_WORKSPACES"))
        p = os.path.join(d, ALIASES["activity"])
        c = sqlite3.connect(p)
        c.execute("CREATE TABLE task_hub_items (task_id TEXT, status TEXT, priority INT, "
                  "project_key TEXT, title TEXT, updated_at TEXT)")
        c.execute("INSERT INTO task_hub_items VALUES ('t1','open',1,'immediate','hello','2026-01-01')")
        c.commit(); c.close()
        conn = connect("activity")
        cols, rows = run(conn, CANNED["tasks"][1], (5,))
        assert rows[0][0] == "t1" and "status" in cols, (cols, rows)
        try:
            conn.execute("DELETE FROM task_hub_items")
            raise AssertionError("write succeeded on ro connection")
        except sqlite3.OperationalError:
            pass  # read-only enforced
    print("ok")


if __name__ == "__main__":
    main()
