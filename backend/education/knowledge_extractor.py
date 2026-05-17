"""Offline knowledge extraction for static textbook resources."""

import json
import os
import re
from typing import Any

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

load_dotenv()

API_KEY = os.getenv("ARK_API_KEY")
MODEL = os.getenv("KNOWLEDGE_EXTRACT_MODEL") or os.getenv("MODEL")
BASE_URL = os.getenv("BASE_URL")
EXTRACT_ENABLED = os.getenv("KNOWLEDGE_EXTRACT_ENABLED", "true").lower() != "false"
MAX_CHUNKS = int(os.getenv("KNOWLEDGE_EXTRACT_MAX_CHUNKS", "24"))
MAX_CHARS_PER_CHUNK = int(os.getenv("KNOWLEDGE_EXTRACT_CHUNK_CHARS", "900"))
BATCH_SIZE = int(os.getenv("KNOWLEDGE_EXTRACT_BATCH_SIZE", "4"))

NODE_TYPES = {"knowledge_point", "concept", "formula", "method"}
RELATION_TYPES = {"prerequisite", "contains", "related", "applies_to", "confusable_with"}

_extract_model = None


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _clean_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [_clean_str(item) for item in value if _clean_str(item)]
    return [_clean_str(value)] if _clean_str(value) else []


def _slug(value: str) -> str:
    cleaned = re.sub(r"\s+", "_", _clean_str(value).lower())
    return re.sub(r"[^\w\u4e00-\u9fff:.-]+", "", cleaned).strip("_")


def _get_model():
    global _extract_model
    if not EXTRACT_ENABLED or not API_KEY or not MODEL:
        return None
    if _extract_model is None:
        _extract_model = init_chat_model(
            model=MODEL,
            model_provider="openai",
            api_key=API_KEY,
            base_url=BASE_URL,
            temperature=0,
        )
    return _extract_model


def _select_extraction_chunks(documents: list[dict]) -> list[dict]:
    l2_docs = [doc for doc in documents if int(doc.get("chunk_level", 0) or 0) == 2]
    l3_docs = [doc for doc in documents if int(doc.get("chunk_level", 0) or 0) == 3]
    source = l2_docs or l3_docs or documents
    selected = []
    seen_text = set()
    for doc in source:
        text = _clean_str(doc.get("text"))
        if len(text) < 20:
            continue
        fingerprint = text[:120]
        if fingerprint in seen_text:
            continue
        seen_text.add(fingerprint)
        selected.append(doc)
        if len(selected) >= MAX_CHUNKS:
            break
    return selected


def _json_from_response(content: str) -> dict:
    text = _clean_str(content)
    if not text:
        return {}
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _build_prompt(resource: dict, chunks: list[dict]) -> str:
    chunk_lines = []
    for idx, chunk in enumerate(chunks, 1):
        chunk_lines.append(
            "\n".join(
                [
                    f"CHUNK {idx}",
                    f"chunk_id: {_clean_str(chunk.get('chunk_id'))}",
                    f"page_number: {chunk.get('page_number', 0)}",
                    f"section_title: {_clean_str(chunk.get('section_title'))}",
                    f"knowledge_tags: {', '.join(_clean_list(chunk.get('knowledge_tags')))}",
                    "text:",
                    _clean_str(chunk.get("text"))[:MAX_CHARS_PER_CHUNK],
                ]
            )
        )

    return f"""
你是教材知识图谱离线抽取器。请从教材片段中抽取结构化知识图谱。

资源信息：
- subject: {_clean_str(resource.get('subject'))}
- grade: {_clean_str(resource.get('grade'))}
- book_version: {_clean_str(resource.get('book_version'))}
- filename: {_clean_str(resource.get('filename'))}

只允许输出 JSON，不要输出 Markdown 或解释。JSON 结构：
{{
  "nodes": [
    {{
      "node_id": "稳定唯一id，建议 subject:英文或拼音/中文slug",
      "node_type": "knowledge_point | concept | formula | method",
      "name": "知识点/概念/公式/方法名称",
      "subject": "学科",
      "grade": "年级",
      "description": "不超过80字",
      "aliases": ["别名"],
      "source_chunk_ids": ["chunk_id"]
    }}
  ],
  "edges": [
    {{
      "source_node_id": "源 node_id",
      "target_node_id": "目标 node_id",
      "relation_type": "prerequisite | contains | related | applies_to | confusable_with",
      "confidence": 0.0,
      "evidence": "简短证据或来源chunk_id"
    }}
  ]
}}

要求：
1. 节点必须来自教材片段，不要凭空补充。
2. 每个节点必须带 source_chunk_ids。
3. 不确定关系不要输出，confidence 用 0 到 1。
4. 同义知识点合并为一个 node_id。

教材片段：
{chr(10).join(chunk_lines)}
""".strip()


