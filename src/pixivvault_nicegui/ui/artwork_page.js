export default {
  inheritAttrs: false,
  render() {
    // Read the server's element slots in this component's render effect.
    // Updating viewer elements then leaves the surrounding grids alone.
    return this.$slots.default?.()[0] ?? null;
  },
  beforeUnmount() {
    this.$el?.querySelector?.('[data-testid="viewer-zoom-viewport"]')?.__pvZoomController?.dispose();
  },
};
