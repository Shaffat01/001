import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))

# .env load — project root
load_dotenv(os.path.join(basedir, '.env'))

# ============ DB URL ============
_db_url = os.environ.get('DATABASE_URL') or \
    'postgresql://postgres:postgres@localhost:5432/netmonitor'

# SQLite vs PostgreSQL — engine options আলাদা
if _db_url.startswith('sqlite'):
    _engine_opts = {
        'connect_args': {'timeout': 30, 'check_same_thread': False},
        'pool_pre_ping': True,
    }
else:
    _engine_opts = {
        'pool_size': 10,
        'max_overflow': 20,
        'pool_pre_ping': True,
        'pool_recycle': 300,
    }


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'network-monitor-secret-key-2024'

    SQLALCHEMY_DATABASE_URI = _db_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = _engine_opts

    UPLOAD_FOLDER = os.path.join(basedir, 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024

    # ============ POLLING ============
    PING_INTERVAL = 15
    PING_TIMEOUT = 1
    PING_COUNT = 2
    PING_PRIVILEGED = True
    MAX_CONCURRENT_PINGS = 350

    # ============ FLAP PROTECTION ============
    FAIL_THRESHOLD = 3
    SUCCESS_THRESHOLD = 2
    WARNING_PACKET_LOSS = 75
    WARNING_THRESHOLD = 3

    SCHEDULER_API_ENABLED = True