/* apps.core.widgets.SearchablePickerWidget (templates/core/widgets/
 * searchable_picker.html): a search box that suggests choices and a list
 * of the picked ones, each removable. The widget's checkboxes stay the
 * real form input — this only checks/unchecks them and hides the plain
 * list, so without JavaScript the checkboxes simply work as they are. */
// eslint-disable-next-line no-unused-vars
function ironstackSearchablePicker() {
  return {
    ready: false,
    open: false,
    query: "",
    active: 0,
    options: [],
    MAX_SUGGESTIONS: 8,

    init() {
      this.options = [...this.$refs.fallback.querySelectorAll('input[type="checkbox"]')].map(
        (box) => ({ value: box.value, label: box.dataset.label, box, checked: box.checked })
      );
      this.$refs.fallback.hidden = true;
      this.ready = true;
    },

    get selected() {
      return this.options.filter((option) => option.checked);
    },

    get matches() {
      const needle = this.query.trim().toLocaleLowerCase();
      return this.options
        .filter((option) => !option.checked && option.label.toLocaleLowerCase().includes(needle))
        .slice(0, this.MAX_SUGGESTIONS);
    },

    move(step) {
      this.open = true;
      const count = this.matches.length;
      if (count) this.active = (this.active + step + count) % count;
    },

    pick(option) {
      if (!option) return;
      option.box.checked = option.checked = true;
      this.query = "";
      this.active = 0;
    },

    remove(option) {
      option.box.checked = option.checked = false;
      // The removed row's button is gone; keep keyboard focus in the picker.
      this.$root.querySelector('input[type="search"]').focus();
      this.open = false;
    },
  };
}
