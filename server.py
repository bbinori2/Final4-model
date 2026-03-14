# server.py

from flask import Flask, request, jsonify
from flask_cors import CORS
import time
import datetime
import os
import requests
import tempfile
from html_utils import extract_relevant_html, extract_urls
import multiprocessing as mp
import traceback
from blacklist import (
    load_blacklist,
    is_blacklisted,
    check_blacklist_source,
    add_to_user_blacklist,
    delete_from_user_blacklist,
    get_user_blacklist,
    clear_user_blacklist
)
from analyzer import analyze_deep, is_safe_domain   # 引入 is_safe_domain
from tools import check_url_safety, analyze_domain_age, check_url_patterns  # 靜態工具 - 已停用
from urllib.parse import urlparse   
from flask import make_response

import cot_graph

app = Flask(__name__)
CORS(app)

# ====== LLM 深度分析超時設定 ======
DEEP_ANALYZE_TIMEOUT_SEC = 150

def log(title):
    print("\n==========", title, "==========")

def _mp_worker(q, func, args, kwargs):
    """Top-level function for multiprocessing (Windows spawn needs picklable target)."""
    try:
        res = func(*args, **kwargs)
        q.put(("ok", res))
    except Exception as e:
        q.put(("err", f"{e}\n{traceback.format_exc()}"))

def run_with_timeout(func, args=(), kwargs=None, timeout=45):
    if kwargs is None:
        kwargs = {}
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_mp_worker, args=(q, func, args, kwargs))
    p.start()
    p.join(timeout)
    if p.is_alive():
        print(f"[TIMEOUT] terminate process pid={p.pid}")
        p.terminate()
        p.join(2)
        raise TimeoutError("timeout")
    if q.empty():
        raise RuntimeError("no result returned")
    status, payload = q.get()
    if status == "ok":
        return payload
    else:
        raise RuntimeError(payload)

@app.route("/user_blacklist", methods=["GET"])
def get_blacklist_route():
    return jsonify({"success": True, "list": get_user_blacklist()})

@app.route("/add_blacklist", methods=["POST"])
def add_blacklist_route():
    data = request.json or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"success": False, "message": "網址不可為空"})
    ok = add_to_user_blacklist(url)
    return jsonify({"success": ok, "message": "已成功加入" if ok else "加入失敗"})

@app.route("/delete_blacklist", methods=["POST"])
def delete_blacklist_route():
    data = request.json or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"success": False, "message": "網址不可為空"})
    ok = delete_from_user_blacklist(url)
    return jsonify({"success": ok, "message": "已刪除" if ok else "找不到此網址"})

@app.route('/clear_blacklist', methods=['POST'])
def handle_clear_blacklist():
    success = clear_user_blacklist()
    if success:
        return jsonify({"success": True, "message": "使用者黑名單已全部清空"})
    else:
        return jsonify({"success": False, "message": "清空失敗，請檢查伺服器日誌"})

@app.route("/deep_analyze_url", methods=["POST"])
def deep_analyze_url():
    data = request.json or {}
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"success": False, "message": "缺少 URL"}), 400

    log("收到深度 URL 分析請求")
    print("URL:", url)

    # 嘗試抓 HTML
    try:
        resp = requests.get(url, timeout=8, headers={
            "User-Agent": "Mozilla/5.0"
        })
        html = resp.text
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"無法取得 HTML：{e}",
            "is_potential_phishing": False,
            "explanation": "無法抓取網頁內容",
        })

    # 把 HTML 丟給深度分析
    data = request.json or {}
    model = data.get("model", "qwen3:8b")

    t0 = time.time()
    try:
        result = run_with_timeout(
            analyze_deep,
            args=(html,),
            kwargs={"model": model},
            timeout=DEEP_ANALYZE_TIMEOUT_SEC
        )
        result["success"] = True
        result["url"] = url
        return jsonify(result)
    except TimeoutError:
        elapsed = round(time.time() - t0, 2)
        return jsonify({
            "success": False,
            "url": url,
            "message": "超時分析失敗",
            "error": "timeout",
            "elapsed_time": elapsed,
            "is_potential_phishing": False,
            "explanation": f"深度分析逾時（>{DEEP_ANALYZE_TIMEOUT_SEC} 秒），請稍後重試或更換模型。"
        }), 200

    except Exception as e:
        elapsed = round(time.time() - t0, 2)
        return jsonify({
            "success": False,
            "url": url,
            "message": "分析失敗",
            "error": str(e),
            "elapsed_time": elapsed,
            "is_potential_phishing": False,
            "explanation": "深度分析過程發生例外"
        }), 200

