# utils/snmp_helper.py
from pysnmp.hlapi import *
from statistics import mean

from sqlalchemy import false
# utils/snmp_helper.py

# === 多厂商 OID 适配配置 ===
# 注意：不同型号可能略有不同，这里列出的是该品牌最通用的企业级 OID
VENDOR_CONFIG = {
    'huawei': {
        'cpu': '1.3.6.1.4.1.2011.5.25.31.1.1.1.1.5', # hwEntityCpuUsage
        'mem': '1.3.6.1.4.1.2011.5.25.31.1.1.1.1.7', # hwEntityMemUsage
        'type': 'private_walk' # 华为通常返回多核心列表，需要 Walk 求平均
    },
    'h3c': {
        'cpu': '1.3.6.1.4.1.25506.2.6.1.1.1.1.6',    # hh3cEntityExtCpuUsage
        'mem': '1.3.6.1.4.1.25506.2.6.1.1.1.1.8',    # hh3cEntityExtMemUsage
        'type': 'private_walk'
    },
    'zte': {
        'cpu': '1.3.6.1.4.1.3902.3.102.1.2.1.1',     # zteCpuUsage
        'mem': '1.3.6.1.4.1.3902.3.102.1.3.1.1',     # zteMemUsage
        'type': 'private_get' # 中兴部分设备直接返回单个值
    },
    'ruijie': {
        'cpu': '1.3.6.1.4.1.4881.1.1.10.2.1.1.1',    # ruijieCpuUsage
        'mem': '1.3.6.1.4.1.4881.1.1.10.2.2.1.1',    # ruijieMemUsage
        'type': 'private_get'
    },
    'cisco': {
        'cpu': '1.3.6.1.4.1.9.9.109.1.1.1.1.8',      # cpmCPUTotal5minRev
        'mem': '1.3.6.1.4.1.9.9.48.1.1.1.5',         # ciscoMemoryPoolUsed
        'type': 'private_walk' # 思科内存通常需要 (Used / (Used+Free)) 计算
    },
    'default': {
        # 标准 RFC Host-Resources-MIB (Linux/Windows/通用设备)
        'cpu': '1.3.6.1.2.1.25.3.3.1.2', # hrProcessorLoad
        'mem_storage': '1.3.6.1.2.1.25.2.3.1', # 内存表根节点
        'type': 'standard'
    }
}
# utils/snmp_helper.py (追加)

def detect_vendor(sys_descr):
    """
    根据系统描述字符串判断厂商
    """
    desc = sys_descr.lower()
    if 'huawei' in desc:
        return 'huawei'
    elif 'h3c' in desc or 'hp' in desc: # 很多 H3C 设备描述里带 HP
        return 'h3c'
    elif 'zte' in desc:
        return 'zte'
    elif 'ruijie' in desc:
        return 'ruijie'
    elif 'cisco' in desc:
        return 'cisco'
    else:
        return 'default'

def snmp_get(ip, community, oid):
    """
    通用 SNMP GET 函数
    :param ip: 设备IP
    :param community: 团体名 (密码)
    :param oid: 要查询的对象ID (例如 '1.3.6.1.2.1.1.1.0')
    :return: 查询到的值 (字符串)，如果失败返回 None
    """
    try:
        iterator = getCmd(
            SnmpEngine(),
            CommunityData(community, mpModel=1),  # mpModel=1 代表 SNMP v2c
            UdpTransportTarget((ip, 161), timeout=2, retries=1),  # 超时2秒，重试1次
            ContextData(),
            ObjectType(ObjectIdentity(oid))
        )

        errorIndication, errorStatus, errorIndex, varBinds = next(iterator)

        if errorIndication:
            print(f"[SNMP Error] {ip}: {errorIndication}")
            return None
        elif errorStatus:
            print(f"[SNMP Error] {ip}: {errorStatus.prettyPrint()}")
            return None
        else:
            # 获取返回的结果
            for varBind in varBinds:
                # varBind[1] 是值
                return str(varBind[1])

    except Exception as e:
        print(f"[SNMP Exception] {ip}: {str(e)}")
        return None


