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
    // Two separate pieces of state, not one — a real bug found live
    // testing this exact split: clicking a day was always preceded by
    // a real mouse first moving onto it (an ordinary part of clicking
    // anything with a mouse), which fired @mouseenter. With a single
    // shared `openDate`, that hover already set it to this day before
    // the click's own toggle() ever ran — toggle() then saw it "already
    // open" and immediately closed it again, so a click looked like it
    // did nothing at all. Tracking them independently means a click
    // toggles clickedDate on its own terms regardless of whatever the
    // mouse happens to be hovering, and openDate (below) is just
    // "whichever of the two is currently set."
    clickedDate: null,
    hoveredDate: null,
    get openDate() {
      return this.hoveredDate || this.clickedDate;
    },
    toggle(dateStr) {
      this.clickedDate = this.clickedDate === dateStr ? null : dateStr;
    },
    close() {
      this.clickedDate = null;
      this.hoveredDate = null;
    },
    get openDay() {
      // Reads hoveredDate/clickedDate directly rather than through
      // the openDate getter above (which itself is only ever read
      // from template expressions, x-show/:aria-expanded) — avoids a
      // getter reading another getter, one less layer of indirection
      // for Alpine's own reactivity to have to track correctly.
      const date = this.hoveredDate || this.clickedDate;
      if (!date) return null;
      return this.days.find((day) => day.date === date) || null;
    },
  };
}
