# from pysnmp.hlapi import *

# IP = "192.168.250.11"          # SW1
# COMMUNITY = "snmpread@123"     # 你的 community

# def snmp_walk(oid):
#     print(f"\n=== WALK {oid} ===")
#     for (errorIndication, errorStatus, errorIndex, varBinds) in nextCmd(
#         SnmpEngine(),
#         CommunityData(COMMUNITY, mpModel=1),  # v2c
#         UdpTransportTarget((IP, 161), timeout=5, retries=2),
#         ContextData(),
#         ObjectType(ObjectIdentity(oid)),
#         lexicographicMode=False
#     ):
#         if errorIndication:
#             print("errorIndication:", errorIndication)
#             return
#         if errorStatus:
#             print("errorStatus:", errorStatus.prettyPrint())
#             return

#         for name, val in varBinds:
#             print(f"{name.prettyPrint()} = {val.prettyPrint()}")

# if __name__ == "__main__":
#     # 1) 先验证 SNMP 基础：设备名 sysName.0
#     snmp_walk("1.3.6.1.2.1.1.5")

#     # 2) 验证 LLDP 邻居名字表：lldpRemSysName
#     snmp_walk("1.0.8802.1.1.2.1.4.1.1.9")

#     # 3) 可选：验证 LLDP 远端端口描述：lldpRemPortDesc
#     snmp_walk("1.0.8802.1.1.2.1.4.1.1.8")
#     snmp_walk("1.0.8802.1.1.2.1.3.2")  # lldpLocChassisId
#     snmp_walk("1.0.8802.1.1.2.1.3.7")  # lldpLocPortTable
