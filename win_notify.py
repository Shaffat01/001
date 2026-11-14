"""Desktop notification module — works on Windows and Linux (via plyer)."""
import platform
import threading

SYSTEM = platform.system().lower()
IS_WINDOWS = SYSTEM == 'windows'
IS_LINUX = SYSTEM == 'linux'


def _play_alert_sound():
    if not IS_WINDOWS:
        return
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:
        pass


def _play_up_sound():
    if not IS_WINDOWS:
        return
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        pass


def send_desktop_notification(title, message):
    """Send desktop notification (Windows toast / Linux notify-send via plyer)."""
    if not (IS_WINDOWS or IS_LINUX):
        return

    def _notify():
        try:
            from plyer import notification
            notification.notify(
                title=title, message=message,
                app_name='NetMonitor Pro', timeout=10
            )
        except Exception as e:
            # Linux server (headless) — silently skip; browser SSE handles UI
            pass

    threading.Thread(target=_notify, daemon=True).start()


def notify_node_down(node_name, ip_address):
    send_desktop_notification(
        '🔴 Node DOWN - NetMonitor Pro',
        f'{node_name} ({ip_address}) is DOWN!'
    )
    threading.Thread(target=_play_alert_sound, daemon=True).start()


def notify_node_up(node_name, ip_address, duration_str=None):
    duration_info = f' | Down for: {duration_str}' if duration_str else ''
    send_desktop_notification(
        '🟢 Node UP - NetMonitor Pro',
        f'{node_name} ({ip_address}) is back UP!{duration_info}'
    )
    threading.Thread(target=_play_up_sound, daemon=True).start()


def notify_warning(node_name, ip_address):
    send_desktop_notification(
        '🟡 Warning - NetMonitor Pro',
        f'{node_name} ({ip_address}) - High packet loss/latency'
    )
    threading.Thread(target=_play_alert_sound, daemon=True).start()