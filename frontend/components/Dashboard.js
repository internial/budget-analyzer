import styles from './Dashboard.module.css';

export default function Dashboard({ results, onReset }) {
  const { alert_summary, human_readable_summary, anomaly_details } = results;

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
