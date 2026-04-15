// ── Meme因子中心 (MemeView) ─────────────────────────────

const MEME_THEME_COLORS = {
  meme_amplitude_compression: 'text-violet-400',
  meme_volume_dry_up:         'text-cyan-400',
  meme_limit_up_momentum:     'text-rose-400',
  meme_breakout_signal:       'text-amber-400',
  meme_opening_attack:        'text-orange-400',
  meme_chip_consolidation:    'text-teal-400',
  meme_vol_price_resonance:   'text-blue-400',
  meme_intraday_high_close:   'text-emerald-400',
  meme_pre_explosion_pattern: 'text-pink-400',
  meme_gap_follow:            'text-indigo-400',
  meme_mutation:              'text-zinc-400',
};
const MEME_THEME_BG = {
  meme_amplitude_compression: 'bg-violet-500/10 border-violet-500/20',
  meme_volume_dry_up:         'bg-cyan-500/10 border-cyan-500/20',
  meme_limit_up_momentum:     'bg-rose-500/10 border-rose-500/20',
  meme_breakout_signal:       'bg-amber-500/10 border-amber-500/20',
  meme_opening_attack:        'bg-orange-500/10 border-orange-500/20',
  meme_chip_consolidation:    'bg-teal-500/10 border-teal-500/20',
  meme_vol_price_resonance:   'bg-blue-500/10 border-blue-500/20',
  meme_intraday_high_close:   'bg-emerald-500/10 border-emerald-500/20',
  meme_pre_explosion_pattern: 'bg-pink-500/10 border-pink-500/20',
  meme_gap_follow:            'bg-indigo-500/10 border-indigo-500/20',
  meme_mutation:              'bg-zinc-500/10 border-zinc-500/20',
};

function MemeKpiCard({icon, label, value, sub, color, loading}) {
  return (
    <div className="bg-surface-2 border border-border rounded-2xl p-4 flex flex-col gap-1 card-hover">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-base">{icon}</span>
        <span className="text-[10px] text-zinc-500 uppercase tracking-wider font-medium">{label}</span>
      </div>
      {loading
        ? <div className="skeleton h-6 w-16 rounded-lg"></div>
        : <div className={`text-xl font-bold tracking-tight ${color || 'text-white'}`}>{value ?? '—'}</div>
      }
      {sub && <div className="text-[10px] text-zinc-600">{sub}</div>}
    </div>
  );
}

