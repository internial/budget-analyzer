"""
ai_analyzer — Reads raw document content, runs forensic analysis via Bedrock,
stores results in DynamoDB. Also serves GET /results API.
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

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
bedrock_runtime = boto3.client("bedrock-runtime")

TABLE_NAME = os.environ["TABLE_NAME"]
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]

table = dynamodb.Table(TABLE_NAME)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    # --- API Gateway GET /results ---
    if event.get("httpMethod") == "GET":
        params = event.get("queryStringParameters") or {}
        document_id = params.get("documentId")
        if not document_id:
            return _api_response(400, {"message": "documentId query parameter is required"})
        item = table.get_item(Key={"document_id": document_id}).get("Item")
        if not item:
            return _api_response(404, {"message": "document not found"})
        return _api_response(200, _to_jsonable(item))

    # --- Async pipeline ---
    event = _parse_event(event)
    document_id = event["document_id"]
    extracted = event.get("extracted_data") or {}
    extracted_key = event.get("extracted_s3_key", "")
    file_hash = event.get("file_hash", "")
    processing_error = event.get("processing_error")

    if processing_error:
        report = _error_report(document_id, processing_error)
    else:
        content = extracted.get("content", "").strip()
        file_type = extracted.get("file_type", "unknown")
        truncated = extracted.get("truncated", False)

        if not content:
            report = _error_report(document_id, "No content could be extracted from the document.")
        else:
            report = _analyze(document_id, content, file_type, truncated)

    _save(document_id, extracted_key, file_hash, report)
    return {"statusCode": 200, "body": json.dumps({"document_id": document_id})}


def _analyze(document_id: str, content: str, file_type: str, truncated: bool) -> dict[str, Any]:
    truncation_note = (
        "\nNOTE: This document was truncated due to size. Analyze what is provided.\n"
        if truncated else ""
    )

    if file_type == "csv":
        format_guidance = (
            "The document is a CSV file. Each row is a transaction or budget line item. "
            "The headers describe the columns. Read every row carefully.\n"
        )
    else:
        format_guidance = (
            "The document is a PDF budget report. It may contain tables, paragraphs, "
            "headers, and financial data in various formats. Read it as a human would.\n"
        )

    prompt = f"""You are a senior forensic government budget auditor. Your job is to read the document below and identify every possible instance of fraud, waste, and abuse.

{format_guidance}{truncation_note}
DEFINITIONS:
- FRAUD: Deliberate deception for financial gain. Examples: duplicate payments, fictitious vendors, inflated invoices, payments to non-existent employees, round-number payments suggesting fabricated invoices, payments just below approval thresholds (e.g. $9,999 to avoid $10,000 review), same invoice paid twice, vendor names that are suspiciously similar.
- WASTE: Inefficient or unnecessary use of public funds. Examples: overspending on consulting or travel, redundant services, excessive unit costs compared to market rates, budget categories with no clear purpose, large unexplained variances, spending on luxury items.
- ABUSE: Misuse of authority or resources. Examples: spending outside department mandate, personal expenses charged to public funds, frequent small purchases that appear to split a larger purchase to avoid oversight, contracts awarded without competitive bidding indicators, payments to related parties.

INSTRUCTIONS:
1. Read the ENTIRE document carefully
2. Extract and list every financial figure you find with its context
3. Identify ALL anomalies — do not stop at the first one
4. Be specific: quote exact amounts, vendor names, dates, department names from the document
5. If you see a pattern (e.g. same vendor appears 5 times), flag it
6. If amounts seem unusually high or low for the category, flag it
7. If data is missing (no vendor name, no date, no description), flag it as a transparency concern
8. Assign severity honestly: high = clear red flag, medium = suspicious and needs review, low = minor concern

Return ONLY valid JSON, no markdown, no explanation outside the JSON:
{{
  "document_id": "{document_id}",
  "alert_summary": {{"fraud": <number>, "waste": <number>, "abuse": <number>}},
  "anomaly_details": [
    {{
      "type": "Fraud|Waste|Abuse",
      "severity": "high|medium|low",
      "description": "<specific finding with exact amounts and names from the document>"
    }}
  ],
  "human_readable_summary": "<detailed paragraph summarizing ALL findings, total flagged amount if calculable, and overall risk assessment>"
}}

DOCUMENT CONTENT:
{content}"""

    try:
        response = bedrock_runtime.converse(
            modelId=BEDROCK_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 8192, "temperature": 0},
        )
        raw_text = _extract_text(response)
        logger.info("Bedrock response length: %d chars", len(raw_text))
        return _parse_json(raw_text, document_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Bedrock call failed: %s", exc)
        return _error_report(document_id, f"AI analysis failed: {exc!s}")


def _save(document_id: str, extracted_key: str, file_hash: str, report: dict[str, Any]) -> None:
    report.setdefault("document_id", document_id)
    report.setdefault("alert_summary", {"fraud": 0, "waste": 0, "abuse": 0})
    report.setdefault("anomaly_details", [])
    report.setdefault("human_readable_summary", "No summary produced.")

    table.put_item(Item={
        "document_id": document_id,
        "document_name": extracted_key,
        "upload_date": datetime.now(timezone.utc).isoformat(),
        "alert_summary": {
            k: int(report["alert_summary"].get(k, 0))
            for k in ("fraud", "waste", "abuse")
        },
        "anomaly_details": report["anomaly_details"],
        "human_readable_summary": report["human_readable_summary"],
        "json_report": json.dumps(report, default=str),
        "file_hash": file_hash,
        "status": "complete",
    })


def _error_report(document_id: str, message: str) -> dict[str, Any]:
    return {
        "document_id": document_id,
        "alert_summary": {"fraud": 0, "waste": 0, "abuse": 0},
        "anomaly_details": [{"type": "Error", "severity": "high", "description": message}],
        "human_readable_summary": f"Processing error: {message}",
    }


def _extract_text(response: dict[str, Any]) -> str:
    content = ((response.get("output") or {}).get("message") or {}).get("content") or []
    for block in content:
        if isinstance(block, dict) and "text" in block:
            return block["text"] or ""
    return json.dumps(response)


def _parse_json(text: str, document_id: str) -> dict[str, Any]:
    text = text.strip()
    # Strip markdown fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Find the outermost JSON object
    start = text.find("{")
    end = text.rfind("}") + 1
    if start != -1 and end > start:
        text = text[start:end]
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError as e:
        logger.error("JSON parse failed: %s\nRaw text: %s", e, text[:500])
    return {
        "document_id": document_id,
        "alert_summary": {"fraud": 0, "waste": 0, "abuse": 0},
        "anomaly_details": [{"type": "Error", "severity": "high", "description": "Model returned invalid JSON."}],
        "human_readable_summary": text[:3000] if text else "Empty model response.",
    }


def _to_jsonable(item: dict[str, Any]) -> dict[str, Any]:
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


def _parse_event(event: dict[str, Any]) -> dict[str, Any]:
    if isinstance(event, dict) and "extracted_data" in event:
        return event
    if isinstance(event, str):
        return json.loads(event)
    body = event.get("body")
    if event.get("isBase64Encoded") and body:
        body = base64.b64decode(body).decode()
    if isinstance(body, str):
        return json.loads(body)
    return event
