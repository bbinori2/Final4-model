"""
cot_graph.py

將 CoT JSONL 紀錄轉成語意/推理圖 (networkx)，並提供 JSON / pyvis HTML 匯出工具。

簡單 heuristics：
- 將 `deliberation_result` 按行拆成節點
- 每行視為一個 statement node，依序連成邊（表示推理流程）
- 偵測行內 URL，建立 URL 節點並建立關聯
"""
from __future__ import annotations
import re   
import json
import tempfile
from typing import List, Dict, Tuple, Optional
import networkx as nx
from pyvis.network import Network

def summarize_for_label(text: str, max_lines: int = 1, max_chars: int = 32) -> str:
    if not text:
        return ""

    text = text.strip()

    # 章節型標題直接保留較完整內容
    if _SECTION_RE.search(text):
        return text[:40] + "…" if len(text) > 40 else text

    # 一般節點：先用句號/分號切，不要用 、 切，避免章節與片語被切太碎
    parts = re.split(r'[；。]', text)
    parts = [p.strip() for p in parts if p.strip()]

    if parts:
        short = parts[0]
        return short[:max_chars] + "…" if len(short) > max_chars else short

    return text[:max_chars] + "…" if len(text) > max_chars else text

def load_jsonl(path: str) -> List[Dict]:
    records = []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except Exception:
                    # 跳過格式錯誤的行
                    continue
    except Exception:
        return []
    return records


_URL_RE = re.compile(r"(?i)\b((?:https?://|www\.)[^\s'\"]+)")

_SECTION_RE = re.compile(
    r"(?:^|[\s\*\#\-：:])"          # 前面可以是空白/符號
    r"(?:[一二三]\s*[、.．]\s*)?"   # 可有可無的一、二、三、
    r"(技術與結構|內容與語意|重點摘要)"  # 三大節點關鍵詞（重點！）
)


def _split_lines(text: str) -> List[str]:
    if not text:
        return []
    # 移除多餘的空白行
    lines = [l.strip() for l in re.split(r'\r?\n', text) if l.strip()]
    # 若整段文字沒有換行，嘗試用句點/頓號分割
    if len(lines) == 1 and ('.' in lines[0] or '。' in lines[0] or '；' in lines[0]):
        parts = re.split(r'[。.;；]+\s*', lines[0])
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) > 1:
            return parts
    return lines


def build_graph_from_record(record: Dict, record_id: Optional[int] = None) -> nx.DiGraph:
    """根據單筆 CoT 紀錄建立有向圖。

    節點屬性：label, type (root/statement/url), record_id, is_potential_phishing
    """
    G = nx.DiGraph()

    root_id = f"record:{record_id or record.get('timestamp','unknown')}"
    G.add_node(root_id, label=f"record\n{record.get('timestamp','')}", type='root',
               is_potential_phishing=record.get('is_potential_phishing', False),
               explanation=record.get('explanation',''))

    deliberation = record.get('deliberation_result') or ''
    lines = _split_lines(deliberation)

    prev_node = None
    for i, line in enumerate(lines):
        nid = f"r{record_id}:s{i}"
        label = line if len(line) < 200 else (line[:197] + '...')
        G.add_node(nid, label=label, type='statement', full_text=line, record_id=record_id)

        # 連接順序（推理流程）
        if prev_node is None:
            G.add_edge(root_id, nid, relation='contains')
        else:
            G.add_edge(prev_node, nid, relation='next')

        # 若此行包含 URL，為每個 URL 建節點並連結
        '''
        for um in _URL_RE.finditer(line):
            url = um.group(1).rstrip('.,;')
            uid = f"r{record_id}:url:{len(G)}"
            G.add_node(uid, label=url, type='url', url=url, record_id=record_id)
            G.add_edge(nid, uid, relation='mentions')
        '''
        prev_node = nid

    # 若沒有分句／節點，則以 explanation 或 visible text 建立單一 statement
    if not lines:
        expl = record.get('explanation') or record.get('visible_text') or ''
        nid = f"r{record_id}:s0"
        G.add_node(nid, label=expl, type='statement', full_text=expl, record_id=record_id)
        G.add_edge(root_id, nid, relation='contains')

    return G


def build_graph_from_records(records: List[Dict], index: Optional[int] = None) -> nx.DiGraph:
    """若 index 為 None，使用最後一筆紀錄；否則使用指定 index（0-based）。"""
    if not records:
        return nx.DiGraph()
    if index is None:
        index = len(records) - 1
    index = max(0, min(index, len(records)-1))
    record = records[index]
    return build_graph_from_record(record, record_id=index)

def compute_zigzag_positions(G: nx.DiGraph) -> Dict[str, Dict[str, int]]:
    positions = {}

    if not G.nodes:
        return positions

    roots = [n for n, a in G.nodes(data=True) if a.get("type") == "root"]
    if not roots:
        return positions
    root = roots[0]

    chain = []
    visited = set()
    cur = root

    while cur and cur not in visited:
        visited.add(cur)
        chain.append(cur)

        next_nodes = list(G.successors(cur))
        if not next_nodes:
            break

        pick = None
        for nxt in next_nodes:
            if G.nodes[nxt].get("type") == "statement":
                pick = nxt
                break
        if pick is None:
            pick = next_nodes[0]

        cur = pick

    x = 0
    y = 0
    dx = 90
    dy = 95
    direction = -1   # 一開始先往左下

    for i, node_id in enumerate(chain):
        attrs = G.nodes[node_id]
        is_section = bool(attrs.get("is_section", False))

        positions[node_id] = {"x": x, "y": y}

        x += dx * direction
        y += dy

        # 遇到大節點後，下一段改方向
        if is_section:
            direction *= -1

    return positions

