(target) => {
  const viewport = [0, 0, innerWidth, innerHeight];
  const box = e => {
    const {left, top, right, bottom} = e.getBoundingClientRect();
    return [left, top, right, bottom];
  };
  const client = e => {
    const [x, y] = box(e);
    return [x + e.clientLeft, y + e.clientTop,
            x + e.clientLeft + e.clientWidth, y + e.clientTop + e.clientHeight];
  };
  const size = r => [r[0], r[1], Math.max(0, r[2] - r[0]), Math.max(0, r[3] - r[1])];
  const clips = value => /^(auto|scroll|hidden|clip|overlay)$/.test(value);
  const clip = (element, rect) => {
    const r = [...rect];
    for (let e = element.parentElement; e; e = e.parentElement) {
      const css = getComputedStyle(e);
      if (css.display === 'contents') continue;
      const bounds = client(e);
      if (clips(css.overflowX)) {
        r[0] = Math.max(r[0], bounds[0]); r[2] = Math.min(r[2], bounds[2]);
      }
      if (clips(css.overflowY)) {
        r[1] = Math.max(r[1], bounds[1]); r[3] = Math.min(r[3], bounds[3]);
      }
      if (css.position === 'fixed') break;
    }
    return size([Math.min(innerWidth, Math.max(0, r[0])), Math.min(innerHeight, Math.max(0, r[1])),
                 Math.min(innerWidth, r[2]), Math.min(innerHeight, r[3])]);
  };
  const shown = e => {
    const [,, w, h] = size(box(e));
    return w > 0 && h > 0 && e.checkVisibility({visibilityProperty:true, opacityProperty:true});
  };
  const first = selector => selector && [...document.querySelectorAll(selector)].find(shown);
  const root = first(target.root) || document;
  const region = first(target.region);
  const within = selector => selector && region ? [...region.querySelectorAll(selector)].filter(shown) : [];
  const items = within(target.item);
  const artwork = e => {
    if (e.tagName === 'IMG') return e.complete && e.naturalWidth > 0;
    return getComputedStyle(e).backgroundImage.startsWith('url(');
  };
  const images = within(target.image).filter(artwork);
  const imageBox = e => {
    const r = box(e);
    if (e.tagName !== 'IMG' || getComputedStyle(e).objectFit !== 'contain') return r;
    const [,, w, h] = size(r);
    const scale = Math.min(w / e.naturalWidth, h / e.naturalHeight);
    const dx = (w - e.naturalWidth * scale) / 2, dy = (h - e.naturalHeight * scale) / 2;
    return [r[0] + dx, r[1] + dy, r[2] - dx, r[3] - dy];
  };
  const imageY = images.length ? Math.min(...images.map(e => imageBox(e)[1])) : null;
  const firstImage = images.find(e => imageBox(e)[1] === imageY);
  const imageRect = firstImage ? clip(firstImage, imageBox(firstImage)) : null;
  const fullyVisible = e => {
    const bounds = imageBox(e);
    const [,, width, height] = size(bounds);
    const [,, visibleWidth, visibleHeight] = clip(e, bounds);
    return width > 0 && height > 0 && visibleWidth >= width - .5 && visibleHeight >= height - .5;
  };
  const start = target.start && items[0];
  const regionBox = region && client(region);
  if (regionBox && start) regionBox[1] = Math.max(regionBox[1], box(start)[1]);
  const visible = region && clip(region, regionBox);
  const rootBox = root === document ? viewport : clip(root, box(root));
  const boundary = visible?.[1] ?? (rootBox[1] + (target.chromeLimit === false ? rootBox[3] : 0));
  const unionHeight = ranges => {
    let bottom = 0, result = 0;
    for (const [start, end] of [...ranges].sort((a, b) => a[0] - b[0])) {
      result += Math.max(0, end - Math.max(start, bottom));
      bottom = Math.max(bottom, end);
    }
    return result;
  };
  const covered = [];
  const chrome = Object.fromEntries(Object.entries(target.chrome).map(([name, selectors]) => {
    const seen = new Set();
    const elements = selectors.flatMap(selector => [...document.querySelectorAll(selector)]
      .filter(e => {
        if (seen.has(e) || !shown(e)) return false;
        seen.add(e);
        return true;
      }).flatMap(e => {
        const [, y, w, h] = clip(e, box(e));
        const bottom = Math.min(boundary, y + h);
        if (w <= 0 || bottom <= y) return [];
        return [{selector, y, height:bottom - y}];
      }));
    const ranges = elements.map(({y, height}) => [y, y + height]);
    covered.push(...ranges);
    return [name, {height:unionHeight(ranges), elements}];
  }));
  const ratio = r => r ? r[2] * r[3] / (innerWidth * innerHeight) : null;
  return {
    viewport: viewport.slice(2),
    visible_rect:visible, area_ratio:ratio(target.single ? imageRect : visible),
    first_image_y:imageY, image_rect:imageRect, image_ratio:ratio(imageRect),
    first_image_complete:!!firstImage && fullyVisible(firstImage),
    complete_images:images.filter(fullyVisible).length,
    rendered_images:images.length, visible_images:images.filter(e => {
      const r = clip(e, imageBox(e)); return r[2] > 0 && r[3] > 0;
    }).length,
    first_item_y:items.length ? Math.min(...items.map(e => box(e)[1])) : null,
    items:items.length, scroll_top:region?.scrollTop ?? null,
    chrome, chrome_boundary:boundary, chrome_union_height:unionHeight(covered),
    chrome_unassigned_height:Math.max(0, boundary - unionHeight(covered)),
  };
}
