"""
document_processor — Runs when a new object is created under uploads/ (S3 event).

Detects PDF vs CSV, extracts structured budget rows, saves JSON to extracted/,
then invokes ai_analyzer asynchronously.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import time
from decimal import Decimal
from typing import Any

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
textract = boto3.client("textract")
lambda_client = boto3.client("lambda")

UPLOAD_BUCKET = os.environ["UPLOAD_BUCKET"]
AI_ANALYZER_NAME = os.environ["AI_ANALYZER_NAME"]
TEXTRACT_SNS_TOPIC_ARN = os.environ["TEXTRACT_SNS_TOPIC_ARN"]
TEXTRACT_SERVICE_ROLE_ARN = os.environ["TEXTRACT_SERVICE_ROLE_ARN"]


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    # S3 sends a batch of records; process each new object.
    if "Records" not in event:
        logger.warning("Expected S3 event with Records; got: %s", json.dumps(event)[:500])
        return {"statusCode": 400, "body": "{}"}

    for record in event["Records"]:
        if record.get("eventSource") != "aws:s3":
            continue
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
        key = key.replace("+", " ")  # S3 event encoding

        if not key.startswith("uploads/"):
            continue

        document_id, ext = _parse_upload_key(key)
        if ext not in (".pdf", ".csv"):
            logger.info("Skipping non pdf/csv key: %s", key)
            continue

        file_hash = _get_file_hash(bucket, key)

        if ext == ".pdf":
            try:
                pdf_result = _extract_pdf_records(bucket, key, document_id)
                if pdf_result is None:
                    continue
                normalized, out_key = pdf_result
                _invoke_ai_analyzer(document_id, normalized, out_key, file_hash)
            except Exception as exc:  # noqa: BLE001
                logger.exception("PDF processing failed for %s: %s", key, exc)
                _invoke_ai_analyzer_error(
                    document_id,
                    key,
                    file_hash,
                    f"PDF processing failed before AI analysis: {exc!s}",
                )
            continue
        
        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()

        records = _extract_csv_records(body)

        normalized = {
            "document_id": document_id,
            "source_s3_key": key,
            "records": records,
        }

        out_key = _write_normalized(bucket, document_id, normalized)
        _invoke_ai_analyzer(document_id, normalized, out_key, file_hash)

    return {"statusCode": 200, "body": json.dumps({"ok": True})}


def _parse_upload_key(key: str) -> tuple[str, str]:
    # uploads/{uuid}.pdf or uploads/{uuid}.csv
    base = key.rsplit("/", 1)[-1]
    if base.lower().endswith(".pdf"):
        return base[:-4], ".pdf"
    if base.lower().endswith(".csv"):
        return base[:-4], ".csv"
    return base, ""


def _extract_pdf_records(bucket: str, key: str, document_id: str) -> tuple[dict[str, Any], str] | None:
    """Try async Textract first; fall back to direct polling if needed."""
    logger.info("Starting Textract document analysis for s3://%s/%s", bucket, key)
    try:
        response = textract.start_document_analysis(
            DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}},
            FeatureTypes=["TABLES", "FORMS"],
            NotificationChannel={
                "SNSTopicArn": TEXTRACT_SNS_TOPIC_ARN,
                "RoleArn": TEXTRACT_SERVICE_ROLE_ARN,
            },
            JobTag=key,
        )
        job_id = response["JobId"]
        logger.info("Textract job started for document_id %s with job_id %s", document_id, job_id)
        return None
    except textract.exceptions.InvalidParameterException as exc:
        logger.warning(
            "Textract notification flow rejected for s3://%s/%s; falling back to direct polling. %s",
            bucket,
            key,
            exc,
        )

    response = textract.start_document_analysis(
        DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}},
        FeatureTypes=["TABLES", "FORMS"],
        JobTag=key,
    )
    job_id = response["JobId"]
    blocks = _wait_for_document_analysis(job_id)
    records = _records_from_analysis_blocks(blocks)

    normalized = {
        "document_id": document_id,
        "source_s3_key": key,
        "records": records,
    }
    out_key = _write_normalized(bucket, document_id, normalized)
    return normalized, out_key


def _wait_for_document_analysis(job_id: str, timeout_seconds: int = 90, poll_seconds: int = 3) -> list[dict[str, Any]]:
    deadline = time.time() + timeout_seconds
    first_page: dict[str, Any] | None = None

    while time.time() < deadline:
        response = textract.get_document_analysis(JobId=job_id)
        status = response.get("JobStatus")
        if status == "SUCCEEDED":
            first_page = response
            break
        if status in ("FAILED", "PARTIAL_SUCCESS"):
            raise RuntimeError(f"Textract job {job_id} finished with status {status}")
        time.sleep(poll_seconds)

    if first_page is None:
        raise TimeoutError(f"Timed out waiting for Textract job {job_id}")

    blocks = list(first_page.get("Blocks", []))
    next_token = first_page.get("NextToken")
    while next_token:
        page = textract.get_document_analysis(JobId=job_id, NextToken=next_token)
        blocks.extend(page.get("Blocks", []))
        next_token = page.get("NextToken")
    return blocks


def _records_from_analysis_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    table_records = _records_from_table_blocks(blocks)
    if table_records:
        return table_records

    lines = [block["Text"] for block in blocks if block.get("BlockType") == "LINE" and "Text" in block]
    return _records_from_text_lines(lines)


def _records_from_table_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {block["Id"]: block for block in blocks if "Id" in block}
    records: list[dict[str, Any]] = []

    for block in blocks:
        if block.get("BlockType") != "TABLE":
            continue
        cells: list[tuple[int, int, str]] = []
        for rel in block.get("Relationships", []):
            if rel.get("Type") != "CHILD":
                continue
            for cell_id in rel.get("Ids", []):
                cell = by_id.get(cell_id)
                if cell and cell.get("BlockType") == "CELL":
                    row_idx = int(cell.get("RowIndex", 1))
                    col_idx = int(cell.get("ColumnIndex", 1))
                    text = _cell_text(cell, by_id).strip()
                    cells.append((row_idx, col_idx, text))

        if not cells:
            continue

        max_row = max(cell[0] for cell in cells)
        start_row = 1
        header_text = " ".join(
            text for _, text in sorted(((col, text) for row, col, text in cells if row == 1), key=lambda item: item[0])
        ).lower()
        if any(keyword in header_text for keyword in ("department", "category", "amount", "budget", "total")):
            start_row = 2

        for row_idx in range(start_row, max_row + 1):
            row_cells = sorted([(col, text) for (row, col, text) in cells if row == row_idx], key=lambda item: item[0])
            if not row_cells:
                continue
            texts = [text for _, text in row_cells]
            if len(texts) >= 3:
                dept, cat, amount_s = texts[0], texts[1], texts[2]
            elif len(texts) == 2:
                dept, cat, amount_s = "Unknown", texts[0], texts[1]
            else:
                continue

            amount = _parse_amount(amount_s)
            if amount is None:
                continue
            records.append(
                {
                    "department": dept or "Unknown",
                    "category": cat or "Unknown",
                    "amount": amount,
                }
            )

    return records


def _cell_text(cell: dict[str, Any], by_id: dict[str, Any]) -> str:
    parts: list[str] = []
    for rel in cell.get("Relationships", []):
        if rel.get("Type") != "CHILD":
            continue
        for word_id in rel.get("Ids", []):
            word = by_id.get(word_id)
            if word and word.get("BlockType") == "WORD":
                parts.append(word.get("Text", ""))
    return " ".join(parts)


def _records_from_text_lines(lines: list[str]) -> list[dict[str, Any]]:
    amount_re = re.compile(r"[\$]?([\d,]+(?:\.\d{2})?)\b")
    records: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        match = amount_re.search(line)
        if not match:
            continue
        amount = _parse_amount(match.group(1))
        if amount is None:
            continue
        label = (line[: match.start()].strip() or line[match.end() :].strip() or "Line item").strip(" -")
        records.append(
            {
                "department": "Unknown",
                "category": label[:500] or "Unknown",
                "amount": amount,
            }
        )
    return records if records else [{"department": "Unknown", "category": "Full text (unparsed)", "amount": Decimal("0")}]


def _get_file_hash(bucket: str, key: str) -> str:
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
        return head.get("Metadata", {}).get("file_hash", "")
    except Exception:  # noqa: BLE001
        logger.exception("Unable to read file_hash metadata for s3://%s/%s", bucket, key)
        return ""


def _write_normalized(bucket: str, document_id: str, normalized: dict[str, Any]) -> str:
    out_key = f"extracted/{document_id}.json"
    s3.put_object(
        Bucket=bucket,
        Key=out_key,
        Body=json.dumps(normalized, default=_json_default).encode("utf-8"),
        ContentType="application/json",
    )
    return out_key


def _invoke_ai_analyzer(document_id: str, normalized: dict[str, Any], out_key: str, file_hash: str) -> None:
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
            default=_json_default,
        ).encode("utf-8"),
    )


def _invoke_ai_analyzer_error(document_id: str, source_key: str, file_hash: str, message: str) -> None:
    lambda_client.invoke(
        FunctionName=AI_ANALYZER_NAME,
        InvocationType="Event",
        Payload=json.dumps(
            {
                "document_id": document_id,
                "normalized_data": {"document_id": document_id, "source_s3_key": source_key, "records": []},
                "extracted_s3_key": "",
                "file_hash": file_hash,
                "processing_error": message,
            }
        ).encode("utf-8"),
    )

def _extract_csv_records(raw: bytes) -> list[dict[str, Any]]:
    text = raw.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    rows: list[dict[str, Any]] = []
    if not reader.fieldnames:
        return rows

    # Map common header names to canonical keys.
    field_map = {_norm(h): h for h in reader.fieldnames if h}

    def pick(*candidates: str) -> str | None:
        for c in candidates:
            n = _norm(c)
            if n in field_map:
                return field_map[n]
        for k, orig in field_map.items():
            for c in candidates:
                if c in k:
                    return orig
        return None

    dcol = pick("department", "dept", "division", "unit")
    ccol = pick("category", "line item", "description", "program", "account")
    acol = pick("amount", "budget", "total", "cost", "spend", "value")

    for line in reader:
        dept = (line.get(dcol, "") if dcol else "").strip() or "Unknown"
        cat = (line.get(ccol, "") if ccol else "").strip() or "Unknown"
        raw_amt = ""
        if acol and acol in line:
            raw_amt = line[acol]
        elif not acol:
            # Single-column CSV: treat as category + try last numeric column
            vals = list(line.values())
            raw_amt = vals[-1] if vals else ""
            if dcol is None and ccol is None and len(vals) >= 1:
                cat = str(vals[0]).strip() or cat

        amt = _parse_amount(str(raw_amt))
        if amt is None:
            continue
        rows.append({"department": dept, "category": cat, "amount": amt})

    return rows if rows else [{"department": "Unknown", "category": "Empty or invalid CSV", "amount": Decimal("0")}]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


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
