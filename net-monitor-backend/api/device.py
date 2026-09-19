# api/device.py
from datetime import datetime
from flask import Blueprint, request, jsonify
from extensions import db
from models import AlertLog, AlertRule, Device, DeviceInterface, InterfaceTraffic, Performance
from utils.snmp_helper import get_device_basic_info
from utils.scan_helper import scan_network
from utils.snmp_helper import verify_snmp_connection, get_device_basic_info
# 创建一个蓝图 (Blueprint)，相当于一个子应用
device_bp = Blueprint('device', __name__)


# 1. 获取设备列表接口
@device_bp.route('/list', methods=['GET'])
def get_devices():
    try:
        # 查询数据库所有设备
        devices = Device.query.all()
        # 将对象列表转换为字典列表 (JSON格式)
        data = [d.to_dict() for d in devices]
        return jsonify({'code': 200, 'msg': 'success', 'data': data})
    except Exception as e:
        return jsonify({'code': 500, 'msg': str(e)}), 500


# 2. 手动添加设备接口
# @device_bp.route('/add', methods=['POST'])
# def add_device():
#     try:
#         data = request.json
#         ip = data.get('ip')
#         user_name = data.get('name')  # 用户手动填写的名称
#         community = data.get('community', 'public')
#
#         if not ip:
#             return jsonify({'code': 400, 'msg': 'IP地址不能为空'})
#
#         existing = Device.query.filter_by(ip=ip).first()
#         if existing:
#             return jsonify({'code': 400, 'msg': '该设备IP已存在'})
#
#         # === 核心修改开始: 尝试通过 SNMP 获取真实信息 ===
#         print(f"正在尝试连接设备 {ip} 获取 SNMP 信息...")
#         snmp_info = get_device_basic_info(ip, community)
#
#         real_name = user_name  # 默认使用用户填的
#         model = 'Unknown Device'
#         status = 'offline'  # 默认离线，连通了才改在线
#
#         if snmp_info['sys_descr'] or snmp_info['sys_name']:
#             print(f"SNMP连接成功! Info: {snmp_info}")
#             status = 'online'
#             # 如果获取到了真实主机名，优先使用真实主机名
#             if snmp_info['sys_name']:
#                 real_name = snmp_info['sys_name']
#             # 从描述信息中提取型号 (这里简单把整个描述存进去，以后可以用正则提取)
#             if snmp_info['sys_descr']:
#                 model = snmp_info['sys_descr'][:50] + '...'  # 截取前50个字符防止太长
#         else:
#             print("SNMP连接失败，将以离线状态纳管")
#         # === 核心修改结束 ===
#
#         new_device = Device(
#             ip=ip,
#             name=real_name,
#             community=community,
#             status=status,
#             model=model
#         )
#
#         db.session.add(new_device)
#         db.session.commit()
#
#         # 返回的消息里带上状态，方便前端知道有没有连通
#         msg = '纳管成功' if status == 'online' else '纳管成功，但SNMP连接失败(设备离线或团体名错误)'
#         return jsonify({'code': 200, 'msg': msg})
#
#     except Exception as e:
#         db.session.rollback()
#         return jsonify({'code': 500, 'msg': str(e)})

@device_bp.route('/add', methods=['POST'])
def add_device():
    try:
        data = request.json
        ip = data.get('ip')
        user_name = data.get('name')
        community = data.get('community', 'public')  # 获取用户输入的团体名，而不是默认写死

        if not ip:
            return jsonify({'code': 400, 'msg': 'IP地址不能为空'})

        # 1. 查重
        if Device.query.filter_by(ip=ip).first():
            return jsonify({'code': 400, 'msg': '该设备IP已存在'})

        # 2. 【核心修改】强制校验 SNMP
        print(f"正在校验设备 {ip} (Community: {community})...")
        is_valid, result_msg = verify_snmp_connection(ip, community)

        if not is_valid:
            # 校验失败，直接拒绝纳管
            print(f"校验失败: {result_msg}")
            return jsonify({
                'code': 400,
                'msg': f'纳管失败: 无法连接设备或团体名错误。详情: {result_msg}'
            })

        # 3. 校验通过，写入数据库
        # result_msg 此时就是设备真实的 sysName
        real_name = result_msg if result_msg else user_name

        # 顺便获取一下详细信息 (Model等)
        full_info = get_device_basic_info(ip, community)
        model = full_info.get('sys_descr', 'Unknown')[:50] if full_info else 'Unknown'

        new_device = Device(
            ip=ip,
            name=real_name,
            community=community,  # 存入验证通过的团体名
            status='online',  # 能通过校验肯定是在线
            model=model,
            last_seen=datetime.now()
        )

        db.session.add(new_device)
        db.session.commit()

        return jsonify({'code': 200, 'msg': '验证通过，设备已纳管'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'code': 500, 'msg': str(e)})

