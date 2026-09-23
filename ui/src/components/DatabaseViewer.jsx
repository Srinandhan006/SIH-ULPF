import React, { useState } from 'react';
import { Search, Filter, Eye, Hash, ArrowUpRight, Database } from 'lucide-react';

const TIER_COLORS = {
  1: { bg: 'rgba(59, 130, 246, 0.15)', text: '#60a5fa', border: '#1e40af', label: 'T1 Structural' },
  2: { bg: 'rgba(16, 185, 129, 0.15)', text: '#34d399', border: '#065f46', label: 'T2 Vendor Pack' },
  3: { bg: 'rgba(6, 182, 212, 0.15)', text: '#22d3ee', border: '#155e75', label: 'T3 Inferred' },
  4: { bg: 'rgba(245, 158, 11, 0.15)', text: '#fbbf24', border: '#92400e', label: 'T4 Drain3 Mined' },
  5: { bg: 'rgba(99, 102, 241, 0.15)', text: '#818cf8', border: '#3730a3', label: 'T5 Similarity' },
  6: { bg: 'rgba(168, 85, 247, 0.15)', text: '#c084fc', border: '#6b21a8', label: 'T6 ML Scorer' },
  7: { bg: 'rgba(100, 116, 139, 0.15)', text: '#94a3b8', border: '#334155', label: 'T7 Quarantine' },
};

export default function DatabaseViewer({ events, loading, onSelectEvent, selectedTier, setSelectedTier, searchQuery, setSearchQuery }) {
  const [copiedId, setCopiedId] = useState(null);

  const handleCopy = (e, text, id) => {
    e.stopPropagation();
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 1500);
  };

  return (
    <div className="section-container">
      <div className="table-controls">
        <div className="search-bar">
          <Search size={15} className="search-icon" />
          <input 
            type="text" 
            placeholder="Search parsed logs, raw hashes, IPs, or parser IDs..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          {searchQuery && (
            <button className="clear-search" onClick={() => setSearchQuery('')}>×</button>
          )}
        </div>

        <div className="tier-filter">
          <Filter size={14} />
          <span className="filter-label">Filter Tier:</span>
          <select value={selectedTier} onChange={(e) => setSelectedTier(e.target.value)}>
            <option value="">All Tiers (1–7)</option>
            <option value="1">Tier 1 - Structural (JSON/Syslog/CEF)</option>
            <option value="2">Tier 2 - Declarative Vendor Pack</option>
            <option value="3">Tier 3 - Field Inference</option>
            <option value="4">Tier 4 - Drain3 Template Mining</option>
            <option value="5">Tier 5 - Shape Similarity</option>
            <option value="6">Tier 6 - ML Anomaly Sidecar</option>
            <option value="7">Tier 7 - Quarantine Fallback</option>
          </select>
        </div>
      </div>

      <div className="table-wrapper">
        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: '130px' }}>Event ID</th>
              <th style={{ width: '100px' }}>Tier</th>
              <th style={{ width: '70px' }}>Conf.</th>
              <th style={{ width: '130px' }}>Source ID</th>
              <th style={{ width: '140px' }}>OCSF Class</th>
              <th style={{ width: '120px' }}>Parser ID</th>
              <th>Parsed Message / Payload</th>
              <th style={{ width: '75px', textAlign: 'right' }}>Action</th>
            </tr>
          </thead>
          <tbody>
            {loading && events.length === 0 ? (
              <tr>
                <td colSpan="8" className="empty-state">
                  <div className="loading-spinner"></div>
                  <span>Polling uli.db records...</span>
                </td>
              </tr>
            ) : events.length === 0 ? (
              <tr>
                <td colSpan="8" className="empty-state">
                  <Database size={24} className="empty-icon" />
                  <p>No events found in uli.db matching current filter.</p>
                  <span className="empty-hint">Use terminal <code>scripts/inject.sh</code> or the Ingest Console to insert live logs.</span>
                </td>
              </tr>
            ) : (
              events.map((ev) => {
                const tier = ev.provenance?.tier || 7;
                const tierStyle = TIER_COLORS[tier] || TIER_COLORS[7];
                const confPercent = Math.round((ev.provenance?.confidence || 0) * 100);
                const classLabel = ev.ocsf?.class_name || (ev.ocsf?.class_uid === 0 ? 'Base Event' : `Class ${ev.ocsf?.class_uid}`);
                const rawHash = ev.provenance?.raw_event_id;

                return (
                  <tr key={ev.event_id} onClick={() => onSelectEvent(ev)} className="table-row">
                    <td>
                      <span className="mono-badge">{ev.event_id?.slice(-8)}</span>
                    </td>
                    <td>
                      <span 
                        className="tier-badge" 
                        style={{ backgroundColor: tierStyle.bg, color: tierStyle.text, borderColor: tierStyle.border }}
                      >
                        {tierStyle.label}
                      </span>
                    </td>
                    <td>
                      <span className={`conf-score ${confPercent >= 80 ? 'conf-high' : confPercent >= 50 ? 'conf-med' : 'conf-low'}`}>
                        {confPercent}%
                      </span>
                    </td>
                    <td>
                      <span className="source-pill" title={ev.source_id}>{ev.source_id}</span>
                    </td>
                    <td>
                      <span className="class-pill">{classLabel}</span>
                    </td>
                    <td>
                      <span className="mono-text" title={ev.provenance?.parser_id}>
                        {ev.provenance?.parser_id || 'none'}
                      </span>
                    </td>
                    <td className="message-cell">
                      <div className="message-preview" title={ev.message}>
                        {ev.message || '—'}
                      </div>
                      {rawHash && (
                        <div className="raw-hash-sub">
                          <Hash size={11} />
                          <span>sha256:{rawHash.slice(0, 12)}...</span>
                        </div>
                      )}
                    </td>
                    <td style={{ textAlign: 'right' }}>
                      <button 
                        className="action-link-btn"
                        onClick={(e) => { e.stopPropagation(); onSelectEvent(ev); }}
                      >
                        <span>View</span>
                        <ArrowUpRight size={13} />
                      </button>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <div className="table-footer">
        <span className="footer-count">Showing {events.length} records from <code>uli.db::events</code></span>
        <span className="footer-legend">
          Click any row to inspect complete OCSF 1.9 schema JSON and forensic SHA-256 byte proof
        </span>
      </div>
    </div>
  );
}
