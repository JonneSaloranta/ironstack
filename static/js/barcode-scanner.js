// Camera barcode scanning for the food-search boxes (diary, recipe
// ingredients, diet-plan meal items, food browse) — asked for
// directly, on top of the existing type-the-digits-in barcode search
// (apps.nutrition.services._BARCODE_RE).
//
// Two decoders, tried in order:
//   1. The browser's own native BarcodeDetector API — zero extra
//      bytes downloaded, but Chromium-only (Chrome/Android — this
//      app's primary mobile target — supports it; Firefox and older
//      Safari don't).
//   2. ZXing (static/js/zxing.min.js, vendored — same "only ever
//      vendor a JS file, never load from a CDN" precedent as
//      htmx.min.js/alpine.min.js) as a fallback for every browser
//      without BarcodeDetector, so the "Scan barcode" button works
//      everywhere getUserMedia does instead of silently disappearing
//      on non-Chromium browsers. Loaded lazily, only the first time a
//      browser without BarcodeDetector actually opens the scanner —
//      a Chrome user, who never needs it, never downloads it.
//
// `supported` is unconditionally true now (any browser with
// getUserMedia can plausibly scan) — camera/permission failures are
// still handled by the existing try/catch in open() either way.
let zxingLoadPromise = null;

function loadZxing() {
  if (window.ZXing) return Promise.resolve(window.ZXing);
  if (!zxingLoadPromise) {
    zxingLoadPromise = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      // Read off a data-* attribute (base.html renders it onto <body>,
      // the same handoff apps.core.views.service_worker's own
      // data-service-worker-url already uses) rather than a hardcoded
      // path — ManifestStaticFilesStorage in production serves this
      // file at a content-hashed URL, not its plain name.
      script.src = document.body.dataset.zxingUrl;
      script.onload = () => resolve(window.ZXing);
      script.onerror = reject;
      document.head.appendChild(script);
    });
  }
  return zxingLoadPromise;
}

// Every format apps.nutrition.services._BARCODE_RE's 8-14 digit range
// actually covers (EAN-8/UPC-A/UPC-E/EAN-13) — ITF-14 isn't a
// BarcodeDetector format and rare on retail packaging next to these,
// no loss keeping both decoders restricted to the same four.
const BARCODE_FORMATS = ["ean_13", "ean_8", "upc_a", "upc_e"];

// Regression: `"BarcodeDetector" in window` alone isn't actually proof
// the native path works — on Android Chrome, that constructor comes
// from a separate on-device barcode-detection service (part of Google
// Play Services) that isn't guaranteed to be installed just because
// the API surface exists. When it isn't, `new BarcodeDetector()`
// still succeeds and `.detect()` never throws — it just resolves to
// an empty array, forever, every single frame — which reads as
// exactly what got reported: camera visibly on, nothing ever
// detected, no error shown anywhere. `getSupportedFormats()` is a
// static method that reflects what's *actually* usable on this
// device/browser combination right now, unlike the constructor —
// checked once up front so a device without real support for any of
// our four formats falls back to the vendored ZXing decoder instead
// of silently hanging on a detector that will never fire.
async function nativeDetectorUsable() {
  if (!("BarcodeDetector" in window)) return false;
  try {
    const supported = await window.BarcodeDetector.getSupportedFormats();
    return BARCODE_FORMATS.some((format) => supported.includes(format));
  } catch {
    return false;
  }
}

function ironstackBarcodeScanner() {
  return {
    active: false,
    supported: true,
    error: null,
    stream: null,
    detector: null,
    usingZxing: false,
    rafId: null,
    targetInputId: null,
    slowHintTimer: null,
    slowHint: false,

    async open(targetInputId) {
      this.targetInputId = targetInputId;
      this.error = null;
      this.slowHint = false;
      this.active = true;
      this.usingZxing = !(await nativeDetectorUsable());
      // Not a fix for any specific decode failure — a still-visible
      // way out if the camera view genuinely never finds a barcode
      // (a device-specific decoder quirk neither fallback path
      // catches, bad lighting, a damaged/unusual barcode, ...)
      // instead of an overlay that looks identical whether it's about
      // to succeed or never will. Cleared in close() either way.
      this.slowHintTimer = setTimeout(() => {
        this.slowHint = true;
      }, 8000);
      try {
        if (this.usingZxing) {
          const ZXing = await loadZxing();
          const hints = new Map();
          hints.set(ZXing.DecodeHintType.POSSIBLE_FORMATS, [
            ZXing.BarcodeFormat.EAN_13,
            ZXing.BarcodeFormat.EAN_8,
            ZXing.BarcodeFormat.UPC_A,
            ZXing.BarcodeFormat.UPC_E,
          ]);
          this.detector = new ZXing.BrowserMultiFormatReader(hints);
        } else {
          this.detector = new window.BarcodeDetector({ formats: BARCODE_FORMATS });
        }
        this.stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: "environment" },
        });
      } catch (e) {
        // Camera permission denied, no camera, getUserMedia itself
        // unavailable (e.g. the page isn't served over HTTPS, which
        // getUserMedia requires everywhere except localhost), or the
        // zxing.min.js fetch failed (offline first load) — close back
        // out to the plain text search rather than leaving a dead,
        // permanently-loading overlay open.
        //
        // `navigator.mediaDevices` itself (not just getUserMedia) is
        // undefined outside a secure context — accessing
        // `.getUserMedia` on it then throws a bare engine-level
        // TypeError ("undefined is not an object (evaluating
        // 'navigator.mediaDevices.getUserMedia')" in Safari's own
        // phrasing, found live testing this exact fix over plain
        // HTTP), which read as this feature being broken rather than
        // explaining what actually needs to change (visit over https,
        // or localhost on the same machine).
        if (!navigator.mediaDevices) {
          this.error = document.body.dataset.insecureContextMessage;
        } else {
          this.error = e.message || String(e);
        }
        this.close();
        return;
      }
      this.$nextTick(() => {
        const video = this.$refs.scannerVideo;
        video.srcObject = this.stream;
        video.play();
        if (this.usingZxing) {
          // decodeFromVideoElement manages its own continuous decode
          // loop against the already-playing <video> — unlike the
          // native path below, no manual requestAnimationFrame loop
          // needed; the callback just fires repeatedly until reset().
          this.detector.decodeFromVideoElement(video, (result) => {
            if (result) this.onDetected(result.getText());
          });
        } else {
          this.scanLoop(video);
        }
      });
    },

    // Native BarcodeDetector path only — ZXing drives its own loop
    // internally via decodeFromVideoElement above.
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
      if (this.slowHintTimer) {
        clearTimeout(this.slowHintTimer);
        this.slowHintTimer = null;
      }
      if (this.rafId) {
        cancelAnimationFrame(this.rafId);
        this.rafId = null;
      }
      if (this.usingZxing && this.detector) {
        this.detector.reset();
      }
      if (this.stream) {
        this.stream.getTracks().forEach((track) => track.stop());
        this.stream = null;
      }
    },
  };
}
