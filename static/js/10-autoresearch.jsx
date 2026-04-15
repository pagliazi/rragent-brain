// ── AutoResearch View ────────────────────────────────

function AutoResearchView() {
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(false);
  const [triggerLoading, setTriggerLoading] = useState(false);
  const [model, setModel] = useState('sonnet');
  const [maxExperiments, setMaxExperiments] = useState(5);
  const [timeoutMin, setTimeoutMin] = useState(60);
  const toast = useContext(ToastContext);

  const MODEL_OPTIONS = [
    {value: 'sonnet', label: 'Claude Sonnet 4.6', provider: 'Anthropic'},
    {value: 'haiku', label: 'Claude Haiku 4.5', provider: 'Anthropic'},
    {value: 'opus', label: 'Claude Opus 4.6', provider: 'Anthropic'},
    {value: 'qwen3.5-plus', label: 'Qwen 3.5 Plus', provider: 'Bailian'},
  ];

  const fetchStatus = async () => {
    setLoading(true);
    try {
      const r = await apiGet('/api/n8n/autoresearch/status');
      setStatus(r);
    } catch(e) { toast('Failed to fetch status', 'error'); }
    setLoading(false);
  };

  useEffect(() => { fetchStatus(); }, []);

  const handleTrigger = async () => {
    setTriggerLoading(true);
    try {
      const r = await apiPost('/api/n8n/trigger/autoresearch', {
        max_experiments: maxExperiments,
        timeout_minutes: timeoutMin,
        model: model,
      });
      if (r.ok) {
        toast(r.message || 'Experiments started', 'success');
        setTimeout(fetchStatus, 2000);
      } else {
        toast(r.error || 'Failed to start', 'error');
      }
    } catch(e) { toast('Request failed', 'error'); }
    setTriggerLoading(false);
  };

  const bestBpb = status?.best_val_bpb;
  const latestBpb = status?.latest_val_bpb;
  const results = status?.results || [];
  const isRunning = status?.running;

  const keptResults = results.filter(r => r.status === 'keep');
  const discardedResults = results.filter(r => r.status === 'discard');
  const crashedResults = results.filter(r => r.status === 'crash');

  return (
    <div className="flex-1 overflow-y-auto overflow-x-hidden p-6 animate-fade-in">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-white">AutoResearch MLX</h1>
          <p className="text-sm text-zinc-500 mt-1">AI-driven autonomous LLM training experiments on Apple Silicon</p>
        </div>
        <button onClick={fetchStatus} disabled={loading}
          className="btn px-3 py-2 rounded-xl text-[13px] bg-surface-2 text-zinc-400 border border-border hover:border-border-light transition">
          {loading ? <Spinner size={4} /> : 'Refresh'}
        </button>
      </div>

      {/* Status Cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div className="glass rounded-2xl p-4 gradient-border">
          <div className="text-xs text-zinc-500 mb-1">Status</div>
          <div className={`text-lg font-bold ${isRunning ? 'text-emerald-400' : 'text-zinc-400'}`}>
            {isRunning ? 'Running' : 'Idle'}
          </div>
          {isRunning && status?.pid && <div className="text-[11px] text-zinc-600 mt-1">PID {status.pid}</div>}
        </div>
        <div className="glass rounded-2xl p-4 gradient-border">
          <div className="text-xs text-zinc-500 mb-1">Best val_bpb</div>
          <div className="text-lg font-bold text-brand-400">{bestBpb ? bestBpb.toFixed(6) : '--'}</div>
        </div>
        <div className="glass rounded-2xl p-4 gradient-border">
          <div className="text-xs text-zinc-500 mb-1">Latest val_bpb</div>
          <div className="text-lg font-bold text-zinc-200">{latestBpb ? latestBpb.toFixed(6) : '--'}</div>
        </div>
        <div className="glass rounded-2xl p-4 gradient-border">
          <div className="text-xs text-zinc-500 mb-1">Experiments</div>
          <div className="text-lg font-bold text-zinc-200">{results.length}</div>
          <div className="text-[11px] text-zinc-600 mt-1">
            {keptResults.length} kept / {discardedResults.length} discard / {crashedResults.length} crash
          </div>
        </div>
      </div>

      {/* Control Panel */}
      <div className="glass rounded-2xl p-5 gradient-border mb-6">
        <h2 className="text-sm font-semibold text-zinc-300 mb-4">Launch Experiment</h2>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 items-end">
          <div>
            <label className="block text-xs text-zinc-500 mb-1.5">Model</label>
            <select value={model} onChange={e => setModel(e.target.value)}
              className="w-full bg-surface-2 border border-border rounded-xl px-3 py-2.5 text-sm text-white focus:outline-none focus:border-brand-500/50 transition">
              {MODEL_OPTIONS.map(m => (
                <option key={m.value} value={m.value}>{m.label} ({m.provider})</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs text-zinc-500 mb-1.5">Experiments</label>
            <input type="number" min={1} max={20} value={maxExperiments} onChange={e => setMaxExperiments(parseInt(e.target.value) || 1)}
              className="w-full bg-surface-2 border border-border rounded-xl px-3 py-2.5 text-sm text-white focus:outline-none focus:border-brand-500/50 transition" />
          </div>
          <div>
            <label className="block text-xs text-zinc-500 mb-1.5">Timeout (min)</label>
            <input type="number" min={10} max={180} value={timeoutMin} onChange={e => setTimeoutMin(parseInt(e.target.value) || 60)}
              className="w-full bg-surface-2 border border-border rounded-xl px-3 py-2.5 text-sm text-white focus:outline-none focus:border-brand-500/50 transition" />
          </div>
          <div>
            <button onClick={handleTrigger} disabled={triggerLoading || isRunning}
              className="w-full py-2.5 bg-gradient-to-r from-brand-600 to-brand-500 hover:from-brand-500 hover:to-brand-400 text-white font-semibold rounded-xl transition-all duration-300 shadow-lg shadow-brand-600/25 disabled:opacity-50 text-sm">
              {triggerLoading ? <Spinner size={4} /> : isRunning ? 'Running...' : 'Start'}
            </button>
          </div>
        </div>
      </div>

      {/* Results Table */}
      {results.length > 0 && (
        <div className="glass rounded-2xl gradient-border overflow-hidden">
          <div className="px-5 py-3 border-b border-border">
            <h2 className="text-sm font-semibold text-zinc-300">Experiment Log</h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-zinc-500 border-b border-border/50">
                  <th className="px-4 py-2.5 text-left font-medium">#</th>
                  <th className="px-4 py-2.5 text-left font-medium">Commit</th>
                  <th className="px-4 py-2.5 text-left font-medium">val_bpb</th>
                  <th className="px-4 py-2.5 text-left font-medium">Memory</th>
                  <th className="px-4 py-2.5 text-left font-medium">Status</th>
                  <th className="px-4 py-2.5 text-left font-medium">Description</th>
                </tr>
              </thead>
              <tbody>
                {results.map((r, i) => {
                  const isBest = r.status === 'keep' && r.val_bpb && parseFloat(r.val_bpb) === bestBpb;
                  const statusColor = r.status === 'keep' ? 'text-emerald-400' : r.status === 'crash' ? 'text-red-400' : 'text-zinc-500';
                  const statusIcon = r.status === 'keep' ? 'check' : r.status === 'crash' ? 'x' : 'minus';
                  return (
                    <tr key={i} className={`border-b border-border/30 hover:bg-surface-2/50 transition ${isBest ? 'bg-brand-600/5' : ''}`}>
                      <td className="px-4 py-2.5 text-zinc-500">{i + 1}</td>
                      <td className="px-4 py-2.5"><code className="text-xs text-zinc-400 bg-surface-3 px-1.5 py-0.5 rounded">{r.commit}</code></td>
                      <td className={`px-4 py-2.5 font-mono ${isBest ? 'text-brand-400 font-bold' : 'text-zinc-300'}`}>
                        {r.val_bpb}{isBest ? ' *' : ''}
                      </td>
                      <td className="px-4 py-2.5 text-zinc-400">{r.memory_gb} GB</td>
                      <td className={`px-4 py-2.5 ${statusColor} font-medium`}>{r.status}</td>
                      <td className="px-4 py-2.5 text-zinc-400 max-w-[300px] truncate">{r.description}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Log Tail */}
      {status?.log_tail && (
        <div className="glass rounded-2xl gradient-border mt-6 overflow-hidden">
          <div className="px-5 py-3 border-b border-border">
            <h2 className="text-sm font-semibold text-zinc-300">Latest Log</h2>
          </div>
          <pre className="px-5 py-4 text-xs text-zinc-500 overflow-x-auto whitespace-pre-wrap font-mono leading-relaxed max-h-[300px] overflow-y-auto overflow-x-hidden">
            {status.log_tail}
          </pre>
        </div>
      )}
    </div>
  );
}
