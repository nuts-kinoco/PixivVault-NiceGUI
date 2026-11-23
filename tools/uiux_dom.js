(options = {}) => {
  const CONTROL = 'button, a[href], input:not([type=hidden]), select, textarea, summary,' +
    '[role=button], [role=link], [role=checkbox], [role=radio], [role=switch], [role=tab],' +
    '[role=option], [role=combobox], [role=slider], [role=menuitem], [tabindex]';
  const ICON = '.q-icon,.material-icons,.material-symbols-outlined,svg';
  const modals = '.q-dialog__inner,.q-menu,[role=dialog],[role=alertdialog]';
  const css = new Map();
  const identifiers = new Map();
  const style = e => {
    if (!css.has(e)) css.set(e, getComputedStyle(e));
    return css.get(e);
  };
  const dialog = [...document.querySelectorAll(modals)].filter(
    e => e.checkVisibility({visibilityProperty:true, opacityProperty:true})).at(-1);
  const root = options.root ? document.querySelector(options.root) : dialog || document.body;
  const rect = e => {
    const box = e.getBoundingClientRect();
    return [box.left, box.top, box.width, box.height].map(n => Math.round(100 * n) / 100);
  };
  const visible = e => e.checkVisibility({visibilityProperty:true, opacityProperty:true}) &&
    rect(e).slice(2).every(n => n > 0);
  const hitRect = (e, r) => {
    const [x,y,w,h] = r;
    if (Math.min(w,h) >= 28) return null;
    const hit = (left, top) => {
      const target = document.elementFromPoint(left,top);
      return target && (target === e || e.contains(target));
    };
    if (!hit(x+w/2,y+h/2)) return null;
    const extension = (axis, direction) => {
      let extra = 0;
      const edge = axis ? y + (direction > 0 ? h : 0) : x + (direction > 0 ? w : 0);
      for (let offset = .5; offset <= 28; offset += 1) {
        const coordinate = edge + offset * direction;
        if (!(axis ? hit(x+w/2,coordinate) : hit(coordinate,y+h/2))) break;
        extra = offset;
      }
      return extra;
    };
    const left = extension(0,-1), right = extension(0,1);
    const top = extension(1,-1), bottom = extension(1,1);
    return [x-left,y-top,w+left+right,h+top+bottom];
  };
  const selector = e => {
    if (identifiers.has(e)) return identifiers.get(e);
    if (e === document.body || e === document.documentElement) return e.localName;
    const id = e.getAttribute('data-testid');
    // Dynamic data IDs and generated framework IDs are omitted from evidence.
    let result;
    if (id && /^[a-z][a-z_-]+$/.test(id) &&
        document.querySelectorAll('[data-testid="' + id + '"]').length === 1) {
      result = '[data-testid="' + id + '"]';
    } else {
      const siblings = [...e.parentElement.children].filter(s => s.localName === e.localName);
      result = selector(e.parentElement) + ' > ' + e.localName + ':nth-of-type(' +
        (siblings.indexOf(e) + 1) + ')';
    }
    identifiers.set(e, result);
    return result;
  };
  const textOf = e => {
    const walker = document.createTreeWalker(e, NodeFilter.SHOW_TEXT);
    const chunks = [];
    for (let n; (n = walker.nextNode());) {
      if (!n.parentElement.closest(ICON + ',[aria-hidden=true]') &&
          n.parentElement.checkVisibility({visibilityProperty:true})) chunks.push(n.textContent.trim());
    }
    return chunks.filter(Boolean).join(' ').trim();
  };
  const accessibleName = e => {
    const refs = (e.getAttribute('aria-labelledby') || '').trim().split(/\s+/);
    const referenced = refs.map(id => document.getElementById(id)?.textContent.trim() || '').join(' ').trim();
    const explicit = referenced || e.getAttribute('aria-label')?.trim() ||
      [...(e.labels || [])].map(textOf).join(' ').trim();
    if (explicit) return explicit;
    const contentRole = /^(button|link|tab|radio|checkbox|switch|option|menuitem)$/.test(e.getAttribute('role') || '') ||
      e.matches('button,a,summary');
    return (contentRole && (textOf(e) || e.querySelector('img[alt]')?.getAttribute('alt'))) ||
      e.getAttribute('alt') || e.getAttribute('title') || '';
  };

  const canvas = document.createElement('canvas').getContext('2d', {willReadFrequently:true});
  const colors = new Map();
  const rgba = color => {
    if (!colors.has(color)) {
      canvas.clearRect(0, 0, 1, 1);
      canvas.fillStyle = '#000';
      canvas.fillStyle = color;
      canvas.fillRect(0, 0, 1, 1);
      const [r,g,b,a] = canvas.getImageData(0, 0, 1, 1).data;
      colors.set(color, [r,g,b,a / 255]);
    }
    return [...colors.get(color)];
  };
  const over = (top, bottom) => {
    const a = top[3] + bottom[3] * (1 - top[3]);
    return a ? [0,1,2].map(i => (top[i] * top[3] + bottom[i] * bottom[3] * (1 - top[3])) / a)
      .concat(a) : [0,0,0,0];
  };
  const effectiveColor = (e, ink) => {
    let background = [0,0,0,0], foreground = rgba(ink), uncertainColor = '';
    for (let el = e; el; el = el.parentElement) {
      const s = style(el);
      if (background[3] < .999 && s.backgroundImage !== 'none') uncertainColor = '背景画像・グラデーション';
      if (s.mixBlendMode !== 'normal' || s.filter !== 'none' || s.backdropFilter !== 'none') {
        uncertainColor = 'フィルター・ブレンド';
      }
      // The opacity multiplies the fully composed element group.
      const fill = rgba(s.backgroundColor);
      background = over(background, fill);
      foreground = over(foreground, fill);
      background[3] *= Number(s.opacity);
      foreground[3] *= Number(s.opacity);
    }
    const scheme = style(document.documentElement).colorScheme.split(' ');
    const darkCanvas = scheme.includes('dark') && (!scheme.includes('light') ||
      matchMedia('(prefers-color-scheme: dark)').matches);
    const defaultCanvas = darkCanvas ? [18,18,18,1] : [255,255,255,1];
    return {foreground:over(foreground, defaultCanvas).slice(0, 3),
            background:over(background, defaultCanvas).slice(0, 3), uncertainColor};
  };
  const fullText = (e, t) => {
    const owner = e.closest('[title],[aria-describedby],[aria-controls][aria-expanded]');
    if (!owner) return false;
    const title = owner.getAttribute('title');
    const describes = (owner.getAttribute('aria-describedby') || '').split(/\s+/)
      .map(id => document.getElementById(id)?.textContent.trim()).filter(Boolean).join(' ');
    return Boolean(t && (title?.includes(t) || describes.includes(t) ||
      document.getElementById(owner.getAttribute('aria-controls'))?.textContent.includes(t)));
  };
  const motion = e => {
    if (!e.getAnimations) return [];
    return e.getAnimations().filter(a => a.playState === 'running' || a.pending).map(a => {
      const timing = a.effect?.getTiming() || {};
      return {selector:selector(e), repeated:timing.iterations > 1, duration:timing.duration,
              animation:a.constructor.name === 'CSSAnimation'};
    });
  };
  const currentFocus = () => {
    const e = document.activeElement;
    if (!e || e === document.body || e === document.documentElement) return null;
    const s = style(e);
    return {
      selector:selector(e), rect:rect(e), inside:!dialog || dialog.contains(e),
      outline: s.outlineStyle === 'none' || parseFloat(s.outlineWidth) === 0 ? null :
        effectiveColor(e.parentElement, s.outlineColor),
      offset: parseFloat(s.outlineOffset),
      shadow: s.boxShadow !== 'none',
      focusOverlay: [e,...e.querySelectorAll('.q-focus-helper')].some(n =>
        ['::before','::after'].some(p => Number(getComputedStyle(n,p).opacity) > 0 &&
          getComputedStyle(n,p).content !== 'none')),
    };
  };
  if (options.focusOnly) return currentFocus();
  const notices = '.q-notification,[data-testid=learning-toast]';
  if (options.startToastWatch) {
    const active = new Map();
    window.__pvAuditToasts = [];
    const sample = () => {
      const now = performance.now();
      for (const e of document.querySelectorAll(notices)) {
        if (!visible(e) || active.has(e) || !textOf(e)) continue;
        const text = textOf(e);
        const row = {selector:selector(e), started:now, duration:null,
          important:/失敗|エラー|警告|確認してください|再試行|回復/.test(text),
          content:/失敗|エラー/.test(text) ? '失敗・エラー' : /完了|成功/.test(text) ? '完了・成功' : '通知',
          close:!!e.querySelector('button,[role=button]')};
        active.set(e,row);
        window.__pvAuditToasts.push(row);
      }
      for (const [e,row] of active) {
        if (!e.isConnected || !visible(e)) {
          row.duration = Math.round(now - row.started);
          active.delete(e);
        }
      }
    };
    new MutationObserver(sample).observe(document.body, {childList:true, subtree:true, attributes:true});
    sample();
    return null;
  }
  if (options.toastOnly) return window.__pvAuditToasts || [];
  if (!root) return null;
  const nodes = [];
  const running = [];
  const headings = 'h1,h2,h3,h4,h5,h6,[role=heading]';
  const viewport = [innerWidth, innerHeight];
  const stateOf = e => {
    const container = e.closest(CONTROL);
    if (e.closest(':disabled,[aria-disabled=true],.disabled')) return 'disabled';
    if (container?.matches(':focus-visible')) return 'focus';
    if (container?.matches(':hover')) return 'hover';
    if (e.closest('[aria-selected=true],[aria-pressed=true],[aria-checked=true],.pv-tab.active,.q-tab--active,.pv-tab--active')) {
      return 'selected';
    }
    return 'normal';
  };
  const elements = new Set([root,...root.querySelectorAll('*')]);
  for (const toast of document.querySelectorAll(notices)) {
    elements.add(toast);
    for (const child of toast.querySelectorAll('*')) elements.add(child);
  }
  for (const e of elements) {
    if (e.closest('script,style,template,svg') || !visible(e)) continue;
    if (options.motion) running.push(...motion(e));
    if (e.closest(ICON)) continue;
    const s = style(e);
    const r = rect(e);
    const role = e.getAttribute('role') || '';
    const control = e.matches(CONTROL) && !e.matches('[tabindex]:not(button,a,input,select,textarea,summary,[role])') &&
      !e.closest('[inert]');
    const button = e.matches('button,[role=button]');
    const direct = [...e.childNodes].filter(n => n.nodeType === Node.TEXT_NODE && n.textContent.trim());
    const ownLabel = direct.map(n => n.textContent.trim()).join(' ');
    const clipping = /hidden|clip/.test(s.overflowX + ' ' + s.overflowY);
    const clipText = clipping && (ownLabel || s.textOverflow === 'ellipsis' ||
      (s.webkitLineClamp && s.webkitLineClamp !== 'none') ? textOf(e) : '');
    const text = ownLabel || clipText || (e.matches('input:not([type=password]),textarea') ?
      e.getAttribute('placeholder') || '' : '');
    const state = stateOf(e);
    if (options.target && !e.closest(options.target)) continue;
    const heading = e.matches(headings) ? (Number(e.localName.at(-1)) || Number(e.getAttribute('aria-level')) || 2) : 0;
    const indicator = e.hasAttribute('data-tone') || /\btext-(negative|warning|positive)\b/.test(e.className) ||
      (control && ['selected','disabled'].includes(state));
    const liveMissing = !e.closest('[role=status],[role=alert],[aria-live]') &&
      e.matches('[data-testid$="-status"],[data-testid$="-error"]') && !!text;
    const ownScroll = /auto|scroll/.test(s.overflowX) && e.clientWidth > 0 && e.scrollWidth > e.clientWidth + 1;
    if (!text && !control && !heading && !indicator && !ownScroll && !e.matches('.q-dialog')) continue;
    const previousControl = control ? e.parentElement.closest(CONTROL) : null;
    const label = control ? accessibleName(e) : '';
    const testId = e.getAttribute('data-testid') || '';
    const cluster = e.parentElement.closest('[role=toolbar],.pv-toolbar,.pv-toolbar-row,.q-card__actions,.pv-nav') ||
      e.parentElement;
    const kind = e.type || e.getAttribute('type');
    const dangerStyle = e.matches('.text-negative,.bg-negative,[color=negative],.q-btn--negative') ||
      /(^|\s)(text|bg)-(negative|danger)(\s|$)/.test(e.className);
    const danger = button && /削除|完全に消|初期化|リセット|破棄|空にする/.test(label);
    const dialogParent = e.closest(modals);
    const headingFont = /Yu Mincho|游明朝|Noto Serif/.test(s.fontFamily);
    const fontSize = parseFloat(s.fontSize);
    const lineRects = new Set();
    for (const n of direct) {
      const range = document.createRange();
      range.selectNode(n);
      for (const box of range.getClientRects()) lineRects.add(Math.round(box.y));
    }
    const pseudoFill = element => ['::before','::after'].some(p => {
      const overlay = getComputedStyle(element,p);
      return overlay.content !== 'none' && Number(overlay.opacity) > 0 && rgba(overlay.backgroundColor)[3] > 0;
    });
    const color = text ? effectiveColor(e, s.color) : {};
    if (text && (e.closest('.pv-card-image,.pv-backup-author-card') ||
        [...(e.closest('button')?.querySelectorAll('.q-focus-helper') || [])].some(pseudoFill))) {
      color.uncertainColor ||= '画像または疑似要素が背景に重なる';
    }
    const scopedScroller = e.closest('.pv-tabs,.pv-tab-strip,.q-tabs__content,[role=tablist]');
    const box = e.parentElement.closest('[class*=ellipsis]');
    const ancestorClip = direct.length && box && box.scrollWidth > box.clientWidth + 1;
    nodes.push({
      selector:selector(e), tag:e.localName, role, control, button, text, name:label,
      state, rect:r, hitRect:control ? hitRect(e,r) : null,
      ...color, weight:Number(s.fontWeight), fontSize, font:s.fontFamily,
      bodyText:e.matches('p,li,article') || (text.length >= 70 && !e.closest(ICON) &&
        !e.matches('.pv-field-hint,.pv-subtitle,small,[role=status],[role=alert]')),
      fontIssue: (heading || e.matches('.pv-title')) && !headingFont ? '見出しの computed font-family' :
        (text && !heading && !e.matches('.pv-title') && headingFont ? '本文の computed font-family' : ''),
      sizeIssue: Boolean(e.style.fontSize && !e.style.fontSize.includes('var(') &&
        ![10,11,12,13,14,16,18,20,24,32].includes(fontSize)),
      lines:lineRects.size, lineHeight:parseFloat(s.lineHeight) || null, heading, liveMissing,
      dialogMissing:e.matches('.q-dialog') && !e.matches('[role=dialog],[role=alertdialog]'),
      indicator:indicator ? state !== 'normal' ? state : e.getAttribute('data-tone') || 'status' : '',
      icon:!!e.querySelector(ICON), shape: e.matches('input[type=checkbox],input[type=radio],.q-checkbox,.q-toggle,.q-radio'),
      clientWidth:e.clientWidth,scrollWidth:e.scrollWidth,clientHeight:e.clientHeight,scrollHeight:e.scrollHeight,
      clip:clipping || ancestorClip ? s.textOverflow || 'hidden' : '', ancestorClip:!!ancestorClip,
      fullText:fullText(e,text), ownScroll, intentionalScroll:!!scopedScroller,
      offHorizontal:r[0] < -1 || (r[0] + r[2]) > viewport[0] + 1,
      cluster:selector(cluster), clusterInferred:!cluster.matches('[role=toolbar],.q-card__actions,.pv-toolbar'),
      bordered:s.borderStyle !== 'none' && parseFloat(s.borderWidth) > 0 || e.matches('.q-btn--outline'),
      filled:button && s.backgroundImage === 'none' && (rgba(s.backgroundColor)[3] > .85 ||
        e.matches('.q-btn--standard')) && !e.matches('.q-btn--flat,.q-btn--outline,.pv-tab'),
      nestedControl:!!previousControl, controlContainer:previousControl ? selector(previousControl) : '',
      tabIndex:e.tabIndex, roving: !!e.closest('[role=tablist],[role=radiogroup],[role=menu],[role=listbox]'),
      hiddenHit:s.pointerEvents === 'none' || e.matches('.q-checkbox__native,.q-toggle__native'),
      opens:e.hasAttribute('aria-haspopup') && e.getAttribute('aria-haspopup') !== 'false' ? 'declared' :
        (button && /(?:^|-)open(?:-|$)/.test(testId) && !e.matches('a') ? 'inferred' : ''),
      actionLabel:button && !e.matches('.pv-tab,[role=tab],.q-btn--round,[aria-pressed]') && !!textOf(e),
      danger, dangerStyle, defaultAction:kind==='submit' && !!e.form &&
        [...e.form.elements].find(c => c.type === 'submit') === e || e.matches('[autofocus],[data-default-action=true]'),
      inDialog:!!dialogParent, dialogCancel:!!dialogParent && [...dialogParent.querySelectorAll('button,[role=button]')]
        .some(c => /取り消|取消|キャンセル|戻る|閉じる/.test(accessibleName(c))),
      nameFromDomOnly:!!e.closest('.q-field,.q-uploader') || e.hasAttribute('aria-owns'),
    });
  }
  return {nodes, viewport, documentWidth:document.documentElement.scrollWidth, motion:running,
          modal:dialog ? selector(dialog) : null, focus:currentFocus(),
          theme:document.body.classList.contains('body--dark') ? 'dark' : 'light',
          zoom:devicePixelRatio};
}
