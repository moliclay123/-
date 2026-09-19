# utils/scan_helper.py
import platform
import subprocess
import ipaddress
from concurrent.futures import ThreadPoolExecutor


def is_reachable(ip):
    """
    Ping 单个 IP 判断是否存活
    """
    # 根据操作系统决定 ping 命令的参数
    param = '-n' if platform.system().lower() == 'windows' else '-c'

    # 执行 ping 命令，超时时间设为 1 秒 (Windows 使用 -w 1000, Linux 使用 -W 1)
    # 为了通用简单，我们只用 count 参数，默认超时通常是几秒
    command = ['ping', param, '1', str(ip)]

    try:
        # stdout=subprocess.DEVNULL 屏蔽输出
        return subprocess.call(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0
    except Exception:
        return False


def scan_network(cidr):
    """
    扫描网段，返回存活 IP 列表
    :param cidr: 例如 '192.168.1.0/24'
    """
    alive_hosts = []
    try:
        # 解析网段
        network = ipaddress.ip_network(cidr, strict=False)
        # 获取该网段下所有主机 IP (去掉网络号和广播地址)
        hosts = list(network.hosts())

        # 限制最大扫描数量，防止扫 B 类网段卡死 (毕设通常演示 C 类 /24)
        if len(hosts) > 512:
            return {'error': '网段过大，仅支持 /24 或更小的子网扫描'}

        print(f"正在扫描网段: {cidr}, 主机数: {len(hosts)}...")

        # 使用线程池并发扫描 (50 个线程并发)
        with ThreadPoolExecutor(max_workers=50) as executor:
            # 提交所有任务
            results = executor.map(is_reachable, hosts)

            # 收集结果
            for ip, is_alive in zip(hosts, results):
                if is_alive:
                    alive_hosts.append(str(ip))

    except ValueError:
        return {'error': '无效的网段格式'}
    except Exception as e:
        return {'error': str(e)}

    return alive_hosts


# utils/snmp_helper.py 追加内容
from pysnmp.hlapi import *


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


