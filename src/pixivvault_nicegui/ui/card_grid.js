const CardThumbnail = {
  template: `
    <div class="pv-thumb pv-grid-image" :class="{'pv-grid-cover': placeholder}">
      <img :key="attempt" v-show="!failed" :src="src" alt="" :loading="eager ? 'eager' : 'lazy'"
           :fetchpriority="first ? 'high' : undefined" decoding="async"
           @load="failed = false" @error="failed = true" />
      <q-icon v-if="failed && placeholder" :name="placeholder"
              class="pv-grid-library-placeholder" role="img" aria-label="画像未表示"
              aria-hidden="false" data-testid="library-image-placeholder" />
      <div v-else-if="failed" class="pv-grid-image-error column flex-center q-pa-sm"
           role="status" data-testid="thumbnail-unavailable">
        <q-icon name="broken_image" size="32px" />
        <span class="text-caption text-center">画像を取得できませんでした</span>
        <q-tooltip>通信の混雑や取得失敗で画像を表示できません。処理が終わってから画面を再表示してください。</q-tooltip>
      </div>
    </div>
  `,
  props: {src: String, eager: Boolean, first: Boolean, placeholder: String, retry: Number},
  data() {
    return {failed: false, attempt: 0};
  },
  watch: {
    retry() {
      if (this.failed) {
        this.failed = false;
        this.attempt += 1;
      }
    },
  },
};