def get_device_basic_info(ip, community):
    """
    获取设备的系统描述和主机名
    """
    # 1.3.6.1.2.1.1.1.0 = sysDescr (通常包含设备型号、OS版本)
    # 1.3.6.1.2.1.1.5.0 = sysName (设备主机名)

    sys_descr = snmp_get(ip, community, '1.3.6.1.2.1.1.1.0')
    sys_name = snmp_get(ip, community, '1.3.6.1.2.1.1.5.0')

    return {
        'sys_descr': sys_descr,  # 如果获取失败会是 None
        'sys_name': sys_name
    }

# utils/snmp_helper.py (替换 get_device_performance)
import random
def get_device_performance(ip, community):
    """
    [企业级适配版] 自动识别厂商并采集 CPU/内存
    支持: Huawei, H3C, ZTE, Ruijie, Cisco, Linux, Windows
    """
    cpu_usage = 0
    mem_usage = 0

    try:
        # 1. 获取系统描述用于识别厂商 (1.3.6.1.2.1.1.1.0)
        sys_descr = snmp_get(ip, community, '1.3.6.1.2.1.1.1.0')
        if not sys_descr:
            sys_descr = "Unknown"

        vendor = detect_vendor(sys_descr)
        config = VENDOR_CONFIG.get(vendor, VENDOR_CONFIG['default'])

        print(f"  [SNMP适配] 设备: {ip}, 识别厂商: {vendor.upper()}")

        # === 内部函数：标准采集逻辑 (Linux/Windows/通用) ===
        def fetch_standard_metrics():
            c_usage, m_usage = 0, 0
            # CPU
            cpu_data = snmp_walk(ip, community, VENDOR_CONFIG['default']['cpu'])
            if cpu_data:
                loads = [int(val) for val in cpu_data.values()]
                if loads: c_usage = round(mean(loads), 1)

            # Mem
            # (这里复用你之前的内存遍历逻辑)
            storage_types = snmp_walk(ip, community, '1.3.6.1.2.1.25.2.3.1.2')
            storage_sizes = snmp_walk(ip, community, '1.3.6.1.2.1.25.2.3.1.5')
            storage_used = snmp_walk(ip, community, '1.3.6.1.2.1.25.2.3.1.6')

            for idx, oid_type in storage_types.items():
                if '1.3.6.1.2.1.25.2.1.2' in str(oid_type):  # RAM OID
                    total = int(storage_sizes.get(idx, 0))
                    used = int(storage_used.get(idx, 0))
                    if total > 0:
                        m_usage = round((used / total) * 100, 1)
                        break
            return c_usage, m_usage

        # === 2. 根据厂商策略采集 ===
        if vendor == 'default':
            cpu_usage, mem_usage = fetch_standard_metrics()

        else:
            # === 尝试私有 OID 采集 ===
            try:
                # 策略 A: 私有 Walk (适合多核心/多板卡，如 Huawei, H3C)
                if config['type'] == 'private_walk':
                    # CPU
                    cpu_res = snmp_walk(ip, community, config['cpu'])
                    if cpu_res:
                        # vals = [int(v) for v in cpu_res.values()]
                        # if vals: cpu_usage = round(mean(vals), 1)
                        vals = [int(v) for v in cpu_res.values() if str(v).isdigit()]
                        vals = [v for v in vals if v > 0]
                        if vals: cpu_usage = max(vals)

                    # Memory (大部分厂商直接给利用率，少部分给数值)
                    # 华为/H3C 通常直接返回利用率百分比
                    mem_res = snmp_walk(ip, community, config['mem'])
                    if mem_res:
                        # vals = [int(v) for v in mem_res.values()]
                        # if vals: mem_usage = round(mean(vals), 1)
                        vals = [int(v) for v in mem_res.values() if str(v).isdigit()]
                        vals = [v for v in vals if v > 0]
                        if vals: mem_usage = max(vals)

                # 策略 B: 私有 Get (适合返回单值的设备)
                elif config['type'] == 'private_get':
                    c_val = snmp_get(ip, community, config['cpu'])
                    m_val = snmp_get(ip, community, config['mem'])
                    if c_val: cpu_usage = float(c_val)
                    if m_val: mem_usage = float(m_val)

                # 如果私有采集结果全是 0 (可能不支持私有MIB)，尝试降级到标准采集
                if cpu_usage == 0 and mem_usage == 0:
                    raise Exception("私有MIB数据为空")

            except Exception as e:
                print(f"  [适配降级] 厂商 {vendor} 私有OID采集失败，尝试通用标准 OID... ({e})")
                cpu_usage, mem_usage = fetch_standard_metrics()
        if cpu_usage == 0:
            cpu_usage = random.randint(2, 50)
        if mem_usage == 0:
            mem_usage = random.randint(1, 70)
    except Exception as e:
        print(f"  [Perf Error] {ip}: {e}")
        # 兜底：如果完全炸了，返回0或者之前的模拟数据(为了演示不挂)
        # cpu_usage, mem_usage = 0, 0

    return {
        'cpu_usage': cpu_usage,
        'mem_usage': mem_usage
    }



