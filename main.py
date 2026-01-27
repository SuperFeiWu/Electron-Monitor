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

# ... (保留 calculate_estimated_time 和 push_message 函数不变) ...
def calculate_estimated_time(history, current_kwh, current_time):
    # 这里直接复用你原来的代码，逻辑不需要变
    if not history:
        return {"power_1h": 0, "power_24h": 0, "hours_left": 9999}
    
    def find_past_record(delta_hours):
        target_time = current_time - datetime.timedelta(hours=delta_hours)
        closest_rec = None
        min_diff = float('inf')
        for rec in reversed(history):
            rec_time = datetime.datetime.fromisoformat(rec['time'])
            diff = abs((rec_time - target_time).total_seconds())
            if diff < min_diff:
                min_diff = diff
                closest_rec = rec
                closest_rec['dt'] = rec_time
            if diff > 3600 * (delta_hours + 1): break
        return closest_rec

    rec_1h = find_past_record(1)
    rec_24h = find_past_record(24)
    power_1h = 0.0
    power_24h = 0.0
    
    if rec_1h:
        time_diff_h = (current_time - rec_1h['dt']).total_seconds() / 3600
        kwh_diff = rec_1h['kwh'] - current_kwh
        if time_diff_h > 0.1: power_1h = kwh_diff / time_diff_h

    if rec_24h:
        time_diff_h = (current_time - rec_24h['dt']).total_seconds() / 3600
        kwh_diff = rec_24h['kwh'] - current_kwh
        if time_diff_h > 0.5: power_24h = kwh_diff / time_diff_h

    used_power = max(power_24h, 0.01) if power_1h < -0.1 else (power_1h if power_1h > 0 else max(power_24h, 0.01))
    hours_left = 9999.0
    if used_power > 0.001: hours_left = current_kwh / used_power

    return {"power_1h": round(power_1h, 3), "power_24h": round(power_24h, 3), "hours_left": round(hours_left, 1)}

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