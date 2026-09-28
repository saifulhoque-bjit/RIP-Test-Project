import argparse
import json
from pathlib import Path


class Neo4jExporter:
    """
    Day 5 Task 16: Neo4j Traceability Exporter
    Generates a Cypher script to build a living Traceability Matrix connecting
    Modules, MFUs, Legacy Artifacts, and Modern Traceability Keys.

    CRITICAL FIXES APPLIED:
    - Guaranteed Cypher Scope: Merged NODE creation and SET operations into single atomic statements.
    - Statement Termination: Appended semicolons (;) to all executable queries.
    - Idempotent Relations: Enforced strict MATCH -> MERGE patterns to prevent duplicate edges.
    - Phase 3 RIP Update: Shifted to execution_track and injected risk_flags/traceability into the graph.
    - Graph Integrity: Added resolved_dependencies and native Cypher list structures.
    - AST Spatial Traceability (Task 3.2): Dynamically maps AST line numbers to edges and enforces Edge Multiplicity.
    """

    def __init__(self, project_root: str, global_graph_path: str = None):
        self.root = Path(project_root)
        self.global_graph_path = Path(global_graph_path) if global_graph_path else None
        self.cypher_statements = []

        # Ingest Global Artifact Data for CALLS relationships (optional — Stage 5a omits it)
        self.global_artifacts = self._load_global_graph()

    def _load_global_graph(self) -> dict:
        """Loads artifacts_enriched.json to extract dependency logic."""
        if self.global_graph_path is None:
            return {}
        if not self.global_graph_path.exists():
            print(f"[WARN] Global graph not found at {self.global_graph_path}")
            return {}

        try:
            data = json.loads(self.global_graph_path.read_text(encoding="utf-8"))
            return {a["id"]: a for a in data.get("artifacts", []) if "id" in a}
        except Exception as e:
            print(f"[ERR] Failed to load global graph: {e}")
            return {}

    def _escape_cypher(self, value: str) -> str:
        """Escapes single quotes to prevent Cypher syntax errors."""
        if not isinstance(value, str):
            return str(value)
        return value.replace("'", "\\'")

    def _to_cypher_list(self, py_list: list) -> str:
        """Converts a Python list of strings into a Cypher array format: ['a', 'b']"""
        if not py_list:
            return "[]"
        escaped_items = [f"'{self._escape_cypher(str(item))}'" for item in py_list]
        return f"[{', '.join(escaped_items)}]"

    def _build_edge_props(self, edge: dict) -> str:
        """
        Dynamically builds Cypher relationship properties from the edge dictionary.
        Filters out zero-values to keep the Neo4j database clean and performant.
        """
        props = []
        edge_type = edge.get("type")
        if edge_type:
            props.append(f"type: '{self._escape_cypher(edge_type)}'")

        # Extract spatial data (AST or Legacy Regex)
        for key in ["line", "line_start", "line_end", "column"]:
            val = edge.get(key)
            if val:  # Evaluates to True if > 0
                props.append(f"{key}: {int(val)}")

        if not props:
            return ""
        return "{" + ", ".join(props) + "}"

    def _add_statement(self, stmt: str):
        self.cypher_statements.append(stmt)

    def generate_graph_indexes(self):
        """Creates indexes for performant querying in Neo4j."""
        self._add_statement(
            "CREATE CONSTRAINT IF NOT EXISTS FOR (m:Module) REQUIRE m.name IS UNIQUE;"
        )
        self._add_statement(
            "CREATE CONSTRAINT IF NOT EXISTS FOR (mfu:MFU) REQUIRE mfu.id IS UNIQUE;"
        )
        self._add_statement(
            "CREATE CONSTRAINT IF NOT EXISTS FOR (a:LegacyArtifact) REQUIRE a.id IS UNIQUE;"
        )
        self._add_statement(
            "CREATE CONSTRAINT IF NOT EXISTS FOR (u:UnresolvedArtifact) REQUIRE u.id IS UNIQUE;"
        )
        self._add_statement(
            "CREATE CONSTRAINT IF NOT EXISTS FOR (t:TraceabilityNode) REQUIRE t.id IS UNIQUE;"
        )
        self._add_statement("")

    def build_matrix(self):
        """Sweeps the directory for config_naming_map.json and builds Cypher nodes."""
        print("--- Building Neo4j Traceability Matrix ---")
        self.generate_graph_indexes()

        modules_dir = self.root / "projects" / "sample_project" / "modules"
        if not modules_dir.exists():
            print(f"[ERR] Modules directory not found: {modules_dir}")
            return

        processed_mfus = 0
        processed_mappings = 0

        # Pass 1: Build Modules, MFUs, Artifacts, and Mappings
        for mod_path in modules_dir.iterdir():
            if not mod_path.is_dir():
                continue

            specs_dir = mod_path / "stage4_specs"
            if not specs_dir.exists():
                continue

            for mfu_dir in specs_dir.iterdir():
                if not mfu_dir.is_dir():
                    continue

                config_file = mfu_dir / "config_naming_map.json"
                if not config_file.exists():
                    continue

                try:
                    config = json.loads(config_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    print(f"[WARN] Skipping invalid JSON: {config_file}")
                    continue

                module_name = self._escape_cypher(config.get("module", mod_path.name))
                mfu_id = self._escape_cypher(config.get("mfu_id", mfu_dir.name))
                mfu_name = self._escape_cypher(config.get("mfu_name", "Unnamed MFU"))

                # CRITICAL RIP FIX: execution_track replaces estimation_track
                track = self._escape_cypher(config.get("execution_track", "Unknown"))

                # NEW RIP DATA: Format as native Cypher Arrays for powerful downstream querying
                trace_chain = config.get("traceability_chain", [])
                risk_flags = config.get("risk_flags", [])

                cypher_trace_chain = self._to_cypher_list(trace_chain)
                cypher_risk_flags = self._to_cypher_list(risk_flags)

                # 1. Module Node
                self._add_statement(f"MERGE (mod:Module {{name: '{module_name}'}});")

                # 2. MFU Node (Atomic MERGE + SET with new RIP array parameters)
                self._add_statement(
                    f"MERGE (mfu:MFU {{id: '{mfu_id}'}}) "
                    f"SET mfu.name = '{mfu_name}', mfu.track = '{track}', "
                    f"mfu.traceability_chain = {cypher_trace_chain}, "
                    f"mfu.risk_flags = {cypher_risk_flags};"
                )

                # 3. Module -> MFU Edge
                self._add_statement(
                    f"MATCH (mod:Module {{name: '{module_name}'}}), (mfu:MFU {{id: '{mfu_id}'}}) MERGE (mod)-[:OWNS]->(mfu);"
                )

                # 4. Artifact Nodes and MFU -> Artifact Edges (Including Resolved Dependencies)
                source_artifacts = config.get("source_artifacts", [])
                resolved_deps = config.get("resolved_dependencies", [])
                all_mfu_artifacts = list(set(source_artifacts + resolved_deps))

                for art_id in all_mfu_artifacts:
                    safe_art = self._escape_cypher(art_id)
                    art_type = self._escape_cypher(
                        self.global_artifacts.get(art_id, {}).get("type", "unknown")
                    )

                    # Atomic Artifact Creation + Linking
                    self._add_statement(
                        f"MERGE (a:LegacyArtifact {{id: '{safe_art}'}}) SET a.type = '{art_type}';"
                    )
                    self._add_statement(
                        f"MATCH (mfu:MFU {{id: '{mfu_id}'}}), (a:LegacyArtifact {{id: '{safe_art}'}}) MERGE (mfu)-[:COMPOSED_OF]->(a);"
                    )

                # 5. Traceability Nodes (Naming Map)
                naming_map = config.get("naming_map", {})
                for legacy_key, modern_val in naming_map.items():
                    safe_legacy = self._escape_cypher(legacy_key)
                    safe_modern = self._escape_cypher(modern_val)

                    # Create a unique ID combining MFU and modern val to prevent global collisions
                    # if different MFUs map the same legacy string differently.
                    trace_node_id = f"{mfu_id}::{safe_modern}"

                    # Atomic Traceability Node Creation + Linking
                    self._add_statement(
                        f"MERGE (t:TraceabilityNode {{id: '{trace_node_id}'}}) SET t.modern_key = '{safe_modern}', t.legacy_source = '{safe_legacy}';"
                    )
                    self._add_statement(
                        f"MATCH (mfu:MFU {{id: '{mfu_id}'}}), (t:TraceabilityNode {{id: '{trace_node_id}'}}) MERGE (mfu)-[:SPECIFIES_MAPPING]->(t);"
                    )
                    processed_mappings += 1

                self._add_statement("")  # Empty line for readability
                processed_mfus += 1

        # Pass 2: Inject Global Graph 'CALLS' Relationships safely with AST Spatial awareness
        self._add_statement("// --- Topological Dependencies (AST Spatial) ---")
        for art_id, meta in self.global_artifacts.items():
            safe_source = self._escape_cypher(art_id)
            edges = meta.get("edges", [])

            for edge in edges:
                target_id = edge.get("target")
                if not target_id:
                    continue

                safe_target = self._escape_cypher(target_id)
                resolved = edge.get("resolved", True)  # Fallback to True if not explicitly flagged

                # Isolate missing code from the healthy graph to prevent phantom dependencies
                target_label = "LegacyArtifact" if resolved else "UnresolvedArtifact"

                # Ensure nodes exist before linking them (safeguard against unmapped artifacts)
                self._add_statement(f"MERGE (s:LegacyArtifact {{id: '{safe_source}'}});")
                self._add_statement(f"MERGE (t:{target_label} {{id: '{safe_target}'}});")

                # Dynamically build edge properties to map precise code topology
                props_str = self._build_edge_props(edge)

                # AST Multiplicity Fix: Using props_str inside the MERGE brackets guarantees
                # that Neo4j creates distinct edges if an artifact is called on multiple different lines.
                self._add_statement(
                    f"MATCH (s:LegacyArtifact {{id: '{safe_source}'}}), (t:{target_label} {{id: '{safe_target}'}}) "
                    f"MERGE (s)-[:CALLS {props_str}]->(t);"
                )

        print(f"--- Matrix Ready: {processed_mfus} MFUs, {processed_mappings} Trace Maps ---")

    def export(self, output_path: str):
        """Writes the Cypher script to disk."""
        out_file = Path(output_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)

        script_content = "\n".join(self.cypher_statements)
        out_file.write_text(script_content, encoding="utf-8")
        print(f"[OK] Cypher export saved to: {out_file.absolute()}")

    def generate_feature_story_indexes(self):
        """Creates Neo4j constraints for Stage 5 nodes."""
        self._add_statement("// --- Stage 5: Feature & Story Traceability Constraints ---")
        self._add_statement(
            "CREATE CONSTRAINT IF NOT EXISTS FOR (f:Feature) REQUIRE f.id IS UNIQUE;"
        )
        self._add_statement(
            "CREATE CONSTRAINT IF NOT EXISTS FOR (s:UserStory) REQUIRE s.id IS UNIQUE;"
        )
        self._add_statement(
            "CREATE CONSTRAINT IF NOT EXISTS FOR (d:SRSDocument) REQUIRE d.id IS UNIQUE;"
        )
        self._add_statement(
            "CREATE CONSTRAINT IF NOT EXISTS FOR (sec:SRSSection) REQUIRE sec.id IS UNIQUE;"
        )
        self._add_statement(
            "CREATE CONSTRAINT IF NOT EXISTS FOR (b:BoundingBox) REQUIRE b.id IS UNIQUE;"
        )
        self._add_statement(
            "CREATE CONSTRAINT IF NOT EXISTS FOR (rd:RFPDocument) REQUIRE rd.id IS UNIQUE;"
        )
        self._add_statement(
            "CREATE CONSTRAINT IF NOT EXISTS FOR (rs:RFPSection) REQUIRE rs.id IS UNIQUE;"
        )
        self._add_statement(
            "CREATE CONSTRAINT IF NOT EXISTS FOR (j:JiraIssue) REQUIRE j.key IS UNIQUE;"
        )
        self._add_statement(
            "CREATE CONSTRAINT IF NOT EXISTS FOR (tc:TestCase) REQUIRE tc.id IS UNIQUE;"
        )
        self._add_statement("")

    def _ingest_features_stories(self) -> list:
        """
        Sweeps all stage5_backlog/features_stories.json files under modules/.
        Returns a flat list of parsed JSON objects.
        """
        results = []
        modules_dir = self.root / "projects" / "sample_project" / "modules"
        if not modules_dir.exists():
            print(f"[WARN] Modules directory not found for Stage 5 sweep: {modules_dir}")
            return results

        for mod_path in sorted(modules_dir.iterdir()):
            if not mod_path.is_dir():
                continue
            specs_dir = mod_path / "stage4_specs"
            if not specs_dir.exists():
                continue
            for mfu_dir in sorted(specs_dir.iterdir()):
                if not mfu_dir.is_dir():
                    continue
                # features_stories.json is written directly into the MFU directory
                # by FeatureStoryAgent._write_output (no stage5_backlog subdir).
                fs_file = mfu_dir / "features_stories.json"
                if not fs_file.exists():
                    continue
                try:
                    data = json.loads(fs_file.read_text(encoding="utf-8"))
                    results.append(data)
                    print(f"[STAGE5] Loaded: {fs_file.relative_to(self.root)}")
                except Exception as e:
                    print(f"[WARN] Skipping invalid features_stories.json at {fs_file}: {e}")
        return results

    def _build_srs_l1_node(self, l1: dict):
        """Emits MERGE for an SRSDocument (L1) node."""
        l1_id = self._escape_cypher(l1.get("id", ""))
        doc_type = self._escape_cypher(l1.get("document_type", "unknown"))
        path = self._escape_cypher(l1.get("path", ""))
        screen_id = self._escape_cypher(l1.get("screen_id", ""))
        mfu_id = self._escape_cypher(l1.get("mfu_id", ""))

        is_rfp = doc_type.startswith("RFP_")
        label = "RFPDocument" if is_rfp else "SRSDocument"

        self._add_statement(
            f"MERGE (d:{label} {{id: '{l1_id}'}}) "
            f"SET d.document_type = '{doc_type}', d.path = '{path}', "
            f"d.screen_id = '{screen_id}', d.mfu_id = '{mfu_id}';"
        )

        # Link L1 → MFU: (MFU)-[:HAS_SPEC]->(SRSDocument)
        if mfu_id:
            self._add_statement(
                f"MATCH (mfu:MFU {{id: '{mfu_id}'}}), (d:{label} {{id: '{l1_id}'}}) "
                f"MERGE (mfu)-[:HAS_SPEC]->(d);"
            )

    def _build_l2_section_node(self, sec: dict, l1_id: str):
        """Emits MERGE for an SRSSection (L2) node and links it to its L1 document."""
        sec_id = self._escape_cypher(sec.get("id", ""))
        sec_type = self._escape_cypher(sec.get("section_type", "unknown"))
        sec_ref = self._escape_cypher(sec.get("section_ref", ""))
        trace_id = self._escape_cypher(sec.get("trace_id", ""))
        control_name = self._escape_cypher(sec.get("control_name", ""))
        line_start = sec.get("line_start", 0)
        line_end = sec.get("line_end", 0)

        is_rfp = sec_type.startswith("RFP_")
        label = "RFPSection" if is_rfp else "SRSSection"
        l1_label = "RFPDocument" if is_rfp else "SRSDocument"

        line_props = ""
        if line_start:
            line_props += f", line_start: {int(line_start)}"
        if line_end:
            line_props += f", line_end: {int(line_end)}"

        self._add_statement(
            f"MERGE (sec:{label} {{id: '{sec_id}'}}) "
            f"SET sec.section_type = '{sec_type}', sec.section_ref = '{sec_ref}', "
            f"sec.trace_id = '{trace_id}', sec.control_name = '{control_name}'"
            f"{line_props};"
        )

        # (SRSDocument)-[:CONTAINS_SECTION]->(SRSSection)
        safe_l1 = self._escape_cypher(l1_id)
        self._add_statement(
            f"MATCH (d:{l1_label} {{id: '{safe_l1}'}}), (sec:{label} {{id: '{sec_id}'}}) "
            f"MERGE (d)-[:CONTAINS_SECTION]->(sec);"
        )

        # If trace_id present, link (SRSSection)-[:REFERENCES_ARTIFACT]->(LegacyArtifact)
        # Resolve artifact_id from trace_id format: TRCE-LOGIC-{artifact_id}-L{line}
        if trace_id and trace_id.startswith("TRCE-"):
            parts = trace_id.split("-")
            # Format: TRCE-LOGIC-{artifact_id}-L{line} — artifact_id is everything between index 2 and last
            if len(parts) >= 4:
                # Last part is L{line}, artifact_id is parts[2:-1] joined by "-"
                art_id = self._escape_cypher("-".join(parts[2:-1]))
                self._add_statement(f"MERGE (la:LegacyArtifact {{id: '{art_id}'}});")
                self._add_statement(
                    f"MATCH (sec:{label} {{id: '{sec_id}'}}), (la:LegacyArtifact {{id: '{art_id}'}}) "
                    f"MERGE (sec)-[:REFERENCES_ARTIFACT]->(la);"
                )

    def _build_bounding_box_node(self, bbox: dict, feature_id: str):
        """Emits MERGE for a BoundingBox node and links it to its Feature."""
        bbox_id = self._escape_cypher(bbox.get("id", ""))
        zone_id = self._escape_cypher(bbox.get("zone_id", ""))
        zone_label_en = self._escape_cypher(bbox.get("zone_label_en", ""))
        zone_label_jp = self._escape_cypher(bbox.get("zone_label_jp", ""))
        screen_pos = self._escape_cypher(bbox.get("screen_position", ""))
        highlight = self._escape_cypher(bbox.get("highlight_color", "#CCCCCC"))

        controls = self._to_cypher_list(bbox.get("controls", []))
        s3_refs = self._to_cypher_list(bbox.get("s3_row_refs", []))

        self._add_statement(
            f"MERGE (b:BoundingBox {{id: '{bbox_id}'}}) "
            f"SET b.zone_id = '{zone_id}', b.zone_label_en = '{zone_label_en}', "
            f"b.zone_label_jp = '{zone_label_jp}', b.screen_position = '{screen_pos}', "
            f"b.highlight_color = '{highlight}', b.controls = {controls}, "
            f"b.s3_row_refs = {s3_refs};"
        )

        # (Feature)-[:VISUALIZED_BY]->(BoundingBox)
        safe_fid = self._escape_cypher(feature_id)
        self._add_statement(
            f"MATCH (f:Feature {{id: '{safe_fid}'}}), (b:BoundingBox {{id: '{bbox_id}'}}) "
            f"MERGE (f)-[:VISUALIZED_BY]->(b);"
        )

    def _build_user_story_node(self, story: dict, feature_id: str):
        """Emits MERGE for a UserStory (L3) node and links it to Feature + L2 sections."""
        sid = self._escape_cypher(story.get("id", ""))
        title = self._escape_cypher(story.get("title", ""))
        as_a = self._escape_cypher(story.get("as_a", ""))
        i_want = self._escape_cypher(story.get("i_want_to", ""))
        so_that = self._escape_cypher(story.get("so_that", ""))
        points = story.get("story_points", 3)
        tech_notes = self._escape_cypher(story.get("technical_notes", ""))
        mig_hint = self._escape_cypher(story.get("migration_hint", ""))
        jira_key = self._escape_cypher(story.get("jira_issue_key", ""))

        ac_list = story.get("acceptance_criteria", [])
        ac_ids = self._to_cypher_list([ac.get("id", "") for ac in ac_list])

        self._add_statement(
            f"MERGE (s:UserStory {{id: '{sid}'}}) "
            f"SET s.title = '{title}', s.as_a = '{as_a}', "
            f"s.i_want_to = '{i_want}', s.so_that = '{so_that}', "
            f"s.story_points = {int(points)}, s.technical_notes = '{tech_notes}', "
            f"s.migration_hint = '{mig_hint}', s.jira_issue_key = '{jira_key}', "
            f"s.ac_ids = {ac_ids};"
        )

        # (Feature)-[:HAS_STORY]->(UserStory)
        safe_fid = self._escape_cypher(feature_id)
        self._add_statement(
            f"MATCH (f:Feature {{id: '{safe_fid}'}}), (s:UserStory {{id: '{sid}'}}) "
            f"MERGE (f)-[:HAS_STORY]->(s);"
        )

        # (UserStory)-[:TRACES_TO]->(SRSSection/RFPSection)
        for l2_id in story.get("l2_sources", []):
            safe_l2 = self._escape_cypher(l2_id)
            # Determine label from ID prefix
            sec_label = "RFPSection" if l2_id.startswith("RFP::") else "SRSSection"
            self._add_statement(
                f"MATCH (s:UserStory {{id: '{sid}'}}), (sec:{sec_label} {{id: '{safe_l2}'}}) "
                f"MERGE (s)-[:TRACES_TO]->(sec);"
            )

        # L4: Jira Issue node (created if key present)
        if jira_key:
            self._add_statement(f"MERGE (j:JiraIssue {{key: '{jira_key}'}});")
            self._add_statement(
                f"MATCH (s:UserStory {{id: '{sid}'}}), (j:JiraIssue {{key: '{jira_key}'}}) "
                f"MERGE (s)-[:IMPLEMENTED_BY]->(j);"
            )

        # L4: Test Case nodes
        for tc_id in story.get("test_case_ids", []):
            safe_tc = self._escape_cypher(tc_id)
            self._add_statement(f"MERGE (tc:TestCase {{id: '{safe_tc}'}});")
            self._add_statement(
                f"MATCH (s:UserStory {{id: '{sid}'}}), (tc:TestCase {{id: '{safe_tc}'}}) "
                f"MERGE (s)-[:VERIFIED_BY]->(tc);"
            )

    def _build_feature_node(self, feature: dict, module_id: str, l1_id: str):
        """Emits MERGE for a Feature node and all its children (BoundingBox, UserStory)."""
        fid = self._escape_cypher(feature.get("id", ""))
        title = self._escape_cypher(feature.get("title", ""))
        desc = self._escape_cypher(feature.get("description", ""))
        source = self._escape_cypher(feature.get("source", ""))

        l2_ids = self._to_cypher_list(feature.get("l2_sources", []))

        self._add_statement(
            f"MERGE (f:Feature {{id: '{fid}'}}) "
            f"SET f.title = '{title}', f.description = '{desc}', "
            f"f.source = '{source}', f.l2_source_ids = {l2_ids};"
        )

        # (Module)-[:HAS_FEATURE]->(Feature)
        safe_mod = self._escape_cypher(module_id)
        self._add_statement(
            f"MATCH (mod:Module {{name: '{safe_mod}'}}), (f:Feature {{id: '{fid}'}}) "
            f"MERGE (mod)-[:HAS_FEATURE]->(f);"
        )

        # (Feature)-[:DERIVED_FROM]->(SRSSection/RFPSection)
        for l2_id in feature.get("l2_sources", []):
            safe_l2 = self._escape_cypher(l2_id)
            sec_label = "RFPSection" if l2_id.startswith("RFP::") else "SRSSection"
            self._add_statement(
                f"MATCH (f:Feature {{id: '{fid}'}}), (sec:{sec_label} {{id: '{safe_l2}'}}) "
                f"MERGE (f)-[:DERIVED_FROM]->(sec);"
            )

        # BoundingBox
        bbox = feature.get("bounding_box")
        if bbox:
            self._build_bounding_box_node(bbox, fid)

        # User Stories (L3)
        for story in feature.get("user_stories", []):
            self._build_user_story_node(story, fid)

    def build_feature_story_graph(self):
        """
        Stage 5 graph builder.

        Reads all stage5_backlog/features_stories.json files from the project tree and
        emits Cypher statements that extend the existing traceability graph with:

          L1:  SRSDocument / RFPDocument  ← source documents
          L2:  SRSSection / RFPSection    ← specific sections / controls / events
          M:   Module                     ← already in graph from build_matrix()
          F:   Feature                    ← new: Module -[:HAS_FEATURE]-> Feature
          L3:  UserStory                  ← new: Feature -[:HAS_STORY]-> UserStory
          L4:  JiraIssue / TestCase       ← future: UserStory -[:IMPLEMENTED_BY / VERIFIED_BY]->

        Cross-links to existing graph:
          (MFU)-[:HAS_SPEC]->(SRSDocument)
          (SRSSection)-[:REFERENCES_ARTIFACT]->(LegacyArtifact)
          (Feature)-[:VISUALIZED_BY]->(BoundingBox)
          (UserStory)-[:TRACES_TO]->(SRSSection)

        This method is idempotent — all statements use MERGE.
        """
        print("--- Building Stage 5 Feature & Story Traceability Graph ---")
        self.generate_feature_story_indexes()

        all_data = self._ingest_features_stories()
        if not all_data:
            print("[STAGE5] No features_stories.json files found. Stage 5 graph skipped.")
            return

        total_features = 0
        total_stories = 0
        total_l2_sections = set()

        for fs_doc in all_data:
            l1 = fs_doc.get("l1_source", {})
            l1_id = l1.get("id", "")
            module_id = fs_doc.get("module_id", "")
            module_name = fs_doc.get("module_name", module_id)

            self._add_statement(f"// --- Module: {module_id} | MFU: {fs_doc.get('mfu_id', '')} ---")

            # Ensure Module node exists (may already exist from build_matrix)
            safe_mod = self._escape_cypher(module_name)
            self._add_statement(f"MERGE (mod:Module {{name: '{safe_mod}'}});")

            # L1 document node
            self._build_srs_l1_node(l1)

            # Collect and emit all L2 sections mentioned across features + stories
            l2_registry: dict[str, dict] = {}
            for feature in fs_doc.get("features", []):
                for l2_id in feature.get("l2_sources", []):
                    if l2_id not in l2_registry:
                        l2_registry[l2_id] = {
                            "id": l2_id,
                            "section_type": "S3_ControlRow",
                            "section_ref": l2_id,
                        }
                bbox = feature.get("bounding_box", {})
                for ref in bbox.get("s3_row_refs", []):
                    if ref not in l2_registry:
                        l2_registry[ref] = {
                            "id": ref,
                            "section_type": "S3_ControlRow",
                            "section_ref": ref,
                        }
                for story in feature.get("user_stories", []):
                    for l2_id in story.get("l2_sources", []):
                        if l2_id not in l2_registry:
                            l2_registry[l2_id] = {
                                "id": l2_id,
                                "section_type": "S5_Event",
                                "section_ref": l2_id,
                            }
                    for ac in story.get("acceptance_criteria", []):
                        ref = ac.get("l2_source_ref", "")
                        if ref and ref not in l2_registry:
                            l2_registry[ref] = {
                                "id": ref,
                                "section_type": "S5_Event",
                                "section_ref": ref,
                            }

            # Emit L2 section nodes
            for sec_data in l2_registry.values():
                self._build_l2_section_node(sec_data, l1_id)
                total_l2_sections.add(sec_data["id"])

            # Emit Feature nodes (which recursively emit BoundingBox + UserStory)
            for feature in fs_doc.get("features", []):
                self._build_feature_node(feature, module_name, l1_id)
                total_features += 1
                total_stories += len(feature.get("user_stories", []))

            self._add_statement("")  # Readability spacer

        print(
            f"--- Stage 5 Graph Ready: {total_features} Features, {total_stories} Stories, {len(total_l2_sections)} L2 Sections ---"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Neo4j Cypher Exporter for Polyglot MFU Pipeline")
    parser.add_argument("--project_root", default=".", help="Root directory of the project")
    parser.add_argument(
        "--global_graph",
        default="./projects/sample_project/_global/artifacts_enriched.json",
        help="Path to global artifacts graph",
    )
    parser.add_argument(
        "--out", default="./output/traceability_matrix.cypher", help="Path to output .cypher file"
    )

    args = parser.parse_args()

    exporter = Neo4jExporter(args.project_root, args.global_graph)
    exporter.build_matrix()
    exporter.export(args.out)


if __name__ == "__main__":
    main()
