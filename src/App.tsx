import { useState, useCallback, useEffect, type FormEvent, useRef, useMemo } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer, ReferenceLine } from 'recharts';
import { ReactFlow, Background, Controls, MiniMap, useNodesState, useEdgesState, MarkerType, Handle, Position } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Loader2, Search, TrendingUp, Users, Clock, AlertCircle, Eye, Trophy, TrendingDown, ExternalLink, Tag, Download, X, Sun, Moon, Filter, Newspaper } from 'lucide-react';
import * as htmlToImage from 'html-to-image';
import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

// --- Types ---
interface Citation {
  source_name: string;
  url: string;
  published_at: string;
  snippet: string;
}

interface StoryEvent {
  id: string;
  title: string;
  description: string;
  date: string;
  impact: 'low' | 'medium' | 'high';
  sentiment: 'positive' | 'negative' | 'neutral';
  playersInvolved: string[];
  arcId: string;
  citations: Citation[];
}

interface Player {
  id: string;
  name: string;
  type: 'person' | 'company' | 'organization' | 'country' | 'other';
  role: string;
  sentimentScore: number;
}

interface Relationship {
  source: string;
  target: string;
  type: 'alliance' | 'conflict' | 'neutral';
  strength: number;
  description: string;
}

interface Arc {
  id: string;
  title: string;
  summary: string;
  involvedPlayers: string[];
  startEventId: string;
  endEventId: string | null;
  status: 'ongoing' | 'resolved';
}

interface Insight {
  id: string;
  type: 'who_is_winning' | 'turning_point' | 'key_player' | 'summary';
  content: string;
  citations: Citation[];
}

interface NewsItem {
  title: string;
  link: string;
  source: string;
  published_at: string;
}

interface PlayerProfileData {
  name: string;
  summary: string;
  role_in_story: string;
  motivations: string[];
  alliances: Array<{ name: string; description: string }>;
  conflicts: Array<{ name: string; description: string }>;
  timeline_contributions: Array<{ event: string; impact: string }>;
  risk_score: number;
  outlook: string;
  citations: string[];
}

interface StoryData {
  timeline: StoryEvent[];
  players: Player[];
  relationships: Relationship[];
  arcs: Arc[];
  insights: Insight[];
  news_context?: NewsItem[];
  fetched_at?: string | null;
}

// --- Custom Node for React Flow ---
const PlayerNode = ({ data }: { data: { label: string; role: string; type: string; isHighlighted?: boolean; isDimmed?: boolean } }) => {
  return (
    <div className={cn(
      "px-4 py-2 shadow-md rounded-xl border-2 bg-white dark:bg-slate-900 min-w-[150px] transition-all duration-300",
      data.type === 'company' ? "border-blue-500" : "border-emerald-500",
      data.isDimmed && "opacity-30 scale-95",
      data.isHighlighted && "shadow-lg ring-4 ring-indigo-500/20 dark:ring-indigo-500/40 scale-105"
    )}>
      <Handle type="target" position={Position.Top} className="w-2 h-2 dark:bg-slate-400" />
      <div className="flex flex-col">
        <div className="font-bold text-sm text-slate-800 dark:text-slate-100">{data.label}</div>
        <div className="text-xs text-slate-500 dark:text-slate-400 mt-1">{data.role}</div>
      </div>
      <Handle type="source" position={Position.Bottom} className="w-2 h-2 dark:bg-slate-400" />
    </div>
  );
};

const nodeTypes = {
  playerNode: PlayerNode,
};


const formatDateTime = (value?: string | null) => {
  if (!value) return 'Unknown';

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(parsed);
};