def _normalize_node(raw: dict, resource: dict, fallback_chunk_ids: list[str]) -> dict | None:
    name = _clean_str(raw.get("name"))
    node_type = _clean_str(raw.get("node_type") or raw.get("type")).lower()
    if not name or node_type not in NODE_TYPES:
        return None
    subject = _clean_str(raw.get("subject") or resource.get("subject") or "general")
    node_id = _clean_str(raw.get("node_id")) or f"{_slug(subject) or 'general'}:{_slug(name)}"
    return {
        "node_id": node_id,
        "node_type": node_type,
        "name": name,
        "subject": subject,
        "grade": _clean_str(raw.get("grade") or resource.get("grade")),
        "description": _clean_str(raw.get("description"))[:200],
        "aliases": _clean_list(raw.get("aliases")),
        "source_chunk_ids": _clean_list(raw.get("source_chunk_ids")) or fallback_chunk_ids,
    }


def _normalize_edge(raw: dict, valid_node_ids: set[str]) -> dict | None:
    source_id = _clean_str(raw.get("source_node_id") or raw.get("source_id"))
    target_id = _clean_str(raw.get("target_node_id") or raw.get("target_id"))
    relation_type = _clean_str(raw.get("relation_type") or raw.get("type")).lower()
    if source_id not in valid_node_ids or target_id not in valid_node_ids or relation_type not in RELATION_TYPES:
        return None
    try:
        confidence = float(raw.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return {
        "source_node_id": source_id,
        "target_node_id": target_id,
        "relation_type": relation_type,
        "confidence": max(0.0, min(1.0, confidence)),
        "evidence": _clean_str(raw.get("evidence"))[:300],
    }


def _merge_payloads(resource: dict, chunks: list[dict], payloads: list[dict]) -> dict:
    nodes_by_id: dict[str, dict] = {}
    edges_by_key: dict[tuple[str, str, str], dict] = {}
    all_chunk_ids = [_clean_str(chunk.get("chunk_id")) for chunk in chunks if _clean_str(chunk.get("chunk_id"))]

    for payload in payloads:
        for raw_node in payload.get("nodes") or []:
            node = _normalize_node(raw_node, resource, all_chunk_ids[:1])
            if not node:
                continue
            existing = nodes_by_id.get(node["node_id"])
            if existing:
                existing["source_chunk_ids"] = sorted(set(existing["source_chunk_ids"] + node["source_chunk_ids"]))
                if not existing.get("description") and node.get("description"):
                    existing["description"] = node["description"]
            else:
                nodes_by_id[node["node_id"]] = node

    valid_node_ids = set(nodes_by_id.keys())
    for payload in payloads:
        for raw_edge in payload.get("edges") or []:
            edge = _normalize_edge(raw_edge, valid_node_ids)
            if not edge:
                continue
            key = (edge["source_node_id"], edge["target_node_id"], edge["relation_type"])
            if key not in edges_by_key or edge["confidence"] > edges_by_key[key]["confidence"]:
                edges_by_key[key] = edge

    return {
        "resources": [resource],
        "chunks": chunks,
        "nodes": list(nodes_by_id.values()),
        "edges": list(edges_by_key.values()),
    }


class KnowledgeExtractor:
    """Extract KnowledgePoint/Concept/Formula/Method graph payloads from static chunks."""

    def extract_payload(self, resource: dict, documents: list[dict]) -> dict:
        chunks = _select_extraction_chunks(documents)
        model = _get_model()
        if not chunks or not model:
            return {"resources": [resource], "chunks": chunks, "nodes": [], "edges": []}

        payloads = []
        for start in range(0, len(chunks), max(1, BATCH_SIZE)):
            batch = chunks[start : start + max(1, BATCH_SIZE)]
            prompt = _build_prompt(resource, batch)
            try:
                response = model.invoke(prompt)
                parsed = _json_from_response(getattr(response, "content", response))
                if parsed:
                    payloads.append(parsed)
            except Exception:
                continue

        return _merge_payloads(resource, chunks, payloads)


knowledge_extractor = KnowledgeExtractor()
