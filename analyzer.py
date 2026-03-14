# analyzer.py — LangChain + Tools （ 弱白名單 / 工具驅動理由 / 繁體）
from html_utils import extract_relevant_html, extract_urls
import time
import re
import json
import os
from datetime import datetime
from typing import List, Dict
from functools import lru_cache
from urllib.parse import urlparse
from opencc import OpenCC
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from models import SimplePhishingAnalysis

DEFAULT_MODEL = "qwen3:8b"
BASE_URL = "http://127.0.0.1:11434/v1"
API_KEY = "ollama"
cc = OpenCC("s2twp")  # 簡體 → 繁體

# ★ CoT 日誌設定
LOG_DIR = "logs/cot"
os.makedirs(LOG_DIR, exist_ok=True)

# ★ 弱白名單（不跳過分析，但限制理由）
SAFE_DOMAINS = [
    "google.com", "google.com.tw", "gstatic.com",
    "facebook.com", "microsoft.com", "github.com",
    "edu.tw", "gov.tw",
    "niu.edu.tw",
]

def _canonicalize_and_ensure_3_sections(text: str, fallback_summary: str = "") -> str:
    if not text:
        text = ""

    lines = [l.rstrip() for l in text.splitlines()]
    out = []

    for l in lines:
        s = l.strip()

        # 去除整行包 ** 的情況
        s2 = re.sub(r"^\*\*(.+?)\*\*$", r"\1", s).strip()

        # 統一三大標題（容忍 一、/二、/三、；容忍是否有「層面分析」）
        if re.match(r"^(?:[一二三][、.．]\s*)?技術與結構(?:層面分析)?$", s2):
            out.append("一、技術與結構層面分析")
            continue
        if re.match(r"^(?:[一二三][、.．]\s*)?內容與語意(?:層面分析)?$", s2):
            out.append("二、內容與語意層面分析")
            continue
        if re.match(r"^(?:[一二三][、.．]\s*)?重點摘要", s2):
            out.append("三、重點摘要")
            continue

        out.append(s)

    norm = "\n".join(out).strip()

    has_1 = "一、技術與結構層面分析" in norm
    has_2 = "二、內容與語意層面分析" in norm
    has_3 = "三、重點摘要" in norm

    if not has_1:
        norm = "一、技術與結構層面分析\n- 未見相關資訊\n\n" + norm

    if not has_2:
        norm = norm + "\n\n二、內容與語意層面分析\n- 未見相關資訊"

    if not has_3:
        bullets = []
        if fallback_summary.strip():
            parts = re.split(r"[、,，；;。\n]+", fallback_summary)
            parts = [p.strip() for p in parts if p.strip()]
            parts = parts[:4]
            bullets = [f"- {p}" for p in parts]

        if not bullets:
            bullets = ["- 資料不足：模型未輸出重點摘要段落"]

        norm = norm + "\n\n三、重點摘要\n" + "\n".join(bullets)

    return norm.strip()

def is_safe_domain(url):
    host = urlparse(url).netloc.lower()
    return any(sd in host for sd in SAFE_DOMAINS)

# ===== 顯示層：依模型決定語言正規化 =====
def _count_cjk(s: str) -> int:
    return sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")

def _count_en_letters(s: str) -> int:
    return sum(1 for ch in s if ch.isascii() and ch.isalpha())

def _needs_en_translation(s: str) -> bool:
    """
    判斷是否需要把英文翻成繁中（顯示層用）
    只要英文明顯偏多才翻，避免誤判。
    """
    s = s.strip()
    if not s:
        return False
    en = _count_en_letters(s)
    cjk = _count_cjk(s)
    return en >= 20 and en > cjk 

@lru_cache(maxsize=2)
def _build_zh_translator_llama():
    """
    專門把「含英文的段落」翻成繁體中文（顯示層用）
    固定用 llama3:8b（你指定的）
    """
    llm = ChatOpenAI(
        model="llama3:8b",
        base_url=BASE_URL,
        api_key=API_KEY,
        temperature=0.1,
        max_tokens=220,
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system",
         "你是翻譯器。請把輸入內容翻譯成「繁體中文」。\n"
         "規則：\n"
         "1) 只翻譯英文/外語，中文保持原意\n"
         "2) 保留 URL、網域、http/https、數字、貨幣、型號、品牌名、人名（不要改寫）\n"
         "3) 不要補充解釋、不加前後文、不列點、不加引號\n"
         "4) 只輸出翻譯後的文字本體"),
        ("human", "{text}")
    ])

    return prompt | llm

