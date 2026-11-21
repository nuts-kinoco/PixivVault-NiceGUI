export default {
  props: {
    tags: { type: Array, default: () => [] },
  },
  template: `<div><div v-for="(tag, index) in tags" :key="index"
    class="pv-subtitle border rounded px-2">{{ tag }}</div></div>`,
};
