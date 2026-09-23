import React, { useState, useEffect, useCallback } from 'react';
import Header from './components/Header';
import MetricCards from './components/MetricCards';
import DatabaseViewer from './components/DatabaseViewer';
import EventDetailModal from './components/EventDetailModal';
import ScalabilityView from './components/ScalabilityView';
import ParsingLadderView from './components/ParsingLadderView';
import IngestPlayground from './components/IngestPlayground';
import { fetchSystemStatus, fetchEvents } from './services/api';
import './App.css';

export default function App() {
  const [activeTab, setActiveTab] = useState('events');
  const [systemStatus, setSystemStatus] = useState(null);
  const [events, setEvents] = useState([]);
  const [selectedEvent, setSelectedEvent] = useState(null);
  const [selectedTier, setSelectedTier] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [isAutoRefreshing, setIsAutoRefreshing] = useState(true);

  const loadData = useCallback(async () => {
    try {
      const [statusData, eventsData] = await Promise.all([
        fetchSystemStatus().catch(() => null),
        fetchEvents({ tier: selectedTier, q: searchQuery, limit: 100 }).catch(() => []),
      ]);
      if (statusData) setSystemStatus(statusData);
      setEvents(eventsData);
    } catch (e) {
      console.error('Data refresh error:', e);
    } finally {
      setLoading(false);
    }
  }, [selectedTier, searchQuery]);

  // Initial load
  useEffect(() => {
    loadData();
  }, [loadData]);

  // Periodic polling (1.5 seconds when active)
  useEffect(() => {
    if (!isAutoRefreshing) return;
    const interval = setInterval(() => {
      loadData();
    }, 1500);
    return () => clearInterval(interval);
  }, [isAutoRefreshing, loadData]);

  return (
    <div className="app-shell">
      <Header 
        activeTab={activeTab} 
        setActiveTab={setActiveTab} 
        systemStatus={systemStatus}
        isAutoRefreshing={isAutoRefreshing}
        toggleAutoRefresh={() => setIsAutoRefreshing(!isAutoRefreshing)}
        refreshNow={loadData}
      />

      <main className="app-main">
        {/* Top metrics always visible to give an instant operational overview */}
        <MetricCards 
          stats={systemStatus?.stats} 
          benchmarks={systemStatus?.benchmarks} 
        />

        {/* Tab views */}
        {activeTab === 'events' && (
          <DatabaseViewer 
            events={events}
            loading={loading}
            onSelectEvent={setSelectedEvent}
            selectedTier={selectedTier}
            setSelectedTier={setSelectedTier}
            searchQuery={searchQuery}
            setSearchQuery={setSearchQuery}
          />
        )}

        {activeTab === 'scalability' && (
          <ScalabilityView systemStatus={systemStatus} />
        )}

        {activeTab === 'ladder' && (
          <ParsingLadderView systemStatus={systemStatus} />
        )}

        {activeTab === 'ingest' && (
          <IngestPlayground onEventCreated={loadData} />
        )}
      </main>

      {/* Modal Inspector for clicked events */}
      {selectedEvent && (
        <EventDetailModal 
          event={selectedEvent} 
          onClose={() => setSelectedEvent(null)} 
        />
      )}
    </div>
  );
}