def normalize_cot_display_by_model(raw_text: str, base_model: str) -> str:
    """
    依據第一階段使用的模型決定顯示層處理：
    - qwen3:8b：只做簡→繁（不翻英文）
    - llama3:8b：簡→繁後，若仍英文偏多，再用 llama3:8b 翻成繁中
    """
    display = cc.convert(raw_text)

    if isinstance(base_model, str) and base_model.startswith("qwen3"):
        return display

    if isinstance(base_model, str) and base_model.startswith("llama3"):
        if _needs_en_translation(display):
            try:
                translator = _build_zh_translator_llama()
                resp = translator.invoke({"text": display})
                translated = resp.content.strip() if hasattr(resp, "content") else str(resp).strip()
                return cc.convert(translated) if translated else display
            except Exception:
                return display
    return display

# ============ JSONL 日誌系統 ============
def _get_log_filename() -> str:
    """
    根據日期生成日誌檔名。
    格式：logs/cot/cot_analysis_YYYY-MM-DD.jsonl
    """
    today = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(LOG_DIR, f"cot_analysis_{today}.jsonl")

def _append_cot_log(log_entry: dict) -> None:
    """
    將分析過程以 JSONL 格式追加到日誌檔案。
    
    Args:
        log_entry: 包含完整分析資訊的字典
    """
    try:
        log_file = _get_log_filename()
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[LOG ERROR] 日誌寫入失敗：{e}")

def _save_cot_result(
    input_text: str,
    visible_text: str,
    urls: List[str],
    evidence: str,
    deliberation_result: str,
    is_potential_phishing: bool,
    explanation: str,
    elapsed_time: float
) -> None:
    """
    保存完整的 CoT 分析過程到 JSONL。
    
    包含：
    - timestamp: 分析時間
    - input_text: 原始輸入（前 500 字元用於蒐證）
    - visible_text: 提取的可見文字
    - urls: 發現的 URL 列表
    - evidence: 工具檢測結果
    - deliberation_result: 第一階段（推理層）輸出
    - is_potential_phishing: 最終判斷
    - explanation: 最終理由
    - elapsed_time: 耗時（秒）
    """
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "input_length": len(input_text),
        "input_preview": input_text[:500],  # 前 500 字用於蒐證
        "visible_text_length": len(visible_text),
        "urls_count": len(urls),
        "urls": urls[:10],  # 最多記錄前 10 個 URL
        "evidence": evidence,
        "deliberation_result": deliberation_result,
        "is_potential_phishing": is_potential_phishing,
        "explanation": explanation,
        "elapsed_time": elapsed_time,
    }
    
    _append_cot_log(log_entry)


def collect_tool_evidence(urls: List[str], visible: str) -> Dict[str, str]:
    # 已停用 tools，回傳空 evidence
    return {}

# ============ 雙階段 CoT（Chain of Thought）架構 ============

