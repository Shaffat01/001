from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta

BD_OFFSET = timedelta(hours=6)


def bd_time(utc_dt):
    if utc_dt is None:
        return None
    return utc_dt + BD_OFFSET


# Engine options config.py থেকে আসবে — models.py-তে hardcode নয়
db = SQLAlchemy()


class Node(db.Model):
    __tablename__ = 'nodes'

    id = db.Column(db.Integer, primary_key=True)
    node_name = db.Column(db.String(255), nullable=False)
    ip_address = db.Column(db.String(45), nullable=False, unique=True)
    response_time = db.Column(db.Float, default=0.0)
    packet_loss = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(20), default='Unknown')
    last_status_change = db.Column(db.DateTime, default=datetime.utcnow)
    last_checked = db.Column(db.DateTime, default=None, nullable=True)
    previous_status = db.Column(db.String(20), default='Unknown')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    enabled = db.Column(db.Boolean, default=True)

    consecutive_failures = db.Column(db.Integer, default=0)
    consecutive_successes = db.Column(db.Integer, default=0)
    last_ping_success = db.Column(db.Boolean, default=None, nullable=True)

    __table_args__ = (
        db.Index('idx_nodes_enabled', 'enabled'),
        db.Index('idx_nodes_status', 'status'),
    )

    def to_dict(self):
        now = datetime.utcnow()
        since_change = now - self.last_status_change if self.last_status_change else None

        if since_change:
            total_seconds = int(since_change.total_seconds())
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60

            if days > 0:
                since_str = f"{days}d {hours}h {minutes}m"
            elif hours > 0:
                since_str = f"{hours}h {minutes}m"
            elif minutes > 0:
                since_str = f"{minutes} minutes"
            else:
                since_str = f"{seconds} seconds"
        else:
            since_str = "N/A"

        return {
            'id': self.id,
            'node_name': self.node_name,
            'ip_address': self.ip_address,
            'response_time': self.response_time,
            'response_time_display': f"{int(self.response_time)} ms" if self.response_time is not None else "N/A",
            'packet_loss': self.packet_loss,
            'packet_loss_display': f"{int(self.packet_loss)} %" if self.packet_loss is not None else "N/A",
            'status': self.status,
            'last_status_change': bd_time(self.last_status_change).isoformat() if self.last_status_change else None,
            'since_last_change': since_str,
            'since_last_change_seconds': int(since_change.total_seconds()) if since_change else 0,
            'last_checked': self.last_checked.isoformat() if self.last_checked else None,
            'enabled': self.enabled,
            'consecutive_failures': self.consecutive_failures,
            'consecutive_successes': self.consecutive_successes
        }

    def __repr__(self):
        return f'<Node {self.node_name} ({self.ip_address})>'


class DowntimeLog(db.Model):
    __tablename__ = 'downtime_logs'

    id = db.Column(db.Integer, primary_key=True)
    node_id = db.Column(db.Integer, db.ForeignKey('nodes.id'), nullable=False)
    node_name = db.Column(db.String(255), nullable=False)
    ip_address = db.Column(db.String(45), nullable=False)
    status_change = db.Column(db.String(20), nullable=False)
    previous_status = db.Column(db.String(20), default='Unknown')
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    downtime_duration = db.Column(db.Float, default=None, nullable=True)
    is_resolved = db.Column(db.Boolean, default=False)

    __table_args__ = (
        db.Index('idx_logs_timestamp', timestamp.desc()),
        db.Index('idx_logs_node_id_ts', 'node_id', timestamp.desc()),
        db.Index('idx_logs_status_change', 'status_change'),
    )

    def to_dict(self):
        duration_str = None
        if self.downtime_duration:
            total_seconds = int(self.downtime_duration)
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            if days > 0:
                duration_str = f"{days}d {hours}h {minutes}m {seconds}s"
            elif hours > 0:
                duration_str = f"{hours}h {minutes}m {seconds}s"
            elif minutes > 0:
                duration_str = f"{minutes}m {seconds}s"
            else:
                duration_str = f"{seconds}s"

        return {
            'id': self.id,
            'node_id': self.node_id,
            'node_name': self.node_name,
            'ip_address': self.ip_address,
            'status_change': self.status_change,
            'previous_status': self.previous_status,
            'timestamp': bd_time(self.timestamp).isoformat() if self.timestamp else None,
            'timestamp_display': bd_time(self.timestamp).strftime('%Y-%m-%d %H:%M:%S') if self.timestamp else 'N/A',
            'downtime_duration': self.downtime_duration,
            'downtime_duration_display': duration_str,
            'is_resolved': self.is_resolved
        }

    def __repr__(self):
        return f'<DowntimeLog {self.node_name} {self.status_change} at {self.timestamp}>'