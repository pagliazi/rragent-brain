// ── News View ───────────────────────────────────────

function NewsView() {
  const [items, setItems] = useState([]);
  const [summary, setSummary] = useState('');
  const [keyword, setKeyword] = useState('');
  const [loading, setLoading] = useState(false);
  const [sumLoading, setSumLoading] = useState(false);
  const toast = useContext(ToastContext);

  const loadNews = async (kw) => {
    setLoading(true);
    try {
      const q = kw ? '?keyword=' + encodeURIComponent(kw) : '';
      const r = await apiGet('/api/news' + q);
      setItems(r.items || []);
      if (r.raw && !r.items?.length) setSummary(r.raw);
    } catch(e) { toast('加载新闻失败','error'); }
    setLoading(false);
  };
  const loadSummary = async () => {
    setSumLoading(true);
    try {
      const r = await apiGet('/api/news/summary');
      setSummary(r.summary || '');
    } catch(e) { toast('生成摘要失败','error'); }
    setSumLoading(false);
  };

  useEffect(() => { loadNews(''); }, []);

  return (
    <div className="flex-1 overflow-y-auto overflow-x-hidden p-6 animate-fade-in">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold text-white">新闻资讯</h1>
        <div className="flex gap-2">
          <button onClick={loadSummary} disabled={sumLoading} className="btn px-3 py-1.5 bg-brand-600/20 text-brand-400 hover:bg-brand-600/30 rounded-lg text-[12px] transition disabled:opacity-50">{sumLoading ? 'AI 分析中...' : 'AI 摘要'}</button>
          <button onClick={() => loadNews(keyword)} className="btn px-3 py-1.5 bg-surface-2 hover:bg-surface-3 rounded-lg text-[12px] text-zinc-400 border border-border hover:border-border-light transition">刷新</button>
        </div>
      </div>
      <div className="flex gap-2 mb-4">
        <input value={keyword} onChange={e => setKeyword(e.target.value)} onKeyDown={e => e.key==='Enter' && loadNews(keyword)} placeholder="搜索关键词..." className="flex-1 bg-surface-2 border border-border rounded-xl px-4 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 transition placeholder-zinc-600" />
      </div>
      {summary && <Card><h3 className="text-sm font-semibold text-zinc-200 mb-2">AI 新闻摘要</h3><pre className="text-[13px] text-zinc-300 whitespace-pre-wrap leading-relaxed">{summary}</pre></Card>}
      {loading ? <Card><div className="flex items-center gap-2 py-8 justify-center"><Spinner /><span className="text-zinc-500 text-sm">加载中...</span></div></Card>
        : items.length > 0 ? (
          <div className="space-y-3">{items.map((item, i) => (
            <Card key={i}>
              <div className="flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <h3 className="text-sm font-medium text-zinc-200 mb-1">{item.title || '无标题'}</h3>
                  <p className="text-[12px] text-zinc-400 line-clamp-2">{item.summary || item.content || ''}</p>
                  <div className="flex items-center gap-3 mt-2 text-[11px] text-zinc-600">
                    {item.source && <span>{item.source}</span>}
                    {item.time && <span>{item.time}</span>}
                    {item.url && <a href={item.url} target="_blank" rel="noopener" className="text-brand-400 hover:text-brand-300 no-underline">查看原文</a>}
                  </div>
                </div>
              </div>
            </Card>
          ))}</div>
        ) : !summary && <Card><div className="text-center py-8 text-zinc-600 text-sm">暂无新闻数据，请尝试搜索或刷新</div></Card>
      }
    </div>
  );
}


// ── Tools View ──────────────────────────────────────

function ToolsView() {
  const tabs = [
    {id:'translate', label:'翻译', icon:'🌐'},
    {id:'write', label:'写作', icon:'✍️'},
    {id:'code', label:'代码', icon:'💻'},
    {id:'calc', label:'计算', icon:'🔢'},
    {id:'websearch', label:'搜索', icon:'🔍'},
  ];
  const [activeTab, setActiveTab] = useState('translate');
  const [input, setInput] = useState('');
  const [extra, setExtra] = useState('');
  const [result, setResult] = useState('');
  const [loading, setLoading] = useState(false);
  const toast = useContext(ToastContext);

  const placeholders = {translate:'输入要翻译的文本...', write:'描述写作需求...', code:'描述代码需求...', calc:'输入数学表达式或问题...', websearch:'输入搜索关键词...'};
  const extraLabels = {translate:'目标语言 (en/zh/ja)', write:'风格 (professional/casual/academic)', code:'编程语言 (python/js/go)'};

  const run = async () => {
    if (!input.trim()) return;
    setLoading(true); setResult('');
    try {
      const bodyMap = {
        translate: {text: input, target: extra || 'en'},
        write: {prompt: input, style: extra || 'professional'},
        code: {prompt: input, language: extra || 'python'},
        calc: {expression: input},
        websearch: {query: input},
      };
      const r = await apiPost('/api/tools/' + activeTab, bodyMap[activeTab]);
      setResult(r.result || r.error || JSON.stringify(r, null, 2));
    } catch(e) { toast('请求失败','error'); }
    setLoading(false);
  };

  return (
    <div className="flex-1 overflow-y-auto overflow-x-hidden p-6 animate-fade-in">
      <h1 className="text-xl font-bold text-white mb-4">AI 工具箱</h1>
      <div className="flex gap-2 mb-4 flex-wrap">
        {tabs.map(t => (
          <button key={t.id} onClick={() => { setActiveTab(t.id); setResult(''); }}
            className={`btn px-4 py-2 rounded-xl text-[13px] border transition ${activeTab===t.id ? 'bg-brand-600/20 text-brand-400 border-brand-500/30' : 'bg-surface-2 text-zinc-400 border-border hover:border-border-light'}`}>
            {t.icon} {t.label}
          </button>
        ))}
      </div>
      <Card>
        <div className="space-y-3">
          <textarea value={input} onChange={e => setInput(e.target.value)} placeholder={placeholders[activeTab]}
            rows={4} className="w-full bg-surface-3 border border-border rounded-xl px-4 py-3 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 resize-none transition placeholder-zinc-600" />
          {extraLabels[activeTab] && (
            <input value={extra} onChange={e => setExtra(e.target.value)} placeholder={extraLabels[activeTab]}
              className="w-full bg-surface-3 border border-border rounded-xl px-4 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 transition placeholder-zinc-600" />
          )}
          <button onClick={run} disabled={loading || !input.trim()} className="btn px-6 py-2.5 bg-brand-600 hover:bg-brand-500 text-white rounded-xl text-sm font-medium transition disabled:opacity-50">
            {loading ? <Spinner /> : '执行'}
          </button>
        </div>
        {result && <div className="mt-4 pt-4 border-t border-border"><pre className="text-[13px] text-zinc-300 whitespace-pre-wrap break-all leading-relaxed max-h-[500px] overflow-auto">{result}</pre></div>}
      </Card>
    </div>
  );
}


// ── Apple View ──────────────────────────────────────

