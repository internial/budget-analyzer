# Gov't Budget Auditor

An AI-powered web application that automatically detects fraud, waste, and abuse in government budget documents. Upload a PDF or CSV — the AI reads every line item and flags suspicious spending based on federal auditing standards used by the GAO and Inspector General offices.

---

## Architecture Diagram

```
User (Browser)
     │
     │  Upload PDF/CSV
     ▼
Next.js Frontend (localhost)
     │
     │  POST /upload
     ▼
API Gateway (REST API)
     │
     ├──► upload_handler Lambda
     │         │
     │         ├── Validates file (PDF/CSV, max 10MB)
     │         ├── Computes SHA-256 hash (duplicate detection)
     │         ├── Checks DynamoDB for existing hash
     │         │     └── If duplicate → return cached document_id
     │         ├── Stores file in S3 (uploads/)
     │         └── Writes "pending" record to DynamoDB
     │
     │  S3 ObjectCreated event
     ▼
document_processor Lambda
     │
     ├── PDF → extracts text page by page (pypdf)
     ├── CSV → smart samples rows if file is too large
     │         (first 300 + ~400 random middle + last 300)
     └── Invokes ai_analyzer Lambda (async)
               │
               ▼
         ai_analyzer Lambda
               │
               ├── Sends text to Amazon Bedrock (Nova Lite)
               │     └── Prompt: forensic gov't auditor
               │           detects fraud, waste, abuse
               ├── Parses AI JSON response
               └── Saves results to DynamoDB (status: complete)

     │
     │  GET /results?documentId=...  (polls every 5s)
     ▼
API Gateway → ai_analyzer Lambda → DynamoDB → returns results
     │
     ▼
Frontend displays:
  - Document Summary
  - Fraud / Waste / Abuse counts
  - Executive Summary
  - Detected Anomalies (with severity)
```

---

## AWS Services Used

| Service | Why |
|---|---|
| **API Gateway** | Exposes two REST endpoints: `POST /upload` and `GET /results`. Acts as the front door to the backend. |
| **Lambda (x3)** | Serverless functions — no server to manage, pay only when running. Three separate functions keep responsibilities isolated. |
| **S3** | Stores uploaded files. Also acts as the trigger — when a file lands in S3, it automatically wakes up the document processor. |
| **DynamoDB** | Stores analysis results. Fast key-value lookups by document ID. Also has a secondary index on file hash for duplicate detection. |
| **Amazon Bedrock** | Managed AI service. Uses Amazon Nova Lite model to analyze document content and return structured fraud/waste/abuse findings. |
| **CloudWatch** | Logs from all three Lambda functions. Alarms fire if any Lambda has errors. Dashboard shows system health at a glance. |
| **SNS** | Sends email alerts when CloudWatch alarms trigger or AWS budget thresholds are crossed. |
| **CloudTrail** | Audit log of all AWS API calls — who did what and when. Single-region, stored in a dedicated S3 bucket. |
| **SQS (Dead Letter Queue)** | Catches failed Lambda invocations so nothing is silently lost. |
| **AWS Budgets** | Alerts at 50%, 80%, and 100% of the $5/month budget cap. |
| **Terraform** | All AWS infrastructure defined as code. One command deploys everything. |

---

## Backend Tech Stack

| Technology | Role |
|---|---|
| Python 3.11 | All three Lambda functions |
| pypdf | Extracts text from PDF files |
| boto3 | AWS SDK — talks to S3, DynamoDB, Bedrock, Lambda |
| Amazon Bedrock (Nova Lite) | AI model for fraud/waste/abuse analysis |
| Terraform | Infrastructure as code |
| GitHub Actions | CI/CD — push to main → auto deploy |

---

## Backend Flow (Step by Step)

