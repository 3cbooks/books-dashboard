/* ============================================================
   图书趋势看板 · 前端逻辑
   职责：加载 data/*.json → 渲染各模块 → 处理筛选交互
   ============================================================ */

// 数据缓存
const STATE = {
  books: [],
  news: [],
  insights: [],
  meta: {},
  activeBookCategory: 'all',
  activeNewsSource: 'all',
};

// 品类 → CSS class 映射
const CAT_CLASS = {
  '小说': 'tag-fiction',
  '文学': 'tag-lit',
  '社科': 'tag-nonfic',
  '人文': 'tag-nonfic',
  '经管': 'tag-business',
  '商业': 'tag-business',
  '科技': 'tag-sci',
  '科普': 'tag-sci',
  '童书': 'tag-kid',
  '少儿': 'tag-kid',
};

// 5 组占位封面渐变（无封面时按 hash 选）
const COVER_GRADIENTS = [
  ['#6ee7b7', '#fbbf24'], // 薄荷 → 柠黄
  ['#a7f3d0', '#fcd34d'],
  ['#34d399', '#fb923c'], // 绿 → 橙
  ['#86efac', '#f472b6'], // 翠 → 粉
  ['#67e8f9', '#a7f3d0'], // 青 → 薄荷
];

// ========== 工具函数 ==========

const $  = (sel, root=document) => root.querySelector(sel);
const $$ = (sel, root=document) => Array.from(root.querySelectorAll(sel));

function hashStr(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function catClass(category) {
  return CAT_CLASS[category] || 'tag-other';
}

function fmtRelative(iso) {
  // 简单的"几小时前/几天前"
  if (!iso) return '';
  const d = new Date(iso);
  const diffMs = Date.now() - d.getTime();
  const diffH  = Math.floor(diffMs / 3600_000);
  if (diffH < 1)   return '刚刚';
  if (diffH < 24)  return `${diffH} 小时前`;
  const diffD = Math.floor(diffH / 24);
  if (diffD < 30)  return `${diffD} 天前`;
  return d.toISOString().slice(0, 10);
}

async function loadJSON(path) {
  try {
    const res = await fetch(path, { cache: 'no-store' });
    if (!res.ok) throw new Error(res.status);
    return await res.json();
  } catch (err) {
    console.warn('[load failed]', path, err);
    return null;
  }
}

// ========== 渲染：洞察卡片 ==========

function renderInsights() {
  const grid = $('#insights-grid');
  if (!STATE.insights.length) {
    grid.innerHTML = `<div class="text-sm text-slate-400 col-span-full">
      今日尚未生成洞察 — 数据更新后会自动填充。</div>`;
    return;
  }
  grid.innerHTML = STATE.insights.map(it => `
    <div class="insight-card ${it.tone || ''}">
      <div class="flex items-start gap-2 mb-1.5">
        <span class="text-lg leading-none">${it.icon || '✨'}</span>
        <h3 class="font-semibold text-slate-900 leading-snug">${it.title}</h3>
      </div>
      <p class="text-sm text-slate-600 leading-relaxed">${it.body}</p>
      ${it.tag ? `<span class="inline-block mt-3 text-[11px] px-2 py-0.5
                                rounded-full bg-mint-50 text-mint-700">
                    ${it.tag}</span>` : ''}
    </div>
  `).join('');
}

// ========== 渲染：行业新闻 ==========

function renderNews() {
  const list = $('#news-list');
  const items = STATE.activeNewsSource === 'all'
    ? STATE.news
    : STATE.news.filter(n => n.source === STATE.activeNewsSource);

  if (!items.length) {
    list.innerHTML = `<div class="p-6 text-sm text-slate-400">暂无相关新闻</div>`;
    return;
  }

  list.innerHTML = items.map(n => `
    <a href="${n.url}" target="_blank" rel="noopener"
       class="block p-4 hover:bg-mint-50/50 transition group">
      <div class="flex items-start gap-3">
        <span class="source-chip mt-0.5 shrink-0">${n.source}</span>
        <div class="flex-1 min-w-0">
          <h3 class="text-sm font-medium text-slate-900
                     group-hover:text-mint-700 leading-snug line-clamp-2">
            ${n.title}
          </h3>
          ${n.summary ? `<p class="text-xs text-slate-500 mt-1
                                    leading-relaxed line-clamp-2">${n.summary}</p>` : ''}
        </div>
        <span class="text-xs text-slate-400 shrink-0 mt-0.5">
          ${fmtRelative(n.published_at)}
        </span>
      </div>
    </a>
  `).join('');
}

// 新闻来源筛选
function renderNewsFilters() {
  const sources = ['all', ...new Set(STATE.news.map(n => n.source))];
  $('#news-filters').innerHTML = sources.map(s => `
    <button class="filter-pill ${STATE.activeNewsSource === s ? 'active' : ''}"
            data-source="${s}">
      ${s === 'all' ? '全部' : s}
    </button>
  `).join('');
  $$('#news-filters .filter-pill').forEach(btn => {
    btn.onclick = () => {
      STATE.activeNewsSource = btn.dataset.source;
      renderNewsFilters();
      renderNews();
    };
  });
}

// ========== 渲染：本周新书 ==========

function renderBooks() {
  const grid = $('#books-grid');
  const items = STATE.activeBookCategory === 'all'
    ? STATE.books
    : STATE.books.filter(b => b.category === STATE.activeBookCategory);

  if (!items.length) {
    grid.innerHTML = `<div class="col-span-full text-sm text-slate-400 py-8 text-center">
      暂无相关图书</div>`;
    return;
  }

  grid.innerHTML = items.map(b => {
    const [c1, c2] = COVER_GRADIENTS[hashStr(b.title) % COVER_GRADIENTS.length];
    const cover = b.cover
      ? `<img src="${b.cover}" alt="${b.title}"
              class="w-full h-full object-cover"
              onerror="this.replaceWith(Object.assign(document.createElement('div'),{
                className:'cover-placeholder w-full h-full',
                style:'--c1:${c1};--c2:${c2}',
                innerHTML:'<span>${b.title.replace(/'/g, '&#39;')}</span>'
              }))">`
      : `<div class="cover-placeholder w-full h-full"
              style="--c1:${c1};--c2:${c2}">
            <span>${b.title}</span>
         </div>`;

    return `
      <a href="${b.url || '#'}" target="_blank" rel="noopener"
         class="card-hover bg-white rounded-2xl border border-mint-100
                overflow-hidden flex flex-col">
        <div class="aspect-[3/4] overflow-hidden bg-mint-50">
          ${cover}
        </div>
        <div class="p-3 flex-1 flex flex-col">
          <h3 class="font-medium text-sm text-slate-900 leading-snug line-clamp-2">
            ${b.title}
          </h3>
          <p class="text-xs text-slate-500 mt-1 line-clamp-1">${b.author || '—'}</p>
          <div class="mt-auto pt-2 flex items-center justify-between gap-2">
            <span class="text-[10px] px-1.5 py-0.5 rounded ${catClass(b.category)}">
              ${b.category || '其他'}
            </span>
            ${b.rating
              ? `<span class="text-xs font-semibold text-lemon-500">★ ${b.rating}</span>`
              : `<span class="text-[10px] text-slate-400">${b.source || ''}</span>`}
          </div>
        </div>
      </a>
    `;
  }).join('');
}

