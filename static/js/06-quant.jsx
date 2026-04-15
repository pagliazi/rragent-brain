// ── Quant Sub-Components ─────────────────────────────

function MetricCard({label, value, unit, color, small}) {
  const v = typeof value === 'number' ? value.toFixed(2) : value;
  const c = color || (typeof value === 'number' ? (value >= 0 ? 'text-emerald-400' : 'text-red-400') : 'text-zinc-200');
  return (
    <div className={`bg-surface-3 rounded-xl border border-border px-3 ${small ? 'py-2' : 'py-3'} text-center`}>
      <div className="text-[9px] text-zinc-600 uppercase tracking-wider mb-0.5">{label}</div>
      <div className={`${small ? 'text-[13px]' : 'text-[15px]'} font-bold ${c}`}>{v}{unit||''}</div>
    </div>
  );
}

function MetricsGrid({m}) {
  if (!m || typeof m !== 'object') return null;
  const highlights = [
    {k:'total_return', l:'总收益', u:'%', v:m.total_return ?? m.total_return_pct},
    {k:'annualized_return', l:'年化收益', u:'%', v:m.annualized_return ?? m.annualized_return_pct},
    {k:'sharpe', l:'夏普比率', v:m.sharpe ?? m.sharpe_ratio, color: (m.sharpe??m.sharpe_ratio) >= 1 ? 'text-emerald-400' : (m.sharpe??m.sharpe_ratio) >= 0 ? 'text-amber-400' : 'text-red-400'},
    {k:'max_drawdown', l:'最大回撤', u:'%', v:m.max_drawdown ?? m.max_drawdown_pct},
    {k:'win_rate', l:'胜率', u:'%', v:m.win_rate ?? m.win_rate_pct},
    {k:'trades', l:'交易次数', v:m.trades ?? m.total_trades, color:'text-zinc-200'},
  ];
  const extras = [
    {l:'Sortino', v:m.sortino_ratio}, {l:'Calmar', v:m.calmar_ratio},
    {l:'年化波动', v:m.annualized_volatility ?? m.annualized_volatility_pct, u:'%'},
    {l:'股票数', v:m.stocks_traded, color:'text-zinc-200'},
    {l:'交易天数', v:m.num_days, color:'text-zinc-200'},
    {l:'选股池', v:m.universe_size, color:'text-zinc-200'},
  ].filter(x => x.v != null);
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-2">
        {highlights.filter(h => h.v != null).map(h => <MetricCard key={h.k} label={h.l} value={h.v} unit={h.u} color={h.color} />)}
      </div>
      {extras.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-1.5">
          {extras.map((e,i) => <MetricCard key={i} label={e.l} value={e.v} unit={e.u} color={e.color} small />)}
        </div>
      )}
    </div>
  );
}

