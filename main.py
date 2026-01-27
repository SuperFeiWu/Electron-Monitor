import requests
import json
import os
import datetime
import re
import time

# 从 Secrets 获取用户配置 (JSON 字符串)
# 格式: [{"name": "宿舍A", "url": "..."}, {"name": "宿舍B", "url": "..."}]
USERS_CONFIG = os.environ.get("USERS_CONFIG")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Mobile Safari/537.36",
}

def get_beijing_time():
    utc_now = datetime.datetime.utcnow()
    beijing_time = utc_now + datetime.timedelta(hours=8)
    return beijing_time.strftime("%Y-%m-%d %H:%M:%S")

def fetch_electricity(url):
    try:
        # 添加随机延迟，避免并发请求被防火墙拦截
        time.sleep(1)
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        match = re.search(r"(-?\d+(\.\d+)?)度", resp.text)
        if match:
            return float(match.group(1))
    except Exception as e:
        print(f"Error fetching: {e}")
    return None

def main():
    if not USERS_CONFIG:
        print("Error: USERS_CONFIG not found.")
        exit(1)
    
    try:
        users = json.loads(USERS_CONFIG)
    except json.JSONDecodeError:
        print("Error: USERS_CONFIG JSON format is incorrect.")
        exit(1)

    file_path = "data.json"
    # 数据结构: {"宿舍A": [...], "宿舍B": [...]}
    all_data = {}

    # 1. 读取旧数据
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                all_data = json.load(f)
        except:
            all_data = {}

    current_time = get_beijing_time()
    
    # 2. 循环抓取
    for user in users:
        name = user['name']
        url = user['url']
        print(f"Fetching for {name}...")
        
        kwh = fetch_electricity(url)
        
        if kwh is not None:
            print(f"  -> Success: {kwh} kWh")
            
            # 初始化该用户的数据列表
            if name not in all_data:
                all_data[name] = []
            
            # 追加数据
            new_record = {"time": current_time, "kWh": kwh}
            all_data[name].append(new_record)
            
            # 限制每个用户保留最近 2000 条
            if len(all_data[name]) > 2000:
                all_data[name] = all_data[name][-2000:]
        else:
            print(f"  -> Failed.")

    # 3. 保存
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    main()