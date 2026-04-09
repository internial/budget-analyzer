'use client';

import { useState } from 'react';
import Uploader from '../components/Uploader';
import Dashboard from '../components/Dashboard';

export default function Home() {
  const [appState, setAppState] = useState('upload'); // 'upload', 'processing', 'results'
  const [fileName, setFileName] = useState('');
  const [results, setResults] = useState(null);
  const [errorMsg, setErrorMsg] = useState(null);

  const startUpload = (name) => {
    setFileName(name);
    setAppState('processing');
    setErrorMsg(null);
  };

  const uploadComplete = (data) => {
    setResults(data);
    setAppState('results');
  };

  const uploadError = (msg) => {
    setErrorMsg(msg);
    setAppState('upload');
  };

  return (
    <main style={{ minHeight: '100vh', padding: '4rem 2rem', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
      <div style={{ textAlign: 'center', marginBottom: '3rem' }}>
        <h1 className="title">Budget Analyzer</h1>
        <p className="subtitle">
          Upload your budget exports to continuously audit transactions for Fraud, Waste, and Abuse using AI.
        </p>
      </div>

      <div style={{ width: '100%', maxWidth: '900px' }}>
        {errorMsg && (
          <div style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid #ef4444', color: '#b91c1c', padding: '1rem', borderRadius: '8px', marginBottom: '1.5rem', textAlign: 'center' }}>
            {errorMsg}
          </div>
        )}

        {appState === 'upload' && (
          <div className="animate-slide-up">
            <Uploader 
              onUploadStart={startUpload} 
              onUploadComplete={uploadComplete} 
              onError={uploadError} 
            />
          </div>
        )}

        {appState === 'processing' && (
          <div className="glass-panel" style={{ padding: '4rem 2rem', textAlign: 'center' }}>
            <div className="spinner" style={{ 
              width: '50px', height: '50px', border: '3px solid rgba(0,0,0,0.1)', 
              borderTopColor: '#3b82f6', borderRadius: '50%', margin: '0 auto 2rem auto',
              animation: 'spin 1s linear infinite'
            }}></div>
            <style jsx>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
            <h3 style={{ fontSize: '1.5rem', margin: '0 0 0.5rem 0' }}>Analyzing &quot;{fileName}&quot;...</h3>
            <p style={{ color: 'var(--text-muted)' }}>The AI is currently structuring the payload and detecting anomalies.</p>
          </div>
        )}

        {appState === 'results' && results && (
          <Dashboard results={results} onReset={() => setAppState('upload')} />
        )}
      </div>
    </main>
  );
}
