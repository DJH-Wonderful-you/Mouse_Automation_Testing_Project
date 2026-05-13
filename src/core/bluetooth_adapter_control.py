import subprocess
import time
import re
from datetime import datetime

# ====================== 配置项 ======================
TEST_TIMES = 100       # 压测次数 0=无限循环
WAIT_SECONDS = 10       # 操作间隔
LOG_FILE = "bt_enum_status_log.txt"

# 目标蓝牙设备配置（用于检测连接状态）
TARGET_DEVICE_NAME = "SUNWINON Mouse MOCO"  # 设备名称关键字
TARGET_DEVICE_MAC = "D1:0B:5E:F2:1E:94"  # 设备MAC地址（可选）

# TARGET_DEVICE_NAME = "LOWA Mouse"  # 设备名称关键字
# TARGET_DEVICE_MAC = "F0:61:EE:DE:5A:7F"  # 设备MAC地址（可选）
# ====================================================

def run_cmd(cmd):
    """执行系统命令"""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, encoding="gbk"
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except:
        return "", "", -1

def run_powershell(script):
    """执行PowerShell命令（更可靠的PnP设备操作）"""
    try:
        full_script = (
            '$ErrorActionPreference="SilentlyContinue";' + script
        )
        
        # 隐藏PowerShell窗口
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        
        result = subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", full_script],
            capture_output=True, text=True, encoding="gbk", timeout=15,
            startupinfo=startupinfo
        )
        return result.stdout.strip(), result.stderr.strip(), result.returncode
    except Exception as e:
        return "", str(e), -1

def check_admin():
    """检查管理员权限"""
    _, _, code = run_cmd("net session >nul 2>&1")
    return code == 0

def get_all_enumerators():
    """获取所有蓝牙枚举器（使用PowerShell）"""
    devices = []
    cmd = '''
    Get-PnpDevice | Where-Object {
        $_.FriendlyName -eq 'Microsoft 蓝牙枚举器' -or 
        $_.FriendlyName -eq 'Microsoft 蓝牙 LE 枚举器'
    } | Select-Object FriendlyName, InstanceId, Status | ConvertTo-Json -Compress -Depth 3
    '''
    stdout, stderr, code = run_powershell(cmd)
    if code == 0 and stdout:
        try:
            import json
            data = json.loads(stdout)
            items = data if isinstance(data, list) else [data]
            for item in items:
                name = item.get("FriendlyName", "")
                iid = item.get("InstanceId", "")
                if name and iid:
                    devices.append((name, iid))
        except Exception as e:
            print(f"解析JSON失败: {e}")
            # 直接执行原始命令获取设备
            cmd_raw = '''
            Get-PnpDevice | Where-Object {
                $_.FriendlyName -eq 'Microsoft 蓝牙枚举器' -or 
                $_.FriendlyName -eq 'Microsoft 蓝牙 LE 枚举器'
            }
            '''
            stdout_raw, _, _ = run_powershell(cmd_raw)
            print(f"原始输出: {stdout_raw}")
    return devices

def get_bluetooth_devices(name_keyword="", mac=""):
    """获取蓝牙设备列表及连接状态"""
    devices = []
    cmd = '''
    Get-PnpDevice | Where-Object {
        $_.InstanceId -match '^(BTHENUM|BTHLEDEVICE|BTHLE)\\\\'
    } | Select-Object FriendlyName, InstanceId, Status, Present | ConvertTo-Json
    '''
    stdout, stderr, code = run_powershell(cmd)
    if code == 0 and stdout:
        try:
            import json
            data = json.loads(stdout)
            items = data if isinstance(data, list) else [data]
            for item in items:
                name = item.get("FriendlyName", "")
                iid = item.get("InstanceId", "")
                status = item.get("Status", "")
                present = item.get("Present", False)
                
                if not name or not iid:
                    continue
                
                mac_addr = extract_mac_from_instance(iid)
                is_connected = (status == "OK" and present)
                
                devices.append({
                    "name": name,
                    "instance_id": iid,
                    "status": status,
                    "present": present,
                    "mac": mac_addr,
                    "connected": is_connected
                })
        except Exception as e:
            write_log(f"⚠️ 解析蓝牙设备数据失败: {e}")
    return devices

def extract_mac_from_instance(instance_id):
    """从实例ID中提取MAC地址"""
    match = re.search(r'([0-9A-Fa-f]{2}[-:]){5}[0-9A-Fa-f]{2}', instance_id)
    if match:
        return match.group(0)
    match = re.search(r'([0-9A-Fa-f]{12})', instance_id)
    if match:
        raw = match.group(1)
        return ":".join(raw[i:i+2] for i in range(0, 12, 2))
    return ""

