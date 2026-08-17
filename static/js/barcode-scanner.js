// Camera barcode scanning for the food-search boxes (diary, recipe
// ingredients, diet-plan meal items, food browse) — asked for
// directly, on top of the existing type-the-digits-in barcode search
// (apps.nutrition.services._BARCODE_RE).
//
// Deliberately zero new dependencies: the browser's own native
// BarcodeDetector API does the actual decoding, not a vendored JS
// library — matches CLAUDE.md's "avoid unnecessary dependencies" and
// this project's existing "only ever vendor a JS file, never load
// from a CDN" precedent (htmx.min.js/alpine.min.js), by needing
// nothing to vendor at all. BarcodeDetector isn't universal yet
// (Chromium-based browsers, most notably Chrome for Android — this
// app's primary mobile target per CLAUDE.md's "mobile-first" goal —
// support it; Firefox and older Safari don't) — `supported` below
// gates the "Scan barcode" button so it simply doesn't appear rather
// than opening onto a broken camera view on a browser that can't
// decode anything.
function ironstackBarcodeScanner() {
  return {
    active: false,
    supported: "BarcodeDetector" in window,
    error: null,
    stream: null,
    detector: null,
    rafId: null,
    targetInputId: null,

    async open(targetInputId) {
      this.targetInputId = targetInputId;
      this.error = null;
      this.active = true;
      try {
        this.detector = new window.BarcodeDetector({
          // Every format apps.nutrition.services._BARCODE_RE's 8-14
          // digit range actually covers (EAN-8/UPC-A/UPC-E/EAN-13) —
          // ITF-14 isn't a BarcodeDetector format, no loss since it's
          // rare on retail packaging compared to these.
          formats: ["ean_13", "ean_8", "upc_a", "upc_e"],
        });
        this.stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "environment" },
        });
      } catch (e) {
        // Camera permission denied, no camera, or getUserMedia itself
        // unavailable (e.g. the page isn't served over HTTPS, which
        // getUserMedia requires everywhere except localhost) — close
        // back out to the plain text search rather than leaving a
        // dead, permanently-loading overlay open.
        this.error = e.message || String(e);
        this.close();
        return;
      }
      this.$nextTick(() => {
        const video = this.$refs.scannerVideo;
        video.srcObject = this.stream;
        video.play();
        this.scanLoop(video);
      });
    },

    scanLoop(video) {
      if (!this.active) return;
      this.detector
        .detect(video)
        .then((barcodes) => {
          if (barcodes.length > 0) {
            this.onDetected(barcodes[0].rawValue);
            return;
          }
          this.rafId = requestAnimationFrame(() => this.scanLoop(video));
        })
        .catch(() => {
          // A single failed frame (e.g. the video isn't ready yet) —
          // keep scanning rather than giving up on the whole session.
          this.rafId = requestAnimationFrame(() => this.scanLoop(video));
        });
    },

    onDetected(barcode) {
      const input = document.getElementById(this.targetInputId);
      this.close();
      if (!input) return;
      input.value = barcode;
      input.focus();
      // Setting .value directly fires no DOM event at all — the
      // search box's own hx-trigger="keyup changed delay:400ms"
      // needs a real "keyup" to fire the HTMX request, same event
      // type a real keystroke would have dispatched.
      input.dispatchEvent(new Event("keyup", { bubbles: true }));
    },

    close() {
      this.active = false;
      if (this.rafId) {
        cancelAnimationFrame(this.rafId);
        this.rafId = null;
      }
      if (this.stream) {
        this.stream.getTracks().forEach((track) => track.stop());
        this.stream = null;
      }
    },
  };
}