# 階段 1：高溫度 Deliberation（詳細推理，保存為內部文本）
@lru_cache(maxsize=4)
def _build_deliberation_chain(model: str):
    """
    第一階段：使用較高 temperature（0.7）生成詳細的 step-by-step 推理。
    模型會深入思考，列出所有可疑點、安全點，以及推理過程。
    """
    llm = ChatOpenAI(
        model=model,
        base_url=BASE_URL,
        api_key=API_KEY,
        temperature=0.7,  # 高溫度：鼓勵多樣化推理
        max_tokens=512,
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system",
         """
        你是「詐騙與釣魚網站辨識」分析專家。
        現在進行【第一階段：Observations（觀察摘要）】。目標是把觀察到的線索整理成可供後續決策使用的分析摘要。
        本階段：不得下「是否為詐騙」結論、不得輸出 JSON、不得臆測未提供的內容；只根據輸入資料整理觀察。

        【語言與引用規則】
        以繁體中文撰寫分析敘述為主。
        允許為了「觀察語言/語法/翻譯腔/拼字」而引用原文片段（可包含英文或簡體），但引用必須短且以「引號」標示，例如：「...」。
        URL、網域、protocol、file path 可保留原樣。

        【輸出格式規範（必須遵守）】
        1) 請只輸出以下三個大標題，且每個標題必須各出現一次，順序不可更動：
        一、技術與結構層面分析
        二、內容與語意層面分析
        三、重點摘要

        2) 標題必須「完全一字不差」使用上述文字：
        不要用加粗（不要用  ）
        不要加括號、不加冒號、不加「分析」以外的字
        不要改成其他同義字
        標題前後各換行一次
        若你輸出時未符合上述標題格式，請在同一則輸出中立刻自行修正並重寫全文，使其符合規範後再輸出。

        3) 每個大標題下方用條列（-）列出重點即可，不需要固定條目數量；條列數量應依實際可觀察線索多寡調整。

        【一、技術與結構層面分析】
        以下列出的項目為「最低檢查範圍」，僅作為分析引導。
        若從資料中觀察到其他與釣魚、詐騙或風險相關的技術／結構特徵，請依實際觀察一併列出，不受下列項目限制。
        URL/網域：是否與宣稱身分一致、是否有混淆設計（子網域偽裝、長參數、可疑路徑關鍵字等）
        連結/導向：是否大量導向外站、是否有短網址、是否疑似跳轉或釣魚收集入口
        表單/互動：是否要求輸入帳密/OTP/信用卡/個資、是否有可疑下載/權限提示（若資料未提供就寫「未見相關資訊」）
        站內結構合理性：可見文字是否過少/模板感強/大量重複、按鈕與導覽是否合理
        安全與可信線索：若有明確官方/組織資訊、清楚服務描述、可驗證的導覽/頁腳等，列出來

        【二、內容與語意層面分析】
        以下列出的項目為「最低檢查範圍」，僅作為分析引導。
        若觀察到其他異常說服手法、心理施壓、資訊不對稱或風險訊號，請依實際觀察補充分析，不受下列項目限制。
        目的與情境：頁面主訴求是什麼（登入、付款、客服、活動、公告等）
        語氣與誘導：是否有「緊急/威嚇/限時/中獎/帳號異常」等施壓、或要求立即操作
        身分與一致性：文字風格是否像宣稱的品牌/政府/銀行；是否出現不自然翻譯腔或錯字（必要時可短引原文佐證）
        風險訊號：是否引導到外部通訊（LINE/WhatsApp/Telegram）、要求私下交易、要求提供敏感資訊

        【三、重點摘要】
        用條列列出「關鍵可疑線索」與「關鍵安全線索」（若沒有就寫無）
        若資料不足以支持判斷，請明確寫出不足之處（例如：缺少表單內容、缺少更完整可見文字、缺少關於付款/登入的描述）
        不要下結論（不要寫「因此是詐騙/不是詐騙」）

        補充說明：
        分析時請優先完整性，其次才是簡潔性。

        """),

        ("human",
         """
        === 可見文字 ===
        {visible_text}

        === URL ===
        {urls}

        === 初步檢測結果 (Evidence) ===
        {evidence}

        請進行合理的推理分析，列出所有理由和推理過程。
        """)
    ])
    return prompt | llm

# 階段 2：低溫度決策（結構化輸出，使用第一階段推理結果）
@lru_cache(maxsize=4)
def _build_decision_chain(model: str):
    """
    第二階段：使用較低 temperature（0.1）基於第一階段推理生成最終 JSON。
    輸入包含：原始 evidence + 第一階段的推理結果。
    輸出：SimplePhishingAnalysis JSON。
    """
    llm = ChatOpenAI(
        model=model,
        base_url=BASE_URL,
        api_key=API_KEY,
        temperature=0.1,  # 低溫度：確保輸出一致性
        max_tokens=60
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system",
         """
        你是釣魚網站辨識系統的【第二階段：Decision】決策模組。
        你只能依據「第一階段 Observations（觀察摘要）的內容」做判斷，不得重新分析原始網頁、不得引入新的推論或臆測。

        請輸出 JSON 物件，欄位：
        is_potential_phishing: boolean
        explanation: 繁體中文，為最終理由摘要（可一句或短語組合），內容必須能在第一階段找到依據；不得新增第一階段沒提到的點。

        判斷原則：
        單一、弱或常見特徵不足以判定釣魚
        只有同時出現兩項以上彼此獨立且高風險的可疑行為，才可明確判定釣魚
        多項中度異常且缺乏合理解釋，也可判為高風險
        若第一階段列出明確安全/合理特徵，需提高判定門檻避免誤判

        限制：
        只輸出 JSON，不得輸出分析過程
        不得使用簡體或中英混合（URL、網域等原樣字串除外）
        """),

        ("human",
         """
        === 推理結果（第一階段） ===
        {deliberation_result}

        === 原始 Evidence ===
        {evidence}

        請根據上述推理結果，輸出最終的 JSON 判斷。
        """)
            ])
    return prompt | llm.with_structured_output(SimplePhishingAnalysis)

