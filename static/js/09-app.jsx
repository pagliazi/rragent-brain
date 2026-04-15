// ── App ──────────────────────────────────────────────

function App() {
  const [authUser, setAuthUser] = useState(null);
  const [authChecked, setAuthChecked] = useState(false);
  const [agents, setAgents] = useState({});
  const [channels, setChannels] = useState({});
  const [currentView, setCurrentView] = useState('dashboard');
  const [chatTarget, setChatTarget] = useState('manager');
  const [isThinking, setIsThinking] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [splitView, setSplitView] = useState(false);
  const [splitPanel, setSplitPanel] = useState('system');
  const [tokenSession, setTokenSession] = useState(() => createTokenSession());
  const [activeToolCalls, setActiveToolCalls] = useState([]);
  const [thinkingText, setThinkingText] = useState('');
  const pendingSend = useRef(null);

  useEffect(() => {
    const token = getToken();
    if (!token) { setAuthChecked(true); return; }
    fetch('/api/auth/me', {headers:{'Authorization':'Bearer '+token}})
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data && data.username) setAuthUser(data); else setToken(''); })
      .catch(() => setToken(''))
      .finally(() => setAuthChecked(true));
  }, []);

  useEffect(() => {
    window.__onAuthExpired = () => { setToken(''); setAuthUser(null); };
    return () => { window.__onAuthExpired = null; };
  }, []);

  const handleLogin = useCallback((token, user) => {
    setToken(token);
    setAuthUser(user);
  }, []);

  const handleLogout = useCallback(() => {
    setToken('');
    setAuthUser(null);
    setCurrentView('dashboard');
  }, []);

  const handleUpdateUser = useCallback((updated) => {
    setAuthUser(prev => ({...prev, ...updated}));
  }, []);

  const [conversations, setConversations] = useState(() => {
    try { const s = localStorage.getItem('rragent_convs_v2'); return s ? JSON.parse(s) : []; } catch(e) { return []; }
  });
  const [activeConvId, setActiveConvId] = useState(() => {
    try { return localStorage.getItem('rragent_active_conv') || ''; } catch(e) { return ''; }
  });

  useEffect(() => {
    try { localStorage.setItem('rragent_convs_v2', JSON.stringify(conversations)); } catch(e) {}
  }, [conversations]);

  useEffect(() => {
    try { localStorage.setItem('rragent_active_conv', activeConvId); } catch(e) {}
  }, [activeConvId]);

  const activeMessages = useMemo(() => {
    const conv = conversations.find(c => c.id === activeConvId);
    return conv?.messages || [];
  }, [conversations, activeConvId]);

  const createConversation = useCallback((target) => {
    const id = genId();
    const conv = {id, title:'', target: target || chatTarget, messages:[], createdAt:Date.now(), updatedAt:Date.now()};
    setConversations(prev => [conv, ...prev]);
    setActiveConvId(id);
    return id;
  }, [chatTarget]);

  const handleNewConv = useCallback(() => {
    createConversation(chatTarget);
  }, [createConversation, chatTarget]);

  const handleSelectConv = useCallback((id) => {
    setActiveConvId(id);
    const conv = conversations.find(c => c.id === id);
    if (conv?.target) setChatTarget(conv.target);
  }, [conversations]);

  const handleDeleteConv = useCallback((id) => {
    setConversations(prev => prev.filter(c => c.id !== id));
    if (activeConvId === id) {
      const remaining = conversations.filter(c => c.id !== id);
      setActiveConvId(remaining[0]?.id || '');
    }
  }, [activeConvId, conversations]);

  const refreshOverview = useCallback(async () => {
    try {
      const data = await apiGet('/api/overview');
      if (data && !data.error) {
        setAgents(data.agents||{});
        setChannels(data.channels||{});
      }
    } catch(e) {}
  }, []);

  useEffect(() => {
    refreshOverview();
    const i = setInterval(refreshOverview, 15000);
    return () => clearInterval(i);
  }, [refreshOverview]);

  const handleSend = useCallback(async (text) => {
    if (currentView !== 'chat') {
      pendingSend.current = text;
      setCurrentView('chat');
      return;
    }

    let convId = activeConvId;
    if (!convId || !conversations.find(c => c.id === convId)) {
      convId = createConversation(chatTarget);
    }

    let target = chatTarget;
    let actualText = text;
    const mentionMatch = text.match(/^@(\w+)\s+([\s\S]*)/);
    if (mentionMatch && CHAT_TARGETS[mentionMatch[1]]) {
      target = mentionMatch[1];
      actualText = mentionMatch[2];
    }

    const userMsg = {role:'user', content: text, ts:Date.now(), target: target};
    setConversations(prev => prev.map(c => c.id === convId ? {
      ...c,
      messages: [...c.messages, userMsg],
      title: c.title || text.slice(0, 30),
      updatedAt: Date.now(),
    } : c));
    setIsThinking(true);
    setActiveToolCalls([]);
    setThinkingText('');

    let fullContent = '';
    let source = target;
    const toolTimers = {};
    try {
      await streamChat(actualText, target, function(event) {
        if (event.type === 'chunk') fullContent += event.content;
        else if (event.type === 'thinking') {
          setThinkingText(prev => prev + (event.content || ''));
        }
        else if (event.type === 'tool_start') {
          toolTimers[event.name] = Date.now();
          setActiveToolCalls(prev => [...prev, {name: event.name, status: 'running', startTime: Date.now(), content: null, duration: null}]);
        }
        else if (event.type === 'tool_result') {
          const elapsed = toolTimers[event.name] ? Date.now() - toolTimers[event.name] : null;
          setActiveToolCalls(prev => prev.map(tc =>
            tc.name === event.name && tc.status === 'running'
              ? {...tc, status: event.is_error ? 'error' : 'done', content: event.content || '', duration: elapsed}
              : tc
          ));
        }
        else if (event.type === 'usage') {
          setTokenSession(prev => updateTokenSession(prev, event));
        }
        else if (event.type === 'error') fullContent = event.content;
        if (event.source) source = event.source;
      });
    } catch (e) { fullContent = '网络错误: ' + e.message; }

    if (!fullContent) fullContent = '未收到回复，请检查 Agent 状态';
    const assistantMsg = {role:'assistant', content: fullContent, ts:Date.now(), source: source};
    setConversations(prev => prev.map(c => c.id === convId ? {
      ...c,
      messages: [...c.messages, assistantMsg],
      updatedAt: Date.now(),
    } : c));
    setIsThinking(false);
    setActiveToolCalls([]);
    setThinkingText('');
  }, [currentView, chatTarget, activeConvId, conversations, createConversation]);

  useEffect(() => {
    if (currentView === 'chat' && pendingSend.current) {
      const t = pendingSend.current;
      pendingSend.current = null;
      setTimeout(() => handleSend(t), 50);
    }
  }, [currentView, handleSend]);

  if (!authChecked) {
    return <div className="h-screen bg-surface-0 flex items-center justify-center"><Spinner size={8} /></div>;
  }

  if (!authUser) {
    return <ToastProvider><LoginPage onLogin={handleLogin} /></ToastProvider>;
  }

  const canWrite = authUser.role !== 'viewer';

  return (
    <ToastProvider>
      <AuthContext.Provider value={authUser}>
        <div className="flex flex-col md:flex-row h-screen bg-surface-0 overflow-x-hidden max-w-[100vw]">
          <NavRail currentView={currentView} onViewChange={(v) => { setCurrentView(v); if (v !== 'chat' && v !== 'dashboard') setSplitPanel(v); }} agents={agents} user={authUser} onLogout={handleLogout} splitView={splitView} onToggleSplit={() => setSplitView(!splitView)} />
          <div className="flex-1 flex h-full min-w-0 pb-14 md:pb-0" key={splitView ? 'split' : currentView}>
            {splitView && currentView === 'chat' ? (
              <>
                <div className="flex-1 flex h-full min-w-0 border-r border-border">
                  <ChatView
                    conversations={conversations} activeConvId={activeConvId} messages={activeMessages}
                    onSend={canWrite ? handleSend : null} isThinking={isThinking}
                    chatTarget={chatTarget} onChatTargetChange={setChatTarget}
                    sidebarCollapsed={sidebarCollapsed} onToggleSidebar={()=>setSidebarCollapsed(!sidebarCollapsed)}
                    onNewConv={handleNewConv} onSelectConv={handleSelectConv} onDeleteConv={handleDeleteConv}
                    agents={agents} activeToolCalls={activeToolCalls} thinkingText={thinkingText} tokenSession={tokenSession} />
                </div>
                <div className="hidden md:flex w-[45%] h-full min-w-0 overflow-hidden">
                  {splitPanel === 'system' && canWrite && <SystemView />}
                  {splitPanel === 'quant' && <QuantView />}
                  {splitPanel === 'market' && <MarketView />}
                  {splitPanel === 'api_usage' && <ApiUsageView />}
                  {splitPanel === 'infra_monitor' && <InfraMonitorView />}
                  {splitPanel === 'agent_skills' && <AgentSkillsView />}
                  {splitPanel === 'tools' && <ToolsView />}
                  {splitPanel === 'news' && <NewsView />}
                  {splitPanel === 'logs' && <DailyLogView />}
                  {!['system','quant','market','api_usage','infra_monitor','agent_skills','tools','news','logs'].includes(splitPanel) && <SystemView />}
                </div>
              </>
            ) : (
              <>
                {currentView === 'dashboard' && <DashboardView agents={agents} channels={channels} onViewChange={setCurrentView} onSend={canWrite ? handleSend : null} />}
                {currentView === 'chat' && <ChatView
                  conversations={conversations} activeConvId={activeConvId} messages={activeMessages}
                  onSend={canWrite ? handleSend : null} isThinking={isThinking}
                  chatTarget={chatTarget} onChatTargetChange={setChatTarget}
                  sidebarCollapsed={sidebarCollapsed} onToggleSidebar={()=>setSidebarCollapsed(!sidebarCollapsed)}
                  onNewConv={handleNewConv} onSelectConv={handleSelectConv} onDeleteConv={handleDeleteConv}
                  agents={agents} activeToolCalls={activeToolCalls} thinkingText={thinkingText} tokenSession={tokenSession} />}
                {currentView === 'market' && <MarketView />}
                {currentView === 'tasks' && <TasksView />}
                {currentView === 'quant' && <QuantView />}
                {currentView === 'intraday' && <IntradayView />}
                {currentView === 'news' && <NewsView />}
                {currentView === 'tools' && <ToolsView />}
                {currentView === 'apple' && <AppleView />}
                {currentView === 'autoresearch' && <AutoResearchView />}
                {currentView === 'devops' && canWrite && <DevView />}
                {currentView === 'api_usage' && <ApiUsageView />}
                {currentView === 'infra_monitor' && <InfraMonitorView />}
                {currentView === 'agent_skills' && <AgentSkillsView />}
                {currentView === 'system' && canWrite && <ErrorBoundary><SystemView /></ErrorBoundary>}
                {currentView === 'logs' && <DailyLogView />}
                {currentView === 'profile' && <ProfileView user={authUser} onUpdateUser={handleUpdateUser} />}
                {currentView === 'admin' && authUser.role === 'admin' && <AdminView currentUser={authUser} />}
                {(currentView === 'system' || currentView === 'devops') && !canWrite && (
                  <div className="flex-1 flex items-center justify-center text-zinc-500 text-sm">访客无权访问此页面</div>
                )}
              </>
            )}
          </div>
        </div>
      </AuthContext.Provider>
    </ToastProvider>
  );
}

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);
