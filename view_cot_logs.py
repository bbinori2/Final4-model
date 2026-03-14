#!/usr/bin/env python3
"""
CoT 日誌查看工具
用途：檢查、分析、監控模型的推理過程與判斷結果
"""

import json
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional


def load_jsonl(filepath: str) -> List[Dict]:
    """載入 JSONL 檔案"""
    records = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
    except FileNotFoundError:
        print(f"❌ 找不到日誌檔案：{filepath}")
        return []
    except json.JSONDecodeError as e:
        print(f"❌ JSON 解析錯誤：{e}")
        return []
    return records


def show_record(record: Dict, idx: int = 0) -> None:
    """美化展示單筆記錄"""
    print(f"\n{'='*80}")
    print(f"【記錄 #{idx}】")
    print(f"{'='*80}")
    print(f"時間：{record.get('timestamp', 'N/A')}")
    print(f"耗時：{record.get('elapsed_time', 'N/A')} 秒")
    status = "[PHISHING]" if record.get('is_potential_phishing') else "[SAFE]"
    print(f"判斷：{status}")
    print(f"理由：{record.get('explanation', 'N/A')}")
    print(f"\n【輸入資訊】")
    print(f"輸入長度：{record.get('input_length', 0)} 字元")
    print(f"可見文字長度：{record.get('visible_text_length', 0)} 字元")
    print(f"URL 數量：{record.get('urls_count', 0)}")
    if record.get('urls'):
        print(f"URL 列表：")
        for url in record['urls']:
            print(f"  - {url}")
    
    print(f"\n【初步檢測】")
    print(f"{record.get('evidence', 'N/A')}")
    
    print(f"\n【第一階段推理過程】")
    deliberation = record.get('deliberation_result', 'N/A')
    if len(deliberation) > 500:
        print(f"{deliberation[:500]}...\n[已截斷，完整內容見下方]")
    else:
        print(deliberation)


def show_summary(records: List[Dict]) -> None:
    """顯示統計摘要"""
    if not records:
        print("沒有記錄")
        return
    
    total = len(records)
    phishing_count = sum(1 for r in records if r.get('is_potential_phishing'))
    safe_count = total - phishing_count
    avg_time = sum(r.get('elapsed_time', 0) for r in records) / total if total > 0 else 0
    
    print(f"\n{'='*80}")
    print(f"【統計摘要】")
    print(f"{'='*80}")
    print(f"總記錄數：{total}")
    print(f"釣魚判定：{phishing_count} ({phishing_count/total*100:.1f}%)")
    print(f"安全判定：{safe_count} ({safe_count/total*100:.1f}%)")
    print(f"平均耗時：{avg_time:.2f} 秒")
    
    if total > 0:
        print(f"\n【最近 5 筆記錄】")
        for i, record in enumerate(records[-5:], 1):
            status = "[PHISHING]" if record.get('is_potential_phishing') else "[SAFE   ]"
            print(f"{i}. {status} [{record.get('timestamp', 'N/A')}] {record.get('explanation', 'N/A')}")


def filter_phishing(records: List[Dict]) -> List[Dict]:
    """篩選釣魚判定"""
    return [r for r in records if r.get('is_potential_phishing')]


def filter_safe(records: List[Dict]) -> List[Dict]:
    """篩選安全判定"""
    return [r for r in records if not r.get('is_potential_phishing')]


def filter_by_keyword(records: List[Dict], keyword: str) -> List[Dict]:
    """按關鍵字篩選"""
    keyword_lower = keyword.lower()
    return [
        r for r in records
        if keyword_lower in r.get('explanation', '').lower()
        or keyword_lower in str(r.get('urls', [])).lower()
        or keyword_lower in r.get('deliberation_result', '').lower()
    ]


def export_csv(records: List[Dict], output_file: str) -> None:
    """匯出為 CSV"""
    import csv
    try:
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            if not records:
                print("❌ 沒有記錄可匯出")
                return
            
            fieldnames = [
                'timestamp', 'is_potential_phishing', 'explanation',
                'elapsed_time', 'input_length', 'urls_count'
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            for record in records:
                row = {k: record.get(k, '') for k in fieldnames}
                writer.writerow(row)
        
        print(f"✅ 已匯出至：{output_file}")
    except Exception as e:
        print(f"❌ 匯出失敗：{e}")


def main():
    """主程式"""
    log_dir = Path("logs/cot")
    
    if len(sys.argv) < 2:
        print("""
CoT 日誌查看工具

用法：
  python view_cot_logs.py --summary              # 顯示今天的統計摘要
  python view_cot_logs.py --latest [N]           # 顯示最近 N 筆記錄（預設 5）
  python view_cot_logs.py --phishing             # 顯示所有釣魚判定
  python view_cot_logs.py --safe                 # 顯示所有安全判定
  python view_cot_logs.py --search <關鍵字>      # 搜尋關鍵字
  python view_cot_logs.py --export <輸出檔>      # 匯出為 CSV
  python view_cot_logs.py --file <日誌檔>        # 指定特定日誌檔案

範例：
  python view_cot_logs.py --latest 10
  python view_cot_logs.py --search "google"
  python view_cot_logs.py --export analysis.csv
        """)
        return
    
    command = sys.argv[1]
    
    # 決定要讀取的日誌檔案
    if command == "--file" and len(sys.argv) > 2:
        log_file = sys.argv[2]
    else:
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = log_dir / f"cot_analysis_{today}.jsonl"
    
    records = load_jsonl(str(log_file))
    
    if command == "--summary":
        show_summary(records)
    
    elif command == "--latest":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        for i, record in enumerate(records[-n:], 1):
            show_record(record, i)
    
    elif command == "--phishing":
        phishing_records = filter_phishing(records)
        print(f"發現 {len(phishing_records)} 筆釣魚判定\n")
        for i, record in enumerate(phishing_records, 1):
            show_record(record, i)
    
    elif command == "--safe":
        safe_records = filter_safe(records)
        print(f"發現 {len(safe_records)} 筆安全判定\n")
        show_summary(safe_records)
    
    elif command == "--search":
        if len(sys.argv) < 3:
            print("❌ 請提供搜尋關鍵字")
            return
        keyword = sys.argv[2]
        results = filter_by_keyword(records, keyword)
        print(f"搜尋 '{keyword}' 發現 {len(results)} 筆記錄\n")
        for i, record in enumerate(results, 1):
            show_record(record, i)
    
    elif command == "--export":
        if len(sys.argv) < 3:
            print("❌ 請提供輸出檔名")
            return
        output_file = sys.argv[2]
        export_csv(records, output_file)
    
    elif command == "--file":
        show_summary(records)
    
    else:
        print(f"❌ 未知命令：{command}")


if __name__ == "__main__":
    main()
