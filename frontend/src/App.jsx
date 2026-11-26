import { useEffect, useRef, useState } from 'react';

const API = import.meta.env.VITE_API_BASE ?? '';

function todayParts() {
  const now = new Date();
  const year = String(now.getFullYear());
  const month = String(now.getMonth() + 1).padStart(2, '0');
  const day = String(now.getDate()).padStart(2, '0');
  const quarter = Math.floor(now.getMonth() / 3) + 1;
  return { year, month, day, quarter };
}

function defaultPeriodForCycle(cycle) {
  const { year, month, day, quarter } = todayParts();
  const startYear = String(Number(year) - 5);
  if (cycle === 'M') return { start: `${startYear}01`, end: `${year}${month}` };
  if (cycle === 'Q') return { start: `${startYear}Q1`, end: `${year}Q${quarter}` };
  if (cycle === 'A') return { start: String(Number(year) - 10), end: year };
  return { start: `${startYear}0101`, end: `${year}${month}${day}` };
}

function coreIndicatorPresets() {
  const d = defaultPeriodForCycle('D');
  const m = defaultPeriodForCycle('M');
  const q = defaultPeriodForCycle('Q');
  return [
    { stat_code: '722Y001', item_code: '0101000', cycle: 'D', ...d, limit: 5000 },
    { stat_code: '731Y001', item_code: '', cycle: 'M', ...m, limit: 5000 },
    { stat_code: '901Y009', item_code: '', cycle: 'M', ...m, limit: 5000 },
    { stat_code: '200Y001', item_code: '', cycle: 'Q', ...q, limit: 5000 },
  ];
}

const blankSync = {
  stat_code: '722Y001',
  item_code: '0101000',
  cycle: 'D',
  ...defaultPeriodForCycle('D'),
  limit: 5000,
};

