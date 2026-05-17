"""Knowledge graph tools used by the teacher Agent."""

try:
    from langchain_core.tools import tool
except ImportError:
    from langchain_core.tools import tool


RELATION_ALIASES = {
    "": "",
    "prerequisite": "PREREQUISITE_OF",
    "contains": "CONTAINS",
    "related": "RELATED_TO",
    "applies_to": "APPLIES_TO",
    "confusable_with": "CONFUSABLE_WITH",
    "PREREQUISITE_OF": "PREREQUISITE_OF",
    "CONTAINS": "CONTAINS",
    "RELATED_TO": "RELATED_TO",
    "APPLIES_TO": "APPLIES_TO",
    "CONFUSABLE_WITH": "CONFUSABLE_WITH",
}


def _format_graph_records(records: list[dict], empty_message: str) -> str:
    if not records:
        return empty_message

    lines = []
    for i, record in enumerate(records, 1):
        name = record.get("name") or record.get("node_name") or "未命名知识点"
        node_type = record.get("node_type") or "knowledge_point"
        subject = record.get("subject") or ""
        grade = record.get("grade") or ""
        description = record.get("description") or ""
        sources = record.get("source_chunk_ids") or []
        header = f"[{i}] {name} ({node_type})"
        meta = " / ".join([item for item in [subject, grade] if item])
        if meta:
            header += f" - {meta}"
        lines.append(header)
        if description:
            lines.append(f"说明：{description}")
        if sources:
            lines.append(f"来源 chunk：{', '.join(sources[:5])}")

        relations = record.get("relations") or []
        if relations:
            lines.append("相关关系：")
            for rel in relations[:8]:
                relation_type = rel.get("relation_type") or rel.get("type") or "RELATED_TO"
                target = rel.get("target_name") or rel.get("name") or ""
                confidence = rel.get("confidence")
                confidence_text = f"，置信度 {confidence:.2f}" if isinstance(confidence, (int, float)) else ""
                lines.append(f"- {relation_type} -> {target}{confidence_text}")
        lines.append("")
    return "\n".join(lines).strip()


def _query_related(knowledge_name: str, relation_type: str = "", limit: int = 10) -> list[dict] | str:
    if not knowledge_name:
        return "knowledge_name 参数不能为空"

    from neo4j_client import graph_client

    rel = RELATION_ALIASES.get((relation_type or "").strip())
    if rel is None:
        return "relation_type 仅支持 prerequisite/contains/related/applies_to/confusable_with"

    relation_filter = "AND type(r) = $relation_type" if rel else ""
    query = f"""
        MATCH (n)
        WHERE (n:KnowledgePoint OR n:Concept OR n:Formula OR n:Method)
          AND toLower(coalesce(n.name, '')) CONTAINS toLower($knowledge_name)
        MATCH (n)-[r]-(m)
        WHERE m:KnowledgePoint OR m:Concept OR m:Formula OR m:Method
        {relation_filter}
        RETURN n.node_id AS source_node_id,
               n.name AS source_name,
               type(r) AS relation_type,
               m.node_id AS node_id,
               m.name AS name,
               m.node_type AS node_type,
               m.subject AS subject,
               m.grade AS grade,
               m.description AS description,
               m.source_chunk_ids AS source_chunk_ids,
               r.confidence AS confidence
        LIMIT $limit
    """
    try:
        return graph_client.execute_read(
            query,
            {
                "knowledge_name": knowledge_name.strip(),
                "relation_type": rel,
                "limit": max(1, min(int(limit or 10), 30)),
            },
        )
    except Exception as e:
        return f"知识图谱查询失败：{e}"


def _format_related(records: list[dict] | str) -> str:
    if isinstance(records, str):
        return records
    if not records:
        return "知识图谱中没有找到相关知识关系。"

    lines = []
    for i, item in enumerate(records, 1):
        confidence = item.get("confidence")
        confidence_text = f"（置信度 {confidence:.2f}）" if isinstance(confidence, (int, float)) else ""
        lines.append(
            f"[{i}] {item.get('source_name')} --{item.get('relation_type')}--> "
            f"{item.get('name')} {confidence_text}"
        )
        if item.get("description"):
            lines.append(f"说明：{item.get('description')}")
        sources = item.get("source_chunk_ids") or []
        if sources:
            lines.append(f"来源 chunk：{', '.join(sources[:5])}")
    return "\n".join(lines)


