import time
from datetime import datetime

from extensions import db
from models import (
    AlertLog,
    AlertRule,
    Device,
    DeviceInterface,
    InterfaceTraffic,
    Performance,
)
from utils.notify import send_email_alert
from utils.snmp_helper import get_device_performance, get_real_interface_data, snmp_probe


COMPARISONS = {
    '>': lambda current, threshold: current > threshold,
    '<': lambda current, threshold: current < threshold,
    '=': lambda current, threshold: current == threshold,
    '>=': lambda current, threshold: current >= threshold,
    '<=': lambda current, threshold: current <= threshold,
    '!=': lambda current, threshold: current != threshold,
}


def build_alert_fingerprint(device_id, rule_id):
    return f'{device_id}:{rule_id}'


def build_metric_display(rule, current_val, threshold):
    if rule.metric == 'device_status':
        current_text = '离线' if current_val == 0 else '在线'
        threshold_text = '离线' if threshold == 0 else '在线'
        return current_text, threshold_text

    return f'{current_val}%', f'{threshold}%'


def build_alert_message(rule, current_val, threshold):
    current_text, threshold_text = build_metric_display(rule, current_val, threshold)
    return (
        f'触发规则「{rule.name}」: '
        f'当前 {rule.metric} ({current_text}) {rule.condition} 阈值 ({threshold_text})'
    )


def get_rule_current_value(rule, device, cpu, mem):
    if rule.metric == 'cpu_usage':
        return cpu
    if rule.metric == 'mem_usage':
        return mem
    if rule.metric == 'device_status':
        return 1 if device.status == 'online' else 0
    return None


def find_active_alert(device_id, rule_id):
    fingerprint = build_alert_fingerprint(device_id, rule_id)
    return (
        AlertLog.query.filter_by(fingerprint=fingerprint, status='Unresolved')
        .order_by(AlertLog.created_at.desc())
        .first()
    )


def resolve_active_alert(device_id, rule_id, current_time):
    active_log = find_active_alert(device_id, rule_id)
    if not active_log:
        return

    active_log.status = 'Resolved'
    active_log.resolved_at = current_time


def upsert_active_alert(rule, device, message, current_time):
    active_log = find_active_alert(device.id, rule.id)

    if active_log:
        active_log.device_name = device.name
        active_log.message = message
        active_log.last_triggered_at = current_time
        return

    db.session.add(
        AlertLog(
            device_id=device.id,
            rule_id=rule.id,
            fingerprint=build_alert_fingerprint(device.id, rule.id),
            device_name=device.name,
            level='Critical',
            message=message,
            status='Unresolved',
            created_at=current_time,
            last_triggered_at=current_time,
        )
    )
    send_email_alert(f'{device.name} 告警', message)


def check_alert_rules(app, device, cpu, mem, current_time):
    _ = app

    try:
        rules = AlertRule.query.all()

        for rule in rules:
            if not rule.enabled:
                resolve_active_alert(device.id, rule.id, current_time)
                continue

            current_val = get_rule_current_value(rule, device, cpu, mem)
            if current_val is None:
                continue

            try:
                threshold = float(rule.threshold)
            except (TypeError, ValueError):
                continue

            comparator = COMPARISONS.get(rule.condition)
            if comparator is None:
                continue

            if comparator(current_val, threshold):
                message = build_alert_message(rule, current_val, threshold)
                print(f'  [告警] {message}')
                upsert_active_alert(rule, device, message, current_time)
            else:
                resolve_active_alert(device.id, rule.id, current_time)

    except Exception as error:
        print(f'  [告警模块异常] {error}')


def collect_performance_task(app):
    started_at = time.perf_counter()
    print('[采集] START')

    with app.app_context():
        devices = Device.query.all()

        for device in devices:
            current_time = datetime.now()
            community = (device.community or 'snmpread@123').strip()
            cpu_val = None
            mem_val = None

            try:
                if not snmp_probe(device.ip, community):
                    raise RuntimeError('SNMP probe failed (no response)')

                if device.status != 'online':
                    print(f'  [状态变更] 设备 {device.name} 上线了')

                device.status = 'online'
                device.last_seen = current_time

                metrics = get_device_performance(device.ip, community)
                cpu_val = metrics.get('cpu_usage')
                mem_val = metrics.get('mem_usage')

                if cpu_val is not None or mem_val is not None:
                    db.session.add(
                        Performance(
                            device_id=device.id,
                            cpu_usage=cpu_val if cpu_val is not None else 0,
                            mem_usage=mem_val if mem_val is not None else 0,
                            timestamp=current_time,
                        )
                    )

                real_interfaces = get_real_interface_data(device.ip, community) or []
                for real_if in real_interfaces:
                    db_if = DeviceInterface.query.filter_by(
                        device_id=device.id,
                        index=real_if['index'],
                    ).first()

                    if not db_if:
                        db.session.add(
                            DeviceInterface(
                                device_id=device.id,
                                name=real_if['name'],
                                index=real_if['index'],
                                speed=real_if['speed'],
                                last_in_octets=real_if['in_octets'],
                                last_out_octets=real_if['out_octets'],
                                last_check_time=current_time,
                            )
                        )
                        continue

                    if db_if.last_check_time:
                        time_delta = (current_time - db_if.last_check_time).total_seconds()
                        if time_delta > 0:
                            delta_in = real_if['in_octets'] - (db_if.last_in_octets or 0)
                            delta_out = real_if['out_octets'] - (db_if.last_out_octets or 0)

                            if delta_in >= 0 and delta_out >= 0:
                                in_rate = round((delta_in * 8) / (1024 * 1024) / time_delta, 4)
                                out_rate = round((delta_out * 8) / (1024 * 1024) / time_delta, 4)
                                db.session.add(
                                    InterfaceTraffic(
                                        interface_id=db_if.id,
                                        in_rate=in_rate,
                                        out_rate=out_rate,
                                        timestamp=current_time,
                                    )
                                )
                                print(
                                    f'  > 接口 {db_if.name}: In={in_rate}Mbps, Out={out_rate}Mbps'
                                )

                    db_if.last_in_octets = real_if['in_octets']
                    db_if.last_out_octets = real_if['out_octets']
                    db_if.last_check_time = current_time

                check_alert_rules(app, device, cpu_val, mem_val, current_time)
                db.session.commit()
                print(f'  > {device.name}: 在线, CPU={cpu_val}%, MEM={mem_val}%')

            except Exception as error:
                db.session.rollback()

                if device.status == 'online':
                    print(f'  [状态变更] 设备 {device.name} 离线了，原因: {error}')

                device.status = 'offline'

                try:
                    check_alert_rules(app, device, cpu_val, mem_val, current_time)
                    db.session.commit()
                except Exception:
                    db.session.rollback()

                print(f'  ! 采集异常 {device.ip}: {error}')

    elapsed = time.perf_counter() - started_at
    print(f'[采集] END {elapsed:.2f}s')
