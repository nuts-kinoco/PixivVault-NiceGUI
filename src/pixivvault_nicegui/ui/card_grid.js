export default {
  template: `
    <div ref="grid" :data-render-epoch="render_epoch">
      <div v-for="(card, index) in cards" :key="card.work_id" class="pv-card"
           :data-work-id="card.work_id" :role="card.src ? 'button' : undefined"
           :tabindex="card.src && grid_active ? 0 : undefined"
           :aria-haspopup="card.menu?.length ? 'menu' : undefined"
           @click="openCard(card)" @keydown="onKey($event, card)"
           @contextmenu="contextMenu($event, card)">
        <div v-if="card.heart" class="row items-center no-wrap gap-1 pv-card-head">
          <div class="pv-card-rank">{{ card.rank }}</div>
          <q-icon name="favorite" size="12px" class="pv-card-heart" aria-label="好みのタグを含む作品"
                  :data-testid="'ranking-heart-' + card.work_id" />
        </div>
        <div v-else class="pv-card-rank">{{ card.rank }}</div>
        <div v-if="card.src" class="pv-thumb pv-grid-image">
          <img :key="card.src" v-show="failed[card.work_id] !== card.src"
               :src="ready ? imageSource(card.src) : undefined" alt="" :loading="index < columns ? 'eager' : 'lazy'"
               :fetchpriority="index === 0 ? 'high' : undefined" decoding="async"
               @load="clearFailure(card)" @error="failed[card.work_id] = card.src" />
          <div v-if="failed[card.work_id] === card.src"
               class="pv-grid-image-error column flex-center q-pa-sm"
               role="status" data-testid="thumbnail-unavailable">
            <q-icon name="broken_image" size="32px" />
            <span class="text-caption text-center">画像を取得できませんでした</span>
            <q-tooltip>通信の混雑や取得失敗で画像を表示できません。処理が終わってから画面を再表示してください。</q-tooltip>
          </div>
        </div>
        <div v-else class="pv-thumb pv-subtitle">画像を取得できません</div>
        <div class="pv-card-meta">
          <div class="pv-card-title">{{ card.title }}</div>
          <div class="pv-card-author">{{ card.author }}</div>
        </div>
      </div>
      <div v-if="more_visible" class="pv-more-cell">
        <q-btn outline no-caps color="primary" :label="more_text" :disable="!more_enabled || !grid_active"
               @click="send(null, 'more')" />
      </div>
      <span class="pv-card-menu-anchor" :style="{left: menuX + 'px', top: menuY + 'px'}">
        <q-menu ref="menu" no-parent-event anchor="top left" self="top left" role="menu"
                :data-testid="menuCard?.menu_id" @hide="onMenuHide">
          <q-item v-for="entry in menuCard?.menu || []" :key="entry.key" clickable
                  :data-testid="entry.testid" :disable="!entry.enabled" role="menuitem"
                  :href="entry.enabled && menuEpoch === render_epoch ? entry.href : undefined"
                  :target="entry.href ? '_blank' : undefined" :rel="entry.href ? 'noopener noreferrer' : undefined"
                  @click="menuAction($event, entry)">
            <q-item-section>{{ entry.label }}</q-item-section>
            <q-icon v-if="entry.icon" :name="entry.icon" class="pv-menu-icon" />
          </q-item>
        </q-menu>
      </span>
    </div>
  `,
  props: {
    cards: {type: Array, default: () => []},
    grid_active: Boolean,
    render_epoch: Number,
    more_visible: Boolean,
    more_enabled: Boolean,
    more_text: String,
  },
  emits: ['card-action'],
  data() {
    return {columns: 6, resizeObserver: null, failed: {}, menuCard: null, menuOpener: null,
            menuEpoch: 0, menuX: 0, menuY: 0, ready: false};
  },
  watch: {
    render_epoch() {
      this.$refs.menu?.hide();
      const activeFailures = {};
      for (const card of this.cards) {
        if (this.failed[card.work_id] === card.src) activeFailures[card.work_id] = card.src;
      }
      this.failed = activeFailures;
    },
  },
  mounted() {
    // NiceGUI initializes path_prefix in the parent app's mounted hook.
    this.$nextTick(() => { this.ready = true; });
    this.resizeObserver = new ResizeObserver(() => {
      const tracks = getComputedStyle(this.$refs.grid).gridTemplateColumns;
      if (tracks !== 'none') this.columns = tracks.split(' ').length;
    });
    this.resizeObserver.observe(this.$refs.grid);
  },
  beforeUnmount() {
    this.resizeObserver.disconnect();
  },
  methods: {
    imageSource(src) {
      if (src.startsWith('/media/')) return (window.path_prefix || '') + src;
      return src.startsWith('data:image/') ? src : undefined;
    },
    clearFailure(card) {
      if (this.failed[card.work_id] === card.src) delete this.failed[card.work_id];
    },
    send(card, action, epoch = this.render_epoch) {
      if (this.grid_active) this.$emit('card-action', {work_id: card?.work_id || null, action, epoch});
    },
    openCard(card) {
      if (card.src) this.send(card, 'open');
    },
    onKey(event, card) {
      if (event.repeat || event.isComposing) return;
      if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
        const bounds = event.currentTarget.getBoundingClientRect();
        this.contextMenu(event, card, bounds.left, bounds.bottom);
      } else if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        event.stopPropagation();
        this.openCard(card);
      }
    },
    async contextMenu(event, card, x = event.clientX, y = event.clientY) {
      if (!this.grid_active || !card.menu?.length) return;
      event.preventDefault();
      event.stopPropagation();
      this.menuOpener = event.currentTarget;
      this.menuOpener.focus({preventScroll: true});
      this.menuCard = card;
      this.menuEpoch = this.render_epoch;
      this.menuX = x;
      this.menuY = y;
      await this.$nextTick();
      if (this.menuEpoch === this.render_epoch && this.grid_active) this.$refs.menu?.show();
    },
    menuAction(event, entry) {
      if (!entry.enabled || this.menuEpoch !== this.render_epoch || !this.grid_active) {
        event.preventDefault();
        return;
      }
      if (!entry.href) this.send(this.menuCard, entry.key, this.menuEpoch);
      this.$refs.menu?.hide();
    },
    onMenuHide() {
      if (this.grid_active && this.menuEpoch === this.render_epoch && this.menuOpener?.isConnected) {
        this.menuOpener.focus({preventScroll: true});
      }
      this.menuCard = this.menuOpener = null;
    },
  },
};
