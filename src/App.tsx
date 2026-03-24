import { useState, useCallback, useEffect, type FormEvent, useRef, useMemo } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer, ReferenceLine } from 'recharts';
import { ReactFlow, Background, Controls, MiniMap, useNodesState, useEdgesState, MarkerType, Handle, Position } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Loader2, Search, TrendingUp, Users, Clock, AlertCircle, Eye, Trophy, TrendingDown, ExternalLink, Tag, Download, X, Sun, Moon, Filter, Newspaper, Menu, ArrowUpRight } from 'lucide-react';
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

interface ProfileRelationship {
  player_id?: string | null;
  name: string;
  description: string;
  relationship_type?: Relationship['type'] | null;
  strength?: number | null;
  citations: Citation[];
}

interface TimelineContribution {
  event_id?: string | null;
  event: string;
  date?: string | null;
  impact: string;
  citations: Citation[];
}

interface RelatedPlayerContext {
  player_id: string;
  name: string;
  role: string;
  relationship_to_selected: string;
}

interface PlayerProfileData {
  id: string;
  name: string;
  summary: string;
  role_in_story: string;
  motivations: string[];
  alliances: ProfileRelationship[];
  conflicts: ProfileRelationship[];
  timeline_contributions: TimelineContribution[];
  risk_score: number;
  outlook: string;
  citations: Citation[];
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

const estimateReadMinutes = (text?: string) => {
  if (!text) return 1;
  const words = text.trim().split(/\s+/).length;
  return Math.max(1, Math.round(words / 180));
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


const buildProfileRequestContext = (storyData: StoryData, player: Player, currentTopic: string) => {
  const timelineSlice = storyData.timeline.filter((event) => event.playersInvolved.includes(player.id));
  const relationships = storyData.relationships.filter(
    (relationship) => relationship.source === player.id || relationship.target === player.id,
  );

  const playerNeighborhood: RelatedPlayerContext[] = relationships.map((relationship) => {
    const relatedPlayerId = relationship.source === player.id ? relationship.target : relationship.source;
    const relatedPlayer = storyData.players.find((candidate) => candidate.id === relatedPlayerId);

    return {
      player_id: relatedPlayerId,
      name: relatedPlayer?.name ?? relatedPlayerId,
      role: relatedPlayer?.role ?? 'Related player',
      relationship_to_selected: relationship.description,
    };
  });

  return {
    player_id: player.id,
    player_name: player.name,
    player_role: player.role,
    player_type: player.type,
    topic: currentTopic,
    timeline_slice: timelineSlice,
    relationships,
    player_neighborhood: playerNeighborhood,
  };
};

const ProfileListSection = ({
  title,
  emptyLabel,
  items,
}: {
  title: string;
  emptyLabel: string;
  items: ProfileRelationship[];
}) => {
  return (
    <section>
      <h4 className="font-semibold text-slate-900 dark:text-white mb-2">{title}</h4>
      {items.length > 0 ? (
        <ul className="space-y-3">
          {items.map((item, idx) => (
            <li key={`${item.name}-${idx}`} className="rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50/70 dark:bg-slate-950/40 p-3">
              <div className="flex items-center justify-between gap-3">
                <span className="font-medium text-slate-900 dark:text-white">{item.name}</span>
                {typeof item.strength === 'number' && (
                  <span className="text-xs text-slate-500 dark:text-slate-400">Strength {(item.strength * 100).toFixed(0)}%</span>
                )}
              </div>
              <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">{item.description}</p>
              <CitationList citations={item.citations} compact />
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-slate-500 dark:text-slate-400">{emptyLabel}</p>
      )}
    </section>
  );
};

const TimelineContributionSection = ({ items }: { items: TimelineContribution[] }) => {
  return (
    <section>
      <h4 className="font-semibold text-slate-900 dark:text-white mb-2">Timeline Contributions</h4>
      {items.length > 0 ? (
        <ul className="space-y-3">
          {items.map((item, idx) => (
            <li key={`${item.event}-${idx}`} className="rounded-xl border border-slate-200 dark:border-slate-800 bg-slate-50/70 dark:bg-slate-950/40 p-3">
              <div className="flex items-center justify-between gap-3">
                <span className="font-medium text-slate-900 dark:text-white">{item.event}</span>
                {item.date && <span className="text-xs text-slate-500 dark:text-slate-400">{item.date}</span>}
              </div>
              <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">{item.impact}</p>
              <CitationList citations={item.citations} compact />
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-slate-500 dark:text-slate-400">No timeline contributions were identified.</p>
      )}
    </section>
  );
};

const LOADING_MESSAGES = [
  "Searching the web for the latest data...",
  "Extracting timeline and sources...",
  "Mapping key players and relationships...",
  "Analyzing sentiment and impact...",
  "Finalizing visual narrative..."
];

const EDITORIAL_CATEGORIES = ['World', 'Business', 'Tech', 'Policy', 'Culture'];

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
  const [deepDiveCache, setDeepDiveCache] = useState<Record<string, PlayerProfileData>>({});
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
  const accentColor = isDarkMode ? '#d33a3f' : '#b61f24';

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

  const leadEvent = useMemo(() => {
    if (!data) return null;
    return filteredTimeline[0] ?? data.timeline[0] ?? null;
  }, [data, filteredTimeline]);

  const sideEvents = useMemo(() => {
    if (!data) return [];
    return (filteredTimeline.length > 1 ? filteredTimeline : data.timeline).slice(1, 3);
  }, [data, filteredTimeline]);

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

    const cacheKey = `${topic}::${player.id}`;
    const cachedProfile = deepDiveCache[cacheKey];

    setDeepDivePlayer(player);

    if (cachedProfile) {
      setDeepDiveContent(cachedProfile);
      setLoadingDeepDive(false);
      return;
    }

    setDeepDiveContent(null);
    setLoadingDeepDive(true);

    try {
      const response = await fetch('/api/player-profile', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(buildProfileRequestContext(data, player, topic)),
      });

      if (!response.ok) {
        throw new Error(`API error: ${response.statusText}`);
      }

      const profile: PlayerProfileData = await response.json();
      setDeepDiveCache((currentCache) => ({
        ...currentCache,
        [cacheKey]: profile,
      }));
      setDeepDiveContent(profile);
    } catch (err) {
      console.error(err);
      setDeepDiveContent(null);
    } finally {
      setLoadingDeepDive(false);
    }
  }, [data, deepDiveCache, topic]);

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
    setDeepDivePlayer(null);
    setDeepDiveContent(null);
    setDeepDiveCache({});
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
    <div className="editorial-shell selection:bg-red-200/60 selection:text-black dark:selection:bg-red-900/40 dark:selection:text-white">
      {/* Header */}
      <header className="sticky top-0 z-20 px-3 py-4 md:px-6">
        <div className="editorial-frame">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_auto_1fr] lg:items-center">
            <div className="flex items-center gap-3">
              <span className="editorial-pill bg-black text-white dark:bg-white dark:text-black">No 5,810</span>
              <div className="hidden items-center gap-3 text-xs text-[var(--muted-fg)] md:flex">
                {EDITORIAL_CATEGORIES.map((category) => (
                  <button key={category} className="hover:text-[var(--page-fg)] transition-colors">
                    {category}
                  </button>
                ))}
              </div>
            </div>

            <div className="text-center">
              <p className="text-4xl tracking-tight md:text-5xl editorial-display">The Viewisland</p>
              {data?.fetched_at && (
                <p className="editorial-meta mt-1">Updated {formatDateTime(data.fetched_at)}</p>
              )}
            </div>

            <div className="flex items-center justify-end gap-2">
              <button className="editorial-pill">
                Subscribe for EUR2.50
                <ArrowUpRight className="h-3.5 w-3.5" />
              </button>
              {data && (
                <button
                  onClick={handleDownload}
                  disabled={isDownloading}
                  className="editorial-pill disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isDownloading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
                  Export
                </button>
              )}
              <button
                onClick={() => setIsDarkMode(!isDarkMode)}
                className="editorial-pill"
                aria-label="Toggle dark mode"
              >
                {isDarkMode ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
              </button>
              <button className="editorial-pill" aria-label="Open menu">
                <Menu className="h-4 w-4" />
              </button>
            </div>
          </div>

          <div className="mt-4 border-y py-2 editorial-divider">
            <form onSubmit={handleSearchSubmit} className="relative">
              <div className="relative flex items-center gap-2">
                <Search className="absolute left-3 w-4 h-4 text-[var(--muted-fg)]" />
                <input
                  type="text"
                  value={topic}
                  onChange={(e) => setTopic(e.target.value)}
                  placeholder="Search a narrative: OpenAI board saga, Nvidia AI dominance..."
                  className="w-full rounded-full border bg-[var(--surface-strong)] py-3 pl-10 pr-28 text-sm outline-none transition-colors focus:border-[var(--line-strong)]"
                  style={{ borderColor: 'var(--line)' }}
                  disabled={loading}
                />
                <button
                  type="submit"
                  disabled={loading || !topic.trim()}
                  className="absolute right-1.5 rounded-full bg-black px-4 py-1.5 text-xs font-medium text-white transition-colors hover:bg-neutral-800 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-white dark:text-black dark:hover:bg-neutral-200"
                >
                  {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Analyze'}
                </button>
              </div>
            </form>
          </div>

          <div className="editorial-ticker">
            <span className="italic">Start the day here</span>
            <span className="truncate">AI policy shifts accelerate, earnings optimism rises, and strategic alliances redraw competitive narratives.</span>
            <ArrowUpRight className="h-3.5 w-3.5 shrink-0" />
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="mx-auto w-full max-w-[1200px] px-3 pb-10 pt-2 md:px-6">
        
        {/* Empty State */}
        {!data && !loading && !error && (
          <div className="flex flex-col items-center justify-center h-[60vh] text-center max-w-2xl mx-auto">
            <div className="w-20 h-20 rounded-full flex items-center justify-center mb-6 border" style={{ backgroundColor: 'var(--surface-muted)', borderColor: 'var(--line)' }}>
              <TrendingUp className="w-10 h-10 text-[var(--accent)]" />
            </div>
            <p className="editorial-kicker mb-3">Narrative analysis desk</p>
            <h2 className="text-5xl editorial-headline mb-4">Track The Narrative Arc</h2>
            <p className="text-lg mb-8 max-w-xl" style={{ color: 'var(--muted-fg)' }}>
              Enter a complex business story or topic above. We'll use AI to extract the timeline, map the key players, analyze sentiment shifts, and predict what happens next.
            </p>
            <div className="flex flex-wrap justify-center gap-3">
              {['The OpenAI board saga', 'Nvidia\'s AI dominance', 'The Boeing safety crisis'].map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => fetchData(suggestion)}
                  className="editorial-pill transition-all hover:-translate-y-0.5"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Error State */}
        {error && (
          <div className="editorial-surface p-4 flex items-start gap-3 text-red-800 dark:text-red-300 border-red-300/70 dark:border-red-900/70">
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
            <Loader2 className="w-12 h-12 text-[var(--accent)] animate-spin mb-6" />
            <p className="font-medium text-lg animate-pulse" style={{ color: 'var(--muted-fg)' }}>
              {LOADING_MESSAGES[loadingMsgIdx]}
            </p>
          </div>
        )}

        {/* Dashboard */}
        {data && !loading && (
          <div id="dashboard-content" className="space-y-8 animate-in fade-in duration-500">

            {/* Editorial Lead Row */}
            <section className="grid grid-cols-1 gap-4 xl:grid-cols-[2fr_1fr]">
              <article className="editorial-surface overflow-hidden">
                <div className="grid grid-cols-1 lg:grid-cols-[1.35fr_1fr]">
                  <div className="p-6 md:p-8 flex flex-col gap-5">
                    <p className="editorial-kicker">Frontline Narrative</p>
                    <h2 className="editorial-headline">
                      {(leadEvent?.title ?? topic) || 'Strategic shifts reshape the arc'}
                    </h2>
                    <p className="text-base leading-relaxed" style={{ color: 'var(--muted-fg)' }}>
                      {leadEvent?.description ?? 'Run an analysis to produce a complete narrative timeline, key players, and emergent strategic arcs.'}
                    </p>
                    <div className="flex flex-wrap items-center gap-3 text-sm">
                      <span className="editorial-pill">{leadEvent?.date ?? 'Now'}</span>
                      <span className="editorial-pill">{estimateReadMinutes(leadEvent?.description)} min read</span>
                      {leadEvent?.impact && <span className="editorial-pill">Impact {leadEvent.impact}</span>}
                    </div>
                  </div>
                  <div className="border-l editorial-divider p-6 md:p-8 flex flex-col gap-4" style={{ backgroundColor: 'var(--surface-muted)' }}>
                    <div>
                      <p className="editorial-kicker">Byline</p>
                      <p className="text-lg">{data.players[0]?.name ?? 'Editorial desk'}</p>
                      <p className="editorial-meta mt-1">{formatDateTime(data.fetched_at ?? undefined)}</p>
                    </div>
                    <div className="grid grid-cols-2 gap-3 text-sm">
                      <div className="editorial-surface p-3">
                        <p className="editorial-kicker">Events</p>
                        <p className="text-3xl leading-none mt-2">{data.timeline.length}</p>
                      </div>
                      <div className="editorial-surface p-3">
                        <p className="editorial-kicker">Players</p>
                        <p className="text-3xl leading-none mt-2">{data.players.length}</p>
                      </div>
                    </div>
                    <p className="text-sm" style={{ color: 'var(--muted-fg)' }}>
                      The analysis below remains fully interactive with filters, graph exploration, and deep-dive profiles.
                    </p>
                  </div>
                </div>
              </article>

              <aside className="space-y-4">
                {sideEvents.map((event) => (
                  <article key={event.id} className="editorial-surface p-5">
                    <p className="editorial-kicker">Supporting Story</p>
                    <h3 className="mt-2 text-3xl leading-none">{event.title}</h3>
                    <p className="mt-3 text-sm leading-relaxed" style={{ color: 'var(--muted-fg)' }}>{event.description}</p>
                    <div className="mt-4 flex items-center justify-between text-xs" style={{ color: 'var(--muted-fg)' }}>
                      <span>{event.date}</span>
                      <span>{estimateReadMinutes(event.description)} min read</span>
                    </div>
                  </article>
                ))}

                <article className="editorial-surface p-5">
                  <p className="editorial-kicker">Desk Snapshot</p>
                  <div className="mt-3 grid grid-cols-2 gap-3">
                    <div className="rounded-xl border p-3" style={{ borderColor: 'var(--line)' }}>
                      <p className="text-xs" style={{ color: 'var(--muted-fg)' }}>Arcs</p>
                      <p className="text-2xl mt-2 leading-none">{data.arcs.length}</p>
                    </div>
                    <div className="rounded-xl border p-3" style={{ borderColor: 'var(--line)' }}>
                      <p className="text-xs" style={{ color: 'var(--muted-fg)' }}>Insights</p>
                      <p className="text-2xl mt-2 leading-none">{data.insights.length}</p>
                    </div>
                  </div>
                </article>
              </aside>
            </section>
            
            {/* Filter Bar */}
            <div className="editorial-surface p-4 flex flex-wrap items-center gap-4">
              <div className="flex items-center gap-2 text-[var(--muted-fg)] font-medium text-sm mr-2">
                <Filter className="w-4 h-4" /> Desk Filters:
              </div>
              
              <select 
                value={filterPlayerId} 
                onChange={(e) => setFilterPlayerId(e.target.value)}
                className="rounded-lg border p-2 text-sm"
                style={{ backgroundColor: 'var(--surface)', borderColor: 'var(--line)', color: 'var(--page-fg)' }}
              >
                <option value="all">All Players (Focus Mode)</option>
                {data.players.map(p => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>

              <select 
                value={filterArcId} 
                onChange={(e) => setFilterArcId(e.target.value)}
                className="rounded-lg border p-2 text-sm"
                style={{ backgroundColor: 'var(--surface)', borderColor: 'var(--line)', color: 'var(--page-fg)' }}
              >
                <option value="all">All Arcs</option>
                {data.arcs.map(a => (
                  <option key={a.id} value={a.id}>{a.title}</option>
                ))}
              </select>

              <select 
                value={filterImpact} 
                onChange={(e) => setFilterImpact(e.target.value)}
                className="rounded-lg border p-2 text-sm"
                style={{ backgroundColor: 'var(--surface)', borderColor: 'var(--line)', color: 'var(--page-fg)' }}
              >
                <option value="all">All Impacts</option>
                <option value="high">High Impact</option>
                <option value="medium">Medium Impact</option>
                <option value="low">Low Impact</option>
              </select>

              <select 
                value={highlightRelType} 
                onChange={(e) => setHighlightRelType(e.target.value as any)}
                className="rounded-lg border p-2 text-sm"
                style={{ backgroundColor: 'var(--surface)', borderColor: 'var(--line)', color: 'var(--page-fg)' }}
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
                  className="ml-auto text-sm font-medium"
                  style={{ color: 'var(--accent)' }}
                >
                  Clear Filters
                </button>
              )}
            </div>

            {/* Top Row: Timeline & Sentiment */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
              
              {/* Timeline */}
              <div className="lg:col-span-1 editorial-surface p-6 flex flex-col h-[500px]">
                <div className="flex items-center justify-between mb-6">
                  <div className="flex items-center gap-2">
                    <Clock className="w-5 h-5 text-[var(--accent)]" />
                    <h2 className="text-3xl leading-none">Chronological Timeline</h2>
                  </div>
                  <div className="flex items-center gap-3">
                    <button 
                      onClick={() => setSortOrder(prev => prev === 'asc' ? 'desc' : 'asc')}
                      className="text-xs font-medium flex items-center gap-1"
                      style={{ color: 'var(--accent)' }}
                    >
                      {sortOrder === 'asc' ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                      {sortOrder === 'asc' ? 'Oldest First' : 'Newest First'}
                    </button>
                    <span className="text-xs hidden sm:inline" style={{ color: 'var(--muted-fg)' }}>Click event to highlight map</span>
                  </div>
                </div>
                <div ref={timelineRef} className="flex-1 overflow-y-auto pr-4 space-y-6 scrollbar-thin scrollbar-thumb-slate-200 dark:scrollbar-thumb-slate-700">
                  {filteredTimeline.map((event, idx) => (
                    <div 
                      key={idx} 
                      onClick={() => handleTimelineClick(event)}
                      className={cn(
                        "relative pl-6 pb-6 last:pb-0 border-l-2 last:border-transparent cursor-pointer transition-colors rounded-r-xl -ml-2 pl-8",
                        (activeEventIdx === idx || selectedEventId === event.id) ? "ring-1" : "hover:opacity-85"
                      )}
                      style={{
                        borderLeftColor: 'var(--line)',
                        backgroundColor: (activeEventIdx === idx || selectedEventId === event.id) ? 'var(--surface-muted)' : 'transparent',
                        borderColor: (activeEventIdx === idx || selectedEventId === event.id) ? 'var(--line)' : 'transparent'
                      }}
                    >
                      <div className={cn(
                        "absolute -left-[11px] top-1 w-5 h-5 rounded-full border-4 border-white dark:border-slate-900 transition-transform",
                        (activeEventIdx === idx || selectedEventId === event.id) && "scale-125",
                        event.sentiment === 'positive' ? "bg-emerald-500" :
                        event.sentiment === 'negative' ? "bg-red-500" : "bg-slate-400 dark:bg-slate-500"
                      )} />
                      <div className="flex items-center justify-between gap-2 mb-1">
                        <div className="text-xs font-bold" style={{ color: 'var(--accent)' }}>{event.date}</div>
                        <div className="flex items-center gap-1 text-[10px] font-medium px-2 py-0.5 bg-slate-100 dark:bg-slate-800 text-slate-600 dark:text-slate-300 rounded-full">
                          <Tag className="w-3 h-3" />
                          Impact: {event.impact}
                        </div>
                      </div>
                      <div className="font-semibold mb-1">{event.title}</div>
                      <div className="text-sm leading-relaxed mb-3" style={{ color: 'var(--muted-fg)' }}>{event.description}</div>
                      <CitationList citations={event.citations} compact />
                    </div>
                  ))}
                </div>
              </div>

              {/* Sentiment Chart */}
              <div className="lg:col-span-2 editorial-surface p-6 flex flex-col h-[500px]">
                <div className="flex items-center justify-between mb-6">
                  <div className="flex items-center gap-2">
                    <TrendingUp className="w-5 h-5 text-[var(--accent)]" />
                    <h2 className="text-3xl leading-none">Sentiment Shifts</h2>
                  </div>
                  <span className="text-xs" style={{ color: 'var(--muted-fg)' }}>Hover to locate on timeline</span>
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
                        tick={{ fontSize: 12, fill: isDarkMode ? '#b8b1a4' : '#696356' }}
                        dy={10}
                      />
                      <YAxis 
                        domain={[-100, 100]} 
                        axisLine={false}
                        tickLine={false}
                        tick={{ fontSize: 12, fill: isDarkMode ? '#b8b1a4' : '#696356' }}
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
                        stroke={accentColor}
                        strokeWidth={3}
                        dot={{ r: 4, fill: accentColor, strokeWidth: 2, stroke: isDarkMode ? '#1a1917' : '#ffffff' }}
                        activeDot={{ r: 6, fill: accentColor, stroke: isDarkMode ? '#1a1917' : '#ffffff', strokeWidth: 2 }}
                        animationDuration={1500}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>
            </div>

            {/* Middle Row: Key Players Map */}
            <div className="editorial-surface p-6 h-[600px] flex flex-col">
              <div className="flex items-center gap-2 mb-4">
                <Users className="w-5 h-5 text-[var(--accent)]" />
                <h2 className="text-3xl leading-none">Key Players & Relationships</h2>
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
              <div className="flex items-center gap-6 mt-4 text-sm justify-center" style={{ color: 'var(--muted-fg)' }}>
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
              <div className="md:col-span-2 editorial-surface p-6">
                <div className="flex items-center gap-2 mb-6">
                  <TrendingUp className="w-5 h-5 text-[var(--accent)]" />
                  <h2 className="text-3xl leading-none">Narrative Arcs</h2>
                </div>
                <div className="space-y-4">
                  {data.arcs.map((arc, idx) => (
                    <div 
                      key={idx} 
                      onClick={() => setFilterArcId(filterArcId === arc.id ? 'all' : arc.id)}
                      className={cn(
                        "p-4 border rounded-xl hover:shadow-md transition-all cursor-pointer",
                            filterArcId === arc.id ? "shadow-sm" : ""
                      )}
                          style={{
                            borderColor: filterArcId === arc.id ? 'var(--line-strong)' : 'var(--line)',
                            backgroundColor: filterArcId === arc.id ? 'var(--surface-muted)' : 'var(--surface)'
                          }}
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
              <div className="editorial-surface p-6">
                <div className="flex items-center gap-2 mb-6">
                  <Eye className="w-5 h-5 text-[var(--accent)]" />
                  <h2 className="text-3xl leading-none">Key Insights</h2>
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
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm animate-in fade-in duration-200">
            <div className="editorial-surface w-full max-w-lg overflow-hidden flex flex-col max-h-[80vh]">
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
                    <Loader2 className="w-8 h-8 animate-spin mb-4" style={{ color: 'var(--accent)' }} />
                    <p className="text-sm" style={{ color: 'var(--muted-fg)' }}>Generating deep dive profile...</p>
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
                      <ProfileListSection
                        title="Alliances"
                        emptyLabel="No alliances were identified."
                        items={deepDiveContent.alliances ?? []}
                      />
                      <ProfileListSection
                        title="Conflicts"
                        emptyLabel="No conflicts were identified."
                        items={deepDiveContent.conflicts ?? []}
                      />
                    </section>
                    <TimelineContributionSection items={deepDiveContent.timeline_contributions ?? []} />
                    <section className="grid grid-cols-1 sm:grid-cols-[120px_1fr] gap-3 items-start">
                      <h4 className="font-semibold text-slate-900 dark:text-white">Risk Score</h4>
                      <p>{(deepDiveContent.risk_score * 100).toFixed(0)}%</p>
                      <h4 className="font-semibold text-slate-900 dark:text-white">Outlook</h4>
                      <p>{deepDiveContent.outlook}</p>
                    </section>
                    <section>
                      <h4 className="font-semibold text-slate-900 dark:text-white mb-2">Citations</h4>
                      <CitationList citations={deepDiveContent.citations} />
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
