export default {
  template: `
    <div style="position:relative; overflow:hidden; display:flex; align-items:center; justify-content:center;">
      <svg v-if="!displayVersion" role="img" aria-label="画像未表示"
           data-testid="viewer-image-placeholder" viewBox="0 0 24 24" width="48" height="48"
           style="fill:currentColor;opacity:.55;flex-shrink:0;transform:translateY(-56px)" focusable="false">
        <path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM5 5h14v14H5V5zm8.5 6.5-3 3.51-2-2.5L6 17h12l-4.5-5.5z" />
      </svg>
      <img v-for="frame in renderedFrames" :key="frame.id" :ref="node => setFrameRef(frame.id, node)"
           :src="frame.url" :alt="frame.id === displayVersion ? (alt || '') : ''"
           :aria-hidden="frame.id !== displayVersion"
           :data-pv-displayed="frame.id === displayVersion ? 'true' : 'false'"
           :style="{display:'block', maxWidth:'100%', maxHeight:'100%', width:'auto', height:'auto',
                    objectFit:'contain', position:frame.id === displayVersion ? 'static' : 'absolute',
                    visibility:frame.id === displayVersion ? 'visible' : 'hidden',
                    pointerEvents:frame.id === displayVersion ? 'auto' : 'none'}" />
    </div>
  `,
  props: {
    src: { type: String, default: '' },
    event_source: { type: String, default: '' },
    request_id: { type: Number, default: 0 },
    alt: { type: String, default: '' },
    retain_previous: { type: Boolean, default: false },
    preload_src: { type: String, default: '' },
    use_preload: { type: Boolean, default: false },
  },
  data() {
    return { frames: [], imageNodes: new Map(), displayVersion: 0, version: 0,
             preloadFrame: null, preloadSerial: 0 };
  },
  computed: {
    verificationRequest() {
      return [this.src, this.request_id, this.retain_previous];
    },
    renderedFrames() {
      return this.preloadFrame ? [...this.frames, this.preloadFrame] : this.frames;
    },
  },
  watch: {
    // Vue applies these props together for one source update. Verify that update once.
    verificationRequest() { this.verify(); },
    preload_src: { flush: 'post', handler() { this.preload(); } },
  },
  mounted() {
    this.verify();
    this.preload();
  },
  beforeUnmount() {
    this.version += 1;
    this.frames = [];
    this.preloadFrame = null;
    this.imageNodes.clear();
  },
  methods: {
    setFrameRef(id, node) {
      if (node) this.imageNodes.set(id, node);
      else this.imageNodes.delete(id);
    },
    imageUrl(source) {
      return (source.startsWith('/') ? (window.path_prefix || '') : '') + source;
    },
    resourceMatches(image, url) {
      const actualResource = new URL(image.currentSrc || image.src, document.baseURI);
      const expectedResource = new URL(url, document.baseURI);
      actualResource.hash = '';
      expectedResource.hash = '';
      return actualResource.href === expectedResource.href;
    },
    async preload() {
      this.preloadFrame = null;
      const source = this.preload_src;
      if (!source.startsWith('/media/')) return;
      const frame = {id: --this.preloadSerial, url: this.imageUrl(source),
                     owner: this.request_id, ready: false};
      this.preloadFrame = frame;
      const current = () => this.preloadFrame?.id === frame.id && frame.owner === this.request_id;
      try {
        await this.$nextTick();
        if (!current()) return;
        const image = this.imageNodes.get(frame.id);
        await image.decode();
        if (current() && image === this.imageNodes.get(frame.id) && image.complete &&
            image.naturalWidth && image.naturalHeight && this.resourceMatches(image, frame.url)) {
          this.preloadFrame.ready = true;
        }
      } catch (_) {
        if (current()) this.preloadFrame = null;
      }
    },
    async verify() {
      const version = ++this.version;
      const source = this.src || '';
      const reportedSource = this.event_source || source;
      const preloaded = this.preloadFrame;
      this.preloadFrame = null;
      // Only the accepted front image is retained. A superseded pending request
      // must never become the next request's rollback image.
      const front = this.retain_previous
        ? this.frames.find(frame => frame.id === this.displayVersion) : null;
      this.frames = source && front ? [front] : [];
      if (!source || !front) this.displayVersion = 0;
      if (!source) return;
      const url = this.imageUrl(source);
      // The fragment identifies the accepted foreground request. Leave the
      // hidden node's src untouched so promotion cannot cause another fetch.
      const warmed = this.use_preload && preloaded?.ready &&
        preloaded.owner + 1 === this.request_id && preloaded.url === url.split('#', 1)[0];
      const candidate = warmed ? preloaded : {id: version, url};
      this.frames = [...this.frames, candidate];
      try {
        if (!warmed) await this.$nextTick();
        if (version !== this.version) return;
        const image = this.imageNodes.get(candidate.id);
        if (!image) throw new Error('candidate image was not mounted');
        // Decode the actual mounted candidate while it remains invisible.
        // Promotion keeps this exact keyed node, so no second undecoded img is created.
        if (!warmed) await image.decode();
        if (version !== this.version || source !== this.src) return;
        if (image !== this.imageNodes.get(candidate.id) || image.getAttribute('src') !== candidate.url ||
            !image.complete || !image.naturalWidth || !image.naturalHeight) {
          throw new Error('candidate image changed before verification');
        }
        // 画素のHTTP資源も照合する。ブラウザが保持する古いfragmentだけを除く。
        // サーバーへは引き続き捕捉した要求のfragmentを送り、世代検証を維持する。
        if (!this.resourceMatches(image, url)) {
          throw new Error('decoded image resource does not match request');
        }
        this.displayVersion = candidate.id;
        this.frames = [candidate];
        await this.$nextTick();
        await new Promise(resolve => requestAnimationFrame(resolve));
        if (version === this.version && image === this.imageNodes.get(candidate.id))
          this.$emit('load', reportedSource);
      } catch (_) {
        if (version === this.version) {
          this.frames = this.frames.filter(frame => frame.id === this.displayVersion);
          this.$emit('error', reportedSource);
        }
      }
    },
  },
};