function TradeTable({trades, title, colorFn}) {
  if (!trades || trades.length === 0) return null;
  return (
    <div>
      <h4 className="text-[11px] font-medium text-zinc-400 mb-2">{title}</h4>
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full min-w-0 text-[10px] md:text-[11px]">
          <thead><tr className="bg-surface-3 text-zinc-500">
            <th className="px-2 py-1.5 text-left font-medium">股票</th>
            <th className="px-2 py-1.5 text-left font-medium">买入日</th>
            <th className="px-2 py-1.5 text-left font-medium">卖出日</th>
            <th className="px-2 py-1.5 text-right font-medium">买入价</th>
            <th className="px-2 py-1.5 text-right font-medium">卖出价</th>
            <th className="px-2 py-1.5 text-right font-medium">收益%</th>
            <th className="px-2 py-1.5 text-center font-medium">状态</th>
          </tr></thead>
          <tbody>{trades.map((t,i) => (
            <tr key={i} className="border-t border-border/50 hover:bg-surface-3/50 transition">
              <td className="px-2 py-1.5 font-mono text-zinc-300">{t.stock}</td>
              <td className="px-2 py-1.5 text-zinc-500">{t.entry_date}</td>
              <td className="px-2 py-1.5 text-zinc-500">{t.exit_date}</td>
              <td className="px-2 py-1.5 text-right text-zinc-400">{Number(t.entry_price).toFixed(2)}</td>
              <td className="px-2 py-1.5 text-right text-zinc-400">{t.exit_price > 0 ? Number(t.exit_price).toFixed(2) : '-'}</td>
              <td className={`px-2 py-1.5 text-right font-medium ${t.return_pct > 0 ? 'text-emerald-400' : t.return_pct < 0 ? 'text-red-400' : 'text-zinc-500'}`}>
                {t.return_pct > 0 ? '+' : ''}{Number(t.return_pct).toFixed(2)}%
              </td>
              <td className="px-2 py-1.5 text-center">
                <span className={`text-[9px] px-1.5 py-0.5 rounded ${t.status === 'Closed' ? 'bg-zinc-700/50 text-zinc-400' : 'bg-amber-500/10 text-amber-400'}`}>{t.status === 'Closed' ? '已平' : '持仓'}</span>
              </td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </div>
  );
}

function PnlDistribution({dist}) {
  if (!dist) return null;
  const total = dist.total_trades || 1;
  const posPct = ((dist.positive / total) * 100).toFixed(1);
  const negPct = ((dist.negative / total) * 100).toFixed(1);
  return (
    <Card>
      <h4 className="text-[12px] font-semibold text-zinc-300 mb-3">盈亏分布</h4>
      <div className="flex items-center gap-2 mb-3 h-3 rounded-full overflow-hidden bg-surface-3">
        <div className="h-full bg-emerald-500/70 rounded-l-full transition-all" style={{width: posPct + '%'}}></div>
        <div className="h-full bg-red-500/60 rounded-r-full transition-all" style={{width: negPct + '%'}}></div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px]">
        <div className="bg-surface-3 rounded-lg px-2.5 py-2"><span className="text-zinc-500">盈利</span> <span className="text-emerald-400 font-medium">{dist.positive}</span> <span className="text-zinc-600">({posPct}%)</span></div>
        <div className="bg-surface-3 rounded-lg px-2.5 py-2"><span className="text-zinc-500">亏损</span> <span className="text-red-400 font-medium">{dist.negative}</span> <span className="text-zinc-600">({negPct}%)</span></div>
        <div className="bg-surface-3 rounded-lg px-2.5 py-2"><span className="text-zinc-500">最佳</span> <span className="text-emerald-400 font-medium">+{Number(dist.best_return_pct).toFixed(1)}%</span></div>
        <div className="bg-surface-3 rounded-lg px-2.5 py-2"><span className="text-zinc-500">最差</span> <span className="text-red-400 font-medium">{Number(dist.worst_return_pct).toFixed(1)}%</span></div>
        <div className="bg-surface-3 rounded-lg px-2.5 py-2"><span className="text-zinc-500">均值</span> <span className={`font-medium ${dist.mean_return_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{Number(dist.mean_return_pct).toFixed(2)}%</span></div>
        <div className="bg-surface-3 rounded-lg px-2.5 py-2"><span className="text-zinc-500">中位数</span> <span className={`font-medium ${dist.median_return_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>{Number(dist.median_return_pct).toFixed(2)}%</span></div>
        <div className="bg-surface-3 rounded-lg px-2.5 py-2"><span className="text-zinc-500">标准差</span> <span className="text-zinc-300 font-medium">{Number(dist.std_return_pct).toFixed(2)}%</span></div>
        <div className="bg-surface-3 rounded-lg px-2.5 py-2"><span className="text-zinc-500">总交易</span> <span className="text-zinc-300 font-medium">{dist.total_trades}</span></div>
      </div>
    </Card>
  );
}

function DailySummaryChart({daily}) {
  if (!daily || daily.length === 0) return null;
  const maxVal = Math.max(...daily.map(d => Math.max(d.entries, d.exits)), 1);
  const [hovIdx, setHovIdx] = useState(-1);
  return (
    <Card>
      <h4 className="text-[12px] font-semibold text-zinc-300 mb-3">每日交易活动 <span className="text-zinc-600 font-normal">({daily.length} 交易日)</span></h4>
      <div className="relative h-32 flex items-end gap-[1px] overflow-x-auto">
        {daily.map((d, i) => {
          const eH = (d.entries / maxVal) * 100;
          const xH = (d.exits / maxVal) * 100;
          return (
            <div key={i} className="flex-shrink-0 flex flex-col items-center gap-[1px] relative group cursor-pointer"
              style={{width: Math.max(100 / daily.length, 3) + '%'}}
              onMouseEnter={() => setHovIdx(i)} onMouseLeave={() => setHovIdx(-1)}>
              <div className="w-full flex gap-[1px]" style={{height: '100%', alignItems: 'flex-end'}}>
                <div className="flex-1 bg-emerald-500/60 rounded-t-sm transition-all hover:bg-emerald-500/80" style={{height: eH + '%', minHeight: d.entries > 0 ? '2px' : 0}}></div>
                <div className="flex-1 bg-red-500/50 rounded-t-sm transition-all hover:bg-red-500/70" style={{height: xH + '%', minHeight: d.exits > 0 ? '2px' : 0}}></div>
              </div>
              {hovIdx === i && (
                <div className="absolute bottom-full mb-1 left-1/2 -translate-x-1/2 glass rounded-lg px-2 py-1.5 text-[9px] whitespace-nowrap z-10 border border-border shadow-lg">
                  <div className="text-zinc-300 font-medium">{d.date}</div>
                  <div className="text-emerald-400">买入 {d.entries}</div>
                  <div className="text-red-400">卖出 {d.exits}</div>
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="flex items-center gap-4 mt-2 text-[10px] text-zinc-500">
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-emerald-500/60"></span>买入</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-red-500/50"></span>卖出</span>
      </div>
    </Card>
  );
}

function TopStocksTable({stocks}) {
  if (!stocks || stocks.length === 0) return null;
  return (
    <Card>
      <h4 className="text-[12px] font-semibold text-zinc-300 mb-2">高频交易股票 TOP {stocks.length}</h4>
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full min-w-0 text-[10px] md:text-[11px]">
          <thead><tr className="bg-surface-3 text-zinc-500">
            <th className="px-2.5 py-1.5 text-left font-medium">股票代码</th>
            <th className="px-2.5 py-1.5 text-right font-medium">交易次数</th>
            <th className="px-2.5 py-1.5 text-right font-medium">平均收益%</th>
          </tr></thead>
          <tbody>{stocks.map((s,i) => (
            <tr key={i} className="border-t border-border/50 hover:bg-surface-3/50">
              <td className="px-2.5 py-1.5 font-mono text-zinc-300">{s.stock}</td>
              <td className="px-2.5 py-1.5 text-right text-zinc-400">{s.trades}</td>
              <td className={`px-2.5 py-1.5 text-right font-medium ${s.avg_return_pct >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {s.avg_return_pct >= 0 ? '+' : ''}{Number(s.avg_return_pct).toFixed(2)}%
              </td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </Card>
  );
}

function RecordFullDetail({rec, toast, onOptimize}) {
  const [detailTab, setDetailTab] = useState('overview');
  if (!rec) return null;
  const m = rec.metrics || {};
  const tl = m.trade_log || {};
  const tabs = [
    {id:'overview', label:'概览'},
    {id:'trades', label:`交易记录`, count: (tl.sample_winners?.length||0)+(tl.sample_losers?.length||0)},
    {id:'daily', label:'每日活动', count: tl.daily_summary?.length},
    {id:'stocks', label:'高频股票', count: tl.top_stocks?.length},
    {id:'code', label:'源码', show: !!rec.code},
    {id:'log', label:'过程日志', count: rec.process_log?.length},
  ].filter(t => t.show !== false && (t.count == null || t.count > 0));
  return (
    <div className="space-y-4 animate-fade-in">
      <Card className={rec.status === 'APPROVE' ? 'border-emerald-500/20' : 'border-red-500/15'}>
        <div className="flex items-start justify-between mb-4">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xl">{rec.status === 'APPROVE' ? '✅' : '❌'}</span>
              <h3 className="text-base font-bold text-white">{rec.title}</h3>
            </div>
            <div className="text-[11px] text-zinc-500">{rec.topic} · {rec.created_at} · {rec.attempts}轮迭代 · {rec.mode || 'technical'}</div>
          </div>
          <span className={`text-[10px] px-2 py-1 rounded-lg font-medium ${rec.status === 'APPROVE' ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-red-500/10 text-red-400 border border-red-500/20'}`}>
            {rec.status === 'APPROVE' ? '通过' : '废弃'}
          </span>
        </div>
        {rec.pm_summary && <div className="text-[12px] text-zinc-300 leading-relaxed whitespace-pre-wrap mb-4 bg-surface-3/50 rounded-xl p-3 border border-border/50">{rec.pm_summary}</div>}
        <MetricsGrid m={m} />
      </Card>

      <div className="flex gap-1 bg-surface-1 rounded-xl p-1 border border-border">
        {tabs.map(t => (
          <button key={t.id} onClick={() => setDetailTab(t.id)}
            className={`btn flex-1 px-2 py-1.5 rounded-lg text-[12px] font-medium transition flex items-center justify-center gap-1
              ${detailTab === t.id ? 'bg-brand-600/15 text-brand-400' : 'text-zinc-500 hover:text-zinc-300'}`}>
            {t.label}{t.count != null && <span className="text-[9px] bg-surface-3 px-1 py-0.5 rounded-full">{t.count}</span>}
          </button>
        ))}
      </div>

      {detailTab === 'overview' && (<>
        {tl.pnl_distribution && <PnlDistribution dist={tl.pnl_distribution} />}
        {tl.daily_summary && tl.daily_summary.length > 0 && <DailySummaryChart daily={tl.daily_summary} />}
        {rec.optimization_hint && <Card><h4 className="text-[12px] font-medium text-zinc-400 mb-2">优化建议</h4><pre className="text-[12px] text-zinc-300 leading-relaxed whitespace-pre-wrap">{rec.optimization_hint}</pre></Card>}
        <Card>
          <h4 className="text-[12px] font-medium text-zinc-300 mb-3">在此基础上迭代</h4>
          <div className="flex gap-2">
            <input id="opt_input" placeholder="输入优化方向..." className="flex-1 bg-surface-3 border border-border rounded-xl px-3 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 transition" />
            <button onClick={() => { const v = document.getElementById('opt_input').value; if (v.trim()) onOptimize(v.trim(), rec); }}
              className="btn px-5 py-2.5 bg-emerald-600 hover:bg-emerald-500 rounded-xl text-sm text-white font-medium transition shadow-lg shadow-emerald-600/20">迭代优化</button>
          </div>
        </Card>
      </>)}

      {detailTab === 'trades' && (<>
        <TradeTable trades={tl.sample_winners} title={`盈利交易 TOP ${tl.sample_winners?.length || 0}`} />
        <TradeTable trades={tl.sample_losers} title={`亏损交易 TOP ${tl.sample_losers?.length || 0}`} />
      </>)}

      {detailTab === 'daily' && <DailySummaryChart daily={tl.daily_summary} />}
      {detailTab === 'stocks' && <TopStocksTable stocks={tl.top_stocks} />}

      {detailTab === 'code' && rec.code && (
        <Card>
          <div className="flex items-center justify-between mb-2">
            <h4 className="text-[12px] font-medium text-zinc-400">策略源码</h4>
            <button onClick={() => { navigator.clipboard.writeText(rec.code); toast('已复制', 'success'); }}
              className="btn text-[11px] text-brand-400 hover:text-brand-300 px-2 py-1 rounded-lg transition">复制代码</button>
          </div>
          <pre className="text-[11px] text-zinc-400 leading-[1.6] font-mono max-h-[500px] overflow-auto bg-surface-0/50 rounded-xl p-3 border border-border/50">{rec.code}</pre>
        </Card>
      )}

      {detailTab === 'log' && rec.process_log && (
        <Card>
          <h4 className="text-[12px] font-medium text-zinc-400 mb-3">研发过程日志</h4>
          <div className="space-y-2 max-h-[500px] overflow-auto">
            {rec.process_log.map((log, i) => (
              <div key={i} className="text-[11px] text-zinc-400 leading-relaxed border-l-2 border-brand-500/20 pl-3 py-1 whitespace-pre-wrap hover:bg-surface-3/30 rounded-r-lg transition">
                <span className="text-zinc-600 mr-2">#{i+1}</span>{log}
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}


// ── QuantView (Rewritten) ────────────────────────────

function QuantView() {
  const [tab, setTab] = useState('records');
  const [topic, setTopic] = useState('');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState(null);
  const [steps, setSteps] = useState([]);
  const [ledger, setLedger] = useState('');
  const [ledgerLoading, setLedgerLoading] = useState(false);
  const [cache, setCache] = useState('');
  const [strategies, setStrategies] = useState([]);
  const [strategiesLoading, setStrategiesLoading] = useState(false);
  const [selectedId, setSelectedId] = useState(null);
  const [selectedDetail, setSelectedDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [optimizing, setOptimizing] = useState(false);
  const [optimizeTopic, setOptimizeTopic] = useState('');
  const [records, setRecords] = useState([]);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [diggerRunning, setDiggerRunning] = useState(false);
  const [diggerLogs, setDiggerLogs] = useState(() => {
    try { const s = localStorage.getItem('rragent_digger_logs'); return s ? JSON.parse(s) : []; } catch(e) { return []; }
  });
  const [diggerStatus, setDiggerStatus] = useState(null);
  const [diggerStatusLoading, setDiggerStatusLoading] = useState(false);
  const [diggerRounds, setDiggerRounds] = useState(5);
  const [diggerFactors, setDiggerFactors] = useState(5);
  const [factorList, setFactorList] = useState([]);
  const [factorStats, setFactorStats] = useState(null);
  const [factorListLoading, setFactorListLoading] = useState(false);
  const [selectedFactor, setSelectedFactor] = useState(null);
  const [combineLoading, setCombineLoading] = useState(false);
  const [diggerSubTab, setDiggerSubTab] = useState('library');
  const [analysisData, setAnalysisData] = useState(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [stratResult, setStratResult] = useState(null);
  const [stratLoading, setStratLoading] = useState(false);
  const [exhaustiveResults, setExhaustiveResults] = useState([]);
  const [exhaustiveRunning, setExhaustiveRunning] = useState(false);
  const [exhaustiveProgress, setExhaustiveProgress] = useState(null);
  const [exhaustiveGroupSize, setExhaustiveGroupSize] = useState(2);
  const [exhaustiveMaxCombos, setExhaustiveMaxCombos] = useState(50);
  const [pipelineStatus, setPipelineStatus] = useState(null);
  const [pipelineLoading, setPipelineLoading] = useState(false);
  const toast = useContext(ToastContext);

  // 持久化挖掘日志到 localStorage，最多保留500条
  useEffect(() => {
    try {
      const trimmed = diggerLogs.slice(-500);
      localStorage.setItem('rragent_digger_logs', JSON.stringify(trimmed));
    } catch(e) {}
  }, [diggerLogs]);

  const presets = [
    {label:'涨停首板溢价',topic:'涨停板放量封板后，次日竞价溢价买入策略，利用A股涨停制度的溢价效应'},
    {label:'聪明钱超跌反弹',topic:'结合资金流向和超跌幅度，在主力资金流入+超跌15%时抄底反弹'},
    {label:'波动收缩突破(VCP)',topic:'振幅逐渐收窄后的放量突破策略，识别有效突破vs假突破'},
    {label:'板块轮动低吸',topic:'利用板块轮动规律，龙头退潮后补涨股低吸策略'},
    {label:'缩量上涨筹码锁定',topic:'缩量稳步上涨=筹码高度集中，放量加速时买入'},
    {label:'竞价博弈策略',topic:'利用开盘竞价跳空+日内走势组合模式，低开反包/高开确认'},
  ];

  const stepIcons = {'1/5':'🔬','2/5':'💻','3/5':'⚙️','4/5':'🛡️','5/5':'📋'};
  const stepNames = {'1/5':'Alpha Researcher','2/5':'Quant Coder','3/5':'Backtest Engine','4/5':'Risk Analyst','5/5':'Portfolio Manager'};

  const runQuantStream = async (t, cmd, optimizePayload) => {
    const target = t || topic.trim();
    if (!target && cmd !== 'quant_optimize') return;
    setRunning(true); setResult(null); setSteps([]);
    setTab('research');
    toast('量化流水线已启动...', 'info');
    const body = cmd === 'quant_optimize' ? {cmd:'quant_optimize', topic:target, optimize_payload: optimizePayload} : {cmd:'quant', topic:target};
    try {
      const resp = await fetch('/api/quant/stream', {method:'POST', headers: authHeaders(), body:JSON.stringify(body)});
      if (resp.status === 401) { window.__onAuthExpired?.(); return; }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while(true) {
        const {done: rdone, value} = await reader.read();
        if (rdone) break;
        buffer += decoder.decode(value, {stream:true});
        const lines = buffer.split('\n'); buffer = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const evt = JSON.parse(line.slice(6));
            if (evt.type === 'heartbeat' || evt.type === 'close') continue;
            if (evt.type === 'done') { setResult(evt.result || {summary: evt.content}); toast('量化研发完成', 'success'); }
            else if (evt.type === 'final') { setResult(prev => prev || {summary: evt.text}); }
            else if (evt.type === 'error') { setSteps(prev => [...prev, {step:'-', title:'错误', content:evt.content, status:'error', ts:Date.now()/1000}]); toast(evt.content, 'error'); }
            else { setSteps(prev => { const existing = prev.findIndex(s => s.step === evt.step && s.title === evt.title && s.status === 'running'); if (existing >= 0 && evt.status !== 'running') { const updated = [...prev]; updated[existing] = evt; return updated; } return [...prev, evt]; }); }
          } catch(e) {}
        }
      }
    } catch(e) { toast('流式连接失败: ' + e.message, 'error'); }
    setRunning(false); loadRecords();
  };

  const runQuant = (t) => runQuantStream(t, 'quant');

  const handleOptimizeFromRecord = (optTopic, rec) => {
    const payload = {topic: optTopic, base_title: rec.title || '', base_code: rec.code || '', base_metrics: rec.metrics || {}};
    runQuantStream(optTopic, 'quant_optimize', payload);
  };

  const loadRecords = async () => { setRecordsLoading(true); try { const r = await apiGet('/api/quant/records'); setRecords(r.records || []); } catch(e) { setRecords([]); } setRecordsLoading(false); };
  const loadRecordDetail = async (id) => { setSelectedId(id); setDetailLoading(true); setSelectedDetail(null); try { const r = await apiGet('/api/quant/records/' + id); if (r && r.id) { setSelectedDetail(r); } } catch(e) { toast('加载失败', 'error'); } setDetailLoading(false); };
  const loadLedger = async () => { setLedgerLoading(true); const r = await apiPost('/api/command', {cmd:'ledger'}); setLedger(r.result!=null ? String(r.result) : ''); setLedgerLoading(false); };
  const loadCache = async () => { const r = await apiPost('/api/command', {cmd:'bt_cache'}); setCache(r.result!=null ? String(r.result) : ''); };
  const loadStrategies = async () => { setStrategiesLoading(true); try { const r = await apiGet('/api/strategies'); if (r.strategies) setStrategies(r.strategies); else setStrategies([]); } catch(e) { setStrategies([]); } setStrategiesLoading(false); };
  const loadStrategyDetail = async (id) => { setSelectedId(id); setDetailLoading(true); setSelectedDetail(null); try { const r = await apiGet('/api/strategies/' + id); if (r && !r.error) { setSelectedDetail({...r, _type:'strategy'}); setOptimizeTopic('优化 ' + (r.title || '')); } else { toast('加载失败', 'error'); } } catch(e) {} setDetailLoading(false); };

  const loadDiggerStatus = async () => {
    setDiggerStatusLoading(true);
    try {
      const r = await apiGet('/api/digger/status');
      if (r && r.text) setDiggerStatus(r.text);
    } catch(e) {}
    setDiggerStatusLoading(false);
  };
  const loadFactorList = async () => {
    setFactorListLoading(true);
    try {
      const r = await apiGet('/api/digger/factors');
      if (r) { setFactorList(r.factors || []); setFactorStats(r.stats || null); }
    } catch(e) { toast('加载因子列表失败','error'); }
    setFactorListLoading(false);
  };
  const retireFactor = async (fid) => {
    if (!confirm('确定退休此因子？')) return;
    try {
      await apiPost('/api/digger/retire', {factor_id: fid});
      toast('因子已退休','success');
      loadFactorList();
      setSelectedFactor(null);
    } catch(e) { toast('操作失败','error'); }
  };
  const [selectedForCombine, setSelectedForCombine] = useState([]);
  const [toStrategyLoading, setToStrategyLoading] = useState(false);
  const [screenResult, setScreenResult] = useState(null);
  const [screenLoading, setScreenLoading] = useState(false);
  const [factorSearch, setFactorSearch] = useState('');
  const [factorSort, setFactorSort] = useState('sharpe');
  const [factorFilter, setFactorFilter] = useState('all'); // all, vectorized, active, retired
  const [factorDetailTab, setFactorDetailTab] = useState('metrics');

  const toggleCombineSelect = (fid) => {
    setSelectedForCombine(prev => prev.includes(fid) ? prev.filter(id=>id!==fid) : [...prev, fid]);
  };

  const _ASTOCK_THEMES = new Set(['limit_up_chain','money_flow','sector_rotation','emotion_cycle','auction_microstructure','chip_concentration','breakout_quality','reversal_signal','smart_money_divergence','volatility_contraction_breakout']);
  const getFilteredFactors = () => {
    let list = [...factorList];
    if (factorFilter === 'vectorized') list = list.filter(f => f.complexity === 'vectorized');
    else if (factorFilter === 'active') list = list.filter(f => f.status === 'active');
    else if (factorFilter === 'retired') list = list.filter(f => f.status !== 'active');
    else if (factorFilter === 'combinable') list = list.filter(f => f.combinable && f.status === 'active');
    else if (factorFilter === 'astock') list = list.filter(f => _ASTOCK_THEMES.has(f.theme) || f.theme === 'mutation');
    else if (factorFilter === 'technical') list = list.filter(f => !_ASTOCK_THEMES.has(f.theme) && f.theme !== 'mutation');
    if (factorSearch.trim()) {
      const q = factorSearch.toLowerCase();
      list = list.filter(f => (f.sub_theme||f.theme||'').toLowerCase().includes(q) || (f.id||'').includes(q) || (f.code||'').toLowerCase().includes(q));
    }
    const sortKey = factorSort;
    if (sortKey === 'sharpe') list.sort((a,b) => (b.sharpe||0) - (a.sharpe||0));
    else if (sortKey === 'ir') list.sort((a,b) => Math.abs(b.ir||0) - Math.abs(a.ir||0));
    else if (sortKey === 'ic') list.sort((a,b) => Math.abs(b.ic_mean||0) - Math.abs(a.ic_mean||0));
    else if (sortKey === 'trades') list.sort((a,b) => (b.trades||0) - (a.trades||0));
    else if (sortKey === 'newest') list.sort((a,b) => (b.created_at||0) - (a.created_at||0));
    return list;
  };
  const [combineHistory, setCombineHistory] = useState([]);
  const [combineHistoryLoading, setCombineHistoryLoading] = useState(false);
  const [selectedCombineRecord, setSelectedCombineRecord] = useState(null);
  const [combineDetailLoading, setCombineDetailLoading] = useState(false);

  const loadCombineHistory = async () => {
    setCombineHistoryLoading(true);
    try {
      const r = await apiGet('/api/digger/combine/history?limit=20');
      if (r?.records) setCombineHistory(r.records);
    } catch(e) { toast('加载融合记录失败','error'); }
    setCombineHistoryLoading(false);
  };
  const loadCombineDetail = async (rid) => {
    setCombineDetailLoading(true);
    try {
      const r = await apiGet('/api/digger/combine/' + rid);
      if (r && r.id) setSelectedCombineRecord(r);
    } catch(e) { toast('加载详情失败','error'); }
    setCombineDetailLoading(false);
  };

  const triggerCombine = async () => {
    const ids = selectedForCombine.length >= 2 ? selectedForCombine : [];
    if (ids.length === 0 && factorList.filter(f=>f.status==='active').length < 2) {
      toast('至少需要 2 个活跃因子才能融合', 'error'); return;
    }
    setCombineLoading(true);
    try {
      const r = await apiPost('/api/digger/combine', {factor_ids: ids});
      if (r && r.ok) {
        const v = r.verdict;
        const msg = v === 'accept' ? `融合成功采纳 (${r.factors_used} 因子, Sharpe ${r.combined_metrics?.sharpe?.toFixed(3)||'?'})` :
                    v === 'marginal' ? `融合结果边缘 — 请在融合记录中查看详情` :
                    `融合已回退 — Sharpe 未超过最佳单因子`;
        toast(msg, v === 'accept' ? 'success' : v === 'reject' ? 'error' : 'warning');
        setSelectedForCombine([]);
        loadCombineHistory();
      } else {
        toast(r?.error || '融合失败', 'error');
      }
    } catch(e) { toast('融合请求失败','error'); }
    setCombineLoading(false);
  };
  const factorToStrategy = async (fid) => {
    setToStrategyLoading(true);
    toast('正在回测生成策略，请稍候...', 'info');
    try {
      const r = await apiPost('/api/digger/to-strategy', {factor_id: fid});
      if (r && !r.error && r.ok) {
        const sharpe = r.strategy_metrics?.sharpe_ratio || r.strategy_metrics?.sharpe || 0;
        toast(`策略化成功 Sharpe=${Number(sharpe).toFixed(2)} → 已存入策略库`, 'success');
        loadStrategies();
      } else {
        toast(typeof r?.error === 'string' ? r.error : (r?.result || '策略化失败'), 'error');
      }
    } catch(e) { toast('请求失败: ' + e.message,'error'); }
    setToStrategyLoading(false);
  };

  const runExhaustiveCombine = async () => {
    setExhaustiveRunning(true); setExhaustiveResults([]); setExhaustiveProgress(null);
    try {
      const resp = await fetch('/api/digger/combine/exhaustive', {
        method: 'POST', headers: {...authHeaders(), 'Content-Type': 'application/json'},
        body: JSON.stringify({group_size: exhaustiveGroupSize, max_combos: exhaustiveMaxCombos, skip_tested: true}),
      });
      if (resp.status === 401) { window.__onAuthExpired?.(); setExhaustiveRunning(false); return; }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buf += decoder.decode(value, {stream: true});
        const lines = buf.split('\n');
        buf = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const evt = JSON.parse(line.slice(6));
            if (evt.type === 'start') setExhaustiveProgress({total: evt.total, current: 0, candidates: evt.candidates});
            else if (evt.type === 'progress') setExhaustiveProgress(p => ({...p, current: evt.idx, names: evt.names}));
            else if (evt.type === 'result') setExhaustiveResults(prev => [...prev, evt]);
            else if (evt.type === 'done') { setExhaustiveProgress(p => ({...p, done: true, accepted: evt.accepted, best: evt.best})); toast(`穷举完成: ${evt.tested} 组合, ${evt.accepted} 通过`, 'success'); }
          } catch(e) {}
        }
      }
    } catch(e) { toast('穷举融合失败: ' + e.message, 'error'); }
    setExhaustiveRunning(false);
  };

  const loadPipelineStatus = async () => {
    setPipelineLoading(true);
    try {
      const r = await apiGet('/api/n8n/pipeline/status');
      if (r) setPipelineStatus(r);
    } catch(e) { toast('加载管线状态失败','error'); }
    setPipelineLoading(false);
  };

  const triggerN8nMine = async (rounds=3) => {
    try {
      const r = await apiPost('/api/n8n/trigger/mine', {rounds, factors_per_round: 5});
      toast(r?.message || '挖掘已触发', 'success');
    } catch(e) { toast('触发失败','error'); }
  };

  const triggerN8nCombineAll = async () => {
    try {
      const r = await apiPost('/api/n8n/trigger/combine-all', {group_size: 2, max_combos: 50});
      toast(r?.message || '融合已触发', 'success');
    } catch(e) { toast('触发失败','error'); }
  };

  const tierColors = {t1_extreme_overfit:'text-red-400',t2_suspect_overfit:'text-amber-400',t3_normal:'text-emerald-400',t4_other:'text-zinc-400'};
  const tierNames = {t1_extreme_overfit:'🔴 极度过拟合',t2_suspect_overfit:'🟠 疑似过拟合',t3_normal:'🟢 正常因子',t4_other:'🟡 其它'};

  const runAnalysis = async () => {
    setAnalysisLoading(true);
    try { const r = await apiPost('/api/digger/analyze', {}); if (r) setAnalysisData(r); }
    catch(e) { toast('分析失败','error'); }
    setAnalysisLoading(false);
  };

  const strategize = async (factorId) => {
    setStratLoading(true); setStratResult(null);
    toast('正在回测生成策略，请稍候...', 'info');
    try {
      const r = await apiPost('/api/digger/to-strategy', {factor_id: factorId});
      if (r && !r.error) {
        setStratResult(r);
        if (r.ok) toast(`策略化成功 Sharpe=${(r.strategy_metrics?.sharpe_ratio||0).toFixed(2)}`,'success');
        else toast(r.error || '策略化失败','error');
      } else {
        toast(typeof r?.error === 'string' ? r.error : (r?.result || '策略化失败'), 'error');
      }
    } catch(e) { toast('策略化失败: ' + e.message,'error'); }
    setStratLoading(false);
  };

  const startDigger = async () => {
    setDiggerRunning(true);
    setDiggerLogs([{ts: Date.now(), text: '⛏️ 因子挖掘启动中...', type: 'info'}]);
    try {
      const resp = await fetch('/api/digger/start', {method:'POST', headers: authHeaders(), body: JSON.stringify({rounds: diggerRounds, factors: diggerFactors, interval: 30})});
      if (resp.status === 401) { window.__onAuthExpired?.(); setDiggerRunning(false); return; }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const {done: rdone, value} = await reader.read();
        if (rdone) break;
        buffer += decoder.decode(value, {stream:true});
        const lines = buffer.split('\n'); buffer = lines.pop();
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const evt = JSON.parse(line.slice(6));
            if (evt.type === 'heartbeat' || evt.type === 'close') continue;
            if (evt.type === 'progress' || evt.type === 'done') {
              setDiggerLogs(prev => [...prev, {ts: Date.now(), text: evt.text, type: evt.type === 'done' ? 'success' : 'info'}]);
              if (evt.type === 'done') { toast('因子挖掘完成', 'success'); break; }
            } else if (evt.type === 'error') {
              setDiggerLogs(prev => [...prev, {ts: Date.now(), text: evt.text, type: 'error'}]);
              toast(evt.text, 'error');
            } else if (evt.type === 'started') {
              setDiggerLogs(prev => [...prev, {ts: Date.now(), text: `启动: ${evt.rounds}轮 × ${evt.factors}因子/轮`, type: 'info'}]);
            }
          } catch(e) {}
        }
      }
    } catch(e) { toast('连接失败: ' + e.message, 'error'); setDiggerLogs(prev => [...prev, {ts: Date.now(), text: '连接失败: ' + e.message, type: 'error'}]); }
    setDiggerRunning(false);
    loadDiggerStatus();
  };

  useEffect(()=>{loadRecords();loadStrategies();},[]);

  const statusIcon = (s) => ({APPROVE:'✅', REJECT:'❌', PENDING:'🟡'}[s] || '⚪');
  const categoryIcon = (c) => ({breakout:'🚀',momentum:'📈',value:'💎',pattern:'📐',sentiment:'🧠',trend:'📊',observation:'👀',four_dim:'🎯'}[c] || '📋');
  const categoryLabel = (c) => ({breakout:'突破',momentum:'动量',value:'价值',pattern:'形态',sentiment:'情绪',trend:'趋势',observation:'观察',four_dim:'四维共振'}[c] || c);
  const themeColor = (t) => ({
    limit_up_chain:'text-red-400', money_flow:'text-cyan-400', sector_rotation:'text-amber-400',
    emotion_cycle:'text-pink-400', auction_microstructure:'text-orange-400', chip_concentration:'text-teal-400',
    breakout_quality:'text-emerald-400', reversal_signal:'text-blue-400', smart_money_divergence:'text-violet-400',
    volatility_contraction_breakout:'text-indigo-400', momentum:'text-zinc-400', mean_reversion:'text-zinc-400',
    intraday_pattern:'text-zinc-400', gap_factor:'text-zinc-400', cross_sectional_rank:'text-zinc-400',
    volatility_regime:'text-zinc-500', range_compression:'text-zinc-500', volume_distribution:'text-zinc-500',
    higher_moment:'text-zinc-500', price_volume_sync:'text-zinc-500', liquidity_shock:'text-zinc-500',
    volume_price_div:'text-zinc-500', mutation:'text-purple-400',
  }[t] || 'text-zinc-500');
  const themeBg = (t) => ({
    limit_up_chain:'bg-red-500/10', money_flow:'bg-cyan-500/10', sector_rotation:'bg-amber-500/10',
    emotion_cycle:'bg-pink-500/10', auction_microstructure:'bg-orange-500/10', chip_concentration:'bg-teal-500/10',
    breakout_quality:'bg-emerald-500/10', reversal_signal:'bg-blue-500/10', smart_money_divergence:'bg-violet-500/10',
    volatility_contraction_breakout:'bg-indigo-500/10', mutation:'bg-purple-500/10',
  }[t] || 'bg-surface-3');
  const themeLabel = (t) => ({
    limit_up_chain:'涨停连板', money_flow:'资金流向', sector_rotation:'板块轮动',
    emotion_cycle:'情绪周期', auction_microstructure:'竞价博弈', chip_concentration:'筹码结构',
    breakout_quality:'突破质量', reversal_signal:'反转信号', smart_money_divergence:'聪明钱',
    volatility_contraction_breakout:'VCP突破', momentum:'动量', mean_reversion:'均值回归',
    intraday_pattern:'日内形态', gap_factor:'跳空缺口', cross_sectional_rank:'截面强弱',
    volatility_regime:'波动率', range_compression:'振幅压缩', volume_distribution:'量能分布',
    higher_moment:'高阶矩', price_volume_sync:'量价同步', liquidity_shock:'流动性',
    volume_price_div:'量价背离', mutation:'变异因子',
  }[t] || t);

  const approvedCount = records.filter(r => r.status === 'APPROVE').length;
  const tabItems = [
    {id:'records', label:'挖掘记录', icon:'📊', badge: records.length},
    {id:'research', label:'启动研发', icon:'🔬'},
    {id:'digger', label:'因子挖掘', icon:'⛏️'},
    {id:'library', label:'策略库', icon:'📚', badge: strategies.length},
    {id:'meme', label:'妖股挖掘', icon:'🐉'},
    {id:'ledger', label:'账本', icon:'📝'},
  ];

  return (
    <div className="flex-1 overflow-y-auto overflow-x-hidden p-4 md:p-6 space-y-4 animate-fade-in">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">A股量化策略研发</h1>
          <p className="text-sm text-zinc-500 mt-0.5">
            涨停连板 · 资金流向 · 板块轮动 · 情绪周期 · 竞价博弈 ·
            <span className="text-emerald-400 ml-1">{approvedCount} 通过</span> /
            <span className="text-zinc-400 ml-1">{records.length} 记录</span> /
            <span className="text-brand-400 ml-1">{strategies.length} 策略</span>
          </p>
        </div>
        <button onClick={loadRecords} className="btn px-3 py-1.5 bg-surface-2 hover:bg-surface-3 rounded-lg text-[12px] text-zinc-400 border border-border hover:border-border-light transition">
          {recordsLoading ? <Spinner /> : '刷新'}
        </button>
      </div>

      <div className="flex gap-1 bg-surface-1 rounded-xl p-1 border border-border">
        {tabItems.map(t => (
          <button key={t.id} onClick={()=>setTab(t.id)}
            className={`btn flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-[13px] font-medium transition
              ${tab===t.id ? 'bg-brand-600/15 text-brand-400' : 'text-zinc-500 hover:text-zinc-300'}`}>
            <span>{t.icon}</span><span>{t.label}</span>
            {t.badge > 0 && <span className="text-[10px] bg-surface-3 px-1.5 py-0.5 rounded-full text-zinc-500">{t.badge}</span>}
          </button>
        ))}
      </div>

      {tab === 'records' && (
        <div className="grid lg:grid-cols-[320px_1fr] gap-4" style={{minHeight:'600px'}}>
          <div className="space-y-2 overflow-y-auto overflow-x-hidden max-h-[calc(100vh-220px)] pr-1">
            {recordsLoading && <LoadingBlock text="加载记录..." />}
            {!recordsLoading && records.length === 0 && <Card><div className="text-center py-8 text-zinc-600 text-sm"><div className="text-3xl mb-2">📊</div>暂无挖掘记录<br/><span className="text-zinc-700">去「启动研发」开始第一次量化挖掘</span></div></Card>}
            {records.map(r => (
              <div key={r.id} onClick={() => loadRecordDetail(r.id)}
                className={`rounded-xl border px-3.5 py-3 cursor-pointer transition-all group
                  ${selectedId === r.id ? 'border-brand-500/30 bg-brand-600/5' : 'border-border bg-surface-2 hover:border-border-light hover:bg-surface-3'}`}>
                <div className="flex items-start gap-2.5">
                  <span className="text-base mt-0.5">{statusIcon(r.status)}</span>
                  <div className="flex-1 min-w-0">
                    <div className="text-[13px] font-medium text-white group-hover:text-brand-400 transition truncate">{r.title}</div>
                    <div className="text-[10px] text-zinc-600 mt-0.5">{r.created_at} · {r.attempts}轮</div>
                    {r.metrics && (
                      <div className="flex gap-2 mt-1.5 flex-wrap text-[10px]">
                        {r.metrics.total_return != null && <span className={`px-1.5 py-0.5 rounded ${Number(r.metrics.total_return) >= 0 ? 'bg-emerald-500/10 text-emerald-400' : 'bg-red-500/10 text-red-400'}`}>收益 {Number(r.metrics.total_return).toFixed(1)}%</span>}
                        {r.metrics.sharpe != null && <span className={`px-1.5 py-0.5 rounded ${Number(r.metrics.sharpe) >= 0 ? 'bg-surface-3 text-zinc-400' : 'bg-red-500/10 text-red-400'}`}>夏普 {Number(r.metrics.sharpe).toFixed(2)}</span>}
                        {r.metrics.win_rate != null && <span className="px-1.5 py-0.5 rounded bg-surface-3 text-zinc-400">胜率 {Number(r.metrics.win_rate).toFixed(0)}%</span>}
                        {r.metrics.max_drawdown != null && <span className="px-1.5 py-0.5 rounded bg-surface-3 text-zinc-500">回撤 {Number(r.metrics.max_drawdown).toFixed(1)}%</span>}
                      </div>
                    )}
                  </div>
                  {r.has_code && <span className="text-[8px] px-1 py-0.5 bg-emerald-500/10 text-emerald-500 rounded border border-emerald-500/20">码</span>}
                </div>
              </div>
            ))}
          </div>
          <div className="overflow-y-auto overflow-x-hidden max-h-[calc(100vh-220px)] pr-1">
            {!selectedId && !detailLoading && <Card><div className="text-center py-16 text-zinc-600"><div className="text-4xl mb-3">👈</div><div className="text-sm">选择左侧记录查看完整挖掘数据</div><div className="text-[11px] text-zinc-700 mt-1">包含指标、交易记录、每日活动、盈亏分布等全量数据</div></div></Card>}
            {detailLoading && <Card><LoadingBlock text="加载完整数据..." /></Card>}
            {selectedDetail && !selectedDetail._type && !detailLoading && <RecordFullDetail rec={selectedDetail} toast={toast} onOptimize={handleOptimizeFromRecord} />}
          </div>
        </div>
      )}

      {tab === 'research' && (<>
        <Card className="glow-brand border-brand-500/10">
          <h3 className="text-sm font-semibold text-zinc-300 mb-3">🔬 启动研究</h3>
          <div className="flex gap-2 mb-3">
            <input value={topic} onChange={e=>setTopic(e.target.value)} onKeyDown={e=>e.key==='Enter'&&runQuant()} placeholder="输入策略研究主题，例如: 涨停板块轮动短线策略" className="flex-1 bg-surface-3 border border-border rounded-xl px-4 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 transition" />
            <button onClick={()=>runQuant()} disabled={running || optimizing || !topic.trim()} className="btn px-5 py-2.5 bg-brand-600 hover:bg-brand-700 rounded-xl text-sm text-white font-medium transition shadow-lg shadow-brand-600/20 disabled:opacity-40">{running ? <Spinner /> : '启动挖掘'}</button>
          </div>
          <div className="flex flex-wrap gap-2">{presets.map((p,i) => (<button key={i} onClick={()=>{setTopic(p.topic);runQuant(p.topic);}} disabled={running || optimizing} className="btn px-3 py-1.5 bg-surface-3 hover:bg-surface-4 border border-border hover:border-border-light rounded-lg text-[12px] text-zinc-400 hover:text-zinc-200 transition disabled:opacity-40">{p.label}</button>))}</div>
          {(running || optimizing) && (<div className="mt-4 flex items-center gap-3 text-sm text-brand-400 bg-brand-600/5 rounded-xl px-4 py-3 border border-brand-500/10"><Spinner /><span>{optimizing ? '策略优化中' : '量化流水线执行中'}... 请耐心等待</span></div>)}
        </Card>
        {steps.length > 0 && (<Card>
          <h3 className="text-sm font-semibold text-zinc-300 mb-3">📋 研发过程 <span className="text-zinc-600 font-normal">({steps.length} 步)</span></h3>
          <div className="space-y-2">{steps.map((s, i) => (
            <div key={i} className={`rounded-xl border px-4 py-3 transition-all ${s.status === 'running' ? 'border-brand-500/30 bg-brand-600/5 animate-pulse' : s.status === 'error' ? 'border-red-500/20 bg-red-600/5' : s.status === 'done' ? 'border-emerald-500/15 bg-emerald-600/5' : 'border-border bg-surface-2'}`}>
              <div className="flex items-center gap-2 mb-1">
                <span className="text-sm">{stepIcons[s.step] || '📌'}</span>
                <span className="text-[12px] font-medium text-zinc-300">{s.title || stepNames[s.step] || s.step}</span>
                <span className="text-[10px] text-zinc-600">{s.step}</span>
                {s.attempt > 1 && <span className="text-[9px] px-1.5 py-0.5 bg-amber-500/10 text-amber-400 rounded-md">第{s.attempt}轮</span>}
                {s.status === 'running' && <Spinner />}
                {s.status === 'done' && <span className="text-[10px] text-emerald-500">✓ 完成</span>}
                {s.status === 'error' && <span className="text-[10px] text-red-400">✗ 失败</span>}
              </div>
              {s.type === 'code' ? (<ExpandableCode code={s.detail || s.content} label={s.code_len ? `Coder 第${s.attempt||'?'}轮` : ''} />)
                : s.type === 'metrics' && s.metrics ? (<div className="flex flex-wrap gap-2 mt-1">{Object.entries(s.metrics).filter(([k]) => typeof s.metrics[k] === 'number').slice(0,8).map(([k,v]) => (<span key={k} className="text-[10px] bg-surface-3 rounded-lg px-2 py-1 text-zinc-400">{k}: <span className="text-zinc-200 font-medium">{typeof v === 'number' ? v.toFixed(3) : v}</span></span>))}</div>)
                : s.status === 'error' && s.detail ? (<ExpandableDetail text={s.detail} />)
                : (<div className="text-[11px] text-zinc-500 max-h-24 overflow-auto leading-relaxed">{s.content}</div>)
              }
              {s.decision && (<div className={`mt-2 text-[11px] font-medium ${s.decision==='APPROVE' ? 'text-emerald-400' : 'text-red-400'}`}>{s.decision === 'APPROVE' ? '✅ 通过' : '❌ 驳回'}{s.suggestions ? ` — ${s.suggestions}` : ''}</div>)}
            </div>
          ))}</div>
        </Card>)}
        {result && (<Card className={result.status === 'APPROVE' ? 'border-emerald-500/20' : 'border-red-500/15'}>
          <h3 className="text-sm font-semibold text-zinc-300 mb-3">📊 研发结果</h3>
          <pre className="text-[13px] text-zinc-300 leading-relaxed whitespace-pre-wrap mb-4">{result.summary || JSON.stringify(result, null, 2)}</pre>
          <MetricsGrid m={result.metrics} />
          {result.code && (<div className="mt-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[11px] text-zinc-500">策略源码</span>
              <button onClick={()=>{navigator.clipboard.writeText(result.code); toast('代码已复制','success');}} className="btn text-[10px] text-brand-400 hover:text-brand-300 px-2 py-1 rounded-lg transition">复制</button>
            </div>
            <pre className="text-[11px] text-zinc-400 font-mono max-h-48 overflow-auto bg-surface-0/50 rounded-xl p-3 border border-border/50 leading-relaxed">{result.code}</pre>
            <div className="mt-3 flex gap-2">
              <input value={optimizeTopic} onChange={e=>setOptimizeTopic(e.target.value)} placeholder="继续优化方向..." className="flex-1 bg-surface-3 border border-border rounded-xl px-3 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 transition" />
              <button onClick={()=>{ if(!optimizeTopic.trim()) return; handleOptimizeFromRecord(optimizeTopic.trim(), result); }} disabled={running || !optimizeTopic.trim()}
                className="btn px-5 py-2.5 bg-emerald-600 hover:bg-emerald-500 rounded-xl text-sm text-white font-medium transition shadow-lg shadow-emerald-600/20 disabled:opacity-40">迭代优化</button>
            </div>
          </div>)}
        </Card>)}
      </>)}

      {tab === 'library' && (
        <div className="grid md:grid-cols-[1fr_1.2fr] gap-4">
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-zinc-500 uppercase tracking-widest">策略库</span>
              <div className="flex items-center gap-2">
                <span className="text-[10px] text-zinc-600">{strategies.filter(s=>s.source==='factor').length} 因子策略 / {strategies.filter(s=>s.source==='preset').length} 预设</span>
                <button onClick={loadStrategies} className="btn text-[12px] text-brand-400 hover:text-brand-300 px-2 py-1 rounded-lg transition">{strategiesLoading ? <Spinner /> : '刷新'}</button>
              </div>
            </div>
            {strategiesLoading && <LoadingBlock text="加载策略库..." />}
            {!strategiesLoading && strategies.length === 0 && <Card><div className="text-center py-6 text-zinc-600 text-sm"><div className="text-2xl mb-2">📚</div>暂无策略<br/><span className="text-[11px]">在因子库中点击 📈 将因子策略化</span></div></Card>}
            {strategies.map(s => (
              <Card key={s.id} onClick={() => loadStrategyDetail(s.id)} className={`group cursor-pointer ${selectedId === s.id ? 'border-brand-500/30 bg-brand-600/5' : ''}`}>
                <div className="flex items-start gap-2.5">
                  <span className="text-lg mt-0.5">{s.source === 'preset' ? '📋' : s.synced_to_139 ? '🚀' : '📝'}</span>
                  <div className="flex-1 min-w-0">
                    <div className="text-[13px] font-medium text-white group-hover:text-brand-400 transition truncate">{s.title}</div>
                    <div className="text-[11px] text-zinc-600 mt-0.5 line-clamp-1">{s.description || ''}</div>
                    <div className="flex gap-1.5 mt-1.5 flex-wrap items-center">
                      <span className={`text-[9px] px-1.5 py-0.5 rounded-md ${s.source==='factor' ? 'bg-violet-600/15 text-violet-400' : 'bg-blue-600/15 text-blue-400'}`}>{s.source==='factor'?'因子策略':'预设'}</span>
                      {s.synced_to_139 && <span className="text-[9px] px-1.5 py-0.5 bg-emerald-600/15 text-emerald-400 rounded-md">已部署</span>}
                      {s.status === 'draft' && <span className="text-[9px] px-1.5 py-0.5 bg-amber-600/15 text-amber-400 rounded-md">草稿</span>}
                      {s.metrics?.sharpe_ratio != null && <span className="text-[9px] text-zinc-500">Sharpe {Number(s.metrics.sharpe_ratio).toFixed(2)}</span>}
                    </div>
                  </div>
                </div>
              </Card>
            ))}
          </div>
          <div>
            {!selectedId && <Card><div className="text-center py-12 text-zinc-600 text-sm"><div className="text-3xl mb-3">👈</div>选择左侧策略查看详情</div></Card>}
            {detailLoading && <Card><LoadingBlock text="加载详情..." /></Card>}
            {selectedDetail && selectedDetail._type === 'strategy' && !detailLoading && (
              <Card className="border-brand-500/10">
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-sm font-semibold text-white">{selectedDetail.title}</h3>
                  <div className="flex items-center gap-1">
                    {selectedDetail.synced_to_139 && <span className="text-[9px] px-1.5 py-0.5 bg-emerald-600/15 text-emerald-400 rounded-md">已部署 139</span>}
                    {selectedDetail.source === 'factor' && <span className="text-[9px] px-1.5 py-0.5 bg-violet-600/15 text-violet-400 rounded-md">因子: {selectedDetail.factor_id}</span>}
                  </div>
                </div>
                <p className="text-[11px] text-zinc-500 mb-3">{selectedDetail.description}</p>
                {selectedDetail.metrics && Object.keys(selectedDetail.metrics).length > 0 && <MetricsGrid m={selectedDetail.metrics} />}
                {selectedDetail.code && <pre className="mt-3 text-[11px] text-zinc-400 font-mono max-h-64 overflow-auto bg-surface-0/50 rounded-xl p-3 border border-border/50">{selectedDetail.code}</pre>}
                {selectedDetail.params && <div className="mt-2 text-[10px] text-zinc-600">入场阈值: {selectedDetail.params.entry_pct} | 出场阈值: {selectedDetail.params.exit_pct}</div>}
                <div className="flex gap-2 mt-3 flex-wrap">
                  {selectedDetail.source === 'factor' && !selectedDetail.synced_to_139 && (
                    <button onClick={async () => {
                      try {
                        const r = await apiPost('/api/strategies/' + selectedDetail.id + '/sync', {});
                        if (r.ok) { toast('已同步到 192.168.1.139','success'); loadStrategyDetail(selectedDetail.id); loadStrategies(); }
                        else toast(r.error || '同步失败','error');
                      } catch(e) { toast('同步请求失败','error'); }
                    }} className="btn px-3 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-xl text-[12px] text-white font-medium transition">
                      🚀 部署到 ReachRich
                    </button>
                  )}
                  {selectedDetail.synced_to_139 && <span className="px-3 py-2 text-[11px] text-emerald-400 flex items-center gap-1">✅ {selectedDetail.remote_path || '已部署'}</span>}
                  {selectedDetail.source === 'factor' && (
                    <button onClick={async () => {
                      try {
                        toast('正在回测...','info');
                        const r = await apiPost('/api/strategies/' + selectedDetail.id + '/backtest', {});
                        if (r.ok) { toast(`回测完成 Sharpe=${(r.metrics?.sharpe_ratio||0).toFixed(2)}`,'success'); loadStrategyDetail(selectedDetail.id); }
                        else toast(r.error || '回测失败','error');
                      } catch(e) { toast('回测请求失败','error'); }
                    }} className="btn px-3 py-2 bg-blue-600 hover:bg-blue-500 rounded-xl text-[12px] text-white font-medium transition">
                      📊 重新回测
                    </button>
                  )}
                  {selectedDetail.source === 'factor' && (
                    <button disabled={screenLoading} onClick={async () => {
                      setScreenLoading(true); setScreenResult(null);
                      toast('正在运行选股，请稍候...','info');
                      try {
                        const r = await apiPost('/api/strategies/' + selectedDetail.id + '/screen', {top_n: 50});
                        setScreenResult(r);
                        if (r.status === 'success') toast(`选股完成: ${r.count} 只股票 (信号日 ${r.signal_date||r.trade_date})`,'success');
                        else toast(r.error || '选股失败','error');
                      } catch(e) { toast('选股请求失败','error'); }
                      setScreenLoading(false);
                    }} className="btn px-3 py-2 bg-amber-600 hover:bg-amber-500 rounded-xl text-[12px] text-white font-medium transition disabled:opacity-50">
                      {screenLoading ? <span className="flex items-center gap-1"><Spinner size={3}/>选股中...</span> : '🎯 运行选股'}
                    </button>
                  )}
                  {selectedDetail.source === 'factor' && (
                    <button onClick={async () => {
                      if (!confirm('确定删除此策略？')) return;
                      try {
                        const r = await fetch('/api/strategies/' + selectedDetail.id, {method:'DELETE', headers: authHeaders()});
                        if (r.ok) { toast('已删除','success'); setSelectedDetail(null); setSelectedId(''); loadStrategies(); }
                        else toast('删除失败','error');
                      } catch(e) { toast('删除失败','error'); }
                    }} className="btn px-3 py-2 bg-rose-600/20 hover:bg-rose-600/30 rounded-xl text-[12px] text-rose-400 font-medium transition">
                      🗑️ 删除
                    </button>
                  )}
                </div>
                {/* Screen results panel */}
                {screenResult && screenResult.status === 'success' && screenResult.count > 0 && (
                  <div className="mt-3 bg-surface-3/50 rounded-xl border border-amber-500/20 p-3">
                    <div className="flex items-center justify-between mb-2">
                      <h4 className="text-[12px] font-semibold text-amber-400">
                        🎯 选股结果 — {screenResult.signal_date || screenResult.trade_date} 信号
                      </h4>
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] text-zinc-500">{screenResult.count}/{screenResult.total_selected} 只</span>
                        <button onClick={()=>{
                          const text = screenResult.stocks.map(s=>`${s.ts_code} ${s.name} ${s.close} ${s.pct_chg>0?'+':''}${s.pct_chg.toFixed(2)}%`).join('\n');
                          navigator.clipboard.writeText(text); toast('已复制股票列表','success');
                        }} className="text-[10px] text-zinc-400 hover:text-zinc-300 transition">📋 复制</button>
                        <button onClick={()=>setScreenResult(null)} className="text-[10px] text-zinc-500 hover:text-zinc-400">✕</button>
                      </div>
                    </div>
                    <div className="overflow-x-auto rounded-lg border border-border max-h-[300px] overflow-y-auto overflow-x-hidden">
                      <table className="w-full min-w-0 text-[10px] md:text-[11px]">
                        <thead className="sticky top-0"><tr className="bg-surface-3 text-zinc-500">
                          <th className="px-2 py-1.5 text-left font-medium">代码</th>
                          <th className="px-2 py-1.5 text-left font-medium">名称</th>
                          <th className="px-2 py-1.5 text-right font-medium">收盘价</th>
                          <th className="px-2 py-1.5 text-right font-medium">涨跌%</th>
                          {screenResult.stocks[0]?.factor_score != null && <th className="px-2 py-1.5 text-right font-medium">因子分</th>}
                        </tr></thead>
                        <tbody>{screenResult.stocks.map((s,i) => (
                          <tr key={i} className="border-t border-border/30 hover:bg-surface-3/50">
                            <td className="px-2 py-1 font-mono text-zinc-300">{s.ts_code}</td>
                            <td className="px-2 py-1 text-zinc-400">{s.name}</td>
                            <td className="px-2 py-1 text-right text-zinc-300">{Number(s.close).toFixed(2)}</td>
                            <td className={`px-2 py-1 text-right font-mono ${s.pct_chg>0?'text-red-400':s.pct_chg<0?'text-emerald-400':'text-zinc-500'}`}>{s.pct_chg>0?'+':''}{Number(s.pct_chg).toFixed(2)}%</td>
                            {s.factor_score != null && <td className="px-2 py-1 text-right font-mono text-brand-400">{Number(s.factor_score).toFixed(4)}</td>}
                          </tr>
                        ))}</tbody>
                      </table>
                    </div>
                  </div>
                )}
                {screenResult && screenResult.status === 'success' && screenResult.count === 0 && (
                  <div className="mt-3 bg-surface-3/50 rounded-xl border border-zinc-600/20 p-3 text-center text-[11px] text-zinc-500">
                    {screenResult.message || '最近交易日无入场信号'}
                  </div>
                )}
                {screenResult && screenResult.status === 'error' && (
                  <div className="mt-3 bg-red-600/5 rounded-xl border border-red-500/20 p-3 text-[11px] text-red-400">
                    选股失败: {screenResult.error}
                  </div>
                )}
                {selectedDetail.source !== 'factor' && (
                  <div className="flex gap-2 mt-3">
                    <input value={optimizeTopic} onChange={e=>setOptimizeTopic(e.target.value)} placeholder="基于此策略优化..." className="flex-1 bg-surface-3 border border-border rounded-xl px-3 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 transition" />
                    <button onClick={() => { if (!optimizeTopic.trim()) return; handleOptimizeFromRecord(optimizeTopic.trim(), selectedDetail); }} disabled={running || !optimizeTopic.trim()}
                      className="btn px-4 py-2.5 bg-emerald-600 hover:bg-emerald-500 rounded-xl text-sm text-white font-medium transition disabled:opacity-40">优化</button>
                  </div>
                )}
              </Card>
            )}
          </div>
        </div>
      )}

      {tab === 'digger' && (
        <div className="space-y-4">
          {/* Sub-tabs */}
          <div className="flex gap-1 bg-surface-2 p-1 rounded-xl">
            {[{id:'library',label:'因子库',icon:'📦'},{id:'analysis',label:'健康分析',icon:'🔬'},{id:'combine_history',label:'融合记录',icon:'🔮'},{id:'exhaustive',label:'穷举融合',icon:'🧪'},{id:'pipeline',label:'n8n 管线',icon:'🔗'},{id:'mine',label:'启动挖掘',icon:'⛏️'},{id:'logs',label:'挖掘日志',icon:'📋',badge:diggerLogs.length}].map(t=>(
              <button key={t.id} onClick={()=>{setDiggerSubTab(t.id);if(t.id==='library'&&factorList.length===0)loadFactorList();if(t.id==='combine_history')loadCombineHistory();}}
                className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg text-[12px] font-medium transition ${diggerSubTab===t.id?'bg-surface-4 text-zinc-200 shadow':'text-zinc-500 hover:text-zinc-400'}`}>
                <span>{t.icon}</span><span>{t.label}</span>{t.badge?<span className="ml-1 px-1.5 py-0.5 bg-brand-600/20 text-brand-400 text-[10px] rounded-full">{t.badge}</span>:null}
              </button>
            ))}
          </div>

          {/* === 因子库 Sub-tab === */}
          {diggerSubTab === 'library' && (<>
            <Card className="glow-brand border-brand-500/10">
              {/* Header + Actions */}
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-zinc-300">📦 因子库 {factorList.length > 0 && <span className="text-zinc-600 font-normal ml-1">({factorList.filter(f=>f.status==='active').length} 活跃 / {factorList.length} 总计)</span>}</h3>
                <div className="flex gap-2">
                  <button onClick={triggerCombine} disabled={combineLoading || factorList.filter(f=>f.status==='active').length < 2}
                    className="btn px-4 py-1.5 bg-amber-600 hover:bg-amber-500 text-white text-[12px] font-semibold rounded-lg transition shadow-lg shadow-amber-600/20 disabled:opacity-50">
                    {combineLoading ? <span className="flex items-center gap-1"><Spinner size={3}/>融合中...</span> : selectedForCombine.length >= 2 ? `🔮 融合 ${selectedForCombine.length} 个` : '🔮 全部融合'}
                  </button>
                  {selectedForCombine.length > 0 && (
                    <button onClick={()=>setSelectedForCombine([])} className="btn px-3 py-1.5 bg-surface-2 hover:bg-surface-3 rounded-lg text-[12px] text-zinc-400 border border-border transition">清除选择</button>
                  )}
                  <button onClick={loadFactorList} disabled={factorListLoading}
                    className="btn px-3 py-1.5 bg-surface-2 hover:bg-surface-3 rounded-lg text-[12px] text-zinc-400 border border-border transition">
                    {factorListLoading ? <Spinner size={3}/> : '刷新'}
                  </button>
                </div>
              </div>

              {/* Stats row */}
              {factorStats && (
                <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 mb-4">
                  {[
                    {label:'活跃因子',value:factorStats.active_count,color:'text-emerald-400'},
                    {label:'最佳 Sharpe',value:(factorStats.best_sharpe||0).toFixed(2),color:'text-brand-400'},
                    {label:'最佳 IR',value:(factorStats.best_ir||0).toFixed(2),color:'text-amber-400'},
                    {label:'⚡ 向量化',value:factorStats.complexity_dist?.vectorized||0,color:'text-cyan-400'},
                    {label:'🐌 嵌套循环',value:factorStats.complexity_dist?.nested||0,color:'text-red-400'},
                    {label:'可融合',value:(factorStats.complexity_dist?.vectorized||0)+(factorStats.complexity_dist?.apply||0),color:factorStats.ready_to_combine?'text-emerald-400':'text-zinc-500'},
                  ].map((m,i)=>(
                    <div key={i} className="bg-surface-3/50 rounded-lg px-3 py-2 border border-border/30">
                      <div className="text-[10px] text-zinc-500">{m.label}</div>
                      <div className={`text-sm font-semibold ${m.color}`}>{m.value}</div>
                    </div>
                  ))}
                </div>
              )}

              {/* Theme distribution tags */}
              {factorStats?.theme_distribution && (
                <div className="flex flex-wrap gap-1.5 mb-3">
                  {Object.entries(factorStats.theme_distribution).sort((a,b)=>b[1]-a[1]).slice(0,12).map(([t,c])=>(
                    <button key={t} onClick={()=>{setFactorSearch(t);setFactorFilter('all');}} className={`text-[10px] px-2 py-0.5 rounded-lg border border-border/30 transition hover:border-border-light ${themeBg(t)} ${themeColor(t)}`}>
                      {themeLabel(t)} <span className="text-zinc-600 ml-0.5">{c}</span>
                    </button>
                  ))}
                </div>
              )}

              {/* Search + Filter + Sort toolbar */}
              <div className="flex flex-wrap gap-2 mb-3">
                <input type="text" value={factorSearch} onChange={e=>setFactorSearch(e.target.value)} placeholder="搜索因子（主题/ID/代码关键词）..."
                  className="flex-1 min-w-[200px] bg-surface-3 rounded-lg px-3 py-1.5 text-[12px] text-zinc-300 border border-border placeholder-zinc-600 focus:border-brand-500/50 focus:outline-none transition" />
                <select value={factorFilter} onChange={e=>setFactorFilter(e.target.value)}
                  className="bg-surface-3 rounded-lg px-3 py-1.5 text-[12px] text-zinc-300 border border-border focus:outline-none">
                  <option value="all">全部</option>
                  <option value="active">活跃</option>
                  <option value="combinable">可融合 (⚡)</option>
                  <option value="vectorized">纯向量化</option>
                  <option value="astock">A股实战</option>
                  <option value="technical">传统技术</option>
                  <option value="retired">已退休</option>
                </select>
                <select value={factorSort} onChange={e=>setFactorSort(e.target.value)}
                  className="bg-surface-3 rounded-lg px-3 py-1.5 text-[12px] text-zinc-300 border border-border focus:outline-none">
                  <option value="sharpe">按 Sharpe ↓</option>
                  <option value="ir">按 IR ↓</option>
                  <option value="ic">按 IC ↓</option>
                  <option value="trades">按交易量 ↓</option>
                  <option value="newest">最新优先</option>
                </select>
              </div>
            </Card>

            {/* Factor list table */}
            {(() => { const filtered = getFilteredFactors(); return filtered.length > 0 ? (
              <Card>
                <div className="text-[11px] text-zinc-500 mb-2">显示 {filtered.length} / {factorList.length} 个因子</div>
                <div className="overflow-x-auto">
                  <table className="w-full min-w-0 text-[12px]">
                    <thead>
                      <tr className="text-zinc-500 border-b border-border/30">
                        <th className="py-2 px-1 w-6"><input type="checkbox" checked={selectedForCombine.length>0 && selectedForCombine.length===filtered.filter(f=>f.status==='active').length} onChange={e=>{ if(e.target.checked) setSelectedForCombine(filtered.filter(f=>f.status==='active').map(f=>f.id)); else setSelectedForCombine([]); }} className="accent-brand-500" /></th>
                        <th className="text-left py-2 px-2 font-medium">主题</th>
                        <th className="text-center py-2 px-1 font-medium w-8" title="计算复杂度">⚡</th>
                        <th className="text-right py-2 px-2 font-medium">Sharpe</th>
                        <th className="text-right py-2 px-2 font-medium">IR</th>
                        <th className="text-right py-2 px-2 font-medium">IC</th>
                        <th className="text-right py-2 px-2 font-medium">Win%</th>
                        <th className="text-right py-2 px-2 font-medium">Trades</th>
                        <th className="text-center py-2 px-2 font-medium">状态</th>
                        <th className="text-center py-2 px-2 font-medium">操作</th>
                      </tr>
                    </thead>
                    <tbody>
                      {filtered.map((f,i) => (
                        <tr key={f.id||i} className={`border-b border-border/10 hover:bg-surface-3/30 cursor-pointer transition ${selectedFactor?.id===f.id?'bg-brand-600/5':''}${selectedForCombine.includes(f.id)?' bg-amber-600/5':''}${f.status!=='active'?' opacity-50':''}`}
                            onClick={()=>{setSelectedFactor(selectedFactor?.id===f.id?null:f);setFactorDetailTab('metrics');}}>
                          <td className="py-2 px-1" onClick={e=>e.stopPropagation()}>
                            {f.status==='active' && <input type="checkbox" checked={selectedForCombine.includes(f.id)} onChange={()=>toggleCombineSelect(f.id)} className="accent-amber-500" />}
                          </td>
                          <td className="py-2 px-2 max-w-[220px]">
                            <span className={`text-[10px] px-1.5 py-0.5 rounded ${themeBg(f.theme)} ${themeColor(f.theme)} mr-1`}>{themeLabel(f.theme)}</span>
                            <span className="text-zinc-400 text-[11px] truncate">{f.sub_theme && f.sub_theme !== f.theme ? f.sub_theme.replace(/^[^:]+:\s*/, '') : ''}</span>
                            <span className="text-[9px] text-zinc-700 ml-1 font-mono">{(f.id||'').slice(-6)}</span>
                          </td>
                          <td className="py-2 px-1 text-center" title={f.complexity==='vectorized'?'向量化（快速）':f.complexity==='apply'?'含 apply（较慢）':'嵌套循环（慢，不可融合）'}>
                            <span className="text-[11px]">{f.complexity==='vectorized'?'⚡':f.complexity==='apply'?'⚠️':'🐌'}</span>
                          </td>
                          <td className={`py-2 px-2 text-right font-mono ${f.sharpe>=1?'text-emerald-400':f.sharpe>=0.5?'text-brand-400':'text-zinc-500'}`}>{(f.sharpe||0).toFixed(2)}</td>
                          <td className={`py-2 px-2 text-right font-mono ${Math.abs(f.ir)>=1?'text-emerald-400':'text-zinc-400'}`}>{(f.ir||0).toFixed(2)}</td>
                          <td className="py-2 px-2 text-right font-mono text-zinc-400">{(f.ic_mean||0).toFixed(4)}</td>
                          <td className="py-2 px-2 text-right font-mono text-zinc-400">{((f.win_rate||0)*100).toFixed(1)}%</td>
                          <td className="py-2 px-2 text-right font-mono text-zinc-400">{f.trades||0}</td>
                          <td className="py-2 px-2 text-center">
                            <span className={`px-1.5 py-0.5 rounded text-[10px] ${f.status==='active'?'bg-emerald-600/20 text-emerald-400':f.status==='decayed'?'bg-amber-600/20 text-amber-400':'bg-zinc-600/20 text-zinc-500'}`}>{f.status}</span>
                          </td>
                          <td className="py-2 px-2 text-center" onClick={e=>e.stopPropagation()}>
                            <div className="flex items-center gap-1 justify-center">
                              {f.status==='active' && (<>
                                <button onClick={()=>factorToStrategy(f.id)} disabled={toStrategyLoading} title="转入策略库深入研发" className="text-[10px] text-brand-400/70 hover:text-brand-300 transition disabled:opacity-50">{toStrategyLoading ? '⏳' : '📈'}</button>
                                <button onClick={()=>retireFactor(f.id)} title="退休因子" className="text-[10px] text-red-500/50 hover:text-red-400 transition">🗑️</button>
                              </>)}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            ) : !factorListLoading ? (
              <Card><div className="text-center py-8 text-zinc-600 text-sm">{factorList.length > 0 ? '没有匹配的因子，试试调整搜索条件' : '暂无因子数据，点击上方「刷新」加载'}</div></Card>
            ) : null; })()}

            {/* Factor detail panel */}
            {selectedFactor && (
              <Card className="glow-brand border-brand-500/10">
                {/* Header */}
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    <h3 className="text-sm font-semibold text-zinc-300">{selectedFactor.sub_theme || selectedFactor.theme}</h3>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-3 text-zinc-500 font-mono">{selectedFactor.id}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded ${selectedFactor.complexity==='vectorized'?'bg-cyan-600/20 text-cyan-400':selectedFactor.complexity==='apply'?'bg-amber-600/20 text-amber-400':'bg-red-600/20 text-red-400'}`}>
                      {selectedFactor.complexity==='vectorized'?'⚡ 向量化':selectedFactor.complexity==='apply'?'⚠️ apply':'🐌 嵌套循环'}
                    </span>
                    {selectedFactor.code_lines && <span className="text-[10px] text-zinc-600">{selectedFactor.code_lines} 行</span>}
                  </div>
                  <button onClick={()=>setSelectedFactor(null)} className="text-zinc-500 hover:text-zinc-300 text-lg transition">✕</button>
                </div>
                <div className="text-[10px] text-zinc-600 mb-3">
                  创建: {selectedFactor.created_at?new Date(selectedFactor.created_at*1000).toLocaleString():'N/A'}
                  {selectedFactor.theme && selectedFactor.theme !== selectedFactor.sub_theme && <span className="ml-2">分类: {selectedFactor.theme}</span>}
                </div>

                {/* Tabs */}
                <div className="flex gap-1 mb-4 bg-surface-2 rounded-lg p-1">
                  {[{id:'metrics',label:'📊 指标'},{id:'code',label:'💻 代码'},{id:'usage',label:'📖 使用方法'}].map(t=>(
                    <button key={t.id} onClick={()=>setFactorDetailTab(t.id)}
                      className={`flex-1 px-3 py-1.5 rounded-md text-[12px] font-medium transition ${factorDetailTab===t.id?'bg-surface-4 text-zinc-200 shadow':'text-zinc-500 hover:text-zinc-400'}`}>{t.label}</button>
                  ))}
                </div>

                {/* Metrics tab */}
                {factorDetailTab === 'metrics' && (
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    {[
                      {label:'Sharpe Ratio',value:(selectedFactor.sharpe||0).toFixed(3),desc:'风险调整收益，>1 优秀',good:selectedFactor.sharpe>=1},
                      {label:'IR (Sortino)',value:(selectedFactor.ir||0).toFixed(3),desc:'下行风险调整收益，>1 优秀',good:Math.abs(selectedFactor.ir)>=1},
                      {label:'Mean IC',value:(selectedFactor.ic_mean||0).toFixed(4),desc:'因子预测能力，|IC|>0.03 有效',good:Math.abs(selectedFactor.ic_mean)>=0.03},
                      {label:'Win Rate',value:((selectedFactor.win_rate||0)*100).toFixed(1)+'%',desc:'盈利交易占比，>50% 为佳',good:selectedFactor.win_rate>=0.5},
                      {label:'交易次数',value:selectedFactor.trades||0,desc:'回测期总交易数，>100 样本充足',good:selectedFactor.trades>=100},
                      {label:'最大回撤',value:((selectedFactor.max_drawdown||0)*100).toFixed(2)+'%',desc:'最大权益回撤，<20% 为佳',good:selectedFactor.max_drawdown<0.2},
                      {label:'换手率',value:(selectedFactor.turnover||0).toFixed(4),desc:'日均换手率',good:true},
                      {label:'单调性',value:(selectedFactor.monotonicity||0).toFixed(4),desc:'分组收益单调性，越接近1越好',good:selectedFactor.monotonicity>=0.5},
                    ].map((m,i)=>(
                      <div key={i} className="bg-surface-3/50 rounded-lg px-3 py-2.5 border border-border/30">
                        <div className="text-[10px] text-zinc-500 mb-0.5">{m.label}</div>
                        <div className={`text-[14px] font-mono font-semibold ${m.good?'text-emerald-400':'text-zinc-300'}`}>{m.value}</div>
                        <div className="text-[9px] text-zinc-600 mt-0.5">{m.desc}</div>
                      </div>
                    ))}
                  </div>
                )}

                {/* Code tab */}
                {factorDetailTab === 'code' && (
                  <div>
                    <div className="flex items-center justify-between mb-2">
                      <div className="text-[11px] text-zinc-500">generate_factor 源码 ({selectedFactor.code_lines||'?'} 行)</div>
                      <div className="flex gap-2">
                        <button onClick={()=>{navigator.clipboard.writeText(selectedFactor.code||'');toast('代码已复制到剪贴板','success');}}
                          className="btn px-3 py-1 bg-surface-3 hover:bg-surface-4 text-zinc-300 text-[11px] rounded-lg border border-border transition">📋 复制</button>
                        <button onClick={()=>{
                          const usage = `# 因子: ${selectedFactor.sub_theme || selectedFactor.theme}\n# ID: ${selectedFactor.id}\n# Sharpe: ${(selectedFactor.sharpe||0).toFixed(3)} | IR: ${(selectedFactor.ir||0).toFixed(3)} | IC: ${(selectedFactor.ic_mean||0).toFixed(4)}\n#\n# 用法: factor = generate_factor(matrices)\n#   matrices = {'open': df, 'high': df, 'low': df, 'close': df, 'volume': df}\n#   返回: DataFrame (index=trade_date, columns=ts_code), 值越高越看多\n\n${selectedFactor.code||''}`;
                          navigator.clipboard.writeText(usage);toast('代码+使用说明已复制','success');
                        }}
                          className="btn px-3 py-1 bg-brand-600/20 hover:bg-brand-600/30 text-brand-400 text-[11px] rounded-lg border border-brand-500/20 transition">📋 复制含说明</button>
                      </div>
                    </div>
                    <div className="bg-[#0d1117] rounded-xl border border-border/30 overflow-hidden">
                      <div className="overflow-x-auto max-h-[500px] overflow-y-auto overflow-x-hidden p-4">
                        <pre className="text-[12px] leading-relaxed font-mono whitespace-pre">{(selectedFactor.code||'(代码不可用)').split('\n').map((line,i)=>(
                          <div key={i} className="flex"><span className="select-none text-zinc-700 w-8 text-right mr-3 flex-shrink-0">{i+1}</span><span className={line.trim().startsWith('#')?'text-zinc-600':line.trim().startsWith('def ')?'text-amber-400':line.trim().startsWith('import ')?'text-cyan-400/70':line.trim().startsWith('return ')?'text-rose-400':'text-emerald-300/90'}>{line}</span></div>
                        ))}</pre>
                      </div>
                    </div>
                  </div>
                )}

                {/* Usage tab */}
                {factorDetailTab === 'usage' && (
                  <div className="space-y-4">
                    <div className="bg-surface-3/30 rounded-xl p-4 border border-border/20">
                      <h4 className="text-[13px] font-semibold text-zinc-200 mb-3">如何使用此因子</h4>
                      <div className="space-y-3 text-[12px] text-zinc-400 leading-relaxed">
                        <div className="flex gap-3">
                          <span className="text-brand-400 font-bold text-lg leading-none mt-0.5">1</span>
                          <div>
                            <div className="text-zinc-200 font-medium mb-1">直接回测</div>
                            <p>将 <code className="bg-surface-3 px-1.5 py-0.5 rounded text-emerald-400 text-[11px]">generate_factor(matrices)</code> 代码复制到回测系统。函数接收 OHLCV 矩阵字典，返回同 shape 的 DataFrame —— 值越高表示越看好该股票（做多信号越强）。</p>
                          </div>
                        </div>
                        <div className="flex gap-3">
                          <span className="text-brand-400 font-bold text-lg leading-none mt-0.5">2</span>
                          <div>
                            <div className="text-zinc-200 font-medium mb-1">策略化研发</div>
                            <p>点击「转入策略研发」，系统会基于此因子自动生成完整交易策略（含入场/出场/仓位管理），并在量化研发模块中迭代优化。因子 → 策略的转化由 LLM 自动完成。</p>
                          </div>
                        </div>
                        <div className="flex gap-3">
                          <span className="text-brand-400 font-bold text-lg leading-none mt-0.5">3</span>
                          <div>
                            <div className="text-zinc-200 font-medium mb-1">因子融合</div>
                            <p>勾选多个因子后点击「融合」，系统将多因子等权平均组合成复合因子，在 139 沙箱重新回测。融合通常能提升 Sharpe 和稳定性。<strong className="text-amber-400">注意：仅 ⚡ 向量化因子可参与融合</strong>（🐌 嵌套循环因子会导致沙箱超时）。</p>
                          </div>
                        </div>
                      </div>
                    </div>
                    <div className="bg-surface-3/30 rounded-xl p-4 border border-border/20">
                      <h4 className="text-[13px] font-semibold text-zinc-200 mb-3">指标速查</h4>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[11px]">
                        {[
                          {metric:'Sharpe > 1',meaning:'优秀因子，风险调整后收益显著'},
                          {metric:'|IC| > 0.03',meaning:'因子对未来收益有预测能力'},
                          {metric:'IR > 1',meaning:'信息比率高，因子收益稳定'},
                          {metric:'Win Rate > 50%',meaning:'多数交易盈利'},
                          {metric:'Trades > 100',meaning:'回测样本充足，统计可靠'},
                          {metric:'Max DD < 20%',meaning:'风险可控，回撤较小'},
                        ].map((r,i)=>(
                          <div key={i} className="flex gap-2 items-start bg-surface-3/50 rounded-lg px-3 py-2 border border-border/20">
                            <code className="text-emerald-400 font-mono whitespace-nowrap">{r.metric}</code>
                            <span className="text-zinc-400">{r.meaning}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                    <div className="bg-[#0d1117] rounded-xl p-4 border border-border/30">
                      <div className="text-[11px] text-zinc-500 mb-2">调用示例 (Python)</div>
                      <pre className="text-[12px] leading-relaxed font-mono text-emerald-300/90 whitespace-pre">{`# 因子 ID: ${selectedFactor.id}
# ${selectedFactor.sub_theme || selectedFactor.theme}

import pandas as pd

# 准备数据：每个 key 是一个 DataFrame (index=日期, columns=股票代码)
matrices = {
    'open':   open_df,    # shape: (120天, 6300只)
    'high':   high_df,
    'low':    low_df,
    'close':  close_df,
    'volume': volume_df,
}

# 调用因子
factor = generate_factor(matrices)

# factor 是 DataFrame, 同 shape
# 每日每股一个因子值，值越高 = 越看好
top_stocks = factor.iloc[-1].nlargest(20)  # 今日 Top 20`}</pre>
                    </div>
                  </div>
                )}

                {/* Action buttons */}
                <div className="mt-4 pt-3 border-t border-border/20 flex flex-wrap gap-2">
                  <button onClick={()=>{navigator.clipboard.writeText(selectedFactor.code||'');toast('已复制','success');}}
                    className="btn px-4 py-2 bg-surface-3 hover:bg-surface-4 text-zinc-300 text-[12px] rounded-lg border border-border transition">📋 复制代码</button>
                  <button onClick={()=>{navigator.clipboard.writeText(selectedFactor.id||'');toast('ID 已复制','success');}}
                    className="btn px-3 py-2 bg-surface-3 hover:bg-surface-4 text-zinc-400 text-[11px] rounded-lg border border-border transition font-mono">#ID</button>
                  {selectedFactor.status==='active' && (<>
                    <button onClick={()=>factorToStrategy(selectedFactor.id)} disabled={toStrategyLoading}
                      className="btn px-4 py-2 bg-gradient-to-r from-violet-600 to-brand-600 hover:from-violet-500 hover:to-brand-500 text-white text-[12px] font-medium rounded-lg transition shadow-lg shadow-brand-600/20 disabled:opacity-50">
                      {toStrategyLoading ? <span className="flex items-center gap-1"><Spinner size={3}/>回测中...</span> : '🚀 生成策略 → 策略库'}
                    </button>
                    <button onClick={()=>{toggleCombineSelect(selectedFactor.id);toast(selectedForCombine.includes(selectedFactor.id)?'已取消选择':'已加入融合列表','success');}}
                      className={`btn px-4 py-2 text-[12px] rounded-lg border transition ${selectedForCombine.includes(selectedFactor.id)?'bg-amber-600/20 text-amber-400 border-amber-500/30':'bg-surface-3 hover:bg-surface-4 text-zinc-400 border-border'}`}>
                      {selectedForCombine.includes(selectedFactor.id)?'✅ 已选中融合':'🔮 加入融合'}
                    </button>
                    <button onClick={()=>retireFactor(selectedFactor.id)}
                      className="btn px-4 py-2 bg-red-600/10 hover:bg-red-600/20 text-red-400 text-[12px] rounded-lg border border-red-600/20 transition">🗑️ 退休</button>
                  </>)}
                </div>
              </Card>
            )}
          </>)}

          {/* === 健康分析 Sub-tab === */}
          {diggerSubTab === 'analysis' && (<>
              <Card className="glow-brand border-brand-500/10">
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-semibold text-zinc-300">🔬 因子库健康分析</h3>
                  <button onClick={runAnalysis} disabled={analysisLoading}
                    className="btn px-4 py-1.5 bg-brand-600 hover:bg-brand-500 text-white text-[12px] font-semibold rounded-lg transition disabled:opacity-50">
                    {analysisLoading ? <span className="flex items-center gap-1"><Spinner size={3}/>分析中...</span> : '运行分析'}
                  </button>
                </div>
                <p className="text-[11px] text-zinc-500 mb-3">检测过拟合因子、指标聚类降维、主题多样性分析。基于 Sharpe/WR/DD/Trades 综合判断。</p>

                {analysisData && (<>
                  {/* Tier summary */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-4">
                    {Object.entries(analysisData.tiers).map(([k,v])=>(
                      <div key={k} className="bg-surface-3/50 rounded-lg px-3 py-2.5 border border-border/30">
                        <div className="text-[10px] text-zinc-500">{tierNames[k]}</div>
                        <div className={`text-lg font-bold ${tierColors[k]}`}>{v.count}</div>
                        <div className="text-[9px] text-zinc-600">{v.desc}</div>
                      </div>
                    ))}
                  </div>

                  {/* Theme distribution */}
                  <div className="mb-4">
                    <h4 className="text-[12px] font-semibold text-zinc-300 mb-2">主题分布 (正常因子)</h4>
                    <div className="flex flex-wrap gap-1.5">
                      {Object.entries(analysisData.theme_distribution).map(([t,c])=>(
                        <span key={t} className="bg-surface-3 rounded-lg px-2.5 py-1 text-[11px] text-zinc-400 border border-border/30">
                          {t} <span className="text-brand-400 font-medium">{c}</span>
                        </span>
                      ))}
                    </div>
                  </div>

                  {/* Cluster representatives */}
                  <div className="mb-4">
                    <h4 className="text-[12px] font-semibold text-zinc-300 mb-2">
                      降维聚类 — {analysisData.clusters.combinable_factors} 可融合因子 → {analysisData.clusters.total} 独立聚类
                    </h4>
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-0 text-[10px] md:text-[11px]">
                        <thead><tr className="text-zinc-500 border-b border-border/30">
                          <th className="py-1.5 px-2 text-left">#</th>
                          <th className="py-1.5 px-2 text-left">代表因子</th>
                          <th className="py-1.5 px-2 text-right">Sharpe</th>
                          <th className="py-1.5 px-2 text-right">IR</th>
                          <th className="py-1.5 px-2 text-right">IC</th>
                          <th className="py-1.5 px-2 text-center">聚类大小</th>
                          <th className="py-1.5 px-2 text-center">操作</th>
                        </tr></thead>
                        <tbody>
                          {analysisData.clusters.top_clusters.map((cl,i) => {const r=cl.representative; return (
                            <tr key={r.id} className="border-b border-border/10 hover:bg-surface-3/30 transition">
                              <td className="py-1.5 px-2 text-zinc-600">{i+1}</td>
                              <td className="py-1.5 px-2 text-zinc-300 max-w-[180px] truncate">
                                <span className={`mr-1 text-[10px] ${r.complexity==='vectorized'?'text-cyan-400':'text-amber-400'}`}>{r.complexity==='vectorized'?'⚡':'⚠️'}</span>
                                {r.sub_theme||r.theme} <span className="text-zinc-600 text-[10px] font-mono ml-1">{r.id.slice(-6)}</span>
                              </td>
                              <td className={`py-1.5 px-2 text-right font-mono ${r.sharpe>=1?'text-emerald-400':'text-zinc-400'}`}>{r.sharpe.toFixed(2)}</td>
                              <td className="py-1.5 px-2 text-right font-mono text-zinc-400">{r.ir.toFixed(2)}</td>
                              <td className="py-1.5 px-2 text-right font-mono text-zinc-400">{r.ic_mean.toFixed(4)}</td>
                              <td className="py-1.5 px-2 text-center"><span className="bg-surface-3 px-2 py-0.5 rounded text-[10px] text-zinc-400">{cl.size}</span></td>
                              <td className="py-1.5 px-2 text-center">
                                <button onClick={()=>strategize(r.id)} disabled={stratLoading}
                                  className="text-[10px] text-brand-400 hover:text-brand-300 transition disabled:opacity-50">
                                  {stratLoading ? '...' : '📈 策略化'}
                                </button>
                              </td>
                            </tr>
                          );})}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  {/* Strategy result */}
                  {stratResult && (
                    <div className={`rounded-xl p-4 border ${stratResult.ok?'bg-emerald-600/5 border-emerald-500/20':'bg-red-600/5 border-red-500/20'}`}>
                      <h4 className="text-[12px] font-semibold text-zinc-300 mb-2">
                        {stratResult.ok ? '✅ 策略化结果' : '❌ 策略化失败'} — {stratResult.factor_theme}
                      </h4>
                      {stratResult.ok && stratResult.strategy_metrics && (
                        <div className="grid grid-cols-3 sm:grid-cols-6 gap-2 mb-3">
                          {[
                            {l:'Sharpe',v:stratResult.strategy_metrics.sharpe_ratio},
                            {l:'年化收益',v:stratResult.strategy_metrics.annualized_return_pct,u:'%'},
                            {l:'最大回撤',v:stratResult.strategy_metrics.max_drawdown_pct,u:'%'},
                            {l:'胜率',v:stratResult.strategy_metrics.win_rate_pct,u:'%'},
                            {l:'交易数',v:stratResult.strategy_metrics.total_trades},
                            {l:'Calmar',v:stratResult.strategy_metrics.calmar_ratio},
                          ].filter(x=>x.v!=null).map((x,i)=>(
                            <div key={i} className="bg-surface-3/50 rounded-lg px-2 py-1.5 text-center border border-border/20">
                              <div className="text-[9px] text-zinc-500">{x.l}</div>
                              <div className="text-[12px] font-mono text-zinc-200">{typeof x.v==='number'?x.v.toFixed(2):x.v}{x.u||''}</div>
                            </div>
                          ))}
                        </div>
                      )}
                      {stratResult.ok && (
                        <div className="flex gap-2 flex-wrap">
                          {stratResult.strategy_id && (
                            <button onClick={()=>{setTab('library');loadStrategies();}} className="btn px-3 py-1.5 bg-brand-600 hover:bg-brand-500 text-white text-[11px] rounded-lg transition">📚 查看策略库</button>
                          )}
                          {stratResult.strategy_code && (
                            <button onClick={()=>{navigator.clipboard.writeText(stratResult.strategy_code);toast('策略代码已复制','success');}}
                              className="btn px-3 py-1.5 bg-surface-3 hover:bg-surface-4 text-zinc-300 text-[11px] rounded-lg border border-border transition">📋 复制代码</button>
                          )}
                        </div>
                      )}
                      {stratResult.error && <p className="text-[11px] text-red-400">{stratResult.error}</p>}
                    </div>
                  )}
                </>)}
                {!analysisData && !analysisLoading && <div className="text-center py-8 text-zinc-600 text-sm">点击「运行分析」检查因子库健康状况</div>}
              </Card>
            </>)}

          {/* === 穷举融合 Sub-tab === */}
          {diggerSubTab === 'exhaustive' && (<>
            <Card className="glow-brand border-brand-500/10">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-zinc-300">🧪 穷举因子组合</h3>
                <button onClick={runExhaustiveCombine} disabled={exhaustiveRunning}
                  className="btn px-4 py-1.5 bg-brand-600 hover:bg-brand-500 text-white text-[12px] font-semibold rounded-lg transition disabled:opacity-50">
                  {exhaustiveRunning ? <span className="flex items-center gap-1"><Spinner size={3}/>运行中...</span> : '开始穷举'}
                </button>
              </div>
              <p className="text-[11px] text-zinc-500 mb-3">对所有可融合因子做 C(n, k) 排列组合，逐个送入 139 沙箱回测。已测试过的组合自动跳过。</p>

              <div className="flex gap-3 mb-4">
                <div>
                  <label className="text-[10px] text-zinc-500 block mb-1">组合大小 (k)</label>
                  <select value={exhaustiveGroupSize} onChange={e=>setExhaustiveGroupSize(Number(e.target.value))} disabled={exhaustiveRunning}
                    className="bg-surface-3 rounded-lg px-3 py-1.5 text-[12px] text-zinc-300 border border-border">
                    <option value={2}>2 因子</option>
                    <option value={3}>3 因子</option>
                    <option value={4}>4 因子</option>
                    <option value={5}>5 因子</option>
                  </select>
                </div>
                <div>
                  <label className="text-[10px] text-zinc-500 block mb-1">最大组合数</label>
                  <select value={exhaustiveMaxCombos} onChange={e=>setExhaustiveMaxCombos(Number(e.target.value))} disabled={exhaustiveRunning}
                    className="bg-surface-3 rounded-lg px-3 py-1.5 text-[12px] text-zinc-300 border border-border">
                    <option value={20}>20</option>
                    <option value={50}>50</option>
                    <option value={100}>100</option>
                    <option value={200}>200</option>
                    <option value={500}>500</option>
                  </select>
                </div>
              </div>

              {exhaustiveProgress && (
                <div className="mb-4">
                  <div className="flex items-center justify-between text-[11px] text-zinc-400 mb-1">
                    <span>进度: {exhaustiveProgress.current || 0} / {exhaustiveProgress.total}</span>
                    <span>{exhaustiveProgress.candidates} 个候选因子</span>
                    {exhaustiveProgress.names && <span className="text-zinc-600 truncate max-w-[200px]">当前: {exhaustiveProgress.names.join(' + ')}</span>}
                  </div>
                  <div className="w-full bg-surface-3 rounded-full h-2">
                    <div className="bg-brand-500 h-2 rounded-full transition-all" style={{width: `${((exhaustiveProgress.current||0)/(exhaustiveProgress.total||1))*100}%`}}></div>
                  </div>
                  {exhaustiveProgress.done && (
                    <div className="mt-2 text-[11px] text-emerald-400">
                      完成! 测试 {exhaustiveProgress.current} 组合, {exhaustiveProgress.accepted || 0} 个被采纳
                      {exhaustiveProgress.best && ` | 最佳 Sharpe: ${(exhaustiveProgress.best.metrics?.sharpe||0).toFixed(2)}`}
                    </div>
                  )}
                </div>
              )}
            </Card>

            {exhaustiveResults.length > 0 && (
              <Card>
                <h4 className="text-[12px] font-semibold text-zinc-300 mb-2">组合结果 ({exhaustiveResults.length})</h4>
                <div className="overflow-x-auto max-h-[400px] overflow-y-auto overflow-x-hidden">
                  <table className="w-full min-w-0 text-[10px] md:text-[11px]">
                    <thead><tr className="text-zinc-500 border-b border-border/30 sticky top-0 bg-surface-1">
                      <th className="py-1.5 px-2 text-left">#</th>
                      <th className="py-1.5 px-2 text-left">因子组合</th>
                      <th className="py-1.5 px-2 text-right">Sharpe</th>
                      <th className="py-1.5 px-2 text-right">IR</th>
                      <th className="py-1.5 px-2 text-right">IC</th>
                      <th className="py-1.5 px-2 text-right">WR</th>
                      <th className="py-1.5 px-2 text-center">结论</th>
                    </tr></thead>
                    <tbody>
                      {exhaustiveResults.map((r,i) => (
                        <tr key={i} className={`border-b border-border/10 ${r.verdict==='accept'?'bg-emerald-600/5':r.verdict==='reject'?'':'bg-amber-600/5'}`}>
                          <td className="py-1.5 px-2 text-zinc-600">{r.idx}</td>
                          <td className="py-1.5 px-2 text-zinc-300 max-w-[250px] truncate">{r.names.join(' + ')}</td>
                          <td className={`py-1.5 px-2 text-right font-mono ${(r.metrics?.sharpe||0)>=1?'text-emerald-400':'text-zinc-400'}`}>{(r.metrics?.sharpe||0).toFixed(2)}</td>
                          <td className="py-1.5 px-2 text-right font-mono text-zinc-400">{(r.metrics?.ir||0).toFixed(2)}</td>
                          <td className="py-1.5 px-2 text-right font-mono text-zinc-400">{(r.metrics?.ic_mean||0).toFixed(4)}</td>
                          <td className="py-1.5 px-2 text-right font-mono text-zinc-400">{((r.metrics?.win_rate||0)*100).toFixed(1)}%</td>
                          <td className="py-1.5 px-2 text-center">
                            <span className={`px-1.5 py-0.5 rounded text-[10px] ${r.verdict==='accept'?'bg-emerald-600/20 text-emerald-400':r.verdict==='reject'?'bg-red-600/20 text-red-400':'bg-amber-600/20 text-amber-400'}`}>
                              {r.verdict==='accept'?'采纳':r.verdict==='reject'?'拒绝':'边缘'}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </>)}

          {/* === n8n 管线 Sub-tab === */}
          {diggerSubTab === 'pipeline' && (<>
            <Card className="glow-brand border-brand-500/10">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-zinc-300">🔗 n8n 自动化管线</h3>
                <button onClick={loadPipelineStatus} disabled={pipelineLoading}
                  className="btn px-3 py-1.5 bg-surface-3 hover:bg-surface-4 text-zinc-400 text-[11px] rounded-lg border border-border transition">
                  {pipelineLoading ? <Spinner size={3}/> : '刷新状态'}
                </button>
              </div>
              <p className="text-[11px] text-zinc-500 mb-4">通过 n8n workflow 自动化因子管线: 定时挖掘 → 自动融合 → 策略化 → 推送通知。</p>

              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">
                <div className="bg-surface-3/50 rounded-xl p-4 border border-border/30">
                  <div className="text-[11px] text-zinc-500 mb-2">⛏️ 触发挖掘</div>
                  <p className="text-[10px] text-zinc-600 mb-3">启动 N 轮因子挖掘 (LLM 生成 → 沙箱评估 → 因子库录入)</p>
                  <div className="flex gap-2">
                    <button onClick={()=>triggerN8nMine(3)} className="btn px-3 py-1.5 bg-brand-600 hover:bg-brand-500 text-white text-[11px] rounded-lg transition">3 轮</button>
                    <button onClick={()=>triggerN8nMine(5)} className="btn px-3 py-1.5 bg-brand-600 hover:bg-brand-500 text-white text-[11px] rounded-lg transition">5 轮</button>
                    <button onClick={()=>triggerN8nMine(10)} className="btn px-3 py-1.5 bg-brand-600/70 hover:bg-brand-500 text-white text-[11px] rounded-lg transition">10 轮</button>
                  </div>
                </div>
                <div className="bg-surface-3/50 rounded-xl p-4 border border-border/30">
                  <div className="text-[11px] text-zinc-500 mb-2">🔮 触发穷举融合</div>
                  <p className="text-[10px] text-zinc-600 mb-3">后台运行 2 因子穷举组合 (最多 50 组)</p>
                  <button onClick={triggerN8nCombineAll} className="btn px-4 py-1.5 bg-amber-600 hover:bg-amber-500 text-white text-[11px] rounded-lg transition">启动融合</button>
                </div>
                <div className="bg-surface-3/50 rounded-xl p-4 border border-border/30">
                  <div className="text-[11px] text-zinc-500 mb-2">📡 n8n Webhook</div>
                  <p className="text-[10px] text-zinc-600 mb-2">在 n8n 中配置以下端点:</p>
                  <div className="space-y-1 text-[10px] font-mono">
                    <div className="bg-surface-2 rounded px-2 py-1 text-zinc-400">POST /api/n8n/trigger/mine</div>
                    <div className="bg-surface-2 rounded px-2 py-1 text-zinc-400">POST /api/n8n/trigger/combine-all</div>
                    <div className="bg-surface-2 rounded px-2 py-1 text-zinc-400">GET /api/n8n/events</div>
                    <div className="bg-surface-2 rounded px-2 py-1 text-zinc-400">GET /api/n8n/pipeline/status</div>
                  </div>
                </div>
              </div>

              {pipelineStatus && (
                <div className="space-y-3">
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                    <div className="bg-surface-3/50 rounded-lg px-3 py-2 border border-border/30">
                      <div className="text-[10px] text-zinc-500">活跃因子</div>
                      <div className="text-sm font-semibold text-emerald-400">{pipelineStatus.factor_stats?.active_count || 0}</div>
                    </div>
                    <div className="bg-surface-3/50 rounded-lg px-3 py-2 border border-border/30">
                      <div className="text-[10px] text-zinc-500">最佳 Sharpe</div>
                      <div className="text-sm font-semibold text-brand-400">{(pipelineStatus.factor_stats?.best_sharpe||0).toFixed(2)}</div>
                    </div>
                    <div className="bg-surface-3/50 rounded-lg px-3 py-2 border border-border/30">
                      <div className="text-[10px] text-zinc-500">挖掘状态</div>
                      <div className={`text-sm font-semibold ${pipelineStatus.digger_running?'text-amber-400':'text-zinc-500'}`}>{pipelineStatus.digger_running?'运行中':'空闲'}</div>
                    </div>
                    <div className="bg-surface-3/50 rounded-lg px-3 py-2 border border-border/30">
                      <div className="text-[10px] text-zinc-500">最近事件</div>
                      <div className="text-sm font-semibold text-zinc-400">{pipelineStatus.recent_events?.length || 0}</div>
                    </div>
                  </div>
                  {pipelineStatus.recent_events?.length > 0 && (
                    <div>
                      <h4 className="text-[11px] text-zinc-500 mb-1">最近管线事件</h4>
                      <div className="space-y-1">
                        {pipelineStatus.recent_events.map((evt,i) => (
                          <div key={i} className="bg-surface-3/30 rounded-lg px-3 py-1.5 text-[11px] flex items-center gap-2 border border-border/20">
                            <span className="text-zinc-600">{evt.timestamp ? new Date(evt.timestamp*1000).toLocaleTimeString() : ''}</span>
                            <span className={`px-1.5 py-0.5 rounded text-[10px] ${evt.type==='factor_mined'?'bg-emerald-600/20 text-emerald-400':evt.type==='mine_session_done'?'bg-brand-600/20 text-brand-400':'bg-amber-600/20 text-amber-400'}`}>{evt.type}</span>
                            {evt.factor_id && <span className="text-zinc-400 font-mono">{evt.factor_id.slice(-8)}</span>}
                            {evt.tested != null && <span className="text-zinc-400">测试 {evt.tested}, 通过 {evt.accepted}</span>}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
              {!pipelineStatus && !pipelineLoading && <div className="text-center py-6 text-zinc-600 text-sm">点击「刷新状态」查看管线概况</div>}
            </Card>

            <Card>
              <h4 className="text-[12px] font-semibold text-zinc-300 mb-3">📋 n8n 推荐 Workflow 配置</h4>
              <div className="space-y-3 text-[11px] text-zinc-400">
                <div className="bg-surface-3/30 rounded-xl p-3 border border-border/20">
                  <div className="text-zinc-200 font-medium mb-1">Workflow 1: 定时挖掘</div>
                  <div className="text-[10px] text-zinc-500">Schedule Trigger (每日 10:00, 14:00) → HTTP POST /api/n8n/trigger/mine → Wait 30min → GET /api/n8n/pipeline/status → IF new_factors → 飞书/Telegram 通知</div>
                </div>
                <div className="bg-surface-3/30 rounded-xl p-3 border border-border/20">
                  <div className="text-zinc-200 font-medium mb-1">Workflow 2: 自动穷举融合</div>
                  <div className="text-[10px] text-zinc-500">Polling Trigger (GET /api/n8n/events, 每 5min) → IF type=mine_session_done → POST /api/n8n/trigger/combine-all → Wait → GET /api/n8n/events → 通知融合结果</div>
                </div>
                <div className="bg-surface-3/30 rounded-xl p-3 border border-border/20">
                  <div className="text-zinc-200 font-medium mb-1">Workflow 3: 每周策略化</div>
                  <div className="text-[10px] text-zinc-500">Schedule Trigger (周五 16:00) → POST /api/digger/analyze → 取 top cluster 代表 → POST /api/digger/to-strategy → 推送策略结果</div>
                </div>
              </div>
            </Card>
          </>)}

          {/* === 融合记录 Sub-tab === */}
          {diggerSubTab === 'combine_history' && (<>
            <Card className="glow-brand border-brand-500/10">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-semibold text-zinc-300">🔮 融合历史记录</h3>
                <button onClick={loadCombineHistory} disabled={combineHistoryLoading}
                  className="btn px-3 py-1.5 bg-surface-3 hover:bg-surface-4 text-zinc-400 text-[11px] rounded-lg border border-border transition">
                  {combineHistoryLoading ? <Spinner size={3}/> : '刷新'}
                </button>
              </div>
              <p className="text-[11px] text-zinc-500 mb-4">每次融合操作都会生成完整记录，包含输入因子、融合结果、质量评估和最终裁决（采纳/回退）</p>
              {combineHistory.length === 0 && !combineHistoryLoading && (
                <div className="text-center py-8 text-zinc-500 text-[12px]">暂无融合记录 — 在因子库中选择因子后点击融合即可</div>
              )}
              {combineHistory.length > 0 && (
                <div className="space-y-2">
                  {combineHistory.map((rec, idx) => {
                    const v = rec.verdict || rec.status || 'unknown';
                    const vColor = v === 'accept' || v === 'accepted' ? 'text-emerald-400 bg-emerald-500/10 border-emerald-500/20' :
                                   v === 'reject' || v === 'rejected' ? 'text-red-400 bg-red-500/10 border-red-500/20' :
                                   'text-amber-400 bg-amber-500/10 border-amber-500/20';
                    const vLabel = v === 'accept' || v === 'accepted' ? 'ACCEPT 采纳' :
                                   v === 'reject' || v === 'rejected' ? 'REJECT 回退' : 'MARGINAL 边缘';
                    const ts = rec.created_at ? new Date(rec.created_at * 1000).toLocaleString('zh-CN') : '';
                    const nFactors = rec.input_factors?.length || rec.input_factor_ids?.length || '?';
                    const cmb = rec.combined_metrics || {};
                    return (
                      <div key={rec.id || idx}
                        onClick={() => loadCombineDetail(rec.id)}
                        className={`p-3 rounded-xl border cursor-pointer transition hover:bg-surface-3/50 ${selectedCombineRecord?.id === rec.id ? 'bg-surface-3/60 border-brand-500/30' : 'bg-surface-2/50 border-border/30'}`}>
                        <div className="flex items-center justify-between mb-1.5">
                          <div className="flex items-center gap-2">
                            <span className={`px-2 py-0.5 text-[10px] font-bold rounded-full border ${vColor}`}>{vLabel}</span>
                            <span className="text-[11px] text-zinc-400">{nFactors} 因子</span>
                          </div>
                          <span className="text-[10px] text-zinc-600">{ts}</span>
                        </div>
                        <div className="flex gap-3 text-[10px] text-zinc-500">
                          {cmb.sharpe != null && <span>Sharpe: <b className="text-zinc-300">{Number(cmb.sharpe).toFixed(3)}</b></span>}
                          {cmb.ir != null && <span>IR: <b className="text-zinc-300">{Number(cmb.ir).toFixed(3)}</b></span>}
                          {cmb.win_rate != null && <span>Win: <b className="text-zinc-300">{(Number(cmb.win_rate)*100).toFixed(1)}%</b></span>}
                          {cmb.max_drawdown != null && <span>DD: <b className="text-zinc-300">{(Number(cmb.max_drawdown)*100).toFixed(2)}%</b></span>}
                        </div>
                        {rec.evaluation?.improvements && (
                          <div className="mt-1 flex gap-3 text-[10px]">
                            {rec.evaluation.improvements.sharpe_vs_best != null && (
                              <span className={rec.evaluation.improvements.sharpe_vs_best > 0 ? 'text-emerald-400' : 'text-red-400'}>
                                vs最佳: {rec.evaluation.improvements.sharpe_vs_best > 0 ? '+' : ''}{Number(rec.evaluation.improvements.sharpe_vs_best).toFixed(3)}
                              </span>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </Card>

            {/* 融合详情面板 */}
            {combineDetailLoading && <div className="text-center py-4"><Spinner size={5}/></div>}
            {selectedCombineRecord && !combineDetailLoading && (() => {
              const rec = selectedCombineRecord;
              const v = rec.verdict || rec.status || 'unknown';
              const vColor = v === 'accept' || v === 'accepted' ? 'border-emerald-500/30' :
                             v === 'reject' || v === 'rejected' ? 'border-red-500/30' : 'border-amber-500/30';
              return (
                <Card className={`${vColor}`}>
                  <div className="flex items-center justify-between mb-3">
                    <h4 className="text-[13px] font-semibold text-zinc-300">融合详情 — {rec.id}</h4>
                    <button onClick={() => setSelectedCombineRecord(null)} className="text-zinc-500 hover:text-zinc-300 text-xs">关闭</button>
                  </div>

                  {/* 评估报告 */}
                  {rec.evaluation?.report && (
                    <div className="bg-surface-3/30 rounded-xl p-4 mb-4 border border-border/20">
                      <h5 className="text-[11px] font-semibold text-zinc-400 mb-2">质量评估报告</h5>
                      <pre className="text-[11px] leading-relaxed text-zinc-300 font-mono whitespace-pre-wrap">{rec.evaluation.report}</pre>
                    </div>
                  )}

                  {/* 指标改善对比 */}
                  {rec.evaluation?.improvements && (
                    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-4">
                      {[
                        {k:'combined_sharpe', label:'融合后 Sharpe'},
                        {k:'best_single_sharpe', label:'最佳单因子 Sharpe'},
                        {k:'avg_single_sharpe', label:'单因子平均 Sharpe'},
                        {k:'sharpe_vs_best', label:'Sharpe vs 最佳', delta:true},
                        {k:'sharpe_vs_avg', label:'Sharpe vs 平均', delta:true},
                        {k:'ir_vs_best', label:'IR vs 最佳', delta:true},
                      ].map(({k, label, delta}) => {
                        const val = rec.evaluation.improvements[k];
                        if (val == null) return null;
                        return (
                          <div key={k} className="bg-surface-2/50 rounded-lg p-2.5 border border-border/20">
                            <div className="text-[10px] text-zinc-500 mb-1">{label}</div>
                            <div className={`text-[13px] font-bold ${delta ? (val > 0 ? 'text-emerald-400' : val < 0 ? 'text-red-400' : 'text-zinc-400') : 'text-zinc-200'}`}>
                              {delta && val > 0 ? '+' : ''}{Number(val).toFixed(3)}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}

                  {/* 输入因子列表 */}
                  {rec.input_factors && rec.input_factors.length > 0 && (
                    <div className="mb-4">
                      <h5 className="text-[11px] font-semibold text-zinc-400 mb-2">输入因子 ({rec.input_factors.length})</h5>
                      <div className="overflow-x-auto">
                        <table className="w-full min-w-0 text-[10px]">
                          <thead><tr className="text-zinc-500 border-b border-border/20">
                            <th className="text-left py-1 px-2">ID</th><th className="text-left py-1 px-2">主题</th>
                            <th className="text-right py-1 px-2">Sharpe</th><th className="text-right py-1 px-2">IR</th>
                            <th className="text-right py-1 px-2">Win%</th>
                          </tr></thead>
                          <tbody>
                            {rec.input_factors.map((f, i) => (
                              <tr key={f.id||i} className="border-b border-border/10 text-zinc-400">
                                <td className="py-1 px-2 font-mono text-[9px]">{(f.id||'').slice(-10)}</td>
                                <td className="py-1 px-2">{f.theme}</td>
                                <td className="py-1 px-2 text-right">{Number(f.sharpe||0).toFixed(3)}</td>
                                <td className="py-1 px-2 text-right">{Number(f.ir||0).toFixed(3)}</td>
                                <td className="py-1 px-2 text-right">{(Number(f.win_rate||0)*100).toFixed(1)}%</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* 融合后代码预览 */}
                  {rec.combined_code_preview && (
                    <div>
                      <h5 className="text-[11px] font-semibold text-zinc-400 mb-2">融合代码 (前2000字符)</h5>
                      <div className="bg-[#0d1117] rounded-xl p-3 border border-border/30 overflow-x-auto max-h-[300px] overflow-y-auto overflow-x-hidden">
                        <pre className="text-[11px] leading-relaxed text-emerald-300/80 font-mono whitespace-pre">{rec.combined_code_preview}</pre>
                      </div>
                    </div>
                  )}

                  {/* 原始返回 */}
                  {rec.result_raw && (
                    <details className="mt-3">
                      <summary className="text-[10px] text-zinc-500 cursor-pointer hover:text-zinc-400">展开原始结果</summary>
                      <div className="bg-[#0d1117] rounded-xl p-3 mt-1 border border-border/30 overflow-x-auto max-h-[200px] overflow-y-auto overflow-x-hidden">
                        <pre className="text-[10px] text-zinc-500 font-mono whitespace-pre-wrap">{rec.result_raw}</pre>
                      </div>
                    </details>
                  )}
                </Card>
              );
            })()}
          </>)}

          {/* === 启动挖掘 Sub-tab === */}
          {diggerSubTab === 'mine' && (
            <Card className="glow-brand border-brand-500/10">
              <h3 className="text-sm font-semibold text-zinc-300 mb-1">⛏️ Alpha Digger — A股实战因子挖掘</h3>
              <p className="text-[11px] text-zinc-500 mb-2">围绕A股独特规律自主探索: 涨停连板/资金流向/板块轮动/情绪周期/竞价博弈/筹码结构/突破质量/聪明钱背离等</p>
              <p className="text-[11px] text-zinc-600 mb-4">LLM 生成因子代码 → 139沙箱回测 → CSCV过拟合检验 → 质量评审入库</p>
              <div className="flex items-end gap-3">
                <div>
                  <label className="block text-[10px] text-zinc-500 mb-1">挖掘轮数</label>
                  <input type="number" min="1" max="50" value={diggerRounds} onChange={e => setDiggerRounds(Number(e.target.value) || 5)}
                    className="w-20 bg-surface-3 border border-border rounded-xl px-3 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 transition text-center" />
                </div>
                <div>
                  <label className="block text-[10px] text-zinc-500 mb-1">每轮因子数</label>
                  <input type="number" min="1" max="20" value={diggerFactors} onChange={e => setDiggerFactors(Number(e.target.value) || 5)}
                    className="w-20 bg-surface-3 border border-border rounded-xl px-3 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 transition text-center" />
                </div>
                <div className="flex-1"></div>
                <div className="text-[11px] text-zinc-600 text-right">
                  预计生成 <span className="text-zinc-400 font-medium">{diggerRounds * diggerFactors}</span> 个因子
                </div>
                <button onClick={()=>{startDigger();setDiggerSubTab('logs');}} disabled={diggerRunning}
                  className="btn px-6 py-2.5 bg-brand-600 hover:bg-brand-500 text-white text-sm font-semibold rounded-xl transition shadow-lg shadow-brand-600/20 disabled:opacity-50">
                  {diggerRunning ? <span className="flex items-center gap-2"><Spinner size={4} /> 挖掘中...</span> : '启动挖掘'}
                </button>
              </div>
            </Card>
          )}

          {/* === 挖掘日志 Sub-tab === */}
          {diggerSubTab === 'logs' && (<>
            {diggerLogs.length > 0 ? (
              <Card>
                <div className="flex items-center justify-between mb-3">
                  <h3 className="text-sm font-semibold text-zinc-300">📋 挖掘日志 <span className="text-zinc-600 font-normal">({diggerLogs.length} 条)</span></h3>
                  {!diggerRunning && <button onClick={() => { setDiggerLogs([]); localStorage.removeItem('rragent_digger_logs'); }} className="btn text-[11px] text-zinc-500 hover:text-zinc-300 px-2 py-1 rounded-lg transition">清空</button>}
                </div>
                <div className="space-y-1.5 max-h-[600px] overflow-y-auto overflow-x-hidden">
                  {diggerLogs.map((log, i) => (
                    <div key={i} className={`text-[12px] leading-relaxed px-3 py-2 rounded-lg border-l-2 transition-all
                      ${log.type === 'success' ? 'border-emerald-500/40 bg-emerald-600/5 text-emerald-300' :
                        log.type === 'error' ? 'border-red-500/40 bg-red-600/5 text-red-300' :
                        'border-brand-500/20 bg-surface-3/30 text-zinc-400'}`}>
                      <span className="text-zinc-600 text-[10px] mr-2">{new Date(log.ts).toLocaleTimeString()}</span>
                      <span className="whitespace-pre-wrap">{log.text}</span>
                    </div>
                  ))}
                  {diggerRunning && (
                    <div className="flex items-center gap-2 px-3 py-2 text-brand-400 text-[12px] animate-pulse">
                      <Spinner size={3} /><span>等待下一条消息...</span>
                    </div>
                  )}
                </div>
              </Card>
            ) : (
              <Card><div className="text-center py-10 text-zinc-600">
                <div className="text-3xl mb-2">📋</div>
                <div className="text-sm text-zinc-500">暂无日志。去「启动挖掘」tab 开始。</div>
              </div></Card>
            )}
          </>)}
        </div>
      )}

      {tab === 'meme' && (
        <MemeView />
      )}

      {tab === 'ledger' && (<>
        {!ledger && !ledgerLoading && <div className="text-center py-2"><button onClick={()=>{loadLedger();loadCache();}} className="btn px-4 py-2 bg-surface-2 hover:bg-surface-3 rounded-xl text-sm text-zinc-300 border border-border transition">加载账本数据</button></div>}
        <div className="grid md:grid-cols-2 gap-4">
          <Card>
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-semibold text-zinc-300">📝 决策账本</h3>
              <button onClick={loadLedger} className="btn text-[12px] text-brand-400 hover:text-brand-300 px-2 py-1 rounded-lg transition">{ledgerLoading ? <Spinner /> : '刷新'}</button>
            </div>
            <DataBlock data={ledger} loading={ledgerLoading} placeholder="暂无策略记录" />
          </Card>
          <Card>
            <h3 className="text-sm font-semibold text-zinc-300 mb-2">📦 数据缓存</h3>
            <DataBlock data={cache} placeholder="暂无缓存" />
          </Card>
        </div>
      </>)}
    </div>
  );
}

function LLMConfigPanel() {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [selProvider, setSelProvider] = useState('');
  const [selModel, setSelModel] = useState('');
  const toast = useContext(ToastContext);
  const loadConfig = async () => { setLoading(true); const data = await apiGet('/api/llm/config'); if (data && !data.error) { setConfig(data); if (data.current) { setSelProvider(data.current.provider || ''); setSelModel(data.current.model || ''); } } setLoading(false); };
  useEffect(() => { loadConfig(); }, []);
  const handleProviderChange = (p) => { setSelProvider(p); const models = config?.providers?.[p]?.models || []; setSelModel(models[0] || ''); };
  const handleSave = async () => { setSaving(true); const r = await apiPost('/api/llm/config', {provider: selProvider, model: selModel}); setSaving(false); if (r.ok) { toast(r.msg, 'success'); loadConfig(); } else toast(r.msg || '设置失败', 'error'); };
  const handleAuto = async () => { setSaving(true); const r = await apiPost('/api/llm/config', {provider: '', model: ''}); setSaving(false); setSelProvider(''); setSelModel(''); if (r.ok) { toast(r.msg, 'success'); loadConfig(); } };
  if (loading) return <Card><LoadingBlock text="加载模型配置..." /></Card>;
  if (!config) return null;
  const providers = config.providers || {};
  const meta = config.model_meta || {};
  const currentModels = selProvider ? (providers[selProvider]?.models || []) : [];
  const hasKey = selProvider ? providers[selProvider]?.has_key : false;
  const selMeta = selModel ? meta[selModel] : null;
  const fmtCtx = (n) => { if (!n) return ''; if (n >= 1000000) return (n/1000000).toFixed(0) + 'M'; return Math.round(n/1000) + 'K'; };
  const tagColor = (t) => {
    if (t === '推荐') return 'bg-brand-600/20 text-brand-400 border-brand-500/30';
    if (t === '图片理解') return 'bg-violet-500/15 text-violet-400 border-violet-500/25';
    if (t === '思考') return 'bg-cyan-500/15 text-cyan-400 border-cyan-500/25';
    if (t === '代码专精') return 'bg-green-500/15 text-green-400 border-green-500/25';
    if (t === '深度分析' || t === '深度推理') return 'bg-amber-500/15 text-amber-400 border-amber-500/25';
    if (t === '高性价比') return 'bg-emerald-500/15 text-emerald-400 border-emerald-500/25';
    return 'bg-surface-3 text-zinc-500 border-border';
  };
  return (
    <Card className="glow-brand border-brand-500/10">
      <div className="flex items-center justify-between mb-4"><div className="flex items-center gap-2"><span className="text-lg">🤖</span><h3 className="text-sm font-semibold text-white">LLM 模型配置</h3></div>{config.current && (<span className="text-[11px] px-2 py-1 bg-brand-600/20 text-brand-400 rounded-lg font-medium">当前: {config.current.provider}/{config.current.model}</span>)}{!config.current && (<span className="text-[11px] px-2 py-1 bg-surface-3 text-zinc-500 rounded-lg">自动路由</span>)}</div>
      <div className="grid grid-cols-[1fr_1fr_auto_auto] gap-3 items-end">
        <div><label className="text-[11px] text-zinc-500 uppercase tracking-wider block mb-1.5">Provider</label><select value={selProvider} onChange={e=>handleProviderChange(e.target.value)} className="w-full bg-surface-3 border border-border rounded-xl px-3 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 transition cursor-pointer"><option value="">自动路由</option>{Object.entries(providers).map(([name, info]) => (<option key={name} value={name} disabled={!info.has_key}>{name} {info.has_key ? '' : '(无 Key)'}</option>))}</select></div>
        <div><label className="text-[11px] text-zinc-500 uppercase tracking-wider block mb-1.5">Model</label><select value={selModel} onChange={e=>setSelModel(e.target.value)} disabled={!selProvider} className="w-full bg-surface-3 border border-border rounded-xl px-3 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 transition cursor-pointer disabled:opacity-40">{currentModels.map(m => { const mm = meta[m]; return <option key={m} value={m}>{mm?.label || m}</option>; })}{currentModels.length === 0 && <option value="">--</option>}</select></div>
        <button onClick={handleSave} disabled={saving || (!selProvider)} className="btn px-4 py-2.5 bg-brand-600 hover:bg-brand-700 rounded-xl text-sm text-white font-medium transition shadow-lg shadow-brand-600/20 disabled:opacity-40">{saving ? <Spinner /> : '应用'}</button>
        <button onClick={handleAuto} disabled={saving} className="btn px-4 py-2.5 bg-surface-3 hover:bg-surface-4 rounded-xl text-sm text-zinc-300 border border-border hover:border-border-light transition disabled:opacity-40">自动</button>
      </div>
      {selMeta && (
        <div className="mt-3 flex items-center gap-2 flex-wrap">
          {selMeta.tags && selMeta.tags.map(t => (<span key={t} className={`text-[10px] px-1.5 py-0.5 rounded border font-medium ${tagColor(t)}`}>{t}</span>))}
          {selMeta.ctx && (<span className="text-[10px] text-zinc-500 ml-1">上下文 {fmtCtx(selMeta.ctx)}</span>)}
          {selMeta.out && (<span className="text-[10px] text-zinc-600">/ 输出 {fmtCtx(selMeta.out)}</span>)}
        </div>
      )}
      {selProvider && !hasKey && (<div className="mt-3 text-[12px] text-yellow-400/80 bg-yellow-500/10 rounded-lg px-3 py-2 border border-yellow-500/20">⚠️ {selProvider} 未配置 API Key</div>)}
      {selProvider && currentModels.length > 0 && (
        <div className="mt-4 pt-3 border-t border-border/50">
          <div className="text-[11px] text-zinc-600 mb-2">{selProvider} 可用模型:</div>
          <div className="grid gap-1.5">{currentModels.map(m => {
            const mm = meta[m];
            const isActive = m === selModel;
            return (
              <button key={m} onClick={() => setSelModel(m)} className={`flex items-center gap-2 px-3 py-2 rounded-lg text-left transition text-[12px] border ${isActive ? 'bg-brand-600/15 border-brand-500/30 text-white' : 'bg-surface-2 border-border hover:border-border-light text-zinc-400 hover:text-zinc-200'}`}>
                <span className="font-medium min-w-[120px]">{mm?.label || m}</span>
                <span className="flex gap-1 flex-wrap flex-1">{(mm?.tags||[]).map(t => (<span key={t} className={`text-[9px] px-1 py-0 rounded border ${tagColor(t)}`}>{t}</span>))}</span>
                {mm?.ctx && <span className="text-[10px] text-zinc-600 whitespace-nowrap">{fmtCtx(mm.ctx)}</span>}
              </button>
            );
          })}</div>
        </div>
      )}
      {!selProvider && (
        <div className="mt-4 pt-3 border-t border-border/50"><div className="text-[11px] text-zinc-600 mb-2">可用 Providers:</div><div className="flex flex-wrap gap-1.5">{Object.entries(providers).map(([name, info]) => (<span key={name} className={`text-[11px] px-2 py-1 rounded-lg border ${info.has_key ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-surface-3 border-border text-zinc-600'}`}>{name} · {info.models.length} models {info.has_key ? '✓' : ''}</span>))}</div></div>
      )}
    </Card>
  );
}

function DailyLogView() {
  const [selectedDate, setSelectedDate] = useState('');
  const [dates, setDates] = useState([]);
  const [log, setLog] = useState(null);
  const [loading, setLoading] = useState(false);
  const toast = useContext(ToastContext);
  const loadDates = async () => { try { const r = await apiGet('/api/daily-log/dates'); if (r.dates) setDates(r.dates); } catch(e) {} };
  const loadLog = async (date) => { setLoading(true); setSelectedDate(date); try { const r = await apiGet('/api/daily-log?date=' + date); if (!r.error) setLog(r); else toast('加载失败', 'error'); } catch(e) { toast('网络错误', 'error'); } setLoading(false); };
  useEffect(() => { loadDates(); loadLog(new Date().toISOString().slice(0, 10)); }, []);
  const fmtTime = (ts) => { if (!ts) return ''; return new Date(ts * 1000).toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit', second:'2-digit'}); };
  const roleIcon = (role) => ({user:'👤', assistant:'🤖', command:'⌨️', result:'📋'}[role] || '💬');
  const roleColor = (role) => ({user:'text-blue-400', assistant:'text-green-400', command:'text-yellow-400', result:'text-zinc-400'}[role] || 'text-zinc-300');

  return (
    <div className="flex-1 overflow-y-auto overflow-x-hidden p-6 animate-fade-in">
      <div className="flex items-center justify-between mb-4"><h1 className="text-xl font-bold text-white">每日工作日志</h1><button onClick={loadDates} className="btn text-[12px] text-brand-400 hover:text-brand-300 px-3 py-1.5 rounded-lg transition">刷新日期</button></div>
      <div className="flex gap-2 mb-4 flex-wrap">{dates.length === 0 && <span className="text-[12px] text-zinc-500">暂无日志记录</span>}{dates.map(d => (<button key={d} onClick={() => loadLog(d)} className={`btn px-3 py-1.5 rounded-xl text-[12px] border transition ${d === selectedDate ? 'bg-brand-600/20 text-brand-400 border-brand-500/30' : 'bg-surface-2 text-zinc-400 border-border hover:border-border-light'}`}>{d}</button>))}</div>
      {loading && (<Card><div className="flex items-center gap-2 py-4 justify-center"><Spinner /> <span className="text-zinc-500 text-sm">加载中...</span></div></Card>)}
      {!loading && log && (<div className="space-y-4">
        <div className="grid grid-cols-2 gap-3"><Card><div className="text-center"><div className="text-2xl font-bold text-brand-400">{log.chat_count || 0}</div><div className="text-[11px] text-zinc-500 mt-1">对话记录</div></div></Card><Card><div className="text-center"><div className="text-2xl font-bold text-emerald-400">{log.task_count || 0}</div><div className="text-[11px] text-zinc-500 mt-1">任务执行</div></div></Card></div>
        {log.tasks && log.tasks.length > 0 && (<Card><h3 className="text-sm font-semibold text-zinc-300 mb-3">📋 当日任务</h3><div className="space-y-2">{log.tasks.map((t, i) => { const icon = {completed:'✅', running:'🔄', failed:'❌', pending:'⏳', cancelled:'🚫'}[t.status] || '❓'; return (<div key={i} className="flex items-center gap-2 text-[13px] py-1.5 border-b border-border/50 last:border-0"><span>{icon}</span><span className="text-zinc-300 font-medium">{t.name || t.id}</span><span className="text-zinc-600 ml-auto">{t.progress != null ? t.progress + '%' : ''}</span></div>); })}</div></Card>)}
        {log.chats && log.chats.length > 0 && (<Card><h3 className="text-sm font-semibold text-zinc-300 mb-3">💬 对话与操作记录</h3><div className="space-y-1 max-h-[600px] overflow-y-auto overflow-x-hidden">{log.chats.map((c, i) => (<div key={i} className="flex gap-2 py-1.5 border-b border-border/30 last:border-0"><span className="text-[13px] flex-shrink-0">{roleIcon(c.role)}</span><div className="flex-1 min-w-0"><span className={`text-[12px] font-medium ${roleColor(c.role)}`}>{c.role}</span><span className="text-[11px] text-zinc-600 ml-2">{fmtTime(c.ts)}</span>{c.view && c.view !== 'chat' && <span className="text-[10px] text-zinc-700 ml-1 px-1.5 py-0.5 bg-surface-3 rounded">{c.view}</span>}<pre className="text-[12px] text-zinc-400 mt-0.5 whitespace-pre-wrap break-all leading-relaxed">{(c.content||'').slice(0, 500)}{(c.content||'').length > 500 ? '...' : ''}</pre></div></div>))}</div></Card>)}
        {(!log.chats || log.chats.length === 0) && (!log.tasks || log.tasks.length === 0) && (<Card><div className="text-center py-8 text-zinc-600 text-sm">当日无操作记录</div></Card>)}
      </div>)}
    </div>
  );
}