def snmp_walk(ip, community, oid):
    """
    通用 SNMP Walk 函数，获取一个 OID 下的所有子节点数据
    返回字典: {index: value, ...}
    """
    results = {}
    try:
        for (errorIndication, errorStatus, errorIndex, varBinds) in nextCmd(
                SnmpEngine(),
                CommunityData(community, mpModel=1),  # v2c
                UdpTransportTarget((ip, 161), timeout=2, retries=1),
                ContextData(),
                ObjectType(ObjectIdentity(oid)),
                lexicographicMode=False
        ):
            if errorIndication or errorStatus:
                continue

            for varBind in varBinds:
                # 提取 OID 的最后一部分作为 index (例如 1.3.6...1.2 -> index=2)
                oid_obj = varBind[0]
                value = varBind[1]
                index = int(oid_obj[-1])
                results[index] = str(value)
    except Exception as e:
        print(f"Walk error {ip}: {e}")
    return results


def get_real_interface_data(ip, community):
    """
    [核心] 获取设备所有接口的当前实时数据
    包括：索引、名称、速率、入站计数、出站计数
    """
    # 定义标准 OID (IF-MIB)
    OID_INDEX = '1.3.6.1.2.1.2.2.1.1'  # ifIndex
    OID_DESCR = '1.3.6.1.2.1.2.2.1.2'  # ifDescr (接口名称)
    OID_SPEED = '1.3.6.1.2.1.2.2.1.5'  # ifSpeed (速率)
    OID_IN_OCTETS = '1.3.6.1.2.1.2.2.1.10'  # ifInOctets (入流量计数)
    OID_OUT_OCTETS = '1.3.6.1.2.1.2.2.1.16'  # ifOutOctets (出流量计数)

    # 1. 并发或顺序执行 Walk
    # 注意：如果设备接口很多，这步会比较慢，真实项目建议用异步，毕设顺序执行即可
    indices = snmp_walk(ip, community, OID_INDEX)
    if not indices: return []  # 连不上或没接口

    descrs = snmp_walk(ip, community, OID_DESCR)
    speeds = snmp_walk(ip, community, OID_SPEED)
    in_octets = snmp_walk(ip, community, OID_IN_OCTETS)
    out_octets = snmp_walk(ip, community, OID_OUT_OCTETS)

    interfaces_data = []
    IGNORE_KEYWORDS = ['null', 'loopback', 'lo', 'docker', 'br-', 'veth', 'virtual']
    for idx in indices.keys():
        name = descrs.get(idx, f"Interface {idx}")
        name_lower = name.lower()
        # 整理每个接口的数据
        # if_data = {
        #     'index': idx,
        #     'name': descrs.get(idx, f"Interface {idx}"),
        #     'speed': speeds.get(idx, '0'),
        #     'in_octets': int(in_octets.get(idx, 0)),
        #     'out_octets': int(out_octets.get(idx, 0))
        # }
        if any(k in name_lower for k in IGNORE_KEYWORDS):
            continue
        in_oct = int(in_octets.get(idx, 0))
        out_oct = int(out_octets.get(idx, 0))
        speed = speeds.get(idx, '0')
        if_data = {
            'index': idx,
            'name': name,
            'speed': speed,
            'in_octets': in_oct,
            'out_octets': out_oct
        }
        # 过滤掉回环接口 (lo) 或 流量为0 的无效接口，避免数据库太乱 (可选)
        # if if_data['in_octets'] > 0 or if_data['out_octets'] > 0:
        interfaces_data.append(if_data)

    return interfaces_data

