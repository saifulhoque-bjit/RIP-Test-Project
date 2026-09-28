import logging
import re
from typing import Any

from tree_sitter import Language, Node, Parser, Query

logger = logging.getLogger(__name__)


class EnterpriseHybridParser:
    """
    Production-grade AST + Regex Hybrid Parser for Legacy Modernization.

    Architecture:
    1. MASK: Sanitizes proprietary syntax (e.g., EXEC SQL) to prevent AST cascading.
    2. PARSE: Builds the AST using native C/Rust Tree-sitter grammars.
    3. QUERY: Executes high-speed S-expressions to extract mathematical edges.
    4. FALLBACK: Scans masked regions and AST error nodes for technical debt and landmines.
    """

    def __init__(
        self,
        grammar_module: Any,
        queries: dict[str, str],
        mask_patterns: dict[str, re.Pattern],
        landmine_patterns: dict[str, re.Pattern],
    ):
        # 1. Initialize v0.22+ Language and Parser
        try:
            self.language = Language(grammar_module.language())
            self.parser = Parser(self.language)
        except Exception as e:
            logger.error(f"[HybridParser] Failed to initialize Tree-sitter language: {e}")
            raise

        self.mask_patterns = mask_patterns
        self.landmine_patterns = landmine_patterns

        # 2. Compile Native C-level Queries (S-expressions)
        self.compiled_queries: dict[str, Query] = {}
        for name, query_str in queries.items():
            try:
                self.compiled_queries[name] = Query(self.language, query_str)
            except Exception as e:
                logger.error(f"[HybridParser] Failed to compile query '{name}': {e}")

        # Native query to instantly find AST errors without Python traversal overhead
        try:
            self.error_query = Query(self.language, "(ERROR) @error")
        except Exception:
            self.error_query = None

    def analyze(self, raw_source: bytes) -> dict[str, Any]:
        """Executes the 4-Stage Parsing Pipeline."""

        # STAGE 1: Masking (Prevent AST Cascading)
        masked_source, masked_regions = self._mask_proprietary_blocks(raw_source)

        # STAGE 2: Parse the Sanitized AST
        tree = self.parser.parse(masked_source)

        # STAGE 3: Native AST Querying (High Performance)
        structural_edges = self._execute_queries(tree.root_node, raw_source)

        # STAGE 4: Targeted Regex Fallback
        error_nodes = self._get_error_nodes(tree.root_node)
        landmines = self._apply_regex_fallback(masked_regions, error_nodes, raw_source)

        return {
            "metrics": {
                "ast_health_score": self._calculate_health(tree.root_node),
                "total_nodes": tree.root_node.child_count if tree.root_node else 0,
                "error_nodes": len(error_nodes),
            },
            "edges": structural_edges,
            "landmines": landmines,
        }

    # ---------------------------------------------------------
    # Stage 1: The Masker
    # ---------------------------------------------------------
    def _mask_proprietary_blocks(self, source: bytes) -> tuple[bytes, list[dict[str, Any]]]:
        """
        Finds proprietary blocks, records their byte coordinates, and replaces
        them with whitespace. This guarantees exact spatial coordinates (Line/Col)
        are preserved for Neo4j while shielding the AST from compiler-specific quirks.
        """
        masked = bytearray(source)
        regions = []

        # Decode once safely for regex operations
        try:
            source_str = source.decode("utf-8", errors="replace")
        except Exception:
            return source, []

        for category, pattern in self.mask_patterns.items():
            for match in pattern.finditer(source_str):
                start, end = match.span()

                # Extract the raw bytes corresponding to this match
                # (Assuming 1:1 byte-to-char mapping for legacy systems,
                # but using byte spans natively where possible is safer.
                # For string regex, we translate back to byte indices).
                raw_fragment = source[start:end]

                regions.append(
                    {
                        "category": category,
                        "start": start,
                        "end": end,
                        "raw_text": source_str[start:end],
                    }
                )

                # Replace with spaces to maintain structural alignment
                masked[start:end] = b" " * (end - start)

        return bytes(masked), regions

    # ---------------------------------------------------------
    # Stage 3: The Query Engine
    # ---------------------------------------------------------
    def _execute_queries(self, root: Node, original_source: bytes) -> list[dict[str, Any]]:
        """
        Runs pre-compiled S-expressions to extract exact structural boundaries.
        Uses original_source to extract identifiers that might have been partially masked.
        """
        edges = []
        if not root:
            return edges

        for query_name, query_obj in self.compiled_queries.items():
            captures = query_obj.captures(root)
            for node, capture_name in captures:
                # Robust extraction to handle Tree-sitter v0.21 vs v0.22+ differences
                row, col = self._extract_coordinates(node.start_point)
                end_row, end_col = self._extract_coordinates(node.end_point)

                identifier_bytes = original_source[node.start_byte : node.end_byte]
                identifier_str = identifier_bytes.decode("utf-8", errors="replace").strip()

                # Skip empty captures
                if not identifier_str:
                    continue

                edges.append(
                    {
                        "type": query_name,  # e.g., "vb6_ast_call"
                        "target": identifier_str.lower(),  # Normalized for Neo4j fuzzy matching
                        "line_start": row + 1,  # +1 for human/Neo4j 1-based indexing
                        "line_end": end_row + 1,
                        "column": col,
                    }
                )

        return edges

    # ---------------------------------------------------------
    # Stage 4: The Fallback Hunter
    # ---------------------------------------------------------
    def _get_error_nodes(self, root: Node) -> list[Node]:
        """Fast extraction of AST parsing failures."""
        if not root or not root.has_error:
            return []

        errors = []
        if self.error_query:
            captures = self.error_query.captures(root)
            errors = [node for node, _ in captures]
        else:
            # Recursive fallback if error query fails to compile
            self._dfs_find_errors(root, errors)
        return errors

    def _dfs_find_errors(self, node: Node, error_list: list[Node]):
        """Standard Depth-First Search for error nodes."""
        if node.type == "ERROR" or node.is_missing:
            error_list.append(node)
        for child in node.children:
            self._dfs_find_errors(child, error_list)

    def _apply_regex_fallback(
        self, masked_regions: list[dict[str, Any]], error_nodes: list[Node], raw_source: bytes
    ) -> dict[str, Any]:
        """
        Applies targeted regex hunting ONLY to known bad blocks and AST errors.
        This maximizes performance by bypassing healthy AST structures.
        """
        found_landmines = {"flags": [], "normalized_score": 0, "severity": "Low"}

        # 1. Scan explicit proprietary blocks (e.g., EXEC SQL)
        for region in masked_regions:
            self._hunt_landmines(region["raw_text"], found_landmines)

        # 2. Scan unexpected Tree-sitter anomalies
        for node in error_nodes:
            raw_fragment = raw_source[node.start_byte : node.end_byte]
            text_fragment = raw_fragment.decode("utf-8", errors="replace")
            self._hunt_landmines(text_fragment, found_landmines)

        # Calculate standard RIP severity
        score = found_landmines["normalized_score"]
        if score >= 4:
            found_landmines["severity"] = "High"
        elif score >= 2:
            found_landmines["severity"] = "Medium"

        return found_landmines

    def _hunt_landmines(self, text: str, tracker: dict[str, Any]):
        """Evaluates a specific text fragment against all landmine regex patterns."""
        for flag_name, pattern in self.landmine_patterns.items():
            if pattern.search(text):
                if flag_name not in tracker["flags"]:
                    tracker["flags"].append(flag_name)
                    # Simple heuristic: Each unique flag adds +1 to risk score
                    tracker["normalized_score"] += 1

    # ---------------------------------------------------------
    # Utilities
    # ---------------------------------------------------------
    def _extract_coordinates(self, point_obj: Any) -> tuple[int, int]:
        """
        Safely extracts (row, column) from a Tree-sitter Point.
        Accounts for API changes between library versions.
        """
        if hasattr(point_obj, "row") and hasattr(point_obj, "column"):
            return point_obj.row, point_obj.column
        elif isinstance(point_obj, (tuple, list)) and len(point_obj) >= 2:
            return point_obj[0], point_obj[1]
        return 0, 0

    def _calculate_health(self, root: Node) -> float:
        """
        Determines structural integrity for analytics.
        100.0 = Perfect Parse. < 80.0 = High reliance on Regex Fallback.
        """
        if not root:
            return 0.0
        if not root.has_error:
            return 100.0

        total_nodes = root.child_count
        if total_nodes == 0:
            return 0.0

        error_nodes = len(self._get_error_nodes(root))
        health = ((total_nodes - error_nodes) / total_nodes) * 100.0
        return round(max(0.0, health), 2)
