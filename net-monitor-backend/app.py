# app.py
from datetime import datetime
from flask import Flask
from sqlalchemy import inspect, text
from config import Config
from extensions import db, cors, scheduler
from models import Device, Performance, AlertRule, AlertLog
from task_runtime_v3 import collect_performance_task
from flask_cors import CORS


def register_scheduler_jobs(app):
    if scheduler.get_job('collect_performance_task') is None:
        print("准备注册采集任务")
        scheduler.add_job(
            func=collect_performance_task,
            trigger='interval',
            seconds=int(app.config.get('COLLECTION_INTERVAL_SECONDS', 250)),
            args=[app],
            id='collect_performance_task',
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(),
        )
        print("注册成功")


def start_scheduler(app):
    register_scheduler_jobs(app)
    if not scheduler.running:
        print("准备启动调度器")
        scheduler.start()
        print("调度器启动完成")


def cleanup_unused_tables():
    inspector = inspect(db.engine)
    if 'topology_links' not in inspector.get_table_names():
        return

    db.session.execute(text("DROP TABLE IF EXISTS topology_links"))
    db.session.commit()


def ensure_runtime_schema():
    inspector = inspect(db.engine)
    if 'alert_logs' not in inspector.get_table_names():
        return

    alert_log_columns = {column['name'] for column in inspector.get_columns('alert_logs')}
    missing_columns = {
        'device_id': 'INTEGER',
        'rule_id': 'INTEGER',
        'fingerprint': 'TEXT',
        'last_triggered_at': 'DATETIME',
        'resolved_at': 'DATETIME'
    }

    for column_name, column_type in missing_columns.items():
        if column_name not in alert_log_columns:
            db.session.execute(
                text(f"ALTER TABLE alert_logs ADD COLUMN {column_name} {column_type}")
            )

    db.session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_alert_logs_fingerprint_status "
            "ON alert_logs (fingerprint, status)"
        )
    )
    db.session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS idx_alert_logs_rule_status "
            "ON alert_logs (rule_id, status)"
        )
    )

    if 'alert_rules' in inspector.get_table_names():
        alert_rule_columns = {column['name'] for column in inspector.get_columns('alert_rules')}
        if 'interface_id' not in alert_rule_columns:
            db.session.execute(text("ALTER TABLE alert_rules ADD COLUMN interface_id INTEGER"))

        db.session.execute(
            text(
                "CREATE INDEX IF NOT EXISTS idx_alert_rules_metric_interface_enabled "
                "ON alert_rules (metric, interface_id, enabled)"
            )
        )

    db.session.commit()


def cleanup_alert_log_history():
    logs = (
        AlertLog.query.order_by(
            AlertLog.created_at.asc(),
            AlertLog.id.asc()
        ).all()
    )
    latest_unresolved = {}

    for log in logs:
        if not log.last_triggered_at:
            log.last_triggered_at = log.created_at

        if log.rule_id and log.device_id and not log.fingerprint:
            rule = AlertRule.query.get(log.rule_id)
            if rule and rule.metric == 'interface_traffic_rate' and rule.interface_id:
                log.fingerprint = f'{log.device_id}:{log.rule_id}:{rule.interface_id}'
            else:
                log.fingerprint = f'{log.device_id}:{log.rule_id}'

        if log.status != 'Unresolved':
            if not log.resolved_at:
                log.resolved_at = log.last_triggered_at or log.created_at
            continue

        dedupe_key = None
        if log.fingerprint:
            dedupe_key = ('fingerprint', log.fingerprint)
        elif log.device_id and log.message:
            dedupe_key = ('device_message', log.device_id, log.message)
        elif log.device_name and log.message:
            dedupe_key = ('name_message', log.device_name, log.message)

        if dedupe_key is None:
            continue

        previous = latest_unresolved.get(dedupe_key)
        current_time = log.last_triggered_at or log.created_at

        if previous is None:
            latest_unresolved[dedupe_key] = log
            continue

        previous_time = previous.last_triggered_at or previous.created_at
        if current_time >= previous_time:
            previous.status = 'Resolved'
            previous.resolved_at = current_time
            latest_unresolved[dedupe_key] = log
        else:
            log.status = 'Resolved'
            log.resolved_at = previous_time

    db.session.commit()


def create_app():
    app = Flask(__name__)
    CORS(
        app,
        resources={r"/api/*": {"origins": "http://localhost:5173"}},
        allow_headers=['Content-Type', 'Authorization']
    )
    app.config.from_object(Config)

    db.init_app(app)

    from api.auth import auth_bp, register_auth_guard
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    from api.report_runtime import report_bp
    app.register_blueprint(report_bp, url_prefix='/api/report')
    from api.alert_runtime import alert_bp
    app.register_blueprint(alert_bp, url_prefix='/api/alert')
    from api.topology import topology_bp
    app.register_blueprint(topology_bp, url_prefix='/api/topology')
    from api.dashboard import dashboard_bp
    app.register_blueprint(dashboard_bp, url_prefix='/api/dashboard')
    from api.device import device_bp
    app.register_blueprint(device_bp, url_prefix='/api/device')
    register_auth_guard(app)

    return app


app = create_app()

# 每次启动时检查数据库表是否存在，不存在则创建
with app.app_context():
    db.create_all()
    cleanup_unused_tables()
    ensure_runtime_schema()
    cleanup_alert_log_history()
    # --- SQLite 并发优化：WAL + busy_timeout ---
    db.session.execute(text("PRAGMA journal_mode=WAL;"))
    db.session.execute(text("PRAGMA synchronous=NORMAL;"))
    db.session.execute(text("PRAGMA busy_timeout=30000;"))
    db.session.commit()
    print("数据库表结构已检查/创建完成。")

if __name__ == '__main__':
    start_scheduler(app)
    app.run(host='0.0.0.0', port=5000, debug=True)