def graph_to_json(G: nx.DiGraph) -> Dict:
    nodes = []
    for n, attrs in G.nodes(data=True):
        node = {'id': n, 'label': attrs.get('label',''), 'type': attrs.get('type','')}
        # 合併剩餘屬性
        for k, v in attrs.items():
            if k in ('label','type'):
                continue
            node[k] = v
        nodes.append(node)

    edges = []
    for u, v, attrs in G.edges(data=True):
        edges.append({'source': u, 'target': v, 'relation': attrs.get('relation','')})

    return {'nodes': nodes, 'edges': edges}


def export_pyvis_html(G: nx.DiGraph, output_path: str) -> str:
    net = Network(
        height='750px',
        width='100%',
        directed=True,
        notebook=False
    )

    # 先標記哪些是 section 節點
    for n, attrs in G.nodes(data=True):
        full_text = attrs.get('full_text') or attrs.get('label', '')
        ntype = attrs.get('type', '')
        text_norm = (full_text or '').strip().replace("\n", " ").replace("*", "")
        is_section = (ntype == 'statement' and bool(_SECTION_RE.search(text_norm)))
        G.nodes[n]["is_section"] = is_section

    # 再根據 is_section 計算斜坡座標
    positions = compute_zigzag_positions(G)

    # ====== 節點 ======
    for n, attrs in G.nodes(data=True):
        full_text = (
            attrs.get('full_text')
            or attrs.get('label', '')
        )
        label = summarize_for_label(full_text, max_lines=1, max_chars=32)
        title = full_text

        ntype = attrs.get('type', '')
        is_section = bool(attrs.get("is_section", False))

        shape = 'dot'
        if is_section:
            color = {'background': '#ff4d4d', 'border': '#ff4d4d'}
            size = 30
            font = {"size": 45, "color": "#ff4d4d", "bold": True}
            borderWidth = 4
        elif ntype == 'root':
            color = {'background': '#2f2f2f', 'border': '#2f2f2f'}
            size = 20
            font = {"size": 22, "color": "#2f2f2f", "bold": True}
            borderWidth = 3
        elif attrs.get('is_potential_phishing'):
            color = {'background': '#ffb84d', 'border': '#ffb84d'}
            size = 20
            font = {"size": 18, "color": "#ff8c00", "bold": True}
            borderWidth = 3
        elif ntype == 'url':
            color = {'background': '#ffd27f', 'border': '#f2b84b'}
            size = 18
            font = {"size": 16, "color": "#b7791f"}
            borderWidth = 1
        else:
            color = {'background': '#97c2fc', 'border': '#6aa5f7'}
            size = 18
            font = {"size": 40, "color": "#2b5fb3"}
            borderWidth = 1

        pos = positions.get(n, {"x": 0, "y": 0})

        net.add_node(
            n,
            label=label,
            title=title,
            color=color,
            shape=shape,
            size=size,
            borderWidth=borderWidth,
            font=font,
            x=pos["x"],
            y=pos["y"],
            fixed={"x": True, "y": True}
        )

    # ====== 邊 ======
    for u, v, attrs in G.edges(data=True):
        net.add_edge(
            u,
            v,
            title=attrs.get('relation', '')
        )

    net.set_options(r"""
    {
    "layout": {
        "hierarchical": {
        "enabled": false
        }
    },
    "physics": {
        "enabled": false
    },
    "edges": {
        "smooth": {
        "enabled": true,
        "type": "cubicBezier",
        "roundness": 0.22
        },
        "arrows": {
        "to": { "enabled": true, "scaleFactor": 0.8 }
        }
    },
    "nodes": {
        "shape": "dot",
        "font": { "size": 14 },
        "margin": 10
    },
    "interaction": {
        "hover": false,
        "navigationButtons": false,
        "keyboard": false,
        "dragNodes": false,
        "dragView": true,
        "zoomView": true
    }
    }
    """)

    net.write_html(output_path)
    return output_path

if __name__ == "__main__":
    import glob
    import os

    files = sorted(glob.glob("logs/cot/cot_analysis_*.jsonl"))
    if not files:
        print("[ERROR] 找不到任何 COT JSONL 檔")
        exit(1)

    INPUT = files[-1]   # 取最新一個
    print("Using log:", INPUT)

    OUT_DIR = "logs/cot_graph"
    os.makedirs(OUT_DIR, exist_ok=True)

    records = load_jsonl(INPUT)
    print("records len =", len(records))

    G = build_graph_from_records(records)

    out_path = os.path.join(
        OUT_DIR,
        os.path.basename(INPUT).replace(".jsonl", ".html")
    )

    export_pyvis_html(G, out_path)
    print(f"[OK] COT graph exported to {out_path}")