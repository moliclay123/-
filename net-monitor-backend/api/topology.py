# api/topology.py
from flask import Blueprint, jsonify

from extensions import db
from models import Device, TopologySnapshot
from utils.snmp_helper import get_lldp_neighbors

topology_bp = Blueprint('topology', __name__)

import re
import ipaddress
import json
import time
from datetime import datetime
from flask import request, jsonify
def short_port(p: str) -> str:
    if not p:
        return ""
    p = p.strip()
    # GigabitEthernet0/0/2 -> GE0/0/2
    p = p.replace("Ten-GigabitEthernet", "10GE")
    p = p.replace("XGigabitEthernet", "XGE")
    p = p.replace("GigabitEthernet", "GE")
    return p

def load_latest_topology_snapshot():
    return TopologySnapshot.query.order_by(TopologySnapshot.created_at.desc()).first()

def save_topology_snapshot(nodes, links, categories, meta=None):
    snap = TopologySnapshot(
        nodes_json=json.dumps(nodes, ensure_ascii=False),
        links_json=json.dumps(links, ensure_ascii=False),
        categories_json=json.dumps(categories, ensure_ascii=False),
        meta_json=json.dumps(meta or {}, ensure_ascii=False)
    )
    db.session.add(snap)
    db.session.commit()
    return snap

@topology_bp.route('/graph', methods=['GET'])
def get_topology_graph():
    snap = load_latest_topology_snapshot()
    if not snap:
        # 没有任何缓存时：返回空，让前端提示“请点刷新”
        return jsonify({
            'code': 200,
            'data': {
                'nodes': [],
                'links': [],
                'categories': [{'name': '核心层'}, {'name': '汇聚层'}, {'name': '接入层'}],
                'cached': False,
                'generated_at': None
            },
            'msg': '暂无拓扑缓存，请点击“刷新拓扑”生成'
        })

    return jsonify({
        'code': 200,
        'data': {
            'nodes': json.loads(snap.nodes_json),
            'links': json.loads(snap.links_json),
            'categories': json.loads(snap.categories_json),
            'cached': True,
            'generated_at': snap.created_at.isoformat()
        }
    })

@topology_bp.route('/graph/refresh', methods=['POST'])
def refresh_topology_graph():
    t0 = time.time()

    nodes, links, categories = build_topology_data()
    # ✅【临时打印】加在这里（build_topology_data() 之后，保存快照之前）
    try:
        # 建一个 ip->name 的字典（从 nodes 来）
        ip_to_name = {n.get('id'): n.get('name') for n in (nodes or [])}

        print("\n===== DEBUG TOPO LINKS (name:port ↔ name:port) =====")
        for l in (links or []):
            s = l.get('source')
            t = l.get('target')
            sp = l.get('src_port', '')
            tp = l.get('dst_port', '')
            sname = ip_to_name.get(s, s)
            tname = ip_to_name.get(t, t)
            print(f"{sname}({s}):{sp}  ↔  {tname}({t}):{tp}  label={l.get('edge_label', '')}")
        print("===== DEBUG END =====\n")
    except Exception as e:
        print("[DEBUG PRINT ERROR]", e)

    meta = {
        'cost_ms': int((time.time() - t0) * 1000),
        'nodes': len(nodes),
        'links': len(links)
    }
    snap = save_topology_snapshot(nodes, links, categories, meta=meta)

    return jsonify({
        'code': 200,
        'data': {
            'nodes': nodes,
            'links': links,
            'categories': categories,
            'cached': True,
            'generated_at': snap.created_at.isoformat()
        }
    })


