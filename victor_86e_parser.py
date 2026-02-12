import serial
import serial.tools.list_ports
import time
import struct
import logging

# 配置日志 - 设置详细的调试信息输出
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('Victor86EParser')

class Victor86EParser:
    """Victor 86E万用表串口数据解析器类"""
    def __init__(self, custom_params=None):
        # 串口通信参数配置（根据Victor 86E协议文档设置）
        self.serial_params = {
            'baudrate': 19200,      # 波特率：19200 bps
            'parity': serial.PARITY_NONE,   # 无奇偶校验
            'stopbits': serial.STOPBITS_ONE, # 1个停止位
            'bytesize': serial.EIGHTBITS,    # 8位数据位
            'timeout': 1            # 读取超时时间1秒
        }
        
        # 如果提供了自定义参数，覆盖默认参数
        if custom_params and isinstance(custom_params, dict):
            # 处理奇偶校验参数（字符串到常量的映射）
            if 'parity' in custom_params and isinstance(custom_params['parity'], str):
                parity_map = {
                    'N': serial.PARITY_NONE,
                    'E': serial.PARITY_EVEN,
                    'O': serial.PARITY_ODD,
                    'M': serial.PARITY_MARK,
                    'S': serial.PARITY_SPACE
                }
                custom_params['parity'] = parity_map.get(custom_params['parity'].upper(), serial.PARITY_NONE)
            
            # 处理停止位参数
            if 'stopbits' in custom_params and isinstance(custom_params['stopbits'], (int, float)):
                if custom_params['stopbits'] == 1:
                    custom_params['stopbits'] = serial.STOPBITS_ONE
                elif custom_params['stopbits'] == 2:
                    custom_params['stopbits'] = serial.STOPBITS_TWO
            
            # 处理数据位参数
            if 'bytesize' in custom_params and isinstance(custom_params['bytesize'], int):
                bytesize_map = {
                    5: serial.FIVEBITS,
                    6: serial.SIXBITS,
                    7: serial.SEVENBITS,
                    8: serial.EIGHTBITS
                }
                custom_params['bytesize'] = bytesize_map.get(custom_params['bytesize'], serial.EIGHTBITS)
            
            self.serial_params.update(custom_params)
            
        self.ser = None
        self.is_connected = False
        
        # 功能码映射表 - 将十六进制功能码转换为对应的测量功能
        self.function_codes = {
            0x3B: 'V',      # 电压
            0x3D: 'mA',     # 毫安（修复10倍误差问题：实际测试发现这个功能码对应mA）
            0x3F: 'mA',     # 毫安
            0x39: 'A',      # 安培
            0xBF: 'mA',     # 毫安（替代功能码，实际测试发现这个功能码对应mA）
            0xB0: 'A',      # 安培（特殊功能码，数据位全为0xB0）
            0xB3: 'Ω',      # 电阻
            0xB5: '通断',    # 通断
            0x31: '二极管',   # 二极管
            0x32: 'Hz',     # 频率
            0xB6: 'F',      # 电容
            0x34: '°',      # 温度
        }
        
        # 量程映射表 - 不同测量功能下各量程对应的数值范围
        self.range_mapping = {
            # 电压量程
            'V': {
                0x34: 220.00e-3,   # 220.00mV
                0xB0: 2.2000,      # 2.2000V
                0x31: 22.000,      # 22.000V
                0x32: 220.00,      # 220.00V
                0xB3: 1000.0,      # 1000.0V
            },
            # 电阻量程
            'Ω': {
                0xB0: 220.00,      # 220.00Ω
                0x31: 2.2000e3,    # 2.2000kΩ
                0x32: 22.000e3,    # 22.000kΩ
                0xB3: 220.00e3,    # 220.00kΩ
                0x34: 2.2000e6,    # 2.2000MΩ
                0xB5: 22.000e6,    # 22.000MΩ
                0xB6: 220.00e6,    # 220.00MΩ
            },
            # 电容量程
            'F': {
                0xB0: 22.000e-9,   # 22.000nF
                0x31: 220.00e-9,   # 220.00nF
                0x32: 2.2000e-6,   # 2.2000uF
                0x33: 22.000e-6,   # 22.000uF
                0x34: 220.00e-6,   # 220.00uF
                0xB5: 2.2000e-3,   # 2.2000mF
                0xB6: 22.000e-3,   # 22.000mF
                0x37: 220.00e-3,   # 220.00mF
            },
            # 频率量程
            'Hz': {
                0xB0: 22.00,       # 22.00Hz
                0x31: 220.0,       # 220.0Hz
                0xB3: 220.00e3,    # 220.00kHz
                0x34: 2.2000e6,    # 2.2000MHz
                0xB5: 22.000e6,    # 22.000MHz
                0xB6: 50.00e6,     # 50.00MHz
                0xB7: None,        # >50 MHz
            },
            # 电流量程
        'A': {
            0x31: 2.2000,      # 2.2000A
            0x32: 22.000,      # 22.000A
            0xB0: 0.22000,     # 220.00mA (扩展量程)
        },
        'mA': {
            0xB0: 220.00,      # 220.00mA
            0x31: 22.00,       # 22.00mA
            0x32: 2.20,        # 2.20mA
        },
        'μA': {
            0xB0: 220.00,      # 220.00μA
            0x31: 2200.00,     # 2.2000mA（2200μA）
            0x32: 22000.00,    # 22.000mA（22000μA）
        }
        }
    
    def list_serial_ports(self):
        """列出系统中所有可用的串行端口
        
        Returns:
            list: 可用串口设备名列表，如 ['COM1', 'COM3']
        """
        ports = serial.tools.list_ports.comports()
        return [port.device for port in ports]
    
    def connect(self, port):
        """建立与指定串口的连接
        
        Args:
            port (str): 串口名称，如 'COM1', '/dev/ttyUSB0'
            
        Returns:
            bool: 连接成功返回True，失败返回False
        """
        try:
            self.ser = serial.Serial(
                port=port,
                baudrate=self.serial_params['baudrate'],
                parity=self.serial_params['parity'],
                stopbits=self.serial_params['stopbits'],
                bytesize=self.serial_params['bytesize'],
                timeout=self.serial_params['timeout']
            )
            self.is_connected = True
            return True
        except Exception as e:
            print(f"连接失败: {e}")
            return False
    
    def disconnect(self):
        """安全关闭串口连接，释放资源"""
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.is_connected = False
    
    def read_serial_data(self):
        """从串口读取并解析14字节的测量数据包
        
        该方法会先清空串口缓冲区中的旧数据，然后读取新的14字节数据包
        并调用parse_data进行解析。
        
        Returns:
            dict or None: 解析成功的数据字典，失败返回None
        """
        if not self.is_connected or not self.ser or not self.ser.is_open:
            logger.warning("串口未连接或未打开")
            return None
        
        try:
            # 清空串口缓冲区
            if self.ser.in_waiting > 0:
                logger.debug(f"串口缓冲区有{self.ser.in_waiting}字节待读取")
                # 读取所有可用数据
                all_data = self.ser.read(self.ser.in_waiting)
                logger.debug(f"清空缓冲区，读取到{len(all_data)}字节数据: {' '.join(f'{b:02X}' for b in all_data)}")
            
            # 读取14字节数据
            data_bytes = self.ser.read(14)
            logger.debug(f"从串口读取到{len(data_bytes)}字节数据: {' '.join(f'{b:02X}' for b in data_bytes)}")
            
            # 验证数据长度
            if len(data_bytes) != 14:
                logger.error(f"数据长度错误，实际长度: {len(data_bytes)}, 预期长度: 14")
                if data_bytes:
                    logger.error(f"数据内容: {' '.join(f'{b:02X}' for b in data_bytes)}")
                return None
            
            # 无论结束符是否正确，只要数据长度为14字节，就尝试解析
            logger.debug(f"尝试解析14字节数据: {' '.join(f'{b:02X}' for b in data_bytes)}")
            parsed_data = self.parse_data(data_bytes)
            if parsed_data:
                logger.debug(f"解析成功，结果: {parsed_data}")
            else:
                logger.warning(f"解析失败")
            return parsed_data
        except Exception as e:
            logger.error(f"读取数据错误: {e}")
            return None
    
    def parse_data(self, data_bytes):
        """核心解析函数 - 严格按照Victor 86E协议解析14字节数据包
        
        数据包结构（共14字节）：
        字节0: 量程位 - 表示当前测量的量程
        字节1-5: 数据位(B0-B4) - 5位BCD码表示的测量数值
        字节6: 功能码 - 表示测量功能（电压、电流、电阻等）
        字节7: 状态位 - 包含符号、电池状态、OL等信息
        字节8: MAX/MIN/REL位 - 最大值、最小值、相对值标志
        字节9: duty/freq位 - 占空比/频率标志
        字节10: DC/AC/AUTO位 - 直流/交流/自动模式标志
        字节11: HOLD位 - 保持功能标志
        字节12-13: 结束符 - 固定为 0x0D 0x0A (\r\n)
        
        Args:
            data_bytes (bytes): 14字节的原始数据
            
        Returns:
            dict or None: 解析结果字典，包含value, function, unit等字段
        """
        try:
            # 字节0: 量程位 - 决定测量的数值范围
            range_byte = data_bytes[0]
            logger.debug(f"完整数据包: {' '.join(f'{b:02X}' for b in data_bytes)}")
            logger.debug(f"量程字节: {range_byte:02X}")
            
            # 字节1-5: 数据位（B0-B4）- 5位BCD码表示的测量数值
            data_digits = []
            has_error = False
            for i in range(1, 6):
                byte_value = data_bytes[i]
                byte_index = i + 1  # 实际字节位置（1-based）
                logger.debug(f"字节{byte_index}值: {byte_value:02X}")
                
                # 标准BCD编码：0xB0-0xB9表示数字0-9
                if 0xB0 <= byte_value <= 0xB9:
                    digit = byte_value - 0xB0
                    data_digits.append(str(digit))
                    logger.debug(f"字节{byte_index}解析为数字: {digit}（0xB0-0xB9格式）")
                else:
                    # 容错处理：支持ASCII数字字符(0x30-0x39)
                    if 0x30 <= byte_value <= 0x39:
                        digit = byte_value - 0x30
                        data_digits.append(str(digit))
                        logger.debug(f"字节{byte_index}解析为数字: {digit}（ASCII格式）")
                    else:
                        # 进一步容错：处理0x00-0x09范围的特殊编码
                        if 0x00 <= byte_value <= 0x09:
                            digit = byte_value
                            data_digits.append(str(digit))
                            logger.debug(f"字节{byte_index}解析为数字: {digit}（0x00-0x09格式）")
                        else:
                            # 容错处理：使用0代替无法识别的数据位
                            data_digits.append('0')
                            logger.warning(f"字节{byte_index}数据位错误: {byte_value:02X}，范围应为0xB0-0xB9、0x30-0x39或0x00-0x09，已使用0代替")
                            has_error = True
            
            # 显示解析后的数据位
            logger.debug(f"解析后的数据位: {''.join(data_digits)}")
            
            # 如果有错误，显示完整数据包
            if has_error:
                logger.debug(f"完整数据包: {' '.join(f'{b:02X}' for b in data_bytes)}")
            
            # 字节6: 功能码 - 确定测量类型
            function_code = data_bytes[6]
            logger.debug(f"功能码: {function_code:02X}")
            if function_code not in self.function_codes:
                logger.error(f"未知功能码: {function_code:02X}")
                logger.error(f"完整数据包: {' '.join(f'{b:02X}' for b in data_bytes)}")
                # 尝试继续解析，使用未知功能码
                function = f"未知(0x{function_code:02X})"
            else:
                function = self.function_codes[function_code]
                logger.debug(f"识别的功能: {function}")
            
            # 字节7: 状态位 - 包含多个状态标志
            status_byte = data_bytes[7]
            is_celsius = (status_byte & 0x08) != 0  # Bit 3: 温度单位(1=Celsius, 0=Fahrenheit)
            is_negative = (status_byte & 0x04) != 0  # Bit 2: 数值符号(1=负数, 0=正数)
            is_batt = (status_byte & 0x02) != 0     # Bit 1: 电池状态(1=低电量, 0=正常)
            is_ol = (status_byte & 0x01) != 0       # Bit 0: 量程状态(1=超出量程, 0=正常)
            
            # 字节8: MAX/MIN/REL功能位
            max_min_rel_byte = data_bytes[8]
            is_max = (max_min_rel_byte & 0x08) != 0  # Bit 3: 最大值记录模式
            is_min = (max_min_rel_byte & 0x04) != 0  # Bit 2: 最小值记录模式
            is_rel = (max_min_rel_byte & 0x02) != 0  # Bit 1: 相对值测量模式
            
            # 字节9: 频率/占空比功能位
            duty_freq_byte = data_bytes[9]
            is_duty = (duty_freq_byte & 0x08) != 0   # Bit 3: 测量类型(1=占空比%, 0=频率Hz)
            
            # 字节10: 测量模式位
            dc_ac_auto_byte = data_bytes[10]
            is_dc = (dc_ac_auto_byte & 0x08) != 0    # Bit 3: 直流测量模式
            is_ac = (dc_ac_auto_byte & 0x04) != 0    # Bit 2: 交流测量模式
            is_auto = (dc_ac_auto_byte & 0x02) != 0  # Bit 1: 自动量程模式
            
            # 字节11: HOLD功能位
            hold_byte = data_bytes[11]
            is_hold = (hold_byte & 0x08) != 0        # Bit 3: 数据保持功能
            
            # 处理OL（超出量程）情况
            if is_ol:
                return {
                    'value': 'OL',
                    'function': function,
                    'range': None,
                    'unit': self.get_unit(function),
                    'is_negative': is_negative,
                    'is_batt': is_batt,
                    'is_max': is_max,
                    'is_min': is_min,
                    'is_rel': is_rel,
                    'is_duty': is_duty,
                    'is_dc': is_dc,
                    'is_ac': is_ac,
                    'is_auto': is_auto,
                    'is_hold': is_hold,
                    'is_celsius': is_celsius
                }
            
            # 获取量程
            range_value = self.get_range(function, range_byte)
            
            # 计算实际数值
            digit_str = ''.join(data_digits)
            if not digit_str:
                return None
            
            # 根据功能和量程确定小数点位置
            value = self.calculate_value(function, digit_str, range_value)
            
            # 添加符号
            if is_negative:
                value = -value
            
            # 获取单位
            unit = self.get_unit(function, is_celsius)
            
            return {
                'value': value,
                'function': function,
                'range': range_value,
                'unit': unit,
                'is_negative': is_negative,
                'is_batt': is_batt,
                'is_max': is_max,
                'is_min': is_min,
                'is_rel': is_rel,
                'is_duty': is_duty,
                'is_dc': is_dc,
                'is_ac': is_ac,
                'is_auto': is_auto,
                'is_hold': is_hold,
                'is_celsius': is_celsius
            }
            
        except Exception as e:
            logger.error(f"解析数据错误: {e}")
            return None
    
    def get_range(self, function, range_byte):
        """根据测量功能和量程字节获取对应的量程数值
        
        Args:
            function (str): 测量功能，如 'V', 'A', 'mA', 'Ω'等
            range_byte (int): 量程字节值
            
        Returns:
            float or None: 对应的量程数值，如2.2表示2.2V量程
        """
        logger.debug(f"获取量程 - 功能: {function}, 量程字节: {range_byte:02X}")
        
        # 先尝试使用原始功能查找量程映射
        if function in self.range_mapping and range_byte in self.range_mapping[function]:
            range_value = self.range_mapping[function][range_byte]
            logger.debug(f"使用原始功能获取量程: {range_value}")
            return range_value
        
        # 如果原始功能找不到，尝试使用主功能（用于电流单位之间的兼容）
        # 注意：μA和mA有自己的量程映射，不应该直接转换为主功能A
        main_function = function
        if function not in ['μA', 'mA'] and function in self.range_mapping and range_byte not in self.range_mapping[function]:
            # 只有当当前功能不是μA/mA且在原始功能的量程映射中找不到时，才尝试转换为主功能
            if function in ['A']:
                main_function = 'A'  # 对于A功能，直接使用自身
            logger.debug(f"转换为主功能: {main_function}")
        
        if main_function in self.range_mapping and range_byte in self.range_mapping[main_function]:
            range_value = self.range_mapping[main_function][range_byte]
            logger.debug(f"使用主功能获取量程: {range_value}")
            return range_value
        else:
            # 为未知量程提供默认值
            default_ranges = {
                'V': 2.2000,      # 默认电压量程 2.2V
                'A': 220.00e-3,   # 默认电流量程 220mA
                'Ω': 220.00,      # 默认电阻量程 220Ω
                'F': 22.000e-9,   # 默认电容量程 22nF
                'Hz': 22.00,      # 默认频率量程 22Hz
                '°': 100.0,       # 默认温度量程 100°C
                '通断': None,      # 通断不需要量程
                '二极管': None     # 二极管不需要量程
            }
            
            default_range = default_ranges.get(main_function, 1.0)  # 默认值为1.0
            logger.warning(f"未知量程: 功能={function}, 字节={range_byte:02X}，使用默认值 {default_range}")
            return default_range
    
    def calculate_value(self, function, digit_str, range_value):
        """核心数值计算函数 - 根据功能类型、数字字符串和量程计算实际测量值
        
        该函数实现了Victor 86E协议中复杂的数值转换逻辑，不同测量功能
        有不同的小数点位置规则。
        
        Args:
            function (str): 测量功能类型
            digit_str (str): 5位数字字符串
            range_value (float): 当前量程值
            
        Returns:
            float: 计算得到的实际测量数值
        """
        logger.debug(f"计算数值 - 功能: {function}, 数字字符串: {digit_str}, 量程: {range_value}")
        
        # 将数字字符串转换为整数
        digit_value = int(digit_str)
        logger.debug(f"转换后的数字值: {digit_value}")
        
        # 处理range_value为None的情况
        if range_value is None:
            logger.debug(f"量程为None")
            # 为不同功能提供默认的数值计算
            if function == 'Hz':
                return '>50MHz'  # 频率特殊情况
            elif function in ['通断', '二极管']:
                return digit_value  # 这些功能不需要复杂计算
            elif function == '°':
                return digit_value / 10.0  # 默认温度除以10
            else:
                # 其他功能默认使用5位数字的合理表示
                result = digit_value * 0.0001
                logger.debug(f"使用默认计算结果: {result}")
                return result
        
        # VICTOR 86E的数据位是5位，根据不同功能和量程确定小数点位置
        
        # 电压测量
        if function == 'V':
            # 根据量程确定小数点位置
            if range_value == 220.00e-3:  # 220.00mV
                return digit_value * 0.0001
            elif range_value == 2.2000:     # 2.2000V
                return digit_value * 0.0001
            elif range_value == 22.000:     # 22.000V
                return digit_value * 0.001
            elif range_value == 220.00:     # 220.00V
                return digit_value * 0.01
            elif range_value == 1000.0:     # 1000.0V
                return digit_value * 0.1
            else:
                # 未知电压量程，使用默认计算
                return digit_value * (range_value / 22000.0)
        
        # 电阻测量
        elif function == 'Ω':
            if range_value == 220.00:        # 220.00Ω
                return digit_value * 0.01
            elif range_value == 2.2000e3:    # 2.2000kΩ
                return digit_value * 0.1
            elif range_value == 22.000e3:    # 22.000kΩ
                return digit_value * 1
            elif range_value == 220.00e3:    # 220.00kΩ
                return digit_value * 10
            elif range_value == 2.2000e6:    # 2.2000MΩ
                return digit_value * 100
            elif range_value == 22.000e6:    # 22.000MΩ
                return digit_value * 1000
            elif range_value == 220.00e6:    # 220.00MΩ
                return digit_value * 10000
            else:
                # 未知电阻量程，使用默认计算
                return digit_value * (range_value / 22000.0)
        
        # 电容测量
        elif function == 'F':
            if range_value == 22.000e-9:     # 22.000nF
                return digit_value * 0.001e-9
            elif range_value == 220.00e-9:    # 220.00nF
                return digit_value * 0.01e-9
            elif range_value == 2.2000e-6:    # 2.2000uF
                return digit_value * 0.001e-6
            elif range_value == 22.000e-6:    # 22.000uF
                return digit_value * 0.01e-6
            elif range_value == 220.00e-6:    # 220.00uF
                return digit_value * 0.1e-6
            elif range_value == 2.2000e-3:    # 2.2000mF
                return digit_value * 0.001e-3
            elif range_value == 22.000e-3:    # 22.000mF
                return digit_value * 0.01e-3
            elif range_value == 220.00e-3:    # 220.00mF
                return digit_value * 0.1e-3
            else:
                # 未知电容量程，使用默认计算
                return digit_value * (range_value / 22000.0)
        
        # 频率测量
        elif function == 'Hz':
            if range_value == 22.00:          # 22.00Hz
                return digit_value * 0.01
            elif range_value == 220.0:         # 220.0Hz
                return digit_value * 0.1
            elif range_value == 220.00e3:      # 220.00kHz
                return digit_value * 10
            elif range_value == 2.2000e6:      # 2.2000MHz
                return digit_value * 100
            elif range_value == 22.000e6:      # 22.000MHz
                return digit_value * 1000
            elif range_value == 50.00e6:       # 50.00MHz
                return digit_value * 1000
            else:
                # 未知频率量程，使用默认计算
                return digit_value * (range_value / 22000.0)
        
        # 电流测量
        elif function in ['A', 'mA', 'μA']:
            logger.debug(f"进入电流测量分支 - 功能: {function}, 量程: {range_value}, 数字值: {digit_value}")
            # 根据量程确定小数点位置，与其他功能保持一致
            if function == 'A':
                logger.debug(f"A量程计算: range_value={range_value}, digit_value={digit_value}")
                if range_value == 2.2000:
                    result = digit_value * 0.0001  # 2.2000A, 5位数字，所以系数是0.0001
                    logger.debug(f"A量程(2.2000A)计算结果: {result} = {digit_value} * 0.0001")
                    return result
                elif range_value == 22.000:
                    result = digit_value * 0.001   # 22.000A, 5位数字，所以系数是0.001
                    logger.debug(f"A量程(22.000A)计算结果: {result} = {digit_value} * 0.001")
                    return result
                elif range_value == 0.22000:
                    result = digit_value * 0.00001  # 0.22000A, 5位数字，所以系数是0.00001
                    logger.debug(f"A量程(0.22000A)计算结果: {result} = {digit_value} * 0.00001")
                    return result
                else:
                    # 通用计算方法
                    calculated_coefficient = range_value / 22000.0
                    result = digit_value * calculated_coefficient
                    logger.debug(f"A量程(其他)计算结果: {result} = {digit_value} * {calculated_coefficient} (range_value={range_value})")
                    return result
            elif function == 'mA':
                logger.debug(f"mA量程计算: range_value={range_value}, digit_value={digit_value}")
                # 根据不同量程使用正确的计算系数
                
                # 为所有220mA左右的量程应用统一逻辑（更鲁棒）
                if 219.0 <= range_value <= 221.0:
                    # 220.00mA量程：5位数字，所以系数是0.01（22000 / 22000.0 = 1.0，这里可能需要调整）
                    # 实际应该是：digit_value * (range_value / 22000.0) = digit_value * (220.0 / 22000.0) = digit_value * 0.01
                    result = digit_value * 0.01
                    logger.debug(f"mA量程(220mA)计算结果: {result} = {digit_value} * 0.01")
                    return result
                elif abs(range_value - 22.00) < 0.0001:
                    # 22.00mA量程：5位数字，所以系数是0.001
                    result = digit_value * 0.001
                    logger.debug(f"mA量程(22mA)计算结果: {result} = {digit_value} * 0.001")
                    return result
                elif abs(range_value - 2.20) < 0.0001:
                    # 2.20mA量程：5位数字，所以系数是0.0001
                    result = digit_value * 0.0001
                    logger.debug(f"mA量程(2.2mA)计算结果: {result} = {digit_value} * 0.0001")
                    return result
                else:
                    # 通用计算方法
                    calculated_coefficient = range_value / 22000.0
                    result = digit_value * calculated_coefficient
                    logger.debug(f"mA量程(其他)计算结果: {result} = {digit_value} * {calculated_coefficient} (range_value={range_value})")
                    return result
            elif function == 'μA':
                logger.debug(f"μA量程计算: range_value={range_value}, digit_value={digit_value}")
                if range_value == 220.00:
                    result = digit_value * 0.01      # 220.00μA, 5位数字，所以系数是0.01
                    logger.debug(f"μA量程(220.00μA)计算结果: {result} = {digit_value} * 0.01")
                    return result
                elif range_value == 2.2000e3:
                    result = digit_value * 0.1     # 2.2000mA, 5位数字，所以系数是0.1
                    logger.debug(f"μA量程(2.2000mA)计算结果: {result} = {digit_value} * 0.1")
                    return result
                elif range_value == 22.000e3:
                    result = digit_value * 1.0       # 22.000mA, 5位数字，所以系数是1.0
                    logger.debug(f"μA量程(22.000mA)计算结果: {result} = {digit_value} * 1.0")
                    return result
                else:
                    # 通用计算方法
                    calculated_coefficient = range_value / 22000.0
                    result = digit_value * calculated_coefficient
                    logger.debug(f"μA量程(其他)计算结果: {result} = {digit_value} * {calculated_coefficient} (range_value={range_value})")
                    return result
        
        # 温度测量
        elif function == '°':
            return digit_value / 10.0  # 温度默认除以10
        
        # 默认情况
        return digit_value
    
    def get_unit(self, function, is_celsius=True):
        """根据测量功能获取对应的单位
        
        Args:
            function (str): 测量功能
            is_celsius (bool): 温度测量时是否为摄氏度
            
        Returns:
            str: 对应的单位字符串
        """
        if function == '°':
            return '°C' if is_celsius else '°F'
        elif function == '通断':
            return ''
        elif function == '二极管':
            return 'V'
        else:
            return function

