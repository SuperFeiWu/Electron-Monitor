import requests
import json
import re
import os
import logging
import datetime
import sys

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 常量定义
DATA_FILE = "data.json"
# 前端读取的配置文件（只包含 id 和 name，不包含敏感 url 和 token）
PUBLIC_CONFIG_FILE = "public_config.json"
MAX_HISTORY_DAYS = 180

def load_json(filepath, default):
    if not os.path.exists(filepath):
        return default
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default

def save_json(filepath, data):
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"保存 {filepath} 失败: {e}")

def get_config():
    """
    优先从环境变量 'APP_CONFIG' 读取配置（GitHub Actions 环境）。
    如果是本地运行，尝试读取 'private_config.json'。
    """
    env_config = os.environ.get("APP_CONFIG")
    if env_config:
        try:
            logging.info("检测到环境变量配置，正在加载...")
            return json.loads(env_config)
        except json.JSONDecodeError:
            logging.error("环境变量 APP_CONFIG 格式错误")
            return []
    
    # 本地回退方案
    if os.path.exists("config.json"):
        logging.info("正在加载本地 config.json...")
        return load_json("config.json", [])
    
    return []

def get_electricity_balance(url):
    """爬取剩余电量"""
    if not url: return None
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "keep-alive",
        "Host": "yktyd.ecust.edu.cn",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": "Mozilla/5.0 (Linux; U; Android 4.1.2; zh-cn; Chitanda/Akari) AppleWebKit/534.30 (KHTML, like Gecko) Version/4.0 Mobile Safari/534.30 MicroMessenger/6.0.0.58_r884092.501 NetType/WIFI",
        }
    try:
        # 增加随机延时防止封禁（可选），但在Github Actions IP经常变动，通常没事
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        match = re.search(r"(-?\d+(\.\d+)?)度", response.text)
        if match:
            return float(match.group(1))
        else:
            logging.warning(f"页面解析失败，未找到'度'字样。状态码: {response.status_code}")
            return None
    except Exception as e:
        logging.error(f"爬取异常: {e}")
        return None

def calculate_estimated_time(history, current_kwh, current_time):
    """
    计算功率和预计剩余时间
    修正版：不再修改 history 中的原始数据，避免污染 data.json
    """
    if not history:
        return {"power_1h": 0, "power_24h": 0, "hours_left": 9999}
    
    # 辅助函数：解析时间字符串，返回 datetime 对象
    def get_time(rec):
        return datetime.datetime.fromisoformat(rec['time'])

    def find_past_record(delta_hours):
        target_time = current_time - datetime.timedelta(hours=delta_hours)
        best_match = None
        min_diff = float('inf')
        
        # 倒序查找，找离目标时间点最近的记录
        for rec in reversed(history):
            rec_dt = get_time(rec)
            diff = abs((rec_dt - target_time).total_seconds())
            
            if diff < min_diff:
                min_diff = diff
                # 返回一个元组 (记录本身, 解析后的时间对象)
                # 这样就不需要修改 rec 字典了
                best_match = (rec, rec_dt)
            
            # 如果差异超过范围（比如找1小时前的，结果找到了5小时前的），停止搜索
            if diff > 3600 * (delta_hours + 1): 
                break
        return best_match

    # 获取 1小时前 和 24小时前 的记录数据
    match_1h = find_past_record(1)
    match_24h = find_past_record(24)
    
    power_1h = 0.0
    power_24h = 0.0
    
    # 计算 1小时功率
    if match_1h:
        rec, rec_dt = match_1h
        time_diff_h = (current_time - rec_dt).total_seconds() / 3600
        kwh_diff = rec['kwh'] - current_kwh # 消耗量 = 过去 - 现在
        if time_diff_h > 0.1: # 避免除以0或时间过短
            power_1h = kwh_diff / time_diff_h

    # 计算 24小时平均功率
    if match_24h:
        rec, rec_dt = match_24h
        time_diff_h = (current_time - rec_dt).total_seconds() / 3600
        kwh_diff = rec['kwh'] - current_kwh
        if time_diff_h > 0.5:
            power_24h = kwh_diff / time_diff_h

    # 逻辑修正：如果功率计算出是负数（比如刚充了电），或者非常小，进行处理
    # 优先使用 1h 功率，如果 1h 数据异常（例如负数），则回退到 24h
    used_power = power_1h
    if used_power <= 0: 
        used_power = power_24h
    
    # 兜底：如果还是 0 或负数，给一个极小值避免除以 0
    if used_power <= 0.001: 
        used_power = 0.01

    hours_left = 9999.0
    if current_kwh > 0:
        hours_left = current_kwh / used_power

    return {
        "power_1h": round(power_1h, 3), 
        "power_24h": round(power_24h, 3), 
        "hours_left": round(hours_left, 1)
    }

def push_message(tokens, title, content):
    # 复用原来的代码
    url = "http://www.pushplus.plus/send"
    for token in tokens:
        if not token: continue
        try:
            requests.post(url, json={"token": token, "title": title, "content": content, "template": "markdown"}, timeout=5)
        except Exception: pass

def main():
    configs = get_config()
    if not configs:
        logging.error("没有找到配置信息，请检查环境变量 APP_CONFIG 或 config.json")
        sys.exit(1)

    full_data = load_json(DATA_FILE, {})
    current_time = datetime.datetime.now()
    
    # 用于生成前端使用的安全配置
    public_configs = []

    for room in configs:
        room_id = room.get('id')
        room_name = room.get('name')
        
        # 添加到公开配置列表（只保留非敏感信息）
        public_configs.append({
            "id": room_id,
            "name": room_name
        })

        logging.info(f"正在处理: {room_name}")
        kwh = get_electricity_balance(room.get('url'))
        
        if kwh is None:
            logging.warning(f"{room_name} 获取数据失败，跳过本次更新")
            continue

        room_history = full_data.get(room_id, [])
        stats = calculate_estimated_time(room_history, kwh, current_time)
        
        new_record = {
            "time": current_time.isoformat(),
            "kwh": kwh,
            "power_1h": stats['power_1h'],
            "power_24h": stats['power_24h'],
            "estimated_hours": stats['hours_left']
        }
        
        room_history.append(new_record)
        
        # 数据保留逻辑
        cutoff_date = current_time - datetime.timedelta(days=MAX_HISTORY_DAYS)
        room_history = [r for r in room_history if datetime.datetime.fromisoformat(r['time']) > cutoff_date]
        full_data[room_id] = room_history

        # 报警逻辑 (复用原来的，略微简化显示)
        alert_msg = []
        is_alert = False
        if kwh < room.get('alert_threshold_kwh', 10):
            alert_msg.append(f"⚠️ 电量低: {kwh} kWh")
            is_alert = True
        if stats['hours_left'] < room.get('alert_threshold_hours', 24) and stats['hours_left'] > 0:
            alert_msg.append(f"⏳ 时间紧: 剩 {stats['hours_left']}h")
            is_alert = True
            
        if is_alert:
            detail = f"- 房间: {room_name}\n- 时间: {current_time.strftime('%m-%d %H:%M')}\n- 详情: {', '.join(alert_msg)}"
            push_message(room.get('pushplus_tokens', []), f"{room_name} 预警", detail)

    # 1. 保存完整的历史数据
    save_json(DATA_FILE, full_data)
    # 2. 保存给前端用的脱敏配置
    save_json(PUBLIC_CONFIG_FILE, public_configs)
    
    logging.info("数据更新完成")

if __name__ == "__main__":
    main()
