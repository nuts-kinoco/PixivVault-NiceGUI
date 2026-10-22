export default {
  template: `
    <div style="overflow:hidden; display:flex; align-items:center; justify-content:center;">
      <img v-show="displayedSrc" :key="version" ref="image" :src="displayedSrc || undefined" :alt="alt || ''"
           style="display:block; max-width:100%; max-height:100%; width:auto; height:auto; object-fit:contain;" />
    </div>
  `,
  props: {
    src: { type: String, default: '' },
    request_id: { type: Number, default: 0 },
    alt: { type: String, default: '' },
  },
  data() {
    return { displayedSrc: '', version: 0 };
  },
  watch: {
    src() { this.verify(); },
    request_id() { this.verify(); },
  },
  mounted() {
    this.verify();
  },
  beforeUnmount() {
    this.version += 1;
    this.displayedSrc = '';
  },
  methods: {
    async verify() {
      const version = ++this.version;
      const source = this.src || '';
      this.displayedSrc = '';
      if (!source) return;
      const url = (source.startsWith('/') ? (window.path_prefix || '') : '') + source;
      const probe = new Image();
      try {
        probe.src = url;
        if (probe.decode) {
          await probe.decode();
        } else {
          await new Promise((resolve, reject) => {
            if (probe.complete) { probe.naturalWidth ? resolve() : reject(new Error('invalid image')); return; }
            probe.onload = resolve;
            probe.onerror = reject;
          });
        }
        if (version !== this.version || !probe.naturalWidth || !probe.naturalHeight) return;
        this.displayedSrc = url;
        await this.$nextTick();
        if (version !== this.version) return;
        await new Promise(resolve => requestAnimationFrame(resolve));
        if (version !== this.version) return;
        const image = this.$refs.image;
        if (!image) return;
        // キャッシュ復帰時も実際に描画するノードをdecodeする。
        // 要求別keyにより旧ノードの完了やcurrentSrcを新要求へ取り違えない。
        await image.decode();
        if (version !== this.version || image !== this.$refs.image ||
            image.getAttribute('src') !== url || !image.complete ||
            !image.naturalWidth || !image.naturalHeight) return;
        // 画素のHTTP資源も照合する。ブラウザが保持する古いfragmentだけを除く。
        // サーバーへは引き続き捕捉した要求のfragmentを送り、世代検証を維持する。
        const actualResource = new URL(image.currentSrc || image.src, document.baseURI);
        const expectedResource = new URL(url, document.baseURI);
        actualResource.hash = '';
        expectedResource.hash = '';
        if (actualResource.href !== expectedResource.href) {
          this.displayedSrc = '';
          throw new Error('decoded image resource does not match request');
        }
        this.$emit('load', source);
      } catch (_) {
        if (version === this.version) this.$emit('error', source);
      }
    },
  },
};
