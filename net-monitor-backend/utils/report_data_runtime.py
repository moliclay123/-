import json
import traceback
from datetime import datetime

from sqlalchemy import desc, func

from extensions import db
from models import (
    AlertLog,
    Device,
    DeviceInterface,
    InterfaceTraffic,
    Performance,
    TopologySnapshot,
)


def is_core_device_name(name):
    normalized = (name or '').lower()
    return 'core' in normalized or '核心' in (name or '')


def infer_topology_category(name):
    normalized = (name or '').lower()
    if 'core' in normalized or '鏍稿績' in (name or ''):
        return 0
    if 'agg' in normalized or '姹囪仛' in (name or ''):
        return 1
    return 2


OVERVIEW_LAYER_LABELS = {
    'core': '核心设备',
    'aggregation': '汇聚层设备',
    'access': '接入层设备',
}


def normalize_overview_layer(layer):
    value = str(layer or 'core').strip().lower()
    if value not in OVERVIEW_LAYER_LABELS:
        return 'core'
    return value


def infer_overview_layer(name):
    normalized = (name or '').lower()
    raw_name = name or ''
    if 'core' in normalized or '核心' in raw_name:
        return 'core'
    if 'agg' in normalized or 'aggregation' in normalized or '汇聚' in raw_name:
        return 'aggregation'
    return 'access'


def build_overview_devices(selected_layer):
    devices = Device.query.order_by(Device.id.asc()).all()
    devices_by_layer = {
        'core': [],
        'aggregation': [],
        'access': [],
    }

    for device in devices:
        layer = infer_overview_layer(device.name)
        devices_by_layer[layer].append({
            'name': device.name,
            'ip': device.ip,
            'status': device.status,
        })

    normalized_layer = normalize_overview_layer(selected_layer)
    return {
        'selectedLayer': normalized_layer,
        'selectedLayerLabel': OVERVIEW_LAYER_LABELS[normalized_layer],
        'layerDevices': list(devices_by_layer.get(normalized_layer) or []),
        'devicesByLayer': devices_by_layer,
        'coreDevices': list(devices_by_layer['core']),
    }


def get_core_devices(limit=5):
    devices = Device.query.order_by(Device.id.asc()).all()
    matched = [device for device in devices if is_core_device_name(device.name)]
    if matched:
        return matched[:limit]

    online_devices = [device for device in devices if device.status == 'online']
    fallback = online_devices if online_devices else devices
    return fallback[:limit]


def build_alert_distribution(alerts):
    distribution = {'CPU高负载': 0, '内存高负载': 0, '设备离线': 0, '其他': 0}

    for alert in alerts:
        message = (alert.message or '').lower()
        if 'cpu' in message:
            distribution['CPU高负载'] += 1
        elif 'mem' in message:
            distribution['内存高负载'] += 1
        elif '离线' in (alert.message or '') or 'offline' in message:
            distribution['设备离线'] += 1
        else:
            distribution['其他'] += 1

    return distribution