export default {
  components: { PvCardThumbnail: CardThumbnail },
  template: `
    <div ref="grid" :data-render-epoch="render_epoch">
      <template v-if="layout === 'library-author'">
        <div v-for="(card, index) in cards" :key="card.work_id" class="pv-card pv-library-author-card"
             :data-author-id="card.work_id" :data-representative-id="card.representative_id"
             @contextmenu="contextMenu($event, card)">
          <div class="pv-library-thumb-wrap">
            <div class="pv-library-author-open" role="button" :tabindex="grid_active ? 0 : -1"
                 :aria-label="card.title" aria-haspopup="menu" data-testid="library-author-open"
                 @click="openCard(card)" @keydown="onKey($event, card)">
              <pv-card-thumbnail :key="card.src" :src="imageSource(card.src)"
                                 :eager="index < columns" :placeholder="library_placeholder" />
            </div>
            <q-btn icon="open_in_new" flat dense round class="pv-library-profile"
                   :href="card.profile" :target="card.profile ? '_blank' : undefined"
                   :rel="card.profile ? 'noopener noreferrer' : undefined" :disable="!card.profile"
                   aria-label="pixivの作者ページ" data-testid="library-author-profile">
              <q-tooltip>{{ card.profile ? 'pixivの作者ページを開く' : 'Pixivの作者IDを確認できません' }}</q-tooltip>
            </q-btn>
          </div>
          <div class="pv-library-card-title">{{ card.title }}</div>
          <div class="row pv-library-card-info">
            <div>{{ card.work_count }}点</div><div>閲覧 {{ card.view_count }}回</div><div>{{ card.capacity }}</div>
          </div>
          <div v-if="card.managed" class="pv-library-card-info" data-testid="library-managed-capacity">
            {{ card.managed }}
          </div>
        </div>
      </template>
      <template v-else-if="layout === 'library-page'">
        <div v-for="(card, index) in cards" :key="card.work_id" class="pv-card pv-library-page-card"
             role="button" :tabindex="grid_active && !card.disabled ? 0 : -1"
             :aria-disabled="card.disabled || undefined" :data-work-id="card.artwork_id"
             :data-page-no="card.page_no" @click="openCard(card)" @keydown="onKey($event, card)">
          <pv-card-thumbnail :key="card.src" :src="imageSource(card.src)"
                             :eager="index < columns" :placeholder="library_placeholder" />
          <div class="pv-library-card-title">{{ card.title }}</div>
          <div class="pv-library-card-info">{{ card.info }}</div>
          <div v-if="card.count !== null" class="pv-library-card-info" data-testid="library-page-bookmark-count">
            {{ card.count }}
          </div>
          <q-btn v-if="card.external" label="外部ビューアで開く" flat dense data-testid="library-external-open"
                 @click.stop="send(card, 'external')" @keydown.stop />
          <q-btn v-if="card.similar" label="似た絵を探す" flat dense data-testid="library-similar-open"
                 @click.stop="send(card, 'similar')" @keydown.stop />
          <q-tooltip v-if="card.disabled">この形式の表示は未接続です</q-tooltip>
        </div>
      </template>
      <template v-else-if="layout === 'bookmark'">
        <div v-for="(card, index) in cards" :key="card.work_id" class="pv-card pv-bookmark-card"
             :data-work-id="card.work_id" data-testid="bookmark-card" @contextmenu="contextMenu($event, card)">
          <div class="pv-library-thumb-wrap">
            <div class="pv-library-author-open" role="button"
                 :tabindex="grid_active && !card.disabled ? 0 : -1" :aria-disabled="card.disabled || undefined"
                 :aria-haspopup="card.menu.length ? 'menu' : undefined"
                 aria-label="作品を開く" data-testid="bookmark-open"
                 @click="openCard(card)" @keydown="onKey($event, card)">
              <pv-card-thumbnail v-if="card.src" :key="card.src" :src="imageSource(card.src)"
                                 :eager="index < columns" :retry="retry_epoch" />
              <q-icon v-else name="image" class="pv-thumb" />
              <q-tooltip v-if="card.disabled">表示未対応の形式、または利用できる画像接続がありません</q-tooltip>
            </div>
            <div v-if="card.downloaded" class="pv-bookmark-badge" data-testid="bookmark-downloaded">DL済み</div>
            <q-checkbox class="pv-bookmark-select" :model-value="card.checked"
                        aria-label="選択" data-testid="bookmark-select" :disable="!grid_active"
                        @update:model-value="value => send(card, 'select', render_epoch, value)" />
          </div>
          <div class="pv-card-title">{{ card.title }}</div>
          <div class="pv-card-author">{{ card.author }}</div>
          <div v-if="card.count !== null" class="pv-library-card-info" data-testid="bookmark-card-count">
            {{ card.count }}
          </div>
        </div>
      </template>
      <template v-else>
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
        <pv-card-thumbnail v-if="card.src" :key="card.src" :src="imageSource(card.src)"
                           :eager="index < columns" :first="index === 0" :retry="retry_epoch" />
        <div v-else class="pv-thumb pv-subtitle">画像を取得できません</div>
        <div class="pv-card-meta">
          <div class="pv-card-title">{{ card.title }}</div>
          <div class="pv-card-author">{{ card.author }}</div>
        </div>
      </div>
      </template>
      <div v-if="more_visible" class="pv-more-cell" :class="{'pv-library-more': layout === 'library-page'}">
        <q-btn :outline="!layout" :flat="!!layout" no-caps :color="layout === 'library-page' ? undefined : 'primary'"
               :label="more_text" :stack="layout === 'library-page'"
               :icon="layout === 'library-page' ? 'add_circle_outline' : undefined"
               :data-testid="layout === 'library-page' ? 'library-more' : layout === 'bookmark' ? 'bookmark-more' : undefined"
               :disable="!more_enabled || !grid_active"
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
    layout: String,
    library_placeholder: String,
    grid_active: Boolean,
    render_epoch: Number,
    retry_epoch: Number,
    more_visible: Boolean,
    more_enabled: Boolean,
    more_text: String,
  },
  emits: ['card-action'],
  data() {
    return {columns: 6, resizeObserver: null, menuCard: null, menuOpener: null,
            menuEpoch: 0, menuX: 0, menuY: 0, ready: false};
  },
  watch: {
    render_epoch() { this.$refs.menu?.hide(); },
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
      if (!this.ready || !src) return undefined;
      if (src.startsWith('/media/')) return (window.path_prefix || '') + src;
      return src.startsWith('data:image/') ? src : undefined;
    },
    send(card, action, epoch = this.render_epoch, value) {
      if (this.grid_active) this.$emit('card-action', {work_id: card?.work_id ?? null, action, epoch, value});
    },
    openCard(card) {
      if (!card.disabled && (card.src || this.layout === 'bookmark')) this.send(card, 'open');
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
      this.menuOpener = event.currentTarget.querySelector('[data-testid=library-author-open], [data-testid=bookmark-open]') ||
                        event.currentTarget;
      this.menuOpener.focus({preventScroll: true});
      this.menuCard = card;
      this.menuEpoch = this.render_epoch;
      this.menuX = x;
      this.menuY = y;
      await this.$nextTick();
      if (this.menuEpoch === this.render_epoch && this.grid_active) {
        this.$refs.menu?.show();
        this.$refs.menu?.updatePosition();
      }
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
      if (this.grid_active && this.menuEpoch === this.render_epoch && this.menuOpener?.isConnected &&
          !this.menuOpener.closest('[inert], [aria-hidden="true"]')) {
        this.menuOpener.focus({preventScroll: true});
      }
      this.menuCard = this.menuOpener = null;
    },
  },
};
