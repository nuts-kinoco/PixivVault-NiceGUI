const CardThumbnail = {
  template: `
    <div class="pv-thumb pv-grid-image" :class="{'pv-grid-cover': placeholder}">
      <img :key="attempt" v-if="src !== null" v-show="!failed" :src="src" alt="" :loading="eager ? 'eager' : 'lazy'"
           :fetchpriority="first ? 'high' : undefined" decoding="async"
           @load="failed = false" @error="failed = true" />
      <q-icon v-if="unavailable && placeholder" :name="placeholder"
              class="pv-grid-library-placeholder" role="img" aria-label="画像未表示"
              aria-hidden="false" data-testid="library-image-placeholder" />
      <div v-else-if="unavailable" class="pv-grid-image-error column flex-center q-pa-sm"
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
  computed: {
    unavailable() { return this.failed || this.src === null; },
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
                 :aria-disabled="!grid_active || undefined"
                 @click="openCard(card)" @keydown="onKey($event, card)">
              <pv-card-thumbnail :key="card.src" :src="imageSource(card.src)"
                                 :eager="index < columns * 3" :placeholder="library_placeholder" :retry="retry_epoch" />
            </div>
            <q-btn icon="open_in_new" flat dense round class="pv-library-profile"
                   :href="grid_active ? card.profile : undefined" :target="card.profile ? '_blank' : undefined"
                   :rel="card.profile ? 'noopener noreferrer' : undefined" :disable="!grid_active || !card.profile"
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
             :aria-disabled="!grid_active || card.disabled || undefined" :data-work-id="card.artwork_id"
             :data-page-no="card.page_no" @click="openCard(card)" @keydown="onKey($event, card)">
          <pv-card-thumbnail :key="card.src" :src="imageSource(card.src)"
                             :eager="index < columns * 3" :placeholder="library_placeholder" :retry="retry_epoch" />
          <div class="pv-library-card-title">{{ card.title }}</div>
          <div class="pv-library-card-info">{{ card.info }}</div>
          <div v-if="card.count !== null" class="pv-library-card-info" data-testid="library-page-bookmark-count">
            {{ card.count }}
          </div>
          <q-btn v-if="card.external" label="外部ビューアで開く" :disable="!grid_active" flat dense data-testid="library-external-open"
                 @click.stop="send(card, 'external')" @keydown.stop />
          <q-btn v-if="card.similar" label="似た絵を探す" :disable="!grid_active" flat dense data-testid="library-similar-open"
                 @click.stop="send(card, 'similar')" @keydown.stop />
          <q-tooltip v-if="card.disabled">この形式の表示は未接続です</q-tooltip>
        </div>
      </template>
      <template v-else-if="layout === 'bookmark'">
        <div v-for="(card, index) in cards" :key="card.work_id" class="pv-card pv-bookmark-card"
             :data-work-id="card.work_id" data-testid="bookmark-card" @contextmenu="contextMenu($event, card)">
          <div class="pv-library-thumb-wrap">
            <div class="pv-library-author-open" role="button"
                 :tabindex="grid_active && !card.disabled ? 0 : -1" :aria-disabled="!grid_active || card.disabled || undefined"
                 :aria-haspopup="card.menu.length ? 'menu' : undefined"
                 aria-label="作品を開く" data-testid="bookmark-open"
                 @mousedown.right="card.disabled && $event.preventDefault()"
                 @click="openCard(card)" @keydown="onKey($event, card)">
              <pv-card-thumbnail v-if="card.src" :key="card.src" :src="imageSource(card.src)"
                                 :eager="index < columns * 3" :retry="retry_epoch" />
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
           :tabindex="card.src ? (grid_active ? 0 : -1) : undefined"
           :aria-disabled="card.src && !grid_active || undefined"
           :aria-haspopup="card.menu?.length ? 'menu' : undefined"
           @click="openCard(card)" @keydown="onKey($event, card)"
           @contextmenu="contextMenu($event, card)">
        <div v-if="card.heart" class="row items-center no-wrap gap-1 pv-card-head">
          <div class="pv-card-rank">{{ card.rank }}</div>
          <q-icon name="favorite" size="12px" class="pv-card-heart" aria-label="好みのタグを含む作品"
                  :data-testid="'ranking-heart-' + card.work_id" />
        </div>
        <div v-else class="pv-card-rank">{{ card.rank }}</div>
        <pv-card-thumbnail v-if="card.src" :key="card.src"
                           :src="index && deferSiblingSources ? undefined : imageSource(card.src)"
                           :eager="index < columns * 3" :first="index === 0" :retry="retry_epoch" />
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
      <span v-for="session in menuSession ? [menuSession] : []" :key="session.sequence"
            class="pv-card-menu-anchor" :style="{left: session.x + 'px', top: session.y + 'px'}">
        <q-menu ref="menu" no-parent-event no-refocus anchor="top left" self="top left" role="menu"
                :data-testid="session.card.menu_id" @hide="onMenuHide(session)">
          <q-item v-for="entry in session.card.menu" :key="entry.key" clickable
                  :data-testid="entry.testid" :disable="!entry.enabled || !grid_active" role="menuitem"
                  :href="entry.enabled && grid_active && session.epoch === render_epoch ? entry.href : undefined"
                  :target="entry.href ? '_blank' : undefined" :rel="entry.href ? 'noopener noreferrer' : undefined"
                  @click="menuAction($event, entry, session)">
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
    return {columns: 6, resizeObserver: null, menuSession: null, menuSequence: 0,
            ready: window.path_prefix !== undefined, deferSiblingSources: false};
  },
  watch: {
    cards(cards, previous) {
      this.deferSiblingSources = false;
      if (this.layout || !previous.length || !cards[0]?.src ||
          previous[0].work_id === cards[0].work_id) return;
      const oldIds = new Set(previous.map(card => card.work_id));
      if (cards.some(card => oldIds.has(card.work_id))) return;
      // Vue inserts disjoint keyed children from the end. Let the first
      // thumbnail dispatch before the reverse insertion can start its siblings.
      this.deferSiblingSources = true;
      this.$nextTick(() => {
        if (this.cards === cards) this.deferSiblingSources = false;
      });
    },
    render_epoch() { this.menuSession = null; },
    grid_active(active) { if (!active) this.menuSession = null; },
  },
  mounted() {
    // NiceGUI initializes path_prefix in the parent app's mounted hook.
    if (!this.ready) this.$nextTick(() => { this.ready = true; });
    this.resizeObserver = new ResizeObserver(() => {
      const tracks = getComputedStyle(this.$refs.grid).gridTemplateColumns;
      if (tracks && tracks !== 'none') this.columns = tracks.trim().split(/\s+/).length;
    });
    this.resizeObserver.observe(this.$refs.grid);
  },
  beforeUnmount() {
    this.resizeObserver.disconnect();
  },
  methods: {
    imageSource(src) {
      if (!this.ready) return undefined;
      if (src?.startsWith('/media/')) return (window.path_prefix || '') + src;
      return src?.startsWith('data:image/') ? src : null;
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
      } else if (event.key === 'Enter') {
        event.preventDefault();
        event.stopPropagation();
        this.openCard(card);
      }
    },
    async contextMenu(event, card, x = event.clientX, y = event.clientY) {
      if (!this.grid_active || !card.menu?.length) return;
      event.preventDefault();
      event.stopPropagation();
      const target = event.currentTarget.querySelector('[data-testid=library-author-open], [data-testid=bookmark-open]') ||
                     event.currentTarget;
      const previous = document.activeElement;
      const previousOpener = this.menuSession && previous?.closest('.q-menu')
        ? this.menuSession.opener : previous;
      const opener = target.tabIndex >= 0 ? target :
        (previousOpener !== target && previousOpener?.isConnected ? previousOpener : null);
      if (!opener && previous === target) target.blur();
      opener?.focus({preventScroll: true});
      const sequence = ++this.menuSequence;
      const epoch = this.render_epoch;
      this.menuSession = {sequence, epoch, card, opener, x, y};
      await this.$nextTick();
      if (this.menuSession?.sequence === sequence && epoch === this.render_epoch && this.grid_active) {
        this.$refs.menu?.[0]?.show();
      }
    },
    hideMenu() {
      const menu = this.$refs.menu?.[0];
      if (menu?.$el && this.menuSession) menu.hide();
      else this.menuSession = null;
    },
    menuAction(event, entry, session) {
      if (!entry.enabled || session.sequence !== this.menuSession?.sequence ||
          session.epoch !== this.render_epoch || !this.grid_active) {
        event.preventDefault();
        return;
      }
      if (!entry.href) this.send(session.card, entry.key, session.epoch);
      this.hideMenu();
    },
    onMenuHide(session) {
      if (session.sequence !== this.menuSession?.sequence) return;
      if (this.grid_active && session.epoch === this.render_epoch && session.opener?.isConnected &&
          !session.opener.closest('[inert], [aria-hidden="true"]')) {
        session.opener.focus({preventScroll: true});
      }
      this.menuSession = null;
    },
  },
};
