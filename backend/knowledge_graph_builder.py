"""Utilities for writing extracted education graph data to Neo4j.

This module intentionally does not extract knowledge with an LLM yet. It only
persists already-structured nodes, edges, resources, and chunk references.
"""
from typing import Any
import re

from neo4j_client import Neo4jGraphClient, graph_client


NODE_LABELS = {
    "knowledge_point": "KnowledgePoint",
    "concept": "Concept",
    "formula": "Formula",
    "method": "Method",
}

RELATION_TYPES = {
    "prerequisite": "PREREQUISITE_OF",
    "contains": "CONTAINS",
    "related": "RELATED_TO",
    "applies_to": "APPLIES_TO",
    "confusable_with": "CONFUSABLE_WITH",
}


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


class KnowledgeGraphBuilder:
    """Persist education knowledge graph payloads into Neo4j."""

    def __init__(self, client: Neo4jGraphClient | None = None) -> None:
        self.client = client or graph_client

    def init_graph(self) -> None:
        self.client.init_constraints()

    def upsert_resource(self, resource: dict) -> None:
        resource_id = resource.get("resource_id") or resource.get("id")
        if resource_id is None:
            return

        self.client.execute_write(
            """
            MERGE (r:Resource {resource_id: $resource_id})
            SET r.filename = $filename,
                r.subject = $subject,
                r.grade = $grade,
                r.book_version = $book_version,
                r.resource_type = $resource_type,
                r.source_file = $source_file
            """,
            {
                "resource_id": int(resource_id),
                "filename": _clean_str(resource.get("filename")),
                "subject": _clean_str(resource.get("subject")),
                "grade": _clean_str(resource.get("grade")),
                "book_version": _clean_str(resource.get("book_version")),
                "resource_type": _clean_str(resource.get("resource_type") or "textbook"),
                "source_file": _clean_str(resource.get("source_file")),
            },
        )

    def upsert_chunk(self, chunk: dict) -> None:
        chunk_id = _clean_str(chunk.get("chunk_id"))
        if not chunk_id:
            return

        self.client.execute_write(
            """
            MERGE (c:Chunk {chunk_id: $chunk_id})
            SET c.resource_id = $resource_id,
                c.filename = $filename,
                c.page_number = $page_number,
                c.section_title = $section_title,
                c.subject = $subject,
                c.grade = $grade,
                c.knowledge_tags = $knowledge_tags,
                c.text_preview = $text_preview
            WITH c
            OPTIONAL MATCH (r:Resource {resource_id: $resource_id})
            FOREACH (_ IN CASE WHEN r IS NULL THEN [] ELSE [1] END |
                MERGE (r)-[:CONTAINS]->(c)
            )
            """,
            {
                "chunk_id": chunk_id,
                "resource_id": int(chunk.get("resource_id") or 0),
                "filename": _clean_str(chunk.get("filename")),
                "page_number": int(chunk.get("page_number") or 0),
                "section_title": _clean_str(chunk.get("section_title")),
                "subject": _clean_str(chunk.get("subject")),
                "grade": _clean_str(chunk.get("grade")),
                "knowledge_tags": _clean_list(chunk.get("knowledge_tags")),
                "text_preview": _clean_str(chunk.get("text"))[:500],
            },
        )

    def upsert_node(self, node: dict) -> None:
        node_type = _clean_str(node.get("node_type") or node.get("type")).lower()
        label = NODE_LABELS.get(node_type)
        node_id = _clean_str(node.get("node_id"))
        if not label or not node_id:
            return

        self.client.execute_write(
            f"""
            MERGE (n:{label} {{node_id: $node_id}})
            SET n.name = $name,
                n.node_type = $node_type,
                n.subject = $subject,
                n.grade = $grade,
                n.description = $description,
                n.aliases = $aliases,
                n.source_chunk_ids = $source_chunk_ids
            WITH n
            UNWIND $source_chunk_ids AS chunk_id
            MATCH (c:Chunk {{chunk_id: chunk_id}})
            MERGE (n)-[:FROM_CHUNK]->(c)
            """,
            {
                "node_id": node_id,
                "name": _clean_str(node.get("name")),
                "node_type": node_type,
                "subject": _clean_str(node.get("subject")),
                "grade": _clean_str(node.get("grade")),
                "description": _clean_str(node.get("description")),
                "aliases": _clean_list(node.get("aliases")),
                "source_chunk_ids": _clean_list(node.get("source_chunk_ids")),
            },
        )

    def upsert_edge(self, edge: dict) -> None:
        relation_type = _clean_str(edge.get("relation_type") or edge.get("type")).lower()
        rel = RELATION_TYPES.get(relation_type)
        source_id = _clean_str(edge.get("source_node_id") or edge.get("source_id"))
        target_id = _clean_str(edge.get("target_node_id") or edge.get("target_id"))
        if not rel or not source_id or not target_id:
            return

        self.client.execute_write(
            f"""
            MATCH (source {{node_id: $source_id}})
            MATCH (target {{node_id: $target_id}})
            MERGE (source)-[r:{rel}]->(target)
            SET r.relation_type = $relation_type,
                r.confidence = $confidence,
                r.evidence = $evidence
            """,
            {
                "source_id": source_id,
                "target_id": target_id,
                "relation_type": relation_type,
                "confidence": float(edge.get("confidence") or 0.0),
                "evidence": _clean_str(edge.get("evidence")),
            },
        )

    def delete_resource_by_filename(self, filename: str) -> None:
        filename = _clean_str(filename)
        if not filename:
            return
        self.client.execute_write(
            """
            MATCH (r:Resource {filename: $filename})
            OPTIONAL MATCH (r)-[:CONTAINS]->(c:Chunk)
            WITH r, [chunk IN collect(c) WHERE chunk IS NOT NULL] AS chunks
            CALL {
                WITH chunks
                UNWIND chunks AS chunk
                DETACH DELETE chunk
                RETURN count(*) AS deleted_chunks
            }
            DETACH DELETE r
            """,
            {"filename": filename},
        )

    def build_minimal_graph_from_documents(self, resource: dict, documents: list[dict]) -> dict:
        """Create a conservative graph from document metadata when no LLM extraction exists yet.

        knowledge_tags become KnowledgePoint nodes, chunks become source Chunk nodes,
        and adjacent tags in the same chunk are connected as RELATED_TO.
        """
        chunks = [doc for doc in documents if _clean_str(doc.get("chunk_id"))]
        nodes_by_id: dict[str, dict] = {}
        edges_by_key: dict[tuple[str, str, str], dict] = {}

        for chunk in chunks:
            tags = _clean_list(chunk.get("knowledge_tags"))
            if not tags and _clean_str(chunk.get("section_title")):
                tags = [_clean_str(chunk.get("section_title"))]

            source_chunk_id = _clean_str(chunk.get("chunk_id"))
            for tag in tags:
                subject = _clean_str(chunk.get("subject") or resource.get("subject"))
                node_id = f"{_slug(subject) or 'general'}:{_slug(tag)}"
                node = nodes_by_id.setdefault(
                    node_id,
                    {
                        "node_id": node_id,
                        "node_type": "knowledge_point",
                        "name": tag,
                        "subject": subject,
                        "grade": _clean_str(chunk.get("grade") or resource.get("grade")),
                        "description": "",
                        "source_chunk_ids": [],
                    },
                )
                if source_chunk_id not in node["source_chunk_ids"]:
                    node["source_chunk_ids"].append(source_chunk_id)

            for left, right in zip(tags, tags[1:]):
                source_id = f"{_slug(chunk.get('subject') or resource.get('subject')) or 'general'}:{_slug(left)}"
                target_id = f"{_slug(chunk.get('subject') or resource.get('subject')) or 'general'}:{_slug(right)}"
                if source_id == target_id:
                    continue
                key = (source_id, target_id, "related")
                edges_by_key[key] = {
                    "source_node_id": source_id,
                    "target_node_id": target_id,
                    "relation_type": "related",
                    "confidence": 0.5,
                    "evidence": _clean_str(chunk.get("chunk_id")),
                }

        payload = {
            "resources": [resource],
            "chunks": chunks,
            "nodes": list(nodes_by_id.values()),
            "edges": list(edges_by_key.values()),
        }
        return self.build_from_payload(payload)

    def build_from_payload(self, payload: dict) -> dict:
        """Write a complete graph payload and return inserted counts."""
        self.init_graph()

        resources = payload.get("resources") or []
        chunks = payload.get("chunks") or []
        nodes = payload.get("nodes") or []
        edges = payload.get("edges") or []

        for resource in resources:
            self.upsert_resource(resource)
        for chunk in chunks:
            self.upsert_chunk(chunk)
        for node in nodes:
            self.upsert_node(node)
        for edge in edges:
            self.upsert_edge(edge)

        return {
            "resources": len(resources),
            "chunks": len(chunks),
            "nodes": len(nodes),
            "edges": len(edges),
        }


knowledge_graph_builder = KnowledgeGraphBuilder()