def check_target_device_connected(name_keyword, mac=""):
    """检查目标蓝牙设备是否连接
    
    Returns:
        tuple: (是否连接, 匹配的设备列表)
    """
    all_devices = get_bluetooth_devices()
    matched = []
    
    name_kw = name_keyword.lower().strip()
    target_mac = normalize_mac(mac)
    
    for device in all_devices:
        name_match = name_kw in device["name"].lower()
        mac_match = target_mac and normalize_mac(device["mac"]) == target_mac
        
        if name_match or mac_match:
            matched.append(device)
    
    connected = any(d["connected"] for d in matched)
    return connected, matched

def normalize_mac(mac):
    """标准化MAC地址格式"""
    cleaned = re.sub(r'[^0-9A-Fa-f]', '', mac).upper()
    if len(cleaned) != 12:
        return ""
    return ":".join(cleaned[i:i+2] for i in range(0, 12, 2))

def check_device_valid(name, instance_id):
    """检测设备是否存在且状态正常（使用PowerShell查询，不禁用设备）"""
    cmd = f'Get-PnpDevice -InstanceId "{instance_id}" | Select-Object Status, Present | ConvertTo-Json'
    stdout, stderr, code = run_powershell(cmd)
    
    if code != 0 or not stdout:
        return False
    
    try:
        import json
        data = json.loads(stdout)
        # 检查设备是否存在且状态不是错误状态
        status = data.get("Status", "").upper()
        present = data.get("Present", True)
        
        # 状态为 OK/DISCONNECTED/UNKNOWN 都认为是有效设备
        # 只有系统关键设备会返回非零退出码（无法禁用），但可以被查询
        return present and status not in {"ERROR", "FAILED", "NOT PRESENT"}
    except Exception:
        # 如果解析失败，但至少命令执行成功，认为设备存在
        return True

def write_log(msg):
    """控制台+文件双日志"""
    time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log = f"[{time_str}] {msg}"
    # 移除可能导致编码问题的字符
    log = log.encode('ascii', 'ignore').decode('ascii')
    print(log)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log + "\n")

def switch_device(name, instance_id, enable=True):
    """操作设备（优先使用pnputil，失败后回退到PowerShell）"""
    action = "启用" if enable else "禁用"
    
    # 优先使用pnputil命令
    if not enable:  # 禁用操作
        pnputil_cmd = f'pnputil /disable-device "{instance_id}"'
        stdout, stderr, code = run_cmd(pnputil_cmd)
        if code == 0:
            write_log(f"✅ {name} | {action}成功 (pnputil)")
            return True
        else:
            write_log(f"⚠️ {name} | {action}失败 (pnputil)，尝试PowerShell...")
    
    # 回退到PowerShell命令
    powershell_cmd = f'Get-PnpDevice -InstanceId "{instance_id}" | {"Enable" if enable else "Disable"}-PnpDevice -Confirm:$false'
    stdout, stderr, code = run_powershell(powershell_cmd)
    
    if code == 0:
        write_log(f"✅ {name} | {action}成功 (PowerShell)")
        return True
    else:
        err_msg = stderr[:100] if stderr else "未知错误"
        write_log(f"❌ {name} | {action}失败 | 错误: {err_msg}")
        return False

def main():
    write_log("="*80)
    write_log("【终极状态版】Microsoft 蓝牙枚举器 压测（无未知状态 + 连接状态检测）")
    write_log("="*80)

    if not check_admin():
        write_log("❌ 必须【管理员身份】运行！")
        return

    # 1. 获取所有枚举器设备
    all_devices = get_all_enumerators()
    if not all_devices:
        write_log("❌ 未找到蓝牙枚举器设备")
        return

    # 2. 自动筛选有效设备
    valid_devices = []
    write_log("🔍 正在检测可操作的有效设备...")
    for name, iid in all_devices:
        if check_device_valid(name, iid):
            valid_devices.append((name, iid))

    if not valid_devices:
        write_log("❌ 未找到可操作的有效蓝牙枚举器")
        return

    # ====================== 核心：内部状态跟踪（解决未知状态） ======================
    device_status = {}  # 记录设备状态 True=启用 False=禁用
    write_log("📌 已筛选【有效设备】：")
    for name, iid in valid_devices:
        device_status[iid] = True  # 初始化默认：已启用
        write_log(f"   → {name} | {iid} | 当前状态：✅ 已启用")

    # 3. 检测目标蓝牙设备初始状态
    write_log(f"\n📡 检测目标蓝牙设备: {TARGET_DEVICE_NAME} ({TARGET_DEVICE_MAC})")
    initial_connected, initial_devices = check_target_device_connected(TARGET_DEVICE_NAME, TARGET_DEVICE_MAC)
    if initial_devices:
        for dev in initial_devices:
            status_text = "✅ 已连接" if dev["connected"] else "❌ 未连接"
            write_log(f"   → {dev['name']} | MAC={dev['mac']} | 状态={status_text}")
    else:
        write_log("   ⚠️ 未找到匹配的蓝牙设备")

    # 4. 压测循环
    success = 0
    fail = 0
    current = 0
    connection_stats = {"connected": 0, "disconnected": 0}  # 连接状态统计

    try:
        while True:
            current += 1
            if TEST_TIMES != 0 and current > TEST_TIMES:
                write_log(f"\n🏁 完成 {TEST_TIMES} 轮压测，自动结束")
                break

            write_log(f"\n=============== 第 {current} 轮压测 ===============")
            round_ok = True

            # ---------------- 禁用阶段 ----------------
            write_log("\n📌 开始禁用设备...")
            for name, iid in valid_devices:
                status_before = "✅ 已启用" if device_status[iid] else "❌ 已禁用"
                write_log(f"[{name}] 禁用前状态：{status_before}")
                
                if switch_device(name, iid, enable=False):
                    device_status[iid] = False  # 更新状态为禁用
                else:
                    round_ok = False
                
                status_after = "✅ 已启用" if device_status[iid] else "❌ 已禁用"
                write_log(f"[{name}] 禁用后状态：{status_after}")
            
            time.sleep(WAIT_SECONDS)

            # 检查禁用后的连接状态
            write_log("\n📡 检查禁用后的蓝牙连接状态...")
            disabled_connected, disabled_devices = check_target_device_connected(TARGET_DEVICE_NAME, TARGET_DEVICE_MAC)
            for dev in disabled_devices:
                status_text = "✅ 已连接" if dev["connected"] else "❌ 未连接"
                write_log(f"   → {dev['name']} | 状态={status_text}")
            
            if not disabled_connected:
                connection_stats["disconnected"] += 1
                write_log("   ✅ 禁用后设备已断开（符合预期）")
            else:
                write_log("   ⚠️ 禁用后设备仍连接（可能异常）")

            # ---------------- 启用阶段 ----------------
            write_log("\n📌 开始启用设备...")
            for name, iid in valid_devices:
                status_before = "✅ 已启用" if device_status[iid] else "❌ 已禁用"
                write_log(f"[{name}] 启用前状态：{status_before}")
                
                if switch_device(name, iid, enable=True):
                    device_status[iid] = True  # 更新状态为启用
                else:
                    round_ok = False
                
                status_after = "✅ 已启用" if device_status[iid] else "❌ 已禁用"
                write_log(f"[{name}] 启用后状态：{status_after}")
            
            time.sleep(WAIT_SECONDS)

            # 检查启用后的连接状态
            write_log("\n📡 检查启用后的蓝牙连接状态...")
            enabled_connected, enabled_devices = check_target_device_connected(TARGET_DEVICE_NAME, TARGET_DEVICE_MAC)
            for dev in enabled_devices:
                status_text = "✅ 已连接" if dev["connected"] else "❌ 未连接"
                write_log(f"   → {dev['name']} | 状态={status_text}")
            
            if enabled_connected:
                connection_stats["connected"] += 1
                write_log("   ✅ 启用后设备已连接（符合预期）")
            else:
                write_log("   ⚠️ 启用后设备未连接（可能异常）")

            # 统计
            if round_ok:
                success += 1
            else:
                fail += 1
            write_log(f"\n📊 本轮统计：成功={success} | 失败={fail}")
            write_log(f"📊 连接状态统计：断开={connection_stats['disconnected']} | 重连={connection_stats['connected']}")

    except KeyboardInterrupt:
        write_log("\n⏹️  用户手动停止压测")
    finally:
        write_log("="*80)
        write_log(f"压测结束 | 总成功：{success} | 总失败：{fail}")
        write_log(f"连接状态统计：断开={connection_stats['disconnected']} | 重连={connection_stats['connected']}")
        write_log(f"日志文件：{LOG_FILE}")
        write_log("="*80)

if __name__ == "__main__":
    main()