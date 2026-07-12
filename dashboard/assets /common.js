/* ============================================================
   Support Triager — shared JS utilities
   Loaded by every page before the page's own inline script.
   ============================================================ */

const ST = (() => {

  const DARK = {
    paper_bgcolor: 'transparent',
    plot_bgcolor:  'transparent',
    font: { color: '#4b5563', size: 11, family: 'system-ui, -apple-system, sans-serif' },
    colorway: ['#6366f1','#22c55e','#f59e0b','#ef4444','#06b6d4','#d946ef'],
  };
  const DARK_EXP = { ...DARK, font: { color: '#6b7280', size: 12, family: 'system-ui, -apple-system, sans-serif' } };
  const CFG = { responsive: true, displayModeBar: false };
  const CFG_EXP = { responsive: true, displayModeBar: true, modeBarButtonsToRemove: ['lasso2d','select2d','toImage'], displaylogo: false };

  const SENT_COLORS = {
    positive: '#22c55e', neutral: '#6b7280',
    negative: '#f59e0b', highly_negative: '#ef4444',
  };
  const PRI_COLORS = { Critical: '#ef4444', High: '#f59e0b', Low: '#22c55e' };

  function floorHour(ts) { const d = new Date(ts); d.setMinutes(0,0,0); return d.toISOString(); }
  function fmtTime(ts) { if (!ts) return '—'; const d = new Date(ts); return d.toLocaleDateString() + ', ' + d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}); }
  function fmtScore(v) { const n = parseFloat(v); if (isNaN(n)) return '—'; return (n>=0?'+':'')+n.toFixed(2); }
  function sentCell(s) { const c = SENT_COLORS[s]||'#6b7280'; return `<span class="sent-wrap"><span class="sent-dot" style="background:${c}"></span>${s||'—'}</span>`; }
  function priBadge(p) { return `<span class="badge badge-${p}">${p||'—'}</span>`; }
  function escapeHtml(s) { return (s||'').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

  // Ticket data is fetched once per tab-session and cached, so navigating
  // between pages (Overview → Tickets → Analytics) doesn't refetch every time.
  const CACHE_KEY = 'st_tickets_cache_v1';
  const CACHE_TTL_MS = 20_000;

  async function fetchTickets(limit = 200, { force = false } = {}) {
    if (!force) {
      try {
        const cached = JSON.parse(sessionStorage.getItem(CACHE_KEY) || 'null');
        if (cached && Date.now() - cached.at < CACHE_TTL_MS) return cached.data;
      } catch (e) { /* ignore bad cache */ }
    }
    const res = await fetch(`/tickets/recent?limit=${limit}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    try { sessionStorage.setItem(CACHE_KEY, JSON.stringify({ at: Date.now(), data })); } catch (e) { /* storage full, skip */ }
    return data;
  }

  function invalidateCache() { try { sessionStorage.removeItem(CACHE_KEY); } catch (e) {} }

  function markActiveNav() {
    const file = (location.pathname.split('/').pop() || 'index.html');
    document.querySelectorAll('.sb-link[data-page]').forEach(a => {
      a.classList.toggle('active', a.dataset.page === file);
    });
  }

  function stampFooter(elId = 'last-refresh') {
    const el = document.getElementById(elId);
    if (el) el.textContent = 'Updated ' + new Date().toLocaleTimeString([],{hour:'2-digit',minute:'2-digit',second:'2-digit'});
  }

  return { DARK, DARK_EXP, CFG, CFG_EXP, SENT_COLORS, PRI_COLORS,
           floorHour, fmtTime, fmtScore, sentCell, priBadge, escapeHtml,
           fetchTickets, invalidateCache, markActiveNav, stampFooter };
})();

document.addEventListener('DOMContentLoaded', ST.markActiveNav);