1. User selects a PDF or CSV on the website and clicks upload
2. Frontend reads the file, encodes it as base64, sends it to `POST /upload` via API Gateway
3. `upload_handler` Lambda receives the file, computes a SHA-256 hash
4. It checks DynamoDB — if this exact file was uploaded before, it returns the cached result immediately (no AI cost)
5. If new, it saves the file to S3 under `uploads/` and writes a `pending` record to DynamoDB
6. S3 automatically triggers `document_processor` Lambda
7. `document_processor` extracts text from the file:
   - PDF: reads each page using pypdf, skips image-only pages
   - CSV: if small, sends everything; if large, takes a smart sample (first 300 rows + ~400 random middle rows + last 300 rows)
8. It invokes `ai_analyzer` Lambda asynchronously, passing the text directly in the payload
9. `ai_analyzer` sends the text to Amazon Bedrock with a detailed forensic auditor prompt
10. Bedrock returns a structured JSON with fraud/waste/abuse findings
11. `ai_analyzer` saves the results to DynamoDB with `status: complete`
12. Meanwhile, the frontend polls `GET /results` every 5 seconds
13. Once DynamoDB shows `status: complete`, the frontend displays the full report

---

## Limitations

**File size**
- Maximum upload size is 10 MB (API Gateway hard limit)
- The AI can only read ~180,000 characters at once
- Large PDFs (200+ pages) will only have the first portion analyzed — the user is warned
- Large CSVs use smart row sampling — not every row is guaranteed to be analyzed

**PDF extraction**
- Only works on text-based PDFs — scanned documents (images of text) cannot be read
- Images, charts, and graphs inside PDFs are ignored — only text is extracted

**AI accuracy**
- The AI may over-flag legitimate spending (false positives) — it is intentionally aggressive
- The AI may miss fraud that requires cross-referencing external data (e.g., verifying a vendor actually exists)
- Analysis quality depends on how much useful text is in the document

**Performance**
- Analysis takes 30–90 seconds depending on document size
- Very large documents may approach the 300-second Lambda timeout

**No authentication**
- The API has no login or user accounts — anyone with the API URL can upload files
- Suitable for personal/demo use, not production public deployment

---

## Key Files

```
.
├── lambda/
│   ├── upload_handler/upload_handler.py     # Validates uploads, deduplication, S3 + DynamoDB write
│   ├── document_processor/document_processor.py  # PDF/CSV text extraction, smart CSV sampling
│   └── ai_analyzer/ai_analyzer.py           # Bedrock AI call, fraud detection prompt, saves results
│
├── frontend/
│   ├── app/page.js                          # Main page — upload, processing, results states
│   ├── app/layout.js                        # HTML shell, page title
│   ├── components/Uploader.js               # Drag-and-drop file upload component
│   ├── components/Dashboard.js              # Results display — summary, anomalies, warnings
│   └── lib/api.js                           # API calls — upload and polling logic
│
├── terraform/
│   ├── main.tf                              # All AWS infrastructure (Lambda, S3, DynamoDB, API Gateway, etc.)
│   ├── variables.tf                         # Configurable settings (region, budget, model ID)
│   └── outputs.tf                           # Useful values after deploy (API URL, bucket names)
│
└── .github/workflows/
    ├── deploy.yml                           # Push to main → build pypdf layer → terraform apply
    └── ci.yml                               # PR checks — lint, build, Python compile, Terraform validate
```

---

## Running Locally

**Prerequisites:** AWS CLI configured, Node.js 20, Python 3.11, Terraform

**1. Deploy the backend**
```bash
cd terraform
terraform init
terraform apply
```

**2. Get the API URL**
```bash
terraform output api_gateway_endpoint
```

**3. Configure the frontend**

Create `frontend/.env.local`:
```
AWS_API_URL=https://your-api-id.execute-api.us-east-1.amazonaws.com/prod
```

**4. Run the frontend**
```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:3000

---

## Cost

Running this project costs approximately **$0.01–$0.05/month** at low usage. The only meaningful cost is Amazon Bedrock — roughly $0.0001 per document analyzed. AWS Budget alerts are configured at $5/month.

---

## CI/CD

Every push to `main` triggers GitHub Actions:
- Lints and builds the frontend
- Compiles all Python Lambda code
- Validates Terraform configuration
- Builds the pypdf Lambda layer (cached by version)
- Runs `terraform apply` to deploy all changes to AWS
