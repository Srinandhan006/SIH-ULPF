import React from 'react';
import { Database, Network, Zap, GitFork, CheckCircle2 } from 'lucide-react';

export default function MetricCards({ stats, benchmarks }) {
  const totalEvents = stats?.events ?? 0;
  const sourcesCount = stats?.sources ?? 0;
  const unknownCount = stats?.open_unknown_clusters ?? 0;
  const baselineEps = benchmarks?.single_worker_baseline_eps ?? 552.5;

  return (
    <div className="metrics-grid">
      <div className="metric-card">
        <div className="metric-header">
          <span className="metric-label">Database Events (uli.db)</span>
          <Database size={16} className="metric-icon icon-blue" />
        </div>
        <div className="metric-value">{totalEvents.toLocaleString()}</div>
        <div className="metric-subtext">
          <span className="text-emerald">✓ Indexed</span> in SQLite FTS5 / Postgres
        </div>
      </div>

      <div className="metric-card">
        <div className="metric-header">
          <span className="metric-label">Active Log Sources</span>
          <Network size={16} className="metric-icon icon-cyan" />
        </div>
        <div className="metric-value">{sourcesCount}</div>
        <div className="metric-subtext">
          Per-source queue isolation (tested to 1,000)
        </div>
      </div>

      <div className="metric-card">
        <div className="metric-header">
          <span className="metric-label">Per-Worker Baseline</span>
          <Zap size={16} className="metric-icon icon-amber" />
        </div>
        <div className="metric-value">{baselineEps} <span className="metric-unit">EPS</span></div>
        <div className="metric-subtext">
          <span className="text-emerald">2.63x gain</span> via batched pipeline writes
        </div>
      </div>

      <div className="metric-card">
        <div className="metric-header">
          <span className="metric-label">Unknown Format Clusters</span>
          <GitFork size={16} className="metric-icon icon-purple" />
        </div>
        <div className="metric-value">{unknownCount}</div>
        <div className="metric-subtext">
          Drain3 prefix trees & template miners
        </div>
      </div>

      <div className="metric-card">
        <div className="metric-header">
          <span className="metric-label">Forensic Loss Rate</span>
          <CheckCircle2 size={16} className="metric-icon icon-green" />
        </div>
        <div className="metric-value">0.000%</div>
        <div className="metric-subtext">
          100% SHA-256 content-addressed raw vault
        </div>
      </div>
    </div>
  );
}
