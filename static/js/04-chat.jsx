// ── Chat Conversation Sidebar ────────────────────────

function ChatSidebar({conversations, activeId, onSelect, onCreate, onDelete, collapsed, onToggle}) {
  return (
    <div className={`h-full bg-surface-1 border-r border-border flex flex-col flex-shrink-0 transition-all duration-200 ${collapsed ? 'w-0 overflow-hidden border-0' : 'hidden md:flex w-[260px]'}`}>
      <div className="p-3 flex items-center gap-2">
        <button onClick={onCreate}
          className="btn flex-1 flex items-center justify-center gap-2 px-3 py-2.5 bg-surface-2 hover:bg-surface-3 border border-border hover:border-border-light rounded-xl text-[13px] text-zinc-300 font-medium transition">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>
          新对话
        </button>
        <button onClick={onToggle} className="p-2 hover:bg-surface-3 rounded-lg text-zinc-500 hover:text-zinc-300 transition" title="收起">
          <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><path d="M11 19l-7-7 7-7"/></svg>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 pb-2">
        {conversations.length === 0 && (
          <div className="text-center py-8 text-zinc-600 text-[12px]">暂无对话</div>
        )}
        {conversations.map(conv => {
          const meta = getChatMeta(conv.target);
          return (
            <div key={conv.id}
              className={`chat-sidebar-item group flex items-center gap-2.5 px-3 py-2.5 rounded-xl mb-0.5 cursor-pointer ${activeId===conv.id ? 'active bg-brand-600/10' : ''}`}
              onClick={() => onSelect(conv.id)}>
              <span className="text-sm flex-shrink-0">{meta.icon}</span>
              <div className="flex-1 min-w-0">
                <div className={`text-[13px] truncate ${activeId===conv.id?'text-brand-400 font-medium':'text-zinc-300'}`}>
                  {conv.title || '新对话'}
                </div>
                <div className="text-[10px] text-zinc-600 truncate">{conv.messages?.length || 0} 条消息</div>
              </div>
              <button onClick={(e)=>{e.stopPropagation();onDelete(conv.id);}}
                className="opacity-0 group-hover:opacity-100 p-1 hover:bg-surface-4 rounded text-zinc-600 hover:text-red-400 transition">
                <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><path d="M18 6L6 18M6 6l12 12"/></svg>
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}


// ── Tool Call Card ───────────────────────────────────

function ToolCallCard({tool}) {
  const [expanded, setExpanded] = useState(false);
  const statusIcon = tool.status === 'running' ? null : tool.status === 'error' ? '✗' : '✓';
  const statusColor = tool.status === 'running' ? 'border-brand-500/40 bg-brand-600/5' : tool.status === 'error' ? 'border-red-500/30 bg-red-500/5' : 'border-emerald-500/30 bg-emerald-500/5';
  const headerColor = tool.status === 'running' ? 'text-brand-400' : tool.status === 'error' ? 'text-red-400' : 'text-emerald-400';
  const duration = tool.duration != null ? (tool.duration / 1000).toFixed(1) + 's' : tool.status === 'running' ? '...' : '';
  const contentLen = tool.content ? tool.content.length.toLocaleString() + ' chars' : '';

  return (
    <div className={`my-2 rounded-xl border ${statusColor} overflow-hidden animate-fade-in transition-all`}>
      <button onClick={() => tool.content && setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-surface-3/30 transition">
        <span className="text-xs">🔧</span>
        {tool.status === 'running' ? (
          <span className="flex-shrink-0"><Spinner size={3} /></span>
        ) : (
          <span className={`text-xs font-bold ${headerColor} flex-shrink-0`}>{statusIcon}</span>
        )}
        <span className="text-[12px] font-mono text-zinc-300 truncate flex-1">{tool.name || 'tool'}</span>
        {duration && <span className="text-[10px] text-zinc-500 tabular-nums flex-shrink-0">{duration}</span>}
        {tool.status !== 'running' && contentLen && (
          <span className="text-[10px] text-zinc-600 flex-shrink-0">{contentLen}</span>
        )}
        {tool.content && (
          <span className={`text-[10px] ${headerColor} flex-shrink-0`}>{expanded ? '收起 ▴' : '展开 ▾'}</span>
        )}
      </button>
      {expanded && tool.content && (
        <div className="px-3 pb-2 border-t border-border/30">
          <pre className="text-[10px] text-zinc-400 font-mono leading-[1.5] overflow-auto max-h-[200px] mt-1.5 whitespace-pre-wrap break-all">{
            tool.content.length > 2000 ? tool.content.slice(0, 2000) + '\n... (' + tool.content.length.toLocaleString() + ' chars total)' : tool.content
          }</pre>
        </div>
      )}
    </div>
  );
}

// ── Token Counter ────────────────────────────────────

function TokenCounter({tokenSession}) {
  if (!tokenSession || (tokenSession.inputTokens === 0 && tokenSession.outputTokens === 0)) return null;
  const info = formatTokenCost(tokenSession);
  return (
    <div className="flex items-center gap-1.5 text-[10px] text-zinc-500 tabular-nums bg-surface-2/60 rounded-lg px-2 py-1 border border-border/40">
      <span className="text-zinc-400">Tokens:</span>
      <span className="text-zinc-300">{info.inStr}</span>
      <span className="text-zinc-600">in</span>
      <span className="text-zinc-600">+</span>
      <span className="text-zinc-300">{info.outStr}</span>
      <span className="text-zinc-600">out</span>
      <span className="text-zinc-700 mx-0.5">|</span>
      <span className="text-amber-500/80">¥{info.costStr}</span>
      <span className="text-zinc-700 mx-0.5">|</span>
      <span className="text-zinc-500">{info.model}</span>
    </div>
  );
}

// ── Thinking Indicator ──────────────────────────────

function ThinkingBubble({text}) {
  if (!text) return null;
  return (
    <div className="my-1 px-3 py-1.5 bg-brand-600/5 border border-brand-500/15 rounded-lg animate-fade-in">
      <div className="flex items-center gap-1.5">
        <span className="text-[10px] text-brand-400/70">💭 思考中</span>
      </div>
      <pre className="text-[10px] text-zinc-500 font-mono leading-[1.4] mt-0.5 whitespace-pre-wrap max-h-16 overflow-hidden">{text.slice(0, 200)}{text.length > 200 ? '...' : ''}</pre>
    </div>
  );
}

// ── Chat View ────────────────────────────────────────

function TypingIndicator() {
  return (
    <div className="flex items-center gap-1 px-1">
      <div className="typing-dot w-1.5 h-1.5 rounded-full bg-brand-400"></div>
      <div className="typing-dot w-1.5 h-1.5 rounded-full bg-brand-400"></div>
      <div className="typing-dot w-1.5 h-1.5 rounded-full bg-brand-400"></div>
    </div>
  );
}

function ChatMessage({msg, isLast}) {
  const isUser = msg.role === 'user';
  const source = msg.source || 'manager';
  const agentInfo = getChatMeta(source);
  const targetInfo = msg.target ? getChatMeta(msg.target) : null;

  return (
    <div className={`animate-slide-up flex ${isUser?'justify-end':'justify-start'} mb-5`}>
      <div className={`max-w-[95%] md:max-w-[75%] ${isUser?'':'flex gap-3'}`}>
        {!isUser && (
          <div className="w-8 h-8 rounded-xl bg-brand-600/15 flex items-center justify-center text-sm flex-shrink-0 mt-0.5">{agentInfo.icon}</div>
        )}
        <div>
          {!isUser && (
            <div className="flex items-center gap-1.5 mb-1">
              <span className="text-[11px] font-medium text-zinc-400">{agentInfo.label}</span>
              {source !== 'manager' && msg.source && (
                <span className="text-[9px] px-1.5 py-0.5 bg-surface-3 text-zinc-500 rounded-md">直答</span>
              )}
            </div>
          )}
          {isUser && targetInfo && targetInfo.label !== 'Manager' && (
            <div className="flex items-center justify-end gap-1 mb-1">
              <span className="text-[10px] text-zinc-600">发给</span>
              <span className="text-[10px] text-zinc-500">{targetInfo.icon} {targetInfo.label}</span>
            </div>
          )}
          <div className={`rounded-2xl px-4 py-3 text-[13.5px] leading-[1.7] ${isUser
            ?'bg-brand-600 text-white rounded-tr-md'
            :'bg-surface-2 text-zinc-200 border border-border rounded-tl-md'}`}>
            <pre className="whitespace-pre-wrap break-all overflow-x-auto max-w-full font-[inherit] text-[13.5px]">{msg.content}</pre>
          </div>
          <div className={`text-[10px] text-zinc-600 mt-1 ${isUser?'text-right':'text-left'}`}>
            {new Date(msg.ts||Date.now()).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}
          </div>
        </div>
      </div>
    </div>
  );
}

function TargetSelector({chatTarget, onChatTargetChange, agents}) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const current = getChatMeta(chatTarget);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const allTargets = useMemo(() => {
    const targets = {...CHAT_TARGETS};
    Object.keys(agents).forEach(name => {
      const key = name === 'orchestrator' ? 'manager' : name;
      if (!targets[key]) targets[key] = getChatMeta(key);
    });
    return targets;
  }, [agents]);

  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen(!open)}
        className="btn flex items-center gap-2 px-3 py-1.5 bg-surface-2 hover:bg-surface-3 border border-border hover:border-border-light rounded-xl text-sm transition-all">
        <span>{current.icon}</span>
        <span className="text-zinc-200 font-medium">{current.label}</span>
        <svg className={`w-3.5 h-3.5 text-zinc-500 transition-transform ${open ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeWidth="2" d="M19 9l-7 7-7-7"/></svg>
      </button>
      {open && (
        <div className="absolute top-full left-0 mt-1.5 w-60 bg-surface-2 border border-border rounded-xl shadow-2xl z-50 py-1 animate-scale-in max-h-80 overflow-y-auto">
          {Object.entries(allTargets).map(([key, info]) => {
            const agentName = key === 'manager' ? 'orchestrator' : key;
            const isOnline = agents[agentName]?.status === 'online';
            return (
              <button key={key} onClick={() => { onChatTargetChange(key); setOpen(false); }}
                className={`w-full flex items-center gap-2.5 px-3 py-2 text-left text-[13px] transition hover:bg-surface-3
                  ${chatTarget === key ? 'text-brand-400 bg-brand-600/10' : 'text-zinc-300'}`}>
                <span className="text-base w-6 text-center">{info.icon}</span>
                <div className="flex-1 min-w-0">
                  <div className="font-medium flex items-center gap-1.5">
                    {info.label}
                    <StatusDot status={isOnline?'online':'offline'} size={1.5} />
                  </div>
                  <div className="text-[10px] text-zinc-600">{info.desc}</div>
                </div>
                {chatTarget === key && <span className="text-brand-400 text-[10px]">●</span>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function MentionPopup({filter, onSelect, position}) {
  const entries = Object.entries(CHAT_TARGETS).filter(([key, info]) =>
    !filter || key.includes(filter) || info.label.includes(filter)
  );
  if (entries.length === 0) return null;
  return (
    <div className="absolute bottom-full left-0 mb-1 w-52 bg-surface-2 border border-border rounded-xl shadow-2xl z-50 py-1 animate-slide-up max-h-56 overflow-y-auto"
      style={{left: position + 'px'}}>
      {entries.map(([key, info]) => (
        <button key={key} onClick={() => onSelect(key)}
          className="w-full flex items-center gap-2 px-3 py-1.5 text-left text-[12px] text-zinc-300 hover:bg-surface-3 transition">
          <span>{info.icon}</span>
          <span className="font-medium">{info.label}</span>
          <span className="text-zinc-600 ml-auto">@{key}</span>
        </button>
      ))}
    </div>
  );
}

function ChatView({conversations, activeConvId, messages, onSend, isThinking, chatTarget, onChatTargetChange, sidebarCollapsed, onToggleSidebar, onNewConv, onSelectConv, onDeleteConv, agents, activeToolCalls, thinkingText, tokenSession}) {
  const [input, setInput] = useState('');
  const [showMention, setShowMention] = useState(false);
  const [mentionFilter, setMentionFilter] = useState('');
  const chatEnd = useRef(null);
  const inputRef = useRef(null);
  const thinkingAgent = getChatMeta(chatTarget);

  useEffect(() => { chatEnd.current?.scrollIntoView({behavior:'smooth'}); }, [messages, isThinking]);

  const handleInputChange = (e) => {
    const val = e.target.value;
    setInput(val);
    const match = val.match(/^@(\w*)$/);
    if (match) { setShowMention(true); setMentionFilter(match[1]); }
    else if (val.match(/^@(\w+)\s/)) { setShowMention(false); }
    else if (!val.includes('@')) { setShowMention(false); }
  };

  const handleMentionSelect = (agentKey) => {
    setInput('@' + agentKey + ' ');
    setShowMention(false);
    if (inputRef.current) inputRef.current.focus();
  };

  const handleSend = (text) => {
    const t = text || input.trim();
    if (!t || isThinking) return;
    onSend(t);
    setInput('');
    setShowMention(false);
    if (inputRef.current) {inputRef.current.style.height='auto';}
  };

  return (
    <div className="flex-1 flex h-full animate-fade-in">
      <ChatSidebar conversations={conversations} activeId={activeConvId}
        onSelect={onSelectConv} onCreate={onNewConv} onDelete={onDeleteConv}
        collapsed={sidebarCollapsed} onToggle={onToggleSidebar} />

      <div className="flex-1 flex flex-col h-full min-w-0">
        <div className="flex items-center gap-3 px-5 py-2.5 border-b border-border bg-surface-0/80 backdrop-blur-sm">
          {sidebarCollapsed && (
            <button onClick={onToggleSidebar} className="p-1.5 hover:bg-surface-3 rounded-lg text-zinc-500 hover:text-zinc-300 transition" title="展开会话列表">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><path d="M4 6h16M4 12h16M4 18h16"/></svg>
            </button>
          )}
          <TargetSelector chatTarget={chatTarget} onChatTargetChange={onChatTargetChange} agents={agents} />
          <span className="text-[11px] text-zinc-600 hidden md:inline">@agent 切换对话对象</span>
          <div className="ml-auto hidden md:block">
            <TokenCounter tokenSession={tokenSession} />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-3 md:px-6 py-4 md:py-6">
          {messages.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full text-center animate-fade-in">
              <div className="w-16 h-16 rounded-2xl bg-brand-600/10 flex items-center justify-center mb-5 glow-brand">
                <span className="text-3xl">{thinkingAgent.icon}</span>
              </div>
              <h2 className="text-xl font-bold text-white mb-1.5">与 {thinkingAgent.label} 对话</h2>
              <p className="text-zinc-500 text-sm mb-8 max-w-sm">{thinkingAgent.desc}。输入自由文本或使用 / 命令。</p>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 md:gap-2.5 max-w-2xl w-full px-2 md:px-0">
                {QUICK.map(q => (
                  <button key={q.cmd} onClick={()=>handleSend('/'+q.cmd)}
                    className="btn flex flex-col items-start gap-1 p-3 bg-surface-2 hover:bg-surface-3 border border-border hover:border-border-light rounded-xl transition-all text-left group">
                    <span className="text-base">{q.icon}</span>
                    <span className="text-[13px] font-medium text-zinc-200 group-hover:text-white">{q.label}</span>
                    <span className="text-[11px] text-zinc-600">{q.desc}</span>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m,i) => <ChatMessage key={i} msg={m} isLast={i===messages.length-1} />)
          )}
          {isThinking && (
            <div className="animate-slide-up flex justify-start mb-5">
              <div className="flex gap-3">
                <div className="w-8 h-8 rounded-xl bg-brand-600/15 flex items-center justify-center text-sm flex-shrink-0">{thinkingAgent.icon}</div>
                <div className="min-w-0 max-w-[85%] md:max-w-[65%]">
                  <div className="text-[11px] text-zinc-500 mb-1">{thinkingAgent.label} 思考中...</div>
                  {thinkingText && <ThinkingBubble text={thinkingText} />}
                  {activeToolCalls && activeToolCalls.length > 0 && (
                    <div className="space-y-0.5">
                      {activeToolCalls.map((tc, i) => <ToolCallCard key={tc.name + '_' + i} tool={tc} />)}
                    </div>
                  )}
                  <div className="rounded-2xl rounded-tl-md bg-surface-2 border border-border px-4 py-3.5 mt-1">
                    <TypingIndicator />
                  </div>
                </div>
              </div>
            </div>
          )}
          <div ref={chatEnd} />
        </div>

        <div className="border-t border-border px-3 md:px-5 py-3 md:py-4 bg-surface-0/80 backdrop-blur-sm">
          <div className="flex items-end gap-2 md:gap-3 max-w-3xl mx-auto relative">
            <div className="flex-1 bg-surface-4 border border-border-light rounded-2xl overflow-hidden focus-within:border-brand-500/40 focus-within:shadow-[0_0_0_3px_rgba(99,102,241,.08)] transition-all relative">
              {showMention && <MentionPopup filter={mentionFilter} onSelect={handleMentionSelect} position={16} />}
              <textarea ref={inputRef} value={input} onChange={handleInputChange}
                onKeyDown={e=>{
                  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();handleSend();}
                  if(e.key==='Escape') setShowMention(false);
                }}
                placeholder={`给 ${thinkingAgent.label} 发消息...`}
                rows={1}
                className="w-full bg-transparent px-4 py-3 text-[14px] text-zinc-50 placeholder-zinc-500 focus:outline-none"
                style={{maxHeight:'120px',minHeight:'48px'}}
                onInput={e=>{e.target.style.height='auto';e.target.style.height=Math.min(e.target.scrollHeight,120)+'px';}}
              />
            </div>
            <button onClick={()=>handleSend()} disabled={isThinking||!input.trim()}
              className="btn p-3 bg-brand-600 hover:bg-brand-500 disabled:bg-surface-3 disabled:text-zinc-700 rounded-xl text-white transition-all shadow-lg shadow-brand-600/20 disabled:shadow-none">
              {isThinking
                ? <Spinner size={5} />
                : <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M5 12h14M12 5l7 7-7 7"/></svg>}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