def build_topology_data():
    devices = Device.query.all()

    nodes = []
    links = []

    # name/ip 映射
    name_to_ip = {}
    ip_to_name = {}
    name_only_to_ip = {}  # ✅ 只存“设备名->IP”，不混入 IP key，避免模糊匹配误判

    ip_to_category = {}   # ✅ 用于稳定 source/target 方向（核心->汇聚->接入）

    IP_RE = re.compile(r'^\d{1,3}(\.\d{1,3}){3}$')

    def ip_sort_key(x: str):
        try:
            return int(ipaddress.ip_address(x))
        except Exception:
            return x

    def norm_name(s: str) -> str:
        return re.sub(r'[^a-z0-9]', '', (s or '').lower())

    def pick_order(ip1: str, ip2: str):
        """
        ✅ 决定 link 的 source/target：优先按层级（core=0, agg=1, acc=2），同层按数值IP
        """
        c1 = ip_to_category.get(ip1, 2)
        c2 = ip_to_category.get(ip2, 2)
        if c1 != c2:
            return (ip1, ip2) if c1 < c2 else (ip2, ip1)
        # 同层：按数值IP排序，避免字符串排序导致“方向乱”
        a, b = sorted([ip1, ip2], key=ip_sort_key)
        return a, b

    def is_placeholder_port(p: str) -> bool:
        p = (p or '').strip()
        return (not p) or p.lower().startswith('port-')

    # 1) 建立映射
    for d in devices:
        clean_name = (d.name or '').lower().split('.')[0].strip()
        if clean_name:
            name_to_ip[clean_name] = d.ip
            name_only_to_ip[clean_name] = d.ip
        name_to_ip[d.ip] = d.ip
        ip_to_name[d.ip] = d.name

    # 2) 生成节点 + ip_to_category
    for d in devices:
        dn = (d.name or '')
        dn_low = dn.lower()

        category = 2
        size = 30
        if ('core' in dn_low) or ('核心' in dn):
            category = 0
            size = 60
        elif ('agg' in dn_low) or ('汇聚' in dn):
            category = 1
            size = 45

        ip_to_category[d.ip] = category

        nodes.append({
            'id': d.ip,
            'name': d.name,
            'category': category,
            'symbolSize': size,
            'draggable': True
        })

    # === 3) 生成连线：合并双向端口（更稳）===
    link_map = {}  # key=(source, target) -> link dict

    for d in devices:
        if d.status != 'online':
            continue

        community = (d.community or "public").strip()
        neighbors = get_lldp_neighbors(d.ip, community)

        for n in neighbors:
            n_name_raw = (n.get('neighbor_name') or '').strip()
            n_name = n_name_raw.lower().split('.')[0].strip()

            local_port = short_port(n.get('local_port') or '')
            neighbor_port = short_port(n.get('neighbor_port') or '')

            # 匹配邻居IP
            target_ip = None

            # A. 精确匹配（最可靠）
            if n_name in name_only_to_ip:
                target_ip = name_only_to_ip[n_name]
            elif IP_RE.match(n_name_raw):
                # LLDP 返回的就是 IP
                target_ip = n_name_raw

            # B. 更安全的模糊匹配：只在“唯一候选”时才匹配
            if not target_ip and n_name:
                nn = norm_name(n_name)
                candidates = []
                for db_name, db_ip in name_only_to_ip.items():
                    dn = norm_name(db_name)
                    if len(dn) >= 4 and len(nn) >= 4:
                        if dn in nn or nn in dn:
                            candidates.append(db_ip)
                if len(set(candidates)) == 1:
                    target_ip = candidates[0]
                # 多个候选 → 不匹配，避免误连

            if not target_ip or target_ip == d.ip:
                continue

            # ✅ 方向稳定：按层级决定 source/target，同层按数值IP
            a, b = pick_order(d.ip, target_ip)
            key = (a, b)

            if key not in link_map:
                link_map[key] = {
                    'source': a,
                    'target': b,
                    'src_port': None,
                    'dst_port': None,
                    'edge_label': ""
                }

            link = link_map[key]

            # ✅ 不覆盖：只有在为空/占位时才写入，避免后续“空值/占位”把正确值覆盖掉
            if d.ip == link['source']:
                if (not link['src_port']) or is_placeholder_port(link['src_port']):
                    if local_port:
                        link['src_port'] = local_port
                if (not link['dst_port']) or is_placeholder_port(link['dst_port']):
                    if neighbor_port:
                        link['dst_port'] = neighbor_port
            else:
                if (not link['dst_port']) or is_placeholder_port(link['dst_port']):
                    if local_port:
                        link['dst_port'] = local_port
                if (not link['src_port']) or is_placeholder_port(link['src_port']):
                    if neighbor_port:
                        link['src_port'] = neighbor_port

    # ✅ 统一生成 edge_label（最后生成，避免过程中被覆盖导致显示跳变）
    links = []
    for link in link_map.values():
        sp = link.get('src_port') or ''
        tp = link.get('dst_port') or ''
        if sp and tp:
            link['edge_label'] = f"{sp} ↔ {tp}"
        elif sp:
            link['edge_label'] = sp
        elif tp:
            link['edge_label'] = tp
        else:
            link['edge_label'] = ""
        links.append(link)

    categories = [{'name': '核心层'}, {'name': '汇聚层'}, {'name': '接入层'}]
    return nodes, links, categories