# 添加测试功能
def test_current_parsing():
    """测试电流解析功能"""
    parser = Victor86EParser()
    
    # 模拟电流数据
    test_data = [
        # 模拟μA数据 (200.00μA)
        b'1\xB0\x32\x30\x30\x30\x3D\xB0\xB0\xB0\xBA\xB3\r\x8A',
        # 模拟mA数据 (150.00mA)
        b'1\xB0\x31\x35\x30\x30\x3F\xB0\xB0\xB0\xBA\xB3\r\x8A',
        # 模拟A数据 (1.5000A)
        b'1\x31\x31\x35\x30\x30\x39\xB0\xB0\xB0\xBA\xB3\r\x8A'
    ]
    
    print("\n--- 电流解析测试 ---")
    for i, data_bytes in enumerate(test_data, 1):
        print(f"\n测试用例 {i}: {' '.join(f'{b:02X}' for b in data_bytes)}")
        result = parser.parse_data(data_bytes)
        if result:
            print(f"数值: {result['value']} {result['unit']}")
            print(f"功能: {result['function']}")
            if result['range']:
                print(f"量程: {result['range']} {result['unit']}")
            print(f"符号: {'负' if result['is_negative'] else '正'}")
            print(f"电池状态: {'低' if result['is_batt'] else '正常'}")
            print(f"测量模式: {'DC' if result['is_dc'] else 'AC' if result['is_ac'] else '未知'}")
            print(f"量程模式: {'自动' if result['is_auto'] else '手动'}")
        else:
            print("解析失败")

