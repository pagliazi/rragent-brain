const {useState, useEffect, useRef, useCallback, useMemo, createContext, useContext} = React;

// ── Context ──────────────────────────────────────────
const ToastContext = createContext();

function ToastProvider({children}) {
  const [toasts, setToasts] = useState([]);
  const addToast = useCallback((msg, type='info') => {
    const id = Date.now();
    setToasts(prev => [...prev, {id, msg, type}]);
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 3000);
  }, []);
  return (
    <ToastContext.Provider value={addToast}>
      {children}
      <div className="fixed top-4 right-4 z-50 flex flex-col gap-2.5 pointer-events-none">
        {toasts.map(t => (
          <div key={t.id} className={`pointer-events-auto animate-toast-in px-4 py-3 rounded-xl text-sm font-medium shadow-2xl border backdrop-blur-xl flex items-center gap-2.5
            ${t.type==='success'?'bg-emerald-500/15 border-emerald-500/20 text-emerald-300 shadow-emerald-500/5':
              t.type==='error'?'bg-red-500/15 border-red-500/20 text-red-300 shadow-red-500/5':
              'bg-brand-500/15 border-brand-500/20 text-brand-400 shadow-brand-500/5'}`}>
            <span className={`w-5 h-5 rounded-full flex items-center justify-center text-xs flex-shrink-0 ${
              t.type==='success'?'bg-emerald-500/20':'bg-red-500/20'
            }`}>{t.type==='success'?'✓':t.type==='error'?'✗':'ℹ'}</span>
            <span>{t.msg}</span>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

// ── Auth Context ─────────────────────────────────────
const AuthContext = createContext(null);

const ROLE_LABELS = {admin:'管理员', user:'用户', viewer:'访客'};
const ROLE_COLORS = {admin:'text-rose-400', user:'text-brand-400', viewer:'text-zinc-400'};

// ── Agent Metadata (known agents, fallback for unknown) ──
const AGENT_META = {
  orchestrator:{icon:'🎯',label:'编排器',desc:'智能分发中心',cat:'core'},
  general:{icon:'🧠',label:'通用助手',desc:'知识问答 · 翻译写作',cat:'core'},
  market:{icon:'📊',label:'行情',desc:'实时行情数据',cat:'quant'},
  analysis:{icon:'🔬',label:'分析师',desc:'市场深度分析',cat:'quant'},
  news:{icon:'📰',label:'新闻',desc:'财经资讯监控',cat:'quant'},
  strategist:{icon:'📈',label:'策略师',desc:'投资策略研判',cat:'quant'},
  backtest:{icon:'📐',label:'回测',desc:'量化策略回测',cat:'quant'},
  dev:{icon:'💻',label:'开发',desc:'远程开发助手',cat:'tool'},
  browser:{icon:'🌐',label:'浏览器',desc:'网页自动化',cat:'tool'},
  desktop:{icon:'🖥️',label:'桌面',desc:'桌面操控',cat:'tool'},
  apple:{icon:'🍎',label:'Apple',desc:'Apple 生态集成',cat:'tool'},
  monitor:{icon:'🔔',label:'监控',desc:'基础设施告警',cat:'ops'},
  intraday:{icon:'⏱️',label:'盘中',desc:'盘中实时扫描',cat:'quant'},
};

const CATEGORIES = [
  {id:'core',label:'核心智能',icon:'🧠',color:'brand'},
  {id:'quant',label:'量化投研',icon:'📈',color:'emerald'},
  {id:'tool',label:'工具集成',icon:'🛠️',color:'amber'},
  {id:'ops',label:'系统运维',icon:'🔧',color:'cyan'},
];

function getAgentMeta(name) {
  if (AGENT_META[name]) return AGENT_META[name];
  return {icon:'🤖', label:name, desc:'Agent', cat:'ops'};
}

const CHAT_TARGETS = {
  manager:{icon:'🎯',label:'Manager',desc:'智能管家 · 自动分发'},
  analysis:{icon:'🔬',label:'分析师',desc:'市场深度分析'},
  market:{icon:'📊',label:'行情',desc:'实时行情数据'},
  news:{icon:'📰',label:'新闻',desc:'财经资讯监控'},
  strategist:{icon:'📈',label:'策略师',desc:'投资策略研判'},
  general:{icon:'🧠',label:'通用助手',desc:'知识问答 · 翻译写作'},
  backtest:{icon:'📐',label:'回测',desc:'量化策略回测'},
  dev:{icon:'💻',label:'开发',desc:'远程开发助手'},
  browser:{icon:'🌐',label:'浏览器',desc:'网页自动化'},
  desktop:{icon:'🖥️',label:'桌面',desc:'桌面操作'},
  apple:{icon:'🍎',label:'Apple',desc:'Apple 生态'},
};

function getChatMeta(target) {
  return CHAT_TARGETS[target] || {icon:'🤖', label:target, desc:'Agent'};
}

const QUICK = [
  {cmd:'zt',label:'涨停板',icon:'📊',desc:'今日涨停股票'},
  {cmd:'lb',label:'连板股',icon:'🔗',desc:'连续涨停个股'},
  {cmd:'bk',label:'板块',icon:'🏷️',desc:'热门板块排行'},
  {cmd:'hot',label:'热股',icon:'🔥',desc:'资金关注热股'},
  {cmd:'summary',label:'市场摘要',icon:'📋',desc:'全市场概览'},
  {cmd:'news',label:'最新新闻',icon:'📰',desc:'财经资讯'},
  {cmd:'strategy',label:'策略建议',icon:'📈',desc:'AI 投资建议'},
  {cmd:'quant 今日热点策略',label:'量化研发',icon:'📐',desc:'量化回测流水线'},
];

const PRESETS = [
  {id:'morning_prep',name:'盘前准备',icon:'🌅',desc:'新闻→行情→分析→策略→推送',steps:5},
  {id:'close_review',name:'收盘复盘',icon:'🌆',desc:'行情→新闻→分析→复盘→风险',steps:7},
  {id:'deep_research',name:'深度研究',icon:'🔬',desc:'全量行情→新闻→深度分析',steps:4},
  {id:'quant_research',name:'量化策略研发',icon:'📐',desc:'Alpha→Coder→Backtest→Risk→PM',steps:4},
  {id:'memory_maintenance',name:'记忆维护',icon:'🧹',desc:'健康→提醒→SOUL→卫生',steps:5},
];

// ── Auth Helpers ─────────────────────────────────────
function getToken() { return localStorage.getItem('openclaw_token') || ''; }
function setToken(t) { if (t) localStorage.setItem('openclaw_token', t); else localStorage.removeItem('openclaw_token'); }
function authHeaders() {
  const t = getToken();
  const h = {'Content-Type':'application/json'};
  if (t) h['Authorization'] = 'Bearer ' + t;
  return h;
}

async function apiPost(url, body) {
  try {
    const r = await fetch(url, {method:'POST', headers:authHeaders(), body:JSON.stringify(body)});
    if (r.status === 401) { window.__onAuthExpired?.(); return {error:true, result:'登录已过期'}; }
    if (!r.ok) return {result:'HTTP '+r.status, error:true};
    return await r.json();
  } catch(e) {return {result:e.message, error:true};}
}
async function apiGet(url) {
  try {
    const r = await fetch(url, {headers: authHeaders()});
    if (r.status === 401) { window.__onAuthExpired?.(); return {error:true}; }
    if (!r.ok) return {error:true};
    return await r.json();
  } catch(e) {return {error:true};}
}
async function apiPut(url, body) {
  try {
    const r = await fetch(url, {method:'PUT', headers:authHeaders(), body:JSON.stringify(body)});
    if (r.status === 401) { window.__onAuthExpired?.(); return {error:true}; }
    if (!r.ok) return {error:true, result:'HTTP '+r.status};
    return await r.json();
  } catch(e) {return {error:true};}
}
async function apiDelete(url) {
  try {
    const r = await fetch(url, {method:'DELETE', headers:authHeaders()});
    if (r.status === 401) { window.__onAuthExpired?.(); return {error:true}; }
    if (!r.ok) return {error:true};
    return await r.json();
  } catch(e) {return {error:true};}
}

function streamChat(message, target, onEvent) {
  return fetch('/api/chat', {method:'POST', headers:authHeaders(), body:JSON.stringify({message, target: target || 'manager'})})
    .then(function(r) {
      if (r.status === 401) { window.__onAuthExpired?.(); return; }
      var reader = r.body.getReader();
      var decoder = new TextDecoder();
      var buffer = '';
      function pump() {
        return reader.read().then(function(result) {
          if (result.done) return;
          buffer += decoder.decode(result.value, {stream:true});
          var lines = buffer.split('\n');
          buffer = lines.pop() || '';
          lines.forEach(function(line) {
            if (line.indexOf('data: ') === 0) {
              try { onEvent(JSON.parse(line.slice(6))); } catch(e) {}
            }
          });
          return pump();
        });
      }
      return pump();
    });
}

function genId() { return 'c_' + Date.now().toString(36) + Math.random().toString(36).slice(2,6); }

// ── Shared Components ────────────────────────────────

function Spinner({size}) {
  const s = size || 4;
  return (
    <div className="relative inline-flex">
      <div className={`w-${s} h-${s} border-2 border-brand-500/20 border-t-brand-500 rounded-full animate-spin`}></div>
      <div className={`absolute inset-0 w-${s} h-${s} border-2 border-transparent border-b-brand-400/30 rounded-full animate-spin`} style={{animationDirection:'reverse',animationDuration:'1.5s'}}></div>
    </div>
  );
}

function StatusDot({status, size}) {
  const s = size || 2;
  const colors = {
    online:'bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,.5)]',
    slow:'bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,.4)]',
    sleeping:'bg-blue-500/60',
    offline:'bg-zinc-600',error:'bg-red-500'
  };
  return <span className={`inline-block w-${s} h-${s} rounded-full ${colors[status]||colors.offline} flex-shrink-0`}></span>;
}

function Card({children, className, onClick, glow}) {
  return (
    <div onClick={onClick}
      className={`bg-surface-2 rounded-2xl border border-border p-5 card-hover gradient-border ${onClick?'cursor-pointer active:scale-[.98]':''} ${glow?'glow-brand':''} ${className||''}`}>
      {children}
    </div>
  );
}

function LoadingBlock({text}) {
  return (
    <div className="flex flex-col items-center gap-3 py-10 justify-center text-zinc-500 animate-fade-in">
      <Spinner size={5} />
      <span className="text-sm font-medium">{text||'加载中...'}</span>
    </div>
  );
}

function SkeletonBlock({lines, className}) {
  const n = lines || 3;
  return (
    <div className={`space-y-3 animate-fade-in ${className||''}`}>
      {Array.from({length: n}).map((_, i) => (
        <div key={i} className="skeleton h-4 rounded-lg" style={{width: `${85 - i * 15}%`, animationDelay: `${i * 0.1}s`}}></div>
      ))}
    </div>
  );
}

function ViewWrapper({children, className}) {
  return <div className={`view-enter flex-1 h-full overflow-auto ${className||''}`}>{children}</div>;
}

function DataBlock({data, loading, placeholder}) {
  if (loading) return <LoadingBlock />;
  if (!data) return <div className="text-zinc-600 text-sm py-4 text-center">{placeholder||'暂无数据'}</div>;
  return <pre className="text-[13px] text-zinc-300 leading-[1.7] font-mono">{data}</pre>;
}

function ExpandableCode({code, label, defaultExpanded = true}) {
  const lines = code ? code.split('\n') : [];
  const [expanded, setExpanded] = React.useState(defaultExpanded);
  if (!code) return null;
  const copyCode = () => { navigator.clipboard.writeText(code).catch(()=>{}); };
  return (
    <div className="mt-1">
      <div className="flex items-center gap-2 mb-1">
        {label && <span className="text-[9px] text-zinc-600">{label}</span>}
        <span className="text-[9px] text-zinc-600">{lines.length} 行 · {code.length} 字符</span>
        <button onClick={copyCode} className="text-[9px] text-zinc-500 hover:text-zinc-200 transition px-1 py-0.5 rounded bg-surface-3 border border-border/50">复制</button>
        <button onClick={() => setExpanded(!expanded)} className="text-[9px] text-brand-400 hover:text-brand-300 transition">
          {expanded ? '收起' : '展开代码'}
        </button>
      </div>
      {expanded && (
        <pre className="text-[10px] text-zinc-400 font-mono leading-[1.6] overflow-auto bg-surface-0/50 rounded-lg p-2 border border-border/50 max-h-[500px]">
          {code}
        </pre>
      )}
      {!expanded && (
        <div className="text-[9px] text-zinc-600 italic pl-1">代码已折叠（{lines.length} 行）</div>
      )}
    </div>
  );
}

function ExpandableDetail({text, label}) {
  const [expanded, setExpanded] = useState(false);
  if (!text) return null;
  const isLong = text.length > 300;
  return (
    <div className="mt-1">
      {label && <span className="text-[9px] text-zinc-600 mb-1 block">{label}</span>}
      <pre className={`text-[10px] text-red-300/70 font-mono leading-[1.5] overflow-auto bg-red-950/20 rounded-lg p-2 border border-red-900/20 ${expanded ? 'max-h-[400px]' : 'max-h-20'}`}>
        {expanded ? text : text.slice(0, 300)}{!expanded && isLong ? '...' : ''}
      </pre>
      {isLong && (
        <button onClick={() => setExpanded(!expanded)} className="text-[9px] text-red-400 hover:text-red-300 mt-1 transition">
          {expanded ? '收起' : `展开全部 (${text.length} 字符)`}
        </button>
      )}
    </div>
  );
}