export default function App() {
  const [messages, setMessages] = useState([]);
  const [history, setHistory] = useState([]);
  const [input, setInput] = useState('');
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [modal, setModal] = useState(false);
  const [sync, setSync] = useState(blankSync);
  const [syncKey, setSyncKey] = useState('');
  const [syncing, setSyncing] = useState(false);
  const [notice, setNotice] = useState('');
  const [scrollPos, setScrollPos] = useState(0);
  const [maxScroll, setMaxScroll] = useState(1);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const feed = useRef(null);
  const inputBox = useRef(null);

  useEffect(() => { refreshStatus(); }, []);
  useEffect(() => {
    if (!feed.current) return;
    feed.current.scrollTop = feed.current.scrollHeight;
    setScrollPos(feed.current.scrollTop);
    setMaxScroll(Math.max(1, feed.current.scrollHeight - feed.current.clientHeight));
  }, [messages, busy, streaming]);
  useEffect(() => {
    const updateBounds = () => {
      if (!feed.current) return;
      setMaxScroll(Math.max(1, feed.current.scrollHeight - feed.current.clientHeight));
    };
    window.addEventListener('resize', updateBounds);
    return () => window.removeEventListener('resize', updateBounds);
  }, []);
  useEffect(() => { if (!input && inputBox.current) inputBox.current.style.height = 'auto'; }, [input]);

  async function refreshStatus() {
    try {
      const response = await fetch(`${API}/api/status`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || '상태를 불러오지 못했습니다.');
      setStatus(data);
    } catch (error) {
      setStatus({ error: error.message || '백엔드 연결이 끊겼습니다.' });
    }
  }

  function scrollTo(position) {
    if (!feed.current) return;
    feed.current.scrollTo({ top: position, behavior: 'smooth' });
  }
  function stepScroll(direction) {
    if (feed.current) scrollTo(feed.current.scrollTop + direction * feed.current.clientHeight * 0.8);
  }

  async function send(text = input.trim()) {
    if (!text || busy) return;
    setBusy(true);
    setStreaming(false);
    setInput('');
    const prior = history;
    setMessages(current => [...current, { role: 'user', content: text }]);
    let fullAnswer = '';
    let citations = [];
    let addedAssistant = false;

    try {
      const response = await fetch(`${API}/api/chat/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question: text, history: prior }),
      });
      if (!response.ok || !response.body) {
        const errBody = await response.json().catch(() => ({}));
        throw new Error(errBody.detail || errBody.error || '답변을 가져오지 못했습니다.');
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      const applyDelta = () => {
        setMessages(current => {
          if (!addedAssistant) {
            addedAssistant = true;
            return [...current, { role: 'assistant', content: fullAnswer, citations }];
          }
          const copy = current.slice();
          copy[copy.length - 1] = { role: 'assistant', content: fullAnswer, citations };
          return copy;
        });
      };

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        for (const rawLine of lines) {
          const line = rawLine.trim();
          if (!line) continue;
          const event = JSON.parse(line);
          if (event.type === 'meta') {
            citations = event.citations || [];
          } else if (event.type === 'delta') {
            setStreaming(true);
            fullAnswer += event.content || '';
            applyDelta();
          } else if (event.type === 'error') {
            throw new Error(event.detail || '스트리밍 중 오류가 발생했습니다.');
          }
        }
      }

      if (!addedAssistant) applyDelta();
      setHistory([...prior, { role: 'user', content: text }, { role: 'assistant', content: fullAnswer }].slice(-12));
    } catch (error) {
      setMessages(current => [...current, { role: 'assistant', content: `오류: ${error.message}` }]);
    } finally {
      setBusy(false);
      setStreaming(false);
      inputBox.current?.focus();
    }
  }

  function handleCycleChange(nextCycle) {
    const defaults = defaultPeriodForCycle(nextCycle);
    setSync(current => ({ ...current, cycle: nextCycle, ...defaults }));
  }

  function resolveApprovalToken() {
    if (status?.sync_enabled === false) {
      throw new Error('이 서버는 SYNC_ADMIN_KEY가 설정되지 않아 ECOS 동기화가 비활성화되어 있습니다.');
    }
    const token = status?.sync_requires_key ? syncKey.trim() : status?.approval_token;
    if (!token) {
      throw new Error(
        status?.sync_requires_key
          ? '동기화 관리자 키를 입력하세요.'
          : '승인 토큰을 가져오지 못했습니다. 백엔드를 다시 확인하세요.'
      );
    }
    return token;
  }

  async function syncData(event) {
    event.preventDefault();
    setSyncing(true);
    try {
      const token = resolveApprovalToken();
      const maxLimit = status?.max_sync_limit ?? 50000;
      const response = await fetch(`${API}/api/data/sync`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Local-Approval': token },
        body: JSON.stringify({ ...sync, limit: Math.max(1, Math.min(maxLimit, Number(sync.limit) || 5000)) }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || '동기화에 실패했습니다.');
      setNotice(`${(result.db_saved ?? result.saved).toLocaleString()}개 관측치를 DB와 벡터 저장소에 바로 저장했습니다.`);
      await refreshStatus();
    } catch (error) { setNotice(error.message); }
    finally { setSyncing(false); }
  }

  async function syncCoreBatch() {
    setSyncing(true);
    try {
      const token = resolveApprovalToken();
      const response = await fetch(`${API}/api/data/sync/batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Local-Approval': token },
        body: JSON.stringify({ items: coreIndicatorPresets() }),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || '일괄 동기화에 실패했습니다.');
      setNotice(`${result.series_count}개 주요 지표에서 총 ${(result.db_saved ?? result.saved).toLocaleString()}개 관측치를 DB와 벡터 저장소에 저장했습니다.`);
      await refreshStatus();
    } catch (error) { setNotice(error.message); }
    finally { setSyncing(false); }
  }

  const vectorCount = status?.vector_count ?? 0;
  const dbCount = status?.db_count ?? vectorCount;
  const maxLimit = status?.max_sync_limit ?? 50000;
  const modelReady = Boolean(status?.model?.connected && status?.model?.model_available);
  const embedReady = status?.model?.embed_model_available !== false;
  let healthText = 'Ollama 확인 필요';
  if (status?.error) healthText = status.error;
  else if (modelReady && !embedReady) healthText = '임베딩 모델(nomic-embed-text) 필요';
  else if (modelReady) healthText = '로컬 모델 연결됨';

  return <div className="app-shell">
    <aside className={`sidebar ${sidebarOpen ? 'sidebar-open' : ''}`}>
      <div className="brand"><span className="brand-icon">₩</span><span><b>경제 연구실</b><small>LOCAL ECONOMY AI</small></span></div>
      <button className="new-chat" onClick={() => { setMessages([]); setHistory([]); setInput(''); }}>＋　새 대화</button>
      <p className="section-label">대화</p><button className="thread active">경제 데이터 상담</button>
      <div className="sidebar-bottom"><button className="data-card" onClick={() => { setNotice(''); setModal(true); setSidebarOpen(false); }}>▤　ECOS 데이터 <small>DB {dbCount.toLocaleString()}건 · 벡터 {vectorCount.toLocaleString()}건</small></button><p className="privacy"><i /> 로컬 환경에서 처리</p></div>
    </aside>
    {sidebarOpen && <button className="drawer-overlay" aria-label="메뉴 닫기" onClick={() => setSidebarOpen(false)} />}
    <main className="workspace">
      <header className="topbar"><button className="mobile-menu" aria-label="메뉴" onClick={() => setSidebarOpen(!sidebarOpen)}>☰</button><span className="model-dot" /><span>{status?.model?.model || 'qwen3.5:4b'}</span><span className={`health ${modelReady && embedReady ? 'online' : 'offline'}`}>{healthText}</span></header>
      <section className="chat-area" ref={feed} onScroll={event => setScrollPos(event.currentTarget.scrollTop)}>
        {!messages.length && <div className="welcome"><span className="welcome-icon">₩</span><p className="eyebrow">LOCAL ECONOMY ASSISTANT</p><h1>무엇을 분석해 볼까요?</h1><p>한국은행 ECOS 경제 통계를 근거로 한국 경제를 살펴봅니다.<br />질문은 연결된 Ollama 모델에서만 처리됩니다.</p><div className="prompts">{['기준금리의 경제적 영향을 설명해줘', '물가와 금리의 관계를 알려줘', '한국 경제를 볼 때 중요한 지표는?'].map(item => <button key={item} onClick={() => send(item)}>{item}<span>↗</span></button>)}</div></div>}
        <div className="message-list">{messages.map((message, index) => <article className={`message ${message.role}`} key={index}><span className="avatar">{message.role === 'user' ? '나' : '₩'}</span><div><small>{message.role === 'user' ? '나' : '경제 연구실'}</small><p>{message.content}</p>{message.citations?.length > 0 && <div className="citations">{message.citations.map((source, i) => { let valid = false; try { const u = new URL(source.url); valid = u.protocol === 'https:' && u.hostname === 'ecos.bok.or.kr'; } catch {} return valid ? <a key={i} href={source.url} target="_blank" rel="noreferrer">{source.label} ↗</a> : null; })}</div>}</div></article>)}{busy && !streaming && <div className="working">경제 자료를 살펴보는 중…</div>}</div>
      </section>
      <div className="scroll-controls" aria-label="대화 스크롤 조작"><button aria-label="위로 이동" onClick={() => stepScroll(-1)}>⌃</button><input aria-label="대화 위치 드래그" type="range" min="0" max={maxScroll} value={Math.min(scrollPos, maxScroll)} onChange={event => { if (feed.current) feed.current.scrollTop = Number(event.target.value); }} /><button aria-label="아래로 이동" onClick={() => stepScroll(1)}>⌄</button></div>
      <footer><form className="composer" onSubmit={event => { event.preventDefault(); send(); }}><textarea ref={inputBox} rows="1" value={input} onChange={event => { setInput(event.target.value); event.target.style.height = 'auto'; event.target.style.height = `${Math.min(event.target.scrollHeight, 160)}px`; }} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send(); } }} placeholder="경제에 관해 무엇이든 물어보세요" /><div><small>Enter 전송 · Shift + Enter 줄바꿈</small><button disabled={busy || !input.trim()} aria-label="전송">↑</button></div></form><p className="disclaimer">AI는 실수할 수 있습니다. 주요 수치는 한국은행 원자료를 확인하세요.</p></footer>
    </main>
    {modal && <div className="modal-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) setModal(false); }}><section className="modal"><button className="close" onClick={() => setModal(false)} aria-label="닫기">×</button><p className="eyebrow">LOCAL VECTOR DATABASE</p><h2>ECOS 통계 가져오기</h2><p>가져오기 버튼을 누르면 입력한 통계 코드와 기간만 한국은행 ECOS에 요청해 SQLite DB와 벡터 DB에 즉시 저장합니다.{status?.ecos_key_configured === false && ' (현재 ECOS_API_KEY가 비어 있어 샘플 키 기준 최대 10건만 조회됩니다. 대량 수집은 .env에 개인 API 키를 설정하세요.)'}</p><form onSubmit={syncData}><div className="form-grid">{[['stat_code', '통계표 코드'], ['item_code', '항목 코드'], ['cycle', '주기'], ['start', '시작 기간'], ['end', '종료 기간'], ['limit', `최대 건수 (최대 ${maxLimit.toLocaleString()})`]].map(([key, label]) => <label key={key}>{label}{key === 'cycle' ? <select value={sync[key]} onChange={event => handleCycleChange(event.target.value)}><option value="D">일간 (D · YYYYMMDD)</option><option value="M">월간 (M · YYYYMM)</option><option value="Q">분기 (Q · YYYYQ1)</option><option value="A">연간 (A · YYYY)</option></select> : <input type={key === 'limit' ? 'number' : 'text'} min={key === 'limit' ? 1 : undefined} max={key === 'limit' ? maxLimit : undefined} placeholder={key === 'item_code' ? '비우면 전체 항목' : undefined} value={sync[key]} onChange={event => setSync({ ...sync, [key]: event.target.value })} />}</label>)}{status?.sync_requires_key && <label className="full-width">동기화 관리자 키<input type="password" value={syncKey} onChange={event => setSyncKey(event.target.value)} placeholder="SYNC_ADMIN_KEY 입력" /></label>}</div><button className="sync-button" disabled={syncing}>{syncing ? '동기화 중…' : 'ECOS 동기화 승인 및 시작'}</button><button type="button" className="batch-button" disabled={syncing} onClick={syncCoreBatch}>{syncing ? '동기화 중…' : '주요 4대 지표 한 번에 받기 (금리·환율·물가·GDP)'}</button></form>{notice && <p className="notice">{notice}</p>}<a href="https://ecos.bok.or.kr/api/" target="_blank" rel="noreferrer">한국은행 ECOS API 안내 ↗</a><p className="modal-foot">임베딩은 Ollama의 nomic-embed-text를 병렬·증분 처리합니다.</p></section></div>}
  </div>;
}
