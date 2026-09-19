from flask import Blueprint, jsonify
from extensions import db
from models import Device, Performance, AlertLog
from sqlalchemy import func

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/stats', methods=['GET'])
def get_dashboard_stats():
    """
    获取仪表盘顶部的 4 个 KPI 数字
    """
    total_count = Device.query.count()
    online_count = Device.query.filter_by(status='online').count()
    offline_count = Device.query.filter_by(status='offline').count()
    alert_count = AlertLog.query.filter_by(status='Unresolved').count()

    return jsonify({
        'code': 200,
        'data': {
            'total': total_count,
            'online': online_count,
            'offline': offline_count,
            'alerts': alert_count
        }
    })


@dashboard_bp.route('/chart/trend', methods=['GET'])
def get_trend_chart():
    """
    获取全网流量/负载趋势图数据 (用于前端折线图)
    """
    bucket = func.strftime('%Y-%m-%d %H:%M', Performance.timestamp)
    perfs = db.session.query(
        bucket.label('bucket'),
        func.round(func.avg(Performance.cpu_usage), 1).label('avg_cpu'),
        func.round(func.avg(Performance.mem_usage), 1).label('avg_mem')
    ).group_by(bucket).order_by(bucket.desc()).limit(20).all()

    if not perfs:
        return jsonify({'code': 200, 'data': {'x': [], 'y_cpu': [], 'y_mem': []}})

    perfs = list(reversed(perfs))

    x_data = [p.bucket[11:16] if p.bucket else '' for p in perfs]
    y_cpu = [float(p.avg_cpu or 0) for p in perfs]
    y_mem = [float(p.avg_mem or 0) for p in perfs]

    return jsonify({
        'code': 200,
        'data': {
            'x': x_data,
            'y_cpu': y_cpu,
            'y_mem': y_mem
        }
    })
