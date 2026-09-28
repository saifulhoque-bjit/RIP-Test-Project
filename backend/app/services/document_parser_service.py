"""Document parsing service.

Receives file content, delegates to LlamaParse, returns raw parsed dict.
"""

from __future__ import annotations

from pathlib import Path
import sys
from uuid import UUID

from app.clients.llamaparser_client import LlamaParserClient
from app.db.unit_of_work import UnitOfWork
from app.schemas.document_schema import DocumentChunksResponse
from app.services.source_service import SourceService

# from app.utils.document_chunker import ChunkerPipeline, normalize_nodes
from app.utils.document_fragment_formatter import format_parsed
from app.utils.logger import get_logger

logger = get_logger(__name__)


class DocumentService:
    """Parses a single document via LlamaParse. Returns raw result dict."""

    def __init__(self) -> None:
        self._parser = LlamaParserClient()
        # self._chunker = ChunkerPipeline()

    async def get_file(self, source_id) -> bytes:
        """Fetch file content from S3 via SourceService."""
        with UnitOfWork() as uow:
            (
                local_path,
                content_type,
                original_name,
            ) = await SourceService().download_single_file_in_local(
                source_id=source_id,
                uow=uow,
            )

        logger.info(
            "          Downloaded: path=%s  content_type=%s  name=%s",
            local_path,
            content_type,
            original_name,
        )
        file_bytes = Path(local_path).read_bytes()
        if not file_bytes:
            logger.error("Downloaded file is empty: %s", local_path)
            sys.exit(1)

        logger.info("          File size: %d bytes", len(file_bytes))
        try:
            Path(local_path).unlink(missing_ok=True)
            logger.info("Cleaned up local file: %s", local_path)
        except OSError:
            pass
        return file_bytes

    # async def parse_and_get_chunks(self, source_id: UUID) -> DocumentChunksResponse:
    #     """Parse file content and return the chunks."""
    #
    #     content= await self.get_file(source_id)
    #     raw = await self._parser.parse(source_id, content)
    #     nodes=normalize_nodes(raw)
    #     chunks=self._chunker.run(nodes)
    #
    #     return DocumentChunksResponse(source_id=source_id, chunks=chunks)

    async def parse_and_format_for_alternate_pipeline(
        self, source_id: UUID
    ) -> DocumentChunksResponse:
        """Parse file content and return the chunks."""

        content = await self.get_file(source_id)
        raw = await self._parser.parse(source_id, content)
        chunks = format_parsed(raw)

        return DocumentChunksResponse(source_id=source_id, chunks=chunks)
