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
  activeNewsTheme: 'all',  // 改为主题维度
  activePerk: 'all',
};

// 新闻主题分类规则（关键词命中 → 归到对应主题）
// 顺序很重要 — 先命中的优先
const NEWS_THEMES = [
  { key: 'ai',         label: 'AI 与出版',  kws: ['AI', '人工智能', '大模型', '智能创作'] },
  { key: 'ecom',       label: '电商博弈',   kws: ['618', '抵制', '京东', '拼多多', '电商', '促销', '当当', '直播带书'] },
  { key: 'transform',  label: '产业转型',   kws: ['转型', '破局', '洗牌', '数字化', '数字出版', '高质量发展'] },
  { key: 'store',      label: '实体书店',   kws: ['实体书店', '独立书店', '书店'] },
  { key: 'kids',       label: '少儿/童书',  kws: ['少儿', '童书', '儿童阅读', '绘本', '亲子'] },
  { key: 'policy',     label: '政策监管',   kws: ['政府奖', '新闻出版署', '条例', '监管', '政策', '促进条例'] },
  { key: 'newbook',    label: '新书发布',   kws: ['新书', '首发', '发布', '上市', '问世'] },
];

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

// 权益标签的颜色（视觉上要醒目，因为是核心对标信息）
const PERK_STYLES = {
  '亲签': 'bg-rose-100 text-rose-700 border-rose-200',
  '限量': 'bg-amber-100 text-amber-700 border-amber-200',
  '独家': 'bg-violet-100 text-violet-700 border-violet-200',
  '首发': 'bg-sky-100 text-sky-700 border-sky-200',
  '礼盒': 'bg-emerald-100 text-emerald-700 border-emerald-200',
  '赠品': 'bg-slate-100 text-slate-600 border-slate-200',
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

// 给一条新闻打主题标签（取第一个命中的主题；都不命中归为 'other'）
function classifyNewsTheme(n) {
  const text = (n.title || '') + ' ' + (n.summary || '');
  for (const t of NEWS_THEMES) {
    if (t.kws.some(k => text.includes(k))) return t.key;
  }
  return 'other';
}

function renderNews() {
  const list = $('#news-list');
  let items = STATE.news;
  if (STATE.activeNewsTheme !== 'all') {
    items = items.filter(n => classifyNewsTheme(n) === STATE.activeNewsTheme);
  }

  // 显示前 20 条 — 太多反而看不过来
  const SHOW_LIMIT = 20;
  const shown = items.slice(0, SHOW_LIMIT);
  const more = items.length - shown.length;

  if (!shown.length) {
    list.innerHTML = `<div class="p-6 text-sm text-slate-400">该主题暂无相关新闻</div>`;
    $('#news-meta').textContent = '0 条';
    return;
  }

  $('#news-meta').textContent =
    `${items.length} 条${more > 0 ? `（仅展示前 ${SHOW_LIMIT}）` : ''}`;

  list.innerHTML = shown.map(n => `
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

// 新闻主题筛选 — 用紧凑胶囊
function renderNewsFilters() {
  // 算每个主题的命中数
  const counts = { all: STATE.news.length };
  STATE.news.forEach(n => {
    const k = classifyNewsTheme(n);
    counts[k] = (counts[k] || 0) + 1;
  });

  // 按命中数排序，0 命中的主题不显示
  const themes = NEWS_THEMES
    .filter(t => (counts[t.key] || 0) > 0)
    .sort((a, b) => (counts[b.key] || 0) - (counts[a.key] || 0));

  // 头部"全部"
  const otherCount = counts.other || 0;
  const buttons = [
    { key: 'all', label: '全部', count: counts.all },
    ...themes.map(t => ({ key: t.key, label: t.label, count: counts[t.key] })),
  ];
  if (otherCount > 0) {
    buttons.push({ key: 'other', label: '其他', count: otherCount });
  }

  $('#news-filters').innerHTML = buttons.map(b => `
    <button class="filter-chip ${STATE.activeNewsTheme === b.key ? 'active' : ''}"
            data-theme="${b.key}">
      ${b.label}
      <span class="count">${b.count}</span>
    </button>
  `).join('');

  $$('#news-filters .filter-chip').forEach(btn => {
    btn.onclick = () => {
      STATE.activeNewsTheme = btn.dataset.theme;
      renderNewsFilters();
      renderNews();
    };
  });
}

// ========== 渲染：本周新书 ==========

function renderBookCard(b) {
  // 渐变占位封面
  const [c1, c2] = COVER_GRADIENTS[hashStr(b.title) % COVER_GRADIENTS.length];
  const safeTitle = b.title.replace(/'/g, '&#39;').replace(/"/g, '&quot;');
  const cover = b.cover
    ? `<img src="${b.cover}" alt="${safeTitle}"
            class="w-full h-full object-cover"
            referrerpolicy="no-referrer"
            onerror="this.replaceWith(Object.assign(document.createElement('div'),{
              className:'cover-placeholder w-full h-full',
              style:'--c1:${c1};--c2:${c2}',
              innerHTML:'<span>${safeTitle}</span>'
            }))">`
    : `<div class="cover-placeholder w-full h-full"
            style="--c1:${c1};--c2:${c2}">
          <span>${b.title}</span>
       </div>`;

  // 权益标签
  const perks = (b.perks || [])
    .slice(0, 3)
    .map(p => {
      const cls = PERK_STYLES[p] || 'bg-slate-100 text-slate-600 border-slate-200';
      return `<span class="text-[9px] px-1.5 py-0.5 rounded border ${cls} font-medium">
                ${p}
              </span>`;
    })
    .join(' ');

  // 排名徽章（仅当当）
  const rankBadge = b.rank
    ? `<span class="absolute top-2 left-2 bg-mint-600 text-white text-[10px] font-bold
                    px-1.5 py-0.5 rounded shadow-sm z-10">#${b.rank}</span>`
    : '';

  return `
    <a href="${b.url || '#'}" target="_blank" rel="noopener"
       class="card-hover bg-white rounded-2xl border border-mint-100
              overflow-hidden flex flex-col">
      <div class="aspect-[3/4] overflow-hidden bg-mint-50 relative">
        ${rankBadge}
        ${cover}
      </div>
      <div class="p-3 flex-1 flex flex-col">
        <h3 class="font-medium text-sm text-slate-900 leading-snug line-clamp-2">
          ${b.title}
        </h3>
        <p class="text-xs text-slate-500 mt-1 line-clamp-1">${b.author || '—'}</p>
        ${perks ? `<div class="flex flex-wrap gap-1 mt-2">${perks}</div>` : ''}
        <div class="mt-auto pt-2 flex items-center justify-between gap-2">
          <span class="text-[10px] px-1.5 py-0.5 rounded ${catClass(b.category)}">
            ${b.category || '其他'}
          </span>
          <div class="flex items-center gap-2">
            ${b.price ? `<span class="text-xs font-semibold text-mint-700">${b.price}</span>` : ''}
            ${b.rating
              ? `<span class="text-xs font-semibold text-lemon-500">★ ${b.rating}</span>`
              : ''}
          </div>
        </div>
      </div>
    </a>
  `;
}

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

  grid.innerHTML = items.map(renderBookCard).join('');
}

