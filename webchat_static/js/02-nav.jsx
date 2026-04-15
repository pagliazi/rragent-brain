function LoginPage({onLogin}) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setLoading(true); setError('');
    try {
      const r = await fetch('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username: username.trim(), password})});
      const data = await r.json();
      if (data.ok) { onLogin(data.token, data.user); }
      else { setError(data.msg || '登录失败'); }
    } catch(e) { setError('网络错误'); }
    setLoading(false);
  };

  return (
    <div className="min-h-screen bg-surface-0 flex items-center justify-center relative overflow-hidden">
      <div className="absolute inset-0 overflow-hidden">
        <div className="absolute -top-40 -right-40 w-80 h-80 rounded-full bg-brand-600/5 blur-3xl"></div>
        <div className="absolute -bottom-40 -left-40 w-96 h-96 rounded-full bg-brand-500/3 blur-3xl"></div>
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] rounded-full bg-brand-600/[.02] blur-3xl"></div>
      </div>
      <div className="relative w-full max-w-[400px] px-6 animate-scale-in">
        <div className="text-center mb-8">
          <div className="w-16 h-16 mx-auto rounded-2xl bg-brand-600/15 border border-brand-500/20 flex items-center justify-center mb-5 shadow-lg shadow-brand-600/10">
            <span className="text-3xl">🦀</span>
          </div>
          <h1 className="text-2xl font-bold text-white tracking-tight">OpenClaw</h1>
          <p className="text-sm text-zinc-500 mt-1.5">多智能体协作平台</p>
        </div>
        <form onSubmit={handleSubmit} className="glass rounded-2xl p-6 space-y-4 shadow-2xl shadow-black/30 gradient-border">
          <div>
            <label className="block text-xs text-zinc-400 font-medium mb-1.5 pl-1">用户名</label>
            <input value={username} onChange={e=>setUsername(e.target.value)} autoFocus
              className="w-full bg-surface-2 border border-border rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-brand-500/50 focus:ring-1 focus:ring-brand-500/20 transition placeholder:text-zinc-600" placeholder="请输入用户名" />
          </div>
          <div>
            <label className="block text-xs text-zinc-400 font-medium mb-1.5 pl-1">密码</label>
            <input type="password" value={password} onChange={e=>setPassword(e.target.value)}
              className="w-full bg-surface-2 border border-border rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-brand-500/50 focus:ring-1 focus:ring-brand-500/20 transition placeholder:text-zinc-600" placeholder="请输入密码"
              onKeyDown={e => e.key === 'Enter' && handleSubmit(e)} />
          </div>
          {error && <div className="text-sm text-red-400 bg-red-500/10 rounded-lg px-3 py-2 border border-red-500/20">{error}</div>}
          <button type="submit" disabled={loading}
            className="w-full py-3 bg-gradient-to-r from-brand-600 to-brand-500 hover:from-brand-500 hover:to-brand-400 text-white font-semibold rounded-xl transition-all duration-300 shadow-lg shadow-brand-600/25 disabled:opacity-50 active:scale-[.98] hover:shadow-brand-500/30">
            {loading ? <span className="flex items-center justify-center gap-2"><Spinner size={4} /> 登录中...</span> : '登 录'}
          </button>
        </form>
        <p className="text-center text-[11px] text-zinc-700 mt-6">OpenClaw Multi-Agent System · v2.0</p>
      </div>
    </div>
  );
}


// ── User Menu (avatar dropdown in NavRail) ───────────

