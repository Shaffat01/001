import sys
import os
import platform

# ══════════════════════════════════════════════════════════
# OS-SPECIFIC PRIVILEGE HANDLING
# ══════════════════════════════════════════════════════════
IS_WINDOWS = platform.system().lower() == 'windows'
IS_LINUX = platform.system().lower() == 'linux'

if IS_WINDOWS:
    import ctypes

    def is_admin():
        try:
            return ctypes.windll.shell32.IsUserAnAdmin()
        except Exception:
            return False

    if not is_admin():
        print("[INIT] Requesting Administrator Privileges...")
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, " ".join(sys.argv), None, 1
        )
        sys.exit(0)

elif IS_LINUX:
    if os.geteuid() != 0:
        print("[INIT] Linux mode: non-root user detected.")
        print("       icmplib needs CAP_NET_RAW. Fix if privileged ping fails:")
        print("       sudo setcap cap_net_raw+ep $(readlink -f $(which python3))")
        print("       OR use systemd AmbientCapabilities=CAP_NET_RAW")

# ══════════════════════════════════════════════════════════
import csv
import io
import logging
import queue as _queue
import json
from datetime import datetime
from flask import (
    Flask, render_template, request, jsonify,
    redirect, url_for, flash, Response
)
from flask_apscheduler import APScheduler
from config import Config
from models import db, Node, DowntimeLog
from monitor import monitor
from events import subscribe, unsubscribe, publish, subscriber_count

# Disable noisy werkzeug logs
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)
app.config.from_object(Config)

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db.init_app(app)


def is_main_process():
    return os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug


if is_main_process():
    monitor.init_app(app)


scheduler = APScheduler()


@scheduler.task(
    'interval',
    id='monitor_nodes',
    seconds=Config.PING_INTERVAL,
    misfire_grace_time=30,
    max_instances=1,
    coalesce=True
)
def scheduled_monitor():
    with app.app_context():
        monitor.monitor_all_nodes()


# ══════════════════════════════════════════════════════════
# DB INIT — cross-platform (PostgreSQL ও SQLite দুটোই support)
# ══════════════════════════════════════════════════════════
with app.app_context():
    db.create_all()
    # SQLite-specific pragma — PostgreSQL-এ skip
    if app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite'):
        with db.engine.connect() as conn:
            conn.execute(db.text("PRAGMA journal_mode=WAL"))
            conn.execute(db.text("PRAGMA synchronous=NORMAL"))
            conn.execute(db.text("PRAGMA busy_timeout=30000"))
            conn.commit()
        print("[DB] SQLite WAL mode enabled")
    else:
        print("[DB] PostgreSQL connected — tables ready")


if is_main_process():
    scheduler.init_app(app)
    scheduler.start()

    import threading
    def initial_ping():
        import time
        time.sleep(3)
        print("[STARTUP] Running initial ping cycle...")
        with app.app_context():
            monitor.monitor_all_nodes()

    threading.Thread(target=initial_ping, daemon=True).start()


# ══════════════════════════════════════════════════════════
# WEB ROUTES
# ══════════════════════════════════════════════════════════

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/add', methods=['GET', 'POST'])
def add_node():
    if request.method == 'POST':
        node_name = request.form.get('node_name', '').strip()
        ip_address = request.form.get('ip_address', '').strip()

        if not node_name or not ip_address:
            flash('Node name and IP address are required.', 'error')
            return render_template('add_node.html')

        existing = Node.query.filter_by(ip_address=ip_address).first()
        if existing:
            flash(f'A node with IP {ip_address} already exists.', 'error')
            return render_template('add_node.html')

        node = Node(
            node_name=node_name,
            ip_address=ip_address,
            status='Unknown',
            last_status_change=datetime.utcnow()
        )
        db.session.add(node)
        db.session.commit()

        import threading
        t = threading.Thread(target=monitor.ping_single_node, args=(node.id,))
        t.daemon = True
        t.start()

        flash(f'Node "{node_name}" added successfully.', 'success')
        return redirect(url_for('index'))

    return render_template('add_node.html')


@app.route('/edit/<int:node_id>', methods=['GET', 'POST'])
def edit_node(node_id):
    node = db.session.get(Node, node_id)
    if not node:
        flash('Node not found.', 'error')
        return redirect(url_for('index'))

    if request.method == 'POST':
        node_name = request.form.get('node_name', '').strip()
        ip_address = request.form.get('ip_address', '').strip()
        enabled = request.form.get('enabled') == 'on'

        if not node_name or not ip_address:
            flash('Node name and IP address are required.', 'error')
            return render_template('edit_node.html', node=node)

        existing = Node.query.filter(
            Node.ip_address == ip_address,
            Node.id != node_id
        ).first()
        if existing:
            flash(f'Another node with IP {ip_address} already exists.', 'error')
            return render_template('edit_node.html', node=node)

        node.node_name = node_name
        node.ip_address = ip_address
        node.enabled = enabled
        node.updated_at = datetime.utcnow()
        db.session.commit()

        flash(f'Node "{node_name}" updated successfully.', 'success')
        return redirect(url_for('index'))

    return render_template('edit_node.html', node=node)


