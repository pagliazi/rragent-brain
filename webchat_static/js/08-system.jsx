// ── Infrastructure Monitor View ─────────────────────

function InfraMonitorView() {
  const [tab, setTab] = useState('alerts');
  const [alertsData, setAlertsData] = useState('');
  const [targetsData, setTargetsData] = useState('');
  const [grafanaData, setGrafanaData] = useState('');
  const [patrolData, setPatrolData] = useState('');
  const [diagData, setDiagData] = useState(null);
  const [loading, setLoading] = useState({});
  const toast = useContext(ToastContext);

  const load = async (type) => {
    setLoading(p=>({...p,[type]:true}));
    try {
      if (type==='alerts') { const r = await apiGet('/api/monitor/alerts'); setAlertsData(r.text||''); }
      else if (type==='targets') { const r = await apiGet('/api/monitor/targets'); setTargetsData(r.text||''); }
      else if (type==='grafana') { const r = await apiGet('/api/monitor/grafana'); setGrafanaData(r.text||''); }
      else if (type==='patrol') { const r = await apiPost('/api/monitor/patrol', {}); setPatrolData(r.text||''); }
      else if (type==='diag') { const r = await apiGet('/api/diagnostics'); setDiagData(r); }
    } catch(e) { toast('加载失败','error'); }
    setLoading(p=>({...p,[type]:false}));
  };

  useEffect(() => { load('alerts'); load('diag'); }, []);

  const tabItems = [{id:'alerts',label:'告警',icon:'🔔'},{id:'targets',label:'目标',icon:'🎯'},{id:'grafana',label:'Grafana',icon:'📊'},{id:'patrol',label:'巡检',icon:'🛡️'},{id:'diag',label:'诊断',icon:'🔬'}];
  const statusColor = (s) => s==='ok'||s==='online' ? 'text-emerald-400' : s==='warn'||s==='slow' ? 'text-yellow-400' : 'text-red-400';
  const statusDot = (s) => s==='ok'||s==='online' ? 'bg-emerald-400' : s==='warn'||s==='slow' ? 'bg-yellow-400' : 'bg-red-400';

  return (
    <div className="flex-1 overflow-y-auto p-6 animate-fade-in">
      <h1 className="text-xl font-bold text-white mb-4">基础设施监控</h1>
      <div className="flex gap-2 mb-4 flex-wrap">
        {tabItems.map(t => (
          <button key={t.id} onClick={() => { setTab(t.id); if (!{alerts:alertsData,targets:targetsData,grafana:grafanaData,patrol:patrolData,diag:diagData}[t.id]) load(t.id); }}
            className={`btn px-4 py-2 rounded-xl text-[13px] border transition ${tab===t.id ? 'bg-brand-600/20 text-brand-400 border-brand-500/30' : 'bg-surface-2 text-zinc-400 border-border hover:border-border-light'}`}>
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {tab==='alerts' && (<Card>
        <div className="flex items-center justify-between mb-3"><h3 className="text-sm font-medium text-zinc-200">Prometheus 活跃告警</h3><button onClick={()=>load('alerts')} className="btn text-[11px] text-brand-400">{loading.alerts ? <Spinner/> : '刷新'}</button></div>
        <DataBlock data={alertsData} loading={loading.alerts} placeholder="点击刷新加载告警"/>
      </Card>)}

      {tab==='targets' && (<Card>
        <div className="flex items-center justify-between mb-3"><h3 className="text-sm font-medium text-zinc-200">Prometheus 目标健康</h3><button onClick={()=>load('targets')} className="btn text-[11px] text-brand-400">{loading.targets ? <Spinner/> : '刷新'}</button></div>
        <DataBlock data={targetsData} loading={loading.targets} placeholder="点击刷新加载目标"/>
      </Card>)}

      {tab==='grafana' && (<Card>
        <div className="flex items-center justify-between mb-3"><h3 className="text-sm font-medium text-zinc-200">Grafana 告警</h3><button onClick={()=>load('grafana')} className="btn text-[11px] text-brand-400">{loading.grafana ? <Spinner/> : '刷新'}</button></div>
        <DataBlock data={grafanaData} loading={loading.grafana} placeholder="点击刷新加载 Grafana 告警"/>
      </Card>)}

      {tab==='patrol' && (<Card>
        <div className="flex items-center justify-between mb-3"><h3 className="text-sm font-medium text-zinc-200">一键巡检</h3><button onClick={()=>load('patrol')} className="btn text-[11px] text-brand-400">{loading.patrol ? <Spinner/> : '执行巡检'}</button></div>
        <DataBlock data={patrolData} loading={loading.patrol} placeholder="点击执行巡检"/>
      </Card>)}

      {tab==='diag' && (<div className="space-y-4">
        <div className="flex items-center justify-between"><h3 className="text-sm font-medium text-zinc-200">系统综合诊断</h3><button onClick={()=>load('diag')} className="btn text-[11px] text-brand-400">{loading.diag ? <Spinner/> : '刷新'}</button></div>
        {diagData && (<>
          <Card>
            <h4 className="text-[13px] font-medium text-zinc-300 mb-3">Agent 健康</h4>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
              {Object.entries(diagData.agents||{}).map(([name, info]) => (
                <div key={name} className="flex items-center gap-2 px-3 py-2 bg-surface-3 rounded-xl">
                  <div className={`w-2 h-2 rounded-full ${statusDot(info.status)}`}></div>
                  <span className="text-[12px] text-zinc-300 font-medium">{name}</span>
                  <span className={`text-[11px] ml-auto ${statusColor(info.status)}`}>{info.age_s != null ? info.age_s + 's' : info.status}</span>
                </div>
              ))}
            </div>
          </Card>
          <div className="grid md:grid-cols-2 gap-4">
            <Card>
              <h4 className="text-[13px] font-medium text-zinc-300 mb-2">Redis</h4>
              <div className="space-y-1 text-[12px]">
                <div className="flex justify-between"><span className="text-zinc-500">Ping</span><span className={diagData.redis?.ping ? 'text-emerald-400' : 'text-red-400'}>{diagData.redis?.ping ? 'OK' : 'FAIL'}</span></div>
                <div className="flex justify-between"><span className="text-zinc-500">内存</span><span className="text-zinc-300">{diagData.redis?.used_memory_human || '?'}</span></div>
                <div className="flex justify-between"><span className="text-zinc-500">连接数</span><span className="text-zinc-300">{diagData.redis?.connected_clients || '?'}</span></div>
              </div>
            </Card>
            <Card>
              <h4 className="text-[13px] font-medium text-zinc-300 mb-2">LLM</h4>
              <pre className="text-[11px] text-zinc-400 whitespace-pre-wrap">{JSON.stringify(diagData.llm||{}, null, 2)}</pre>
            </Card>
          </div>
          <Card>
            <h4 className="text-[13px] font-medium text-zinc-300 mb-2">记忆</h4>
            <div className="text-[12px] text-zinc-400">会话数: {diagData.memory?.session_count ?? '?'}</div>
          </Card>
        </>)}
        {!diagData && !loading.diag && <Card><div className="text-center py-8 text-zinc-600 text-sm">点击刷新获取诊断数据</div></Card>}
      </div>)}
    </div>
  );
}


function PlanTracePanel() {
  const [plans, setPlans] = React.useState([]);
  const [selected, setSelected] = React.useState(null);
  const [detail, setDetail] = React.useState(null);
  const [loading, setLoading] = React.useState(false);
  const fetchHistory = async () => {
    setLoading(true);
    try { const res = await fetch('/api/plan/history?limit=30'); const data = await res.json(); setPlans(data.plans || []); } catch(e) {}
    setLoading(false);
  };
  const fetchDetail = async (id) => {
    setSelected(id); setDetail(null);
    try { const res = await fetch(`/api/plan/${id}`); const data = await res.json(); setDetail(data); } catch(e) {}
  };
  React.useEffect(() => { fetchHistory(); }, []);
  const levelColor = {'L0':'text-accent-emerald','L1':'text-accent-blue','L2':'text-accent-amber'};
  const fmtTs = ts => ts ? new Date(ts * 1000).toLocaleTimeString('zh-CN') : '';
  return React.createElement('div', {className:'flex gap-4 h-full'},
    React.createElement('div', {className:'w-72 flex-shrink-0 bg-surface-2 rounded-xl border border-border p-3 flex flex-col gap-2 overflow-y-auto'},
      React.createElement('div', {className:'flex items-center justify-between mb-1'},
        React.createElement('span', {className:'text-sm font-semibold text-white'}, '路由轨迹'),
        React.createElement('button', {onClick: fetchHistory, className:'text-xs text-brand-400 hover:text-brand-300 px-2 py-0.5 rounded'}, loading ? '…' : '刷新')
      ),
      plans.map(p => React.createElement('div', {key: p.id, onClick: () => fetchDetail(p.id),
        className: `cursor-pointer rounded-lg p-2.5 border text-xs transition-all ${selected === p.id ? 'border-brand-500 bg-brand-800/30' : 'border-border hover:border-border-lighter bg-surface-3'}`,
      },
        React.createElement('div', {className:'flex items-center gap-1.5 mb-1'},
          React.createElement('span', {className:`font-bold text-xs ${levelColor[p.route_level]||'text-gray-400'}`}, p.route_level || '?'),
          React.createElement('span', {className:'text-gray-300 truncate flex-1'}, p.input),
        ),
        React.createElement('div', {className:'flex gap-2 text-gray-500'},
          React.createElement('span', {}, `${p.steps}步`),
          p.latency_ms && React.createElement('span', {}, `${p.latency_ms}ms`),
          React.createElement('span', {}, fmtTs(p.ts)),
        )
      ))
    ),
    detail ? React.createElement('div', {className:'flex-1 bg-surface-2 rounded-xl border border-border p-4 overflow-y-auto text-xs space-y-4'},
      React.createElement('div', {className:'flex items-center gap-3 pb-2 border-b border-border'},
        React.createElement('span', {className:`font-bold text-base ${levelColor[detail.route_level]||'text-gray-400'}`}, detail.route_level),
        React.createElement('span', {className:'text-white font-medium flex-1'}, detail.input),
      ),
      detail.steps?.length > 0 && React.createElement('div', {className:'space-y-2'},
        detail.steps.map((s, i) => {
          const st = s.step || {}; const ok = s.ok !== false;
          return React.createElement('div', {key:i, className:`rounded-lg p-2.5 border ${ok ? 'border-accent-emerald/30 bg-surface-3' : 'border-accent-rose/30 bg-surface-3'}`},
            React.createElement('div', {className:'flex items-center gap-2 mb-1'},
              React.createElement('span', {className:`text-xs font-bold ${ok ? 'text-accent-emerald' : 'text-accent-rose'}`}, ok ? '✓' : '✗'),
              React.createElement('span', {className:'text-gray-300'}, `${st.agent||'?'}.${st.action||'?'}`),
            ),
            React.createElement('p', {className:'text-gray-400 whitespace-pre-wrap break-all line-clamp-4'}, s.result)
          );
        })
      ),
      React.createElement('div', {className:'space-y-1'},
        React.createElement('div', {className:'text-gray-400 font-semibold'}, '最终输出'),
        React.createElement('p', {className:'text-white whitespace-pre-wrap bg-surface-3 rounded p-2'}, detail.final_output)
      )
    ) : React.createElement('div', {className:'flex-1 flex items-center justify-center text-gray-600 text-sm'}, '← 选择一条路由记录查看详情')
  );
}


// ── Agent & Skills View ──────────────────────────────

function AgentSkillsView() {
  const [tab, setTab] = useState('agents');
  const [agentsData, setAgentsData] = useState(null);
  const [skillsData, setSkillsData] = useState(null);
  const [factorsData, setFactorsData] = useState(null);
  const [reflectData, setReflectData] = useState(null);
  const [loading, setLoading] = useState({});
  const [expandedAgent, setExpandedAgent] = useState(null);
  const [expandedFactor, setExpandedFactor] = useState(null);
  const [factorDetail, setFactorDetail] = useState('');
  const toast = useContext(ToastContext);

  const load = async (type) => {
    setLoading(p=>({...p,[type]:true}));
    try {
      if (type==='agents') { const r = await apiGet('/api/agents/info'); setAgentsData(r.agents||{}); }
      else if (type==='skills') { const r = await apiGet('/api/skills'); setSkillsData(r.agents||[]); }
      else if (type==='factors') { const r = await apiGet('/api/digger/factors'); setFactorsData(r); }
      else if (type==='reflect') { const r = await apiGet('/api/reflect/stats'); setReflectData(r.text||''); }
    } catch(e) { toast('加载失败','error'); }
    setLoading(p=>({...p,[type]:false}));
  };

  const loadFactorDetail = async (fid) => {
    if (expandedFactor === fid) { setExpandedFactor(null); return; }
    setExpandedFactor(fid);
    try {
      const r = await apiPost('/api/command', {cmd:'factor_detail',args:fid});
      setFactorDetail(r.result!=null ? String(r.result) : '');
    } catch(e) { setFactorDetail('加载失败'); }
  };

  useEffect(() => { load('agents'); load('skills'); }, []);

  const tabItems = [{id:'agents',label:'Agent 全景',icon:'🤖'},{id:'skills',label:'技能清单',icon:'📋'},{id:'factors',label:'因子库',icon:'📊'},{id:'reflect',label:'反思引擎',icon:'🔄'}];
  const statusDot = (s) => s==='online' ? 'bg-emerald-400' : s==='slow' ? 'bg-yellow-400' : 'bg-zinc-600';
  const statusLabel = (s) => s==='online' ? '在线' : s==='slow' ? '缓慢' : '离线';

  return (
    <div className="flex-1 overflow-y-auto p-6 animate-fade-in">
      <h1 className="text-xl font-bold text-white mb-4">Agent & Skills</h1>
      <div className="flex gap-2 mb-4 flex-wrap">
        {tabItems.map(t => (
          <button key={t.id} onClick={() => { setTab(t.id); if (t.id==='factors'&&!factorsData) load('factors'); if (t.id==='reflect'&&!reflectData) load('reflect'); }}
            className={`btn px-4 py-2 rounded-xl text-[13px] border transition ${tab===t.id ? 'bg-brand-600/20 text-brand-400 border-brand-500/30' : 'bg-surface-2 text-zinc-400 border-border hover:border-border-light'}`}>
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {tab==='agents' && (<div className="space-y-3">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm text-zinc-400">{agentsData ? Object.keys(agentsData).length+' 个 Agent' : ''}</span>
          <button onClick={()=>load('agents')} className="btn text-[11px] text-brand-400">{loading.agents ? <Spinner/> : '刷新'}</button>
        </div>
        {agentsData && Object.entries(agentsData).sort(([,a],[,b])=>(a.status==='online'?0:1)-(b.status==='online'?0:1)).map(([name, info]) => (
          <Card key={name} className="!p-0 overflow-hidden">
            <button onClick={()=>setExpandedAgent(expandedAgent===name?null:name)}
              className="w-full flex items-center gap-3 px-4 py-3 hover:bg-surface-2 transition text-left">
              <div className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${statusDot(info.status)}`}></div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold text-white">{name}</span>
                  <span className="text-[10px] text-zinc-500">{statusLabel(info.status)}{info.pid ? ` · PID ${info.pid}` : ''}</span>
                </div>
                {info.description && <div className="text-[11px] text-zinc-500 truncate mt-0.5">{info.description}</div>}
              </div>
              <svg className={`w-4 h-4 text-zinc-500 transition-transform ${expandedAgent===name?'rotate-180':''}`} fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>
            </button>
            {expandedAgent===name && info.skills?.length > 0 && (
              <div className="px-4 pb-3 border-t border-border/50">
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {info.skills.map(s => (
                    <span key={s} className="px-2 py-0.5 bg-surface-3 rounded-lg text-[11px] text-zinc-300 border border-border">{s}</span>
                  ))}
                </div>
              </div>
            )}
          </Card>
        ))}
        {!agentsData && !loading.agents && <Card><div className="text-center py-8 text-zinc-600 text-sm">点击刷新加载数据</div></Card>}
      </div>)}

      {tab==='skills' && (<div className="space-y-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm text-zinc-400">{skillsData ? skillsData.length+' 个 Agent Skills' : ''}</span>
          <button onClick={()=>load('skills')} className="btn text-[11px] text-brand-400">{loading.skills ? <Spinner/> : '刷新'}</button>
        </div>
        {skillsData && skillsData.map(agent => (
          <Card key={agent.agent}>
            <h3 className="text-sm font-semibold text-white mb-1">{agent.agent}</h3>
            {agent.description && <p className="text-[11px] text-zinc-500 mb-3">{agent.description}</p>}
            <div className="space-y-1.5">
              {agent.skills.map(s => (
                <div key={s.name} className="flex items-start gap-2 px-3 py-2 bg-surface-3 rounded-xl">
                  <span className="text-brand-400 text-[11px] font-mono font-bold mt-px flex-shrink-0">{s.name}</span>
                  <span className="text-[11px] text-zinc-400 flex-1">{s.description}</span>
                  {s.trigger && <span className="text-[10px] text-zinc-600 flex-shrink-0">[{s.trigger}]</span>}
                </div>
              ))}
            </div>
          </Card>
        ))}
        {!skillsData && !loading.skills && <Card><div className="text-center py-8 text-zinc-600 text-sm">点击刷新加载数据</div></Card>}
      </div>)}

      {tab==='factors' && (<div className="space-y-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm text-zinc-400">
            {factorsData?.stats ? `活跃 ${factorsData.stats.active_count||0} / 衰减 ${factorsData.stats.decayed_count||0} / 总 ${factorsData.stats.total_count||0}` : ''}
          </span>
          <button onClick={()=>load('factors')} className="btn text-[11px] text-brand-400">{loading.factors ? <Spinner/> : '刷新'}</button>
        </div>
        {factorsData?.stats && (
          <Card>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-center">
              <div><div className="text-lg font-bold text-white">{factorsData.stats.active_count||0}</div><div className="text-[10px] text-zinc-500">活跃因子</div></div>
              <div><div className="text-lg font-bold text-white">{(factorsData.stats.best_sharpe||0).toFixed(3)}</div><div className="text-[10px] text-zinc-500">最佳 Sharpe</div></div>
              <div><div className="text-lg font-bold text-white">{(factorsData.stats.best_ir||0).toFixed(3)}</div><div className="text-[10px] text-zinc-500">最佳 IR</div></div>
              <div><div className="text-lg font-bold text-white">{(factorsData.stats.avg_sharpe||0).toFixed(3)}</div><div className="text-[10px] text-zinc-500">平均 Sharpe</div></div>
            </div>
            {factorsData.stats.ready_to_combine && <div className="mt-2 text-center text-xs text-accent-amber font-medium">已达融合阈值</div>}
          </Card>
        )}
        {factorsData?.factors?.map(f => (
          <Card key={f.id} className="!p-0 overflow-hidden">
            <button onClick={()=>loadFactorDetail(f.id)}
              className="w-full flex items-center gap-3 px-4 py-3 hover:bg-surface-2 transition text-left">
              <div className={`w-2 h-2 rounded-full flex-shrink-0 ${f.status==='active'?'bg-emerald-400':'bg-red-400'}`}></div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-[12px] font-mono text-zinc-300">{f.id}</span>
                  <span className="text-[10px] text-zinc-500">{f.theme}{f.sub_theme?'/'+f.sub_theme:''}</span>
                </div>
                <div className="flex gap-3 mt-0.5 text-[11px] text-zinc-500">
                  <span>Sharpe <b className="text-zinc-300">{f.sharpe?.toFixed(3)}</b></span>
                  <span>IR <b className="text-zinc-300">{f.ir?.toFixed(3)}</b></span>
                  <span>WR <b className="text-zinc-300">{(f.win_rate*100).toFixed(1)}%</b></span>
                  <span>DD <b className="text-zinc-300">{(f.max_drawdown*100).toFixed(1)}%</b></span>
                </div>
              </div>
              <svg className={`w-4 h-4 text-zinc-500 transition-transform ${expandedFactor===f.id?'rotate-180':''}`} fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></svg>
            </button>
            {expandedFactor===f.id && (
              <div className="px-4 pb-3 border-t border-border/50">
                <DataBlock data={factorDetail} loading={!factorDetail} />
              </div>
            )}
          </Card>
        ))}
        {(!factorsData || !factorsData.factors?.length) && !loading.factors && <Card><div className="text-center py-8 text-zinc-600 text-sm">{factorsData ? '因子库为空' : '点击刷新加载数据'}</div></Card>}
      </div>)}

      {tab==='reflect' && (<div className="space-y-4">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm text-zinc-400">反思引擎数据</span>
          <button onClick={()=>load('reflect')} className="btn text-[11px] text-brand-400">{loading.reflect ? <Spinner/> : '刷新'}</button>
        </div>
        <Card>
          <DataBlock data={reflectData} loading={loading.reflect} placeholder="点击刷新获取反思引擎状态" />
        </Card>
        <div className="flex gap-2">
          <button onClick={async()=>{setLoading(p=>({...p,insight:true})); const r=await apiGet('/api/reflect/insight'); setReflectData(r.text||''); setLoading(p=>({...p,insight:false}));}}
            className="btn px-4 py-2 bg-surface-2 hover:bg-surface-3 rounded-xl text-[12px] text-zinc-300 border border-border hover:border-border-light transition">
            {loading.insight ? <Spinner/> : '今日洞察'}
          </button>
          <button onClick={async()=>{setLoading(p=>({...p,weekly:true})); const r=await apiPost('/api/command',{cmd:'reflect_weekly'}); setReflectData(r.result!=null?String(r.result):''); setLoading(p=>({...p,weekly:false}));}}
            className="btn px-4 py-2 bg-surface-2 hover:bg-surface-3 rounded-xl text-[12px] text-zinc-300 border border-border hover:border-border-light transition">
            {loading.weekly ? <Spinner/> : '周报'}
          </button>
        </div>
      </div>)}
    </div>
  );
}


function SystemView() {
  const panels = [{title:'LLM 路由',icon:'🤖',cmd:'llm_status'},{title:'Embedding',icon:'🧠',cmd:'embed_status'},{title:'数据源',icon:'📡',cmd:'data_source_status'},{title:'SOUL 守护',icon:'🛡️',cmd:'soul_check'},{title:'记忆健康',icon:'💊',cmd:'memory_health'},{title:'记忆卫生',icon:'🧹',cmd:'memory_hygiene'}];
  const [results, setResults] = useState({});
  const [loading, setLoading] = useState({});
  const [customCmd, setCustomCmd] = useState('');
  const [customArgs, setCustomArgs] = useState('');
  const [customResult, setCustomResult] = useState('');
  const [customLoading, setCustomLoading] = useState(false);
  const toast = useContext(ToastContext);
  const refresh = async (cmd) => { setLoading(prev=>({...prev,[cmd]:true})); const r = await apiPost('/api/command', {cmd}); setResults(prev=>({...prev,[cmd]:r.result!=null ? String(r.result) : ''})); setLoading(prev=>({...prev,[cmd]:false})); };
  const refreshAll = () => { panels.forEach(p => refresh(p.cmd)); };
  const runCustom = async () => { if (!customCmd.trim()) return; setCustomLoading(true); const r = await apiPost('/api/command', {cmd:customCmd.trim(),args:customArgs.trim()}); setCustomResult(r.result!=null ? String(r.result) : ''); setCustomLoading(false); toast('命令已执行', r.error ? 'error' : 'success'); };
  useEffect(() => { refreshAll(); }, []);
  return (
    <div className="flex-1 overflow-y-auto p-6 space-y-4 animate-fade-in">
      <div className="flex items-center justify-between mb-1"><h1 className="text-xl font-bold text-white">系统状态</h1><button onClick={refreshAll} className="btn px-3 py-1.5 bg-surface-2 hover:bg-surface-3 rounded-lg text-[12px] text-zinc-400 border border-border hover:border-border-light transition">全部刷新</button></div>
      <LLMConfigPanel />
      <div className="grid md:grid-cols-2 gap-4">{panels.map(p => (<Card key={p.cmd}><div className="flex items-center justify-between mb-3"><h3 className="text-sm font-medium text-zinc-200">{p.icon} {p.title}</h3><button onClick={()=>refresh(p.cmd)} className="btn px-2 py-1 text-[11px] text-brand-400 hover:text-brand-300 hover:bg-brand-600/10 rounded-lg transition">{loading[p.cmd] ? <Spinner /> : '刷新'}</button></div><div className="max-h-52 overflow-auto"><DataBlock data={results[p.cmd]} loading={loading[p.cmd]} placeholder="点击刷新获取数据" /></div></Card>))}</div>
      <Card><h3 className="text-sm font-medium text-zinc-200 mb-3">🔧 自定义命令</h3><div className="flex gap-2"><input value={customCmd} onChange={e=>setCustomCmd(e.target.value)} placeholder="命令" className="w-28 bg-surface-3 border border-border rounded-xl px-3 py-2.5 text-sm text-zinc-300 focus:outline-none focus:border-brand-500/40 transition" /><input value={customArgs} onChange={e=>setCustomArgs(e.target.value)} placeholder="参数" onKeyDown={e=>e.key==='Enter'&&runCustom()} className="flex-1 bg-surface-3 border border-border rounded-xl px-3 py-2.5 text-sm text-zinc-300 focus:outline-none focus:border-brand-500/40 transition" /><button onClick={runCustom} disabled={customLoading} className="btn px-4 py-2.5 bg-surface-3 hover:bg-surface-4 rounded-xl text-sm text-zinc-300 border border-border hover:border-border-light transition disabled:opacity-50">{customLoading ? <Spinner /> : '执行'}</button></div>{(customResult || customLoading) && (<div className="mt-3 pt-3 border-t border-border max-h-64 overflow-auto"><DataBlock data={customResult} loading={customLoading} /></div>)}</Card>
    </div>
  );
}


// ── Profile View ─────────────────────────────────────

function ProfileView({user, onUpdateUser}) {
  const [displayName, setDisplayName] = useState(user?.display_name || '');
  const [avatar, setAvatar] = useState(user?.avatar || '🦀');
  const [newPwd, setNewPwd] = useState('');
  const [confirmPwd, setConfirmPwd] = useState('');
  const [saving, setSaving] = useState(false);
  const [avatars, setAvatars] = useState([]);
  const toast = useContext(ToastContext);

  useEffect(() => {
    apiGet('/api/admin/avatars').then(d => { if (d.avatars) setAvatars(d.avatars); });
  }, []);

  const handleSave = async () => {
    if (newPwd && newPwd !== confirmPwd) { toast('两次密码不一致', 'error'); return; }
    setSaving(true);
    const body = {display_name: displayName, avatar};
    if (newPwd) body.password = newPwd;
    const r = await apiPut('/api/auth/profile', body);
    if (r.ok) {
      toast('已保存', 'success');
      if (r.token) setToken(r.token);
      if (r.user && onUpdateUser) onUpdateUser(r.user);
      setNewPwd(''); setConfirmPwd('');
    } else { toast('保存失败', 'error'); }
    setSaving(false);
  };

  return (
    <div className="flex-1 overflow-y-auto p-6 animate-fade-in">
      <h1 className="text-xl font-bold text-white mb-6">个人资料</h1>
      <div className="max-w-lg space-y-6">
        <Card>
          <div className="flex items-center gap-4 mb-5">
            <div className="w-16 h-16 rounded-2xl bg-surface-3 border border-border flex items-center justify-center text-3xl">{avatar}</div>
            <div>
              <div className="text-lg font-semibold text-white">{user?.username}</div>
              <div className={`text-sm ${ROLE_COLORS[user?.role]}`}>{ROLE_LABELS[user?.role]}</div>
            </div>
          </div>
          <div className="space-y-4">
            <div>
              <label className="block text-xs text-zinc-400 font-medium mb-1.5">显示名称</label>
              <input value={displayName} onChange={e => setDisplayName(e.target.value)}
                className="w-full bg-surface-3 border border-border rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-brand-500/40 transition" />
            </div>
            <div>
              <label className="block text-xs text-zinc-400 font-medium mb-2">选择头像</label>
              <div className="flex flex-wrap gap-2">
                {avatars.map(a => (
                  <button key={a} onClick={() => setAvatar(a)}
                    className={`w-10 h-10 rounded-xl flex items-center justify-center text-xl transition-all ${avatar === a ? 'bg-brand-600/20 border-brand-500/40 border-2 scale-110' : 'bg-surface-3 border border-border hover:bg-surface-4'}`}>
                    {a}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </Card>
        <Card>
          <h3 className="text-sm font-semibold text-white mb-4">修改密码</h3>
          <div className="space-y-3">
            <input type="password" value={newPwd} onChange={e => setNewPwd(e.target.value)} placeholder="新密码（留空则不修改）"
              className="w-full bg-surface-3 border border-border rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-brand-500/40 transition placeholder:text-zinc-600" />
            <input type="password" value={confirmPwd} onChange={e => setConfirmPwd(e.target.value)} placeholder="确认新密码"
              className="w-full bg-surface-3 border border-border rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-brand-500/40 transition placeholder:text-zinc-600" />
          </div>
        </Card>
        <button onClick={handleSave} disabled={saving}
          className="btn px-6 py-3 bg-brand-600 hover:bg-brand-500 text-white font-semibold rounded-xl transition shadow-lg shadow-brand-600/20 disabled:opacity-50">
          {saving ? <Spinner size={4} /> : '保存修改'}
        </button>
      </div>
    </div>
  );
}


// ── Admin View ───────────────────────────────────────

function AdminView({currentUser}) {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [newUser, setNewUser] = useState({username:'', password:'', role:'user', display_name:''});
  const [editingId, setEditingId] = useState(null);
  const [editData, setEditData] = useState({});
  const [avatars, setAvatars] = useState([]);
  const toast = useContext(ToastContext);

  const loadUsers = async () => {
    setLoading(true);
    const r = await apiGet('/api/admin/users');
    if (r.users) setUsers(r.users);
    setLoading(false);
  };

  useEffect(() => {
    loadUsers();
    apiGet('/api/admin/avatars').then(d => { if (d.avatars) setAvatars(d.avatars); });
  }, []);

  const handleCreate = async () => {
    if (!newUser.username || !newUser.password) { toast('用户名和密码必填', 'error'); return; }
    const r = await apiPost('/api/admin/users', newUser);
    if (r.ok) { toast(r.msg, 'success'); setShowCreate(false); setNewUser({username:'', password:'', role:'user', display_name:''}); loadUsers(); }
    else { toast(r.msg || '创建失败', 'error'); }
  };

  const handleUpdate = async (username) => {
    const r = await apiPut('/api/admin/users/' + username, editData);
    if (r.ok) { toast(r.msg, 'success'); setEditingId(null); loadUsers(); }
    else { toast(r.msg || '更新失败', 'error'); }
  };

  const handleDelete = async (username) => {
    if (!confirm('确定删除用户 ' + username + '？')) return;
    const r = await apiDelete('/api/admin/users/' + username);
    if (r.ok) { toast(r.msg, 'success'); loadUsers(); }
    else { toast(r.msg || '删除失败', 'error'); }
  };

  if (loading) return <div className="flex-1 flex items-center justify-center"><LoadingBlock text="加载用户列表..." /></div>;

  return (
    <div className="flex-1 overflow-y-auto p-6 animate-fade-in">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-white">用户管理</h1>
          <p className="text-sm text-zinc-500 mt-1">共 {users.length} 个用户</p>
        </div>
        <button onClick={() => setShowCreate(!showCreate)}
          className="btn px-4 py-2.5 bg-brand-600 hover:bg-brand-500 text-white text-sm font-medium rounded-xl transition shadow-lg shadow-brand-600/20">
          {showCreate ? '取消' : '+ 新建用户'}
        </button>
      </div>

      {showCreate && (
        <Card className="mb-6 animate-slide-up">
          <h3 className="text-sm font-semibold text-white mb-4">创建新用户</h3>
          <div className="grid grid-cols-2 gap-3">
            <input value={newUser.username} onChange={e => setNewUser({...newUser, username:e.target.value})} placeholder="用户名"
              className="bg-surface-3 border border-border rounded-xl px-3 py-2.5 text-sm text-white focus:outline-none focus:border-brand-500/40 transition placeholder:text-zinc-600" />
            <input type="password" value={newUser.password} onChange={e => setNewUser({...newUser, password:e.target.value})} placeholder="密码"
              className="bg-surface-3 border border-border rounded-xl px-3 py-2.5 text-sm text-white focus:outline-none focus:border-brand-500/40 transition placeholder:text-zinc-600" />
            <input value={newUser.display_name} onChange={e => setNewUser({...newUser, display_name:e.target.value})} placeholder="显示名称（可选）"
              className="bg-surface-3 border border-border rounded-xl px-3 py-2.5 text-sm text-white focus:outline-none focus:border-brand-500/40 transition placeholder:text-zinc-600" />
            <select value={newUser.role} onChange={e => setNewUser({...newUser, role:e.target.value})}
              className="bg-surface-3 border border-border rounded-xl px-3 py-2.5 text-sm text-white focus:outline-none focus:border-brand-500/40 transition cursor-pointer">
              <option value="user">用户</option>
              <option value="admin">管理员</option>
              <option value="viewer">访客</option>
            </select>
          </div>
          <button onClick={handleCreate} className="btn mt-4 px-5 py-2.5 bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium rounded-xl transition">创建</button>
        </Card>
      )}

      <div className="space-y-3">
        {users.map(u => (
          <Card key={u.username} className="!p-4">
            {editingId === u.username ? (
              <div className="space-y-3">
                <div className="flex items-center gap-3 mb-2">
                  <span className="text-2xl">{editData.avatar || u.avatar || '🦀'}</span>
                  <div className="flex flex-wrap gap-1">
                    {avatars.map(a => (
                      <button key={a} onClick={() => setEditData({...editData, avatar:a})}
                        className={`w-7 h-7 rounded-lg text-sm flex items-center justify-center ${(editData.avatar||u.avatar)===a ? 'bg-brand-600/20 border-brand-500' : 'bg-surface-3 border-border'} border transition`}>{a}</button>
                    ))}
                  </div>
                </div>
                <div className="grid grid-cols-3 gap-3">
                  <input value={editData.display_name ?? u.display_name} onChange={e => setEditData({...editData, display_name:e.target.value})} placeholder="显示名称"
                    className="bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-brand-500/40 transition" />
                  <select value={editData.role ?? u.role} onChange={e => setEditData({...editData, role:e.target.value})}
                    className="bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-brand-500/40 transition cursor-pointer">
                    <option value="admin">管理员</option><option value="user">用户</option><option value="viewer">访客</option>
                  </select>
                  <input type="password" value={editData.password || ''} onChange={e => setEditData({...editData, password:e.target.value})} placeholder="新密码（留空不改）"
                    className="bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-brand-500/40 transition placeholder:text-zinc-600" />
                </div>
                <div className="flex gap-2">
                  <button onClick={() => handleUpdate(u.username)} className="btn px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-medium rounded-lg transition">保存</button>
                  <button onClick={() => setEditingId(null)} className="btn px-4 py-2 bg-surface-3 hover:bg-surface-4 text-zinc-300 text-xs rounded-lg border border-border transition">取消</button>
                </div>
              </div>
            ) : (
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-xl bg-surface-3 border border-border flex items-center justify-center text-xl">{u.avatar || '🦀'}</div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-white">{u.display_name || u.username}</span>
                      <span className="text-[10px] text-zinc-500">@{u.username}</span>
                    </div>
                    <span className={`text-[11px] font-medium ${ROLE_COLORS[u.role]}`}>{ROLE_LABELS[u.role]}</span>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button onClick={() => { setEditingId(u.username); setEditData({}); }}
                    className="btn px-3 py-1.5 text-[11px] text-brand-400 hover:bg-brand-600/10 rounded-lg transition">编辑</button>
                  {u.username !== currentUser?.username && (
                    <button onClick={() => handleDelete(u.username)}
                      className="btn px-3 py-1.5 text-[11px] text-red-400 hover:bg-red-500/10 rounded-lg transition">删除</button>
                  )}
                </div>
              </div>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}
