# migrate_to_postgres.py
"""
SQLite → PostgreSQL data migration.
Run after `python app.py` creates empty PG tables.

Usage:
    # Windows-এ SQLite থেকে Linux PG-তে (network):
    python migrate_to_postgres.py --pg "postgresql://netmon:pass@SERVER_IP:5432/netmonitor"
"""
import argparse
import os
import sqlite3
import sys

DEFAULT_SQLITE = os.path.join(os.path.dirname(__file__), 'nodes.db')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sqlite', default=DEFAULT_SQLITE, help='SQLite file path')
    parser.add_argument('--pg', required=True, help='PostgreSQL DSN')
    args = parser.parse_args()

    if not os.path.exists(args.sqlite):
        print(f"❌ SQLite file not found: {args.sqlite}")
        sys.exit(1)

    try:
        import psycopg2
    except ImportError:
        print("❌ Install: pip install psycopg2-binary")
        sys.exit(1)

    print("=" * 60)
    print("🚚 SQLite → PostgreSQL Migration")
    print(f"   Source: {args.sqlite}")
    print(f"   Target: {args.pg.split('@')[-1] if '@' in args.pg else args.pg}")
    print("=" * 60)

    sq_conn = sqlite3.connect(args.sqlite)
    sq_conn.row_factory = sqlite3.Row
    sq = sq_conn.cursor()

    try:
        pg_conn = psycopg2.connect(args.pg)
    except Exception as e:
        print(f"❌ PostgreSQL connect failed: {e}")
        sys.exit(1)

    pg = pg_conn.cursor()

    # Verify target tables exist
    try:
        pg.execute("SELECT 1 FROM nodes LIMIT 1")
    except psycopg2.errors.UndefinedTable:
        print("⚠️  Run `python app.py` once to create empty tables, then retry.")
        sys.exit(1)
    finally:
        pg_conn.rollback()

    # ── Nodes ──
    sq.execute("SELECT * FROM nodes")
    nodes = sq.fetchall()
    print(f"\n📦 Migrating {len(nodes)} nodes...")

    node_ok = 0
    for n in nodes:
        try:
            pg.execute("""
                INSERT INTO nodes (id, node_name, ip_address, response_time, packet_loss,
                    status, last_status_change, last_checked, previous_status,
                    created_at, updated_at, enabled, consecutive_failures,
                    consecutive_successes, last_ping_success)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (ip_address) DO NOTHING
            """, (
                n['id'], n['node_name'], n['ip_address'], n['response_time'],
                n['packet_loss'], n['status'], n['last_status_change'],
                n['last_checked'], n['previous_status'], n['created_at'],
                n['updated_at'], n['enabled'],
                n['consecutive_failures'] if 'consecutive_failures' in n.keys() else 0,
                n['consecutive_successes'] if 'consecutive_successes' in n.keys() else 0,
                n['last_ping_success'] if 'last_ping_success' in n.keys() else None,
            ))
            node_ok += 1
        except Exception as e:
            print(f"  ⚠️  Node '{n['node_name']}' skipped: {e}")

    pg_conn.commit()
    print(f"  ✅ {node_ok} nodes migrated")

    # ── Logs ──
    sq.execute("SELECT * FROM downtime_logs")
    logs = sq.fetchall()
    print(f"\n📋 Migrating {len(logs)} log entries...")

    log_ok = 0
    for l in logs:
        try:
            pg.execute("""
                INSERT INTO downtime_logs (id, node_id, node_name, ip_address,
                    status_change, previous_status, timestamp, downtime_duration, is_resolved)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING
            """, (
                l['id'], l['node_id'], l['node_name'], l['ip_address'],
                l['status_change'], l['previous_status'], l['timestamp'],
                l['downtime_duration'], l['is_resolved'],
            ))
            log_ok += 1
        except Exception:
            pass

    pg_conn.commit()
    print(f"  ✅ {log_ok} logs migrated")

    # Reset sequences
    pg.execute("SELECT setval(pg_get_serial_sequence('nodes','id'), COALESCE(MAX(id),1)) FROM nodes")
    pg.execute("SELECT setval(pg_get_serial_sequence('downtime_logs','id'), COALESCE(MAX(id),1)) FROM downtime_logs")
    pg_conn.commit()

    print("\n✅ Migration complete!")
    pg.close(); pg_conn.close(); sq_conn.close()


if __name__ == '__main__':
    main()