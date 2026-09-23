import React, { useState, useEffect } from 'react';
import { Layers, ShieldCheck, GitFork, ArrowRight, Check, AlertTriangle } from 'lucide-react';
import { fetchParsers, fetchUnknownClusters } from '../services/api';

export default function ParsingLadderView({ systemStatus }) {
  const [parsers, setParsers] = useState([]);
  const [unknownClusters, setUnknownClusters] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      fetchParsers().catch(() => []),
      fetchUnknownClusters().catch(() => []),
    ]).then(([pList, uList]) => {
      setParsers(pList);
      setUnknownClusters(uList);
    }).finally(() => setLoading(false));
  }, []);

  const ladder = systemStatus?.ladder || [
    { tier: 1, name: 'Structural Detection', formats: 'JSON, CEF, LEEF, Syslog 5424/3164, logfmt, CLF', confidence: '0.60 – 0.80' },
    { tier: 2, name: 'Declarative Vendor Pack', formats: 'pfSense, Squid, Zeek (hot-loaded YAML packs)', confidence: '0.85 – 1.00' },
    { tier: 3, name: 'Type & Observable Inference', formats: 'IPv4/v6, Ports, Timestamps, MAC, Hostnames, UUIDs', confidence: '+0.05 – 0.15' },
    { tier: 4, name: 'Drain3 Template Mining', formats: 'Online prefix tree clustering for unseen text logs', confidence: '0.40 – 0.60' },
    { tier: 5, name: 'Shape Similarity', formats: 'MinHash n-gram shape vector matching to known parsers', confidence: '0.30 – 0.70' },
    { tier: 6, name: 'ML Anomaly Sidecar', formats: 'IsolationForest anomaly scoring & vendor probability', confidence: 'Additive (Advisory)' },
    { tier: 7, name: 'Forensic Quarantine', formats: 'Guaranteed total fallback; raw preserved + inferred fields', confidence: '≤ 0.30' },
  ];

  return (
    <div className="section-container">
      {/* 7-Tier Ladder Visual Hierarchy */}
      <div className="ladder-card">
        <div className="sub-panel-title">
          <Layers size={18} className="icon-blue" />
          <h3>The 7-Tier Fail-Soft Parsing Ladder</h3>
        </div>
        <p className="panel-sub">
          Total function design: every stage transforms <code>Event ➔ Event</code> without raising exceptions. A log is never dropped.
        </p>

        <div className="ladder-steps">
          {ladder.map((step) => (
            <div key={step.tier} className="ladder-step-row">
              <div className="tier-num-box">Tier {step.tier}</div>
              <div className="tier-details">
                <div className="tier-title-line">
                  <strong>{step.name}</strong>
                  <span className="tier-conf-tag">Conf: {step.confidence}</span>
                </div>
                <span className="tier-formats">{step.formats}</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Two columns: Active Vendor Packs & Mined Unknown Clusters */}
      <div className="detail-two-col">
        {/* Active Declarative Vendor Packs */}
        <div className="sub-panel">
          <div className="sub-panel-title">
            <ShieldCheck size={16} className="icon-green" />
            <h4>Active Declarative Vendor Packs ({parsers.length})</h4>
          </div>
          <p className="panel-sub">Hot-loaded from <code>parsers/vendors/*.yaml</code> with Ed25519 signature checks</p>

          <div className="scroll-panel-wrap">
            {parsers.length === 0 ? (
              <p className="empty-sub">Loading active parser packs...</p>
            ) : (
              <table className="mini-table">
                <thead>
                  <tr>
                    <th>Parser ID</th>
                    <th>Vendor / Product</th>
                    <th>Version</th>
                    <th>Signature</th>
                  </tr>
                </thead>
                <tbody>
                  {parsers.map((p) => (
                    <tr key={p.parser_id}>
                      <td><code className="mono">{p.parser_id}</code></td>
                      <td>{p.vendor || 'Generic'} / {p.product || '—'}</td>
                      <td>{p.version || '1.0.0'}</td>
                      <td>
                        <span className="badge-verified">
                          {p.signature_ok ? 'Verified ✓' : 'Local Unsigned'}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* Drain3 Unknown Source Clusters */}
        <div className="sub-panel">
          <div className="sub-panel-title">
            <GitFork size={16} className="icon-purple" />
            <h4>Unknown Source Clusters ({unknownClusters.length})</h4>
          </div>
          <p className="panel-sub">Unseen formats clustered via Drain3; auto-generates candidate YAML packs</p>

          <div className="scroll-panel-wrap">
            {unknownClusters.length === 0 ? (
              <div className="empty-clusters-box">
                <Check size={20} className="text-emerald" />
                <p>No unclassified format clusters. All incoming logs successfully parsed at Tier 1 or Tier 2.</p>
              </div>
            ) : (
              <table className="mini-table">
                <thead>
                  <tr>
                    <th>Cluster ID</th>
                    <th>Events</th>
                    <th>Status</th>
                    <th>Mined Template</th>
                  </tr>
                </thead>
                <tbody>
                  {unknownClusters.map((c) => {
                    const templates = Object.values(c.templates || {});
                    const mined = templates.sort((a, b) => b.length - a.length)[0];
                    return (
                      <tr key={c.cluster_id}>
                        <td><code className="mono">{c.cluster_id?.slice(0, 8)}</code></td>
                        <td>{c.event_count || 1}</td>
                        <td><span className="source-pill">{c.status}</span></td>
                        <td className="message-cell"><code className="mono text-xs">{mined || '(mining...)'}</code></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
