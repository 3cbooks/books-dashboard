/* ============================================================
   图书趋势看板 · 前端逻辑
   职责：加载 data/*.json → 渲染各模块 → 处理筛选交互
   ============================================================ */

// 数据缓存
const STATE = {
  books: [],
  newBooks: [],   // 预售书 / 真新书
  news: [],
  insights: [],
  meta: {},
  activeBookCategory: 'all',
  activeNewsTheme: 'all',  // 改为主题维度
  activePerk: 'all',
  activeUpcomingCat: 'all',
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
  grid.innerHTML = STATE.insights.map(it => {
    const inner = `
      <div class="flex items-start gap-2 mb-1.5">
        <span class="text-lg leading-none">${it.icon || '✨'}</span>
        <h3 class="font-semibold text-slate-900 leading-snug">${it.title}</h3>
      </div>
      <p class="text-sm text-slate-600 leading-relaxed">${it.body}</p>
      <div class="flex items-center justify-between mt-3">
        ${it.tag ? `<span class="text-[11px] px-2 py-0.5
                                  rounded-full bg-mint-50 text-mint-700">
                      ${it.tag}</span>` : '<span></span>'}
        ${it.anchor ? `<span class="text-[11px] text-mint-600 font-medium
                                    opacity-0 group-hover:opacity-100 transition">
                         查看详情 →
                       </span>` : ''}
      </div>
    `;
    // 有 anchor 的用 <a> 渲染（可跳转 + hover 浮起），没有的用 <div>
    if (it.anchor) {
      return `<a href="#${it.anchor}"
                 class="insight-card group cursor-pointer hover:shadow-md
                        hover:-translate-y-0.5 hover:border-mint-300 ${it.tone || ''}
                        block transition">
                ${inner}
              </a>`;
    }
    return `<div class="insight-card ${it.tone || ''}">${inner}</div>`;
  }).join('');
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

  // 默认展示 10 条，"展开更多"后展示前 30
  const DEFAULT_LIMIT = 10;
  const EXPANDED_LIMIT = 30;
  const expanded = STATE._newsExpanded === true;
  const showCount = expanded ? EXPANDED_LIMIT : DEFAULT_LIMIT;
  const shown = items.slice(0, showCount);
  const hidden = items.length - shown.length;

  if (!shown.length) {
    list.innerHTML = `<div class="p-6 text-sm text-slate-400">该主题暂无相关新闻</div>`;
    $('#news-meta').textContent = '0 条';
    return;
  }

  $('#news-meta').textContent = `共 ${items.length} 条`;

  const newsHtml = shown.map(n => `
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

  // "展开/收起"按钮
  let toggleBtn = '';
  if (items.length > DEFAULT_LIMIT) {
    if (!expanded) {
      toggleBtn = `<button id="news-toggle"
        class="block w-full py-3 text-sm text-mint-700 hover:bg-mint-50/50
               transition font-medium">
        展开更多 (+${items.length - DEFAULT_LIMIT}) ↓
      </button>`;
    } else {
      toggleBtn = `<button id="news-toggle"
        class="block w-full py-3 text-sm text-slate-500 hover:bg-mint-50/50
               transition font-medium">
        收起 ↑
      </button>`;
    }
  }

  list.innerHTML = newsHtml + toggleBtn;

  const tg = document.getElementById('news-toggle');
  if (tg) {
    tg.onclick = () => {
      STATE._newsExpanded = !expanded;
      renderNews();
    };
  }
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

  // 出版状态徽章（仅 dangdang_new 来的书）
  let pubBadge = '';
  if (b.pub_status === 'preorder') {
    const days = -b.days_since_pub;
    pubBadge = `<span class="absolute top-2 right-2 bg-violet-500 text-white
                  text-[10px] font-semibold px-1.5 py-0.5 rounded shadow-sm z-10">
                  距上市${days}天</span>`;
  } else if (b.pub_status === 'fresh') {
    pubBadge = `<span class="absolute top-2 right-2 bg-rose-500 text-white
                  text-[10px] font-semibold px-1.5 py-0.5 rounded shadow-sm z-10">
                  本周新书</span>`;
  } else if (b.pub_status === 'recent') {
    pubBadge = `<span class="absolute top-2 right-2 bg-amber-500 text-white
                  text-[10px] font-semibold px-1.5 py-0.5 rounded shadow-sm z-10">
                  近期新书</span>`;
  }

  // 豆瓣校验徽章（左下角）
  let verifyBadge = '';
  if (b.verify_status === 'verified' && b.douban_pubdate) {
    const year = b.douban_pubdate.slice(0, 4);
    verifyBadge = `<span class="absolute bottom-2 left-2 bg-white/95 text-mint-700
                    text-[9px] font-medium px-1.5 py-0.5 rounded border border-mint-200 z-10"
                    title="豆瓣记录的真实出版日期：${b.douban_pubdate}">
                    豆瓣✓ ${year}</span>`;
  } else if (b.verify_status === 'unverified') {
    verifyBadge = `<span class="absolute bottom-2 left-2 bg-white/90 text-slate-400
                    text-[9px] px-1.5 py-0.5 rounded border border-slate-200 z-10"
                    title="豆瓣未找到该书，出版日期未经独立校验">
                    豆瓣?</span>`;
  }

  return `
    <a href="${b.url || '#'}" target="_blank" rel="noopener"
       class="card-hover bg-white rounded-2xl border border-mint-100
              overflow-hidden flex flex-col">
      <div class="aspect-[3/4] overflow-hidden bg-mint-50 relative">
        ${rankBadge}
        ${pubBadge}
        ${verifyBadge}
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

  // 默认展示 10 本（两排），其他折叠
  const DEFAULT_LIMIT = 10;
  const expanded = STATE._booksExpanded === true;
  const shown = expanded ? items : items.slice(0, DEFAULT_LIMIT);
  const hiddenCount = items.length - shown.length;

  const cardsHtml = shown.map(renderBookCard).join('');

  let toggleHtml = '';
  if (items.length > DEFAULT_LIMIT) {
    if (!expanded) {
      toggleHtml = `
        <button id="books-toggle"
                class="col-span-full mt-2 py-2.5 text-sm text-mint-700 font-medium
                       border border-mint-100 rounded-xl bg-white hover:bg-mint-50/50
                       transition">
          展开更多 (+${hiddenCount}) ↓
        </button>`;
    } else {
      toggleHtml = `
        <button id="books-toggle"
                class="col-span-full mt-2 py-2.5 text-sm text-slate-500 font-medium
                       border border-mint-100 rounded-xl bg-white hover:bg-mint-50/50
                       transition">
          收起 ↑
        </button>`;
    }
  }

  grid.innerHTML = cardsHtml + toggleHtml;

  const tg = document.getElementById('books-toggle');
  if (tg) {
    tg.onclick = () => {
      STATE._booksExpanded = !expanded;
      renderBooks();
    };
  }
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

// ========== 渲染：当当 vs 京东 权益对标 ==========

const GAP_LABELS = {
  'no_jd':    { label: '完全缺口',     icon: '❌', tone: 'rose',    desc: '京东根本不卖' },
  'no_self':  { label: '自营空白',     icon: '⚠️', tone: 'amber',   desc: '京东只 POP 在售' },
  'perk_gap': { label: '权益差距',     icon: '🎯', tone: 'violet',  desc: '京东自营有但权益少' },
  'none':     { label: '无明显缺口',   icon: '✅', tone: 'emerald', desc: '京东自营有同等权益' },
};

function renderDangdangBenchmark() {
  const grid = $('#benchmark-grid');
  const summary = $('#benchmark-summary');
  const filterBar = $('#benchmark-filters');
  if (!grid) return;

  const items = STATE.benchmark || [];
  if (!items.length) {
    summary.textContent = '暂无对标数据';
    filterBar.innerHTML = '';
    grid.innerHTML = `<div class="text-sm text-slate-400 py-8 text-center">
      数据更新后会自动填充</div>`;
    return;
  }

  // 按 gap_level 统计
  const counts = {};
  items.forEach(r => counts[r.gap_level] = (counts[r.gap_level] || 0) + 1);
  const gapKeys = ['no_jd', 'no_self', 'perk_gap', 'none'];

  // 摘要：突出"有缺口的"
  const withGap = items.filter(r => r.gap_level !== 'none').length;
  summary.innerHTML = `
    <span class="font-semibold text-slate-900">${withGap}/${items.length}</span> 本带权益的当当书，
    京东侧存在对标缺口
    <span class="text-slate-400 mx-2">·</span>
    缺口类型：${gapKeys
      .filter(k => counts[k])
      .map(k => `<span class="text-${GAP_LABELS[k].tone}-700">${GAP_LABELS[k].label} ${counts[k]}</span>`)
      .join(' · ')}
  `;

  // 筛选器：缺口类型
  const activeGap = STATE.activeGap || 'all';
  const filters = [
    { key: 'all', label: '全部', count: items.length },
    ...gapKeys.filter(k => counts[k]).map(k => ({
      key: k,
      label: `${GAP_LABELS[k].icon} ${GAP_LABELS[k].label}`,
      count: counts[k],
    })),
  ];
  filterBar.innerHTML = filters.map(f => `
    <button class="filter-chip ${activeGap === f.key ? 'active' : ''}"
            data-gap="${f.key}">
      ${f.label}
      <span class="count">${f.count}</span>
    </button>
  `).join('');
  $$('#benchmark-filters .filter-chip').forEach(btn => {
    btn.onclick = () => {
      STATE.activeGap = btn.dataset.gap;
      renderDangdangBenchmark();
    };
  });

  // 过滤 + 按"缺口严重度"排序（no_jd → no_self → perk_gap → none），同级别按销量降序
  const ORDER = { no_jd: 1, no_self: 2, perk_gap: 3, none: 4 };
  let shown = activeGap === 'all'
    ? items
    : items.filter(r => r.gap_level === activeGap);
  shown = shown.slice().sort((a, b) => {
    const oa = ORDER[a.gap_level] || 9;
    const ob = ORDER[b.gap_level] || 9;
    if (oa !== ob) return oa - ob;
    return (a.dangdang.rank || 99) - (b.dangdang.rank || 99);
  });

  // 默认 5 行，其他折叠
  const DEFAULT_LIMIT = 5;
  const expanded = STATE._benchmarkExpanded === true;
  const display = expanded ? shown : shown.slice(0, DEFAULT_LIMIT);

  grid.innerHTML = display.map(renderBenchmarkRow).join('');

  // "展开更多" 按钮
  if (shown.length > DEFAULT_LIMIT) {
    const remain = shown.length - DEFAULT_LIMIT;
    grid.innerHTML += `
      <button id="benchmark-toggle"
              class="w-full mt-2 py-2.5 text-sm ${expanded ? 'text-slate-500' : 'text-mint-700'}
                     font-medium border border-mint-100 rounded-xl bg-white hover:bg-mint-50/50 transition">
        ${expanded ? '收起 ↑' : `展开更多 (+${remain}) ↓`}
      </button>`;
    const tg = document.getElementById('benchmark-toggle');
    if (tg) tg.onclick = () => { STATE._benchmarkExpanded = !expanded; renderDangdangBenchmark(); };
  }
}

function renderBenchmarkRow(r) {
  const dd = r.dangdang;
  const jd = r.jd || {};
  const gap = GAP_LABELS[r.gap_level] || GAP_LABELS.none;

  // 当当权益标签
  const ddPerks = (dd.perks || []).map(p => {
    const cls = PERK_STYLES[p] || 'bg-slate-100 text-slate-600 border-slate-200';
    return `<span class="text-[10px] px-1.5 py-0.5 rounded border ${cls} font-medium">${p}</span>`;
  }).join(' ') || '<span class="text-xs text-slate-400">(无)</span>';

  // 京东方
  let jdSection;
  if (!jd.available) {
    jdSection = `
      <div class="flex-1 p-4 bg-rose-50/40 rounded-r-2xl border-l-2 border-rose-200">
        <div class="flex items-center gap-2 mb-2">
          <span class="text-[11px] px-2 py-0.5 rounded-full bg-rose-100 text-rose-700 font-medium">京东</span>
          <span class="text-rose-600 text-sm font-medium">❌ 未在售</span>
        </div>
        <p class="text-xs text-slate-500">
          搜索"${escapeHtml(dd.title.slice(0, 16))}…" 无结果
        </p>
      </div>`;
  } else {
    const best = jd.best_match || {};
    const isSelf = best.is_self;
    const shopBadge = isSelf
      ? `<span class="text-[11px] px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-700 font-medium">🟢 京东自营</span>`
      : `<span class="text-[11px] px-2 py-0.5 rounded-full bg-amber-100 text-amber-700 font-medium">🔵 京东 POP</span>`;
    const jdPerks = (best.perks || []).map(p => {
      const cls = PERK_STYLES[p] || 'bg-slate-100 text-slate-600 border-slate-200';
      return `<span class="text-[10px] px-1.5 py-0.5 rounded border ${cls} font-medium">${p}</span>`;
    }).join(' ') || '<span class="text-xs text-slate-400">(无差异化权益)</span>';

    const stats = [
      best.show_count_str ? `近期销量 ${best.show_count_str}` : null,
      best.comment_count_str ? `评论 ${best.comment_count_str}` : null,
    ].filter(Boolean).join(' · ');

    jdSection = `
      <div class="flex-1 p-4 bg-slate-50/40 rounded-r-2xl border-l-2 border-slate-200">
        <div class="flex items-center gap-2 mb-1.5 flex-wrap">
          ${shopBadge}
          ${best.price ? `<span class="text-sm font-semibold text-rose-600">${best.price}</span>` : ''}
        </div>
        <a href="${best.detail_url || '#'}" target="_blank" rel="noopener"
           class="block text-xs text-slate-700 leading-snug line-clamp-2 hover:text-mint-700 mb-2">
          ${escapeHtml(best.title || '')}
        </a>
        <p class="text-[10px] text-slate-500 line-clamp-1 mb-2">🏪 ${escapeHtml(best.shop_name || '')}</p>
        <div class="flex flex-wrap gap-1 mb-1.5">${jdPerks}</div>
        ${stats ? `<p class="text-[10px] text-slate-400">${stats}</p>` : ''}
      </div>`;
  }

  return `
    <div class="bg-white rounded-2xl border border-mint-100 overflow-hidden">
      <!-- 顶部缺口标签条 -->
      <div class="px-4 py-1.5 bg-${gap.tone}-50 border-b border-${gap.tone}-100
                  flex items-center justify-between">
        <span class="text-xs font-medium text-${gap.tone}-700">
          ${gap.icon} ${gap.label} · ${gap.desc}
        </span>
        ${dd.rank ? `<span class="text-[11px] text-slate-500">当当排名 #${dd.rank}</span>` : ''}
      </div>
      <!-- 主体：左当当 + 右京东 -->
      <div class="flex flex-col md:flex-row">
        <!-- 当当方 -->
        <div class="flex-1 p-4 bg-mint-50/30">
          <div class="flex items-center gap-2 mb-1.5 flex-wrap">
            <span class="text-[11px] px-2 py-0.5 rounded-full bg-rose-100 text-rose-700 font-medium">🎯 当当</span>
            ${dd.price ? `<span class="text-sm font-semibold text-mint-700">${dd.price}</span>` : ''}
            ${dd.rating ? `<span class="text-xs text-lemon-500">★ ${dd.rating}</span>` : ''}
          </div>
          <a href="${dd.url || '#'}" target="_blank" rel="noopener"
             class="block text-xs text-slate-700 leading-snug line-clamp-2 hover:text-mint-700 mb-2">
            ${escapeHtml(dd.title || '')}
          </a>
          <p class="text-[10px] text-slate-500 line-clamp-1 mb-2">${escapeHtml(dd.author || '—')}</p>
          <div class="flex flex-wrap gap-1">${ddPerks}</div>
        </div>
        <!-- 京东方 -->
        ${jdSection}
      </div>
    </div>
  `;
}

function escapeHtml(s) {
  return (s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;')
                  .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
                  .replace(/'/g, '&#39;');
}

// ========== 渲染：京东 POP 有自营无 ==========

function renderJdPop() {
  const grid = $('#jdpop-grid');
  const summary = $('#jdpop-summary');
  if (!grid) return;

  const items = STATE.jdPopOnly || [];
  if (!items.length) {
    summary.textContent = '京东对云端 IP 反爬，需本地或国内云服务器运行';
    grid.innerHTML = `<div class="col-span-full bg-white rounded-2xl border border-dashed
                              border-mint-200 p-8 text-center">
      <div class="text-4xl mb-3 opacity-60">🚧</div>
      <h3 class="font-medium text-slate-700 mb-1.5">数据源建设中</h3>
      <p class="text-xs text-slate-500 leading-relaxed max-w-md mx-auto">
        抓取代码已就绪（Playwright + 反检测），但京东对 GitHub Actions 的云端 IP 段
        系统性反爬，云端跑出来 0 条。<br>
        后续方案：本地手动跑 → 推数据，或部署到国内云服务器。
      </p>
      <div class="mt-4 inline-flex items-center gap-3 text-xs">
        <span class="px-2.5 py-1 rounded bg-mint-50 text-mint-700">京东 POP 在售</span>
        <span class="text-slate-400">−</span>
        <span class="px-2.5 py-1 rounded bg-rose-50 text-rose-700">京东自营在售</span>
        <span class="text-slate-400">=</span>
        <span class="px-2.5 py-1 rounded bg-amber-100 text-amber-800 font-medium">自营缺口</span>
      </div>
    </div>`;
    return;
  }

  summary.innerHTML = `
    <span class="font-semibold text-slate-900">${items.length}</span> 本
    京东 POP 商家在售、自营未上架
    <span class="text-slate-400 mx-2">·</span>
    销量门槛 ≥ 100 (近期评论数过滤)
  `;

  const DEFAULT_LIMIT = 5;
  const expanded = STATE._jdpopExpanded === true;
  const shown = expanded ? items : items.slice(0, DEFAULT_LIMIT);

  const cardsHtml = shown.map(b => {
    const safeTitle = (b.title || '').replace(/'/g, '&#39;').replace(/"/g, '&quot;');
    const [c1, c2] = COVER_GRADIENTS[hashStr(b.title || '') % COVER_GRADIENTS.length];
    return `
      <a href="${b.detail_url || '#'}" target="_blank" rel="noopener"
         class="card-hover bg-white rounded-2xl border border-mint-100 overflow-hidden flex flex-col">
        <div class="aspect-[3/4] overflow-hidden bg-mint-50 relative">
          <span class="absolute top-2 left-2 bg-amber-500 text-white text-[10px] font-bold
                       px-1.5 py-0.5 rounded shadow-sm z-10">POP</span>
          ${b.show_count_str ? `<span class="absolute top-2 right-2 bg-rose-500 text-white
                                  text-[10px] font-semibold px-1.5 py-0.5 rounded shadow-sm z-10">
                                  销量${b.show_count_str}</span>` : ''}
          <div class="cover-placeholder w-full h-full"
               style="--c1:${c1};--c2:${c2}">
            <span>${safeTitle}</span>
          </div>
        </div>
        <div class="p-3 flex-1 flex flex-col">
          <h3 class="font-medium text-sm text-slate-900 leading-snug line-clamp-2">
            ${b.title || '(无书名)'}
          </h3>
          <p class="text-xs text-slate-500 mt-1 line-clamp-1">${b.author || '—'}</p>
          ${b.publisher ? `<p class="text-[10px] text-slate-400 mt-0.5 line-clamp-1">${b.publisher}</p>` : ''}
          ${b.shop_name ? `<p class="text-[10px] text-amber-600 mt-0.5 line-clamp-1">🏪 ${b.shop_name}</p>` : ''}
          <div class="mt-auto pt-2 flex items-center justify-between gap-2">
            ${b.comment_count_str ? `<span class="text-[10px] text-slate-500">💬 ${b.comment_count_str}</span>` : '<span></span>'}
            ${b.price ? `<span class="text-xs font-semibold text-amber-600">${b.price}</span>` : ''}
          </div>
        </div>
      </a>
    `;
  }).join('');

  let toggleHtml = '';
  if (items.length > DEFAULT_LIMIT) {
    toggleHtml = `<button id="jdpop-toggle"
      class="col-span-full mt-2 py-2.5 text-sm ${expanded ? 'text-slate-500' : 'text-mint-700'} font-medium
             border border-mint-100 rounded-xl bg-white hover:bg-mint-50/50 transition">
      ${expanded ? '收起 ↑' : `展开更多 (+${items.length - DEFAULT_LIMIT}) ↓`}
    </button>`;
  }

  grid.innerHTML = cardsHtml + toggleHtml;

  const tg = document.getElementById('jdpop-toggle');
  if (tg) {
    tg.onclick = () => {
      STATE._jdpopExpanded = !expanded;
      renderJdPop();
    };
  }
}

// ========== 渲染：上游预售信号 ==========

function renderUpcoming() {
  const grid = $('#upcoming-grid');
  const summary = $('#upcoming-summary');
  const filterBar = $('#upcoming-filters');
  if (!grid) return;

  const all = STATE.newBooks || [];
  const preorder = all.filter(b => b.pub_status === 'preorder');
  const fresh = all.filter(b => b.pub_status === 'fresh' || b.pub_status === 'recent');
  // 显示池：预售在前，近期出版在后
  const pool = [...preorder, ...fresh];

  if (!pool.length) {
    summary.textContent = '暂无预售/近期出版数据';
    filterBar.innerHTML = '';
    grid.innerHTML = `<div class="col-span-full text-sm text-slate-400 py-8 text-center">
      暂无数据 — 下次抓取后会自动填充</div>`;
    return;
  }

  // 头部摘要
  const verifiedCount = pool.filter(b => b.verify_status === 'verified').length;
  const verifiedPreorder = preorder.filter(b => b.verify_status === 'verified').length;
  summary.innerHTML = `
    <span class="font-semibold text-slate-900">${preorder.length}</span> 本预售书
    ${fresh.length ? `· <span class="font-semibold text-slate-900">${fresh.length}</span> 本近期出版` : ''}
    <span class="text-slate-400 mx-2">·</span>
    含权益版 <span class="font-semibold text-slate-900">${pool.filter(b => b.perks && b.perks.length).length}</span> 本
    <br>
    <span class="text-xs text-slate-400">
      豆瓣校验通过 ${verifiedCount}/${pool.length} 本
      ${verifiedPreorder > 0 ? `（其中 ${verifiedPreorder} 本预售已被豆瓣确认）`
                              : '（预售书多数豆瓣未收录，疑为占位数据）'}
    </span>
  `;

  // 品类筛选
  const cats = ['all', ...new Set(pool.map(b => b.category).filter(Boolean))];
  const counts = { all: pool.length };
  pool.forEach(b => {
    const c = b.category;
    if (c) counts[c] = (counts[c] || 0) + 1;
  });

  filterBar.innerHTML = cats.map(c => {
    const label = c === 'all' ? '全部' : c;
    const active = STATE.activeUpcomingCat === c;
    return `<button class="filter-chip ${active ? 'active' : ''}" data-cat="${c}">
              ${label}
              <span class="count">${counts[c] || 0}</span>
            </button>`;
  }).join('');

  $$('#upcoming-filters .filter-chip').forEach(btn => {
    btn.onclick = () => {
      STATE.activeUpcomingCat = btn.dataset.cat;
      renderUpcoming();
    };
  });

  // 渲染卡片
  let items = STATE.activeUpcomingCat === 'all'
    ? pool
    : pool.filter(b => b.category === STATE.activeUpcomingCat);

  // 排序：预售在前，按距上市天数升序（越近越前），其次新出版（按已出版天数升序）
  items = items.slice().sort((a, b) => {
    if (a.pub_status === 'preorder' && b.pub_status !== 'preorder') return -1;
    if (b.pub_status === 'preorder' && a.pub_status !== 'preorder') return 1;
    if (a.pub_status === 'preorder' && b.pub_status === 'preorder') {
      return (a.days_since_pub || 0) - (b.days_since_pub || 0);  // -10 在 -100 之前
    }
    return (a.days_since_pub || 0) - (b.days_since_pub || 0);
  });

  // 默认展示 5 本（一排），其他折叠起来
  const DEFAULT_LIMIT = 5;
  const expanded = STATE._upcomingExpanded === true;
  const shown = expanded ? items : items.slice(0, DEFAULT_LIMIT);
  const hiddenCount = items.length - shown.length;

  const cardsHtml = shown.map(renderBookCard).join('');

  let toggleHtml = '';
  if (items.length > DEFAULT_LIMIT) {
    if (!expanded) {
      toggleHtml = `
        <button id="upcoming-toggle"
                class="col-span-full mt-2 py-2.5 text-sm text-mint-700 font-medium
                       border border-mint-100 rounded-xl bg-white hover:bg-mint-50/50
                       transition">
          展开更多 (+${hiddenCount}) ↓
        </button>`;
    } else {
      toggleHtml = `
        <button id="upcoming-toggle"
                class="col-span-full mt-2 py-2.5 text-sm text-slate-500 font-medium
                       border border-mint-100 rounded-xl bg-white hover:bg-mint-50/50
                       transition">
          收起 ↑
        </button>`;
    }
  }

  grid.innerHTML = cardsHtml + toggleHtml;

  const tg = document.getElementById('upcoming-toggle');
  if (tg) {
    tg.onclick = () => {
      STATE._upcomingExpanded = !expanded;
      renderUpcoming();
    };
  }
}

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
  const [meta, books, newBooks, news, insights, jdPopOnly, benchmark] = await Promise.all([
    loadJSON('data/meta.json'),
    loadJSON('data/books.json'),
    loadJSON('data/books_new.json'),
    loadJSON('data/news.json'),
    loadJSON('data/insights.json'),
    loadJSON('data/jd_pop_only.json'),
    loadJSON('data/benchmark.json'),
  ]);

  STATE.meta      = meta      || {};
  STATE.books     = books     || [];
  STATE.newBooks  = newBooks  || [];
  STATE.news      = news      || [];
  STATE.insights  = insights  || [];
  STATE.jdPopOnly = jdPopOnly || [];
  STATE.benchmark = benchmark || [];

  // Header 更新时间
  if (STATE.meta.updated_at) {
    $('#last-update').textContent = fmtRelative(STATE.meta.updated_at);
  } else {
    $('#last-update').textContent = '—';
  }

  // 渲染各模块
  renderInsights();
  renderDangdangBenchmark();
  renderJdPop();              // 新增：京东 POP 有自营无
  renderUpcoming();
  renderNewsFilters();
  renderNews();
  renderBookFilters();
  renderBooks();
  renderStats();
  renderCategoryChart();
}

document.addEventListener('DOMContentLoaded', init);
