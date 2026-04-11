import styles from './Dashboard.module.css';

export default function Dashboard({ results, isDuplicate, onReset }) {
  const { alert_summary, human_readable_summary, anomaly_details, document_summary } = results;

  const getSeverityClass = (sev) => {
    if (sev === 'high') return styles.sevHigh;
    if (sev === 'medium') return styles.sevMedium;
    return styles.sevLow;
  };

  return (
    <div className={`glass-panel animate-slide-up ${styles.dashboard}`}>
      <div className={styles.header}>
        <h2>Analysis Complete</h2>
        <button className={styles.resetBtn} onClick={onReset}>Analyze Another</button>
      </div>

      {isDuplicate && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: '0.75rem',
          background: 'rgba(234,179,8,0.1)', border: '1px solid #ca8a04',
          color: '#92400e', padding: '0.875rem 1.25rem', borderRadius: '8px',
          marginBottom: '1.5rem', fontSize: '0.95rem'
        }}>
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
            <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
          </svg>
          <span>
            <strong>Duplicate file detected.</strong> This file was previously analyzed — showing cached results. No AI credits were used.
          </span>
        </div>
      )}

      <div className={styles.summaryGrid}>
        <div className={styles.metricCard}>
          <span className={styles.metricLabel}>Fraud Warnings</span>
          <span className={`${styles.metricValue} ${alert_summary?.fraud > 0 ? styles.textHigh : ''}`}>
            {alert_summary?.fraud || 0}
          </span>
        </div>
        <div className={styles.metricCard}>
          <span className={styles.metricLabel}>Waste Alerts</span>
          <span className={`${styles.metricValue} ${alert_summary?.waste > 0 ? styles.textMedium : ''}`}>
            {alert_summary?.waste || 0}
          </span>
        </div>
        <div className={styles.metricCard}>
          <span className={styles.metricLabel}>Abuse Suspicions</span>
          <span className={`${styles.metricValue} ${alert_summary?.abuse > 0 ? styles.textMedium : ''}`}>
            {alert_summary?.abuse || 0}
          </span>
        </div>
      </div>

      {document_summary && (
        <div className={styles.glassSection}>
          <h3 className={styles.sectionTitle}>Document Summary</h3>
          <p className={styles.summaryText}>{document_summary}</p>
        </div>
      )}

      <div className={styles.glassSection}>
        <h3 className={styles.sectionTitle}>Executive Summary</h3>
        <p className={styles.summaryText}>{human_readable_summary || 'No summary available.'}</p>
      </div>

      <div className={styles.glassSection}>
        <h3 className={styles.sectionTitle}>Detected Anomalies</h3>
        {anomaly_details && anomaly_details.length > 0 ? (
          <div className={styles.anomalyList}>
            {anomaly_details.map((anomaly, idx) => (
              <div key={idx} className={`${styles.anomalyItem} ${getSeverityClass(anomaly.severity?.toLowerCase())}`}>
                <div className={styles.anomalyHeader}>
                  <span className={styles.anomalyType}>{anomaly.type}</span>
                  <span className={styles.anomalyBadge}>{anomaly.severity?.toUpperCase() || 'UNKNOWN'}</span>
                </div>
                <p className={styles.anomalyDesc}>{anomaly.description}</p>
              </div>
            ))}
          </div>
        ) : (
          <div className={styles.allClear}>
            <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>
            <p style={{ marginTop: '1rem' }}>No anomalies detected in this budget payload.</p>
          </div>
        )}
      </div>
    </div>
  );
}
