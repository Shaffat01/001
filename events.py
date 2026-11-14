"""In-process pub/sub for Server-Sent Events (no Redis needed)."""
import queue
import threading

_subscribers = []
_lock = threading.Lock()


def subscribe():
    """New subscriber queue."""
    q = queue.Queue(maxsize=300)
    with _lock:
        _subscribers.append(q)
    return q


def unsubscribe(q):
    with _lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


def publish(event: dict):
    """Non-blocking publish to all subscribers."""
    with _lock:
        dead = []
        for q in _subscribers:
            try:
                q.put_nowait(event)
            except queue.Full:
                dead.append(q)
        for q in dead:
            try:
                _subscribers.remove(q)
            except ValueError:
                pass


def subscriber_count():
    with _lock:
        return len(_subscribers)