import React from 'react';
import { Shield, Activity, Database, Cpu, Layers, Terminal, RefreshCw } from 'lucide-react';

export default function Header({ activeTab, setActiveTab, systemStatus, isAutoRefreshing, toggleAutoRefresh, refreshNow }) {
  return (
    <header className="app-header">
      <div className="header-left">
        <div className="brand-logo">
          <Shield size={20} className="brand-icon" />
          <span className="brand-name">ULPF <span className="brand-sub">Console</span></span>
        </div>
        <div className="status-badge">
          <span className={`status-dot ${systemStatus?.status === 'OPERATIONAL' ? 'dot-active' : 'dot-warn'}`}></span>
          <span className="status-text">{systemStatus?.status || 'INITIALIZING'}</span>
          <span className="mode-pill">{systemStatus?.mode?.toUpperCase() || 'LOCAL'}</span>
        </div>
      </div>

      <nav className="header-nav">
        <button 
          className={`nav-tab ${activeTab === 'events' ? 'active' : ''}`}
          onClick={() => setActiveTab('events')}
        >
          <Database size={15} />
          <span>Events & Database</span>
        </button>
        <button 
          className={`nav-tab ${activeTab === 'scalability' ? 'active' : ''}`}
          onClick={() => setActiveTab('scalability')}
        >
          <Cpu size={15} />
          <span>Workers & Scalability</span>
        </button>
        <button 
          className={`nav-tab ${activeTab === 'ladder' ? 'active' : ''}`}
          onClick={() => setActiveTab('ladder')}
        >
          <Layers size={15} />
          <span>7-Tier Ladder</span>
        </button>
        <button 
          className={`nav-tab ${activeTab === 'ingest' ? 'active' : ''}`}
          onClick={() => setActiveTab('ingest')}
        >
          <Terminal size={15} />
          <span>Ingest Console</span>
        </button>
      </nav>

      <div className="header-right">
        <button 
          className={`refresh-btn ${isAutoRefreshing ? 'auto-active' : ''}`}
          onClick={toggleAutoRefresh}
          title={isAutoRefreshing ? 'Auto-refresh active (1.5s)' : 'Click to enable auto-refresh'}
        >
          <Activity size={14} className={isAutoRefreshing ? 'pulse-icon' : ''} />
          <span>{isAutoRefreshing ? 'LIVE 1.5s' : 'PAUSED'}</span>
        </button>
        <button className="icon-btn" onClick={refreshNow} title="Refresh immediately">
          <RefreshCw size={14} />
        </button>
      </div>
    </header>
  );
}
