# formatter.py


def format_parsed(raw: dict) -> list:
    pages = raw["items"]["pages"]
    chunks = []

    for page in pages:
        page_num = page["page_number"]

        for item in page["items"]:
            chunks.append(
                {
                    "frag_type": item.get("type", "unknown"),
                    "content": _extract_text(item),
                    "bbox": [
                        {
                            "page": page_num,
                            "bbox": _extract_bbox(item),
                            "confidence": _extract_confidence(item),
                        }
                    ],
                }
            )

    return chunks


def _extract_bbox(item: dict) -> dict:
    bbox = item.get("bbox")
    if isinstance(bbox, list) and len(bbox) > 0:
        b = bbox[0]
        return {"x": b.get("x", 0), "y": b.get("y", 0), "w": b.get("w", 0), "h": b.get("h", 0)}
    elif isinstance(bbox, dict):
        return {
            "x": bbox.get("x", 0),
            "y": bbox.get("y", 0),
            "w": bbox.get("w", 0),
            "h": bbox.get("h", 0),
        }
    nested = item.get("items", [])
    if nested:
        return _extract_bbox(nested[0])
    return {"x": 0, "y": 0, "w": 0, "h": 0}


def _extract_confidence(item: dict) -> float:
    bbox = item.get("bbox")
    if isinstance(bbox, list) and len(bbox) > 0:
        return bbox[0].get("confidence") or 0.0
    elif isinstance(bbox, dict):
        return bbox.get("confidence") or 0.0
    return 0.0


def _extract_text(item: dict) -> str:
    if item.get("md"):
        return item["md"]
    if item.get("value"):
        return item["value"]
    nested = item.get("items", [])
    if nested:
        return "\n".join(_extract_text(sub) for sub in nested)
    return ""