function MemeThemeGrid({themes, loading}) {
  if (loading) return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
      {Array(10).fill(0).map((_,i) => <div key={i} className="skeleton h-16 rounded-xl"></div>)}
    </div>
  );
  if (!themes || themes.length === 0) return (
    <div className="text-center text-zinc-600 text-sm py-8">暂无主题数据</div>
  );
  const maxSharpe = Math.max(...themes.map(t => t.avg_sharpe || 0), 0.1);
  return (
    <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
      {themes.map(t => {
        const barW = Math.round((t.avg_sharpe / maxSharpe) * 100);
        const bg   = MEME_THEME_BG[t.theme_id]   || 'bg-zinc-500/10 border-zinc-500/20';
        const tc   = MEME_THEME_COLORS[t.theme_id]|| 'text-zinc-400';
        return (
          <div key={t.theme_id} className={`rounded-xl border p-3 ${bg} flex flex-col gap-1.5 transition-all hover:scale-[1.02]`}>
            <div className={`text-[11px] font-semibold ${tc} truncate`}>{t.theme_name}</div>
            <div className="flex items-center justify-between text-[10px] text-zinc-500">
              <span>因子数</span>
              <span className={`font-bold ${tc}`}>{t.count}</span>
            </div>
            <div className="h-1 bg-surface-4 rounded-full overflow-hidden">
              <div className={`h-full rounded-full ${tc.replace('text-','bg-')}`} style={{width: barW + '%', opacity: 0.7}}></div>
            </div>
            <div className="flex justify-between text-[9px] text-zinc-600">
              <span>Sharpe {t.avg_sharpe > 0 ? t.avg_sharpe.toFixed(2) : '—'}</span>
              <span>WR {t.avg_win_rate > 0 ? (t.avg_win_rate*100).toFixed(0)+'%' : '—'}</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function MemeFactorTable({factors, loading}) {
  if (loading) return <div className="skeleton h-40 rounded-xl w-full"></div>;
  if (!factors || factors.length === 0) return (
    <div className="text-center text-zinc-600 text-sm py-8">
      暂无Meme因子入库 — 等待挖掘结果...
    </div>
  );
  return (
    <div className="overflow-x-auto rounded-xl border border-border">
      <table className="w-full text-[11px]">
        <thead>
          <tr className="bg-surface-3 text-zinc-500 border-b border-border">
            <th className="px-3 py-2 text-left font-medium">主题</th>
            <th className="px-3 py-2 text-right font-medium">Sharpe</th>
            <th className="px-3 py-2 text-right font-medium">胜率</th>
            <th className="px-3 py-2 text-right font-medium">IC均值</th>
            <th className="px-3 py-2 text-right font-medium">IR</th>
            <th className="px-3 py-2 text-right font-medium">交易数</th>
            <th className="px-3 py-2 text-center font-medium">状态</th>
          </tr>
        </thead>
        <tbody>
          {factors.map((f, i) => {
            const tc = MEME_THEME_COLORS[f.theme_id] || 'text-zinc-400';
            return (
              <tr key={f.id || i} className="border-t border-border/50 hover:bg-surface-3/50 transition">
                <td className="px-3 py-2">
                  <div className={`text-[10px] font-semibold ${tc}`}>{f.theme_name}</div>
                  <div className="text-[9px] text-zinc-600 truncate max-w-[120px]">{f.sub_theme || f.id}</div>
                </td>
                <td className={`px-3 py-2 text-right font-bold ${f.sharpe >= 1 ? 'text-emerald-400' : f.sharpe >= 0 ? 'text-amber-400' : 'text-red-400'}`}>
                  {f.sharpe?.toFixed(3) ?? '—'}
                </td>
                <td className={`px-3 py-2 text-right ${f.win_rate >= 0.5 ? 'text-emerald-400' : 'text-zinc-400'}`}>
                  {f.win_rate > 0 ? (f.win_rate*100).toFixed(1)+'%' : '—'}
                </td>
                <td className="px-3 py-2 text-right text-zinc-400">
                  {f.ic_mean != null ? f.ic_mean.toFixed(4) : '—'}
                </td>
                <td className={`px-3 py-2 text-right ${f.ir >= 0.5 ? 'text-emerald-400' : 'text-zinc-400'}`}>
                  {f.ir?.toFixed(3) ?? '—'}
                </td>
                <td className="px-3 py-2 text-right text-zinc-400">{f.trades ?? '—'}</td>
                <td className="px-3 py-2 text-center">
                  <span className="text-[9px] px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                    {f.status || 'active'}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function MemeSignalsList({signals, loading}) {
  if (loading) return <div className="skeleton h-32 rounded-xl w-full"></div>;
  const stocks = signals?.stocks || [];
  if (stocks.length === 0) return (
    <div className="text-center text-zinc-600 text-sm py-6">
      暂无实盘信号 — 等待截面筛选...
    </div>
  );
  return (
    <div>
      <div className="text-[10px] text-zinc-600 mb-2">
        信号时间: {signals?.ts_str || '—'} · 基于 {signals?.based_on?.length || 0} 个因子
      </div>
      <div className="overflow-x-auto rounded-xl border border-border">
        <table className="w-full text-[11px]">
          <thead>
            <tr className="bg-surface-3 text-zinc-500 border-b border-border">
              <th className="px-3 py-2 text-left font-medium">股票代码</th>
              <th className="px-3 py-2 text-left font-medium">信号主题</th>
              <th className="px-3 py-2 text-right font-medium">因子强度 (Sharpe)</th>
              <th className="px-3 py-2 text-center font-medium">信号</th>
            </tr>
          </thead>
          <tbody>
            {stocks.slice(0, 20).map((s, i) => (
              <tr key={i} className="border-t border-border/50 hover:bg-surface-3/50 transition">
                <td className="px-3 py-2 font-mono text-zinc-300 font-bold">{s.stock || s.ts_code || s.code || '—'}</td>
                <td className="px-3 py-2">
                  <span className={`text-[10px] ${MEME_THEME_COLORS[s.factor_theme_id] || 'text-zinc-400'}`}>
                    {s.factor_theme || '—'}
                  </span>
                </td>
                <td className="px-3 py-2 text-right">
                  <span className={`font-medium ${(s.factor_sharpe || 0) >= 1 ? 'text-emerald-400' : 'text-amber-400'}`}>
                    {s.factor_sharpe?.toFixed(2) ?? '—'}
                  </span>
                </td>
                <td className="px-3 py-2 text-center">
                  <span className="text-[9px] px-2 py-0.5 rounded-full bg-rose-500/10 text-rose-400 border border-rose-500/20 font-medium">
                    🐉 Meme候选
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function MemeIterationTimeline({log, loading}) {
  if (loading) return <div className="skeleton h-24 rounded-xl w-full"></div>;
  if (!log || log.length === 0) return (
    <div className="text-center text-zinc-600 text-sm py-6">暂无迭代记录</div>
  );
  return (
    <div className="space-y-2 max-h-64 overflow-y-auto overflow-x-hidden pr-1">
      {log.map((ev, i) => {
        const isAnalysis = ev.type === 'analysis';
        const isIterate  = ev.type === 'iterate';
        const isMine     = ev.type === 'mine_session_done';
        const icon = isAnalysis ? '📊' : isIterate ? '🔄' : isMine ? '⛏️' : '📌';
        const color = isAnalysis ? 'border-blue-500/30' : isIterate ? 'border-amber-500/30' : 'border-emerald-500/30';
        return (
          <div key={i} className={`flex gap-3 items-start p-3 rounded-xl bg-surface-2 border ${color}`}>
            <span className="text-base mt-0.5 flex-shrink-0">{icon}</span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] font-semibold text-zinc-300">
                  {isAnalysis ? '库分析' : isIterate ? `迭代挖掘 → ${ev.focus_theme_name || '全主题'}` : isMine ? '挖掘会话完成' : ev.type}
                </span>
                <span className="text-[9px] text-zinc-600 flex-shrink-0">{ev.ts_str || ''}</span>
              </div>
              <div className="text-[10px] text-zinc-500 mt-0.5">
                {isAnalysis && (
                  <span>
                    {ev.total_factors} 个因子 · 最优主题: <span className="text-amber-400">{ev.best_theme}</span>
                    {ev.avg_sharpe > 0 && ` · avg Sharpe=${ev.avg_sharpe?.toFixed(2)}`}
                  </span>
                )}
                {isIterate && (
                  <span>
                    完成 {ev.rounds} 轮 · 入库 <span className="text-emerald-400">{ev.admitted || 0}</span> 个
                  </span>
                )}
                {isMine && (
                  <span>入库 <span className="text-emerald-400">{ev.result?.total_admitted || 0}</span> 个</span>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function MemeView() {
  const [tab, setTab] = React.useState('overview');
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [iterating, setIterating] = React.useState(false);
  const [refreshing, setRefreshing] = React.useState(false);
  const [analyzing, setAnalyzing] = React.useState(false);
  const [mining, setMining] = React.useState(false);
  const [mineLogs, setMineLogs] = React.useState(() => {
    try { const s = localStorage.getItem('rragent_meme_logs'); return s ? JSON.parse(s) : []; } catch(e) { return []; }
  });
  const toast = React.useContext(ToastContext);

  // Persist mine logs
  React.useEffect(() => {
    try { localStorage.setItem('rragent_meme_logs', JSON.stringify(mineLogs.slice(-300))); } catch(e) {}
  }, [mineLogs]);

  const fetchDashboard = React.useCallback(async (bust=false) => {
    setLoading(true);
    try {
      const url = bust ? '/api/meme/dashboard?bust=1' : '/api/meme/dashboard';
      const r = await fetch(url, {headers: authHeaders()});
      if (r.ok) setData(await r.json());
    } catch(e) { console.error(e); }
    setLoading(false);
  }, []);

  React.useEffect(() => { fetchDashboard(); }, [fetchDashboard]);

  // Auto-refresh every 60s
  React.useEffect(() => {
    const t = setInterval(() => fetchDashboard(), 60000);
    return () => clearInterval(t);
  }, [fetchDashboard]);

  const handleIterate = async () => {
    setIterating(true);
    try {
      const r = await apiPost('/api/meme/iterate', {});
      if (r.ok) {
        toast(`🔄 迭代已启动 → ${r.focus_theme_name}`, 'success');
        setMineLogs(prev => [...prev, {ts: Date.now(), text: `🔄 智能迭代启动 → ${r.focus_theme_name}`, type: 'info'}]);
        setTimeout(() => fetchDashboard(true), 10000);
      } else {
        toast(r.message || '迭代启动失败', 'error');
      }
    } catch(e) { toast('网络错误', 'error'); }
    setIterating(false);
  };

  const handleRefreshSignals = async () => {
    setRefreshing(true);
    try {
      const r = await apiPost('/api/meme/signals/refresh', {top_n: 3});
      toast(r.ok ? `🎯 信号刷新完成, ${r.count} 只候选` : '信号刷新失败', r.ok ? 'success' : 'error');
      if (r.ok) setTimeout(() => fetchDashboard(true), 2000);
    } catch(e) { toast('网络错误', 'error'); }
    setRefreshing(false);
  };

  const handleAnalyze = async () => {
    setAnalyzing(true);
    toast('正在分析妖股因子库...', 'info');
    try {
      const r = await apiPost('/api/meme/analyze', {});
      if (r.ok || r.analysis) {
        toast('因子库分析完成', 'success');
        setMineLogs(prev => [...prev, {ts: Date.now(), text: `🔬 分析完成: ${JSON.stringify(r.analysis || r).slice(0, 200)}`, type: 'success'}]);
        setTimeout(() => fetchDashboard(true), 2000);
      } else { toast(r.error || '分析失败', 'error'); }
    } catch(e) { toast('分析请求失败', 'error'); }
    setAnalyzing(false);
  };

  const handleStartMine = async () => {
    setMining(true);
    setMineLogs(prev => [...prev, {ts: Date.now(), text: '⛏️ 妖股因子挖掘启动中...', type: 'info'}]);
    setTab('logs');
    try {
      const resp = await fetch('/api/meme/mine', {method:'POST', headers: authHeaders(), body: JSON.stringify({rounds: 2, factors: 5})});
      if (resp.status === 401) { window.__onAuthExpired?.(); setMining(false); return; }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {stream:true});
        const lines = buffer.split('\n'); buffer = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const evt = JSON.parse(line.slice(6));
            if (evt.type === 'close' || evt.type === 'heartbeat') continue;
            if (evt.type === 'progress' || evt.type === 'done') {
              setMineLogs(prev => [...prev, {ts: Date.now(), text: evt.text, type: evt.type === 'done' ? 'success' : 'info'}]);
              if (evt.type === 'done') toast('妖股因子挖掘完成', 'success');
            } else if (evt.type === 'error') {
              setMineLogs(prev => [...prev, {ts: Date.now(), text: evt.text, type: 'error'}]);
              toast(evt.text, 'error');
            } else if (evt.type === 'started') {
              setMineLogs(prev => [...prev, {ts: Date.now(), text: `启动: ${evt.rounds}轮 × ${evt.factors}因子/轮`, type: 'info'}]);
            }
          } catch(e) {}
        }
      }
    } catch(e) { toast('挖掘连接失败: ' + e.message, 'error'); setMineLogs(prev => [...prev, {ts: Date.now(), text: '连接失败: ' + e.message, type: 'error'}]); }
    setMining(false);
    fetchDashboard(true);
  };

  const summ = data?.summary || {};
  const tabs = [
    {id:'overview',  label:'概览'},
    {id:'factors',   label:`因子库 ${summ.total_factors > 0 ? '('+summ.total_factors+')' : ''}`},
    {id:'signals',   label:'实盘信号'},
    {id:'mine',      label:'启动挖掘'},
    {id:'logs',      label:`挖掘日志${mineLogs.length > 0 ? ' ('+mineLogs.length+')' : ''}`},
    {id:'history',   label:'迭代历程'},
  ];

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="bg-surface-1/50 rounded-xl border border-border px-5 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-rose-500/10 border border-rose-500/20 flex items-center justify-center">
              <span className="text-lg">🐉</span>
            </div>
            <div>
              <h1 className="text-[15px] font-bold text-white tracking-tight">Meme因子中心</h1>
              <p className="text-[10px] text-zinc-500">A股高弹性股票启动特征量化因子 · PBO过拟合检验 · 持续迭代优化</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={()=>{setTab('mine');}} disabled={mining}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-brand-600/15 border border-brand-500/25 text-[11px] text-brand-400 hover:bg-brand-600/25 transition disabled:opacity-40">
              {mining ? <Spinner size={3} /> : <span>⛏️</span>}
              挖掘因子
            </button>
            <button onClick={handleAnalyze} disabled={analyzing || loading}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-violet-500/10 border border-violet-500/25 text-[11px] text-violet-400 hover:bg-violet-500/20 transition disabled:opacity-40">
              {analyzing ? <Spinner size={3} /> : <span>🔬</span>}
              分析
            </button>
            <button onClick={handleRefreshSignals} disabled={refreshing || loading}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-3 border border-border text-[11px] text-zinc-400 hover:text-white hover:border-border-light transition disabled:opacity-40">
              {refreshing ? <Spinner size={3} /> : <span>🎯</span>}
              信号
            </button>
            <button onClick={handleIterate} disabled={iterating || loading}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-rose-500/10 border border-rose-500/25 text-[11px] text-rose-400 hover:bg-rose-500/20 hover:text-rose-300 transition disabled:opacity-40">
              {iterating ? <Spinner size={3} /> : <span>🔄</span>}
              迭代
            </button>
            <button onClick={() => fetchDashboard(true)} disabled={loading}
              className="flex items-center gap-1.5 px-2 py-1.5 rounded-lg bg-surface-3 border border-border text-[11px] text-zinc-400 hover:text-white transition disabled:opacity-40">
              {loading ? <Spinner size={3} /> : <span>↻</span>}
            </button>
          </div>
        </div>

        {/* Tab Bar */}
        <div className="flex items-center gap-1 mt-4">
          {tabs.map(t => (
            <button key={t.id} onClick={() => setTab(t.id)}
              className={`px-3 py-1.5 rounded-lg text-[11px] font-medium transition-all ${tab===t.id ? 'tab-active bg-brand-600/15 text-brand-400' : 'text-zinc-500 hover:text-zinc-300 hover:bg-surface-3'}`}>
              {t.label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div className="space-y-4">

        {/* ── 概览 Tab ── */}
        {tab === 'overview' && (
          <>
            {/* KPI Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <MemeKpiCard icon="🐉" label="Meme因子" value={summ.total_factors ?? 0}
                sub={`覆盖 ${summ.total_themes || 0} 个主题`} color="text-rose-400" loading={loading} />
              <MemeKpiCard icon="🏆" label="最佳 Sharpe" value={summ.best_sharpe > 0 ? summ.best_sharpe?.toFixed(3) : '—'}
                sub={`均值 ${summ.avg_sharpe > 0 ? summ.avg_sharpe?.toFixed(2) : '—'}`}
                color="text-emerald-400" loading={loading} />
              <MemeKpiCard icon="🎯" label="最佳胜率" value={summ.best_win_rate > 0 ? (summ.best_win_rate*100).toFixed(1)+'%' : '—'}
                sub={`均值 ${summ.avg_win_rate > 0 ? (summ.avg_win_rate*100).toFixed(1)+'%' : '—'}`}
                color="text-amber-400" loading={loading} />
              <MemeKpiCard icon="📊" label="迭代次数" value={data?.iteration_log?.length ?? 0}
                sub="智能优化轮次" color="text-blue-400" loading={loading} />
            </div>

            {/* Theme Heatmap */}
            <Card>
              <h3 className="text-[12px] font-semibold text-zinc-300 mb-3 flex items-center gap-2">
                <span>🗺️</span> 主题性能热图
                <span className="text-[10px] text-zinc-600 font-normal ml-1">颜色深度 = 平均 Sharpe</span>
              </h3>
              <MemeThemeGrid themes={data?.theme_stats} loading={loading} />
            </Card>

            {/* Top 5 factors preview */}
            <Card>
              <h3 className="text-[12px] font-semibold text-zinc-300 mb-3 flex items-center gap-2">
                <span>🥇</span> TOP 5 Meme因子
              </h3>
              <MemeFactorTable factors={(data?.top_factors || []).slice(0,5)} loading={loading} />
            </Card>

            {/* Latest 5 iteration events */}
            <Card>
              <h3 className="text-[12px] font-semibold text-zinc-300 mb-3 flex items-center gap-2">
                <span>⚡</span> 最近迭代动态
              </h3>
              <MemeIterationTimeline log={(data?.iteration_log || []).slice(0,5)} loading={loading} />
            </Card>
          </>
        )}

        {/* ── 因子库 Tab ── */}
        {tab === 'factors' && (
          <>
            <Card>
              <h3 className="text-[12px] font-semibold text-zinc-300 mb-3 flex items-center gap-2">
                <span>🗺️</span> 主题分布
              </h3>
              <MemeThemeGrid themes={data?.theme_stats} loading={loading} />
            </Card>
            <Card>
              <h3 className="text-[12px] font-semibold text-zinc-300 mb-3 flex items-center justify-between">
                <span className="flex items-center gap-2"><span>📋</span> 全部Meme因子 ({data?.top_factors?.length || 0})</span>
                <span className="text-[10px] text-zinc-600 font-normal">按 Sharpe 降序</span>
              </h3>
              <MemeFactorTable factors={data?.top_factors} loading={loading} />
            </Card>
          </>
        )}

        {/* ── 实盘信号 Tab ── */}
        {tab === 'signals' && (
          <Card>
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-[12px] font-semibold text-zinc-300 flex items-center gap-2">
                <span>🎯</span> Meme候选信号
              </h3>
              <button onClick={handleRefreshSignals} disabled={refreshing}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-rose-500/10 border border-rose-500/20 text-[10px] text-rose-400 hover:bg-rose-500/20 transition disabled:opacity-40">
                {refreshing ? <Spinner size={3} /> : '🔄'} 刷新信号
              </button>
            </div>
            <MemeSignalsList signals={data?.signals} loading={loading} />
            <div className="mt-3 p-3 rounded-lg bg-amber-500/5 border border-amber-500/15 text-[10px] text-amber-600">
              ⚠️ 信号仅供参考，基于量化因子截面排序。Meme行情风险极高，请结合基本面和风控执行。
            </div>
          </Card>
        )}

        {/* ── 启动挖掘 Tab ── */}
        {tab === 'mine' && (
          <Card className="glow-brand border-brand-500/10">
            <h3 className="text-sm font-semibold text-zinc-300 mb-1">⛏️ 妖股因子挖掘</h3>
            <p className="text-[11px] text-zinc-500 mb-2">围绕A股高弹性股票的启动特征，LLM生成因子 → 沙箱回测 → CSCV验证 → 入库</p>
            <p className="text-[11px] text-zinc-600 mb-4">主题: 振幅压缩/缩量蓄能/涨停动量/突破信号/竞价进攻/筹码集中/量价共振/日内强势/预爆发形态/跳空跟随</p>
            <div className="flex items-end gap-3">
              <div className="flex-1 text-[11px] text-zinc-600">
                每次执行 2轮 × 5因子/轮 = 10个因子候选
              </div>
              <button onClick={handleStartMine} disabled={mining}
                className="btn px-6 py-2.5 bg-brand-600 hover:bg-brand-500 text-white text-sm font-semibold rounded-xl transition shadow-lg shadow-brand-600/20 disabled:opacity-50">
                {mining ? <span className="flex items-center gap-2"><Spinner size={4} /> 挖掘中...</span> : '启动挖掘'}
              </button>
            </div>
          </Card>
        )}

        {/* ── 挖掘日志 Tab ── */}
        {tab === 'logs' && (
          <>
            {mineLogs.length > 0 ? (
              <Card>
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-semibold text-zinc-300">📋 妖股挖掘日志 <span className="text-zinc-600 font-normal">({mineLogs.length} 条)</span></h3>
                  {!mining && <button onClick={() => { setMineLogs([]); localStorage.removeItem('rragent_meme_logs'); }} className="btn text-[11px] text-zinc-500 hover:text-zinc-300 px-2 py-1 rounded-lg transition">清空</button>}
                </div>
                <div className="space-y-1.5 max-h-[600px] overflow-y-auto overflow-x-hidden">
                  {mineLogs.map((log, i) => (
                    <div key={i} className={`text-[12px] leading-relaxed px-3 py-2 rounded-lg border-l-2 transition-all
                      ${log.type === 'success' ? 'border-emerald-500/40 bg-emerald-600/5 text-emerald-300' :
                        log.type === 'error' ? 'border-red-500/40 bg-red-600/5 text-red-300' :
                        'border-rose-500/20 bg-surface-3/30 text-zinc-400'}`}>
                      <span className="text-zinc-600 text-[10px] mr-2">{new Date(log.ts).toLocaleTimeString()}</span>
                      <span className="whitespace-pre-wrap">{log.text}</span>
                    </div>
                  ))}
                  {mining && (
                    <div className="flex items-center gap-2 px-3 py-2 text-rose-400 text-[12px] animate-pulse">
                      <Spinner size={3} /><span>挖掘进行中...</span>
                    </div>
                  )}
                </div>
              </Card>
            ) : (
              <Card><div className="text-center py-10 text-zinc-600">
                <div className="text-3xl mb-2">📋</div>
                <div className="text-sm text-zinc-500">暂无日志。点击「启动挖掘」开始。</div>
              </div></Card>
            )}
          </>
        )}

        {/* ── 迭代历程 Tab ── */}
        {tab === 'history' && (
          <Card>
            <h3 className="text-[12px] font-semibold text-zinc-300 mb-3 flex items-center gap-2">
              <span>📈</span> 迭代优化历程
            </h3>
            <MemeIterationTimeline log={data?.iteration_log} loading={loading} />
          </Card>
        )}

      </div>

    </div>
  );
}