@app.route('/delete/<int:node_id>', methods=['POST'])
def delete_node(node_id):
    node = db.session.get(Node, node_id)
    if not node:
        flash('Node not found.', 'error')
        return redirect(url_for('index'))

    node_name = node.node_name
    db.session.delete(node)
    db.session.commit()

    flash(f'Node "{node_name}" deleted successfully.', 'success')
    return redirect(url_for('index'))


@app.route('/upload', methods=['GET', 'POST'])
def upload_csv():
    if request.method == 'POST':
        if 'csv_file' not in request.files:
            flash('No file selected.', 'error')
            return render_template('upload_csv.html')

        file = request.files['csv_file']

        if file.filename == '':
            flash('No file selected.', 'error')
            return render_template('upload_csv.html')

        if not file.filename.endswith('.csv'):
            flash('Please upload a CSV file.', 'error')
            return render_template('upload_csv.html')

        try:
            stream = io.StringIO(file.stream.read().decode('utf-8-sig'))
            reader = csv.DictReader(stream)

            added = 0
            skipped = 0
            errors = []

            for row_num, row in enumerate(reader, start=2):
                node_name = (
                    row.get('node_name') or row.get('Node') or
                    row.get('node') or row.get('hostname') or
                    row.get('Hostname') or row.get('Name') or ''
                ).strip()

                ip_address = (
                    row.get('ip_address') or row.get('IP Address') or
                    row.get('ip') or row.get('IP') or
                    row.get('address') or ''
                ).strip()

                if not node_name or not ip_address:
                    errors.append(f"Row {row_num}: Missing node name or IP")
                    skipped += 1
                    continue

                existing = Node.query.filter_by(ip_address=ip_address).first()
                if existing:
                    skipped += 1
                    continue

                node = Node(
                    node_name=node_name,
                    ip_address=ip_address,
                    status='Unknown',
                    last_status_change=datetime.utcnow()
                )
                db.session.add(node)
                added += 1

            db.session.commit()

            msg = f'Successfully added {added} nodes. Skipped {skipped}.'
            if errors:
                msg += f' Errors: {"; ".join(errors[:5])}'

            flash(msg, 'success' if added > 0 else 'warning')

            import threading
            t = threading.Thread(target=monitor.monitor_all_nodes)
            t.daemon = True
            t.start()

            return redirect(url_for('index'))

        except Exception as e:
            flash(f'Error processing CSV: {str(e)}', 'error')
            return render_template('upload_csv.html')

    return render_template('upload_csv.html')


# ══════════════════════════════════════════════════════════
# API — NODES
# ══════════════════════════════════════════════════════════

@app.route('/api/nodes')
def api_get_nodes():
    sort_by = request.args.get('sort', 'since_last_change')
    order = request.args.get('order', 'asc')

    sort_map = {
        'node_name': Node.node_name,
        'ip_address': Node.ip_address,
        'response_time': Node.response_time,
        'packet_loss': Node.packet_loss,
        'status': Node.status,
        'last_status_change': Node.last_status_change,
        'since_last_change': Node.last_status_change
    }

    sort_column = sort_map.get(sort_by, Node.last_status_change)

    if sort_by == 'since_last_change':
        nodes = Node.query.order_by(sort_column.desc() if order == 'asc' else sort_column.asc()).all()
    else:
        nodes = Node.query.order_by(sort_column.desc() if order == 'desc' else sort_column.asc()).all()

    return jsonify({
        'nodes': [node.to_dict() for node in nodes],
        'total': len(nodes),
        'sort': sort_by,
        'order': order
    })


@app.route('/api/nodes/<int:node_id>')
def api_get_single_node(node_id):
    node = db.session.get(Node, node_id)
    if not node:
        return jsonify({'error': 'Node not found'}), 404
    return jsonify(node.to_dict())


@app.route('/api/nodes/<int:node_id>/ping', methods=['POST'])
def api_ping_node(node_id):
    node = db.session.get(Node, node_id)
    if not node:
        return jsonify({'error': 'Node not found'}), 404

    monitor.ping_single_node(node_id)
    db.session.refresh(node)

    return jsonify(node.to_dict())


@app.route('/api/nodes/delete', methods=['POST'])
def api_delete_node():
    data = request.get_json()
    node_id = data.get('node_id')

    node = db.session.get(Node, node_id)
    if not node:
        return jsonify({'error': 'Node not found'}), 404

    db.session.delete(node)
    db.session.commit()

    return jsonify({'success': True})


@app.route('/api/refresh', methods=['POST'])
def api_refresh_all():
    import threading
    t = threading.Thread(target=monitor.monitor_all_nodes)
    t.daemon = True
    t.start()

    return jsonify({'success': True, 'message': 'Refresh started'})


# ══════════════════════════════════════════════════════════
# SSE — Server-Sent Events
# ══════════════════════════════════════════════════════════

