import asyncio
import threading
import platform
import subprocess
import re
from datetime import datetime
from icmplib import async_multiping, ping as sync_ping
from models import db, Node, DowntimeLog
from win_notify import notify_node_down, notify_node_up, notify_warning
from events import publish


class NetworkMonitor:
    def __init__(self, app=None):
        self.app = app
        self.is_running = False
        self._lock = threading.Lock()
        self._loop = None
        self._loop_thread = None
        self._use_system_ping = False

    def init_app(self, app):
        self.app = app
        self._start_event_loop()
        self._test_icmp_capability()

    def _test_icmp_capability(self):
        try:
            result = sync_ping(
                '127.0.0.1', count=1, timeout=1,
                privileged=self.app.config.get('PING_PRIVILEGED', True)
            )
            if result.is_alive:
                print("[INIT] icmplib working in privileged mode (fast async)")
                self._use_system_ping = False
                return
        except Exception as e:
            print(f"[INIT] icmplib privileged failed: {e}")

        try:
            result = sync_ping('127.0.0.1', count=1, timeout=1, privileged=False)
            if result.is_alive:
                print("[INIT] icmplib working in non-privileged mode")
                self.app.config['PING_PRIVILEGED'] = False
                self._use_system_ping = False
                return
        except Exception as e:
            print(f"[INIT] icmplib non-privileged failed: {e}")

        print("=" * 60)
        print("⚠️  WARNING: SYSTEM PING FALLBACK ACTIVE — 20-50x SLOWER!")
        if platform.system().lower() == 'windows':
            print("   Fix: Run app.py as Administrator (right click → Run as admin)")
        else:
            print("   Fix: sudo setcap cap_net_raw+ep $(readlink -f $(which python3))")
            print("        OR use systemd AmbientCapabilities=CAP_NET_RAW")
        print("=" * 60)
        self._use_system_ping = True

    def _start_event_loop(self):
        def run_loop():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()

        self._loop_thread = threading.Thread(target=run_loop, daemon=True)
        self._loop_thread.start()

        import time
        while self._loop is None:
            time.sleep(0.05)

    def _system_ping(self, ip_address, count=2, timeout=1):
        system = platform.system().lower()
        try:
            if system == 'windows':
                cmd = ['ping', '-n', str(count), '-w', str(timeout * 1000), ip_address]
                creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)
            else:
                cmd = ['ping', '-c', str(count), '-W', str(timeout), ip_address]
                creationflags = 0

            result = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=(timeout * count) + 3,
                creationflags=creationflags
            )

            output = result.stdout
            packet_loss = 100.0
            response_time = 0.0
            is_alive = False

            if system == 'windows':
                if "unreachable" in output.lower() or "timed out" in output.lower() and "100% loss" in output:
                    is_alive = False
                    packet_loss = 100.0
                else:
                    loss_match = re.search(r'\((\d+)% loss\)', output)
                    if loss_match:
                        packet_loss = float(loss_match.group(1))
                    time_match = re.search(r'Average = (\d+)ms', output)
                    if time_match:
                        response_time = float(time_match.group(1))
                    is_alive = packet_loss < 100.0
            else:
                loss_match = re.search(r'(\d+)% packet loss', output)
                if loss_match:
                    packet_loss = float(loss_match.group(1))
                time_match = re.search(r'rtt min/avg/max/mdev = [\d.]+/([\d.]+)/', output)
                if time_match:
                    response_time = float(time_match.group(1))
                is_alive = packet_loss < 100.0

            if not is_alive:
                response_time = 0.0
                packet_loss = 100.0

            return {
                'address': ip_address,
                'is_alive': is_alive,
                'avg_rtt': response_time,
                'packet_loss': packet_loss / 100.0
            }

        except subprocess.TimeoutExpired:
            return {'address': ip_address, 'is_alive': False, 'avg_rtt': 0.0, 'packet_loss': 1.0}
        except Exception:
            return {'address': ip_address, 'is_alive': False, 'avg_rtt': 0.0, 'packet_loss': 1.0}

    def _evaluate_status(self, node, ping_success, packet_loss, response_time):
        config = self.app.config
        FAIL_THRESHOLD = config.get('FAIL_THRESHOLD', 3)
        SUCCESS_THRESHOLD = config.get('SUCCESS_THRESHOLD', 2)

        old_status = node.status
        new_status = old_status

        if ping_success:
            node.consecutive_successes = (node.consecutive_successes or 0) + 1
            node.consecutive_failures = 0
        else:
            node.consecutive_failures = (node.consecutive_failures or 0) + 1
            node.consecutive_successes = 0

        if ping_success:
            if old_status in ('Node Down', 'Unknown'):
                if node.consecutive_successes >= SUCCESS_THRESHOLD:
                    new_status = 'Node Up'
            else:
                new_status = 'Node Up'
        else:
            if old_status in ('Node Up', 'Unknown'):
                if node.consecutive_failures >= FAIL_THRESHOLD:
                    new_status = 'Node Down'
            else:
                new_status = 'Node Down'

        node.response_time = response_time
        node.packet_loss = packet_loss
        node.last_ping_success = ping_success
        node.last_checked = datetime.utcnow()

        if new_status != old_status:
            node.previous_status = old_status
            node.status = new_status
            node.last_status_change = datetime.utcnow()

            print(f"[STATUS CHANGE] {node.node_name} ({node.ip_address}): "
                  f"{old_status} -> {new_status}")

            # Windows/Linux desktop notification (non-blocking)
            def fire_notification(n_name, n_ip, n_status, o_status):
                try:
                    if n_status == 'Node Down':
                        notify_node_down(n_name, n_ip)
                    elif n_status == 'Node Up' and o_status in ('Node Down', 'Warning'):
                        notify_node_up(n_name, n_ip)
                    elif n_status == 'Warning':
                        notify_warning(n_name, n_ip)
                except Exception as e:
                    print(f"[NOTIFY ERROR] {e}")

            threading.Thread(
                target=fire_notification,
                args=(node.node_name, node.ip_address, new_status, old_status),
                daemon=True
            ).start()

            # Downtime logging
            try:
                if new_status == 'Node Down':
                    db.session.add(DowntimeLog(
                        node_id=node.id, node_name=node.node_name,
                        ip_address=node.ip_address,
                        status_change='Node Down', previous_status=old_status,
                        timestamp=datetime.utcnow(), is_resolved=False
                    ))

                elif new_status == 'Node Up' and old_status in ('Node Down', 'Warning'):
                    unresolved = DowntimeLog.query.filter_by(
                        node_id=node.id, is_resolved=False
                    ).order_by(DowntimeLog.timestamp.desc()).first()

                    if unresolved:
                        duration = (datetime.utcnow() - unresolved.timestamp).total_seconds()
                        unresolved.is_resolved = True
                        unresolved.downtime_duration = duration

                        db.session.add(DowntimeLog(
                            node_id=node.id, node_name=node.node_name,
                            ip_address=node.ip_address,
                            status_change='Node Up', previous_status=old_status,
                            timestamp=datetime.utcnow(),
                            downtime_duration=duration, is_resolved=True
                        ))

                elif new_status == 'Warning':
                    db.session.add(DowntimeLog(
                        node_id=node.id, node_name=node.node_name,
                        ip_address=node.ip_address,
                        status_change='Warning', previous_status=old_status,
                        timestamp=datetime.utcnow(), is_resolved=False
                    ))
            except Exception as e:
                print(f"[LOG ERROR] Failed to save downtime log: {e}")

            # SSE publish — instant UI update
            try:
                publish({
                    'type': 'node_status',
                    'data': {
                        'id': node.id,
                        'node_name': node.node_name,
                        'ip_address': node.ip_address,
                        'old_status': old_status,
                        'new_status': new_status,
                        'timestamp': datetime.utcnow().isoformat(),
                    }
                })
            except Exception:
                pass

    def ping_single_node(self, node_id):
        with self.app.app_context():
            node = db.session.get(Node, node_id)
            if not node or not node.enabled:
                return

            try:
                if self._use_system_ping:
                    result = self._system_ping(
                        node.ip_address,
                        count=self.app.config.get('PING_COUNT', 2),
                        timeout=self.app.config.get('PING_TIMEOUT', 1)
                    )
                    response_time = round(result['avg_rtt'], 1)
                    packet_loss = round(result['packet_loss'] * 100, 1)
                    ping_success = result['is_alive']
                else:
                    result = sync_ping(
                        node.ip_address,
                        count=self.app.config.get('PING_COUNT', 2),
                        interval=0.2,
                        timeout=self.app.config.get('PING_TIMEOUT', 1),
                        privileged=self.app.config.get('PING_PRIVILEGED', True)
                    )
                    response_time = round(result.avg_rtt, 1) if result.is_alive else 0.0
                    packet_loss = round(result.packet_loss * 100, 1)
                    ping_success = result.is_alive

            except Exception as e:
                print(f"Ping error for {node.ip_address}: {e}")
                response_time = 0.0
                packet_loss = 100.0
                ping_success = False

            self._evaluate_status(node, ping_success, packet_loss, response_time)
            db.session.commit()

    def monitor_all_nodes(self):
        with self._lock:
            if self.is_running:
                print("[MONITOR] Previous cycle still running, skipping...")
                return
            self.is_running = True

        start_time = datetime.utcnow()

        try:
            with self.app.app_context():
                nodes = Node.query.filter_by(enabled=True).all()
                if not nodes:
                    return

                ip_to_nodes = {}
                for node in nodes:
                    ip_to_nodes.setdefault(node.ip_address, []).append(node)

                ip_list = list(ip_to_nodes.keys())

                if self._use_system_ping:
                    results = self._parallel_system_ping(ip_list)
                else:
                    future = asyncio.run_coroutine_threadsafe(
                        self._async_ping_all(ip_list), self._loop
                    )
                    raw_results = future.result(timeout=120)

                    results = [{
                        'address': r.address,
                        'is_alive': r.is_alive,
                        'avg_rtt': r.avg_rtt if r.is_alive else 0.0,
                        'packet_loss': r.packet_loss
                    } for r in raw_results]

                for result in results:
                    for node in ip_to_nodes.get(result['address'], []):
                        self._evaluate_status(
                            node, result['is_alive'],
                            round(result['packet_loss'] * 100, 1),
                            round(result['avg_rtt'], 1) if result['is_alive'] else 0.0
                        )

                db.session.commit()

                # Publish fresh stats via SSE
                try:
                    publish({
                        'type': 'stats',
                        'data': {
                            'total': Node.query.count(),
                            'up': Node.query.filter_by(status='Node Up').count(),
                            'down': Node.query.filter_by(status='Node Down').count(),
                            'unknown': Node.query.filter_by(status='Unknown').count(),
                            'warning': Node.query.filter_by(status='Warning').count(),
                        }
                    })
                except Exception:
                    pass

                elapsed = (datetime.utcnow() - start_time).total_seconds()
                print(f"[MONITOR] Pinged {len(ip_list)} nodes in {elapsed:.1f}s")

        except Exception as e:
            print(f"[MONITOR ERROR] {e}")
            import traceback
            traceback.print_exc()
        finally:
            with self._lock:
                self.is_running = False

    def _parallel_system_ping(self, ip_list):
        from concurrent.futures import ThreadPoolExecutor
        max_workers = min(100, len(ip_list))
        results = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_ip = {
                executor.submit(
                    self._system_ping, ip,
                    self.app.config.get('PING_COUNT', 2),
                    self.app.config.get('PING_TIMEOUT', 1)
                ): ip for ip in ip_list
            }

            for future in future_to_ip:
                try:
                    results.append(future.result(timeout=10))
                except Exception:
                    ip = future_to_ip[future]
                    results.append({'address': ip, 'is_alive': False, 'avg_rtt': 0.0, 'packet_loss': 1.0})

        return results

    async def _async_ping_all(self, ip_list):
        try:
            return await async_multiping(
                ip_list,
                count=self.app.config.get('PING_COUNT', 2),
                interval=0.2,
                timeout=self.app.config.get('PING_TIMEOUT', 1),
                concurrent_tasks=self.app.config.get('MAX_CONCURRENT_PINGS', 350),
                privileged=self.app.config.get('PING_PRIVILEGED', True)
            )
        except Exception as e:
            print(f"Async ping error: {e}")
            return []


monitor = NetworkMonitor()