@app.route("/static_analyze", methods=["POST"])
def static_analyze_route():
    t0 = time.time()
    data = request.json or {}
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({
            "ok": False,
            "error": "缺少 url 參數",
            "level": "unknown"
        }), 400

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log("收到靜態分析請求（SERP）")
    print(f"時間：{now}")
    print(f"IP  ：{request.remote_addr}")
    print(f"URL ：{url}")

    # ---------- 1. 黑名單檢查 ----------
    bl_source = check_blacklist_source(url)   # official / user / None
    if bl_source is not None:
        elapsed = round(time.time() - t0, 2)
        log("黑名單命中（static_analyze）")
        print(f"黑名單來源：{bl_source}")
        print(f"耗時：{elapsed} 秒")

        return jsonify({
            "phase": "blacklist",
            "level": "phishing",       # 釣魚
            "reason": "命中官方黑名單" if bl_source == "official" else "命中使用者黑名單",
            "elapsed_time": elapsed,
            "is_blacklisted": True,
            "blacklist_source": bl_source,
            "is_whitelisted": False
        })

    # ---------- 2. 弱白名單（官方安全域名） ----------
    if is_safe_domain(url):
        elapsed = round(time.time() - t0, 2)
        log("弱白名單命中（static_analyze）")
        print(f"安全域名：{url}")
        print(f"耗時：{elapsed} 秒")

        return jsonify({
            "phase": "whitelist",
            "level": "safe",           # 無害
            "reason": "官方安全域名（低風險）",
            "elapsed_time": elapsed,
            "is_blacklisted": False,
            "blacklist_source": None,
            "is_whitelisted": True
        })

    # ---------- 3. 靜態 URL 分析 ----------
    findings = []
    suspicion_score = 0

    # 3-1 單一 URL 安全檢查 - 已停用
    try:
        safety = check_url_safety.invoke({"url": url})
        safety_str = str(safety)
        if ("未發現明顯可疑特徵" in safety_str) or ("基本檢查通過" in safety_str):
            pass
        else:
            suspicion_score += 1
            findings.append(safety_str)
    except Exception as e:
        findings.append(f"URL 安全檢查失敗：{e}")
    
    # 3-2 批量結構檢查（這裡只丟單一 URL，但沿用工具）- 已停用
    try:
        patt = check_url_patterns.invoke({"urls": [url]})
        patt_str = str(patt)
        if ("未發現明顯可疑模式" in patt_str) or ("檢查通過" in patt_str):
            pass
        else:
            suspicion_score += 1
            findings.append(patt_str)
    except Exception as e:
        findings.append(f"URL 結構檢查失敗：{e}")
    
    # 3-3 網域格式檢查 - 已停用
    try:
        domain = urlparse(url).netloc
        age = analyze_domain_age.invoke({"domain": domain})
        age_str = str(age)
        if ("格式檢查通過" in age_str) or ("格式看起來正常" in age_str):
            pass
        else:
            suspicion_score += 1
            findings.append(age_str)
    except Exception as e:
        findings.append(f"網域檢查失敗：{e}")

    # ---------- 4. 靜態等級判斷 ----------
    if suspicion_score == 0:
        level = "safe"        # 無害
    elif suspicion_score >= 2:
        level = "phishing"    # 釣魚
    else:
        level = "unknown"     # 不確定（之後才能進一步深度分析）

    reason = "；".join(findings) if findings else "未發現明顯可疑特徵"
    elapsed = round(time.time() - t0, 2)

    log("靜態分析完成（static_analyze）")
    print(f"等級：{level}")
    print(f"耗時：{elapsed} 秒")

    return jsonify({
        "phase": "static",
        "level": level,
        "reason": reason,
        "elapsed_time": elapsed,
        "is_blacklisted": False,
        "blacklist_source": None,
        "is_whitelisted": False
    })


