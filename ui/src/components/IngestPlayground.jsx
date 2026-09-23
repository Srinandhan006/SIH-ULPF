import React, { useState } from 'react';
import { Terminal, Send, CheckCircle2, AlertCircle, ArrowRight, Sparkles } from 'lucide-react';
import { ingestLogLines, fetchEventById } from '../services/api';

const PRESETS = [
  {
    title: 'pfSense Firewall Block (Tier 2 Vendor)',
    source: 'pfsense:fw01',
    line: 'Sep 22 09:14:02 fw01 filterlog[1234]: 5,,,1000000103,igb1,match,block,in,4,0x0,,64,0,0,DF,6,tcp,60,203.0.113.7,10.0.0.5,53211,443,0,S,1085138282,,64240,,mss',
    expected: 'Tier 2 (pfSense filterlog Pack) -> OCSF Network Activity, action=block',
  },
  {
    title: 'Zeek Connection Log (Tier 2 Vendor)',
    source: 'zeek:sensor1',
    line: '1331901000.000000\tCCUIP21wTjqkj8ZqX5\t192.168.202.79\t50463\t192.168.229.251\t80\ttcp\t-\t-\t-\t-\tSH\t-\t0\tFa\t1\t52\t1\t52\t(empty)',
    expected: 'Tier 2 (Zeek conn.log Pack) -> OCSF Network Activity',
  },
  {
    title: 'Squid Web Proxy Denied (Tier 2 Vendor)',
    source: 'squid:proxy01',
    line: '1726947060.123 250 192.168.1.50 TCP_DENIED/403 1450 GET http://malicious-site.com/payload.exe - HIER_NONE/- text/html',
    expected: 'Tier 2 (Squid Access Pack) -> OCSF HTTP Activity',
  },
  {
    title: 'Invented Device, Never Configured (Tier 4 Drain3)',
    source: 'quantumflux:unit-7',
    line: 'QUANTUMFLUX-7 experiencing unscheduled harmonic resonance near sector nine tonight',
    expected: 'Tier 4 (Drain3 mines a fresh template live) -> joins an Unknown Cluster for review',
  },
];

export default function IngestPlayground({ onEventCreated }) {
  const [sourceId, setSourceId] = useState('demo-console');
  const [logLine, setLogLine] = useState(PRESETS[0].line);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const handleInject = async () => {
    if (!logLine.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);

    const lines = logLine
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter((l) => l.length > 0);

    if (lines.length === 0) return;

    try {
      const resp = await ingestLogLines(sourceId, lines);
      const eventIds = resp.event_ids || [];
      
      // Fetch details for all returned events (up to 10 for display)
      const eventDetails = await Promise.all(
        eventIds.slice(0, 10).map((id) => fetchEventById(id).catch(() => null))
      );
      
      setResult({
        resp,
        count: lines.length,
        events: eventDetails.filter(Boolean),
      });
      if (onEventCreated) onEventCreated();
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const loadPreset = (preset) => {
    setSourceId(preset.source);
    setLogLine(preset.line);
    setResult(null);
    setError(null);
  };

  return (
    <div className="section-container">
      <div className="sub-panel">
        <div className="sub-panel-title">
          <Terminal size={18} className="icon-cyan" />
          <h3>Interactive Ingestion Playground</h3>
        </div>
        <p className="panel-sub">
          Inject any single or multi-line log messages into the pipeline and observe immediate classification across the 7-tier parsing ladder.
        </p>

        {/* Preset selectors */}
        <div className="presets-bar">
          <span className="preset-label">Test Samples:</span>
          {PRESETS.map((p, idx) => (
            <button key={idx} className="preset-btn" onClick={() => loadPreset(p)}>
              <Sparkles size={12} />
              <span>{p.title}</span>
            </button>
          ))}
        </div>

        {/* Form controls */}
        <div className="ingest-form">
          <div className="form-group-inline">
            <label>Source Identifier:</label>
            <input 
              type="text" 
              className="source-input" 
              value={sourceId} 
              onChange={(e) => setSourceId(e.target.value)} 
            />
          </div>

          <div className="form-group">
            <label>Raw Log Payload (Single or Multi-line):</label>
            <textarea 
              className="log-textarea" 
              rows="4" 
              value={logLine} 
              onChange={(e) => setLogLine(e.target.value)}
              placeholder="Paste one or multiple log lines here (one per line)..."
            />
          </div>

          <div className="form-actions">
            <button 
              className="primary-btn" 
              onClick={handleInject} 
              disabled={loading || !logLine.trim()}
            >
              <Send size={14} />
              <span>{loading ? 'Processing through Ladder...' : 'Inject & Parse Logs'}</span>
            </button>
          </div>
        </div>

        {/* Result card */}
        {error && (
          <div className="error-banner">
            <AlertCircle size={16} />
            <span>{error}</span>
          </div>
        )}

        {result && (
          <div className="result-card">
            <div className="result-header">
              <CheckCircle2 size={18} className="text-emerald" />
              <strong>Successfully Ingested & Parsed {result.count} Log {result.count === 1 ? 'Line' : 'Lines'} into uli.db</strong>
            </div>

            <div className="result-events-list">
              {result.events.map((ev, idx) => (
                <div key={ev.event_id || idx} className="result-event-row">
                  <div className="event-row-main">
                    <span className="mono-badge">ID: {ev.event_id?.slice(-8)}</span>
                    <span className="val-tier" style={{ fontWeight: 600 }}>
                      Tier {ev.provenance?.tier} ({ev.provenance?.parser_id || 'quarantine'})
                    </span>
                    <span className="val-conf" style={{ color: 'var(--accent-emerald)', fontFamily: 'var(--font-mono)' }}>
                      Conf: {Math.round((ev.provenance?.confidence || 0) * 100)}%
                    </span>
                    <span className="val-class" style={{ color: 'var(--accent-cyan)' }}>
                      OCSF: {ev.ocsf?.class_name || 'Base Event'}
                    </span>
                  </div>
                  <div className="event-row-msg mono-text" style={{ fontSize: '11px', opacity: 0.8, marginTop: '2px' }}>
                    {ev.message}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

