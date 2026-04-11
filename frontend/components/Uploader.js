'use client';
import { useState, useRef } from 'react';
import styles from './Uploader.module.css';

export default function Uploader({ onUploadStart, onUploadComplete, onError }) {
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef(null);

  const handleDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setIsDragging(true);
    } else if (e.type === "dragleave") {
      setIsDragging(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFile(e.dataTransfer.files[0]);
    }
  };

  const handleChange = (e) => {
    e.preventDefault();
    if (e.target.files && e.target.files[0]) {
      handleFile(e.target.files[0]);
    }
  };

  const handleFile = async (file) => {
    if (!file.name.endsWith('.csv') && !file.name.endsWith('.pdf')) {
      onError("Please upload a .csv or .pdf file.");
      return;
    }

    onUploadStart(file.name);

    try {
      const { uploadBudgetFile, pollForResults } = await import('../lib/api');
      const { documentId, isDuplicate } = await uploadBudgetFile(file);
      if (isDuplicate) onUploadStart(file.name, true); // signal duplicate to show correct spinner text
      const results = await pollForResults(documentId);
      onUploadComplete(results, isDuplicate);
    } catch (err) {
      onError(err.message || "Something went wrong during analysis.");
    }
  };

  return (
    <div 
      className={`glass-panel ${styles.uploader} ${isDragging ? styles.dragActive : ''}`}
      onDragEnter={handleDrag}
      onDragLeave={handleDrag}
      onDragOver={handleDrag}
      onDrop={handleDrop}
      onClick={() => fileInputRef.current.click()}
    >
      <input 
        ref={fileInputRef}
        type="file" 
        accept=".csv,.pdf" 
        className={styles.fileInput} 
        onChange={handleChange} 
      />
      <div className={styles.iconWrapper}>
        <svg xmlns="http://www.w3.org/2000/svg" width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
          <polyline points="17 8 12 3 7 8"></polyline>
          <line x1="12" y1="3" x2="12" y2="15"></line>
        </svg>
      </div>
      <h3>Drag & Drop your Budget File</h3>
      <p>Supports .CSV and .PDF formats</p>
      <button className={styles.browseBtn}>Browse Files</button>
    </div>
  );
}
