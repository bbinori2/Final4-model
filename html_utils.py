# HTML 處理與萃取

from bs4 import BeautifulSoup
import re
from urllib.parse import urlparse, urlunparse

def extract_relevant_html(raw_html: str, max_length: int = 3000) -> str:
    """保留 title、meta、links、可見文字 + 常見屬性文字（alt/aria/placeholder等），供模型快速分析。"""
    soup = BeautifulSoup(raw_html, "html.parser")

    # 1) title
    title = soup.title.string.strip() if soup.title and soup.title.string else ""

    # 2) meta（原本只有 name=description/keywords/author）
    metas = []
    for meta in soup.find_all("meta"):
        name = (meta.get("name") or "").lower()
        prop = (meta.get("property") or "").lower()
        content = (meta.get("content") or "").strip()
        if not content:
            continue

        # 你原本的
        if name in ["description", "keywords", "author"]:
            metas.append(f'{name}: {content}')

        # 補強（常見的社群/分享描述）
        if prop in ["og:title", "og:description"]:
            metas.append(f'{prop}: {content}')

    # 3) links（照舊）
    links = [a.get("href") for a in soup.find_all("a", href=True)[:10]]

    # 4) ★補強：抽「常用屬性文字」（這步幾乎不增加時間，但很有用）
    attr_texts = []

    def add_attr(tag, attr_name):
        v = (tag.get(attr_name) or "").strip()
        if v:
            attr_texts.append(v)

    # (a) 圖片常把文案放在 alt/title
    for img in soup.find_all("img"):
        add_attr(img, "alt")
        add_attr(img, "title")

    # (b) 表單欄位：placeholder/aria-label/name/value 很常含關鍵字（付款/驗證/客服）
    for inp in soup.find_all(["input", "textarea", "select"]):
        add_attr(inp, "placeholder")
        add_attr(inp, "aria-label")
        add_attr(inp, "name")
        add_attr(inp, "value")
        add_attr(inp, "title")

    # (c) 任何元素的 aria-label（導購按鈕、icon 按鈕很常用）
    for tag in soup.find_all(attrs={"aria-label": True}):
        add_attr(tag, "aria-label")

    # 5) 抽按鈕/連結/標籤文字（這些常出現「立即購買/加入客服/前往付款」）
    #    用 get_text(strip=True) 不會太慢
    clickable_texts = []
    for t in soup.find_all(["a", "button", "label"]):
        txt = t.get_text(" ", strip=True)
        if txt:
            clickable_texts.append(txt)

    # 6) body 可見文字（照舊，但把換行變多一點有助模型抓結構）
    body_text = soup.get_text("\n", strip=True)

    # 7) 去重（保持順序）+ 截斷，避免 input 變大拖慢
    def uniq_keep_order(items):
        seen = set()
        out = []
        for x in items:
            x = " ".join(x.split())
            if not x or len(x) < 2:
                continue
            key = x.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(x)
        return out

    metas_u = uniq_keep_order(metas)[:10]
    attr_u = uniq_keep_order(attr_texts)[:80]         # 屬性文字最多收 80 條，夠用了
    click_u = uniq_keep_order(clickable_texts)[:80]   # 按鈕/連結最多 80 條
    body_cut = body_text[:1200]                       # body 文字上限略加一點點，但仍很小

    result = (
        f"<title>{title}</title>\n"
        f"<meta>{' | '.join(metas_u)}</meta>\n"
        f"<links>{links}</links>\n"
        f"<attrs>{' | '.join(attr_u)}</attrs>\n"
        f"<clickables>{' | '.join(click_u)}</clickables>\n"
        f"<body>{body_cut}</body>"
    )

    return result[:max_length]


# URL 正規化
def _normalize_url(url: str) -> str | None:
    """標準化 URL（過濾垃圾字元、只保留 http/https）。"""

    if not url:
        return None

    url = url.strip().strip('\'"(),.;:!?]}>')

    # 不要的協定
    if url.startswith(("javascript:", "mailto:", "tel:", "#")):
        return None

    # 協定相對，補 http
    if url.startswith("//"):
        url = "http:" + url

    # 自動補上 http
    if url.startswith("www."):
        url = "http://" + url

    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return None

        netloc = parsed.netloc.lower().rstrip('.')

        # 移除預設 port
        if netloc.endswith(":80") and parsed.scheme == "http":
            netloc = netloc[:-3]
        if netloc.endswith(":443") and parsed.scheme == "https":
            netloc = netloc[:-4]

        path = parsed.path or "/"

        normalized = urlunparse((parsed.scheme, netloc, path, "", parsed.query, ""))
        return normalized

    except Exception:
        return None

# 擷取 URL
def extract_urls(text: str, max_count: int = 50) -> list[str]:
    """從 HTML 或純文字中萃取網址，並格式化。"""
    urls = set()
    lowered = text.lower()

    # HTML 模式（<a href>）
    if "<html" in lowered or "<a " in lowered or "href=" in lowered:
        try:
            soup = BeautifulSoup(text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = (a.get("href") or "").strip()
                norm = _normalize_url(href)
                if norm:
                    urls.add(norm)
        except:
            pass

    # Regex 模式（www., http://, https://）
    pattern = re.compile(r"(?i)\b((?:https?://|www\.)[^\s<>\"'\)]{3,})")
    for m in pattern.finditer(text):
        cand = m.group(1)
        norm = _normalize_url(cand)
        if norm:
            urls.add(norm)

    return sorted(urls)[:max_count]