# 3. 删除设备接口
# api/device.py (只修改 delete_device 部分)

@device_bp.route('/delete', methods=['POST'])
def delete_device():
    try:
        # data = request.json
        data = request.json or {}
        print(f"收到删除请求，数据: {data}")  # [调试] 打印前端传来的数据

        device_id = data.get('id')
        delete_history = bool(data.get('delete_history', False))
        if not device_id:
            print("错误: 前端未传递设备ID")
            return jsonify({'code': 400, 'msg': '参数错误: 缺少设备ID'})

        # 尝试查找设备
        device = Device.query.get(device_id)

        if not device:
            print(f"错误: 数据库中找不到ID为 {device_id} 的设备")
            return jsonify({'code': 404, 'msg': '设备不存在或已被删除'})

        # 执行删除
        print(f"正在删除设备: {device.name} (IP: {device.ip})")

        iface_ids = [
            item.id for item in DeviceInterface.query.filter_by(device_id=device_id).all()
        ]
        if iface_ids:
            traffic_rules = (
                AlertRule.query.filter(
                    AlertRule.metric == 'interface_traffic_rate',
                    AlertRule.interface_id.in_(iface_ids)
                ).all()
            )
            for rule in traffic_rules:
                AlertLog.query.filter_by(rule_id=rule.id, status='Unresolved').update(
                    {
                        AlertLog.status: 'Resolved',
                        AlertLog.resolved_at: datetime.now()
                    },
                    synchronize_session=False
                )
                db.session.delete(rule)

        if delete_history:
            # 1) 删接口流量（InterfaceTraffic） -> 先找该设备的接口id
            iface_ids = [
                x.id for x in DeviceInterface.query.filter_by(device_id=device_id).all()
            ]
            if iface_ids:
                InterfaceTraffic.query.filter(
                    InterfaceTraffic.interface_id.in_(iface_ids)
                ).delete(synchronize_session=False)

            # 2) 删接口表（DeviceInterface）
            DeviceInterface.query.filter_by(device_id=device_id).delete(synchronize_session=False)

            # 3) 删性能历史（Performance）(你库里一般是 device_id 外键)
            try:
                Performance.query.filter_by(device_id=device_id).delete(synchronize_session=False)
            except Exception as e:
                # 如果你 Performance 表字段不是 device_id，这里会报错；你改成你的字段即可
                print(f"[警告] Performance 删除失败: {e}")

            # 4) 删告警日志（AlertLog）——你表里是 device_name 字段
            # ⚠️ 注意：如果设备改过名，旧日志可能删不干净（因为没device_id）
            try:
                AlertLog.query.filter(
                    (AlertLog.device_id == device.id) |
                    ((AlertLog.device_id.is_(None)) & (AlertLog.device_name == device.name))
                ).delete(synchronize_session=False)
            except Exception as e:
                print(f"[警告] AlertLog 删除失败: {e}")
        db.session.delete(device)
        db.session.commit()
        msg = "删除成功(含历史记录)" if delete_history else "删除成功(仅删除设备)"
        print("删除成功, 事务已提交")

        return jsonify({'code': 200, 'msg': msg})


    except Exception as e:
        db.session.rollback()  # [关键] 发生错误必须回滚，否则下次操作会报错
        print(f"删除过程中发生异常: {str(e)}")  # [调试] 打印报错信息
        return jsonify({'code': 500, 'msg': f"服务器内部错误: {str(e)}"})


# api/device.py 追加内容
from models import Performance  # 确保引入了 Performance 模型


@device_bp.route('/history', methods=['GET'])
def get_device_history():
    try:
        device_id = request.args.get('id')
        if not device_id:
            return jsonify({'code': 400, 'msg': '缺少设备ID'})

        # 1. 查基本信息
        device = Device.query.get(device_id)
        if not device:
            return jsonify({'code': 404, 'msg': '设备不存在'})

        # 2. 查历史性能数据 (最近 50 条，按时间正序)
        perfs = Performance.query.filter_by(device_id=device_id) \
            .order_by(Performance.timestamp.desc()) \
            .limit(50).all()

        # 翻转数组，因为图表是从左(旧)到右(新)画的
        perfs.reverse()

        # 格式化数据
        data = {
            'info': device.to_dict(),
            'chart': {
                'x': [p.timestamp.strftime('%H:%M:%S') for p in perfs],
                'cpu': [p.cpu_usage for p in perfs],
                'mem': [p.mem_usage for p in perfs]
            }
        }

        return jsonify({'code': 200, 'data': data})

    except Exception as e:
        return jsonify({'code': 500, 'msg': str(e)})


# api/device.py (确保引入了 scan_network)

