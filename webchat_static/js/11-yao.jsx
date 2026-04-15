// ── 妖股因子中心 (YaoView) ─────────────────────────────

const YAO_THEME_COLORS = {
  yao_amplitude_compression: 'text-violet-400',
  yao_volume_dry_up:         'text-cyan-400',
  yao_limit_up_momentum:     'text-rose-400',
  yao_breakout_signal:       'text-amber-400',
  yao_opening_attack:        'text-orange-400',
  yao_chip_consolidation:    'text-teal-400',
  yao_vol_price_resonance:   'text-blue-400',
  yao_intraday_high_close:   'text-emerald-400',
  yao_pre_explosion_pattern: 'text-pink-400',
  yao_gap_follow:            'text-indigo-400',
  yao_mutation:              'text-zinc-400',
};
const YAO_THEME_BG = {
  yao_amplitude_compression: 'bg-violet-500/10 border-violet-500/20',
  yao_volume_dry_up:         'bg-cyan-500/10 border-cyan-500/20',
  yao_limit_up_momentum:     'bg-rose-500/10 border-rose-500/20',
  yao_breakout_signal:       'bg-amber-500/10 border-amber-500/20',
  yao_opening_attack:        'bg-orange-500/10 border-orange-500/20',
  yao_chip_consolidation:    'bg-teal-500/10 border-teal-500/20',
  yao_vol_price_resonance:   'bg-blue-500/10 border-blue-500/20',
  yao_intraday_high_close:   'bg-emerald-500/10 border-emerald-500/20',
  yao_pre_explosion_pattern: 'bg-pink-500/10 border-pink-500/20',
  yao_gap_follow:            'bg-indigo-500/10 border-indigo-500/20',
  yao_mutation:              'bg-zinc-500/10 border-zinc-500/20',
};