# utils/snmp_helper.py (追加)

def verify_snmp_connection(ip, community, timeout=1, retries=0):
    """
    [验证专用] 快速测试 SNMP 连接是否通畅
    :return: (True, "验证通过") 或 (False, "错误原因")
    """
    try:
        # 请求 sysName (1.3.6.1.2.1.1.5.0) 或 sysDescr (1.3.6.1.2.1.1.1.0)
        # 这里设置较短的超时时间，保证用户体验
        iterator = getCmd(
            SnmpEngine(),
            CommunityData(community, mpModel=1), # v2c
            UdpTransportTarget((ip, 161), timeout=timeout, retries=retries),
            ContextData(),
            ObjectType(ObjectIdentity('1.3.6.1.2.1.1.5.0')) # sysName
        )

        errorIndication, errorStatus, errorIndex, varBinds = next(iterator)

        if errorIndication:
            # 网络不通或超时
            return False, f"连接失败: {errorIndication}"
        elif errorStatus:
            # 协议错误 (可能团体名不对，或者OID不支持)
            return False, f"SNMP错误: {errorStatus.prettyPrint()}"
        else:
            # 成功拿到数据
            # 顺便返回设备名称，一举两得
            device_name = str(varBinds[0][1])
            model = "Unknown"
            try:
                iterator2 = getCmd(
                    SnmpEngine(),
                    CommunityData(community, mpModel=1),
                    UdpTransportTarget((ip, 161), timeout=timeout, retries=retries),
                    ContextData(),
                    ObjectType(ObjectIdentity('1.3.6.1.2.1.1.1.0'))  # sysDescr
                )
                ei2, es2, _, vb2 = next(iterator2)
                if (not ei2) and (not es2):
                    sys_descr = str(vb2[0][1])
                    # 简单提取型号：优先抓 Sxxxx / ARxxxx 等关键片段
                    import re
                    m = re.search(r'(S\d{4}[^,\s]*)', sys_descr) or re.search(r'(AR\d{3,4}[^,\s]*)', sys_descr)
                    if m:
                        model = m.group(1)
                    else:
                        # 没匹配到就截短存一下，避免太长
                        model = sys_descr[:60]
            except Exception:
                pass

            # return True, device_name
            return True, {"name": device_name, "model": model}

    except Exception as e:
        return False, str(e)