function renderBookFilters() {
  const cats = ['all', ...new Set(STATE.books.map(b => b.category).filter(Boolean))];
  $('#book-filters').innerHTML = cats.map(c => `
    <button class="filter-pill ${STATE.activeBookCategory === c ? 'active' : ''}"
            data-cat="${c}">
      ${c === 'all' ? '全部' : c}
    </button>
  `).join('');
  $$('#book-filters .filter-pill').forEach(btn => {
    btn.onclick = () => {
      STATE.activeBookCategory = btn.dataset.cat;
      renderBookFilters();
      renderBooks();
    };
  });
}

// ========== 渲染：数据快照 + 图表 ==========

function renderStats() {
  $('#stat-new-books').textContent  = STATE.meta.new_books_week ?? STATE.books.length;
  $('#stat-news-count').textContent = STATE.news.length;

  const trend = STATE.meta.new_books_trend_pct;
  const trendEl = $('#stat-new-books-trend');
  if (trend == null) {
    trendEl.textContent = '—';
  } else if (trend > 0) {
    trendEl.textContent = `↑ ${trend}%`;
    trendEl.className = 'trend-up font-bold';
  } else if (trend < 0) {
    trendEl.textContent = `↓ ${Math.abs(trend)}%`;
    trendEl.className = 'trend-down font-bold';
  } else {
    trendEl.textContent = `→ 0%`;
    trendEl.className = 'trend-flat font-bold';
  }
}

function renderCategoryChart() {
  const ctx = $('#category-chart');
  if (!ctx) return;
  const counts = {};
  STATE.books.forEach(b => {
    const k = b.category || '其他';
    counts[k] = (counts[k] || 0) + 1;
  });
  const labels = Object.keys(counts);
  const data   = Object.values(counts);
  const palette = ['#10b981', '#fbbf24', '#34d399', '#fb923c',
                   '#a78bfa', '#f472b6', '#60a5fa', '#94a3b8'];

  // 销毁旧实例（如果存在）
  if (window._catChart) window._catChart.destroy();

  window._catChart = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{
        data,
        backgroundColor: labels.map((_, i) => palette[i % palette.length]),
        borderWidth: 2,
        borderColor: '#fff',
      }],
    },
    options: {
      plugins: {
        legend: {
          position: 'bottom',
          labels: { boxWidth: 10, font: { size: 11 } },
        },
      },
      cutout: '60%',
    },
  });
}

// ========== 主流程 ==========

async function init() {
  // 并行加载所有数据
  const [meta, books, news, insights] = await Promise.all([
    loadJSON('data/meta.json'),
    loadJSON('data/books.json'),
    loadJSON('data/news.json'),
    loadJSON('data/insights.json'),
  ]);

  STATE.meta     = meta     || {};
  STATE.books    = books    || [];
  STATE.news     = news     || [];
  STATE.insights = insights || [];

  // Header 更新时间
  if (STATE.meta.updated_at) {
    $('#last-update').textContent = fmtRelative(STATE.meta.updated_at);
  } else {
    $('#last-update').textContent = '—';
  }

  // 渲染各模块
  renderInsights();
  renderNewsFilters();
  renderNews();
  renderBookFilters();
  renderBooks();
  renderStats();
  renderCategoryChart();
}

document.addEventListener('DOMContentLoaded', init);
