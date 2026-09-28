"""Standalone script to test the OmniParser pipeline.

NOT part of the FastAPI server — run directly from the terminal:

    python -m scripts.test_omniparser_pipeline <source-uuid>

Flow:
    1. Opens a DB connection via UnitOfWork.
    2. Looks up the source by UUID (reuses SourceService.download_file).
    3. Downloads the file from S3 to a local temp path.
    4. Base64-encodes the file and sends it to OmniParser.
    5. Prints the structured result to stdout.
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import sys
from uuid import UUID

# ensure project root is on sys.path so `app.*` imports resolve
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.clients.omniparser_client import omniparser_client
from app.db.unit_of_work import UnitOfWork
from app.services.source_service import SourceService
from app.utils.logger import get_logger

logger = get_logger(__name__)


async def run_pipeline(source_id: UUID) -> None:
    """Execute the full pipeline: DB → S3 → base64 → OmniParser → print."""

    # ── 1. Download file from S3 (uses DB lookup internally) ───────────
    logger.info("Step 1/3  — Downloading source %s from S3 via DB lookup …", source_id)

    with UnitOfWork() as uow:
        local_path, content_type, original_name = await SourceService().download_file(
            source_id=source_id,
            uow=uow,
        )

    logger.info(
        "          Downloaded: path=%s  content_type=%s  name=%s",
        local_path,
        content_type,
        original_name,
    )

    # ── 2. Read local file and base64-encode ───────────────────────────
    logger.info("Step 2/3  — Base64-encoding the downloaded file …")

    file_bytes = Path(local_path).read_bytes()
    if not file_bytes:
        logger.error("Downloaded file is empty: %s", local_path)
        sys.exit(1)

    image_base64 = base64.b64encode(file_bytes).decode("utf-8")
    logger.info(
        "          File size: %d bytes  |  Base64 length: %d chars",
        len(file_bytes),
        len(image_base64),
    )

    # ── 3. Send to OmniParser ──────────────────────────────────────────
    logger.info("Step 3/3  — Sending to OmniParser …")

    ctx = {
        "request_id": f"test-{source_id}",
        "user_id": "script",
        "project_id": "test",
    }

    result = await omniparser_client.parse_image_base64(
        base64_image=image_base64,
        ctx=ctx,
    )

    # ── Print result ───────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("OmniParser Result")
    print("=" * 60)
    print(f"Source ID       : {source_id}")
    print(f"File            : {original_name}")
    print(f"Content-Type    : {content_type}")
    print(f"Total elements  : {len(result.elements)}")
    print("-" * 60)

    for i, el in enumerate(result.elements, 1):
        print(
            f"  [{i:>3}]  type={el.type:<8}  "
            f"interactive={el.interactivity}  "
            f'content="{el.content}"  '
            f"bbox={el.bbox}  "
            f"source={el.source}"
        )

    print("=" * 60)
    print("\nRaw JSON:")
    print(json.dumps(result.model_dump(), indent=2))

    # ── Save output to txt ─────────────────────────────────────────────
    output_dir = Path(__file__).resolve().parent / "outputs"
    output_dir.mkdir(exist_ok=True)
    safe_stem = Path(original_name).stem.replace(" ", "_")
    output_file = output_dir / f"omniparser_{safe_stem}.txt"

    with open(output_file, "w", encoding="utf-8") as f:
        f.write(f"Source ID       : {source_id}\n")
        f.write(f"File            : {original_name}\n")
        f.write(f"Content-Type    : {content_type}\n")
        f.write(f"Total elements  : {len(result.elements)}\n")
        f.write("-" * 60 + "\n")
        for i, el in enumerate(result.elements, 1):
            f.write(
                f"  [{i:>3}]  type={el.type:<8}  "
                f"interactive={el.interactivity}  "
                f'content="{el.content}"  '
                f"bbox={el.bbox}  "
                f"source={el.source}\n"
            )
        f.write("=" * 60 + "\n\n")
        f.write("Raw JSON:\n")
        f.write(json.dumps(result.model_dump(), indent=2))

    logger.info("Output saved to: %s", output_file)

    # ── Cleanup local file (best-effort) ───────────────────────────────
    try:
        Path(local_path).unlink(missing_ok=True)
        logger.info("Cleaned up local file: %s", local_path)
    except OSError:
        pass

    logger.info("Pipeline complete.")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage:  python -m scripts.test_omniparser_pipeline <source-uuid>")
        sys.exit(1)

    try:
        source_id = UUID(sys.argv[1])
    except ValueError:
        print(f"Error: '{sys.argv[1]}' is not a valid UUID.")
        sys.exit(1)

    asyncio.run(run_pipeline(source_id))


if __name__ == "__main__":
    main()
