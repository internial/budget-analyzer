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

        if ext == ".pdf":
            job_id = _extract_pdf_records(bucket, key)
            logger.info("Textract job started for document_id %s with job_id %s", document_id, job_id)
            continue
        
        obj = s3.get_object(Bucket=bucket, Key=key)
        body = obj["Body"].read()

        records = _extract_csv_records(body)

        normalized = {
            "document_id": document_id,
            "source_s3_key": key,
            "records": records,
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
                },
                default=_json_default
            ).encode("utf-8"),
        )

    return {"statusCode": 200, "body": json.dumps({"ok": True})}


def _parse_upload_key(key: str) -> tuple[str, str]:
    # uploads/{uuid}.pdf or uploads/{uuid}.csv
    base = key.rsplit("/", 1)[-1]
    if base.lower().endswith(".pdf"):
        return base[:-4], ".pdf"
    if base.lower().endswith(".csv"):
        return base[:-4], ".csv"
    return base, ""


def _extract_pdf_records(bucket: str, key: str) -> str:
    """Starts an asynchronous Textract job for PDF documents."""
    logger.info("Starting Textract document analysis for s3://%s/%s", bucket, key)
    response = textract.start_document_analysis(
        DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}},
        FeatureTypes=["TABLES", "FORMS"],  # Request both tables and forms
        NotificationChannel={
            "SNSTopicArn": TEXTRACT_SNS_TOPIC_ARN,
            "RoleArn": os.environ["TEXTRACT_SERVICE_ROLE_ARN"],  # Need to define this in Terraform
        },
        JobTag=key,  # Use S3 key as JobTag for correlation
    )
    job_id = response["JobId"]
    logger.info("Started Textract job with ID: %s for s3://%s/%s", job_id, bucket, key)
    return job_id





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