def build_topology_payload():
    snapshot = TopologySnapshot.query.order_by(TopologySnapshot.created_at.desc()).first()
    default_payload = {
        'hasSnapshot': False,
        'generatedAt': None,
        'nodes': [],
        'links': [],
        'summary': {
            'nodeCount': 0,
            'linkCount': 0,
            'onlineCount': 0,
            'offlineCount': 0,
        },
        'coreLinks': [],
    }

    if not snapshot:
        return default_payload

    try:
        snapshot_nodes = json.loads(snapshot.nodes_json) if snapshot.nodes_json else []
        snapshot_links = json.loads(snapshot.links_json) if snapshot.links_json else []
    except Exception:
        return default_payload

    device_map = {device.ip: device for device in Device.query.all()}
    node_name_map = {}
    topology_nodes = []
    used_ips = set()

    for node in snapshot_nodes:
        ip = node.get('id')
        if not ip:
            continue

        used_ips.add(ip)
        device = device_map.get(ip)
        name = node.get('name') or (device.name if device else ip)
        status = device.status if device else 'unknown'
        category = node.get('category')
        if category is None:
            category = infer_topology_category(name)
        node_name_map[ip] = name
        topology_nodes.append({
            'id': ip,
            'name': name,
            'status': status,
            'category': category,
        })

    topology_links = []
    for link in snapshot_links:
        source = link.get('source')
        target = link.get('target')
        if not source or not target:
            continue

        used_ips.add(source)
        used_ips.add(target)
        topology_links.append({
            'source': source,
            'target': target,
            'edge_label': link.get('edge_label', ''),
        })

    existing_ids = {node['id'] for node in topology_nodes}
    for ip in used_ips:
        if ip in existing_ids:
            continue
        device = device_map.get(ip)
        name = node_name_map.get(ip) or (device.name if device else ip)
        topology_nodes.append({
            'id': ip,
            'name': name,
            'status': device.status if device else 'unknown',
            'category': infer_topology_category(name),
        })

    online_count = sum(1 for node in topology_nodes if node.get('status') == 'online')
    offline_count = sum(1 for node in topology_nodes if node.get('status') == 'offline')

    core_link_rows = []
    for link in topology_links:
        source_device = device_map.get(link['source'])
        target_device = device_map.get(link['target'])
        source_name = node_name_map.get(link['source']) or (source_device.name if source_device else None)
        target_name = node_name_map.get(link['target']) or (target_device.name if target_device else None)
        core_link_rows.append({
            'sourceName': source_name or link['source'],
            'targetName': target_name or link['target'],
            'edgeLabel': link.get('edge_label') or '未标注',
        })

    prioritized_links = [
        row for row in core_link_rows
        if is_core_device_name(row['sourceName']) or is_core_device_name(row['targetName'])
    ]
    if not prioritized_links:
        prioritized_links = core_link_rows

    return {
        'hasSnapshot': True,
        'generatedAt': snapshot.created_at.isoformat(),
        'nodes': topology_nodes,
        'links': topology_links,
        'summary': {
            'nodeCount': len(topology_nodes),
            'linkCount': len(topology_links),
            'onlineCount': online_count,
            'offlineCount': offline_count,
        },
        'coreLinks': prioritized_links[:5],
    }


