// Front-page month calendar (templates/nutrition/_month_calendar.html)
// — tapping a day shows a small detail panel instead of navigating
// anywhere, since the grid itself is deliberately icon/color-only (no
// text) and this is where that detail actually lives. `dataElementId`
// points at a `|json_script` tag apps.core.views.DashboardView
// renders with each day's already-translated summary lines — no data
// or text is embedded in this file itself, same reasoning as
// dashboard-carousel.js's own comment (CSP's script-src has no
// 'unsafe-inline', so this can only ever be loaded via <script src>).
function ironstackMonthCalendar(dataElementId) {
  return {
    days: JSON.parse(document.getElementById(dataElementId).textContent),
    openDate: null,
    toggle(dateStr) {
      this.openDate = this.openDate === dateStr ? null : dateStr;
    },
    get openDay() {
      if (!this.openDate) return null;
      return this.days.find((day) => day.date === this.openDate) || null;
    },
  };
}