# 原本深度分析 API（保留給一般頁面擷取 + 之後的「手動深度分析」）
@app.route("/analyze", methods=["POST"])
def analyze_route():
    t0 = time.time()
    data = request.json or {}
    text = data.get("text", "")
    model = data.get("model", "qwen3:8b")

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log("收到分析請求")
    print(f"時間：{now}")
    print(f"IP  ：{request.remote_addr}")
    print(f"長度：{len(text)}")
    print(f"模型：{model}")

    # 先做黑名單檢查（不跑 LLM）
    urls = extract_urls(text)
    for u in urls:
        if is_blacklisted(u):
            source = check_blacklist_source(u)
            elapsed = round(time.time() - t0, 2)
            log("黑名單命中 → 直接返回")
            print(f"黑名單網址：{u}")
            print(f"來源：{source}")
            print(f"耗時：{elapsed} 秒")

            return jsonify({
                "is_potential_phishing": True,
                "is_blacklisted": True,
                "blacklist_source": source,
                "explanation": f"偵測到黑名單惡意網址：{u}",
                "elapsed_time": elapsed
            })

    # 再做一次「清理」後的深度分析（只跑一次，且一定帶 model）
    cleaned = extract_relevant_html(text) if "<html" in text.lower() else text
    t_llm = time.time()
    try:
        result = run_with_timeout(
            analyze_deep,
            args=(cleaned,),
            kwargs={"model": model},
            timeout=DEEP_ANALYZE_TIMEOUT_SEC
        )
    except TimeoutError:
        elapsed = round(time.time() - t0, 2)
        return jsonify({
            "is_potential_phishing": False,
            "is_blacklisted": False,
            "blacklist_source": None,
            "explanation": f"超時分析失敗（>{DEEP_ANALYZE_TIMEOUT_SEC} 秒）",
            "elapsed_time": elapsed,
            "error": "timeout"
        }), 200

    result["is_blacklisted"] = False
    result["blacklist_source"] = None

    elapsed = round(result["elapsed_time"], 2)
    log("分析完成（深度檢測）")
    print(f"耗時：{elapsed} 秒")
    print(f"分析結果：{result['is_potential_phishing']}")

    return jsonify(result)



@app.route('/cot_graph_json', methods=['GET'])
def cot_graph_json():
    """回傳圖形 JSON（nodes / edges）。
    Query params:
      - file: 指定 JSONL 檔案（可選，預設為今日 logs/cot）
      - index: 指定紀錄 index（0-based），預設最後一筆
    """
    file = request.args.get('file')
    idx = request.args.get('index')
    try:
        index = int(idx) if idx is not None else None
    except Exception:
        index = None

    if not file:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        file = os.path.join('logs', 'cot', f'cot_analysis_{today}.jsonl')

    records = cot_graph.load_jsonl(file)
    if not records:
        return jsonify({'success': False, 'message': '找不到或讀取日誌失敗', 'file': file}), 404

    G = cot_graph.build_graph_from_records(records, index=index)
    j = cot_graph.graph_to_json(G)
    return jsonify({'success': True, 'file': file, 'index': index if index is not None else len(records)-1, 'graph': j})

@app.route('/cot_graph_html', methods=['GET'])
def cot_graph_html():
    """回傳互動 HTML（pyvis）。參數同 /cot_graph_json。"""
    file = request.args.get('file')
    idx = request.args.get('index')
    try:
        index = int(idx) if idx is not None else None
    except Exception:
        index = None

    if not file:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        file = os.path.join('logs', 'cot', f'cot_analysis_{today}.jsonl')

    records = cot_graph.load_jsonl(file)
    if not records:
        return jsonify({'success': False, 'message': '找不到或讀取日誌失敗', 'file': file}), 404

    G = cot_graph.build_graph_from_records(records, index=index)

    #  產生暫存 HTML
    with tempfile.NamedTemporaryFile(delete=False, suffix=".html", mode="w", encoding="utf-8") as tf:
        out_path = tf.name

    cot_graph.export_pyvis_html(G, out_path)

    #  讀回 HTML 字串回傳
    with open(out_path, "r", encoding="utf-8") as f:
        html = f.read()

    resp = make_response(html)
    resp.headers['Content-Type'] = 'text/html; charset=utf-8'
    return resp

if __name__ == "__main__":
    print("[INIT] Loading official blacklist...")
    load_blacklist("phishtank.csv")
    
    app.run(
        host="0.0.0.0",
        port=5000,
        threaded=True,
        use_reloader=False,
        debug=False
    )
