export const API_URL = "/api/aws";

export async function uploadBudgetFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.readAsDataURL(file);
    reader.onload = async () => {
      const base64Data = reader.result.split(',')[1];
      try {
        const response = await fetch(`${API_URL}/upload`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            filename: file.name,
            file_base64: base64Data
          })
        });
        
        if (!response.ok) throw new Error('Upload failed');
        const data = await response.json();
        resolve(data.document_id);
      } catch (err) {
        reject(err);
      }
    };
    reader.onerror = error => reject(error);
  });
}

export async function pollForResults(documentId, maxAttempts = 15) {
  for (let i = 0; i < maxAttempts; i++) {
    const res = await fetch(`${API_URL}/results?documentId=${documentId}`);
    if (res.ok) {
      return await res.json();
    }
    // If 404, it's still processing
    if (res.status === 404) {
      await new Promise(r => setTimeout(r, 4000)); // wait 4s before polling again
    } else {
      throw new Error(`Failed to fetch results: ${res.status}`);
    }
  }
  throw new Error('AI analysis timed out');
}
