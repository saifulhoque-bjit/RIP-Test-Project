"""Unit tests for small Neo4j domain dataclasses lacking any coverage."""

from __future__ import annotations

import uuid

from app.models.neo4j.config_spec_model import ConfigSpecModel
from app.models.neo4j.project_metadata_model import ProjectMetadataNode


class TestProjectMetadataNode:
    def test_defaults(self):
        project_id = uuid.uuid4()
        node = ProjectMetadataNode(project_id=project_id)

        assert node.project_id == project_id
        assert node.id is None
        assert node.business_requirements == "[]"
        assert node.exclusions == "[]"
        assert node.persona_glossary == "[]"
        assert node.created_at is None
        assert node.updated_at is None
        assert node.deleted_at is None

    def test_all_fields_set(self):
        project_id = uuid.uuid4()
        node_id = uuid.uuid4()
        node = ProjectMetadataNode(
            project_id=project_id,
            id=node_id,
            business_requirements='[{"id": "br-1"}]',
            exclusions='["legacy"]',
            persona_glossary='[{"term": "MFU"}]',
        )

        assert node.id == node_id
        assert node.business_requirements == '[{"id": "br-1"}]'
        assert node.exclusions == '["legacy"]'
        assert node.persona_glossary == '[{"term": "MFU"}]'


class TestConfigSpecModel:
    def test_defaults(self):
        model = ConfigSpecModel(id="cs-1")

        assert model.id == "cs-1"
        assert model.mod_code is None
        assert model.fea_code is None
        assert model.filename is None
        assert model.storage_key is None
        assert model.source_id is None
        assert model.project_id is None
        assert model.created_at is None
        assert model.updated_at is None

    def test_all_fields_set(self):
        model = ConfigSpecModel(
            id="cs-1",
            mod_code="MOD_001",
            fea_code="FEA_001",
            filename="config.json",
            storage_key="key/config.json",
            source_id="src-1",
            project_id="proj-1",
        )

        assert model.mod_code == "MOD_001"
        assert model.fea_code == "FEA_001"
        assert model.filename == "config.json"
        assert model.storage_key == "key/config.json"
        assert model.source_id == "src-1"
        assert model.project_id == "proj-1"
