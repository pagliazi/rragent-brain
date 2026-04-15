// ── Market View ──────────────────────────────────────

function PctCell({v}) {
  if (v == null || v === '') return <span className="text-zinc-600">—</span>;
  const n = typeof v === 'string' ? parseFloat(v) : v;
  if (isNaN(n)) return <span className="text-zinc-500">{v}</span>;
  const color = n > 0 ? 'text-red-400' : n < 0 ? 'text-emerald-400' : 'text-zinc-400';
  return <span className={`font-medium tabular-nums ${color}`}>{n > 0 ? '+' : ''}{n.toFixed(2)}%</span>;
}

function StatCard({icon, label, value, sub, color}) {
  return (
    <div className="bg-surface-2 rounded-xl border border-border p-4 flex items-center gap-3">
      <span className="text-2xl">{icon}</span>
      <div className="flex-1 min-w-0">
        <div className="text-[11px] text-zinc-500 uppercase tracking-wider">{label}</div>
        <div className={`text-xl font-bold tabular-nums ${color || 'text-white'}`}>{value}</div>
        {sub && <div className="text-[11px] text-zinc-600 truncate">{sub}</div>}
      </div>
    </div>
  );
}

function MarketView() {
  const [overview, setOverview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState('zt');
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [answerLoading, setAnswerLoading] = useState(false);
  const toast = useContext(ToastContext);

  const load = async () => {
    setLoading(true);
    try {
      const r = await apiGet('/api/market/overview');
      if (r && !r.error) setOverview(r);
    } catch(e) { toast('行情加载失败', 'error'); }
    setLoading(false);
  };

  useEffect(() => { load(); const iv = setInterval(load, 60000); return () => clearInterval(iv); }, []);

  const askQ = async () => {
    if (!question.trim()) return;
    setAnswerLoading(true); setAnswer('');
    const r = await apiPost('/api/command', {cmd:'ask', args:question});
    setAnswer(r.result!=null ? String(r.result) : '');
    setAnswerLoading(false);
    if (r.error) toast('分析失败', 'error');
  };

  if (loading && !overview) return <div className="flex-1 flex items-center justify-center"><Spinner size={8} /></div>;

  const ctx = overview?.time_context || {};
  const stats = overview?.stats || {};
  const ztList = overview?.limitup || [];
  const lbList = overview?.limitstep || [];
  const bkList = overview?.concepts || [];
  const hotList = overview?.hot || [];
  const idxList = overview?.indices || [];
  const topInd = stats.top_industries || [];

  const tabs = [
    {id:'zt', label:'涨停板', icon:'🔴', badge: stats.zt_count},
    {id:'lb', label:'连板股', icon:'🔗', badge: lbList.length},
    {id:'bk', label:'板块', icon:'🏷️', badge: bkList.length},
    {id:'hot', label:'热股', icon:'🔥', badge: hotList.length},
  ];

  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-4 animate-fade-in">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">行情总览</h1>
          <p className="text-[12px] text-zinc-500 mt-0.5">{ctx.freshness_label || ''} · {ctx.phase_cn || ''} · {ctx.timestamp || ''}</p>
        </div>
        <button onClick={load} disabled={loading} className="btn px-3 py-1.5 bg-surface-2 hover:bg-surface-3 rounded-lg text-[12px] text-zinc-400 border border-border hover:border-border-light transition disabled:opacity-50">{loading ? <Spinner /> : '刷新'}</button>
      </div>

      {/* 统计卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <StatCard icon="🔴" label="涨停" value={stats.zt_count || 0} sub={topInd.length > 0 ? topInd.map(([n,c])=>`${n}×${c}`).slice(0,3).join(' ') : ''} color="text-red-400" />
        <StatCard icon="🔗" label="最高连板" value={stats.lb_max_height || 0} sub={lbList[0] ? `${lbList[0].name} ${lbList[0].up_stat||''}` : ''} color="text-amber-400" />
        <StatCard icon="🏷️" label="领涨板块" value={bkList[0]?.board_name || '—'} sub={bkList[0] ? `${bkList[0].pct_chg||0}% 涨${bkList[0].up_count||0}跌${bkList[0].down_count||0}` : ''} color="text-brand-400" />
        <StatCard icon="🔥" label="人气龙头" value={hotList[0]?.ts_name || '—'} sub={hotList[0] ? `#${hotList[0].rank||1} ${hotList[0].pct_change||''}%` : ''} color="text-orange-400" />
      </div>

      {/* 大盘指数 */}
      {idxList.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {idxList.map((idx, i) => {
            const pct = parseFloat(idx.pct_chg || idx.pct_change || 0);
            const color = pct > 0 ? 'border-red-900/40 bg-red-950/20' : pct < 0 ? 'border-emerald-900/40 bg-emerald-950/20' : 'border-border bg-surface-2';
            return (
              <div key={i} className={`px-3 py-2 rounded-xl border ${color} text-center min-w-[120px]`}>
                <div className="text-[11px] text-zinc-500 truncate">{idx.name || idx.ts_name || idx.index_name || ''}</div>
                <div className="text-sm font-bold text-zinc-200 tabular-nums">{idx.close || idx.price || '—'}</div>
                <PctCell v={pct} />
              </div>
            );
          })}
        </div>
      )}

      {/* Tab 切换 */}
      <div className="flex gap-2 flex-wrap">
        {tabs.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`btn flex items-center gap-1.5 px-4 py-2 rounded-xl text-[13px] font-medium border transition-all
              ${tab===t.id ? 'bg-brand-600/20 text-brand-400 border-brand-500/30' : 'bg-surface-2 text-zinc-400 border-border hover:border-border-light'}`}>
            <span>{t.icon}</span>
            <span>{t.label}</span>
            {t.badge > 0 && <span className={`text-[10px] px-1.5 py-0.5 rounded-md font-medium ${tab===t.id ? 'bg-brand-600/30 text-brand-300' : 'bg-surface-3 text-zinc-500'}`}>{t.badge}</span>}
          </button>
        ))}
      </div>

      {/* 数据表格区 */}
      <Card>
        {tab === 'zt' && (
          <div>
            <h3 className="text-sm font-semibold text-zinc-200 mb-3">🔴 涨停板 ({stats.zt_count || 0} 只)</h3>
            {ztList.length === 0 ? <div className="text-zinc-600 text-sm py-4 text-center">暂无涨停数据</div> : (
              <div className="overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead><tr className="text-zinc-500 border-b border-border/50">
                    <th className="text-left py-2 px-1 w-8">#</th>
                    <th className="text-left py-2 px-1">名称</th>
                    <th className="text-left py-2 px-1">代码</th>
                    <th className="text-right py-2 px-1">涨幅</th>
                    <th className="text-right py-2 px-1">封单(万)</th>
                    <th className="text-left py-2 px-1">行业</th>
                    <th className="text-center py-2 px-1">连板</th>
                  </tr></thead>
                  <tbody>{ztList.map((r, i) => {
                    const times = r.limit_times || 1;
                    const fd = Math.round((r.fd_amount || 0) / 10000);
                    return (
                      <tr key={i} className="border-b border-border/20 hover:bg-surface-3/50 transition">
                        <td className="py-2 px-1 text-zinc-600">{i+1}</td>
                        <td className="py-2 px-1 text-zinc-200 font-medium">{r.name || ''}</td>
                        <td className="py-2 px-1 text-zinc-500 font-mono">{r.ts_code || ''}</td>
                        <td className="py-2 px-1 text-right"><PctCell v={r.pct_chg} /></td>
                        <td className="py-2 px-1 text-right text-zinc-400 tabular-nums">{fd > 0 ? fd.toLocaleString() : '—'}</td>
                        <td className="py-2 px-1 text-zinc-500">{r.industry || ''}</td>
                        <td className="py-2 px-1 text-center">{times > 1 ? <span className="px-1.5 py-0.5 bg-red-950/40 text-red-400 rounded text-[10px] font-bold">{times}板</span> : <span className="text-zinc-700">首板</span>}</td>
                      </tr>
                    );
                  })}</tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {tab === 'lb' && (
          <div>
            <h3 className="text-sm font-semibold text-zinc-200 mb-3">🔗 连板股</h3>
            {lbList.length === 0 ? <div className="text-zinc-600 text-sm py-4 text-center">暂无连板数据</div> : (
              <div className="overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead><tr className="text-zinc-500 border-b border-border/50">
                    <th className="text-left py-2 px-1 w-8">#</th>
                    <th className="text-left py-2 px-1">名称</th>
                    <th className="text-left py-2 px-1">代码</th>
                    <th className="text-right py-2 px-1">涨幅</th>
                    <th className="text-left py-2 px-1">连板</th>
                    <th className="text-left py-2 px-1">行业</th>
                  </tr></thead>
                  <tbody>{lbList.map((r, i) => (
                    <tr key={i} className="border-b border-border/20 hover:bg-surface-3/50 transition">
                      <td className="py-2 px-1 text-zinc-600">{i+1}</td>
                      <td className="py-2 px-1 text-zinc-200 font-medium">{r.name || ''}</td>
                      <td className="py-2 px-1 text-zinc-500 font-mono">{r.ts_code || ''}</td>
                      <td className="py-2 px-1 text-right"><PctCell v={r.pct_chg} /></td>
                      <td className="py-2 px-1"><span className="px-2 py-0.5 bg-amber-950/40 text-amber-400 rounded text-[10px] font-bold">{r.up_stat || r.limit_times || ''}</span></td>
                      <td className="py-2 px-1 text-zinc-500">{r.industry || ''}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {tab === 'bk' && (
          <div>
            <h3 className="text-sm font-semibold text-zinc-200 mb-3">🏷️ 概念板块涨幅榜</h3>
            {bkList.length === 0 ? <div className="text-zinc-600 text-sm py-4 text-center">暂无板块数据</div> : (
              <div className="overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead><tr className="text-zinc-500 border-b border-border/50">
                    <th className="text-left py-2 px-1 w-8">#</th>
                    <th className="text-left py-2 px-1">板块</th>
                    <th className="text-right py-2 px-1">涨幅</th>
                    <th className="text-right py-2 px-1 text-emerald-600">涨</th>
                    <th className="text-right py-2 px-1 text-red-600">跌</th>
                    <th className="text-left py-2 px-1">领涨股</th>
                  </tr></thead>
                  <tbody>{bkList.map((r, i) => (
                    <tr key={i} className="border-b border-border/20 hover:bg-surface-3/50 transition">
                      <td className="py-2 px-1 text-zinc-600">{i+1}</td>
                      <td className="py-2 px-1 text-zinc-200 font-medium">{r.board_name || ''}</td>
                      <td className="py-2 px-1 text-right"><PctCell v={r.pct_chg} /></td>
                      <td className="py-2 px-1 text-right text-red-400 tabular-nums">{r.up_count || 0}</td>
                      <td className="py-2 px-1 text-right text-emerald-400 tabular-nums">{r.down_count || 0}</td>
                      <td className="py-2 px-1 text-zinc-400">{r.leading_stock_name || ''}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {tab === 'hot' && (
          <div>
            <h3 className="text-sm font-semibold text-zinc-200 mb-3">🔥 同花顺热股</h3>
            {hotList.length === 0 ? <div className="text-zinc-600 text-sm py-4 text-center">暂无热股数据</div> : (
              <div className="overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead><tr className="text-zinc-500 border-b border-border/50">
                    <th className="text-center py-2 px-1 w-10">排名</th>
                    <th className="text-left py-2 px-1">名称</th>
                    <th className="text-left py-2 px-1">代码</th>
                    <th className="text-right py-2 px-1">涨跌</th>
                    <th className="text-left py-2 px-1">概念</th>
                  </tr></thead>
                  <tbody>{hotList.map((r, i) => {
                    let concept = '';
                    try { const tags = JSON.parse(r.concept || '[]'); concept = Array.isArray(tags) ? tags.slice(0,2).join(' ') : ''; } catch(e) { concept = r.concept || ''; }
                    return (
                      <tr key={i} className="border-b border-border/20 hover:bg-surface-3/50 transition">
                        <td className="py-2 px-1 text-center"><span className={`inline-block w-6 h-6 leading-6 rounded-lg text-[11px] font-bold ${i<3?'bg-orange-600/30 text-orange-300':'bg-surface-3 text-zinc-500'}`}>{r.rank || i+1}</span></td>
                        <td className="py-2 px-1 text-zinc-200 font-medium">{r.ts_name || ''}</td>
                        <td className="py-2 px-1 text-zinc-500 font-mono">{r.ts_code || ''}</td>
                        <td className="py-2 px-1 text-right"><PctCell v={r.pct_change} /></td>
                        <td className="py-2 px-1 text-zinc-600 text-[11px] truncate max-w-[180px]">{concept}</td>
                      </tr>
                    );
                  })}</tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </Card>

      {/* 行业分布 */}
      {topInd.length > 0 && (
        <Card>
          <h3 className="text-sm font-semibold text-zinc-200 mb-3">🏭 涨停行业分布</h3>
          <div className="flex flex-wrap gap-2">
            {topInd.map(([name, count], i) => (
              <div key={i} className="flex items-center gap-1.5 px-3 py-1.5 bg-surface-3 rounded-lg border border-border/50">
                <span className="text-[12px] text-zinc-300">{name}</span>
                <span className="text-[11px] font-bold text-red-400 bg-red-950/40 px-1.5 py-0.5 rounded">{count}</span>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* 自由分析 */}
      <Card>
        <h3 className="text-sm font-semibold text-zinc-300 mb-3">🔬 AI 自由分析</h3>
        <div className="flex gap-2">
          <input value={question} onChange={e=>setQuestion(e.target.value)}
            onKeyDown={e=>e.key==='Enter'&&askQ()}
            placeholder="输入关于市场的问题，AI 会结合实时数据分析..."
            className="flex-1 bg-surface-3 border border-border rounded-xl px-4 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 transition placeholder-zinc-600" />
          <button onClick={askQ} disabled={answerLoading}
            className="btn px-5 py-2.5 bg-brand-600 hover:bg-brand-700 rounded-xl text-sm text-white font-medium transition disabled:opacity-50 shadow-lg shadow-brand-600/20">
            {answerLoading ? <Spinner /> : '分析'}
          </button>
        </div>
        {(answer || answerLoading) && (
          <div className="mt-3 pt-3 border-t border-border">
            <DataBlock data={answer} loading={answerLoading} />
          </div>
        )}
      </Card>
    </div>
  );
}


// ── Tasks View ───────────────────────────────────────

function TaskStepRow({step, index}) {
  const icons = {pending:'⬜',running:'🔵',completed:'✅',failed:'❌',skipped:'⏭️'};
  const elapsed = (step.finished_at && step.started_at) ? ((step.finished_at - step.started_at).toFixed(1) + 's') : '';
  return (
    <div className={`flex items-center gap-2 py-1.5 text-[12px] ${step.status==='running' ? 'text-white' : 'text-zinc-400'}`}>
      <span className="w-4 text-center">{icons[step.status]||'❓'}</span>
      <span className="text-zinc-600 w-5 text-right">{index+1}.</span>
      <span className="flex-1 truncate">{step.description}</span>
      <span className="text-[10px] text-zinc-600 font-mono">{step.agent}.{step.action}</span>
      {elapsed && <span className="text-[10px] text-zinc-600 tabular-nums">{elapsed}</span>}
      {step.error && <span className="text-[10px] text-red-400 truncate max-w-[200px]" title={step.error}>❗{step.error.slice(0,40)}</span>}
    </div>
  );
}

function TaskCard({task, onCancel, onRefresh, expanded, onToggle}) {
  const icons = {pending:'⏳',running:'🔄',paused:'⏸️',completed:'✅',failed:'❌',cancelled:'🚫'};
  const colors = {pending:'border-zinc-700',running:'border-brand-500/40 ring-1 ring-brand-500/10',completed:'border-emerald-500/30',failed:'border-red-500/30',cancelled:'border-zinc-700'};
  const elapsed = task.elapsed ? (task.elapsed < 60 ? task.elapsed.toFixed(0)+'s' : (task.elapsed/60).toFixed(1)+'min') : '';
  const running = task.status === 'running';
  return (
    <Card className={`${colors[task.status]||'border-border'} ${running ? 'glow-brand' : ''}`}>
      <div className="flex items-center gap-3 cursor-pointer" onClick={onToggle}>
        <span className="text-lg">{icons[task.status]||'❓'}</span>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-white truncate">{task.name}</span>
            <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${running ? 'bg-brand-600/20 text-brand-400' : 'bg-surface-3 text-zinc-500'}`}>{task.status}</span>
          </div>
          <div className="flex items-center gap-3 mt-1 text-[10px] text-zinc-500">
            <span>{task.id}</span>
            {elapsed && <span>⏱ {elapsed}</span>}
            <span>{task.steps?.length||0} 步骤</span>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <div className="w-24 bg-surface-3 rounded-full h-1.5 overflow-hidden">
            <div className={`h-full rounded-full transition-all duration-500 ${task.status==='completed' ? 'bg-emerald-500' : task.status==='failed' ? 'bg-red-500' : 'bg-brand-500'}`} style={{width: `${task.progress||0}%`}}></div>
          </div>
          <span className="text-[11px] text-zinc-400 tabular-nums w-8 text-right">{Math.round(task.progress||0)}%</span>
        </div>
      </div>
      {expanded && (
        <div className="mt-3 pt-3 border-t border-border/50 space-y-0.5">
          {(task.steps||[]).map((s,i) => <TaskStepRow key={i} step={s} index={i} />)}
          <div className="flex gap-2 mt-3 pt-2 border-t border-border/30">
            {(task.status==='running'||task.status==='pending') && (
              <button onClick={(e)=>{e.stopPropagation();onCancel(task.id);}} className="btn px-3 py-1.5 text-[11px] bg-red-950/40 hover:bg-red-900/40 text-red-400 rounded-lg border border-red-900/30 transition">取消任务</button>
            )}
            <button onClick={(e)=>{e.stopPropagation();onRefresh();}} className="btn px-3 py-1.5 text-[11px] bg-surface-3 hover:bg-surface-4 text-zinc-400 rounded-lg border border-border transition">刷新</button>
          </div>
        </div>
      )}
    </Card>
  );
}

function TasksView() {
  const [tasks, setTasks] = useState([]);
  const [presets, setPresets] = useState({});
  const [listLoading, setListLoading] = useState(false);
  const [creating, setCreating] = useState('');
  const [expandedId, setExpandedId] = useState('');
  const [progressLog, setProgressLog] = useState({});
  const toast = useContext(ToastContext);
  const sseRefs = useRef({});

  const refresh = async () => {
    setListLoading(true);
    try {
      const r = await apiGet('/api/tasks');
      if (r.tasks) setTasks(r.tasks);
      if (r.presets) setPresets(r.presets);
    } catch(e) { toast('加载失败', 'error'); }
    setListLoading(false);
  };

  const create = async (preset) => {
    setCreating(preset);
    try {
      const r = await apiPost('/api/tasks/create', {preset});
      if (!r.error) {
        toast((presets[preset]?.name || preset) + ' 已创建', 'success');
        setTimeout(refresh, 500);
      } else toast('创建失败', 'error');
    } catch(e) { toast('网络错误', 'error'); }
    setCreating('');
  };

  const cancelTask = async (id) => {
    await apiPost('/api/tasks/'+id+'/cancel', {});
    toast('任务已取消', 'success');
    refresh();
  };

  const subscribeProgress = (taskId) => {
    if (sseRefs.current[taskId]) return;
    try {
      const es = new EventSource('/api/tasks/'+taskId+'/progress');
      sseRefs.current[taskId] = es;
      es.onmessage = (e) => {
        try {
          const d = JSON.parse(e.data);
          if (d.text) setProgressLog(prev => ({...prev, [taskId]: [...(prev[taskId]||[]), d.text].slice(-20)}));
          if (d.event === 'done' || d.status === 'completed' || d.status === 'failed' || d.status === 'cancelled') {
            es.close(); delete sseRefs.current[taskId]; refresh();
          }
        } catch(err) {}
      };
      es.onerror = () => { es.close(); delete sseRefs.current[taskId]; };
    } catch(e) {}
  };

  useEffect(() => { refresh(); const iv = setInterval(refresh, 15000); return () => clearInterval(iv); }, []);
  useEffect(() => {
    tasks.filter(t => t.status === 'running').forEach(t => subscribeProgress(t.id));
  }, [tasks]);
  useEffect(() => () => { Object.values(sseRefs.current).forEach(es => es.close()); }, []);

  const running = tasks.filter(t => t.status === 'running' || t.status === 'pending');
  const finished = tasks.filter(t => t.status !== 'running' && t.status !== 'pending');

  return (
    <div className="flex-1 overflow-y-auto p-6 animate-fade-in">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-xl font-bold text-white">多任务管理</h1>
          <p className="text-[12px] text-zinc-500 mt-0.5">并行任务编排 · 实时进度追踪 · 多 Agent 协同</p>
        </div>
        <div className="flex items-center gap-2">
          {running.length > 0 && <span className="text-[11px] px-2 py-1 bg-brand-600/20 text-brand-400 rounded-lg animate-pulse">{running.length} 运行中</span>}
          <button onClick={refresh} disabled={listLoading} className="btn px-3 py-1.5 rounded-lg text-[11px] bg-surface-2 text-zinc-400 border border-border hover:border-border-light transition disabled:opacity-50">{listLoading ? <Spinner /> : '刷新'}</button>
        </div>
      </div>

      <Card className="mb-4"><h3 className="text-sm font-medium text-zinc-200 mb-3">🗺️ Viking 路由轨迹</h3><div style={{height:'420px'}}><PlanTracePanel /></div></Card>

      <div className="grid md:grid-cols-[1fr_280px] gap-4">
        <div className="space-y-3">
          {running.length > 0 && (<div className="text-[11px] text-zinc-500 uppercase tracking-widest mb-1">⚡ 运行中</div>)}
          {running.map(t => (
            <div key={t.id}>
              <TaskCard task={t} onCancel={cancelTask} onRefresh={refresh} expanded={expandedId===t.id} onToggle={()=>setExpandedId(expandedId===t.id?'':t.id)} />
              {progressLog[t.id] && expandedId===t.id && (
                <div className="mt-1 ml-6 space-y-0.5 text-[10px] text-zinc-500 font-mono max-h-24 overflow-y-auto">
                  {progressLog[t.id].map((l,i) => <div key={i}>{l}</div>)}
                </div>
              )}
            </div>
          ))}
          {finished.length > 0 && (<div className="text-[11px] text-zinc-500 uppercase tracking-widest mb-1 mt-4">📋 历史任务</div>)}
          {finished.slice(0, 15).map(t => (
            <TaskCard key={t.id} task={t} onCancel={cancelTask} onRefresh={refresh} expanded={expandedId===t.id} onToggle={()=>setExpandedId(expandedId===t.id?'':t.id)} />
          ))}
          {tasks.length === 0 && !listLoading && <Card><div className="text-center py-8 text-zinc-600 text-sm">暂无任务记录</div></Card>}
        </div>

        <div className="space-y-2">
          <div className="text-[11px] text-zinc-500 uppercase tracking-widest mb-1">🚀 快速启动</div>
          {PRESETS.map(p => (
            <button key={p.id} onClick={()=>create(p.id)} disabled={!!creating}
              className="w-full flex items-center gap-2.5 px-3 py-2.5 rounded-xl bg-surface-1 border border-border hover:border-brand-500/30 hover:bg-brand-950/10 text-left transition group disabled:opacity-50">
              <span className="text-lg">{p.icon}</span>
              <div className="flex-1 min-w-0">
                <div className="text-[12px] font-semibold text-zinc-200 group-hover:text-brand-400 transition truncate">{p.name}</div>
                <div className="text-[10px] text-zinc-600 truncate">{p.desc}</div>
              </div>
              {creating===p.id ? <Spinner /> : <span className="text-[10px] text-zinc-600">{p.steps}步</span>}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}


// ── Intraday / Quant / System / DailyLog / LLMConfig ──
// (preserved from original — full implementations below)

function IntradayView() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [selectLoading, setSelectLoading] = useState(false);
  const [scanLoading, setScanLoading] = useState(false);
  const [monitorLoading, setMonitorLoading] = useState(false);
  const [strategy, setStrategy] = useState('');

  const loadStatus = async () => { setLoading(true); try { const r = await fetch('/api/intraday/status', {credentials:'same-origin'}); if (r.ok) setStatus(await r.json()); } catch(e) {} setLoading(false); };
  useEffect(() => { loadStatus(); const t = setInterval(loadStatus, 30000); return () => clearInterval(t); }, []);
  const runSelect = async () => { setSelectLoading(true); try { await fetch('/api/intraday/select', {method:'POST', headers:{'Content-Type':'application/json'}, credentials:'same-origin', body: JSON.stringify({strategy})}); } catch(e) {} setSelectLoading(false); setTimeout(loadStatus, 2000); };
  const runScan = async () => { setScanLoading(true); try { await fetch('/api/intraday/scan', {method:'POST', headers:{'Content-Type':'application/json'}, credentials:'same-origin', body: JSON.stringify({strategy_logic: strategy || '默认策略监控'})}); } catch(e) {} setScanLoading(false); setTimeout(loadStatus, 2000); };

  const pool = status?.pool || {};
  const signals = status?.recent_signals || [];
  const scans = status?.recent_scans || [];

  return (
    <div className="flex-1 h-full overflow-y-auto p-6 space-y-6 animate-fade-in">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-zinc-100 flex items-center gap-2"><span className="text-2xl">⏱️</span> 盘中实时监控</h2>
          <p className="text-sm text-zinc-500 mt-1">盘后 ClickHouse 选股 → Redis 存池 → 盘中 DolphinDB 扫描</p>
        </div>
        <button onClick={loadStatus} disabled={loading} className="btn px-3 py-1.5 bg-surface-3 text-zinc-300 text-sm rounded-lg border border-border hover:bg-surface-4">{loading ? '刷新中...' : '🔄 刷新'}</button>
      </div>
      <div className="bg-surface-2 rounded-xl border border-border p-5">
        <h3 className="text-sm font-medium text-zinc-300 mb-3">📋 目标股票池</h3>
        {pool.count > 0 ? (<div><div className="flex items-center gap-4 mb-2"><span className="text-2xl font-bold text-brand-400">{pool.count}</span><span className="text-sm text-zinc-500">只目标股</span><span className="text-xs text-zinc-600 ml-auto">策略: {pool.strategy || '-'} | 日期: {pool.date || '-'}</span></div><div className="flex flex-wrap gap-1.5 mt-2">{(pool.stocks || []).map(s => (<span key={s} className="px-2 py-0.5 bg-surface-3 text-xs text-zinc-400 rounded">{s}</span>))}{pool.count > 20 && <span className="text-xs text-zinc-600">+{pool.count - 20} 只</span>}</div></div>) : (<p className="text-zinc-600 text-sm">暂无目标股票池</p>)}
      </div>
      <div className="bg-surface-2 rounded-xl border border-border p-5">
        <h3 className="text-sm font-medium text-zinc-300 mb-3">🎮 操作</h3>
        <div className="flex items-center gap-3 mb-3"><input value={strategy} onChange={e => setStrategy(e.target.value)} placeholder="策略描述（可选）" className="flex-1 px-3 py-2 bg-surface-3 border border-border rounded-lg text-sm text-zinc-200 placeholder-zinc-600 focus:outline-none focus:border-brand-500" /></div>
        <div className="flex gap-3 flex-wrap">
          <button onClick={runSelect} disabled={selectLoading} className="btn px-4 py-2 bg-brand-600 text-white text-sm rounded-lg hover:bg-brand-500 disabled:opacity-50">{selectLoading ? '⏳ 选股中...' : '🌙 盘后选股'}</button>
          <button onClick={runScan} disabled={scanLoading || !pool.count} className="btn px-4 py-2 bg-emerald-600 text-white text-sm rounded-lg hover:bg-emerald-500 disabled:opacity-50">{scanLoading ? '⏳ 扫描中...' : '⚡ 单次扫描'}</button>
          <button onClick={async () => { setMonitorLoading(true); try { const endpoint = status?.monitoring ? '/api/intraday/stop' : '/api/intraday/monitor'; await fetch(endpoint, {method:'POST', headers:{'Content-Type':'application/json'}, credentials:'same-origin', body: JSON.stringify({strategy_logic: strategy})}); } catch(e) {} setMonitorLoading(false); setTimeout(loadStatus, 2000); }} disabled={monitorLoading || (!status?.monitoring && !pool.count)} className={`btn px-4 py-2 text-white text-sm rounded-lg disabled:opacity-50 ${status?.monitoring ? 'bg-red-600 hover:bg-red-500' : 'bg-amber-600 hover:bg-amber-500'}`}>{monitorLoading ? '⏳ ...' : (status?.monitoring ? '⏹ 停止监控' : '🔄 持续监控')}</button>
        </div>
        {status?.monitoring && <p className="text-xs text-emerald-400 mt-2">🟢 自动监控运行中 — 每 {status?.auto_scan_interval || 120}s 扫描一次</p>}
      </div>
      <div className="bg-surface-2 rounded-xl border border-border p-5">
        <h3 className="text-sm font-medium text-zinc-300 mb-3">🎯 最近信号</h3>
        {signals.length > 0 ? (<div className="space-y-2">{signals.slice(0, 10).map((sigGroup, i) => { const items = Array.isArray(sigGroup) ? sigGroup : [sigGroup]; return items.map((sig, j) => (<div key={`${i}-${j}`} className="flex items-center gap-3 px-3 py-2 bg-surface-3 rounded-lg text-sm"><span className={`font-medium ${sig.signal === 'BUY' ? 'text-red-400' : 'text-green-400'}`}>{sig.signal || 'SIGNAL'}</span><span className="text-zinc-200 font-mono">{sig.ts_code || '-'}</span><span className="text-zinc-400">@ {sig.price || '-'}</span><span className="text-zinc-500 text-xs ml-auto">{sig.reason || ''}</span>{sig.strength && <div className="w-12 h-1.5 bg-surface-4 rounded-full overflow-hidden"><div className="h-full bg-brand-400 rounded-full" style={{width: `${(sig.strength||0)*100}%`}} /></div>}</div>)); })}</div>) : (<p className="text-zinc-600 text-sm">暂无盘中信号</p>)}
      </div>
      <div className="bg-surface-2 rounded-xl border border-border p-5">
        <h3 className="text-sm font-medium text-zinc-300 mb-3">📜 扫描日志</h3>
        {scans.length > 0 ? (<div className="space-y-1.5">{scans.slice(0, 10).map((log, i) => (<div key={i} className="flex items-center gap-3 text-xs text-zinc-500"><span className="font-mono text-zinc-600">{log.timestamp ? log.timestamp.split('T')[1]?.split('.')[0] : '-'}</span><span className="text-zinc-400">扫描 {log.scanned || 0} 只</span><span className={log.signals?.length > 0 ? 'text-red-400 font-medium' : 'text-zinc-600'}>{log.signals?.length > 0 ? `🎯 ${log.signals.length} 只命中` : '无信号'}</span></div>))}</div>) : (<p className="text-zinc-600 text-sm">暂无扫描记录</p>)}
      </div>
      <div className="bg-surface-2 rounded-xl border border-border p-5">
        <h3 className="text-sm font-medium text-zinc-300 mb-3">🤖 Agent 状态</h3>
        <div className="grid grid-cols-2 gap-3 text-sm">
          <div className="flex items-center gap-2"><span className={status?.monitoring ? 'text-emerald-400' : 'text-zinc-600'}>{status?.monitoring ? '🟢' : '🔴'}</span><span className="text-zinc-400">监控: {status?.monitoring ? '运行中' : '未启动'}</span></div>
          <div className="flex items-center gap-2"><span className={status?.is_trading_hours ? 'text-emerald-400' : 'text-zinc-600'}>{status?.is_trading_hours ? '📈' : '🌙'}</span><span className="text-zinc-400">市场: {status?.market_phase || '-'}</span></div>
        </div>
      </div>
      <div className="bg-surface-1 rounded-xl border border-border/50 p-4 text-xs text-zinc-600">
        <p className="font-medium text-zinc-500 mb-1">数据架构 (Phase D)</p>
        <p>盘后 20:00 → vectorbt + ClickHouse 全市场回测选股 → Redis 存池</p>
        <p>盘中 09:30~15:00 → DolphinDB 1.17 亿行实时数据扫描 → 信号推送</p>
      </div>
    </div>
  );
}
