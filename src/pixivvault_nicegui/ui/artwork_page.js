export default {
  inheritAttrs: false,
  render() {
    // Read the server's element slots in this component's render effect.
    // Updating viewer elements then leaves the surrounding grids alone.
    const children = this.$slots.default?.() ?? [];
    if (children.length > 1) throw new Error('ArtworkPage requires exactly one root child');
    return children[0] ?? null;
  },
  beforeUnmount() {
    this.$el?.querySelector?.('[data-testid="viewer-zoom-viewport"]')?.__pvZoomController?.dispose();
  },
};