function UserMenu({user, onLogout, onViewChange}) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen(!open)}
        className="w-10 h-10 rounded-xl flex items-center justify-center bg-surface-2 border border-border hover:border-border-light hover:bg-surface-3 transition-all duration-200 group">
        <span className="text-lg group-hover:scale-110 transition-transform">{user.avatar || '🦀'}</span>
      </button>
      {open && (
        <div className="absolute bottom-full left-0 mb-2 w-52 glass rounded-xl shadow-2xl shadow-black/40 animate-scale-in z-50 overflow-hidden gradient-border">
          <div className="px-4 py-3 border-b border-border">
            <div className="flex items-center gap-2">
              <span className="text-xl">{user.avatar || '🦀'}</span>
              <div className="min-w-0">
                <div className="text-sm font-semibold text-white truncate">{user.display_name || user.username}</div>
                <div className={`text-[10px] font-medium ${ROLE_COLORS[user.role] || 'text-zinc-400'}`}>{ROLE_LABELS[user.role] || user.role}</div>
              </div>
            </div>
          </div>
          <div className="py-1">
            <button onClick={() => { setOpen(false); onViewChange('profile'); }}
              className="w-full text-left px-4 py-2 text-sm text-zinc-300 hover:bg-surface-3 hover:text-white transition flex items-center gap-2.5">
              <svg className="w-4 h-4 text-zinc-500" fill="none" stroke="currentColor" strokeWidth="1.5" viewBox="0 0 24 24"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
              个人资料
            </button>
            {user.role === 'admin' && (
              <button onClick={() => { setOpen(false); onViewChange('admin'); }}
                className="w-full text-left px-4 py-2 text-sm text-zinc-300 hover:bg-surface-3 hover:text-white transition flex items-center gap-2.5">
                <svg className="w-4 h-4 text-zinc-500" fill="none" stroke="currentColor" strokeWidth="1.5" viewBox="0 0 24 24"><path d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197"/></svg>
                用户管理
              </button>
            )}
            <div className="border-t border-border my-1"></div>
            <button onClick={onLogout}
              className="w-full text-left px-4 py-2 text-sm text-red-400 hover:bg-red-500/10 transition flex items-center gap-2.5">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth="1.5" viewBox="0 0 24 24"><path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
              退出登录
            </button>
          </div>
        </div>
      )}
    </div>
  );
}


// ── Nav Rail (collapsible left sidebar with labels) ───

