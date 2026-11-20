export default {
  inheritAttrs: false,
  props: { root_id: Number },
  data() {
    return { localElements: {}, revision: 0 };
  },
  methods: {
    applyViewerState(revision, patch) {
      if (revision <= this.revision) return;
      for (const element of Object.values(patch)) {
        window.replaceUndefinedAttributes(element);
      }
      this.revision = revision;
      this.localElements = { ...this.localElements, ...patch };
    },
  },
  render() {
    if (this.root_id === undefined) return null;
    // Preserve NiceGUI's slots, refs and event dispatch under its root instance.
    // Only this component subscribes to page state. Updates to static viewer
    // elements, such as opening details, can still use the normal NiceGUI path.
    const local = this.localElements;
    const elements = new Proxy(this.$root.elements, {
      get: (shared, id) => local[id] ?? shared[id],
    });
    return Vue.withCtx(() => window.renderRecursively(elements, this.root_id), this.$root.$)();
  },
};
