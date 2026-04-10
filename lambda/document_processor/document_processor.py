"""
document_processor — Triggered by S3 when a file lands in uploads/.

PDF: full text extracted with pypdf, sent as-is to ai_analyzer.
CSV: raw text sent as-is to ai_analyzer.
No pre-parsing — the AI reads the raw content directly.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
from typing import Any

import boto3
import pypdf

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
lambda_client = boto3.client("lambda")

UPLOAD_BUCKET = os.environ["UPLOAD_BUCKET"]
AI_ANALYZER_NAME = os.environ["AI_ANALYZER_NAME"]

# Bedrock Nova Lite context window is ~300k tokens (~200k chars safe limit)
MAX_CONTENT_CHARS = 180_000


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    if "Records" not in event:
        logger.warning("Expected S3 event with Records; got: %s", json.dumps(event)[:500])
        return {"statusCode": 400, "body": "{}"}

    for record in event["Records"]:
        if record.get("eventSource") != "aws:s3":
            continue
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"].replace("+", " ")

        if not key.startswith("uploads/"):
            continue

        document_id, ext = _parse_upload_key(key)
        if ext not in (".pdf", ".csv"):
            logger.info("Skipping unsupported file type: %s", key)
            continue

        logger.info("Processing %s (document_id=%s)", key, document_id)
        file_hash = _get_file_hash(bucket, key)

        try:
            obj = s3.get_object(Bucket=bucket, Key=key)
            raw = obj["Body"].read()

            if ext == ".pdf":
                content, file_type = _extract_pdf(raw)
            else:
                content, file_type = _extract_csv(raw)

            payload = {
                "document_id": document_id,
                "source_s3_key": key,
                "file_type": file_type,
                "content": content[:MAX_CONTENT_CHARS],
                "truncated": len(content) > MAX_CONTENT_CHARS,
            }

            out_key = _write_extracted(bucket, document_id, payload)
            _invoke_ai_analyzer(document_id, payload, out_key, file_hash)
            logger.info("Dispatched %s to ai_analyzer (%d chars)", key, len(content))

        except Exception as exc:  # noqa: BLE001
            logger.exception("Processing failed for %s: %s", key, exc)
            _invoke_ai_analyzer_error(document_id, key, file_hash, str(exc))

    return {"statusCode": 200, "body": json.dumps({"ok": True})}


def _extract_pdf(raw: bytes) -> tuple[str, str]:
    reader = pypdf.PdfReader(io.BytesIO(raw))
    pages: list[str] = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"--- Page {i + 1} ---\n{text.strip()}")

    full_text = "\n\n".join(pages)
    if not full_text.strip():
        raise ValueError(
            "PDF contains no extractable text. It may be a scanned image PDF. "
            "Please upload a text-based PDF or a CSV file."
        )
    return full_text, "pdf"


def _extract_csv(raw: bytes) -> tuple[str, str]:
    # Return the raw CSV text — the AI reads it directly
    text = raw.decode("utf-8", errors="replace")
    return text, "csv"


def _parse_upload_key(key: str) -> tuple[str, str]:
    base = key.rsplit("/", 1)[-1]
    if base.lower().endswith(".pdf"):
        return base[:-4], ".pdf"
    if base.lower().endswith(".csv"):
        return base[:-4], ".csv"
    return base, ""


def _get_file_hash(bucket: str, key: str) -> str:
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
        return head.get("Metadata", {}).get("file_hash", "")
    except Exception:  # noqa: BLE001
        logger.exception("Unable to read file_hash for s3://%s/%s", bucket, key)
        return ""


def _write_extracted(bucket: str, document_id: str, payload: dict[str, Any]) -> str:
    out_key = f"extracted/{document_id}.json"
    s3.put_object(
        Bucket=bucket,
        Key=out_key,
        Body=json.dumps(payload).encode("utf-8"),
        ContentType="application/json",
    )
    return out_key


def _invoke_ai_analyzer(document_id: str, payload: dict[str, Any], out_key: str, file_hash: str) -> None:
    lambda_client.invoke(
        FunctionName=AI_ANALYZER_NAME,
        InvocationType="Event",
        Payload=json.dumps({
            "document_id": document_id,
            "extracted_data": payload,
            "extracted_s3_key": out_key,
            "file_hash": file_hash,
        }).encode("utf-8"),
    )


def _invoke_ai_analyzer_error(document_id: str, source_key: str, file_hash: str, message: str) -> None:
    lambda_client.invoke(
        FunctionName=AI_ANALYZER_NAME,
        InvocationType="Event",
        Payload=json.dumps({
            "document_id": document_id,
            "extracted_data": {"document_id": document_id, "content": "", "file_type": "unknown"},
            "extracted_s3_key": "",
            "file_hash": file_hash,
            "processing_error": message,
        }).encode("utf-8"),
    )
