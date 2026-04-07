import json
import logging
import os
import re
from decimal import Decimal
from typing import Any

import boto3
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core.lambda_launcher import patch_all

patch_all()

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
textract = boto3.client("textract")
lambda_client = boto3.client("lambda")

AI_ANALYZER_NAME = os.environ["AI_ANALYZER_NAME"]
UPLOAD_BUCKET = os.environ["UPLOAD_BUCKET"] # This will be needed to fetch the original document if necessary.

@xray_recorder.capture('lambda_handler')
def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    logger.info("Received event: %s", json.dumps(event))

    # Textract sends a notification to SNS, which then triggers this Lambda
    for record in event["Records"]:
        if record["EventSource"] == "aws:sns":
            message = json.loads(record["Sns"]["Message"])
            job_status = message["Status"]
            job_id = message["JobId"]
            document_s3_object = message["DocumentLocation"]["S3Object"]
            bucket = document_s3_object["Bucket"]
            key = document_s3_object["Name"]

            logger.info(
                "Textract job %s finished with status: %s for s3://%s/%s",
                job_id,
                job_status,
                bucket,
                key,
            )

            if job_status == "SUCCEEDED":
                # Retrieve Textract results
                full_text = ""
                table_records = []

                # Get file_hash from S3 object metadata
                try:
                    head_object_response = s3.head_object(Bucket=bucket, Key=key)
                    file_hash = head_object_response["Metadata"].get("file_hash")
                    if not file_hash:
                        logger.warning(f"file_hash not found in S3 metadata for s3://{bucket}/{key}")
                except Exception as e:
                    logger.error(f"Error getting S3 head_object for s3://{bucket}/{key}: {e}")
                    file_hash = None

                pagination_token = None
                while True:
                    if pagination_token:
                        response = textract.get_document_analysis(JobId=job_id, NextToken=pagination_token)
                    else:
                        response = textract.get_document_analysis(JobId=job_id)

                    blocks = response.get("Blocks", [])
                    
                    # Extract full text
                    for block in blocks:
                        if block["BlockType"] == "PAGE":
                            # For full text, we might want to iterate through lines and words
                            pass
                        
                    # Extract table records
                    table_records.extend(_records_from_table_blocks(blocks))
                    
                    pagination_token = response.get("NextToken")
                    if not pagination_token:
                        break

                # Fallback to lines if no tables found, or combine
                if not table_records:
                    lines = [
                        b["Text"]
                        for b in blocks
                        if b.get("BlockType") == "LINE" and "Text" in b
                    ]
                    table_records = _records_from_text_lines(lines)


                document_id, _ = _parse_upload_key(key)
                normalized = {
                    "document_id": document_id,
                    "source_s3_key": key,
                    "records": table_records, # Use table_records for now, can be expanded
                }

                out_key = f"extracted/{document_id}.json"
                s3.put_object(
                    Bucket=bucket,
                    Key=out_key,
                    Body=json.dumps(normalized, default=_json_default).encode("utf-8"),
                    ContentType="application/json",
                )

                lambda_client.invoke(
                    FunctionName=AI_ANALYZER_NAME,
                    InvocationType="Event",
                    Payload=json.dumps(
                        {
                            "document_id": document_id,
                            "normalized_data": normalized,
                            "extracted_s3_key": out_key,
                            "file_hash": file_hash,
                        },
                        default=_json_default
                    ).encode("utf-8"),
                )

            else:
                logger.error("Textract job %s failed with status: %s", job_id, job_status)

    return {"statusCode": 200, "body": json.dumps({"ok": True})}


def _parse_upload_key(key: str) -> tuple[str, str]:
    # uploads/{uuid}.pdf or uploads/{uuid}.csv
    base = key.rsplit("/", 1)[-1]
    if base.lower().endswith(".pdf"):
        return base[:-4], ".pdf"
    if base.lower().endswith(".csv"):
        return base[:-4], ".csv"
    return base, ""


def _records_from_table_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn Textract TABLE blocks into rows (department, category, amount)."""
    by_id = {b["Id"]: b for b in blocks if "Id" in b}
    records: list[dict[str, Any]] = []

    for block in blocks:
        if block.get("BlockType") != "TABLE":
            continue
        cells: list[tuple[int, int, str]] = []
        for rel in block.get("Relationships", []):
            if rel.get("Type") != "CHILD":
                continue
            for cid in rel.get("Ids", []):
                cell = by_id.get(cid)
                if cell and cell.get("BlockType") == "CELL":
                    r = int(cell.get("RowIndex", 1))
                    c = int(cell.get("ColumnIndex", 1))
                    text = _cell_text(cell, by_id).strip()
                    cells.append((r, c, text))
        if not cells:
            continue

        max_r = max(c[0] for c in cells)
        start_row = 1
        row1_text = " ".join(
            t for _, t in sorted(((c, t) for r, c, t in cells if r == 1), key=lambda x: x[0])
        ).lower()
        if any(k in row1_text for k in ("department", "category", "amount", "budget", "total")):
            start_row = 2

        for r in range(start_row, max_r + 1):
            row_cells = sorted([(c, t) for (rr, c, t) in cells if rr == r], key=lambda x: x[0])
            if not row_cells:
                continue
            texts = [t for _, t in row_cells]
            if len(texts) >= 3:
                dept, cat, amt_s = texts[0], texts[1], texts[2]
            elif len(texts) == 2:
                dept, cat, amt_s = "Unknown", texts[0], texts[1]
            else:
                continue
            amt = _parse_amount(amt_s)
            if amt is None:
                continue
            records.append(
                {
                    "department": dept or "Unknown",
                    "category": cat or "Unknown",
                    "amount": amt,
                }
            )

    return records


def _cell_text(cell: dict[str, Any], by_id: dict[str, Any]) -> str:
    parts: list[str] = []
    for rel in cell.get("Relationships", []):
        if rel.get("Type") != "CHILD":
            continue
        for wid in rel.get("Ids", []):
            w = by_id.get(wid)
            if w and w.get("BlockType") == "WORD":
                parts.append(w.get("Text", ""))
    return " ".join(parts)


def _records_from_text_lines(lines: list[str]) -> list[dict[str, Any]]:
    """When no tables: one record per line that contains a money-like number."""
    amount_re = re.compile(r"[\$]?([\d,]+(?:\.\d{2})?)\b")
    out: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = amount_re.search(line)
        if not m:
            continue
        amt = _parse_amount(m.group(1))
        if amt is None:
            continue
        label = (line[: m.start()].strip() or line[m.end() :].strip() or "Line item").strip(" -")
        out.append(
            {
                "department": "Unknown",
                "category": label[:500] or "Unknown",
                "amount": amt,
            }
        )
    return out if out else [{"department": "Unknown", "category": "Full text (unparsed)", "amount": Decimal("0")}]


def _parse_amount(s: str) -> Decimal | None:
    s = str(s).strip().replace(",", "").replace("$", "")
    if not s:
        return None
    try:
        return Decimal(s)
    except Exception:  # noqa: BLE001
        return None


def _json_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(type(o))
