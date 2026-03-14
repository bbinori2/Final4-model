# blacklist.py — 官方黑名單 + 使用者黑名單
import csv
import os
OFFICIAL_BLACKLIST = set()
USER_BLACKLIST = set()
USER_FILE = "user_blacklist.txt"

def load_blacklist(csv_path: str):
    global OFFICIAL_BLACKLIST

    try:
        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                url = (row.get("url") or "").strip()
                if url:
                    OFFICIAL_BLACKLIST.add(url)

        print(f"[BLACKLIST] 已載入官方黑名單 {len(OFFICIAL_BLACKLIST)} 筆")
    except Exception as e:
        print("[BLACKLIST] 官方黑名單載入失敗:", e)

    load_user_blacklist()

def load_user_blacklist():
    global USER_BLACKLIST
    if not os.path.exists(USER_FILE):
        return

    try:
        with open(USER_FILE, "r", encoding="utf-8") as f:
            for line in f:
                url = line.strip()
                if url:
                    USER_BLACKLIST.add(url)

        print(f"[BLACKLIST] 已載入使用者黑名單 {len(USER_BLACKLIST)} 筆")
    except Exception as e:
        print("[BLACKLIST] 使用者黑名單載入失敗:", e)

def add_to_user_blacklist(url: str) -> bool:
    global USER_BLACKLIST
    url = url.strip()
    if not url:
        return False
    if url in USER_BLACKLIST:
        return True

    try:
        USER_BLACKLIST.add(url)
        with open(USER_FILE, "a", encoding="utf-8") as f:
            f.write(url + "\n")
        return True
    except Exception as e:
        print("[BLACKLIST] 新增使用者黑名單失敗:", e)
        return False

def delete_from_user_blacklist(url: str) -> bool:
    global USER_BLACKLIST
    url = url.strip()
    if url not in USER_BLACKLIST:
        return False

    try:
        USER_BLACKLIST.remove(url)
        with open(USER_FILE, "w", encoding="utf-8") as f:
            for u in USER_BLACKLIST:
                f.write(u + "\n")
        return True
    except Exception as e:
        print("[BLACKLIST] 刪除使用者黑名單失敗:", e)
        return False

def is_blacklisted(url: str) -> bool:
    url = url.strip()
    return url in OFFICIAL_BLACKLIST or url in USER_BLACKLIST

#  新增：回傳命中來源
def check_blacklist_source(url: str):
    url = url.strip()
    if url in OFFICIAL_BLACKLIST:
        return "official"
    if url in USER_BLACKLIST:
        return "user"
    return None

def get_user_blacklist() -> list:
    #依照時間排序
    if not os.path.exists(USER_FILE):
        return []
        
    urls = []
    try:
        with open(USER_FILE, "r", encoding="utf-8") as f:
            for line in f:
                u = line.strip()
                if u:
                    urls.append(u)
        return urls
    except:
        return []
def clear_user_blacklist() -> bool:
    """清空所有使用者黑名單（記憶體 + 檔案）"""
    global USER_BLACKLIST
    
    try:
        # 1. 清空記憶體中的集合
        USER_BLACKLIST.clear()
        
        # 2. 清空檔案內容 (以 "w" 模式開啟但不寫入任何東西，就會清空檔案)
        with open(USER_FILE, "w", encoding="utf-8") as f:
            pass
            
        print("[BLACKLIST] 使用者黑名單已全部清空")
        return True
        
    except Exception as e:
        print("[BLACKLIST] 清空使用者黑名單失敗:", e)
        return False