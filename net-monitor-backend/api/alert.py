from datetime import datetime

from flask import Blueprint, jsonify, request
from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import AlertRule, AlertLog

alert_bp = Blueprint('alert', __name__)


def resolve_rule_alerts(rule_id):
    current_time = datetime.now()
    (
        AlertLog.query.filter_by(rule_id=rule_id, status='Unresolved')
        .update(
            {
                AlertLog.status: 'Resolved',
                AlertLog.resolved_at: current_time
            },
            synchronize_session=False
        )
    )

# === 规则管理 (AlertRules) ===

@alert_bp.route('/rules/list', methods=['GET'])
def get_rules():
    rules = AlertRule.query.all()
    # 简单的转换
    data = []
    for r in rules:
        data.append({
            'id': r.id,
            'name': r.name,
            'metric': r.metric,
            'condition': r.condition,
            'threshold': r.threshold,
            'enabled': r.enabled
        })
    return jsonify({'code': 200, 'data': data})

# api/alert.py 中的 delete_rule
@alert_bp.route('/rules/add', methods=['POST'])
def add_rule():
    # 1) 更稳地拿到 JSON
    data = request.get_json(silent=True) or {}

    # 2) 兼容字段名 + 校验必填
    name = data.get('name')
    metric = data.get('metric')
    condition = data.get('condition') or data.get('operator') or data.get('op') or data.get('compare')
    threshold = data.get('threshold')

    missing = [k for k, v in {
        "name": name,
        "metric": metric,
        "condition": condition,
        "threshold": threshold
    }.items() if v is None or (isinstance(v, str) and v.strip() == "")]

    if missing:
        return jsonify({
            "code": 400,
            "msg": f"缺少字段: {', '.join(missing)}",
            "received": data
        }), 400

    # 3) condition 合法性校验
    allowed_conditions = {">", "<", "=", ">=", "<=", "!="}
    if condition not in allowed_conditions:
        return jsonify({
            "code": 400,
            "msg": f"condition 不合法，应为 {sorted(list(allowed_conditions))}",
            "received_condition": condition
        }), 400

    # 4) threshold 尝试转数字（如果你要支持 'offline' 这种字符串阈值，可把这段改掉）
    try:
        # int/float 都支持
        if isinstance(threshold, str):
            threshold = threshold.strip()
        threshold = float(threshold)
    except Exception:
        return jsonify({
            "code": 400,
            "msg": "threshold 必须是数字（如 80、20.5）",
            "received_threshold": data.get("threshold")
        }), 400

    # 5) 入库 + 异常处理
    try:
        new_rule = AlertRule(
            name=name,
            metric=metric,
            condition=condition,
            threshold=threshold,
            enabled=True
        )
        db.session.add(new_rule)
        db.session.commit()

        return jsonify({
            "code": 200,
            "msg": "规则添加成功",
            "data": {"id": new_rule.id}
        })
    except SQLAlchemyError as e:
        db.session.rollback()
        return jsonify({
            "code": 500,
            "msg": "数据库写入失败",
            "error": str(e)
        }), 500
    except Exception as e:
        db.session.rollback()
        return jsonify({
            "code": 500,
            "msg": "服务器内部错误",
            "error": str(e)
        }), 500

@alert_bp.route('/rules/delete', methods=['POST'])
def delete_rule():
    try:
        data = request.json
        rule_id = data.get('id')  # 确保这里获取的是 'id'

        rule = AlertRule.query.get(rule_id)
        if rule:
            resolve_rule_alerts(rule.id)
            db.session.delete(rule)
            db.session.commit()
            return jsonify({'code': 200, 'msg': '删除成功'})
        else:
            return jsonify({'code': 404, 'msg': '规则不存在'})
    except Exception as e:
        return jsonify({'code': 500, 'msg': str(e)})
# === 告警日志 (AlertLogs) ===

@alert_bp.route('/logs/list', methods=['GET'])
def get_logs():
    # 按时间倒序，最新的在最前
    latest_change = func.coalesce(
        AlertLog.resolved_at,
        AlertLog.last_triggered_at,
        AlertLog.created_at
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
            'status': log.status
        })
    return jsonify({'code': 200, 'data': data})


@alert_bp.route('/logs/clear', methods=['POST'])
def clear_logs():
    try:
        deleted_count = AlertLog.query.delete()
        db.session.commit()
        return jsonify({
            'code': 200,
            'msg': f'已清空 {deleted_count} 条告警日志'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'code': 500,
            'msg': f'清空告警日志失败: {str(e)}'
        }), 500


# api/alert.py (追加在末尾)

@alert_bp.route('/rules/status', methods=['POST'])
def update_rule_status():
    """
    切换规则的启用/禁用状态
    """
    try:
        data = request.json
        rule_id = data.get('id')
        enabled = data.get('enabled')  # true or false

        rule = AlertRule.query.get(rule_id)
        if rule:
            rule.enabled = enabled
            if not enabled:
                resolve_rule_alerts(rule.id)
            db.session.commit()
            return jsonify({'code': 200, 'msg': '状态更新成功'})
        else:
            return jsonify({'code': 404, 'msg': '规则不存在'})

    except Exception as e:
        return jsonify({'code': 500, 'msg': str(e)})