# 主分析流程：雙階段 CoT
def analyze_deep(text: str, model: str = DEFAULT_MODEL) -> dict:
    start = time.time()
    base_model = model

    if isinstance(model, str) and "|" in model:
        base_model = model.split("|", 1)[0].strip()
    summary_text = extract_relevant_html(text)
    visible = summary_text
    urls = extract_urls(text)
    urls_str = "\n".join(urls[:10]) if urls else "（無網址）"
    evidence_dict = collect_tool_evidence(urls, visible)
    evidence_text = (
        "\n".join(f"{k}: {v}" for k, v in evidence_dict.items())
        if evidence_dict else
        "（evidence 欄位保留；目前未啟用外部工具檢測，請勿視為安全或風險依據）"
    )
    
    # ============ 階段 1：詳細推理（Deliberation） ============
    deliberation_chain = _build_deliberation_chain(base_model)
    try:
        deliberation_response = deliberation_chain.invoke({
            "visible_text": visible[:1200],
            "urls": urls_str,
            "evidence": evidence_text,
        })
        # 提取推理結果
        deliberation_result_raw = (
            deliberation_response.content
            if hasattr(deliberation_response, "content")
            else str(deliberation_response)
        )

        # display：給 log / COT 圖用（依模型做：qwen只簡轉繁 / llama才翻英文）
        deliberation_result_display = normalize_cot_display_by_model(deliberation_result_raw, base_model)

    except Exception as e:
        deliberation_result_raw = f"[推理失敗] {str(e)}"
        deliberation_result_display = cc.convert(deliberation_result_raw)

    # ============ 階段 2：最終決策（Decision） ============
    decision_chain = _build_decision_chain(base_model)

    def run_decision():
        resp = decision_chain.invoke({
            "deliberation_result": deliberation_result_raw,
            "evidence": evidence_text,
            "visible_text": visible[:800],
            "urls": urls_str,
        })
        return resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)

    try:
        r = run_decision()
    except Exception as e:
        r = {
            "is_potential_phishing": False,
            "explanation": f"分析失敗：{str(e)}"
        }

    # ============ 取得最終判定（單次決策） ============
    is_phishing_final = bool(r.get("is_potential_phishing", False))
    raw_expl = (r.get("explanation") or "").strip()

    # ============ 理由整理（保留你的去重/切分規則） ============
    parts = re.split(r"[\n、,，]+", raw_expl)
    parts = [p.strip("- ").strip() for p in parts if p.strip()]
    seen = set()
    unique_parts = []
    for p in parts:
        key = p.lower()
        if key not in seen:
            seen.add(key)
            unique_parts.append(p)

    unique_parts = unique_parts[:4]
    explanation_joined = "、".join(unique_parts) if unique_parts else "未發現可疑特徵"
    explanation_final = cc.convert(explanation_joined)
    elapsed = round(time.time() - start, 2)

    deliberation_result_display = _canonicalize_and_ensure_3_sections(
    deliberation_result_display,
    fallback_summary=explanation_final
    )
    # ============ 落盤日誌：保存完整 CoT 過程 ============
    _save_cot_result(
        input_text=text,
        visible_text=visible,
        urls=urls,
        evidence=evidence_text,
        deliberation_result=deliberation_result_display,
        is_potential_phishing=is_phishing_final,
        explanation=explanation_final,
        elapsed_time=elapsed,
    )

    return {
        "is_potential_phishing": is_phishing_final,
        "explanation": explanation_final,
        "elapsed_time": elapsed,
    }
