import React, { useState } from 'react';
import { Cpu, Server, HardDrive, Layers, TrendingUp, Zap, Box, CheckCircle2 } from 'lucide-react';

export default function ScalabilityView({ systemStatus }) {
  const [workerCount, setWorkerCount] = useState(4);
  const baselineEps = 552.5;
  const projectedEps = Math.round(workerCount * baselineEps);
  const projectedDailyEvents = ((projectedEps * 86400) / 1_000_000).toFixed(1);

  const benchmarks = systemStatus?.benchmarks || {
    single_worker_baseline_eps: 552.5,
    batch_speedup: '2.63x',
    p99_latency_ms: 2.58,
    verified_concurrent_sources: 1000,
    memory_rss_delta_mb: 3.1,
    loss_rate: '0.000%',
  };

  const containers = systemStatus?.containers || [
    { name: 'uli-collector', role: 'Go 1.22 Distroless Edge Forwarder', size_mb: 17.9, ports: '5514/udp, 5514/tcp' },
    { name: 'uli-worker', role: '7-Tier Parsing & Drain3 Worker', size_mb: 422.0, ports: 'Internal Consumer' },
    { name: 'uli-api', role: 'FastAPI Ingest & Management Gateway', size_mb: 441.0, ports: '8080/tcp' },
    { name: 'uli-ml', role: 'Optional IsolationForest Sidecar', size_mb: 687.0, ports: '8090/tcp' },
  ];

  return (
    <div className="section-container">
      {/* Topology Banner */}
      <div className="topology-card">
        <div className="topology-header">
          <Server size={18} className="icon-blue" />
          <h3>Distributed Production Scaling Architecture</h3>
          <span className="topology-mode">Current Mode: {systemStatus?.mode?.toUpperCase() || 'LOCAL'}</span>
        </div>
        <p className="topology-desc">
          ULPF scales horizontally via stateless consumer group workers. Log ingest is decoupled from parsing through Redis Streams, partitioned by <code>source_id</code> to guarantee chronological ordering per source.
        </p>

        <div className="topology-pipeline">
          <div className="pipeline-node">
            <span className="node-tag">INGEST TIER</span>
            <strong>Go Collector</strong>
            <span className="node-detail">17.9 MB Distroless</span>
            <span className="node-spec">UDP/TCP Syslog 5514</span>
          </div>
          <div className="pipeline-arrow">➔</div>
          <div className="pipeline-node">
            <span className="node-tag">BUFFER QUEUE</span>
            <strong>Redis Streams</strong>
            <span className="node-detail">Consumer group, partitioned by source_id</span>
            <span className="node-spec">At-least-once delivery</span>
          </div>
          <div className="pipeline-arrow">➔</div>
          <div className="pipeline-node active-highlight">
            <span className="node-tag">WORKER TIER</span>
            <strong>Stateless Workers (x{workerCount})</strong>
            <span className="node-detail">7-Tier Non-Blocking</span>
            <span className="node-spec">552.5 EPS per Core</span>
          </div>
          <div className="pipeline-arrow">➔</div>
          <div className="pipeline-node">
            <span className="node-tag">STORAGE TIER</span>
            <strong>SQL + Raw Store</strong>
            <span className="node-detail">SQLite (demo) / Postgres (prod)</span>
            <span className="node-spec">Content-addressed segment files</span>
          </div>
        </div>
      </div>

      {/* Interactive Scaling Simulator */}
      <div className="simulator-card">
        <div className="simulator-header">
          <div className="sim-title">
            <TrendingUp size={18} className="icon-cyan" />
            <h4>Horizontal Capacity & Throughput Calculator</h4>
          </div>
          <div className="slider-control">
            <label>Worker Replicas: <strong>{workerCount} workers</strong></label>
            <input 
              type="range" 
              min="1" 
              max="32" 
              value={workerCount} 
              onChange={(e) => setWorkerCount(Number(e.target.value))} 
            />
          </div>
        </div>

        <div className="sim-results-grid">
          <div className="sim-result-item">
            <span className="sim-lbl">Projected Aggregate Throughput</span>
            <div className="sim-val">{projectedEps.toLocaleString()} <span className="sim-unit">Events/sec</span></div>
            <span className="sim-sub">Based on verified ~552.5 EPS single-worker baseline</span>
          </div>
          <div className="sim-result-item">
            <span className="sim-lbl">Daily Event Ingestion Capacity</span>
            <div className="sim-val">{projectedDailyEvents} <span className="sim-unit">Million / Day</span></div>
            <span className="sim-sub">Continuous 24-hour non-stop stream ingestion</span>
          </div>
          <div className="sim-result-item">
            <span className="sim-lbl">Recommended CPU Allocation</span>
            <div className="sim-val">{workerCount} <span className="sim-unit">vCPUs</span></div>
            <span className="sim-sub">1 dedicated vCPU per worker replica</span>
          </div>
          <div className="sim-result-item">
            <span className="sim-lbl">RAM Footprint (RSS)</span>
            <div className="sim-val">{(workerCount * 0.35).toFixed(1)} <span className="sim-unit">GB</span></div>
            <span className="sim-sub">Flat memory profile; zero leaks verified</span>
          </div>
        </div>
      </div>

      {/* Benchmarks & Container Footprint Grid */}
      <div className="detail-two-col">
        <div className="sub-panel">
          <div className="sub-panel-title">
            <Zap size={16} className="icon-amber" />
            <h4>Empirical Test Suite Benchmarks</h4>
          </div>
          <p className="panel-sub">Verbatim figures measured on test hardware from <code>docs/scalability.md</code></p>
          <table className="mini-table">
            <thead>
              <tr>
                <th>Scale Target</th>
                <th>Throughput</th>
                <th>Speedup</th>
                <th>Latency (p99)</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>1,000 Events</td>
                <td>500.7 EPS</td>
                <td><span className="text-emerald">2.70x</span></td>
                <td>2.68 ms</td>
              </tr>
              <tr>
                <td>10,000 Events</td>
                <td>484.3 EPS</td>
                <td><span className="text-emerald">2.63x</span></td>
                <td>2.70 ms</td>
              </tr>
              <tr>
                <td><strong>100,000 Events</strong></td>
                <td><strong>552.5 EPS</strong></td>
                <td><span className="text-emerald"><strong>2.63x</strong></span></td>
                <td><strong>2.58 ms</strong></td>
              </tr>
              <tr>
                <td>1,000 Concurrent Sources</td>
                <td>494.6 EPS</td>
                <td>Isolated</td>
                <td>2.72 ms</td>
              </tr>
              <tr>
                <td>Drift Under Load (200w)</td>
                <td>744.5 EPS</td>
                <td>Online</td>
                <td>1.94 ms</td>
              </tr>
            </tbody>
          </table>
          <div className="benchmark-note">
            <CheckCircle2 size={13} className="text-emerald" />
            <span>SQL statement consolidation via <code>process_batch()</code> reduced 10 separate executes per event to 1 batch upsert.</span>
          </div>
        </div>

        <div className="sub-panel">
          <div className="sub-panel-title">
            <Box size={16} className="icon-purple" />
            <h4>Docker Container Footprints</h4>
          </div>
          <p className="panel-sub">Multi-stage distroless and slim image sizes measured from this build</p>
          <table className="mini-table">
            <thead>
              <tr>
                <th>Service Name</th>
                <th>Role in Cluster</th>
                <th>Image Size</th>
              </tr>
            </thead>
            <tbody>
              {containers.map((c) => (
                <tr key={c.name}>
                  <td><code>{c.name}</code></td>
                  <td>{c.role}</td>
                  <td><span className="size-badge">{c.size_mb} MB</span></td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="benchmark-note">
            <CheckCircle2 size={13} className="text-emerald" />
            <span>ML sidecar is fully decoupled (687 MB) and optional (<code>ULI_ML_ENABLED=false</code> by default).</span>
          </div>
        </div>
      </div>
    </div>
  );
}
