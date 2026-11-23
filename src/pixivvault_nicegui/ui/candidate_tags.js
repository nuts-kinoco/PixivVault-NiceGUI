(() => {
    if (window.pvCandidateTags) return;
    window.pvCandidateTags = true;
    const boxes = new Set();
    const rowOf = box => box.querySelector('.pv-candidate-row');
    const moreOf = box => box.querySelector('.pv-candidate-more');
    const menuOf = box => box.querySelector('.pv-candidate-menu');
    const buttons = node => [...node.querySelectorAll('[data-candidate-index]')];
    const open = box => moreOf(box)?.getAttribute('aria-expanded') === 'true';

    function close(box, restore = false) {
        const more = moreOf(box), menu = menuOf(box);
        if (!more || !menu) return;
        more.setAttribute('aria-expanded', 'false');
        if (menu.matches(':popover-open')) menu.hidePopover();
        menu.hidden = true;
        if (restore) more.focus();
    }

    function positionMenu(more, menu) {
        const {right, bottom} = more.getBoundingClientRect();
        menu.style.left = Math.max(8, Math.min(right - menu.offsetWidth, innerWidth - menu.offsetWidth - 8)) + 'px';
        menu.style.top = Math.max(8, Math.min(bottom + 4, innerHeight - menu.offsetHeight - 8)) + 'px';
    }

    function layout(box) {
        const row = rowOf(box), more = moreOf(box), menu = menuOf(box);
        if (!row || !more || !menu || !box.getClientRects().length) return;
        const items = buttons(row), choices = buttons(menu);
        const active = document.activeElement;
        const activeIndex = active?.dataset.candidateIndex;
        const activeHere = box.contains(active);
        const wasOpen = open(box);
        const room = box.clientWidth;
        const gap = parseFloat(getComputedStyle(row).columnGap) || 0;
        const width = items.map(item => item.getBoundingClientRect().width);
        let visible = items.length;
        const total = width.reduce((sum, value) => sum + value, 0) + gap * Math.max(0, items.length - 1);
        if (total > room) {
            more.hidden = false;
            const label = more.querySelector('.pv-candidate-count');
            let used = 0;
            visible = 0;
            for (const size of width) {
                label.textContent = '+' + (items.length - visible);
                if (used + size + gap + more.offsetWidth + gap > room) break;
                used += size + gap;
                visible++;
            }
            const rest = items.length - visible;
            label.textContent = '+' + rest;
            more.title = 'ほか ' + rest + ' 件';
            more.setAttribute('aria-label', '残り ' + rest + ' 件のよく効くタグを表示');
        } else {
            close(box);
            more.hidden = true;
        }
        items.forEach((item, index) => { item.style.visibility = index < visible ? '' : 'hidden'; });
        choices.forEach((item, index) => { item.hidden = index < visible; });
        if (open(box)) positionMenu(more, menu);
        if (!activeHere) return;
        if (active === more && more.hidden || activeIndex !== undefined && !active.getClientRects().length ||
                activeIndex !== undefined && getComputedStyle(active).visibility === 'hidden') {
            const item = items[Number(activeIndex)] ?? items[visible - 1];
            if (item && getComputedStyle(item).visibility !== 'hidden') item.focus();
            else if (!more.hidden) more.focus();
            else box.closest('.pv-personal-candidates')?.querySelector('[data-testid=personal-candidates-toggle]')?.focus();
        } else if (wasOpen && !open(box) && menu.contains(active)) {
            items[Number(activeIndex)]?.focus();
        }
    }

    const resize = new ResizeObserver(entries => entries.forEach(entry => layout(entry.target)));
    function discover() {
        for (const box of boxes) {
            if (!box.isConnected) {
                resize.unobserve(box);
                boxes.delete(box);
            }
        }
        for (const box of document.querySelectorAll('.pv-candidates-box')) {
            if (!boxes.has(box)) {
                boxes.add(box);
                resize.observe(box);
            }
            layout(box);
        }
    }
    function start() {
        new MutationObserver(mutations => {
            if (mutations.some(({addedNodes, removedNodes}) => [...addedNodes, ...removedNodes]
                .some(node => node.nodeType === Node.ELEMENT_NODE))) discover();
        }).observe(document.body, {subtree: true, childList: true});
        document.fonts.ready.then(discover);
        document.fonts.addEventListener('loadingdone', discover);
        discover();
    }
    document.addEventListener('click', event => {
        for (const box of boxes) {
            const more = moreOf(box), menu = menuOf(box);
            if (more?.contains(event.target)) {
                if (open(box)) close(box);
                else {
                    more.setAttribute('aria-expanded', 'true');
                    menu.hidden = false;
                    menu.showPopover();
                    positionMenu(more, menu);
                }
            } else if (open(box) && menu?.contains(event.target) && event.target.closest('[data-candidate-index]')) {
                close(box, true);
            } else if (open(box) && !box.contains(event.target)) {
                close(box);
            }
        }
    });
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || event.isComposing || event.keyCode === 229) return;
        const box = event.target.closest('.pv-candidates-box');
        if (box && open(box)) {
            event.preventDefault();
            event.stopPropagation();
            close(box, true);
        }
    }, true);
    if (document.body) start();
    else document.addEventListener('DOMContentLoaded', start, {once:true});
})();