def get_report_data(start_time, end_time, overview_layer='core'):
    data = {
        'genTime': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'summary': {},
        'overview': {},
        'performance': {},
        'traffic': {},
        'alerts': {},
        'topology': build_topology_payload(),
        'suggestions': [],
    }

    try:
        total_devices = Device.query.count()
        online_devices = Device.query.filter_by(status='online').count()
        offline_devices = total_devices - online_devices

        overview_devices = build_overview_devices(overview_layer)
        data['overview'] = {
            'total': total_devices,
            'online': online_devices,
            'offline': offline_devices,
            **overview_devices,
        }

        performance_rows = (
            db.session.query(
                Performance.device_id,
                func.round(func.avg(Performance.cpu_usage), 1).label('avg_cpu'),
                func.max(Performance.cpu_usage).label('max_cpu'),
                func.round(func.avg(Performance.mem_usage), 1).label('avg_mem'),
                func.max(Performance.mem_usage).label('max_mem'),
            )
            .filter(Performance.timestamp.between(start_time, end_time))
            .group_by(Performance.device_id)
            .all()
        )

        device_name_map = {device.id: device.name for device in Device.query.all()}
        top_cpu = []
        top_mem = []
        for row in performance_rows:
            device_name = device_name_map.get(row.device_id, f'Unknown ID {row.device_id}')
            top_cpu.append({
                'name': device_name,
                'avg': float(row.avg_cpu or 0),
                'peak': float(row.max_cpu or 0),
            })
            top_mem.append({
                'name': device_name,
                'avg': float(row.avg_mem or 0),
                'peak': float(row.max_mem or 0),
            })

        top_cpu = sorted(top_cpu, key=lambda item: item['avg'], reverse=True)[:5]
        top_mem = sorted(top_mem, key=lambda item: item['avg'], reverse=True)[:5]
        high_load_count = sum(1 for item in top_cpu if item['avg'] > 70)
        performance_conclusion = '所有设备负载正常。'
        if high_load_count > 0:
            performance_conclusion = (
                f'有 {high_load_count} 台设备 CPU 平均负载超过 70%，需要重点关注。'
            )

        data['performance'] = {
            'topCpu': top_cpu,
            'topMem': top_mem,
            'conclusion': performance_conclusion,
        }

        latest_change = func.coalesce(
            AlertLog.resolved_at,
            AlertLog.last_triggered_at,
            AlertLog.created_at,
        )
        alert_query = (
            AlertLog.query.filter(latest_change.between(start_time, end_time))
            .order_by(latest_change.desc(), AlertLog.id.desc())
        )
        alert_logs = alert_query.all()
        unresolved_alerts = (
            AlertLog.query.filter(
                latest_change.between(start_time, end_time),
                AlertLog.status == 'Unresolved',
            )
            .order_by(latest_change.desc(), AlertLog.id.desc())
            .limit(5)
            .all()
        )

        data['alerts'] = {
            'total': len(alert_logs),
            'distribution': build_alert_distribution(alert_logs),
            'unresolved': [
                {
                    'time': (alert.resolved_at or alert.last_triggered_at or alert.created_at)
                    .strftime('%m-%d %H:%M'),
                    'device': alert.device_name,
                    'message': alert.message,
                }
                for alert in unresolved_alerts
            ],
        }

        traffic_rows = (
            db.session.query(
                DeviceInterface.id.label('interface_id'),
                Device.name.label('device_name'),
                DeviceInterface.name.label('interface_name'),
                func.round(func.avg(InterfaceTraffic.in_rate + InterfaceTraffic.out_rate), 2)
                .label('avg_rate'),
                func.max(InterfaceTraffic.in_rate + InterfaceTraffic.out_rate).label('peak_rate'),
            )
            .select_from(InterfaceTraffic)
            .join(DeviceInterface, InterfaceTraffic.interface_id == DeviceInterface.id)
            .join(Device, DeviceInterface.device_id == Device.id)
            .filter(InterfaceTraffic.timestamp.between(start_time, end_time))
            .group_by(DeviceInterface.id)
            .order_by(desc('avg_rate'))
            .limit(5)
            .all()
        )

        top_interfaces = []
        for row in traffic_rows:
            peak_record = (
                InterfaceTraffic.query.filter(
                    InterfaceTraffic.interface_id == row.interface_id,
                    InterfaceTraffic.timestamp.between(start_time, end_time),
                )
                .order_by(
                    (InterfaceTraffic.in_rate + InterfaceTraffic.out_rate).desc(),
                    InterfaceTraffic.timestamp.desc(),
                    InterfaceTraffic.id.desc(),
                )
                .first()
            )

            peak_time = '-'
            if peak_record and peak_record.timestamp:
                peak_time = peak_record.timestamp.strftime('%Y-%m-%d %H:%M:%S')

            top_interfaces.append(
                {
                    'device': row.device_name,
                    'interface': row.interface_name,
                    'avgRate': float(row.avg_rate or 0),
                    'peakRate': float(row.peak_rate or 0),
                    'peakTime': peak_time,
                }
            )
        traffic_conclusion = '核心链路流量平稳。'
        if top_interfaces and top_interfaces[0]['avgRate'] >= 800:
            traffic_conclusion = '部分核心链路利用率较高。'

        data['traffic'] = {
            'topInterfaces': top_interfaces,
            'conclusion': traffic_conclusion,
        }

        uptime = round((online_devices / total_devices) * 100, 1) if total_devices else 0
        data['summary'] = {
            'uptime': uptime,
            'alertCount': data['alerts']['total'],
            'busyDevice': top_cpu[0]['name'] if top_cpu else '无',
            'conclusion': f'当前网络在线率为 {uptime}%，共产生 {data["alerts"]["total"]} 次告警。',
        }

        suggestions = []
        if uptime < 90:
            suggestions.append('在线率低于 90%，建议检查接入层交换机供电与链路状态。')
        if data['alerts']['total'] > 20:
            suggestions.append('告警数量较多，建议检查网络波动并评估告警阈值是否过于敏感。')
        if top_cpu and top_cpu[0]['avg'] > 60:
            suggestions.append(f'设备 {top_cpu[0]["name"]} CPU 负载较高，建议重点关注。')
        if data['topology']['hasSnapshot'] and not data['topology']['coreLinks']:
            suggestions.append('当前拓扑快照缺少核心链路摘要，建议在拓扑页面重新刷新后再导出报表。')
        if not suggestions:
            suggestions.append('网络运行健康，暂无特殊优化建议。')

        data['suggestions'] = suggestions

    except Exception as error:
        print(f'数据聚合出错: {error}')
        traceback.print_exc()

    return data
