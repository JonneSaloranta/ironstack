// Used by apps/core/test_layout.py (page.evaluate): measures the page
// for content running off the screen or out of its card, and rows of a
// list whose actions don't line up. Returns [[kind, detail], ...].
() => {
  const W = window.innerWidth, issues = [];
  const scrolls = (el) => { for (let n = el.parentElement; n && n !== document.body; n = n.parentElement) {
      const o = getComputedStyle(n).overflowX; if (o === 'auto' || o === 'scroll' || o === 'hidden') return true; } return false; };
  const visible = (el) => { const r = el.getBoundingClientRect(), cs = getComputedStyle(el);
      return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const name = (el) => el.tagName.toLowerCase() + ' «' + (el.innerText || '').trim().slice(0, 40) + '»';
  if (document.documentElement.scrollWidth > W + 1) issues.push(['page-overflow', document.documentElement.scrollWidth]);
  const main = document.querySelector('main');
  for (const el of main.querySelectorAll('*')) {
    if (!visible(el) || scrolls(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.right > W + 1 || r.left < -1) { issues.push(['off-screen', name(el)]); continue; }
    const card = el.parentElement && el.parentElement.closest('.card');
    if (card && r.right > card.getBoundingClientRect().right + 1) issues.push(['out-of-card', name(el)]);
  }
  const lists = new Map();
  for (const row of main.querySelectorAll('.card-action-row')) {
    const kids = [...row.children].filter(visible);
    if (!visible(row) || kids.length < 2) continue;
    if (!lists.has(row.parentElement)) lists.set(row.parentElement, []);
    lists.get(row.parentElement).push(kids);
  }
  for (const rows of lists.values()) {
    const info = rows.map(kids => { const first = kids[0].getBoundingClientRect(), last = kids[kids.length - 1].getBoundingClientRect();
      return { under: last.top >= first.bottom - 2, right: Math.round(last.right), width: Math.round(last.width) }; });
    for (const i of info) if (i.under) issues.push(['action-under-text', '']);
    if (new Set(info.map(i => i.right)).size > 1) issues.push(['actions-misaligned', info.map(i => i.right).join(',')]);
  }
  return issues;
}
