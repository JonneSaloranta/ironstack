// Used by apps/core/test_ui_audit_crawl.py (page.evaluate): per-page facts
// for docs/ui-ux/audit.md - title, h1 count, headings, inline styles,
// targets under 24px, primary-styled buttons, lower-case labels.
() => {
  const q = s => [...document.querySelectorAll(s)];
  const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none'; };
  const main = document.querySelector('main') || document.body;
  const small = [];
  for (const el of main.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button]')) {
    if (!vis(el)) continue;
    const r = el.getBoundingClientRect();
    const inline = el.tagName === 'A' && el.closest('p, li, td') && getComputedStyle(el).display === 'inline';
    if (!inline && (r.width < 24 || r.height < 24))
      small.push(el.tagName.toLowerCase() + '.' + [...el.classList].join('.') + ' ' + Math.round(r.width) + 'x' + Math.round(r.height) + ' «' + (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 25) + '»');
  }
  const headings = q('h1,h2,h3,h4,h5,h6').filter(vis).map(h => h.tagName + ':' + h.className + ':' + h.innerText.trim().slice(0, 40));
  return {
    title: document.title,
    h1: q('h1').length,
    headings,
    inlineStyles: q('main [style]').map(e => e.tagName.toLowerCase() + '.' + [...e.classList].join('.') + ' ' + e.getAttribute('style')).slice(0, 20),
    smallTargets: small.slice(0, 30),
    primaryButtons: q('main button:not(.button-secondary):not(.button-danger):not(.button-link):not(.modal-close):not(.help-button):not(.pr-toast-close):not([class*=calendar]), main a.button:not(.button-secondary):not(.button-danger)').filter(vis).map(b => (b.innerText || b.getAttribute('aria-label') || '').trim().slice(0, 30)),
    lowercaseLabels: q('main label').filter(vis).map(l => (l.childNodes[0] && l.childNodes[0].textContent || '').trim()).filter(t => /^[a-z]/.test(t)).slice(0, 10),
  };
}
