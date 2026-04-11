export const API_URL = "/api/aws";

// Resolves with { documentId, isDuplicate }
export async function uploadBudgetFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.readAsDataURL(file);
    reader.onload = async () => {
      const base64Data = reader.result.split(',')[1];
      try {
        const response = await fetch(`${API_URL}/upload`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ filename: file.name, file_base64: base64Data })
        });

        const data = await response.json();

        if (!response.ok) {
          throw new Error(data.message || 'Upload failed');
        }

        const isDuplicate = response.status === 200 &&
          typeof data.message === 'string' &&
          data.message.toLowerCase().includes('duplicate');

        resolve({ documentId: data.document_id, isDuplicate });
      } catch (err) {
        reject(err);
      }
    };
    reader.onerror = error => reject(error);
  });
}

export async function pollForResults(documentId, maxAttempts = 40) {
  for (let i = 0; i < maxAttempts; i++) {
    const res = await fetch(`${API_URL}/results?documentId=${documentId}`);
    if (res.ok) {
      return await res.json();
    }
    // If 404, it's still processing
    if (res.status === 404) {
      await new Promise(r => setTimeout(r, 5000)); // wait 5s before polling again
    } else {
      throw new Error(`Failed to fetch results: ${res.status}`);
    }
  }
  throw new Error('AI analysis timed out after 3+ minutes. Please try again.');
}
