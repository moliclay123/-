from datetime import datetime

from flask import Blueprint, jsonify, request
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import AlertLog, AlertRule, Device, DeviceInterface

alert_bp = Blueprint('alert', __name__)

ALLOWED_METRICS = {
    'cpu_usage',
    'mem_usage',
    'device_status',
    'interface_traffic_rate',
}
ALLOWED_CONDITIONS = {'>', '<', '=', '>=', '<=', '!='}


def resolve_rule_alerts(rule_id):
    current_time = datetime.now()
    (
        AlertLog.query.filter_by(rule_id=rule_id, status='Unresolved')
        .update(
            {
                AlertLog.status: 'Resolved',
                AlertLog.resolved_at: current_time,
            },
            synchronize_session=False,
        )
    )


def build_interface_lookup():
    rows = (
        db.session.query(
            DeviceInterface.id.label('interface_id'),
            DeviceInterface.name.label('interface_name'),
            DeviceInterface.device_id.label('device_id'),
            Device.name.label('device_name'),
        )
        .outerjoin(Device, Device.id == DeviceInterface.device_id)
        .all()
    )
    return {
        row.interface_id: {
            'interface_name': row.interface_name,
            'device_id': row.device_id,
            'device_name': row.device_name,
        }
        for row in rows
    }


def serialize_rule(rule, interface_lookup):
    interface_info = interface_lookup.get(rule.interface_id)
    return {
        'id': rule.id,
        'name': rule.name,
        'metric': rule.metric,
        'condition': rule.condition,
        'threshold': rule.threshold,
        'enabled': rule.enabled,
        'interface_id': rule.interface_id,
        'interface_name': interface_info['interface_name'] if interface_info else None,
        'device_id': interface_info['device_id'] if interface_info else None,
        'device_name': interface_info['device_name'] if interface_info else None,
    }


@alert_bp.route('/rules/list', methods=['GET'])
def get_rules():
    rules = AlertRule.query.order_by(AlertRule.id.desc()).all()
    interface_lookup = build_interface_lookup()
    data = [serialize_rule(rule, interface_lookup) for rule in rules]
    return jsonify({'code': 200, 'data': data})


@alert_bp.route('/rules/add', methods=['POST'])
def add_rule():
    data = request.get_json(silent=True) or {}

    name = (data.get('name') or '').strip()
    metric = (data.get('metric') or '').strip()
    condition = (data.get('condition') or data.get('operator') or '').strip()
    threshold = data.get('threshold')
    interface_id = data.get('interface_id')

    missing = []
    if not name:
        missing.append('name')
    if not metric:
        missing.append('metric')
    if not condition:
        missing.append('condition')
    if threshold in (None, ''):
        missing.append('threshold')

    if missing:
        return jsonify({'code': 400, 'msg': f"缺少字段: {', '.join(missing)}"}), 400

    if metric not in ALLOWED_METRICS:
        return jsonify({'code': 400, 'msg': '不支持的监控指标类型'}), 400

    if condition not in ALLOWED_CONDITIONS:
        return jsonify({'code': 400, 'msg': '不支持的比较运算符'}), 400

    try:
        normalized_threshold = float(str(threshold).strip())
    except Exception:
        return jsonify({'code': 400, 'msg': 'threshold 必须是数字'}), 400

    if metric == 'interface_traffic_rate':
        if interface_id in (None, ''):
            return jsonify({'code': 400, 'msg': '接口流量规则必须选择接口'}), 400

        try:
            interface_id = int(interface_id)
        except Exception:
            return jsonify({'code': 400, 'msg': 'interface_id 非法'}), 400

        interface = DeviceInterface.query.get(interface_id)
        if not interface:
            return jsonify({'code': 400, 'msg': '目标接口不存在'}), 400
        if normalized_threshold <= 0:
            return jsonify({'code': 400, 'msg': '接口流量阈值必须大于 0 Mbps'}), 400
    else:
        interface_id = None

    try:
        new_rule = AlertRule(
            name=name,
            metric=metric,
            condition=condition,
            threshold=normalized_threshold,
            enabled=True,
            interface_id=interface_id,
        )
        db.session.add(new_rule)
        db.session.commit()
        return jsonify({'code': 200, 'msg': '规则添加成功', 'data': {'id': new_rule.id}})
    except SQLAlchemyError as error:
        db.session.rollback()
        return jsonify({'code': 500, 'msg': f'数据库写入失败: {error}'}), 500
    except Exception as error:
        db.session.rollback()
        return jsonify({'code': 500, 'msg': f'服务器内部错误: {error}'}), 500


@alert_bp.route('/rules/delete', methods=['POST'])
def delete_rule():
    try:
        data = request.get_json(silent=True) or {}
        rule = AlertRule.query.get(data.get('id'))
        if not rule:
            return jsonify({'code': 404, 'msg': '规则不存在'})

        resolve_rule_alerts(rule.id)
        db.session.delete(rule)
        db.session.commit()
        return jsonify({'code': 200, 'msg': '删除成功'})
    except Exception as error:
        db.session.rollback()
        return jsonify({'code': 500, 'msg': str(error)})


@alert_bp.route('/logs/list', methods=['GET'])
def get_logs():
    latest_change = func.coalesce(
        AlertLog.resolved_at,
        AlertLog.last_triggered_at,
        AlertLog.created_at,
    )
    logs = AlertLog.query.order_by(latest_change.desc(), AlertLog.id.desc()).limit(50).all()
    data = []
    for log in logs:
        display_time = log.resolved_at or log.last_triggered_at or log.created_at
        data.append({
            'id': log.id,
            'time': display_time.strftime('%Y-%m-%d %H:%M:%S') if display_time else '',
            'device': log.device_name,
            'level': log.level,
            'message': log.message,
            'status': log.status,
        })
    return jsonify({'code': 200, 'data': data})


@alert_bp.route('/logs/clear', methods=['POST'])
def clear_logs():
    try:
        deleted_count = AlertLog.query.delete()
        db.session.commit()
        return jsonify({'code': 200, 'msg': f'已清空 {deleted_count} 条告警日志'})
    except Exception as error:
        db.session.rollback()
        return jsonify({'code': 500, 'msg': f'清空告警日志失败: {error}'}), 500


@alert_bp.route('/rules/status', methods=['POST'])
def update_rule_status():
    try:
        data = request.get_json(silent=True) or {}
        rule = AlertRule.query.get(data.get('id'))
        if not rule:
            return jsonify({'code': 404, 'msg': '规则不存在'})

        rule.enabled = bool(data.get('enabled'))
        if not rule.enabled:
            resolve_rule_alerts(rule.id)
        db.session.commit()
        return jsonify({'code': 200, 'msg': '状态更新成功'})
    except Exception as error:
        db.session.rollback()
        return jsonify({'code': 500, 'msg': str(error)})