# 4. 网络扫描接口 (POST /api/device/scan)
@device_bp.route('/scan', methods=['POST'])
def scan_devices():
    try:
        cidr = request.json.get('cidr')
        if not cidr:
            return jsonify({'code': 400, 'msg': '请输入网段 (例如 192.168.1.0/24)'})

        # 执行扫描
        result = scan_network(cidr)

        # 检查是否有错误
        if isinstance(result, dict) and 'error' in result:
            return jsonify({'code': 400, 'msg': result['error']})

        return jsonify({'code': 200, 'data': result, 'msg': '扫描完成'})

    except Exception as e:
        return jsonify({'code': 500, 'msg': str(e)})


# 5. 批量纳管接口 (POST /api/device/batch_add)
# 为了方便前端一次性添加多个扫描到的设备
# @device_bp.route('/batch_add', methods=['POST'])
# def batch_add_devices():
#     try:
#         devices_list = request.json.get('devices')  # 格式: [{'ip': '...', 'name': '...', 'community': '...'}]
#         success_count = 0
#
#         for d in devices_list:
#             ip = d.get('ip')
#             # 查重
#             if not Device.query.filter_by(ip=ip).first():
#                 # 复用之前的单设备添加逻辑，或者直接创建
#                 # 这里简单起见，直接插入库，后续由轮询任务去更新详情
#                 new_device = Device(
#                     ip=ip,
#                     name=d.get('name', ip),  # 默认名称用IP
#                     community=d.get('community', 'public'),
#                     status='online',  # 刚扫出来的肯定是通的
#                     model='Unknown'
#                 )
#                 db.session.add(new_device)
#                 success_count += 1
#
#         db.session.commit()
#         return jsonify({'code': 200, 'msg': f'成功纳管 {success_count} 台设备'})
#
#     except Exception as e:
#         db.session.rollback()
#         return jsonify({'code': 500, 'msg': str(e)})
#
#
# api/device.py (追加)
from models import DeviceInterface, InterfaceTraffic  # 引入


# 获取设备的接口列表
@device_bp.route('/interfaces', methods=['GET'])
def get_interfaces():
    device_id = request.args.get('device_id')
    ifs = DeviceInterface.query.filter_by(device_id=device_id).all()
    data = [{'id': i.id, 'name': i.name, 'speed': i.speed} for i in ifs]
    return jsonify({'code': 200, 'data': data})

#
# 获取某个接口的流量历史
@device_bp.route('/interface/traffic', methods=['GET'])
def get_interface_traffic():
    interface_id = request.args.get('interface_id')
    # 取最近 50 个点
    traffics = InterfaceTraffic.query.filter_by(interface_id=interface_id) \
        .order_by(InterfaceTraffic.timestamp.desc()) \
        .limit(50).all()

    traffics.reverse()  # 时间正序

    return jsonify({
        'code': 200,
        'data': {
            'x': [t.timestamp.strftime('%H:%M:%S') for t in traffics],
            'in': [t.in_rate for t in traffics],
            'out': [t.out_rate for t in traffics]
        }
    })
@device_bp.route('/batch_add', methods=['POST'])
def batch_add_devices():
    try:
        devices_list = request.json.get('devices')
        # devices_list 里的每一项都应该包含 community，如果没填则用全局默认的

        success_count = 0
        fail_count = 0
        fail_details = []

        for d in devices_list:
            ip = d.get('ip')
            community = d.get('community', 'public')

            # 查重
            if Device.query.filter_by(ip=ip).first():
                fail_count += 1
                fail_details.append(f"{ip}: 已存在")
                continue

            # 【核心修改】逐个校验
            # 注意：批量纳管时，校验可能会慢。如果是成百上千台，建议使用 Celery 异步任务
            # 但毕设几十台设备，串行校验是可以接受的
            is_valid, result_msg = verify_snmp_connection(ip, community, timeout=2)
            if is_valid:
                device_name = result_msg.get("name", ip) if isinstance(result_msg, dict) else str(result_msg)
                device_model = result_msg.get("model", "Unknown") if isinstance(result_msg, dict) else "Unknown"

                new_device = Device(
                    ip=ip,
                    name=device_name,
                    community=community,
                    status='online',
                    model=device_model
                )
                # 缩短超时加快速度

            # if is_valid:
            #     new_device = Device(
            #         ip=ip,
            #         name=result_msg,  # 使用真实名称
            #         community=community,
            #         status='online',
            #         model='Unknown'  # 批量时先不查详细的，等轮询去更新，为了快
            #     )
                db.session.add(new_device)
                success_count += 1
            else:
                fail_count += 1
                fail_details.append(f"{ip}: 连接失败")

        db.session.commit()

        msg = f"成功: {success_count}, 失败: {fail_count}"
        if fail_details:
            msg += f" (失败详情: {', '.join(fail_details[:3])}...)"

        return jsonify({'code': 200, 'msg': msg})

    except Exception as e:
        db.session.rollback()
        return jsonify({'code': 500, 'msg': str(e)})
