export default {
  template: `
    <div style="position:relative; overflow:hidden; display:flex; align-items:center; justify-content:center;">
      <img v-for="frame in frames" :key="frame.id" :ref="node => setFrameRef(frame.id, node)"
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
    request_id: { type: Number, default: 0 },
    alt: { type: String, default: '' },
    retain_previous: { type: Boolean, default: false },
  },
  data() {
    return { frames: [], imageNodes: new Map(), displayVersion: 0, version: 0 };
  },
  watch: {
    src() { this.verify(); },
    request_id() { this.verify(); },
    retain_previous() { this.verify(); },
  },
  mounted() {
    this.verify();
  },
  beforeUnmount() {
    this.version += 1;
    this.frames = [];
    this.imageNodes.clear();
  },
  methods: {
    setFrameRef(id, node) {
      if (node) this.imageNodes.set(id, node);
      else this.imageNodes.delete(id);
    },
    async verify() {
      const version = ++this.version;
      const source = this.src || '';
      // Only the accepted front image is retained. A superseded pending request
      // must never become the next request's rollback image.
      const front = this.retain_previous
        ? this.frames.find(frame => frame.id === this.displayVersion) : null;
      this.frames = source && front ? [front] : [];
      if (!source || !front) this.displayVersion = 0;
      if (!source) return;
      const url = (source.startsWith('/') ? (window.path_prefix || '') : '') + source;
      const candidate = {id: version, url};
      this.frames = [...this.frames, candidate];
      try {
        await this.$nextTick();
        if (version !== this.version) return;
        const image = this.imageNodes.get(version);
        if (!image) throw new Error('candidate image was not mounted');
        // Decode the actual mounted candidate while it remains invisible.
        // Promotion keeps this exact keyed node, so no second undecoded img is created.
        await image.decode();
        if (version !== this.version || source !== this.src) return;
        if (image !== this.imageNodes.get(version) || image.getAttribute('src') !== url ||
            !image.complete || !image.naturalWidth || !image.naturalHeight) {
          throw new Error('candidate image changed before verification');
        }
        // 画素のHTTP資源も照合する。ブラウザが保持する古いfragmentだけを除く。
        // サーバーへは引き続き捕捉した要求のfragmentを送り、世代検証を維持する。
        const actualResource = new URL(image.currentSrc || image.src, document.baseURI);
        const expectedResource = new URL(url, document.baseURI);
        actualResource.hash = '';
        expectedResource.hash = '';
        if (actualResource.href !== expectedResource.href) {
          throw new Error('decoded image resource does not match request');
        }
        this.displayVersion = version;
        this.frames = [candidate];
        await this.$nextTick();
        await new Promise(resolve => requestAnimationFrame(resolve));
        if (version === this.version && image === this.imageNodes.get(version))
          this.$emit('load', source);
      } catch (_) {
        if (version === this.version) {
          this.frames = this.frames.filter(frame => frame.id === this.displayVersion);
          this.$emit('error', source);
        }
      }
    },
  },
};
