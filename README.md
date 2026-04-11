# Gov't Budget Auditor

A web app that lets anyone upload a government budget document and uses AI to automatically find fraud, waste, and abuse. Upload a file, the AI reads it like a skeptical government auditor, and you get a detailed report — analysis typically takes 1–3 minutes depending on file size.

---

## The Problem it Solves

Government budget documents are public — but they're hundreds of pages long and nobody reads them. Auditors cost money and take weeks. This tool scans an entire budget document and surfaces exactly what a human auditor would flag.

---

## How it Works (Plain English)

1. You go to the website and drag in a PDF or CSV
2. The file goes to AWS (Amazon's cloud)
3. Text is extracted from every page
4. That text is sent to an AI with a very specific prompt: *"You are a highly skeptical government auditor. Find fraud, waste, and abuse."*
5. The AI returns a structured report
6. You see the results on screen — what the document is about, how many issues were found, and every suspicious item with a severity rating

---

## AWS Architecture

![Architecture](docs/architecture.png)

![Observability](docs/observability.png)



---

## AWS Services and Why Each One

| Service | What it does in this project |
|---|---|
| **API Gateway** | The front door — exposes `POST /upload` and `GET /results` to the browser |
| **Lambda (×3)** | Serverless functions — no server to manage, pay only when running |
| **S3** | Stores uploaded files AND automatically triggers processing when a file arrives |
| **DynamoDB** | Stores analysis results, indexed by file hash for instant duplicate detection |
| **Amazon Bedrock** | The AI — Nova Lite model reads the document and returns fraud findings |
| **CloudWatch** | Logs everything, fires alarms if any Lambda has errors |
| **SNS** | Sends email alerts when alarms trigger or budget is exceeded |
| **CloudTrail** | Audit log of every AWS action — who did what and when |
| **SQS Dead Letter Queue** | Catches failed Lambda invocations so nothing is silently lost |
| **AWS Budgets** | Alerts at 50%, 80%, 100% of the $5/month cap |
| **Terraform** | All infrastructure defined as code — one command deploys everything |
| **GitHub Actions** | Push to main → automatically deploys to AWS |

---

## The Three Lambda Functions

**upload_handler** — The gatekeeper
- Validates the file is PDF or CSV, under 10MB
- Computes a SHA-256 fingerprint of the file
- Checks if this exact file was analyzed before — if yes, returns cached results (saves AI cost)
- Saves the file to S3 and writes a "pending" record to DynamoDB

**document_processor** — The reader
- Triggered automatically when a file lands in S3
- PDF: uses pypdf to extract text from every page, skips image-only pages
- CSV: if small, sends everything; if large, takes a smart sample (first 300 rows + ~400 random middle rows + last 300 rows)
- Passes the text to the AI analyzer

**ai_analyzer** — The auditor
- Sends the text to Amazon Bedrock with a detailed forensic auditor prompt
- The prompt defines fraud, waste, and abuse using real GAO and Inspector General standards
- Returns a document summary, fraud/waste/abuse counts, and every suspicious item with severity
- Saves everything to DynamoDB

---

## The AI Prompt

The AI is told it is a "highly skeptical forensic government auditor with 20 years of experience." It is given:
- Exact definitions of fraud, waste, and abuse based on federal standards
- 15 specific red flags to always check (round numbers, threshold gaming, vague descriptions, etc.)
- Instructions to flag everything — it is better to over-flag than miss real fraud
- A mandate that "no anomalies" is only acceptable if the document has fewer than 3 transactions

---

## Limitations

- **10 MB max** file size (API Gateway hard limit)
- **Large PDFs**: text is capped at 180,000 characters — for dense documents this may cover only the first portion. User is warned when this happens.
- **Large CSVs**: smart sampling — first 300 rows, ~400 random middle rows, last 300 rows. Rows outside the sample are not analyzed.
- **Scanned PDFs**: if the PDF is a photo of a document, text cannot be extracted
- **No login**: anyone with the URL can upload — demo use only
- **AI can be wrong**: it may flag legitimate spending (false positives) or miss fraud that requires external data to verify
- **Analysis time**: 1–3 minutes for most files, up to 5 minutes for very large ones

---

## Run Locally

**Prerequisites:** AWS CLI configured, Node.js 20

The backend deploys automatically to AWS via GitHub Actions on every push to `main`. To run the frontend locally:

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000

The frontend talks to the live AWS backend via API Gateway.

---

## Cost

About **$0.01–$0.05/month** at low usage. Bedrock charges per token processed — exact cost per document varies by file size. All other services are within AWS free tier at this usage level.
