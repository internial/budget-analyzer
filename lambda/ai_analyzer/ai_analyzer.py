"""
ai_analyzer — Pipeline: Bedrock analyzes normalized budget JSON, writes results to DynamoDB.
API: GET /results?documentId= returns stored analysis (no Bedrock call).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core.lambda_launcher import patch_all

patch_all()

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
bedrock_runtime = boto3.client("bedrock-runtime")

TABLE_NAME = os.environ["TABLE_NAME"]
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]

table = dynamodb.Table(TABLE_NAME)


@xray_recorder.capture('lambda_handler')
def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    # --- API Gateway: fetch prior result ---
    if event.get("httpMethod") == "GET":
        params = event.get("queryStringParameters") or {}
        document_id = params.get("documentId")
        if not document_id:
            return _api_response(400, {"message": "documentId query parameter is required"})
        item = table.get_item(Key={"document_id": document_id}).get("Item")
        if not item:
            return _api_response(404, {"message": "document not found"})
        return _api_response(200, _dynamo_item_to_jsonable(item))

    # --- Async pipeline from document_processor ---
    payload = _parse_pipeline_event(event)
    document_id = payload["document_id"]
    normalized = payload.get("normalized_data") or {}
    extracted_key = payload.get("extracted_s3_key", "")
    file_hash = payload.get("file_hash", "")

    instruction = (
        "You are a government budget analyst reviewing structured budget data. "
        "The input is structured JSON representing budget data with fields like department, category, and amount "
        '(e.g., {"department": "Transportation", "category": "Consulting", "amount": 900000}).\n\n'
        "Detect the following anomalies:\n"
        "- Fraud: duplicate payments, suspicious repeated vendors, inconsistent amounts.\n"
        "- Waste: overspending relative to other categories, unusually high travel or consulting spending.\n"
        "- Abuse: spending outside approved category, spending unrelated to department purpose.\n\n"
        "Determine a severity level (low, medium, high) for each anomaly found.\n\n"
        "Return ONLY valid JSON (no markdown fences) with exactly this deterministic format:\n"
        "{\n"
        '  "document_id": string,\n'
        '  "alert_summary": { "fraud": number, "waste": number, "abuse": number },\n'
        '  "anomaly_details": [ { "type": "Fraud|Waste|Abuse", "severity": "low|medium|high", "description": "string" } ],\n'
        '  "human_readable_summary": "string"\n'
        "}\n"
        "Counts in alert_summary are how many distinct issues you found in each category.\n\n"
        "DATA:\n"
        f"{json.dumps(normalized, default=str)[:95000]}"
    )

    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": [{"type": "text", "text": instruction}]}],
        }
    )

    report: dict[str, Any] = {}
    try:
        br = bedrock_runtime.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=body.encode(),
            contentType="application/json",
            accept="application/json",
        )
        raw = br["body"].read()
        parsed = json.loads(raw)
        text_out = _extract_bedrock_text(parsed)
        report = _parse_model_json(text_out, document_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Bedrock failed: %s", exc)
        report = {
            "document_id": document_id,
            "alert_summary": {"fraud": 0, "waste": 0, "abuse": 0},
            "anomaly_details": [
                {"type": "Error", "severity": "high", "description": f"Analysis failed: {exc!s}"},
            ],
            "human_readable_summary": "Automated analysis could not be completed.",
        }

    report.setdefault("document_id", document_id)
    report.setdefault("alert_summary", {"fraud": 0, "waste": 0, "abuse": 0})
    report.setdefault("anomaly_details", [])
    report.setdefault("human_readable_summary", "No summary produced.")

    now = datetime.now(timezone.utc).isoformat()
    json_report_str = json.dumps(report, default=str)

    item = {
        "document_id": document_id,
        "document_name": extracted_key,
        "upload_date": now,
        "alert_summary": _alert_summary_for_dynamo(report["alert_summary"]),
        "anomaly_details": report["anomaly_details"],
        "human_readable_summary": report["human_readable_summary"],
        "json_report": json_report_str,
        "file_hash": file_hash,
    }

    table.put_item(Item=item)
    return {"statusCode": 200, "body": json.dumps({"document_id": document_id})}


def _alert_summary_for_dynamo(summary: Any) -> dict[str, Any]:
    """DynamoDB maps prefer ints/Decimals for numeric attributes."""
    if not isinstance(summary, dict):
        return {"fraud": 0, "waste": 0, "abuse": 0}
    out: dict[str, Any] = {}
    for k in ("fraud", "waste", "abuse"):
        v = summary.get(k, 0)
        try:
            out[k] = int(v)
        except (TypeError, ValueError):
            out[k] = 0
    return out


def _extract_bedrock_text(parsed: dict[str, Any]) -> str:
    for block in parsed.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            return block.get("text", "") or ""
    return json.dumps(parsed)


def _parse_model_json(text: str, document_id: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return {
        "document_id": document_id,
        "alert_summary": {"fraud": 0, "waste": 0, "abuse": 0},
        "anomaly_details": [{"type": "ParseError", "severity": "high", "description": "Model did not return valid JSON."}],
        "human_readable_summary": text[:2000] if text else "Empty model response.",
    }


def _dynamo_item_to_jsonable(item: dict[str, Any]) -> dict[str, Any]:
    """Convert DynamoDB types (e.g. Decimal) for JSON API response."""

    def conv(v: Any) -> Any:
        if isinstance(v, Decimal):
            return float(v) if v % 1 else int(v)
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        if isinstance(v, list):
            return [conv(x) for x in v]
        return v

    return conv(item)


def _api_response(status_code: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload, default=str),
    }


def _parse_pipeline_event(event: dict[str, Any]) -> dict[str, Any]:
    if isinstance(event, dict) and "normalized_data" in event:
        return event
    if isinstance(event, str):
        return json.loads(event)
    body = event.get("body")
    if event.get("isBase64Encoded") and body:
        body = base64.b64decode(body).decode()
    if isinstance(body, str):
        return json.loads(body)
    return event
