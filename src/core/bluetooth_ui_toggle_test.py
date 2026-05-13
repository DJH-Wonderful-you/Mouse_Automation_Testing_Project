import subprocess
import time
from ctypes import windll
import platform

# 运行前自定义次数
while True:
    try:
        cycle_count = int(input("请输入循环次数: "))
        if cycle_count > 0:
            break
        else:
            print("循环次数必须大于0，请重新输入")
    except ValueError:
        print("请输入有效的数字")

# 检测Windows版本
def get_windows_version():
    version = platform.release()
    build = platform.version().split('.')[2] if len(platform.version().split('.')) > 2 else '0'
    if version == '10' and int(build) >= 22000:
        return 'Windows 11'
    elif version == '10':
        return 'Windows 10'
    else:
        return 'Unknown'

# 根据系统版本设置TAB键次数
windows_version = get_windows_version()
print(f"检测到系统: {windows_version}")
if windows_version == 'Windows 11':
    tab_count = 3
    print("Windows 11 系统 - TAB键次数: 3")
elif windows_version == 'Windows 10':
    tab_count = 1
    print("Windows 10 系统 - TAB键次数: 1")
else:
    tab_count = 1
    print("未知系统 - 默认TAB键次数: 1")

# 定义按键函数
def press_key(vk_code):
    windll.user32.keybd_event(vk_code, 0, 0, 0)
    windll.user32.keybd_event(vk_code, 0, 2, 0)

# 定义组合键函数
def press_combination_key(vk_code1, vk_code2):
    windll.user32.keybd_event(vk_code1, 0, 0, 0)
    windll.user32.keybd_event(vk_code2, 0, 0, 0)
    windll.user32.keybd_event(vk_code2, 0, 2, 0)
    windll.user32.keybd_event(vk_code1, 0, 2, 0)

VK_TAB = 0x09
VK_SPACE = 0x20
VK_ALT = 0x12
VK_F4 = 0x73

# 第一次测试前先打开再关闭蓝牙设置窗口（初始化）
print("\n初始化：打开并关闭蓝牙设置窗口...")
subprocess.run("control bthprops.cpl", shell=True)
time.sleep(3)
press_combination_key(VK_ALT, VK_F4)
print("初始化完成")
time.sleep(2)

# 循环执行整个操作
for cycle in range(cycle_count):
    print(f"\n===== 第 {cycle+1} 次循环 =====")
    
    # 1. 打开蓝牙设置
    subprocess.run("control bthprops.cpl", shell=True)
    time.sleep(2)
    
    # 2. 控制 TAB 键（按下和释放）
    for i in range(tab_count):
        press_key(VK_TAB)
        print(f"  第 {i+1} 次 TAB 已按下")
        time.sleep(1)
    
    # 3. 控制空格键点击蓝牙开关
    time.sleep(1)
    press_key(VK_SPACE)
    print("  空格键已按下")
    
    # 4. 关闭设置窗口（Alt+F4）
    time.sleep(1)
    press_combination_key(VK_ALT, VK_F4)
    print("  已关闭窗口")
    
    print(f"第 {cycle+1} 次循环完成")
    time.sleep(5)

print("\n✅ 所有循环已完成！")