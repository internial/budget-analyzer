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

    prompt = f"""You are a HIGHLY SKEPTICAL forensic government budget auditor with 20 years of experience catching fraud. Your reputation depends on finding problems. Assume government spending is wasteful until proven otherwise.

{format_guidance}{truncation_note}
CRITICAL MANDATE: You MUST find and flag suspicious patterns. "No anomalies" is only acceptable if the document is completely empty or contains fewer than 3 transactions.

DEFINITIONS (based on GAO and federal Inspector General standards):

FRAUD — Intentional deception to obtain money or property unlawfully:
- Duplicate payments: same invoice, vendor, or amount paid more than once
- Ghost employees or vendors: payments to people/companies that don't exist or did no work
- Inflated invoices: charges significantly above market rate for goods/services
- Bid rigging: contracts awarded without competition or to pre-selected vendors
- Misrepresentation: falsely labeling personal expenses as official business
- Kickbacks: payments to vendors who then return money to the approving official
- Identity fraud: using another person's name or entity to receive payments
- Timesheet fraud: billing for hours not worked
- Round-number payments ($5,000, $10,000, $25,000) with no itemization — fabricated amounts
- Amounts just below approval thresholds ($9,999, $24,999, $49,999) — deliberate threshold avoidance

WASTE — Spending that provides no reasonable public benefit or is grossly inefficient:
- Consulting and advisory contracts with vague deliverables — one of the most common forms of government waste
- Redundant contracts: paying two vendors for the same service
- Unused or underused services (software licenses, subscriptions, equipment)
- Excessive travel: first-class flights, luxury hotels, per diems above GSA rates
- Conference and event spending: registration fees, catering, team retreats
- Printing and mailing costs when digital alternatives exist
- Overtime abuse: excessive overtime without justification
- Equipment purchases near fiscal year-end (use-it-or-lose-it spending)
- Paying full price when government discount rates exist (GSA schedules)
- Maintenance contracts on equipment that should be replaced

ABUSE — Misuse of position, resources, or authority (may be legal but unethical):
- No-bid or sole-source contracts without documented justification
- Contracts awarded to politically connected vendors
- Spending outside the department's legal mandate or appropriation
- Personal use of government resources (vehicles, phones, credit cards)
- Nepotism: contracts or jobs given to family/friends
- Retaliation spending: budget cuts targeting whistleblowers or critics
- Micro-purchases split to avoid competitive bidding thresholds
- Lack of documentation or missing receipts for expenditures
- Payments to related parties (vendor owned by employee's family member)
- Excessive administrative overhead compared to program spending

RED FLAGS TO ALWAYS CHECK:
1. Round numbers ($10,000, $5,000, $1,000) — often fabricated, real invoices have cents
2. Amounts just below thresholds ($9,999 / $24,999 / $49,999 / $99,999) — threshold gaming
3. Same vendor appearing multiple times — potential kickback or favoritism
4. Similar vendor names (ABC Corp vs ABC Company vs ABC Inc) — shell companies
5. Missing data (no vendor, no date, no description, no invoice number) — transparency violation
6. Consulting/advisory fees — almost always wasteful, flag every single one
7. Travel expenses over $500/day or $2,000/trip — excessive
8. Vague descriptions ("services rendered", "supplies", "miscellaneous", "other") — hiding true purpose
9. Weekend, holiday, or after-hours transaction dates — suspicious timing
10. Payments to individuals (not registered companies) — potential fraud
11. New vendors receiving large first contracts — no track record, possible fraud
12. Contracts with no end date or deliverable — open-ended waste
13. Year-end spending spikes (September for federal, June for many states) — use-it-or-lose-it abuse
14. Single-source IT contracts — almost always overpriced
15. Any "emergency" or "urgent" procurement — often used to bypass oversight

INSTRUCTIONS:
1. Examine EVERY line item, transaction, and budget category with suspicion
2. Flag ANYTHING that seems even slightly unusual — it is better to over-flag than miss real fraud
3. Every consulting fee must be flagged as potential waste
4. Every travel expense must be scrutinized
5. Missing vendor names = HIGH severity fraud
6. Round numbers = MEDIUM severity fraud at minimum
7. Be specific: quote exact amounts, vendor names, dates, line numbers
8. If you find fewer than 3 issues in a document with 10+ transactions, you are not looking hard enough
9. Consider the TOTAL flagged amount and express it as a percentage of the budget if possible
10. Note patterns across multiple line items (same vendor, same amount, same approver)

SEVERITY GUIDELINES:
- HIGH: Clear fraud indicators, missing critical data, duplicate payments, threshold gaming, ghost vendors
- MEDIUM: Wasteful consulting/travel, vague descriptions, round numbers, no-bid contracts
- LOW: Minor transparency issues, slightly elevated costs, missing minor documentation

Return ONLY valid JSON, no markdown:
{{
  "document_id": "{document_id}",
  "document_summary": "<2-paragraph plain-English summary of what this document is: what agency/department, what time period, total budget size, main spending categories, and any notable context. Write this as if briefing a congressional oversight committee.>",
  "alert_summary": {{"fraud": <number>, "waste": <number>, "abuse": <number>}},
  "anomaly_details": [
    {{
      "type": "Fraud|Waste|Abuse",
      "severity": "high|medium|low",
      "description": "<specific finding with exact amounts, vendor names, dates, and why it's suspicious>"
    }}
  ],
  "human_readable_summary": "<aggressive summary highlighting ALL concerns, total flagged amount, and overall risk level>"
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
    report.setdefault("document_summary", "")
    report.setdefault("alert_summary", {"fraud": 0, "waste": 0, "abuse": 0})
    report.setdefault("anomaly_details", [])
    report.setdefault("human_readable_summary", "No summary produced.")

    table.put_item(Item={
        "document_id": document_id,
        "document_name": extracted_key,
        "upload_date": datetime.now(timezone.utc).isoformat(),
        "document_summary": report["document_summary"],
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