const CitationList = ({ citations, compact = false }: { citations?: Citation[]; compact?: boolean }) => {
  if (!citations?.length) return null;

  return (
    <div className={cn('space-y-2', compact && 'space-y-1.5')}>
      {citations.map((citation, index) => (
        <div
          key={`${citation.url}-${index}`}
          className={cn(
            'rounded-lg border border-slate-200/80 dark:border-slate-700 bg-white/70 dark:bg-slate-900/50 px-3 py-2',
            compact && 'px-2.5 py-2'
          )}
        >
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs font-medium text-slate-700 dark:text-slate-200">
            <span className="inline-flex items-center gap-1">
              <Newspaper className="w-3 h-3" />
              {citation.source_name}
            </span>
            <span className="text-slate-400 dark:text-slate-500">•</span>
            <span className="text-slate-500 dark:text-slate-400">{formatDateTime(citation.published_at)}</span>
            {citation.url && (
              <a
                href={citation.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-indigo-600 dark:text-indigo-400 hover:underline"
              >
                Source <ExternalLink className="w-3 h-3" />
              </a>
            )}
          </div>
          <p className="mt-1 text-xs leading-relaxed text-slate-600 dark:text-slate-400">{citation.snippet}</p>
        </div>
      ))}
    </div>
  );
};

const LOADING_MESSAGES = [
  "Searching the web for the latest data...",
  "Extracting timeline and sources...",
  "Mapping key players and relationships...",
  "Analyzing sentiment and impact...",
  "Finalizing visual narrative..."
];

// --- Main Component ---
export default function App() {
  const [topic, setTopic] = useState('');
  const [loading, setLoading] = useState(false);
  const [loadingMsgIdx, setLoadingMsgIdx] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<StoryData | null>(null);
  const [activeEventIdx, setActiveEventIdx] = useState<number | null>(null);

  const [deepDivePlayer, setDeepDivePlayer] = useState<Player | null>(null);
  const [deepDiveContent, setDeepDiveContent] = useState<PlayerProfileData | null>(null);
  const [loadingDeepDive, setLoadingDeepDive] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [isDarkMode, setIsDarkMode] = useState(false);

  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  
  // Filters
  const [filterPlayerId, setFilterPlayerId] = useState<string | 'all'>('all');
  const [filterArcId, setFilterArcId] = useState<string | 'all'>('all');
  const [filterImpact, setFilterImpact] = useState<string | 'all'>('all');
  const [highlightRelType, setHighlightRelType] = useState<'all' | 'conflict' | 'alliance'>('all');
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('asc');

  const timelineRef = useRef<HTMLDivElement>(null);

  // Filtered Timeline
  const filteredTimeline = useMemo(() => {
    if (!data) return [];
    let filtered = data.timeline.filter(event => {
      const playersInvolved = Array.isArray(event.playersInvolved) ? event.playersInvolved : (event.playersInvolved ? [event.playersInvolved] : []);
      if (filterPlayerId !== 'all' && !playersInvolved.includes(filterPlayerId)) return false;
      if (filterArcId !== 'all' && event.arcId !== filterArcId) return false;
      if (filterImpact !== 'all' && event.impact !== filterImpact) return false;
      return true;
    });
    
    if (sortOrder === 'desc') {
      filtered = [...filtered].reverse();
    }
    
    return filtered;
  }, [data, filterPlayerId, filterArcId, filterImpact, sortOrder]);

  // Apply Filters to Graph
  useEffect(() => {
    if (!data) return;

    const activePlayerIds = new Set<string>();
    filteredTimeline.forEach(event => {
      const players = Array.isArray(event.playersInvolved) ? event.playersInvolved : (event.playersInvolved ? [event.playersInvolved] : []);
      players.forEach(id => activePlayerIds.add(id));
    });

    const selectedEvent = selectedEventId ? data.timeline.find(e => e.id === selectedEventId) : null;
    const eventPlayers = selectedEvent ? new Set(Array.isArray(selectedEvent.playersInvolved) ? selectedEvent.playersInvolved : (selectedEvent.playersInvolved ? [selectedEvent.playersInvolved] : [])) : null;

    setNodes((nds) => nds.map(n => {
      let isDimmed = false;
      let isHighlighted = false;

      if (eventPlayers) {
        isHighlighted = eventPlayers.has(n.id);
        isDimmed = !eventPlayers.has(n.id);
      } else if (filterPlayerId !== 'all') {
        const connectedEdges = data.relationships.filter(e => e.source === filterPlayerId || e.target === filterPlayerId);
        const connectedNodeIds = new Set([filterPlayerId, ...connectedEdges.map(e => e.source), ...connectedEdges.map(e => e.target)]);
        isHighlighted = n.id === filterPlayerId;
        isDimmed = !connectedNodeIds.has(n.id);
      } else if (filterArcId !== 'all' || filterImpact !== 'all') {
        isDimmed = !activePlayerIds.has(n.id);
      }

      return {
        ...n,
        data: {
          ...n.data,
          isHighlighted,
          isDimmed
        }
      };
    }));

    setEdges((eds) => eds.map(e => {
      let isVisible = true;
      let isConnectedToFocus = false;

      if (highlightRelType !== 'all' && e.data?.type !== highlightRelType) {
        isVisible = false;
      }

      if (eventPlayers) {
        isConnectedToFocus = eventPlayers.has(e.source) || eventPlayers.has(e.target);
        if (!isConnectedToFocus) isVisible = false;
      } else if (filterPlayerId !== 'all') {
        isConnectedToFocus = e.source === filterPlayerId || e.target === filterPlayerId;
        if (!isConnectedToFocus) isVisible = false;
      } else if (filterArcId !== 'all' || filterImpact !== 'all') {
        if (!activePlayerIds.has(e.source) || !activePlayerIds.has(e.target)) {
          isVisible = false;
        }
      }

      return {
        ...e,
        style: {
          ...e.style,
          opacity: isVisible ? 1 : 0.05,
          strokeWidth: isVisible && isConnectedToFocus ? 3 : 2,
        },
        animated: isVisible ? e.data?.originalAnimated : false,
      };
    }));
  }, [data, filteredTimeline, filterPlayerId, filterArcId, filterImpact, highlightRelType, selectedEventId, setNodes, setEdges]);

  // Cycle loading messages
  useEffect(() => {
    let interval: NodeJS.Timeout;
    if (loading) {
      setLoadingMsgIdx(0);
      interval = setInterval(() => {
        setLoadingMsgIdx((prev) => Math.min(prev + 1, LOADING_MESSAGES.length - 1));
      }, 2500);
    }
    return () => clearInterval(interval);
  }, [loading]);

  // Toggle dark mode class on html element
  useEffect(() => {
    if (isDarkMode) {
      document.documentElement.classList.add('dark');
    } else {
      document.documentElement.classList.remove('dark');
    }
  }, [isDarkMode]);

  // Scroll timeline when chart is hovered
  useEffect(() => {
    if (activeEventIdx !== null && timelineRef.current) {
      const eventEl = timelineRef.current.children[activeEventIdx] as HTMLElement;
      if (eventEl) {
        eventEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
    }
  }, [activeEventIdx]);

  const onNodeClick = useCallback((_: any, node: any) => {
    setFilterPlayerId(node.id);
    setSelectedEventId(null);
  }, []);

  const onPaneClick = useCallback(() => {
    setFilterPlayerId('all');
    setSelectedEventId(null);
  }, []);

  const onNodeDoubleClick = useCallback(async (_: any, node: any) => {
    if (!data) return;
    const player = data.players.find(p => p.id === node.id);
    if (!player) return;
    
    setDeepDivePlayer(player);
    setDeepDiveContent(null);
    setLoadingDeepDive(true);

    try {
      const response = await fetch('/api/player-profile', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          player_id: player.id,
          player_name: player.name,
          player_role: player.role,
          player_type: player.type,
          topic: topic,
        }),
      });

      if (!response.ok) {
        throw new Error(`API error: ${response.statusText}`);
      }

      const profile: PlayerProfileData = await response.json();
      
      // Format the structured profile into readable text for display
      setDeepDiveContent(profile);
    } catch (err) {
      console.error(err);
      setDeepDiveContent(null);
    } finally {
      setLoadingDeepDive(false);
    }
  }, [data, topic]);

  const handleDownload = async () => {
    const element = document.getElementById('dashboard-content');
    if (!element) return;
    setIsDownloading(true);
    try {
      const dataUrl = await htmlToImage.toPng(element, { 
        pixelRatio: 2, 
        backgroundColor: isDarkMode ? '#020617' : '#f8fafc'
      });
      const link = document.createElement('a');
      link.download = `story-arc-${topic.replace(/\\s+/g, '-').toLowerCase()}.png`;
      link.href = dataUrl;
      link.click();
    } catch (err) {
      console.error("Failed to download", err);
    } finally {
      setIsDownloading(false);
    }
  };

  const handleTimelineClick = (event: StoryEvent) => {
    if (!data) return;
    const mentionedPlayerIds = Array.isArray(event.playersInvolved) ? event.playersInvolved : (event.playersInvolved ? [event.playersInvolved] : []);
    
    if (mentionedPlayerIds.length === 0) {
      setSelectedEventId(null);
      return;
    }

    setSelectedEventId(event.id);
  };

  const setupGraph = (parsedData: StoryData) => {
    const newNodes = parsedData.players.map((player, index) => ({
      id: player.id,
      type: 'playerNode',
      position: { 
        x: (index % 3) * 250 + 50, 
        y: Math.floor(index / 3) * 150 + 50 
      },
      data: { label: player.name, role: player.role, type: player.type },
    }));

    const newEdges = parsedData.relationships.map((rel, index) => {
      let color = '#94a3b8'; // slate-400
      if (rel.type === 'conflict') color = '#ef4444'; // red-500
      if (rel.type === 'alliance') color = '#10b981'; // emerald-500
      if (rel.type === 'neutral') color = '#3b82f6'; // blue-500

      return {
        id: `e-${rel.source}-${rel.target}-${index}`,
        source: rel.source,
        target: rel.target,
        label: rel.description,
        animated: rel.type === 'conflict',
        data: { originalAnimated: rel.type === 'conflict', type: rel.type },
        style: { stroke: color, strokeWidth: 2, transition: 'opacity 0.3s, stroke-width 0.3s' },
        labelStyle: { fill: 'var(--edge-label-color)', fontWeight: 500, fontSize: 10 },
        labelBgStyle: { fill: 'var(--edge-bg-color)', fillOpacity: 0.8 },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: color,
        },
      };
    });

    setNodes(newNodes);
    setEdges(newEdges);
  };

  const fetchData = async (searchQuery: string) => {
    if (!searchQuery.trim()) return;

    setLoading(true);
    setError(null);
    setData(null);
    setActiveEventIdx(null);
    setTopic(searchQuery);

    const cacheKey = `story-arc-${searchQuery.toLowerCase().trim()}`;
    const cached = localStorage.getItem(cacheKey);
    
    if (cached) {
      try {
        const rawData = JSON.parse(cached);
        const parsedData: StoryData = {
          players: Array.isArray(rawData.players) ? rawData.players : [],
          relationships: Array.isArray(rawData.relationships) ? rawData.relationships : [],
          timeline: Array.isArray(rawData.timeline) ? rawData.timeline : [],
          arcs: Array.isArray(rawData.arcs) ? rawData.arcs : [],
          insights: Array.isArray(rawData.insights) ? rawData.insights : [],
          news_context: Array.isArray(rawData.news_context) ? rawData.news_context : [],
          fetched_at: rawData.fetched_at ?? null
        };
        setData(parsedData);
        setupGraph(parsedData);
        setLoading(false);
        return;
      } catch (e) {
        console.error("Cache parsing failed", e);
      }
    }

    try {
      // Call backend API to analyze the story
      const response = await fetch('/api/analyze', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ topic: searchQuery }),
      });

      if (!response.ok) {
        throw new Error(`API error: ${response.statusText}`);
      }

      const parsedData: StoryData = await response.json();
      
      setData(parsedData);
      localStorage.setItem(cacheKey, JSON.stringify(parsedData));
      
      setupGraph(parsedData);

    } catch (err: any) {
      console.error(err);
      setError(err.message || "An error occurred while analyzing the story.");
    } finally {
      setLoading(false);
    }
  };

  const handleSearchSubmit = (e: FormEvent) => {
    e.preventDefault();
    fetchData(topic);
  };

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-950 text-slate-900 dark:text-slate-50 font-sans selection:bg-indigo-100 selection:text-indigo-900 dark:selection:bg-indigo-900 dark:selection:text-indigo-100 transition-colors duration-300">
      {/* Header */}
      <header className="bg-white dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800 sticky top-0 z-10 transition-colors duration-300">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4">
          <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
            <div className="flex items-center gap-2">
              <div className="bg-indigo-600 p-2 rounded-lg">
                <TrendingUp className="w-6 h-6 text-white" />
              </div>
              <div>
                <h1 className="text-2xl font-bold tracking-tight text-slate-900 dark:text-white">Story Arc Tracker</h1>
                {data?.fetched_at && (
                  <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
                    Last updated {formatDateTime(data.fetched_at)}
                  </p>
                )}
              </div>
            </div>
            
            <div className="flex-1 max-w-3xl flex items-center gap-3">
              <form onSubmit={handleSearchSubmit} className="flex-1 relative">
                <div className="relative flex items-center w-full">
                  <Search className="absolute left-3 w-5 h-5 text-slate-400 dark:text-slate-500" />
                  <input
                    type="text"
                    value={topic}
                    onChange={(e) => setTopic(e.target.value)}
                    placeholder="e.g., The OpenAI board saga, Nvidia's AI dominance..."
                    className="w-full pl-10 pr-24 py-3 bg-slate-100 dark:bg-slate-800 border-transparent dark:border-slate-700 rounded-full focus:bg-white dark:focus:bg-slate-900 focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200 dark:focus:ring-indigo-900 transition-all outline-none text-sm dark:text-white dark:placeholder-slate-400"
                    disabled={loading}
                  />
                  <button
                    type="submit"
                    disabled={loading || !topic.trim()}
                    className="absolute right-1.5 px-4 py-1.5 bg-indigo-600 text-white text-sm font-medium rounded-full hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-2"
                  >
                    {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Analyze'}
                  </button>
                </div>
              </form>
              {data && (
                <button
                  onClick={handleDownload}
                  disabled={isDownloading}
                  className="px-4 py-2.5 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-200 text-sm font-medium rounded-full hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors flex items-center gap-2 shadow-sm shrink-0"
                >
                  {isDownloading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
                  Export
                </button>
              )}
              <button
                onClick={() => setIsDarkMode(!isDarkMode)}
                className="p-2.5 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-200 rounded-full hover:bg-slate-50 dark:hover:bg-slate-700 transition-colors shadow-sm shrink-0 flex items-center justify-center"
                aria-label="Toggle dark mode"
              >
                {isDarkMode ? <Sun className="w-5 h-5" /> : <Moon className="w-5 h-5" />}
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        
        {/* Empty State */}
        {!data && !loading && !error && (
          <div className="flex flex-col items-center justify-center h-[60vh] text-center max-w-2xl mx-auto">
            <div className="w-20 h-20 bg-indigo-50 dark:bg-indigo-900/30 rounded-full flex items-center justify-center mb-6">
              <TrendingUp className="w-10 h-10 text-indigo-600 dark:text-indigo-400" />
            </div>
            <h2 className="text-3xl font-bold text-slate-900 dark:text-white mb-4">Track the Narrative Arc</h2>
            <p className="text-lg text-slate-600 dark:text-slate-400 mb-8">
              Enter a complex business story or topic above. We'll use AI to extract the timeline, map the key players, analyze sentiment shifts, and predict what happens next.
            </p>
            <div className="flex flex-wrap justify-center gap-3">
              {['The OpenAI board saga', 'Nvidia\'s AI dominance', 'The Boeing safety crisis'].map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => fetchData(suggestion)}
                  className="px-4 py-2 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-full text-sm font-medium text-slate-700 dark:text-slate-300 hover:border-indigo-300 dark:hover:border-indigo-500 hover:text-indigo-600 dark:hover:text-indigo-400 hover:shadow-sm transition-all"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Error State */}
        {error && (
          <div className="p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800/50 rounded-xl flex items-start gap-3 text-red-800 dark:text-red-400">
            <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
            <div>
              <h3 className="font-semibold">Analysis Failed</h3>
              <p className="text-sm mt-1">{error}</p>
            </div>
          </div>
        )}

        {/* Loading State */}
        {loading && (
          <div className="flex flex-col items-center justify-center h-[60vh]">
            <Loader2 className="w-12 h-12 text-indigo-600 dark:text-indigo-400 animate-spin mb-6" />
            <p className="text-slate-700 dark:text-slate-300 font-medium text-lg animate-pulse">
              {LOADING_MESSAGES[loadingMsgIdx]}
            </p>
          </div>
        )}

        {/* Dashboard */}
        {data && !loading && (
          <div id="dashboard-content" className="space-y-8 animate-in fade-in duration-500">
            
            {/* Filter Bar */}
            <div className="bg-white dark:bg-slate-900 rounded-2xl shadow-sm border border-slate-200 dark:border-slate-800 p-4 flex flex-wrap items-center gap-4">
              <div className="flex items-center gap-2 text-slate-500 dark:text-slate-400 font-medium text-sm mr-2">
                <Filter className="w-4 h-4" /> Filters:
              </div>
              
              <select 
                value={filterPlayerId} 
                onChange={(e) => setFilterPlayerId(e.target.value)}
                className="bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 text-sm rounded-lg focus:ring-indigo-500 focus:border-indigo-500 block p-2"
              >
                <option value="all">All Players (Focus Mode)</option>
                {data.players.map(p => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>

              <select 
                value={filterArcId} 
                onChange={(e) => setFilterArcId(e.target.value)}
                className="bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 text-sm rounded-lg focus:ring-indigo-500 focus:border-indigo-500 block p-2"
              >
                <option value="all">All Arcs</option>
                {data.arcs.map(a => (
                  <option key={a.id} value={a.id}>{a.title}</option>
                ))}
              </select>

              <select 
                value={filterImpact} 
                onChange={(e) => setFilterImpact(e.target.value)}
                className="bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 text-sm rounded-lg focus:ring-indigo-500 focus:border-indigo-500 block p-2"
              >
                <option value="all">All Impacts</option>
                <option value="high">High Impact</option>
                <option value="medium">Medium Impact</option>
                <option value="low">Low Impact</option>
              </select>

              <select 
                value={highlightRelType} 
                onChange={(e) => setHighlightRelType(e.target.value as any)}
                className="bg-slate-50 dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 text-sm rounded-lg focus:ring-indigo-500 focus:border-indigo-500 block p-2"
              >
                <option value="all">All Relationships</option>
                <option value="conflict">Conflicts Only</option>
                <option value="alliance">Alliances Only</option>
              </select>
              
              {(filterPlayerId !== 'all' || filterArcId !== 'all' || filterImpact !== 'all' || highlightRelType !== 'all' || selectedEventId !== null) && (
                <button 
                  onClick={() => {
                    setFilterPlayerId('all');
                    setFilterArcId('all');
                    setFilterImpact('all');
                    setHighlightRelType('all');
                    setSelectedEventId(null);
                  }}
                  className="ml-auto text-sm text-indigo-600 dark:text-indigo-400 hover:text-indigo-800 dark:hover:text-indigo-300 font-medium"
                >
                  Clear Filters
                </button>
              )}
            </div>

            {/* Top Row: Timeline & Sentiment */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
              
              {/* Timeline */}
              <div className="lg:col-span-1 bg-white dark:bg-slate-900 rounded-2xl shadow-sm border border-slate-200 dark:border-slate-800 p-6 flex flex-col h-[500px]">
                <div className="flex items-center justify-between mb-6">
                  <div className="flex items-center gap-2">
                    <Clock className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />
                    <h2 className="text-lg font-semibold text-slate-900 dark:text-white">Chronological Timeline</h2>
                  </div>
                  <div className="flex items-center gap-3">
                    <button 
                      onClick={() => setSortOrder(prev => prev === 'asc' ? 'desc' : 'asc')}
                      className="text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:text-indigo-800 dark:hover:text-indigo-300 flex items-center gap-1"
                    >
                      {sortOrder === 'asc' ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                      {sortOrder === 'asc' ? 'Oldest First' : 'Newest First'}
                    </button>
                    <span className="text-xs text-slate-500 dark:text-slate-400 hidden sm:inline">Click event to highlight map</span>
                  </div>
                </div>
                <div ref={timelineRef} className="flex-1 overflow-y-auto pr-4 space-y-6 scrollbar-thin scrollbar-thumb-slate-200 dark:scrollbar-thumb-slate-700">
                  {filteredTimeline.map((event, idx) => (
                    <div 
                      key={idx} 
                      onClick={() => handleTimelineClick(event)}
                      className={cn(
                        "relative pl-6 pb-6 last:pb-0 border-l-2 border-slate-100 dark:border-slate-800 last:border-transparent cursor-pointer transition-colors rounded-r-xl",
                        (activeEventIdx === idx || selectedEventId === event.id) ? "bg-indigo-50/50 dark:bg-indigo-900/20 -ml-2 pl-8" : "hover:bg-slate-50 dark:hover:bg-slate-800/50 -ml-2 pl-8"
                      )}
                    >
                      <div className={cn(
                        "absolute -left-[11px] top-1 w-5 h-5 rounded-full border-4 border-white dark:border-slate-900 transition-transform",
                        (activeEventIdx === idx || selectedEventId === event.id) && "scale-125",
                        event.sentiment === 'positive' ? "bg-emerald-500" :
                        event.sentiment === 'negative' ? "bg-red-500" : "bg-slate-400 dark:bg-slate-500"
                      )} />
                      <div className="flex items-center justify-between gap-2 mb-1">
                        <div className="text-xs font-bold text-indigo-600 dark:text-indigo-400">{event.date}</div>
                        <div className="flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 rounded-full">
                          <Tag className="w-3 h-3" />
                          Impact: {event.impact}
                        </div>
                      </div>
                      <div className="font-semibold text-slate-900 dark:text-white mb-1">{event.title}</div>
                      <div className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed mb-3">{event.description}</div>
                      <CitationList citations={event.citations} compact />
                    </div>
                  ))}
                </div>
              </div>

              {/* Sentiment Chart */}
              <div className="lg:col-span-2 bg-white dark:bg-slate-900 rounded-2xl shadow-sm border border-slate-200 dark:border-slate-800 p-6 flex flex-col h-[500px]">
                <div className="flex items-center justify-between mb-6">
                  <div className="flex items-center gap-2">
                    <TrendingUp className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />
                    <h2 className="text-lg font-semibold text-slate-900 dark:text-white">Sentiment Shifts</h2>
                  </div>
                  <span className="text-xs text-slate-500 dark:text-slate-400">Hover to locate on timeline</span>
                </div>
                <div className="flex-1 min-h-0">
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart 
                      data={(sortOrder === 'desc' ? [...filteredTimeline].reverse() : filteredTimeline).map(e => ({
                        ...e,
                        sentimentScore: e.sentiment === 'positive' ? 100 : e.sentiment === 'negative' ? -100 : 0
                      }))} 
                      margin={{ top: 20, right: 30, left: 0, bottom: 20 }}
                      onMouseMove={(e) => {
                        if (e.activeTooltipIndex !== undefined) {
                          // If sortOrder is desc, the chart index is reversed compared to filteredTimeline
                          const activeIdx = Number(e.activeTooltipIndex);
                          const idx = sortOrder === 'desc' ? filteredTimeline.length - 1 - activeIdx : activeIdx;
                          setActiveEventIdx(idx);
                        }
                      }}
                      onMouseLeave={() => setActiveEventIdx(null)}
                    >
                      <CartesianGrid strokeDasharray="3 3" vertical={false} stroke={isDarkMode ? '#334155' : '#e2e8f0'} />
                      <XAxis 
                        dataKey="date" 
                        axisLine={false}
                        tickLine={false}
                        tick={{ fontSize: 12, fill: isDarkMode ? '#94a3b8' : '#64748b' }}
                        dy={10}
                      />
                      <YAxis 
                        domain={[-100, 100]} 
                        axisLine={false}
                        tickLine={false}
                        tick={{ fontSize: 12, fill: isDarkMode ? '#94a3b8' : '#64748b' }}
                      />
                      <RechartsTooltip 
                        contentStyle={{ 
                          borderRadius: '12px', 
                          border: isDarkMode ? '1px solid #334155' : 'none', 
                          boxShadow: '0 10px 15px -3px rgb(0 0 0 / 0.1)',
                          backgroundColor: isDarkMode ? '#1e293b' : '#ffffff',
                          color: isDarkMode ? '#f8fafc' : '#0f172a'
                        }}
                        labelStyle={{ fontWeight: 'bold', color: isDarkMode ? '#f8fafc' : '#0f172a', marginBottom: '4px' }}
                      />
                      <ReferenceLine y={0} stroke={isDarkMode ? '#475569' : '#94a3b8'} strokeDasharray="3 3" />
                      <Line 
                        name="Sentiment"
                        type="monotone" 
                        dataKey="sentimentScore" 
                        stroke="#4f46e5" 
                        strokeWidth={3}
                        dot={{ r: 4, fill: '#4f46e5', strokeWidth: 2, stroke: isDarkMode ? '#0f172a' : '#ffffff' }}
                        activeDot={{ r: 6, fill: '#4f46e5', stroke: isDarkMode ? '#0f172a' : '#ffffff', strokeWidth: 2 }}
                        animationDuration={1500}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </div>

            {/* Middle Row: Key Players Map */}
            <div className="bg-white dark:bg-slate-900 rounded-2xl shadow-sm border border-slate-200 dark:border-slate-800 p-6 h-[600px] flex flex-col">
              <div className="flex items-center gap-2 mb-4">
                <Users className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />
                <h2 className="text-lg font-semibold text-slate-900 dark:text-white">Key Players & Relationships</h2>
              </div>
              <div className="flex-1 border border-slate-100 dark:border-slate-800 rounded-xl overflow-hidden bg-slate-50/50 dark:bg-slate-950/50">
                <ReactFlow
                  nodes={nodes}
                  edges={edges}
                  onNodesChange={onNodesChange}
                  onEdgesChange={onEdgesChange}
                  onNodeClick={onNodeClick}
                  onNodeDoubleClick={onNodeDoubleClick}
                  onPaneClick={onPaneClick}
                  nodeTypes={nodeTypes}
                  fitView
                  attributionPosition="bottom-right"
                  colorMode={isDarkMode ? 'dark' : 'light'}
                >
                  <Background color={isDarkMode ? '#475569' : '#cbd5e1'} gap={16} />
                  <Controls />
                  <MiniMap nodeStrokeWidth={3} zoomable pannable />
                </ReactFlow>
              </div>
              <div className="flex items-center gap-6 mt-4 text-sm text-slate-600 dark:text-slate-400 justify-center">
                <div className="flex items-center gap-2"><div className="w-3 h-3 rounded-full bg-blue-500"></div> Company</div>
                <div className="flex items-center gap-2"><div className="w-3 h-3 rounded-full bg-emerald-500"></div> Person/Other</div>
                <div className="flex items-center gap-2"><div className="w-4 h-0.5 bg-red-500"></div> Conflict</div>
                <div className="flex items-center gap-2"><div className="w-4 h-0.5 bg-emerald-500"></div> Alliance</div>
                <div className="flex items-center gap-2"><div className="w-4 h-0.5 bg-blue-500"></div> Neutral</div>
              </div>
            </div>

            {/* Bottom Row: Arcs & Insights */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
              
              {/* Story Arcs */}
              <div className="md:col-span-2 bg-white dark:bg-slate-900 rounded-2xl shadow-sm border border-slate-200 dark:border-slate-800 p-6">
                <div className="flex items-center gap-2 mb-6">
                  <TrendingUp className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />
                  <h2 className="text-lg font-semibold text-slate-900 dark:text-white">Narrative Arcs</h2>
                </div>
                <div className="space-y-4">
                  {data.arcs.map((arc, idx) => (
                    <div 
                      key={idx} 
                      onClick={() => setFilterArcId(filterArcId === arc.id ? 'all' : arc.id)}
                      className={cn(
                        "p-4 border rounded-xl hover:shadow-md transition-all cursor-pointer",
                        filterArcId === arc.id ? "border-indigo-500 bg-indigo-50/50 dark:bg-indigo-900/20 shadow-sm" : "border-slate-100 dark:border-slate-800 bg-slate-50/50 dark:bg-slate-950/50"
                      )}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <h3 className="font-bold text-slate-900 dark:text-white text-base">{arc.title}</h3>
                        <div className={cn(
                          "px-2.5 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider",
                          arc.status === 'resolved' ? "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-400" : "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400"
                        )}>
                          {arc.status}
                        </div>
                      </div>
                      <p className="text-sm text-slate-600 dark:text-slate-400 leading-relaxed mb-3">{arc.summary}</p>
                      {Array.isArray(arc.involvedPlayers) && arc.involvedPlayers.length > 0 && (
                        <div className="flex flex-wrap gap-2">
                          {arc.involvedPlayers.map(playerId => {
                            const player = data.players.find(p => p.id === playerId);
                            if (!player) return null;
                            return (
                              <span key={playerId} className="inline-flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-300 rounded-md">
                                {player.type === 'company' ? <div className="w-1.5 h-1.5 rounded-full bg-blue-500" /> : <div className="w-1.5 h-1.5 rounded-full bg-emerald-500" />}
                                {player.name}
                              </span>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>

              {/* Insights */}
              <div className="bg-white dark:bg-slate-900 rounded-2xl shadow-sm border border-slate-200 dark:border-slate-800 p-6">
                <div className="flex items-center gap-2 mb-6">
                  <Eye className="w-5 h-5 text-indigo-600 dark:text-indigo-400" />
                  <h2 className="text-lg font-semibold text-slate-900 dark:text-white">Key Insights</h2>
                </div>
                <div className="space-y-4">
                  {data.insights.map((insight, idx) => {
                    let Icon = AlertCircle;
                    let colorClass = "text-indigo-600 dark:text-indigo-400 bg-indigo-50 dark:bg-indigo-900/20 border-indigo-100 dark:border-indigo-900/50";
                    
                    if (insight.type === 'who_is_winning') {
                      Icon = Trophy;
                      colorClass = "text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20 border-emerald-100 dark:border-emerald-900/50";
                    } else if (insight.type === 'turning_point') {
                      Icon = TrendingUp;
                      colorClass = "text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 border-amber-100 dark:border-amber-900/50";
                    } else if (insight.type === 'key_player') {
                      Icon = Users;
                      colorClass = "text-blue-700 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20 border-blue-100 dark:border-blue-900/50";
                    }

                    return (
                      <div key={idx} className={cn("p-4 rounded-xl border text-sm leading-relaxed flex gap-3", colorClass)}>
                        <Icon className="w-5 h-5 shrink-0 mt-0.5" />
                        <div>
                          <div className="text-xs font-bold uppercase tracking-wider mb-1 opacity-80">{insight.type.replace(/_/g, ' ')}</div>
                          <div className="opacity-90 mb-3">{insight.content}</div>
                          <CitationList citations={insight.citations} compact />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

            </div>
          </div>
        )}

        {/* Deep Dive Modal */}
        {deepDivePlayer && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-sm animate-in fade-in duration-200">
            <div className="bg-white dark:bg-slate-900 rounded-2xl shadow-xl w-full max-w-lg overflow-hidden flex flex-col max-h-[80vh] border border-slate-200 dark:border-slate-800">
              <div className="flex items-center justify-between p-4 border-b border-slate-100 dark:border-slate-800">
                <div className="flex items-center gap-3">
                  <div className={cn("w-10 h-10 rounded-full flex items-center justify-center text-white font-bold", deepDivePlayer.type === 'company' ? 'bg-blue-500' : 'bg-emerald-500')}>
                    {deepDivePlayer.name.charAt(0)}
                  </div>
                  <div>
                    <h3 className="font-bold text-slate-900 dark:text-white">{deepDivePlayer.name}</h3>
                    <p className="text-xs text-slate-500 dark:text-slate-400">{deepDivePlayer.role}</p>
                  </div>
                </div>
                <button onClick={() => setDeepDivePlayer(null)} className="p-2 text-slate-400 dark:text-slate-500 hover:text-slate-600 dark:hover:text-slate-300 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-full transition-colors">
                  <X className="w-5 h-5" />
                </button>
              </div>
              <div className="p-6 overflow-y-auto">
                {loadingDeepDive ? (
                  <div className="flex flex-col items-center justify-center py-12">
                    <Loader2 className="w-8 h-8 text-indigo-600 dark:text-indigo-400 animate-spin mb-4" />
                    <p className="text-sm text-slate-500 dark:text-slate-400">Generating deep dive profile...</p>
                  </div>
                ) : deepDiveContent ? (
                  <div className="space-y-5 text-sm text-slate-700 dark:text-slate-300">
                    <section>
                      <h4 className="font-semibold text-slate-900 dark:text-white mb-1">Summary</h4>
                      <p>{deepDiveContent.summary}</p>
                    </section>
                    <section>
                      <h4 className="font-semibold text-slate-900 dark:text-white mb-1">Role in Story</h4>
                      <p>{deepDiveContent.role_in_story}</p>
                    </section>
                    <section>
                      <h4 className="font-semibold text-slate-900 dark:text-white mb-2">Motivations</h4>
                      <ul className="list-disc pl-5 space-y-1">
                        {deepDiveContent.motivations?.map((motivation: string) => <li key={motivation}>{motivation}</li>)}
                      </ul>
                    </section>
                    <section className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                      <div>
                        <h4 className="font-semibold text-slate-900 dark:text-white mb-2">Alliances</h4>
                        <ul className="space-y-2">
                          {deepDiveContent.alliances?.map((alliance: { name: string; description: string }, idx: number) => <li key={`${alliance.name}-${idx}`}><span className="font-medium">{alliance.name}</span>: {alliance.description}</li>)}
                        </ul>
                      </div>
                      <div>
                        <h4 className="font-semibold text-slate-900 dark:text-white mb-2">Conflicts</h4>
                        <ul className="space-y-2">
                          {deepDiveContent.conflicts?.map((conflict: { name: string; description: string }, idx: number) => <li key={`${conflict.name}-${idx}`}><span className="font-medium">{conflict.name}</span>: {conflict.description}</li>)}
                        </ul>
                      </div>
                    </section>
                    <section>
                      <h4 className="font-semibold text-slate-900 dark:text-white mb-2">Timeline Contributions</h4>
                      <ul className="space-y-2">
                        {deepDiveContent.timeline_contributions?.map((item: { event: string; impact: string }, idx: number) => <li key={`${item.event}-${idx}`}><span className="font-medium">{item.event}</span>: {item.impact}</li>)}
                      </ul>
                    </section>
                    <section className="grid grid-cols-1 sm:grid-cols-[120px_1fr] gap-3 items-start">
                      <h4 className="font-semibold text-slate-900 dark:text-white">Risk Score</h4>
                      <p>{(deepDiveContent.risk_score * 100).toFixed(0)}%</p>
                      <h4 className="font-semibold text-slate-900 dark:text-white">Outlook</h4>
                      <p>{deepDiveContent.outlook}</p>
                    </section>
                    <section>
                      <h4 className="font-semibold text-slate-900 dark:text-white mb-2">Citations</h4>
                      <ul className="list-disc pl-5 space-y-1 text-slate-600 dark:text-slate-400">
                        {deepDiveContent.citations?.map((citation: string, idx: number) => <li key={`${citation}-${idx}`}>{citation}</li>)}
                      </ul>
                    </section>
                  </div>
                ) : (
                  <p className="text-sm text-slate-500 dark:text-slate-400">Failed to load profile.</p>
                )}
              </div>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