function renderBookFilters() {
  const cats = ['all', ...new Set(STATE.books.map(b => b.category).filter(Boolean))];
  // 算每个分类的数量
  const counts = { all: STATE.books.length };
  STATE.books.forEach(b => {
    const c = b.category;
    if (c) counts[c] = (counts[c] || 0) + 1;
  });

  $('#book-filters').innerHTML = cats.map(c => `
    <button class="filter-chip ${STATE.activeBookCategory === c ? 'active' : ''}"
            data-cat="${c}">
      ${c === 'all' ? '全部' : c}
      <span class="count">${counts[c] || 0}</span>
    </button>
  `).join('');
  $$('#book-filters .filter-chip').forEach(btn => {
    btn.onclick = () => {
      STATE.activeBookCategory = btn.dataset.cat;
      renderBookFilters();
      renderBooks();
    };
  });
}

// ========== 渲染：当当对标专区 ==========

function renderDangdangBenchmark() {
  const grid = $('#benchmark-grid');
  const filterBar = $('#benchmark-filters');
  const summary = $('#benchmark-summary');
  if (!grid) return;

  // 只看当当来源、且带权益标签的书
  const allDD = STATE.books.filter(b => b.source === '当当');
  const allPerk = allDD.filter(b => b.perks && b.perks.length > 0);

  // 顶部摘要
  if (allDD.length) {
    const ratio = (allPerk.length / allDD.length * 100).toFixed(0);
    const counter = {};
    allPerk.forEach(b => (b.perks || []).forEach(p => counter[p] = (counter[p] || 0) + 1));
    const breakdown = Object.entries(counter)
      .sort((a, b) => b[1] - a[1])
      .map(([p, c]) => `${p} ${c}`)
      .join(' · ');
    summary.innerHTML = `
      <span class="font-semibold text-slate-900">${allPerk.length}/${allDD.length}</span>
      本带差异化权益（${ratio}%）<span class="text-slate-400 mx-2">·</span>
      <span>${breakdown || '无标签'}</span>
    `;
  } else {
    summary.textContent = '暂无当当数据';
  }

  // 筛选标签 — 列出所有出现过的权益（按出现频率排序）
  const counter = {};
  allPerk.forEach(b => (b.perks || []).forEach(p => counter[p] = (counter[p] || 0) + 1));
  const perkOrder = ['all', ...Object.keys(counter).sort((a, b) => counter[b] - counter[a])];

  filterBar.innerHTML = perkOrder.map(p => {
    const label = p === 'all' ? '全部' : p;
    const count = p === 'all' ? allPerk.length : counter[p];
    const active = STATE.activePerk === p;
    return `<button class="filter-chip ${active ? 'active' : ''}" data-perk="${p}">
              ${label}
              <span class="count">${count}</span>
            </button>`;
  }).join('');

  $$('#benchmark-filters .filter-chip').forEach(btn => {
    btn.onclick = () => {
      STATE.activePerk = btn.dataset.perk;
      renderDangdangBenchmark();
    };
  });

  // 渲染卡片
  let items;
  if (STATE.activePerk === 'all') {
    items = allPerk;
  } else {
    items = allPerk.filter(b => (b.perks || []).includes(STATE.activePerk));
  }
  // 按榜单排名升序（排名越靠前越值得关注）
  items = items.slice().sort((a, b) => (a.rank || 99) - (b.rank || 99));

  if (!items.length) {
    grid.innerHTML = `<div class="col-span-full text-sm text-slate-400 py-8 text-center">
      暂无符合条件的当当权益版图书</div>`;
    return;
  }

  grid.innerHTML = items.map(renderBookCard).join('');
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
  renderDangdangBenchmark();   // 新增：当当对标专区
  renderBookFilters();
  renderBooks();
  renderStats();
  renderCategoryChart();
}

document.addEventListener('DOMContentLoaded', init);
