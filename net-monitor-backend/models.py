# models.py
from datetime import datetime
from extensions import db

# 1. 设备表
class Device(db.Model):
    __tablename__ = 'devices'
    id = db.Column(db.Integer, primary_key=True)
    ip = db.Column(db.String(50), unique=True, nullable=False)
    name = db.Column(db.String(100))
    community = db.Column(db.String(50)) # SNMP团体名
    status = db.Column(db.String(20), default='offline')   # online/offline
    model = db.Column(db.String(100))                      # 设备型号
    last_seen = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            'id': self.id,
            'ip': self.ip,
            'name': self.name,
            'status': self.status,
            'model': self.model,
            'last_seen': self.last_seen.strftime('%Y-%m-%d %H:%M:%S') if self.last_seen else ''
        }

# 2. 性能历史表 (一对多关联设备)
class Performance(db.Model):
    __tablename__ = 'performance'
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('devices.id'))
    cpu_usage = db.Column(db.Float)
    mem_usage = db.Column(db.Float)
    timestamp = db.Column(db.DateTime, default=datetime.now)

# 3. 告警规则表
class AlertRule(db.Model):
    __tablename__ = 'alert_rules'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    metric = db.Column(db.String(50)) # cpu, memory, status
    condition = db.Column(db.String(10)) # >, <, =
    threshold = db.Column(db.String(50)) # 80, offline
    enabled = db.Column(db.Boolean, default=True)
    interface_id = db.Column(db.Integer, nullable=True)

# 4. 告警日志表
class AlertLog(db.Model):
    __tablename__ = 'alert_logs'
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('devices.id'), nullable=True)
    rule_id = db.Column(db.Integer, nullable=True)
    fingerprint = db.Column(db.String(100), nullable=True)
    device_name = db.Column(db.String(100))
    level = db.Column(db.String(20)) # Critical, Warning
    message = db.Column(db.String(255))
    status = db.Column(db.String(20), default='Unresolved')
    created_at = db.Column(db.DateTime, default=datetime.now)
    last_triggered_at = db.Column(db.DateTime, nullable=True)
    resolved_at = db.Column(db.DateTime, nullable=True)


# models.py

# ... 其他类保持不变 ...

# 5. 设备接口表 (升级版)
class DeviceInterface(db.Model):
    __tablename__ = 'device_interfaces'
    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.Integer, db.ForeignKey('devices.id'))
    name = db.Column(db.String(50))  # 接口名称
    index = db.Column(db.Integer)  # SNMP ifIndex (唯一标识)
    speed = db.Column(db.String(20))  # 接口物理速率

    # === 新增：用于计算速率的缓存字段 ===
    last_in_octets = db.Column(db.BigInteger, default=0)  # 上次入站总字节数
    last_out_octets = db.Column(db.BigInteger, default=0)  # 上次出站总字节数
    last_check_time = db.Column(db.DateTime)  # 上次采集时间


# 6. 接口流量历史表 (保持不变，存储计算后的速率)
class InterfaceTraffic(db.Model):
    __tablename__ = 'interface_traffic'
    id = db.Column(db.Integer, primary_key=True)
    interface_id = db.Column(db.Integer, db.ForeignKey('device_interfaces.id'))
    in_rate = db.Column(db.Float)  # 入站速率 (Mbps)
    out_rate = db.Column(db.Float)  # 出站速率 (Mbps)
    timestamp = db.Column(db.DateTime, default=datetime.now)


import json
from datetime import datetime
class TopologySnapshot(db.Model):
    __tablename__ = 'topology_snapshot'

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True, nullable=False)

    nodes_json = db.Column(db.Text, nullable=False)
    links_json = db.Column(db.Text, nullable=False)
    categories_json = db.Column(db.Text, nullable=False)

    # 可选：记录是谁触发刷新/耗时等
    meta_json = db.Column(db.Text, nullable=True)