function AppleView() {
  const [tab, setTab] = useState('calendar');
  const [calData, setCalData] = useState('');
  const [remData, setRemData] = useState('');
  const [noteData, setNoteData] = useState('');
  const [sysData, setSysData] = useState('');
  const [musicData, setMusicData] = useState('');
  const [shortcutData, setShortcutData] = useState('');
  const [clipData, setClipData] = useState('');
  const [contactData, setContactData] = useState('');
  const [alarmData, setAlarmData] = useState('');
  const [loading, setLoading] = useState({});
  const [noteKw, setNoteKw] = useState('');
  const [contactKw, setContactKw] = useState('');
  const [calForm, setCalForm] = useState({title:'', date:'', time:'', duration:'60'});
  const [remForm, setRemForm] = useState({title:'', due:'', notes:'', url:'', priority:'', list_name:'', flagged:false});
  const [remFilter, setRemFilter] = useState('upcoming');
  const [noteForm, setNoteForm] = useState({title:'', body:''});
  const [notifyForm, setNotifyForm] = useState({title:'', message:''});
  const [alarmForm, setAlarmForm] = useState({time:'', label:'', date:''});
  const [timerMin, setTimerMin] = useState('5');
  const [timerLabel, setTimerLabel] = useState('');
  const [clipText, setClipText] = useState('');
  const [volumeVal, setVolumeVal] = useState('50');
  const [brightVal, setBrightVal] = useState('50');
  const toast = useContext(ToastContext);

  const load = async (type) => {
    setLoading(p=>({...p,[type]:true}));
    try {
      if (type==='calendar') { const r = await apiGet('/api/apple/calendar'); setCalData(r.text||''); }
      else if (type==='reminders') { const r = await apiGet('/api/apple/reminders?filter='+remFilter); setRemData(r.text||''); }
      else if (type==='notes') { const r = await apiGet('/api/apple/notes' + (noteKw ? '?keyword='+encodeURIComponent(noteKw) : '')); setNoteData(r.text||''); }
      else if (type==='sysinfo') { const r = await apiGet('/api/apple/sysinfo'); setSysData(r.text||''); }
      else if (type==='music') { const r = await apiGet('/api/apple/music/status'); setMusicData(r.text||''); }
      else if (type==='shortcuts') { const r = await apiGet('/api/apple/shortcuts'); setShortcutData(r.text||''); }
      else if (type==='clipboard') { const r = await apiGet('/api/apple/clipboard'); setClipData(r.text||''); }
      else if (type==='contacts') { const r = await apiGet('/api/apple/contacts' + (contactKw ? '?keyword='+encodeURIComponent(contactKw) : '')); setContactData(r.text||''); }
      else if (type==='alarm') { const r = await apiGet('/api/apple/alarm/list'); setAlarmData(r.text||''); }
    } catch(e) { toast('加载失败','error'); }
    setLoading(p=>({...p,[type]:false}));
  };

  const addCal = async () => {
    if (!calForm.title) return;
    try { const r = await apiPost('/api/apple/calendar', calForm); toast(r.text||'已添加','success'); load('calendar'); setCalForm({title:'',date:'',time:'',duration:'60'}); } catch(e) { toast('添加失败','error'); }
  };
  const addRem = async () => {
    if (!remForm.title) return;
    const body = {title: remForm.title};
    if (remForm.due) body.due = remForm.due;
    if (remForm.notes) body.notes = remForm.notes;
    if (remForm.url) body.url = remForm.url;
    if (remForm.priority) body.priority = remForm.priority;
    if (remForm.list_name) body.list_name = remForm.list_name;
    if (remForm.flagged) body.flagged = true;
    try { const r = await apiPost('/api/apple/reminders', body); toast(r.text||'已添加','success'); load('reminders'); setRemForm({title:'',due:'',notes:'',url:'',priority:'',list_name:'',flagged:false}); } catch(e) { toast('添加失败','error'); }
  };
  const addNote = async () => {
    if (!noteForm.title) return;
    try { const r = await apiPost('/api/apple/notes', noteForm); toast(r.text||'已创建','success'); load('notes'); setNoteForm({title:'',body:''}); } catch(e) { toast('创建失败','error'); }
  };
  const sendNotify = async () => {
    if (!notifyForm.title) return;
    try { const r = await apiPost('/api/apple/notify', notifyForm); toast(r.text||'已发送','success'); setNotifyForm({title:'',message:''}); } catch(e) { toast('发送失败','error'); }
  };
  const musicAction = async (action) => {
    setLoading(p=>({...p,music:true}));
    try { const r = await apiPost('/api/apple/music', {action}); toast(r.text||action,'success'); load('music'); } catch(e) { toast('操作失败','error'); }
    setLoading(p=>({...p,music:false}));
  };
  const runShortcut = async (name) => {
    setLoading(p=>({...p,shortcuts:true}));
    try { const r = await apiPost('/api/apple/shortcuts/run', {name}); toast(r.text||'已执行','success'); } catch(e) { toast('执行失败','error'); }
    setLoading(p=>({...p,shortcuts:false}));
  };
  const setClipboard = async () => {
    if (!clipText.trim()) return;
    try { const r = await apiPost('/api/apple/clipboard', {text: clipText}); toast(r.text||'已设置','success'); setClipText(''); load('clipboard'); } catch(e) { toast('设置失败','error'); }
  };
  const setAlarm = async () => {
    if (!alarmForm.time) return;
    try { const r = await apiPost('/api/apple/alarm', alarmForm); toast(r.text||'已设置','success'); load('alarm'); setAlarmForm({time:'',label:'',date:''}); } catch(e) { toast('设置失败','error'); }
  };
  const setTimer = async () => {
    if (!timerMin) return;
    try { const r = await apiPost('/api/apple/timer', {minutes: timerMin, label: timerLabel}); toast(r.text||'已启动','success'); load('alarm'); setTimerLabel(''); } catch(e) { toast('设置失败','error'); }
  };
  const cancelAlarm = async (id) => {
    try { const r = await apiPost('/api/apple/alarm/cancel', {id}); toast(r.text||'已取消','success'); load('alarm'); } catch(e) { toast('取消失败','error'); }
  };
  const ctrlAction = async (endpoint, value) => {
    const bodyMap = {
      volume: value === 'mute' ? {action:'mute'} : {action:'set', value},
      brightness: {value},
      dnd: {action: value},
    };
    try { const r = await apiPost('/api/apple/' + endpoint, bodyMap[endpoint] || {value}); toast(r.text||'OK','success'); } catch(e) { toast('操作失败','error'); }
  };

  useEffect(() => { load('calendar'); }, []);

  const tabItems = [
    {id:'calendar',label:'日历',icon:'📅'},{id:'reminders',label:'提醒',icon:'⏰'},
    {id:'notes',label:'备忘录',icon:'📝'},{id:'music',label:'音乐',icon:'🎵'},
    {id:'shortcuts',label:'快捷指令',icon:'⚡'},{id:'clipboard',label:'剪贴板',icon:'📋'},
    {id:'contacts',label:'通讯录',icon:'👤'},{id:'alarm',label:'闹钟',icon:'⏰'},
    {id:'notify',label:'通知',icon:'🔔'},{id:'controls',label:'系统控制',icon:'🎛️'},{id:'sysinfo',label:'系统信息',icon:'💻'},
  ];

  return (
    <div className="flex-1 overflow-y-auto overflow-x-hidden p-6 animate-fade-in">
      <h1 className="text-xl font-bold text-white mb-4">Apple 集成</h1>
      <div className="flex gap-2 mb-4 flex-wrap">
        {tabItems.map(t => (
          <button key={t.id} onClick={() => { setTab(t.id); if (!['controls','notify'].includes(t.id)) load(t.id); }}
            className={`btn px-3 py-1.5 rounded-xl text-[12px] border transition ${tab===t.id ? 'bg-brand-600/20 text-brand-400 border-brand-500/30' : 'bg-surface-2 text-zinc-400 border-border hover:border-border-light'}`}>
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {tab==='calendar' && (<div className="space-y-4">
        <Card>
          <div className="flex items-center justify-between mb-3"><h3 className="text-sm font-medium text-zinc-200">日历事件</h3><button onClick={()=>load('calendar')} className="btn text-[11px] text-brand-400 hover:text-brand-300">{loading.calendar ? <Spinner/> : '刷新'}</button></div>
          <DataBlock data={calData} loading={loading.calendar} placeholder="点击刷新加载日历"/>
        </Card>
        <Card>
          <h3 className="text-sm font-medium text-zinc-200 mb-3">添加日历事件</h3>
          <div className="grid grid-cols-2 gap-2">
            <input value={calForm.title} onChange={e=>setCalForm(p=>({...p,title:e.target.value}))} placeholder="事件标题" className="col-span-2 bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 placeholder-zinc-600" />
            <input type="date" value={calForm.date} onChange={e=>setCalForm(p=>({...p,date:e.target.value}))} className="bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40" />
            <input type="time" value={calForm.time} onChange={e=>setCalForm(p=>({...p,time:e.target.value}))} className="bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40" />
          </div>
          <button onClick={addCal} className="btn mt-3 px-4 py-2 bg-brand-600 hover:bg-brand-500 text-white rounded-xl text-sm transition">添加</button>
        </Card>
      </div>)}

      {tab==='reminders' && (<div className="space-y-4">
        <Card>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-medium text-zinc-200">提醒事项</h3>
            <button onClick={()=>load('reminders')} className="btn text-[11px] text-brand-400 hover:text-brand-300">{loading.reminders ? <Spinner/> : '刷新'}</button>
          </div>
          <div className="flex gap-1 mb-3 flex-wrap">
            {['today','tomorrow','week','overdue','upcoming','all'].map(f => (
              <button key={f} onClick={()=>{setRemFilter(f); setTimeout(()=>load('reminders'),0);}}
                className={`btn px-2.5 py-1 rounded-lg text-[11px] border transition ${remFilter===f ? 'bg-brand-600/20 text-brand-400 border-brand-500/30' : 'bg-surface-3 text-zinc-500 border-border'}`}>
                {({today:'今天',tomorrow:'明天',week:'本周',overdue:'逾期',upcoming:'即将',all:'全部'})[f]}
              </button>
            ))}
          </div>
          <DataBlock data={remData} loading={loading.reminders} placeholder="点击刷新加载提醒"/>
        </Card>
        <Card>
          <h3 className="text-sm font-medium text-zinc-200 mb-3">新建提醒</h3>
          <div className="space-y-2">
            <input value={remForm.title} onChange={e=>setRemForm(p=>({...p,title:e.target.value}))} placeholder="提醒标题 *" className="w-full bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 placeholder-zinc-600" />
            <textarea value={remForm.notes} onChange={e=>setRemForm(p=>({...p,notes:e.target.value}))} placeholder="备注 (Notes)" rows={2} className="w-full bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 resize-none placeholder-zinc-600" />
            <div className="grid grid-cols-2 gap-2">
              <input value={remForm.due} onChange={e=>setRemForm(p=>({...p,due:e.target.value}))} placeholder="到期时间 (如: 明天 14:00)" className="bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 placeholder-zinc-600" />
              <input value={remForm.list_name} onChange={e=>setRemForm(p=>({...p,list_name:e.target.value}))} placeholder="列表名 (如: 工作)" className="bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 placeholder-zinc-600" />
              <input value={remForm.url} onChange={e=>setRemForm(p=>({...p,url:e.target.value}))} placeholder="URL (可选)" className="bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 placeholder-zinc-600" />
              <div className="flex items-center gap-3">
                <select value={remForm.priority} onChange={e=>setRemForm(p=>({...p,priority:e.target.value}))} className="flex-1 bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40">
                  <option value="">优先级</option>
                  <option value="high">高 (Urgent)</option>
                  <option value="medium">中</option>
                  <option value="low">低</option>
                </select>
                <label className="flex items-center gap-1.5 text-[12px] text-zinc-400 cursor-pointer select-none">
                  <input type="checkbox" checked={remForm.flagged} onChange={e=>setRemForm(p=>({...p,flagged:e.target.checked}))} className="accent-brand-500" />
                  🚩 旗标
                </label>
              </div>
            </div>
          </div>
          <button onClick={addRem} disabled={!remForm.title} className="btn mt-3 px-4 py-2 bg-brand-600 hover:bg-brand-500 text-white rounded-xl text-sm transition disabled:opacity-50">添加提醒</button>
        </Card>
      </div>)}

      {tab==='notes' && (<div className="space-y-4">
        <Card>
          <div className="flex items-center justify-between mb-3"><h3 className="text-sm font-medium text-zinc-200">备忘录</h3><button onClick={()=>load('notes')} className="btn text-[11px] text-brand-400 hover:text-brand-300">{loading.notes ? <Spinner/> : '刷新'}</button></div>
          <div className="flex gap-2 mb-3"><input value={noteKw} onChange={e=>setNoteKw(e.target.value)} onKeyDown={e=>e.key==='Enter'&&load('notes')} placeholder="搜索备忘录..." className="flex-1 bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 placeholder-zinc-600" /></div>
          <DataBlock data={noteData} loading={loading.notes} placeholder="搜索或刷新加载备忘录"/>
        </Card>
        <Card>
          <h3 className="text-sm font-medium text-zinc-200 mb-3">新建备忘录</h3>
          <div className="space-y-2">
            <input value={noteForm.title} onChange={e=>setNoteForm(p=>({...p,title:e.target.value}))} placeholder="标题" className="w-full bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 placeholder-zinc-600" />
            <textarea value={noteForm.body} onChange={e=>setNoteForm(p=>({...p,body:e.target.value}))} placeholder="内容..." rows={4} className="w-full bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 resize-none placeholder-zinc-600" />
          </div>
          <button onClick={addNote} className="btn mt-3 px-4 py-2 bg-brand-600 hover:bg-brand-500 text-white rounded-xl text-sm transition">创建</button>
        </Card>
      </div>)}

      {tab==='music' && (<div className="space-y-4">
        <Card>
          <div className="flex items-center justify-between mb-3"><h3 className="text-sm font-medium text-zinc-200">音乐控制</h3><button onClick={()=>load('music')} className="btn text-[11px] text-brand-400 hover:text-brand-300">{loading.music ? <Spinner/> : '刷新状态'}</button></div>
          <DataBlock data={musicData} loading={loading.music} placeholder="点击刷新获取播放状态"/>
          <div className="flex gap-2 mt-3">
            {[{a:'previous',icon:'⏮',l:'上一首'},{a:'playpause',icon:'⏯',l:'播放/暂停'},{a:'next',icon:'⏭',l:'下一首'}].map(b => (
              <button key={b.a} onClick={()=>musicAction(b.a)} className="btn flex-1 px-3 py-2 bg-surface-3 hover:bg-surface-2 border border-border rounded-xl text-sm text-zinc-300 transition">{b.icon} {b.l}</button>
            ))}
          </div>
        </Card>
      </div>)}

      {tab==='shortcuts' && (<div className="space-y-4">
        <Card>
          <div className="flex items-center justify-between mb-3"><h3 className="text-sm font-medium text-zinc-200">快捷指令</h3><button onClick={()=>load('shortcuts')} className="btn text-[11px] text-brand-400 hover:text-brand-300">{loading.shortcuts ? <Spinner/> : '刷新列表'}</button></div>
          <DataBlock data={shortcutData} loading={loading.shortcuts} placeholder="点击刷新加载快捷指令列表"/>
          {shortcutData && (<div className="mt-3 flex flex-wrap gap-2">
            {shortcutData.split('\n').filter(l=>l.trim()).slice(0, 20).map((name,i) => (
              <button key={i} onClick={()=>runShortcut(name.trim())} className="btn px-3 py-1.5 bg-surface-3 hover:bg-surface-2 border border-border rounded-xl text-[12px] text-zinc-300 transition truncate max-w-[200px]" title={name.trim()}>
                ▶ {name.trim()}
              </button>
            ))}
          </div>)}
        </Card>
      </div>)}

      {tab==='clipboard' && (<div className="space-y-4">
        <Card>
          <div className="flex items-center justify-between mb-3"><h3 className="text-sm font-medium text-zinc-200">剪贴板内容</h3><button onClick={()=>load('clipboard')} className="btn text-[11px] text-brand-400 hover:text-brand-300">{loading.clipboard ? <Spinner/> : '刷新'}</button></div>
          <DataBlock data={clipData} loading={loading.clipboard} placeholder="点击刷新读取剪贴板"/>
        </Card>
        <Card>
          <h3 className="text-sm font-medium text-zinc-200 mb-3">设置剪贴板</h3>
          <textarea value={clipText} onChange={e=>setClipText(e.target.value)} placeholder="输入要写入剪贴板的内容..." rows={3} className="w-full bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 resize-none placeholder-zinc-600" />
          <button onClick={setClipboard} className="btn mt-3 px-4 py-2 bg-brand-600 hover:bg-brand-500 text-white rounded-xl text-sm transition">写入剪贴板</button>
        </Card>
      </div>)}

      {tab==='contacts' && (<Card>
        <div className="flex items-center justify-between mb-3"><h3 className="text-sm font-medium text-zinc-200">通讯录</h3><button onClick={()=>load('contacts')} className="btn text-[11px] text-brand-400 hover:text-brand-300">{loading.contacts ? <Spinner/> : '搜索'}</button></div>
        <div className="flex gap-2 mb-3"><input value={contactKw} onChange={e=>setContactKw(e.target.value)} onKeyDown={e=>e.key==='Enter'&&load('contacts')} placeholder="输入姓名搜索通讯录..." className="flex-1 bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 placeholder-zinc-600" /></div>
        <DataBlock data={contactData} loading={loading.contacts} placeholder="输入姓名搜索通讯录"/>
      </Card>)}

      {tab==='alarm' && (<div className="space-y-4">
        <Card>
          <div className="flex items-center justify-between mb-3"><h3 className="text-sm font-medium text-zinc-200">活跃闹钟</h3>
            <div className="flex gap-2">
              <button onClick={()=>cancelAlarm('all')} className="btn text-[11px] text-rose-400 hover:text-rose-300">全部取消</button>
              <button onClick={()=>load('alarm')} className="btn text-[11px] text-brand-400 hover:text-brand-300">{loading.alarm ? <Spinner/> : '刷新'}</button>
            </div>
          </div>
          <DataBlock data={alarmData} loading={loading.alarm} placeholder="暂无活跃闹钟"/>
        </Card>
        <Card>
          <h3 className="text-sm font-medium text-zinc-200 mb-3">设置闹钟</h3>
          <div className="grid grid-cols-2 gap-2">
            <input type="time" value={alarmForm.time} onChange={e=>setAlarmForm(p=>({...p,time:e.target.value}))} className="bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40" />
            <input type="date" value={alarmForm.date} onChange={e=>setAlarmForm(p=>({...p,date:e.target.value}))} className="bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40" />
            <input value={alarmForm.label} onChange={e=>setAlarmForm(p=>({...p,label:e.target.value}))} placeholder="标签（可选）" className="col-span-2 bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 placeholder-zinc-600" />
          </div>
          <button onClick={setAlarm} disabled={!alarmForm.time} className="btn mt-3 px-4 py-2 bg-brand-600 hover:bg-brand-500 text-white rounded-xl text-sm transition disabled:opacity-50">设置闹钟</button>
        </Card>
        <Card>
          <h3 className="text-sm font-medium text-zinc-200 mb-3">快捷定时器</h3>
          <div className="flex items-center gap-3">
            <div className="flex gap-1">
              {['1','3','5','10','15','30','60'].map(m => (
                <button key={m} onClick={()=>setTimerMin(m)} className={`btn px-2.5 py-1.5 rounded-lg text-[12px] border transition ${timerMin===m ? 'bg-brand-600/20 text-brand-400 border-brand-500/30' : 'bg-surface-3 text-zinc-400 border-border'}`}>{m}min</button>
              ))}
            </div>
          </div>
          <div className="flex gap-2 mt-3">
            <input value={timerLabel} onChange={e=>setTimerLabel(e.target.value)} placeholder="定时器标签（可选）" className="flex-1 bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 placeholder-zinc-600" />
            <button onClick={setTimer} className="btn px-4 py-2 bg-brand-600 hover:bg-brand-500 text-white rounded-xl text-sm transition">启动定时器</button>
          </div>
        </Card>
      </div>)}

      {tab==='notify' && (<Card>
        <h3 className="text-sm font-medium text-zinc-200 mb-3">发送系统通知</h3>
        <div className="space-y-2">
          <input value={notifyForm.title} onChange={e=>setNotifyForm(p=>({...p,title:e.target.value}))} placeholder="通知标题" className="w-full bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 placeholder-zinc-600" />
          <textarea value={notifyForm.message} onChange={e=>setNotifyForm(p=>({...p,message:e.target.value}))} placeholder="通知内容..." rows={3} className="w-full bg-surface-3 border border-border rounded-xl px-3 py-2 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 resize-none placeholder-zinc-600" />
        </div>
        <button onClick={sendNotify} className="btn mt-3 px-4 py-2 bg-brand-600 hover:bg-brand-500 text-white rounded-xl text-sm transition">发送通知</button>
      </Card>)}

      {tab==='controls' && (<div className="space-y-4">
        <Card>
          <h3 className="text-sm font-medium text-zinc-200 mb-3">音量控制</h3>
          <div className="flex items-center gap-3">
            <button onClick={()=>ctrlAction('volume','mute')} className="btn px-3 py-2 bg-surface-3 hover:bg-surface-2 border border-border rounded-xl text-sm text-zinc-300 transition">🔇 静音</button>
            <input type="range" min="0" max="100" value={volumeVal} onChange={e=>setVolumeVal(e.target.value)} className="flex-1 accent-brand-500" />
            <span className="text-sm text-zinc-400 w-10 text-right">{volumeVal}%</span>
            <button onClick={()=>ctrlAction('volume',volumeVal)} className="btn px-3 py-2 bg-brand-600 hover:bg-brand-500 text-white rounded-xl text-sm transition">设置</button>
          </div>
        </Card>
        <Card>
          <h3 className="text-sm font-medium text-zinc-200 mb-3">屏幕亮度</h3>
          <div className="flex items-center gap-3">
            <span className="text-zinc-400">🔅</span>
            <input type="range" min="0" max="100" value={brightVal} onChange={e=>setBrightVal(e.target.value)} className="flex-1 accent-brand-500" />
            <span className="text-sm text-zinc-400 w-10 text-right">{brightVal}%</span>
            <button onClick={()=>ctrlAction('brightness',brightVal)} className="btn px-3 py-2 bg-brand-600 hover:bg-brand-500 text-white rounded-xl text-sm transition">设置</button>
            <span className="text-zinc-400">🔆</span>
          </div>
        </Card>
        <Card>
          <h3 className="text-sm font-medium text-zinc-200 mb-3">勿扰模式</h3>
          <div className="flex gap-2">
            <button onClick={()=>ctrlAction('dnd','on')} className="btn flex-1 px-3 py-2 bg-surface-3 hover:bg-amber-600/20 border border-border hover:border-amber-500/30 rounded-xl text-sm text-zinc-300 hover:text-amber-400 transition">🌙 开启勿扰</button>
            <button onClick={()=>ctrlAction('dnd','off')} className="btn flex-1 px-3 py-2 bg-surface-3 hover:bg-emerald-600/20 border border-border hover:border-emerald-500/30 rounded-xl text-sm text-zinc-300 hover:text-emerald-400 transition">☀️ 关闭勿扰</button>
          </div>
        </Card>
      </div>)}

      {tab==='sysinfo' && (<Card>
        <div className="flex items-center justify-between mb-3"><h3 className="text-sm font-medium text-zinc-200">系统信息</h3><button onClick={()=>load('sysinfo')} className="btn text-[11px] text-brand-400 hover:text-brand-300">{loading.sysinfo ? <Spinner/> : '刷新'}</button></div>
        <DataBlock data={sysData} loading={loading.sysinfo} placeholder="点击刷新获取系统信息"/>
      </Card>)}
    </div>
  );
}


// ── Dev View ────────────────────────────────────────

// 轻量 Markdown → React 渲染（支持代码块、表格、标题、加粗、列表）
function MdBlock({text}) {
  if (!text) return null;
  const lines = text.split('\n');
  const elements = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    // 代码块
    if (line.startsWith('```')) {
      const lang = line.slice(3).trim();
      const codeLines = [];
      i++;
      while (i < lines.length && !lines[i].startsWith('```')) { codeLines.push(lines[i]); i++; }
      i++; // skip closing ```
      elements.push(
        React.createElement('div', {key: elements.length, className: 'relative group my-2'},
          lang && React.createElement('span', {className: 'absolute top-1 right-2 text-[10px] text-zinc-600 select-none'}, lang),
          React.createElement('pre', {className: 'bg-zinc-950 border border-zinc-800 rounded-lg p-3 overflow-x-auto text-[12px] leading-relaxed text-emerald-300 font-mono'},
            React.createElement('code', null, codeLines.join('\n'))
          )
        )
      );
      continue;
    }
    // 表格
    if (line.includes('|') && line.trim().startsWith('|')) {
      const tableLines = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim().startsWith('|')) { tableLines.push(lines[i]); i++; }
      if (tableLines.length >= 2) {
        const parseRow = r => r.split('|').filter((_,j,a) => j > 0 && j < a.length-1).map(c => c.trim());
        const headers = parseRow(tableLines[0]);
        const isSep = r => /^[\s|:-]+$/.test(r);
        const dataStart = isSep(tableLines[1]) ? 2 : 1;
        const rows = tableLines.slice(dataStart).filter(r => !isSep(r)).map(parseRow);
        elements.push(
          React.createElement('div', {key: elements.length, className: 'overflow-x-auto my-2'},
            React.createElement('table', {className: 'w-full text-[12px] border border-zinc-800 rounded'},
              React.createElement('thead', null,
                React.createElement('tr', {className: 'bg-zinc-900'},
                  headers.map((h,j) => React.createElement('th', {key:j, className: 'px-3 py-1.5 text-left text-zinc-400 font-semibold border-b border-zinc-800'}, h))
                )
              ),
              React.createElement('tbody', null,
                rows.map((row,ri) => React.createElement('tr', {key:ri, className: ri%2?'bg-zinc-900/30':''},
                  row.map((cell,ci) => React.createElement('td', {key:ci, className: 'px-3 py-1 text-zinc-300 border-b border-zinc-800/50'}, cell))
                ))
              )
            )
          )
        );
        continue;
      }
    }
    // 标题
    const hm = line.match(/^(#{1,4})\s+(.+)/);
    if (hm) {
      const level = hm[1].length;
      const sizes = {1:'text-lg font-bold',2:'text-base font-semibold',3:'text-sm font-semibold',4:'text-[13px] font-medium'};
      elements.push(React.createElement('div', {key: elements.length, className: `${sizes[level]||sizes[4]} text-zinc-200 mt-3 mb-1`}, hm[2]));
      i++; continue;
    }
    // 列表
    const lm = line.match(/^(\s*)([-*]|\d+\.)\s+(.+)/);
    if (lm) {
      elements.push(React.createElement('div', {key: elements.length, className: 'flex gap-2 text-[13px] text-zinc-300', style:{paddingLeft: Math.min((lm[1]||'').length, 8)*4}},
        React.createElement('span', {className: 'text-zinc-600 flex-shrink-0'}, lm[2].match(/\d/) ? lm[2] : '•'),
        React.createElement('span', {dangerouslySetInnerHTML: {__html: inlineMd(lm[3])}})
      ));
      i++; continue;
    }
    // 空行
    if (!line.trim()) { elements.push(React.createElement('div', {key: elements.length, className: 'h-1.5'})); i++; continue; }
    // 普通段落（含行内 Markdown）
    elements.push(React.createElement('p', {key: elements.length, className: 'text-[13px] text-zinc-300 leading-relaxed', dangerouslySetInnerHTML: {__html: inlineMd(line)}}));
    i++;
  }
  return React.createElement('div', {className: 'space-y-0.5'}, elements);
}
function inlineMd(s) {
  return s
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong class="text-zinc-100 font-semibold">$1</strong>')
    .replace(/`([^`]+)`/g, '<code class="px-1.5 py-0.5 rounded bg-zinc-800 text-amber-300 text-[12px] font-mono">$1</code>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" class="text-brand-400 underline">$1</a>');
}

function DevView() {
  const [tab, setTab] = useState('claude');
  const [inputs, setInputs] = useState({ssh:'', local:'', claude:'', review:'', refactor:'', fix:'', test:'', explain:''});
  const [result, setResult] = useState('');
  const [progress, setProgress] = useState('');
  const [loading, setLoading] = useState(false);
  const [history, setHistory] = useState([]);
  const [showHistory, setShowHistory] = useState(false);
  const [sshHost, setSshHost] = useState('local');
  const [selectedRepo, setSelectedRepo] = useState('');
  const [customRepoPath, setCustomRepoPath] = useState('');
  const [showCustomRepo, setShowCustomRepo] = useState(false);
  const [envStatus, setEnvStatus] = useState(null);
  const [envLoading, setEnvLoading] = useState(false);
  const [testingHost, setTestingHost] = useState('');
  const [hostTestResults, setHostTestResults] = useState({});
  const [devModel, setDevModel] = useState('');
  const toast = useContext(ToastContext);

  const setInput = (key, val) => setInputs(prev => ({...prev, [key]: val}));

  // 普通 POST 请求（SSH/local/git）
  const run = async (apiPath, body, label) => {
    setLoading(true); setResult(''); setProgress('');
    try {
      const r = await apiPost(apiPath, body);
      const out = r.result || r.output || JSON.stringify(r, null, 2);
      setResult(out);
      setHistory(prev => [{cmd: label, time: new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}), ok: true}, ...prev].slice(0, 20));
    } catch(e) {
      toast('执行失败: ' + (e.message||''),'error');
      setHistory(prev => [{cmd: label, time: new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}), ok: false}, ...prev].slice(0, 20));
    }
    setLoading(false);
  };

  // SSE 流式请求（Claude Code）— 实时显示进度
  const runClaude = async (body, label) => {
    setLoading(true); setResult(''); setProgress('');
    const nowStr = new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});
    const payload = {...body};
    if (devModel) {
      if (devModel.includes(':')) {
        const [prov, mod] = devModel.split(':', 2);
        payload.provider = prov;
        payload.model = mod;
      } else {
        payload.model = devModel;
      }
    }
    try {
      const token = localStorage.getItem('token');
      const resp = await fetch('/api/dev/claude', {
        method: 'POST', headers: {'Content-Type': 'application/json', ...(token ? {'Authorization': `Bearer ${token}`} : {})},
        body: JSON.stringify(payload),
      });
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let finalResult = '';
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
            if (evt.type === 'progress') setProgress(evt.content || '');
            else if (evt.type === 'done') { finalResult = evt.content || ''; setProgress(''); }
            else if (evt.type === 'error') { finalResult = '❌ ' + (evt.content || 'Error'); setProgress(''); }
          } catch(e) {}
        }
      }
      setResult(finalResult || '(无输出)');
      setHistory(prev => [{cmd: label, time: nowStr, ok: !finalResult.startsWith('❌')}, ...prev].slice(0, 20));
    } catch(e) {
      setResult(''); setProgress('');
      toast('执行失败: ' + (e.message||''), 'error');
      setHistory(prev => [{cmd: label, time: nowStr, ok: false}, ...prev].slice(0, 20));
    }
    setLoading(false);
  };

  const testHost = async (hostKey) => {
    setTestingHost(hostKey);
    setHostTestResults(prev => ({...prev, [hostKey]: {testing: true}}));
    try {
      const r = await apiPost('/api/dev/host/test', {host: hostKey});
      setHostTestResults(prev => ({...prev, [hostKey]: {connected: r.connected, text: r.result, ts: Date.now()}}));
      if (r.connected) toast(`${hostKey} 连接成功`, 'success');
      else toast(`${hostKey} 连接失败`, 'error');
    } catch(e) {
      setHostTestResults(prev => ({...prev, [hostKey]: {connected: false, text: e.message||'请求失败', ts: Date.now()}}));
      toast(`${hostKey} 测试失败`, 'error');
    }
    setTestingHost('');
  };

  const loadEnv = async () => {
    setEnvLoading(true);
    try {
      const r = await apiGet('/api/dev/hosts');
      setEnvStatus(r);
      if (r?.repos && !selectedRepo) {
        const firstKey = Object.keys(r.repos).find(k => r.repos[k].exists);
        if (firstKey) setSelectedRepo(firstKey);
      }
    } catch(e) {}
    setEnvLoading(false);
  };

  useEffect(() => { loadEnv(); }, []);

  const tabs = [
    {id:'claude', icon:'🧠', label:'Claude Code', color:'from-violet-500 to-purple-600', tag:'AI'},
    {id:'review', icon:'🔍', label:'代码审查', color:'from-blue-500 to-indigo-600', tag:'AI'},
    {id:'refactor', icon:'🔄', label:'智能重构', color:'from-amber-500 to-orange-600', tag:'AI'},
    {id:'fix', icon:'🐛', label:'Bug 修复', color:'from-rose-500 to-red-600', tag:'AI'},
    {id:'test', icon:'🧪', label:'生成测试', color:'from-emerald-500 to-teal-600', tag:'AI'},
    {id:'explain', icon:'📖', label:'代码解释', color:'from-cyan-500 to-blue-600', tag:'AI'},
    {id:'git', icon:'📦', label:'Git 管理', color:'from-orange-500 to-red-600', tag:'SCM'},
    {id:'hosts', icon:'🌐', label:'主机连接', color:'from-teal-500 to-cyan-600', tag:'运维'},
    {id:'ssh', icon:'🖥️', label:'SSH 远程', color:'from-slate-500 to-gray-600', tag:'运维'},
    {id:'local', icon:'💻', label:'本地命令', color:'from-slate-500 to-gray-600', tag:'运维'},
  ];
  const activeTab = tabs.find(t => t.id === tab) || tabs[0];

  const hostPresets = envStatus?.hosts || {};
  const repoPresets = envStatus?.repos || {};
  const activeHost = hostPresets[sshHost] || {};
  const activeRepo = selectedRepo === '__custom__'
    ? {label: customRepoPath.split('/').pop() || '自定义', path: customRepoPath, work_dir: customRepoPath, exists: true}
    : (repoPresets[selectedRepo] || {});
  const workDir = activeRepo.work_dir || activeRepo.path || '';
  const hostLabel = activeHost.label || sshHost;

  return (
    <div className="flex-1 overflow-y-auto overflow-x-hidden p-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            开发工具 <span className="text-[10px] px-2 py-0.5 rounded-full bg-brand-600/20 text-brand-400 font-semibold">Claude Code</span>
          </h1>
          <p className="text-[12px] text-zinc-500 mt-1">AI 编程 · 代码审查 · Git 管理 · 多主机开发 — {tabs.length} 项能力就绪</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowHistory(!showHistory)}
            className={`btn px-3 py-1.5 rounded-lg text-[11px] border transition ${showHistory ? 'bg-brand-600/20 text-brand-400 border-brand-500/30' : 'bg-surface-2 text-zinc-500 border-border hover:border-border-light'}`}>
            📋 {history.length || ''}
          </button>
          <button onClick={loadEnv} disabled={envLoading}
            className="btn px-3 py-1.5 rounded-lg text-[11px] bg-emerald-600/20 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-600/30 transition disabled:opacity-50">
            {envLoading ? <Spinner /> : '📡'} 刷新环境
          </button>
        </div>
      </div>

      {/* Active environment selector */}
      <div className="mb-4 rounded-xl bg-surface-1 border border-border p-3">
        <div className="flex items-center gap-4 flex-wrap">
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-zinc-600 font-semibold uppercase tracking-wider">主机</span>
            <div className="flex gap-1">
              {Object.entries(hostPresets).map(([k, h]) => (
                <button key={k} onClick={() => setSshHost(k)} title={`${h.user||'root'}@${h.host}`}
                  className={`px-2.5 py-1 rounded-lg text-[10px] border transition-all ${sshHost===k
                    ? 'bg-brand-600/20 text-brand-400 border-brand-500/30 shadow-[0_0_8px_rgba(99,102,241,.1)]'
                    : h.connected ? 'bg-surface-2 text-zinc-300 border-border hover:border-border-light' : 'bg-surface-2 text-zinc-600 border-border hover:border-border-light'}`}>
                  {h.connected ? '🟢' : '🔴'} {h.label || k}
                </button>
              ))}
            </div>
          </div>
          <span className="w-px h-5 bg-border"></span>
          <div className="flex items-center gap-2 flex-1 min-w-0">
            <span className="text-[10px] text-zinc-600 font-semibold uppercase tracking-wider flex-shrink-0">项目</span>
            <div className="flex gap-1 flex-wrap">
              {Object.entries(repoPresets).filter(([,r]) => r.exists).map(([k, r]) => (
                <button key={k} onClick={() => { setSelectedRepo(k); setShowCustomRepo(false); }} title={r.path}
                  className={`px-2.5 py-1 rounded-lg text-[10px] border transition-all ${selectedRepo===k && !showCustomRepo
                    ? 'bg-brand-600/20 text-brand-400 border-brand-500/30 shadow-[0_0_8px_rgba(99,102,241,.1)]'
                    : 'bg-surface-2 text-zinc-300 border-border hover:border-border-light'}`}>
                  📂 {r.label || k} {r.branch ? <span className="text-zinc-600">({r.branch})</span> : null}
                  {r.dirty && <span className="ml-0.5 text-amber-400">*</span>}
                </button>
              ))}
              <button onClick={() => setShowCustomRepo(!showCustomRepo)} title="自定义项目路径"
                className={`px-2 py-1 rounded-lg text-[10px] border transition-all ${showCustomRepo
                  ? 'bg-violet-600/20 text-violet-400 border-violet-500/30'
                  : 'bg-surface-2 text-zinc-500 border-border hover:border-border-light hover:text-zinc-300'}`}>
                + 自定义
              </button>
            </div>
          </div>
          <span className="w-px h-5 bg-border"></span>
          <span title={envStatus?.claude?.detail || ''} className={`flex items-center gap-1 text-[10px] cursor-default ${envStatus?.claude?.available ? 'text-emerald-400' : 'text-zinc-600'}`}>
            {envStatus?.claude?.available ? '🟢' : '🔴'} Claude
          </span>
          <select value={devModel} onChange={e => setDevModel(e.target.value)} title="开发模型"
            className="bg-surface-2 border border-border rounded-lg px-2 py-1 text-[10px] text-zinc-300 focus:outline-none focus:border-brand-500/40 cursor-pointer">
            <optgroup label="Claude Code (agentic)">
              <option value="">Sonnet (默认)</option>
              <option value="opus">Opus</option>
              <option value="haiku">Haiku</option>
            </optgroup>
            <optgroup label="百炼">
              <option value="bailian:qwen3-coder-plus">Qwen3 Coder Plus</option>
              <option value="bailian:qwen3-coder-next">Qwen3 Coder Next</option>
              <option value="bailian:qwen3.5-plus">Qwen3.5 Plus</option>
              <option value="bailian:qwen3-max">Qwen3 Max</option>
              <option value="bailian:kimi-k2.5">Kimi K2.5</option>
            </optgroup>
            <optgroup label="DeepSeek">
              <option value="deepseek:deepseek-chat">DeepSeek V3</option>
              <option value="deepseek:deepseek-reasoner">DeepSeek R1</option>
            </optgroup>
            <optgroup label="SiliconFlow">
              <option value="siliconflow:deepseek-v3">DeepSeek V3</option>
              <option value="siliconflow:qwen-72b">Qwen 72B</option>
            </optgroup>
          </select>
        </div>
        {showCustomRepo && (
          <div className="mt-2 pt-2 border-t border-border/50 flex items-center gap-2">
            <span className="text-[10px] text-zinc-500 flex-shrink-0">📁 路径:</span>
            <input value={customRepoPath} onChange={e => setCustomRepoPath(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter' && customRepoPath.trim()) { setSelectedRepo('__custom__'); toast('已切换到自定义路径', 'success'); }}}
              placeholder="输入项目绝对路径，如 /Users/zayl/myproject"
              className="flex-1 bg-surface-3 border border-border rounded-lg px-2.5 py-1.5 text-[11px] text-zinc-200 font-mono focus:outline-none focus:border-violet-500/40 placeholder-zinc-600" />
            <button onClick={() => { if (customRepoPath.trim()) { setSelectedRepo('__custom__'); toast('已切换到自定义路径', 'success'); }}}
              disabled={!customRepoPath.trim()}
              className="btn px-3 py-1.5 rounded-lg text-[10px] bg-violet-600/20 text-violet-400 border border-violet-500/30 hover:bg-violet-600/30 transition disabled:opacity-40">
              确定
            </button>
          </div>
        )}
        {(activeHost.host || activeRepo.path) && (
          <div className={`${showCustomRepo ? 'mt-2' : 'mt-2 pt-2 border-t border-border/50'} flex items-center gap-4 text-[10px] text-zinc-500`}>
            {activeHost.host && <span>🖥️ {activeHost.user}@{activeHost.host}</span>}
            {workDir && <span title={activeRepo.path || ''}>📁 工作目录: {workDir}</span>}
            {envStatus?.scan_dirs && <span className="text-zinc-600" title={envStatus.scan_dirs.join(', ')}>🔍 扫描: {envStatus.scan_dirs.length} 个根目录</span>}
          </div>
        )}
      </div>

      {/* History panel */}
      {showHistory && history.length > 0 && (
        <div className="mb-4 rounded-xl border border-border bg-surface-1 p-3 animate-slide-up">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[11px] font-semibold text-zinc-400">最近操作</span>
            <button onClick={() => setHistory([])} className="text-[10px] text-zinc-600 hover:text-zinc-400 transition">清空</button>
          </div>
          <div className="space-y-1 max-h-32 overflow-y-auto overflow-x-hidden">
            {history.map((h, i) => (
              <div key={i} className="flex items-center gap-2 text-[11px]">
                <span className={h.ok ? 'text-emerald-400' : 'text-rose-400'}>{h.ok ? '✓' : '✗'}</span>
                <span className="text-zinc-400 flex-1 truncate">{h.cmd}</span>
                <span className="text-zinc-600 tabular-nums">{h.time}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Tab grid */}
      <div className="grid grid-cols-5 gap-1.5 mb-4">
        {tabs.map(t => (
          <button key={t.id} onClick={() => { setTab(t.id); setResult(''); }}
            className={`btn flex items-center gap-1.5 px-2.5 py-2 rounded-xl text-[11px] border transition-all ${
              tab===t.id
                ? 'bg-brand-600/15 text-brand-400 border-brand-500/30 shadow-[0_0_12px_rgba(99,102,241,.08)]'
                : 'bg-surface-2 text-zinc-500 border-border hover:border-border-light hover:text-zinc-300'
            }`}>
            <span>{t.icon}</span>
            <span className="font-medium truncate">{t.label}</span>
          </button>
        ))}
      </div>

      {/* Active tool panel */}
      <Card>
        <div className="flex items-center gap-3 mb-4">
          <div className={`w-9 h-9 rounded-xl bg-gradient-to-br ${activeTab.color} flex items-center justify-center text-lg shadow-sm`}>{activeTab.icon}</div>
          <div>
            <h3 className="text-sm font-semibold text-zinc-100">{activeTab.label}</h3>
            <p className="text-[11px] text-zinc-500">
              {tab==='claude' && 'Claude Code CLI — 执行完整的 AI 编程任务（读写文件、搜索代码、执行命令）'}
              {tab==='review' && 'AI 代码审查 — 安全性、性能、代码质量、风格一体化检查'}
              {tab==='refactor' && '智能代码重构 — 保持功能不变，改进结构与可读性'}
              {tab==='fix' && 'Bug 诊断与自动修复 — 提供错误描述或日志即可'}
              {tab==='test' && '自动生成单元测试 — 支持 pytest / jest / vitest 等'}
              {tab==='explain' && '深度代码解释 — 功能概述、逻辑流程、依赖关系分析'}
              {tab==='git' && 'Git 仓库管理 — 状态查看 · 拉取 · 日志 · 差异 · 同步部署'}
              {tab==='hosts' && '远程主机管理 — 连接测试 · 多机切换 · 环境探测'}
              {tab==='ssh' && '在远程主机上执行 shell 命令（支持切换目标主机）'}
              {tab==='local' && '在本机执行安全命令（白名单限制）'}
            </p>
          </div>
        </div>

        {/* Claude Code */}
        {tab==='claude' && (<div className="space-y-3">
          <textarea value={inputs.claude} onChange={e=>setInput('claude',e.target.value)} placeholder={`在 ${activeRepo.label||selectedRepo} 上执行编程任务...\n例如: 帮我写一个用户注册接口 / 重构错误处理逻辑`} rows={4}
            className="w-full bg-surface-3 border border-border rounded-xl px-4 py-3 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 resize-none placeholder-zinc-600 font-mono" />
          <div className="flex items-center gap-2">
            <button onClick={() => runClaude({task: inputs.claude, work_dir: workDir}, `Claude@${hostLabel}: ${inputs.claude.slice(0,25)}`)} disabled={loading || !inputs.claude.trim()}
              className="btn px-5 py-2.5 bg-gradient-to-r from-violet-600 to-purple-600 hover:from-violet-500 hover:to-purple-500 text-white rounded-xl text-sm font-medium transition disabled:opacity-40 shadow-lg shadow-violet-600/10">{loading ? <Spinner/> : '🚀 执行任务'}</button>
            <div className="flex gap-1 ml-auto">
              {['给 UserTable 添加排序','搜索所有 TODO'].map((ex,i) => (
                <button key={i} onClick={() => setInput('claude', ex)} className="text-[10px] px-2 py-1 rounded-lg bg-surface-3 text-zinc-500 hover:text-zinc-300 border border-border hover:border-border-light transition truncate max-w-[150px]">{ex}</button>
              ))}
            </div>
          </div>
        </div>)}

        {/* Code Review */}
        {tab==='review' && (<div className="space-y-3">
          <input value={inputs.review} onChange={e=>setInput('review',e.target.value)}
            onKeyDown={e=>e.key==='Enter'&&inputs.review.trim()&&runClaude({task:`/cr ${inputs.review}`, work_dir: workDir},`审查@${hostLabel}: ${inputs.review.split('/').pop()}`)}
            placeholder="输入文件路径，例如: agents/dev_agent.py"
            className="w-full bg-surface-3 border border-border rounded-xl px-4 py-2.5 text-sm text-zinc-200 font-mono focus:outline-none focus:border-brand-500/40 placeholder-zinc-600" />
          <div className="flex items-center gap-2">
            <button onClick={() => runClaude({task: `/cr ${inputs.review}`, work_dir: workDir}, `审查@${hostLabel}: ${inputs.review.split('/').pop()}`)} disabled={loading || !inputs.review.trim()}
              className="btn px-5 py-2.5 bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-500 hover:to-indigo-500 text-white rounded-xl text-sm font-medium transition disabled:opacity-40">{loading ? <Spinner/> : '🔍 开始审查'}</button>
            <div className="flex gap-1 ml-auto">
              {['agents/orchestrator.py','agents/dev_agent.py'].map((ex,i) => (
                <button key={i} onClick={() => setInput('review', ex)} className="text-[10px] px-2 py-1 rounded-lg bg-surface-3 text-zinc-500 hover:text-zinc-300 border border-border hover:border-border-light transition">{ex.split('/').pop()}</button>
              ))}
            </div>
          </div>
        </div>)}

        {/* Refactor */}
        {tab==='refactor' && (<div className="space-y-3">
          <input value={inputs.refactor} onChange={e=>setInput('refactor',e.target.value)} placeholder="文件路径，例如: agents/orchestrator.py"
            className="w-full bg-surface-3 border border-border rounded-xl px-4 py-2.5 text-sm text-zinc-200 font-mono focus:outline-none focus:border-brand-500/40 placeholder-zinc-600" />
          <textarea value={inputs.refactorDesc||''} onChange={e=>setInputs(p=>({...p,refactorDesc:e.target.value}))} placeholder="重构需求描述（可选）..." rows={2}
            className="w-full bg-surface-3 border border-border rounded-xl px-4 py-2.5 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 resize-none placeholder-zinc-600" />
          <button onClick={() => runClaude({task: `/refactor ${inputs.refactor} ${inputs.refactorDesc||''}`, work_dir: workDir}, `重构@${hostLabel}: ${inputs.refactor.split('/').pop()}`)} disabled={loading || !inputs.refactor.trim()}
            className="btn px-5 py-2.5 bg-gradient-to-r from-amber-600 to-orange-600 hover:from-amber-500 hover:to-orange-500 text-white rounded-xl text-sm font-medium transition disabled:opacity-40">{loading ? <Spinner/> : '🔄 开始重构'}</button>
        </div>)}

        {/* Bug Fix */}
        {tab==='fix' && (<div className="space-y-3">
          <textarea value={inputs.fix} onChange={e=>setInput('fix',e.target.value)} placeholder="描述 Bug 或粘贴错误日志..." rows={3}
            className="w-full bg-surface-3 border border-border rounded-xl px-4 py-3 text-sm text-zinc-200 focus:outline-none focus:border-brand-500/40 resize-none placeholder-zinc-600 font-mono" />
          <input value={inputs.fixPath||''} onChange={e=>setInputs(p=>({...p,fixPath:e.target.value}))} placeholder="相关文件路径（可选）"
            className="w-full bg-surface-3 border border-border rounded-xl px-4 py-2.5 text-sm text-zinc-200 font-mono focus:outline-none focus:border-brand-500/40 placeholder-zinc-600" />
          <button onClick={() => runClaude({task: `/fix ${inputs.fix} ${inputs.fixPath||''}`, work_dir: workDir}, `Bug修复@${hostLabel}: ${inputs.fix.slice(0,25)}`)} disabled={loading || !inputs.fix.trim()}
            className="btn px-5 py-2.5 bg-gradient-to-r from-rose-600 to-red-600 hover:from-rose-500 hover:to-red-500 text-white rounded-xl text-sm font-medium transition disabled:opacity-40">{loading ? <Spinner/> : '🐛 诊断修复'}</button>
        </div>)}

        {/* Generate Tests */}
        {tab==='test' && (<div className="space-y-3">
          <input value={inputs.test} onChange={e=>setInput('test',e.target.value)}
            onKeyDown={e=>e.key==='Enter'&&inputs.test.trim()&&runClaude({task:`/test ${inputs.test}`, work_dir: workDir},`测试@${hostLabel}: ${inputs.test.split('/').pop()}`)}
            placeholder="输入文件路径，例如: agents/dev_agent.py"
            className="w-full bg-surface-3 border border-border rounded-xl px-4 py-2.5 text-sm text-zinc-200 font-mono focus:outline-none focus:border-brand-500/40 placeholder-zinc-600" />
          <button onClick={() => runClaude({task: `/test ${inputs.test}`, work_dir: workDir}, `测试@${hostLabel}: ${inputs.test.split('/').pop()}`)} disabled={loading || !inputs.test.trim()}
            className="btn px-5 py-2.5 bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500 text-white rounded-xl text-sm font-medium transition disabled:opacity-40">{loading ? <Spinner/> : '🧪 生成测试'}</button>
        </div>)}

        {/* Explain */}
        {tab==='explain' && (<div className="space-y-3">
          <input value={inputs.explain} onChange={e=>setInput('explain',e.target.value)}
            onKeyDown={e=>e.key==='Enter'&&inputs.explain.trim()&&runClaude({task:`/explain ${inputs.explain}`, work_dir: workDir},`解释@${hostLabel}: ${inputs.explain.split('/').pop()}`)}
            placeholder="输入文件路径，例如: agents/orchestrator.py"
            className="w-full bg-surface-3 border border-border rounded-xl px-4 py-2.5 text-sm text-zinc-200 font-mono focus:outline-none focus:border-brand-500/40 placeholder-zinc-600" />
          <button onClick={() => runClaude({task: `/explain ${inputs.explain}`, work_dir: workDir}, `解释@${hostLabel}: ${inputs.explain.split('/').pop()}`)} disabled={loading || !inputs.explain.trim()}
            className="btn px-5 py-2.5 bg-gradient-to-r from-cyan-600 to-blue-600 hover:from-cyan-500 hover:to-blue-500 text-white rounded-xl text-sm font-medium transition disabled:opacity-40">{loading ? <Spinner/> : '📖 深度解析'}</button>
        </div>)}

        {/* Git Management */}
        {tab==='git' && (<div className="space-y-4">
          <div className="grid grid-cols-5 gap-2">
            {[
              {action:'status', label:'状态', icon:'📊', color:'bg-blue-600'},
              {action:'log', label:'日志', icon:'📜', color:'bg-violet-600'},
              {action:'diff', label:'差异', icon:'📝', color:'bg-amber-600'},
              {action:'pull', label:'拉取', icon:'⬇️', color:'bg-emerald-600'},
              {action:'sync', label:'同步部署', icon:'🚀', color:'bg-rose-600'},
            ].map(g => (
              <button key={g.action} onClick={() => run('/api/dev/git', {action: g.action, repo: selectedRepo === '__custom__' ? '' : selectedRepo, path: selectedRepo === '__custom__' ? customRepoPath : '', deploy_to: g.action==='sync' ? sshHost : ''}, `Git ${g.label} [${activeRepo.label||selectedRepo}]`)}
                disabled={loading}
                className={`btn flex flex-col items-center gap-1 px-3 py-3 rounded-xl text-[11px] text-white font-medium transition disabled:opacity-40 ${g.color} hover:opacity-90`}>
                <span className="text-base">{g.icon}</span>
                <span>{g.label}</span>
              </button>
            ))}
          </div>
        </div>)}

        {/* Host Management */}
        {tab==='hosts' && (<div className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            {Object.entries(hostPresets).map(([k, h]) => {
              const tr = hostTestResults[k];
              const isOnline = tr ? tr.connected : h.connected;
              return (
              <div key={k} className={`rounded-xl border p-4 transition ${sshHost===k ? 'border-brand-500/40 bg-brand-950/10 ring-1 ring-brand-500/20' : isOnline ? 'border-emerald-500/30 bg-emerald-950/20' : 'border-border bg-surface-2'}`}>
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className={`w-2.5 h-2.5 rounded-full ${isOnline ? 'bg-emerald-400 animate-pulse' : 'bg-zinc-600'}`}></span>
                    <span className="text-sm font-medium text-zinc-200">{h.label || k}</span>
                    {sshHost===k && <span className="text-[9px] px-1.5 py-0.5 rounded bg-brand-600/20 text-brand-400">当前</span>}
                  </div>
                  <button onClick={() => testHost(k)} disabled={testingHost===k}
                    className={`text-[10px] px-2.5 py-1 rounded-lg border transition disabled:opacity-50 ${testingHost===k ? 'bg-amber-600/20 text-amber-400 border-amber-500/30' : 'bg-surface-3 text-zinc-400 border-border hover:border-border-light'}`}>
                    {testingHost===k ? <><Spinner /> 测试中</> : '测试连接'}
                  </button>
                </div>
                <div className="text-[11px] text-zinc-500 font-mono">{h.user || 'root'}@{h.host}</div>
                {tr && !tr.testing && (
                  <div className={`mt-2 px-2.5 py-1.5 rounded-lg text-[10px] font-mono leading-relaxed max-h-20 overflow-auto ${tr.connected ? 'bg-emerald-950/30 text-emerald-300 border border-emerald-500/20' : 'bg-rose-950/30 text-rose-300 border border-rose-500/20'}`}>
                    {tr.text || (tr.connected ? '连接成功' : '连接失败')}
                  </div>
                )}
                <div className="flex items-center gap-2 mt-2">
                  <button onClick={() => setSshHost(k)}
                    className={`text-[10px] px-2.5 py-1 rounded transition ${sshHost===k ? 'bg-brand-600/30 text-brand-300' : 'bg-brand-600/20 text-brand-400 hover:bg-brand-600/30'}`}>
                    {sshHost===k ? '✓ 已选中' : '选为开发主机'}
                  </button>
                  <button onClick={() => { setSshHost(k); setTab('ssh'); }}
                    className="text-[10px] px-2 py-1 rounded bg-surface-3 text-zinc-400 border border-border hover:border-border-light transition">SSH →</button>
                  <span className={`text-[10px] px-2 py-0.5 rounded ml-auto ${isOnline ? 'bg-emerald-500/20 text-emerald-400' : 'bg-zinc-700 text-zinc-500'}`}>
                    {isOnline ? '在线' : '离线'}
                  </span>
                </div>
              </div>
            );})}
          </div>
          <div className="flex gap-2">
            <button onClick={async () => { for (const k of Object.keys(hostPresets)) { await testHost(k); } }} disabled={!!testingHost}
              className="btn px-4 py-2 bg-brand-600 hover:bg-brand-500 text-white rounded-lg text-[11px] transition disabled:opacity-40">
              {testingHost ? <><Spinner /> 测试中...</> : '🔍 测试全部连接'}
            </button>
            <button onClick={loadEnv} disabled={envLoading}
              className="btn px-4 py-2 bg-surface-3 text-zinc-300 rounded-lg text-[11px] border border-border hover:border-border-light transition disabled:opacity-40">
              {envLoading ? <Spinner /> : '🔄'} 刷新环境
            </button>
          </div>
        </div>)}

        {/* SSH */}
        {tab==='ssh' && (<div className="space-y-3">
          <div className="flex gap-2">
            <input value={inputs.ssh} onChange={e=>setInput('ssh',e.target.value)}
              onKeyDown={e=>e.key==='Enter'&&inputs.ssh.trim()&&run('/api/dev/ssh',{command:inputs.ssh, host:sshHost},`SSH@${hostLabel}: ${inputs.ssh.slice(0,30)}`)}
              placeholder={`在 ${hostLabel} 上执行命令...`}
              className="flex-1 bg-surface-3 border border-border rounded-xl px-4 py-2.5 text-sm text-zinc-200 font-mono focus:outline-none focus:border-brand-500/40 placeholder-zinc-600" />
            <button onClick={() => run('/api/dev/ssh', {command: inputs.ssh, host: sshHost}, `SSH@${hostLabel}: ${inputs.ssh.slice(0,30)}`)} disabled={loading || !inputs.ssh.trim()}
              className="btn px-5 py-2.5 bg-surface-3 hover:bg-surface-4 text-zinc-200 rounded-xl text-sm border border-border hover:border-border-light transition disabled:opacity-40">{loading ? <Spinner/> : '执行'}</button>
          </div>
          <div className="flex gap-1.5 flex-wrap">
            {['uptime','df -h','free -m','ps aux --sort=-%mem | head','docker ps','ls -la'].map(cmd => (
              <button key={cmd} onClick={() => { setInput('ssh', cmd); run('/api/dev/ssh', {command: cmd, host: sshHost}, `SSH@${hostLabel}: ${cmd}`); }}
                className="text-[10px] px-2 py-1 rounded-lg bg-surface-3 text-zinc-500 hover:text-zinc-300 border border-border hover:border-border-light transition">{cmd}</button>
            ))}
          </div>
        </div>)}

        {/* Local */}
        {tab==='local' && (<div className="space-y-3">
          <div className="flex gap-2">
            <input value={inputs.local} onChange={e=>setInput('local',e.target.value)} onKeyDown={e=>e.key==='Enter'&&inputs.local.trim()&&run('/api/dev/local',{command:inputs.local},'本地: '+inputs.local.slice(0,30))}
              placeholder="例如: git status, ls -la src/"
              className="flex-1 bg-surface-3 border border-border rounded-xl px-4 py-2.5 text-sm text-zinc-200 font-mono focus:outline-none focus:border-brand-500/40 placeholder-zinc-600" />
            <button onClick={() => run('/api/dev/local', {command: inputs.local}, '本地: '+inputs.local.slice(0,30))} disabled={loading || !inputs.local.trim()}
              className="btn px-5 py-2.5 bg-surface-3 hover:bg-surface-4 text-zinc-200 rounded-xl text-sm border border-border hover:border-border-light transition disabled:opacity-40">{loading ? <Spinner/> : '执行'}</button>
          </div>
          <div className="flex gap-1.5 flex-wrap">
            {['git status','git log --oneline -10','ls -la','rg TODO --count'].map(cmd => (
              <button key={cmd} onClick={() => { setInput('local', cmd); run('/api/dev/local', {command: cmd}, '本地: '+cmd); }}
                className="text-[10px] px-2 py-1 rounded-lg bg-surface-3 text-zinc-500 hover:text-zinc-300 border border-border hover:border-border-light transition">{cmd}</button>
            ))}
          </div>
        </div>)}

        {/* Result */}
        {(loading || progress || result) && (
          <div className="mt-4 pt-4 border-t border-border">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[11px] font-semibold text-zinc-400">{loading ? '执行中...' : '执行结果'}</span>
              {result && <button onClick={() => {navigator.clipboard.writeText(result); toast('已复制','success');}} className="text-[10px] text-zinc-600 hover:text-zinc-400 transition">📋 复制</button>}
            </div>
            {loading && (
              <div className="flex items-center gap-3 py-3">
                <Spinner />
                <span className="text-sm text-zinc-500 animate-pulse">{progress || '正在处理中，请稍候...'}</span>
              </div>
            )}
            {result && (
              <div className="max-h-[600px] overflow-auto rounded-xl bg-surface-0 border border-border p-4">
                {['claude','review','refactor','fix','test','explain'].includes(tab)
                  ? React.createElement(MdBlock, {text: result})
                  : React.createElement('pre', {className: 'text-[13px] text-zinc-300 whitespace-pre-wrap break-all leading-relaxed font-mono'}, result)}
              </div>
            )}
          </div>
        )}
      </Card>
    </div>
  );
}


// ── API Usage View ───────────────────────────────────

function ApiUsageView() {
  const [data, setData] = useState(null);
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [configLoading, setConfigLoading] = useState(true);
  const [days, setDays] = useState(30);
  const toast = useContext(ToastContext);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiGet(`/api/usage?days=${days}&recent=50`);
      setData(r);
    } catch (e) { toast('加载用量数据失败','error'); }
    setLoading(false);
  }, [days]);

  const loadConfig = useCallback(async () => {
    setConfigLoading(true);
    try {
      const r = await apiGet('/api/llm/config');
      setConfig(r);
    } catch (e) {}
    setConfigLoading(false);
  }, []);

  useEffect(() => { load(); loadConfig(); }, [load, loadConfig]);

  const totals = data?.totals || {};
  const byProvider = data?.by_provider || {};
  const byTask = data?.by_task_type || {};
  const daily = data?.daily || [];
  const recentCalls = data?.recent_calls || [];
  const routerStatus = data?.router_status || {};

  const taskLabels = {brief:'快速简报',analysis:'深度分析',code:'代码生成',default:'通用'};
  const taskColors = {brief:'bg-amber-500',analysis:'bg-blue-500',code:'bg-violet-500',default:'bg-emerald-500'};

  return (
    <div className="flex-1 overflow-y-auto overflow-x-hidden p-6 animate-fade-in">
      <div className="flex items-center justify-between mb-5">
        <div>
          <h1 className="text-xl font-bold text-white">API 用量</h1>
          <p className="text-[12px] text-zinc-500 mt-1">LLM 调用统计 · 模型配置 · 费用监控</p>
        </div>
        <div className="flex items-center gap-2">
          <select value={days} onChange={e => setDays(Number(e.target.value))}
            className="bg-surface-2 border border-border rounded-lg px-3 py-1.5 text-[12px] text-zinc-300 focus:outline-none focus:border-brand-500/40">
            <option value={7}>近 7 天</option><option value={14}>近 14 天</option><option value={30}>近 30 天</option><option value={90}>近 90 天</option>
          </select>
          <button onClick={() => { load(); loadConfig(); }} disabled={loading}
            className="btn px-3 py-1.5 rounded-lg text-[11px] bg-surface-2 text-zinc-400 border border-border hover:border-border-light transition disabled:opacity-50">
            {loading ? <Spinner /> : '🔄'} 刷新
          </button>
        </div>
      </div>

      {loading && !data ? (
        <div className="flex items-center justify-center py-20"><Spinner /><span className="ml-3 text-zinc-500 text-sm">加载中...</span></div>
      ) : (
        <div className="space-y-5">
          {/* Summary cards */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {[
              {label:'总调用', value:(totals.total_calls||0).toLocaleString(), icon:'📡', color:'from-blue-500 to-indigo-600'},
              {label:'总费用', value:'¥'+(totals.total_cost_yuan||0).toFixed(2), icon:'💰', color:'from-amber-500 to-orange-600'},
              {label:'活跃 Provider', value:String(Object.keys(byProvider).length), icon:'🔌', color:'from-emerald-500 to-teal-600'},
              {label:'路由模式', value:routerStatus.preference ? `${routerStatus.preference.provider}` : '自动', icon:'🧠', color:'from-violet-500 to-purple-600'},
            ].map((c, i) => (
              <div key={i} className="relative overflow-hidden rounded-xl bg-surface-1 border border-border p-4 group hover:border-border-light transition">
                <div className={`absolute -top-3 -right-3 w-14 h-14 rounded-full bg-gradient-to-br ${c.color} opacity-5 group-hover:opacity-10 transition`} />
                <div className="flex items-center gap-3">
                  <span className="text-xl">{c.icon}</span>
                  <div>
                    <p className="text-lg font-bold text-zinc-100">{c.value}</p>
                    <p className="text-[10px] text-zinc-500">{c.label}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>

          {/* Provider & Task breakdown */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* By Provider */}
            <Card>
              <h3 className="text-sm font-semibold text-zinc-200 mb-3">按 Provider</h3>
              <div className="space-y-2">
                {Object.entries(byProvider).sort(([,a],[,b]) => (b.calls||0) - (a.calls||0)).map(([name, d]) => {
                  const maxCalls = Math.max(...Object.values(byProvider).map(v => v.calls || 0), 1);
                  const pct = ((d.calls||0) / maxCalls) * 100;
                  return (
                    <div key={name}>
                      <div className="flex items-center justify-between text-[12px] mb-1">
                        <span className="font-medium text-zinc-300 truncate">{name}</span>
                        <span className="text-zinc-500 tabular-nums">{(d.calls||0).toLocaleString()} 次 · ¥{(d.cost_yuan||0).toFixed(2)}</span>
                      </div>
                      <div className="h-1.5 rounded-full bg-surface-3 overflow-hidden">
                        <div className="h-full rounded-full bg-gradient-to-r from-brand-500 to-brand-400 transition-all" style={{width:`${pct}%`}} />
                      </div>
                    </div>
                  );
                })}
                {Object.keys(byProvider).length === 0 && <p className="text-[11px] text-zinc-600 text-center py-3">暂无数据</p>}
              </div>
            </Card>

            {/* By Task Type */}
            <Card>
              <h3 className="text-sm font-semibold text-zinc-200 mb-3">按任务类型</h3>
              <div className="space-y-2">
                {Object.entries(byTask).sort(([,a],[,b]) => (b.calls||0) - (a.calls||0)).map(([type, d]) => {
                  const maxCalls = Math.max(...Object.values(byTask).map(v => v.calls || 0), 1);
                  const pct = ((d.calls||0) / maxCalls) * 100;
                  return (
                    <div key={type}>
                      <div className="flex items-center justify-between text-[12px] mb-1">
                        <span className="flex items-center gap-2">
                          <span className={`w-2 h-2 rounded-full ${taskColors[type]||'bg-zinc-500'}`}></span>
                          <span className="font-medium text-zinc-300">{taskLabels[type]||type}</span>
                        </span>
                        <span className="text-zinc-500 tabular-nums">{(d.calls||0).toLocaleString()} 次</span>
                      </div>
                      <div className="h-1.5 rounded-full bg-surface-3 overflow-hidden">
                        <div className="h-full rounded-full bg-gradient-to-r from-emerald-500 to-teal-500 transition-all" style={{width:`${pct}%`}} />
                      </div>
                    </div>
                  );
                })}
                {Object.keys(byTask).length === 0 && <p className="text-[11px] text-zinc-600 text-center py-3">暂无数据</p>}
              </div>
            </Card>
          </div>

          {/* Model config */}
          {config && !config.error && (
            <Card>
              <h3 className="text-sm font-semibold text-zinc-200 mb-3">模型配置</h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {Object.entries(config.task_profiles || {}).map(([task, prof]) => (
                  <div key={task} className="rounded-xl border border-border bg-surface-2 p-3">
                    <div className="flex items-center gap-2 mb-2">
                      <span className={`w-2 h-2 rounded-full ${taskColors[task]||'bg-zinc-500'}`}></span>
                      <span className="text-[12px] font-semibold text-zinc-200">{taskLabels[task]||task}</span>
                    </div>
                    <p className="text-[10px] text-zinc-500 mb-2">{prof.desc}</p>
                    <div className="flex flex-wrap gap-1">
                      {(prof.chain||[]).map(([p,m], i) => (
                        <span key={i} className={`px-1.5 py-0.5 text-[9px] rounded ${i===0 ? 'bg-brand-600/20 text-brand-400' : 'bg-surface-3 text-zinc-500'}`}>
                          {p}/{m}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </Card>
          )}

          {/* Recent calls */}
          {recentCalls.length > 0 && (
            <Card>
              <h3 className="text-sm font-semibold text-zinc-200 mb-3">最近调用</h3>
              <div className="space-y-1 max-h-60 overflow-y-auto overflow-x-hidden">
                {recentCalls.slice(0, 30).map((c, i) => (
                  <div key={i} className="flex items-center gap-3 text-[11px] py-1.5 border-b border-border/30 last:border-0">
                    <span className={`w-1.5 h-1.5 rounded-full ${c.success !== false ? 'bg-emerald-400' : 'bg-rose-400'}`}></span>
                    <span className="text-zinc-400 truncate flex-1">{c.model || c.provider || '-'}</span>
                    <span className="text-zinc-600 truncate max-w-[120px]">{c.task_type || '-'}</span>
                    <span className="text-zinc-500 tabular-nums">{c.latency_ms ? c.latency_ms+'ms' : '-'}</span>
                    <span className="text-zinc-600 tabular-nums">{c.ts ? new Date(c.ts*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}) : '-'}</span>
                  </div>
                ))}
              </div>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