function NavRail({currentView, onViewChange, agents, user, onLogout}) {
  const [collapsed, setCollapsed] = React.useState(false);
  const activeCount = Object.values(agents).filter(a=>a.status==='online'||a.status==='slow').length;
  const totalCount = Object.keys(agents).filter(n=>agents[n].status!=='sleeping').length;

  const navItems = [
    {id:'dashboard',icon:<svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="2"/><rect x="14" y="3" width="7" height="4" rx="2"/><rect x="14" y="11" width="7" height="10" rx="2"/><rect x="3" y="14" width="7" height="7" rx="2"/></svg>,label:'概览'},
    {id:'chat',icon:<svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg>,label:'对话'},
    {id:'_sep1'},
    {id:'market',icon:<svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>,label:'行情'},
    {id:'quant',icon:<svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24"><path d="M3 3v18h18"/><path d="M7 16l4-8 4 4 4-8"/></svg>,label:'量化'},
    {id:'intraday',icon:<svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>,label:'盘中'},
    {id:'tasks',icon:<svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24"><path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 01-2 2H5a2 2 0 01-2-2V5a2 2 0 012-2h11"/></svg>,label:'任务'},
    {id:'news',icon:<svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24"><path d="M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V9a2 2 0 012-2h2a2 2 0 012 2v9a2 2 0 01-2 2z"/></svg>,label:'新闻'},
    {id:'_sep2'},
    {id:'tools',icon:<svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24"><path d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.76 3.76z"/></svg>,label:'工具'},
    {id:'apple',icon:<svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24"><path d="M12 2a7 7 0 00-7 7c0 5 7 13 7 13s7-8 7-13a7 7 0 00-7-7z"/><circle cx="12" cy="9" r="2.5"/></svg>,label:'Apple'},
    {id:'autoresearch',icon:<svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24"><path d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>,label:'Research'},
    {id:'devops',icon:<svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>,label:'开发'},
    {id:'infra_monitor',icon:<svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>,label:'监控'},
    {id:'_sep3'},
    {id:'agent_skills',icon:<svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24"><path d="M17 21v-2a4 4 0 00-4-4H5a4 4 0 00-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 00-3-3.87"/><path d="M16 3.13a4 4 0 010 7.75"/></svg>,label:'Agent'},
    {id:'system',icon:<svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>,label:'系统'},
    {id:'logs',icon:<svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>,label:'日志'},
    {id:'api_usage',icon:<svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" strokeWidth="1.6" viewBox="0 0 24 24"><path d="M12 20V10"/><path d="M18 20V4"/><path d="M6 20v-4"/></svg>,label:'API用量'},
  ];

  const collapseBtn = <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24">
    {collapsed
      ? <path d="M9 18l6-6-6-6"/>
      : <path d="M15 18l-6-6 6-6"/>}
  </svg>;

  return (
    <div className={`${collapsed ? 'w-[52px]' : 'w-[160px]'} h-full bg-surface-0 border-r border-border flex flex-col py-3 flex-shrink-0 transition-all duration-300 ease-[cubic-bezier(.4,0,.2,1)]`}>
      <div className={`flex items-center ${collapsed ? 'justify-center px-0' : 'justify-between px-3'} mb-3`}>
        <div className="w-8 h-8 rounded-xl bg-brand-600/20 flex items-center justify-center cursor-pointer hover:bg-brand-600/30 transition-all duration-200 flex-shrink-0 hover:shadow-[0_0_12px_rgba(99,102,241,.2)]"
          onClick={()=>onViewChange('dashboard')}>
          <span className="text-base">🦀</span>
        </div>
        {!collapsed && <span className="text-xs font-bold text-brand-400 tracking-wide">RRClaw</span>}
        <button onClick={()=>setCollapsed(!collapsed)}
          className="w-6 h-6 flex items-center justify-center text-zinc-600 hover:text-zinc-400 transition-all duration-200 rounded-lg hover:bg-surface-2 flex-shrink-0">
          {collapseBtn}
        </button>
      </div>

      <nav className="flex-1 flex flex-col gap-0.5 w-full px-1.5 overflow-y-auto overflow-x-hidden">
        {navItems.map(v => {
          if (v.id.startsWith('_sep')) return (
            <div key={v.id} className={`${collapsed ? 'mx-2' : 'mx-1'} border-t border-border/40 my-1.5`}></div>
          );
          const isActive = currentView === v.id;
          const baseClass = `nav-item group flex items-center rounded-xl cursor-pointer
            ${isActive ? 'active bg-brand-600/15 text-brand-400 font-medium' : 'text-zinc-500 hover:text-zinc-200'}
            ${collapsed ? 'w-9 h-9 justify-center mx-auto' : 'h-9 px-2.5 gap-2.5'}`;
          const inner = (Tag, extra) => (
            <Tag key={v.id} {...extra} className={baseClass + (Tag === 'a' ? ' no-underline' : '')}>
              <span className={`transition-transform duration-200 ${isActive ? 'scale-110' : 'group-hover:scale-105'}`}>{v.icon}</span>
              {!collapsed && <span className="text-[12px] truncate">{v.label}</span>}
              {collapsed && (
                <span className="absolute left-full ml-2 px-2.5 py-1 bg-surface-4 text-zinc-200 text-[11px] font-medium rounded-lg shadow-xl border border-border-light opacity-0 group-hover:opacity-100 pointer-events-none transition-opacity duration-200 whitespace-nowrap z-50">
                  {v.label}
                </span>
              )}
            </Tag>
          );
          if (v.href) return inner('a', {href:v.href, target:'_blank', rel:'noopener'});
          return inner('button', {onClick:()=>onViewChange(v.id)});
        })}
      </nav>

      <div className={`flex ${collapsed ? 'flex-col items-center gap-2' : 'flex-row items-center justify-between px-3'} mt-2 pt-2.5 border-t border-border/40`}>
        {collapsed ? (
          <>
            <div className="text-[9px] text-zinc-600 font-medium tabular-nums">{activeCount}/{totalCount}</div>
            <div className={`w-2 h-2 rounded-full ${activeCount > 0 ? 'bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,.4)] pulse-ring text-emerald-400' : 'bg-zinc-600'}`}></div>
            {user && <UserMenu user={user} onLogout={onLogout} onViewChange={onViewChange} />}
          </>
        ) : (
          <>
            <div className="flex items-center gap-2 bg-surface-2/50 rounded-lg px-2 py-1">
              <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${activeCount > 0 ? 'bg-emerald-400 shadow-[0_0_4px_rgba(52,211,153,.5)]' : 'bg-zinc-600'}`}></div>
              <span className="text-[10px] text-zinc-500 tabular-nums font-medium">{activeCount}/{totalCount} 在线</span>
            </div>
            {user && <UserMenu user={user} onLogout={onLogout} onViewChange={onViewChange} />}
          </>
        )}
      </div>
    </div>
  );
}