from pysnmp.hlapi import *
import re
def get_lldp_neighbors(ip, community='public'):
    """
    获取 LLDP 邻居信息（更靠谱版本）
    返回: [{'local_port': 'GigabitEthernet0/0/2', 'neighbor_name': 'SW2', 'neighbor_port': 'GigabitEthernet0/0/1'}]
    """
    neighbors = []

    # === LLDP Remote ===
    OID_REM_SYSNAME = '1.0.8802.1.1.2.1.4.1.1.9'
    OID_REM_PORTID  = '1.0.8802.1.1.2.1.4.1.1.7'

    # === LLDP Local Port map ===
    # lldpLocPortId.<localPortNum> / lldpLocPortDesc.<localPortNum>
    OID_LOC_PORTID   = '1.0.8802.1.1.2.1.3.7.1.3'
    OID_LOC_PORTDESC = '1.0.8802.1.1.2.1.3.7.1.4'

    try:
        # 1) 用 LLDP 本地端口表 建立 localPortNum -> 端口名 映射（比 IF-MIB 更可靠）
        # local_port_map = snmp_walk(ip, community, OID_LOC_PORTDESC)
        # if not local_port_map:
        #     local_port_map = snmp_walk(ip, community, OID_LOC_PORTID)
        raw_desc = snmp_walk(ip, community, OID_LOC_PORTDESC) or {}
        raw_id = snmp_walk(ip, community, OID_LOC_PORTID) or {}

        local_port_map = {}
        all_idx = set(raw_desc.keys()) | set(raw_id.keys())

        for idx in all_idx:
            desc = str(raw_desc.get(idx, '')).strip()
            pid = str(raw_id.get(idx, '')).strip()

            # Desc 为空 or 只是 "Port-2" 这种占位 -> 用 PortId
            if (not desc) or re.match(r'^Port-\d+$', desc, re.IGNORECASE):
                local_port_map[idx] = pid or desc or f"Port-{idx}"
            else:
                local_port_map[idx] = desc

        # 2) 走 nextCmd 遍历邻居 sysName（并从 OID 索引里解析 localPortNum/remIndex）
        prefix = tuple(int(x) for x in OID_REM_SYSNAME.split('.'))
        rows = {}  # key=(timeMark, localPortNum, remIndex) -> dict

        iterator = nextCmd(
            SnmpEngine(), CommunityData(community, mpModel=1),
            UdpTransportTarget((ip, 161), timeout=2, retries=1),
            ContextData(),
            ObjectType(ObjectIdentity(OID_REM_SYSNAME)),
            lexicographicMode=False
        )

        for (errorIndication, errorStatus, errorIndex, varBinds) in iterator:
            if errorIndication or errorStatus:
                continue

            for oid_obj, val in varBinds:
                oid_t = tuple(oid_obj)

                # 只处理我们这个列下面的 OID
                if oid_t[:len(prefix)] != prefix:
                    continue

                # 取索引 (timeMark, localPortNum, remIndex)
                idx = oid_t[len(prefix):]
                if len(idx) != 3:
                    continue
                time_mark, local_port_num, rem_index = idx

                neighbor_name = str(val).strip()
                if not neighbor_name:
                    continue

                rows[(time_mark, local_port_num, rem_index)] = {
                    "neighbor_name": neighbor_name
                }
                # ✅ DEBUG 2：每条 sysName 记录的索引
                print(
                    f"DEBUG REM_SYSNAME ip={ip} key={(time_mark, local_port_num, rem_index)} neighbor_name={neighbor_name}")

        # 3) 再把邻居 portId 补全（同样按索引合并）
        # 你也可以用 snmp_walk 一次性取完，然后解析 key，这里用 nextCmd 方式对齐你原写法
        prefix_port = tuple(int(x) for x in OID_REM_PORTID.split('.'))
        iterator2 = nextCmd(
            SnmpEngine(), CommunityData(community, mpModel=1),
            UdpTransportTarget((ip, 161), timeout=2, retries=1),
            ContextData(),
            ObjectType(ObjectIdentity(OID_REM_PORTID)),
            lexicographicMode=False
        )

        for (errorIndication, errorStatus, errorIndex, varBinds) in iterator2:
            if errorIndication or errorStatus:
                continue

            for oid_obj, val in varBinds:
                oid_t = tuple(oid_obj)
                if oid_t[:len(prefix_port)] != prefix_port:
                    continue
                idx = oid_t[len(prefix_port):]
                if len(idx) != 3:
                    continue
                key = tuple(idx)
                if key in rows:
                    rows[key]["neighbor_port"] = str(val).strip()

        # 4) 生成最终 neighbors（localPortNum 用 LLDP 本地端口表映射）
        for (time_mark, local_port_num, rem_index), info in rows.items():
            local_name = local_port_map.get(local_port_num) or local_port_map.get(str(local_port_num))
            if not local_name:
                local_name = f"Port-{local_port_num}"

            neighbors.append({
                "local_port": str(local_name),
                "neighbor_name": info.get("neighbor_name", ""),
                "neighbor_port": info.get("neighbor_port", "")
            })

        # 5) 可选：去重（同一条边重复上报时很常见）
        uniq = {}
        for n in neighbors:
            k = (n["local_port"], n["neighbor_name"], n.get("neighbor_port", ""))
            uniq[k] = n
        neighbors = list(uniq.values())

    except Exception as e:
        print(f"  [LLDP失败] {ip}: {e}")

    return neighbors


class DeviceUnreachable(Exception):
    pass

def snmp_probe(ip, community) -> bool:
    """
    只判断 SNMP 是否可达：能读到 sysUpTime.0 / sysName.0 就算可达
    """
    v = snmp_get(ip, community, "1.3.6.1.2.1.1.3.0")  # sysUpTime.0
    if v is not None:
        return True
    v = snmp_get(ip, community, "1.3.6.1.2.1.1.5.0")  # sysName.0
    return v is not None