@tool("search_knowledge_graph")
def search_knowledge_graph(keyword: str, limit: int = 5) -> str:
    """Search knowledge points in Neo4j by keyword and return nearby relations."""
    if not keyword:
        return "keyword 参数不能为空"

    from neo4j_client import graph_client

    limit = max(1, min(int(limit or 5), 20))
    try:
        records = graph_client.execute_read(
            """
            MATCH (n)
            WHERE n:KnowledgePoint OR n:Concept OR n:Formula OR n:Method
            WITH n
            WHERE toLower(coalesce(n.name, '')) CONTAINS toLower($keyword)
               OR toLower(coalesce(n.description, '')) CONTAINS toLower($keyword)
            OPTIONAL MATCH (n)-[r]-(m)
            WHERE m:KnowledgePoint OR m:Concept OR m:Formula OR m:Method
            WITH n, collect({
                relation_type: type(r),
                target_name: coalesce(m.name, ''),
                target_node_id: coalesce(m.node_id, ''),
                confidence: r.confidence
            })[0..8] AS relations
            RETURN n.node_id AS node_id,
                   n.name AS name,
                   n.node_type AS node_type,
                   n.subject AS subject,
                   n.grade AS grade,
                   n.description AS description,
                   n.source_chunk_ids AS source_chunk_ids,
                   relations AS relations
            LIMIT $limit
            """,
            {"keyword": keyword.strip(), "limit": limit},
        )
    except Exception as e:
        return f"知识图谱查询失败：{e}"

    return _format_graph_records(records, "知识图谱中没有找到相关知识点。")


@tool("get_related_knowledge")
def get_related_knowledge(knowledge_name: str, relation_type: str = "", limit: int = 10) -> str:
    """Get prerequisite, related, confusable, contains, or applies-to neighbors for a knowledge point."""
    return _format_related(_query_related(knowledge_name, relation_type=relation_type, limit=limit))


@tool("get_prerequisites")
def get_prerequisites(knowledge_name: str, limit: int = 10) -> str:
    """Get prerequisite knowledge points for a given knowledge point."""
    return _format_related(_query_related(knowledge_name, relation_type="prerequisite", limit=limit))


@tool("get_teaching_path")
def get_teaching_path(knowledge_name: str, max_depth: int = 4) -> str:
    """Build a teaching path from prerequisite chains ending near the target knowledge point."""
    if not knowledge_name:
        return "knowledge_name 参数不能为空"

    from neo4j_client import graph_client

    depth = max(1, min(int(max_depth or 4), 6))
    try:
        records = graph_client.execute_read(
            f"""
            MATCH path = (start)-[:PREREQUISITE_OF*0..{depth}]->(target)
            WHERE (start:KnowledgePoint OR start:Concept OR start:Formula OR start:Method)
              AND (target:KnowledgePoint OR target:Concept OR target:Formula OR target:Method)
              AND toLower(coalesce(target.name, '')) CONTAINS toLower($knowledge_name)
            WITH path
            ORDER BY length(path) DESC
            LIMIT 5
            RETURN [node IN nodes(path) | {{
                node_id: node.node_id,
                name: node.name,
                node_type: node.node_type,
                description: node.description
            }}] AS nodes
            """,
            {"knowledge_name": knowledge_name.strip()},
        )
    except Exception as e:
        return f"知识图谱查询失败：{e}"

    if not records:
        return "知识图谱中没有找到可用的教学路径。"

    lines = ["教学路径候选："]
    seen = set()
    for record in records:
        names = [node.get("name") for node in record.get("nodes", []) if node.get("name")]
        path_text = " -> ".join(names)
        if not path_text or path_text in seen:
            continue
        seen.add(path_text)
        lines.append(f"- {path_text}")
    return "\n".join(lines) if len(lines) > 1 else "知识图谱中没有找到可用的教学路径。"
