# config.py
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'net-monitor-backend-secret-key')
    ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'chenglei')
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', '123456')
    AUTH_TOKEN_EXPIRES = int(os.environ.get('AUTH_TOKEN_EXPIRES', 86400))
    COLLECTION_INTERVAL_SECONDS = int(os.environ.get('COLLECTION_INTERVAL_SECONDS', 250))
    REPORT_PDF_RENDERER = os.environ.get('REPORT_PDF_RENDERER', 'auto').lower()
    REPORT_BROWSER_EXECUTABLE = os.environ.get('REPORT_BROWSER_EXECUTABLE', '').strip()
    REPORT_BROWSER_TIMEOUT_SECONDS = int(os.environ.get('REPORT_BROWSER_TIMEOUT_SECONDS', 60))
    # 数据库文件会生成在项目根目录下，叫 app.db
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(BASE_DIR, 'app.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {
            "check_same_thread": False,
            "timeout": 30,          # 等待锁 30 秒
        },
        "pool_pre_ping": True,
    }