function YaoKpiCard({icon, label, value, sub, color, loading}) {
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

function YaoThemeGrid({themes, loading}) {
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
        const bg   = YAO_THEME_BG[t.theme_id]   || 'bg-zinc-500/10 border-zinc-500/20';
        const tc   = YAO_THEME_COLORS[t.theme_id]|| 'text-zinc-400';
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

function YaoFactorTable({factors, loading}) {
  if (loading) return <div className="skeleton h-40 rounded-xl w-full"></div>;
  if (!factors || factors.length === 0) return (
    <div className="text-center text-zinc-600 text-sm py-8">
      暂无妖股因子入库 — 等待挖掘结果...
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
            const tc = YAO_THEME_COLORS[f.theme_id] || 'text-zinc-400';
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

function YaoSignalsList({signals, loading}) {
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
                  <span className={`text-[10px] ${YAO_THEME_COLORS[s.factor_theme_id] || 'text-zinc-400'}`}>
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
                    🐉 妖股候选
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

function YaoIterationTimeline({log, loading}) {
  if (loading) return <div className="skeleton h-24 rounded-xl w-full"></div>;
  if (!log || log.length === 0) return (
    <div className="text-center text-zinc-600 text-sm py-6">暂无迭代记录</div>
  );
  return (
    <div className="space-y-2 max-h-64 overflow-y-auto pr-1">
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

function YaoView() {
  const [tab, setTab] = React.useState('overview');
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [iterating, setIterating] = React.useState(false);
  const [refreshing, setRefreshing] = React.useState(false);
  const [toast, setToast] = React.useState('');
  const token = window._authToken;

  const showToast = (msg) => { setToast(msg); setTimeout(() => setToast(''), 3000); };

  const fetchDashboard = React.useCallback(async (bust=false) => {
    setLoading(true);
    try {
      const url = bust ? '/api/yao/dashboard?bust=1' : '/api/yao/dashboard';
      const r = await fetch(url, {headers: {'Authorization': `Bearer ${token}`}});
      if (r.ok) setData(await r.json());
    } catch(e) { console.error(e); }
    setLoading(false);
  }, [token]);

  React.useEffect(() => { fetchDashboard(); }, [fetchDashboard]);

  // Auto-refresh every 60s
  React.useEffect(() => {
    const t = setInterval(() => fetchDashboard(), 60000);
    return () => clearInterval(t);
  }, [fetchDashboard]);

  const handleIterate = async () => {
    setIterating(true);
    try {
      const r = await fetch('/api/yao/iterate', {method:'POST',
        headers:{'Authorization':`Bearer ${token}`,'Content-Type':'application/json'}});
      const d = await r.json();
      if (d.ok) {
        showToast(`🔄 迭代已启动 → ${d.focus_theme_name}`);
        setTimeout(() => fetchDashboard(true), 5000);
      } else {
        showToast(d.message || '迭代启动失败');
      }
    } catch(e) { showToast('网络错误'); }
    setIterating(false);
  };

  const handleRefreshSignals = async () => {
    setRefreshing(true);
    try {
      const r = await fetch('/api/yao/signals/refresh', {method:'POST',
        headers:{'Authorization':`Bearer ${token}`,'Content-Type':'application/json'},
        body: JSON.stringify({top_n: 3})});
      const d = await r.json();
      showToast(d.ok ? `🎯 信号刷新完成, ${d.count} 只候选` : '信号刷新失败');
      if (d.ok) setTimeout(() => fetchDashboard(true), 2000);
    } catch(e) { showToast('网络错误'); }
    setRefreshing(false);
  };

  const summ = data?.summary || {};
  const tabs = [
    {id:'overview',  label:'概览'},
    {id:'factors',   label:`因子库 ${summ.total_factors > 0 ? '('+summ.total_factors+')' : ''}`},
    {id:'signals',   label:'实盘信号'},
    {id:'history',   label:'迭代历程'},
  ];

  return (
    <div className="flex-1 flex flex-col h-full min-w-0 bg-surface-0 overflow-hidden">
      {/* Header */}
      <div className="flex-shrink-0 border-b border-border bg-surface-1/50 px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-rose-500/10 border border-rose-500/20 flex items-center justify-center">
              <span className="text-lg">🐉</span>
            </div>
            <div>
              <h1 className="text-[15px] font-bold text-white tracking-tight">妖股因子中心</h1>
              <p className="text-[10px] text-zinc-500">A股高弹性股票启动特征量化因子 · PBO过拟合检验 · 持续迭代优化</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={handleRefreshSignals} disabled={refreshing || loading}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-3 border border-border text-[11px] text-zinc-400 hover:text-white hover:border-border-light transition disabled:opacity-40">
              {refreshing ? <Spinner size={3} /> : <span>🎯</span>}
              刷新信号
            </button>
            <button onClick={handleIterate} disabled={iterating || loading}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-rose-500/10 border border-rose-500/25 text-[11px] text-rose-400 hover:bg-rose-500/20 hover:text-rose-300 transition disabled:opacity-40">
              {iterating ? <Spinner size={3} /> : <span>🔄</span>}
              智能迭代
            </button>
            <button onClick={() => fetchDashboard(true)} disabled={loading}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface-3 border border-border text-[11px] text-zinc-400 hover:text-white transition disabled:opacity-40">
              {loading ? <Spinner size={3} /> : <span>↻</span>}
              刷新
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
      <div className="flex-1 overflow-y-auto p-6 space-y-5 view-enter">

        {/* ── 概览 Tab ── */}
        {tab === 'overview' && (
          <>
            {/* KPI Cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <YaoKpiCard icon="🐉" label="妖股因子" value={summ.total_factors ?? 0}
                sub={`覆盖 ${summ.total_themes || 0} 个主题`} color="text-rose-400" loading={loading} />
              <YaoKpiCard icon="🏆" label="最佳 Sharpe" value={summ.best_sharpe > 0 ? summ.best_sharpe?.toFixed(3) : '—'}
                sub={`均值 ${summ.avg_sharpe > 0 ? summ.avg_sharpe?.toFixed(2) : '—'}`}
                color="text-emerald-400" loading={loading} />
              <YaoKpiCard icon="🎯" label="最佳胜率" value={summ.best_win_rate > 0 ? (summ.best_win_rate*100).toFixed(1)+'%' : '—'}
                sub={`均值 ${summ.avg_win_rate > 0 ? (summ.avg_win_rate*100).toFixed(1)+'%' : '—'}`}
                color="text-amber-400" loading={loading} />
              <YaoKpiCard icon="📊" label="迭代次数" value={data?.iteration_log?.length ?? 0}
                sub="智能优化轮次" color="text-blue-400" loading={loading} />
            </div>

            {/* Theme Heatmap */}
            <Card>
              <h3 className="text-[12px] font-semibold text-zinc-300 mb-3 flex items-center gap-2">
                <span>🗺️</span> 主题性能热图
                <span className="text-[10px] text-zinc-600 font-normal ml-1">颜色深度 = 平均 Sharpe</span>
              </h3>
              <YaoThemeGrid themes={data?.theme_stats} loading={loading} />
            </Card>

            {/* Top 5 factors preview */}
            <Card>
              <h3 className="text-[12px] font-semibold text-zinc-300 mb-3 flex items-center gap-2">
                <span>🥇</span> TOP 5 妖股因子
              </h3>
              <YaoFactorTable factors={(data?.top_factors || []).slice(0,5)} loading={loading} />
            </Card>

            {/* Latest 5 iteration events */}
            <Card>
              <h3 className="text-[12px] font-semibold text-zinc-300 mb-3 flex items-center gap-2">
                <span>⚡</span> 最近迭代动态
              </h3>
              <YaoIterationTimeline log={(data?.iteration_log || []).slice(0,5)} loading={loading} />
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
              <YaoThemeGrid themes={data?.theme_stats} loading={loading} />
            </Card>
            <Card>
              <h3 className="text-[12px] font-semibold text-zinc-300 mb-3 flex items-center justify-between">
                <span className="flex items-center gap-2"><span>📋</span> 全部妖股因子 ({data?.top_factors?.length || 0})</span>
                <span className="text-[10px] text-zinc-600 font-normal">按 Sharpe 降序</span>
              </h3>
              <YaoFactorTable factors={data?.top_factors} loading={loading} />
            </Card>
          </>
        )}

        {/* ── 实盘信号 Tab ── */}
        {tab === 'signals' && (
          <Card>
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-[12px] font-semibold text-zinc-300 flex items-center gap-2">
                <span>🎯</span> 妖股候选信号
              </h3>
              <button onClick={handleRefreshSignals} disabled={refreshing}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-rose-500/10 border border-rose-500/20 text-[10px] text-rose-400 hover:bg-rose-500/20 transition disabled:opacity-40">
                {refreshing ? <Spinner size={3} /> : '🔄'} 刷新信号
              </button>
            </div>
            <YaoSignalsList signals={data?.signals} loading={loading} />
            <div className="mt-3 p-3 rounded-lg bg-amber-500/5 border border-amber-500/15 text-[10px] text-amber-600">
              ⚠️ 信号仅供参考，基于量化因子截面排序。妖股行情风险极高，请结合基本面和风控执行。
            </div>
          </Card>
        )}

        {/* ── 迭代历程 Tab ── */}
        {tab === 'history' && (
          <Card>
            <h3 className="text-[12px] font-semibold text-zinc-300 mb-3 flex items-center gap-2">
              <span>📈</span> 迭代优化历程
            </h3>
            <YaoIterationTimeline log={data?.iteration_log} loading={loading} />
          </Card>
        )}

      </div>

      {/* Toast */}
      {toast && (
        <div className="fixed bottom-6 right-6 glass px-4 py-2.5 rounded-xl text-sm text-white border border-border shadow-2xl animate-toast-in z-50">
          {toast}
        </div>
      )}
    </div>
  );
}
