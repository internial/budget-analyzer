"""
upload_handler — API Gateway POST /upload

Accepts a JSON body with base64 file content (typical for API Gateway + Lambda).
Validates PDF or CSV only, max 10 MB, writes to S3 under uploads/.
S3 then triggers document_processor (configured in Terraform).
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from typing import Any

import boto3
import hashlib
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core.lambda_launcher import patch_all

patch_all()

dynamodb = boto3.client("dynamodb")
s3 = boto3.client("s3")

UPLOAD_BUCKET = os.environ["UPLOAD_BUCKET"]
DYNAMODB_TABLE_NAME = os.environ["DYNAMODB_TABLE_NAME"]

# 10 MB max decoded file size (API Gateway has a 10 MB payload limit too).
MAX_FILE_SIZE_MB = 10
MAX_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

# PDF files start with %PDF; CSV we treat as text after extension/magic checks.
PDF_MAGIC = b"%PDF"


def _get_results_by_hash(file_hash: str) -> dict[str, Any] | None:
    try:
        response = dynamodb.query(
            TableName=DYNAMODB_TABLE_NAME,
            IndexName="file_hash-index",
            KeyConditionExpression="#file_hash = :file_hash",
            ExpressionAttributeNames={
                "#file_hash": "file_hash"
            },
            ExpressionAttributeValues={
                ":file_hash": {"S": file_hash}
            },
            Limit=1
        )
        if response["Items"]:
            item = response["Items"][0]
            return {
                "document_id": item["document_id"]["S"],
                "s3_key": item["s3_key"]["S"],
                "file_hash": item["file_hash"]["S"]
            }
    except Exception as e:
        print(f"Error querying DynamoDB for file_hash {file_hash}: {e}")
    return None


@xray_recorder.capture('lambda_handler')
def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    if event.get("httpMethod") not in (None, "POST"):
        return _response(405, {"message": "Method not allowed"})

    try:
        payload = _parse_body(event)
    except ValueError as e:
        return _response(400, {"message": str(e)})

    filename = (payload.get("filename") or "").strip()
    b64 = payload.get("file_base64") or payload.get("content_base64")
    if not filename or not b64:
        return _response(
            400,
            {"message": "Required: filename and file_base64 (or content_base64)."},
        )

    raw = base64.b64decode(b64, validate=True)
    file_hash = hashlib.sha256(raw).hexdigest()

    existing_results = _get_results_by_hash(file_hash)
    if existing_results:
        return _response(200, {"message": "Duplicate file, returning cached results.", "document_id": existing_results["document_id"], "s3_key": existing_results["s3_key"], "file_hash": file_hash})
    if len(raw) > MAX_BYTES:
        return _response(400, {"message": f"File too large. Maximum size is {MAX_FILE_SIZE_MB} MB."})

    ext = _file_extension(filename)
    if ext not in (".pdf", ".csv"):
        return _response(400, {"message": "Only PDF and CSV files are allowed."})

    if ext == ".pdf" and not raw.startswith(PDF_MAGIC):
        return _response(400, {"message": "File does not look like a valid PDF."})

    if ext == ".csv":
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            return _response(400, {"message": "CSV must be valid UTF-8 text."})

    document_id = str(uuid.uuid4())
    key = f"uploads/{document_id}{ext}"

    extra: dict[str, str] = {}
    if ext == ".pdf":
        extra["ContentType"] = "application/pdf"
    else:
        extra["ContentType"] = "text/csv"

    s3.put_object(Bucket=UPLOAD_BUCKET, Key=key, Body=raw, Metadata={"file_hash": file_hash}, **extra)

    return _response(
        202,
        {
            "document_id": document_id,
            "message": "Upload received and queued for processing.",
            "s3_key": key,
            "file_hash": file_hash,
        },
    )


def _parse_body(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    if isinstance(body, str):
        return json.loads(body)
    return body


def _file_extension(name: str) -> str:
    lower = name.lower()
    if lower.endswith(".pdf"):
        return ".pdf"
    if lower.endswith(".csv"):
        return ".csv"
    return ""


def _response(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }
