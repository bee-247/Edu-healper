"""Neo4j client for the education knowledge graph."""
import os
import threading
from typing import Any

from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()


class Neo4jGraphClient:
    """Small Neo4j wrapper used by offline graph builders and future graph tools."""

    def __init__(self) -> None:
        self.uri = os.getenv("NEO4J_URI", "bolt://127.0.0.1:7687")
        self.username = os.getenv("NEO4J_USERNAME", "neo4j")
        self.password = os.getenv("NEO4J_PASSWORD", "supermew-neo4j")
        self.database = os.getenv("NEO4J_DATABASE", "neo4j")
        self._driver = None
        self._lock = threading.RLock()

    def _get_driver(self):
        with self._lock:
            if self._driver is None:
                self._driver = GraphDatabase.driver(
                    self.uri,
                    auth=(self.username, self.password),
                )
            return self._driver

    def close(self) -> None:
        with self._lock:
            if self._driver is None:
                return
            self._driver.close()
            self._driver = None

    def verify_connectivity(self) -> bool:
        self._get_driver().verify_connectivity()
        return True

    def execute_write(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict]:
        with self._get_driver().session(database=self.database) as session:
            result = session.run(query, parameters or {})
            return [record.data() for record in result]

    def execute_read(self, query: str, parameters: dict[str, Any] | None = None) -> list[dict]:
        with self._get_driver().session(database=self.database) as session:
            result = session.run(query, parameters or {})
            return [record.data() for record in result]

    def init_constraints(self) -> None:
        """Create idempotent constraints for the education graph."""
        constraints = [
            (
                "CREATE CONSTRAINT knowledge_point_id IF NOT EXISTS "
                "FOR (n:KnowledgePoint) REQUIRE n.node_id IS UNIQUE"
            ),
            (
                "CREATE CONSTRAINT concept_id IF NOT EXISTS "
                "FOR (n:Concept) REQUIRE n.node_id IS UNIQUE"
            ),
            (
                "CREATE CONSTRAINT formula_id IF NOT EXISTS "
                "FOR (n:Formula) REQUIRE n.node_id IS UNIQUE"
            ),
            (
                "CREATE CONSTRAINT method_id IF NOT EXISTS "
                "FOR (n:Method) REQUIRE n.node_id IS UNIQUE"
            ),
            (
                "CREATE CONSTRAINT resource_id IF NOT EXISTS "
                "FOR (n:Resource) REQUIRE n.resource_id IS UNIQUE"
            ),
            (
                "CREATE CONSTRAINT chunk_id IF NOT EXISTS "
                "FOR (n:Chunk) REQUIRE n.chunk_id IS UNIQUE"
            ),
        ]
        for query in constraints:
            self.execute_write(query)


graph_client = Neo4jGraphClient()
