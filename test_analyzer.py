import json
import base64
import time
import urllib.request
import urllib.error

# The live API Gateway URL we just deployed!
API_URL = "https://kl4vcw3lh0.execute-api.us-east-1.amazonaws.com/prod"

# Let's create a dummy CSV completely full of Fraud, Waste, and Abuse!
csv_content = """department,category,amount,vendor
Transportation,Consulting,900000,Shady LLC
Transportation,Consulting,900000,Shady LLC
Transportation,Travel,500000,Luxury Flights Inc
Health,Office Supplies,4000,Staples
"""

print("1. Creating a dummy budget CSV full of suspicious data...")

payload = {
    "filename": "test_budget.csv",
    "file_base64": base64.b64encode(csv_content.encode()).decode()
}

req = urllib.request.Request(
    f"{API_URL}/upload", 
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"}
)

print("2. Pushing the payload to AWS API Gateway -> Upload Lambda...")
with urllib.request.urlopen(req) as response:
    res = json.loads(response.read())
    doc_id = res["document_id"]
    print(f"   Success! Uploaded with Document ID: {doc_id}")

print("3. Waiting for S3 to trigger Document Processor Lambda -> AI Analyzer Lambda -> DynamoDB...")
# We will poll the results endpoint until DynamoDB has the result
for i in range(10):
    try:
        with urllib.request.urlopen(f"{API_URL}/results?documentId={doc_id}") as r:
            print("\n🚨 AI ANALYSIS COMPLETE. Results from DynamoDB:\n")
            result = json.loads(r.read())
            print(json.dumps(result, indent=2))
            break
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("   Still processing...")
            time.sleep(3)
        else:
            print(f"Error checking status: {e}")
            break
