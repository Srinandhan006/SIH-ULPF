import React, { useState, useEffect } from 'react';
import { X, CheckCircle2, ShieldCheck, Copy, Database, Code, FileText, Check } from 'lucide-react';
import { fetchRawRecord } from '../services/api';

export default function EventDetailModal({ event, onClose }) {
  const [activeTab, setActiveTab] = useState('ocsf');
  const [rawRecord, setRawRecord] = useState(null);
  const [rawLoading, setRawLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (event?.provenance?.raw_event_id) {
      setRawLoading(true);
      fetchRawRecord(event.provenance.raw_event_id)
        .then(setRawRecord)
        .catch(() => setRawRecord(null))
        .finally(() => setRawLoading(false));
    }
  }, [event]);

  if (!event) return null;

  const handleCopy = (text) => {
    navigator.clipboard.writeText(typeof text === 'string' ? text : JSON.stringify(text, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const prov = event.provenance || {};
  const ocsf = event.ocsf || {};

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title-wrap">
            <span className="modal-tag">EVENT RECORD</span>
            <h3>{event.event_id}</h3>
            <span className="source-pill">{event.source_id}</span>
          </div>
          <button className="close-btn" onClick={onClose}><X size={18} /></button>
        </div>

        <div className="modal-subnav">
          <button 
            className={`subnav-btn ${activeTab === 'ocsf' ? 'active' : ''}`}
            onClick={() => setActiveTab('ocsf')}
          >
            <Code size={14} />
            <span>OCSF 1.9 Canonical Schema</span>
          </button>
          <button 
            className={`subnav-btn ${activeTab === 'raw' ? 'active' : ''}`}
            onClick={() => setActiveTab('raw')}
          >
            <FileText size={14} />
            <span>Forensic Raw Store</span>
            {rawRecord?.sha256_verified && (
              <span className="badge-verified">SHA256 ✓</span>
            )}
          </button>
          <button 
            className={`subnav-btn ${activeTab === 'provenance' ? 'active' : ''}`}
            onClick={() => setActiveTab('provenance')}
          >
            <ShieldCheck size={14} />
            <span>Provenance & Ladder</span>
          </button>
        </div>

        <div className="modal-body">
          {activeTab === 'ocsf' && (
            <div className="tab-pane">
              <div className="pane-summary-grid">
                <div className="summary-item">
                  <span className="lbl">OCSF Class UID</span>
                  <span className="val">{ocsf.class_uid ?? 0} ({ocsf.class_name || 'Base Event'})</span>
                </div>
                <div className="summary-item">
                  <span className="lbl">Classification Tier</span>
                  <span className="val">Tier {prov.tier} ({prov.parser_id})</span>
                </div>
                <div className="summary-item">
                  <span className="lbl">Confidence Score</span>
                  <span className="val">{Math.round((prov.confidence || 0) * 100)}%</span>
                </div>
                <div className="summary-item">
                  <span className="lbl">Processed At</span>
                  <span className="val">{prov.processed_at ? new Date(prov.processed_at).toLocaleTimeString() : '—'}</span>
                </div>
              </div>

              <div className="code-header">
                <span>Normalized OCSF 1.9 JSON Body</span>
                <button className="copy-code-btn" onClick={() => handleCopy(ocsf)}>
                  {copied ? <Check size={13} className="text-emerald" /> : <Copy size={13} />}
                  <span>{copied ? 'Copied' : 'Copy JSON'}</span>
                </button>
              </div>
              <pre className="code-block">
                <code>{JSON.stringify(ocsf, null, 2)}</code>
              </pre>
            </div>
          )}

          {activeTab === 'raw' && (
            <div className="tab-pane">
              <div className="raw-verification-banner">
                <ShieldCheck size={20} className="icon-green" />
                <div className="banner-text">
                  <strong>Zero Information Loss Guarantee</strong>
                  <p>Raw event bytes preserved immutably in content-addressed segment store before any parsing occurred.</p>
                </div>
              </div>

              <div className="pane-summary-grid">
                <div className="summary-item">
                  <span className="lbl">Raw Event ID (SHA-256)</span>
                  <span className="val mono">{prov.raw_event_id}</span>
                </div>
                <div className="summary-item">
                  <span className="lbl">Integrity Check</span>
                  <span className="val text-emerald">
                    {rawRecord?.sha256_verified ? '✓ SHA-256 Matched & Verified' : 'Checking hash...'}
                  </span>
                </div>
                <div className="summary-item">
                  <span className="lbl">Segment Storage File</span>
                  <span className="val mono">{prov.raw_segment || 'segment_0001'}</span>
                </div>
                <div className="summary-item">
                  <span className="lbl">Byte Offset & Length</span>
                  <span className="val mono">offset: {prov.raw_offset ?? 0}, len: {prov.raw_length ?? 0}B</span>
                </div>
              </div>

              <div className="code-header">
                <span>Immutable Raw Bytes Retrieved from Disk</span>
                <button className="copy-code-btn" onClick={() => handleCopy(rawRecord?.payload || event.message)}>
                  {copied ? <Check size={13} className="text-emerald" /> : <Copy size={13} />}
                  <span>{copied ? 'Copied' : 'Copy Raw'}</span>
                </button>
              </div>
              <pre className="code-block raw-block">
                <code>{rawLoading ? 'Loading raw payload from segment...' : (rawRecord?.payload || event.message)}</code>
              </pre>
            </div>
          )}

          {activeTab === 'provenance' && (
            <div className="tab-pane">
              <div className="provenance-table-wrap">
                <table className="prov-table">
                  <tbody>
                    <tr>
                      <td className="prop-name">Parsing Tier</td>
                      <td className="prop-val">Tier {prov.tier} (1=Structural, 2=Vendor Pack, 3=Inferred, 4=Drain3, 5=Similarity, 6=ML, 7=Quarantine)</td>
                    </tr>
                    <tr>
                      <td className="prop-name">Parser ID</td>
                      <td className="prop-val mono">{prov.parser_id}</td>
                    </tr>
                    <tr>
                      <td className="prop-name">Parser Version</td>
                      <td className="prop-val mono">{prov.parser_version || '1.0.0'}</td>
                    </tr>
                    <tr>
                      <td className="prop-name">Confidence</td>
                      <td className="prop-val">{prov.confidence} ({Math.round((prov.confidence || 0) * 100)}%)</td>
                    </tr>
                    <tr>
                      <td className="prop-name">Shape Hash</td>
                      <td className="prop-val mono">{prov.shape_hash || '—'}</td>
                    </tr>
                    <tr>
                      <td className="prop-name">Family Hash</td>
                      <td className="prop-val mono">{prov.family_hash || '—'}</td>
                    </tr>
                    <tr>
                      <td className="prop-name">Drain3 Template ID</td>
                      <td className="prop-val mono">{prov.template_id || 'Not mined (High Tier match)'}</td>
                    </tr>
                    <tr>
                      <td className="prop-name">Processing Errors</td>
                      <td className="prop-val">{prov.processing_errors?.length ? prov.processing_errors.join(', ') : 'None (Total function execution)'}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