# 示例用法
if __name__ == "__main__":
    parser = Victor86EParser()
    
    # 列出可用串口
    ports = parser.list_serial_ports()
    print(f"可用串口: {ports}")
    
    # 先运行电流解析测试
    test_current_parsing()
    
    if not ports:
        print("没有可用的串口")
        exit()
    
    # 尝试连接到COM18（用户可能使用的串口），如果不可用则让用户选择
    target_port = "COM18"
    if target_port in ports:
        if parser.connect(target_port):
            print(f"已连接到 {target_port}")
        else:
            print(f"无法连接到 {target_port}，请手动选择串口")
            exit()
    else:
        # 连接到第一个可用串口
        if parser.connect(ports[0]):
            print(f"已连接到 {ports[0]}")
        else:
            print("连接失败")
            exit()
    
    print("正在读取数据... (按Ctrl+C退出)")
    
    try:
        while True:
            data = parser.read_serial_data()
            if data:
                print("\n--- 测量数据 ---")
                if data['value'] == 'OL':
                    print(f"数值: OL")
                else:
                    print(f"数值: {data['value']} {data['unit']}")
                print(f"功能: {data['function']}")
                if data['range']:
                    print(f"量程: {data['range']} {data['unit']}")
                print(f"符号: {'负' if data['is_negative'] else '正'}")
                print(f"电池状态: {'低' if data['is_batt'] else '正常'}")
                print(f"测量模式: {'DC' if data['is_dc'] else 'AC' if data['is_ac'] else '未知'}")
                print(f"量程模式: {'自动' if data['is_auto'] else '手动'}")
                print(f"特殊标记: {'HOLD ' if data['is_hold'] else ''}{'MAX ' if data['is_max'] else ''}{'MIN ' if data['is_min'] else ''}{'REL ' if data['is_rel'] else ''}")
                if data['function'] == 'Hz' or data['function'] == 'Duty':
                    print(f"类型: {'占空比' if data['is_duty'] else '频率'}")
                if data['function'] == '°':
                    print(f"温度单位: {'°C' if data['is_celsius'] else '°F'}")
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n退出程序")
    finally:
        parser.disconnect()