@app.route('/api/stream')
def stream():
    def event_stream():
        q = subscribe()
        try:
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            while True:
                try:
                    event = q.get(timeout=25)
                    yield f"data: {json.dumps(event)}\n\n"
                except _queue.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            unsubscribe(q)

    return Response(
        event_stream(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )


@app.route('/api/sse/stats')
def sse_stats():
    return jsonify({'subscribers': subscriber_count()})


# ══════════════════════════════════════════════════════════
# DOWNTIME LOGS
# ══════════════════════════════════════════════════════════

@app.route('/node/<int:node_id>')
def node_detail(node_id):
    node = db.session.get(Node, node_id)
    if not node:
        flash('Node not found.', 'error')
        return redirect(url_for('index'))
    return render_template('node_detail.html', node=node)


@app.route('/logs')
def downtime_logs():
    return render_template('downtime_logs.html')


@app.route('/api/logs')
def api_get_logs():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    node_id = request.args.get('node_id', None, type=int)
    status_filter = request.args.get('status', None)
    resolved = request.args.get('resolved', None)

    query = DowntimeLog.query

    if node_id:
        query = query.filter_by(node_id=node_id)
    if status_filter:
        query = query.filter_by(status_change=status_filter)
    if resolved == 'true':
        query = query.filter_by(is_resolved=True)
    elif resolved == 'false':
        query = query.filter_by(is_resolved=False)

    query = query.order_by(DowntimeLog.timestamp.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    logs = pagination.items

    return jsonify({
        'logs': [log.to_dict() for log in logs],
        'total': pagination.total,
        'page': page,
        'per_page': per_page,
        'total_pages': pagination.pages,
        'has_next': pagination.has_next,
        'has_prev': pagination.has_prev
    })


@app.route('/api/logs/node/<int:node_id>/stats')
def api_node_log_stats(node_id):
    node = db.session.get(Node, node_id)
    if not node:
        return jsonify({'error': 'Node not found'}), 404

    total_down = DowntimeLog.query.filter_by(node_id=node_id, status_change='Node Down').count()
    total_up = DowntimeLog.query.filter_by(node_id=node_id, status_change='Node Up').count()

    resolved_down = DowntimeLog.query.filter(
        DowntimeLog.node_id == node_id,
        DowntimeLog.status_change == 'Node Down',
        DowntimeLog.is_resolved == True,
        DowntimeLog.downtime_duration.isnot(None)
    ).all()

    total_downtime = sum(l.downtime_duration for l in resolved_down if l.downtime_duration)
    avg_downtime = total_downtime / len(resolved_down) if resolved_down else 0
    max_downtime = max((l.downtime_duration for l in resolved_down if l.downtime_duration), default=0)

    if node.created_at:
        total_lifetime = (datetime.utcnow() - node.created_at).total_seconds()
        availability = round(((total_lifetime - total_downtime) / total_lifetime) * 100, 2) if total_lifetime > 0 else 100
    else:
        availability = 100

    return jsonify({
        'node_id': node_id,
        'node_name': node.node_name,
        'current_status': node.status,
        'total_down': total_down,
        'total_up': total_up,
        'total_downtime': round(total_downtime, 1),
        'avg_downtime': round(avg_downtime, 1),
        'max_downtime': round(max_downtime, 1),
        'availability': availability
    })


@app.route('/api/logs/stats')
def api_log_stats():
    total_logs = DowntimeLog.query.count()
    unresolved = DowntimeLog.query.filter_by(is_resolved=False).count()
    down_events = DowntimeLog.query.filter_by(status_change='Node Down').count()
    up_events = DowntimeLog.query.filter_by(status_change='Node Up').count()

    resolved_logs = DowntimeLog.query.filter(
        DowntimeLog.status_change == 'Node Down',
        DowntimeLog.is_resolved == True,
        DowntimeLog.downtime_duration.isnot(None)
    ).all()

    avg_downtime = 0
    max_downtime = 0
    if resolved_logs:
        durations = [l.downtime_duration for l in resolved_logs if l.downtime_duration]
        if durations:
            avg_downtime = sum(durations) / len(durations)
            max_downtime = max(durations)

    return jsonify({
        'total_logs': total_logs,
        'unresolved': unresolved,
        'down_events': down_events,
        'up_events': up_events,
        'avg_downtime': round(avg_downtime, 1),
        'max_downtime': round(max_downtime, 1)
    })


@app.route('/api/logs/clear', methods=['POST'])
def api_clear_logs():
    try:
        num_deleted = db.session.query(DowntimeLog).delete()
        db.session.commit()
        return jsonify({'success': True, 'deleted': num_deleted})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/stats')
def api_stats():
    total = Node.query.count()
    up = Node.query.filter_by(status='Node Up').count()
    down = Node.query.filter_by(status='Node Down').count()
    warning = Node.query.filter_by(status='Warning').count()
    unknown = Node.query.filter_by(status='Unknown').count()

    return jsonify({
        'total': total,
        'up': up,
        'down': down,
        'warning': warning,
        'unknown': unknown
    })


# ══════════════════════════════════════════════════════════
if __name__ == '__main__':
    from waitress import serve
    print("[SERVER] Starting waitress on 0.0.0.0:3000")
    serve(
        app,
        host='0.0.0.0',
        port=3000,
        threads=32,
        channel_timeout=120,
        connection_limit=200,
    )