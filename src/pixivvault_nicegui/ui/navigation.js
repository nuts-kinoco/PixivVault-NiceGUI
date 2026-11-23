(() => {
    if (window.pvNavigation) return;
    const narrow = matchMedia('(max-width: 640px)');
    const root = node => node?.closest?.('[data-pv-navigation]');
    const toggle = region => region?.querySelector('[data-testid=nav-toggle]');
    const opened = region => toggle(region)?.getAttribute('aria-expanded') === 'true';
    function setOpen(region, value, restore = false) {
        const button = toggle(region);
        if (!button) return;
        button.setAttribute('aria-expanded', String(value));
        button.querySelector('[data-nav-action]').textContent = value ? '全画面の一覧を閉じる' : '全画面の一覧を開く';
        if (restore) button.focus({preventScroll:true});
    }
    document.addEventListener('click', event => {
        const region = root(event.target);
        if (!region || !narrow.matches) return;
        if (toggle(region).contains(event.target)) setOpen(region, !opened(region));
        if (event.target.closest('.pv-tab') && opened(region)) setOpen(region, false);
    });
    document.addEventListener('focusout', event => {
        const region = root(event.target);
        if (opened(region) && event.relatedTarget && !region.contains(event.relatedTarget)) setOpen(region, false);
    });
    const toolbars = new WeakSet();
    const toolbarSelector = 'details[data-pv-toolbar-key]';
    function syncToolbar(details) {
        details.querySelector('summary')?.setAttribute('aria-expanded', String(details.open));
    }
    function setupToolbars() {
        for (const details of document.querySelectorAll(toolbarSelector)) {
            if (toolbars.has(details)) continue;
            toolbars.add(details);
            if (details.dataset.pvToolbarKey !== 'personal') {
                try {
                    const saved = localStorage.getItem('pv-toolbar-' + details.dataset.pvToolbarKey);
                    if (saved !== null) details.open = saved === 'true';
                } catch {}
            }
            syncToolbar(details);
        }
    }
    function start() {
        new MutationObserver(records => {
            if (records.some(record => [...record.addedNodes].some(node =>
                node.nodeType === Node.ELEMENT_NODE && (node.matches(toolbarSelector) || node.querySelector(toolbarSelector))))) {
                setupToolbars();
            }
        }).observe(document.body, {childList:true, subtree:true});
        setupToolbars();
    }
    if (document.body) start();
    else document.addEventListener('DOMContentLoaded', start, {once:true});
    document.addEventListener('toggle', event => {
        const details = event.target;
        if (!details.matches?.(toolbarSelector)) return;
        syncToolbar(details);
        if (details.dataset.pvToolbarKey !== 'personal') {
            try {
                localStorage.setItem('pv-toolbar-' + details.dataset.pvToolbarKey, String(details.open));
            } catch {}
        }
    }, true);
    document.addEventListener('keydown', event => {
        if (event.key !== 'Escape' || event.isComposing || event.keyCode === 229 || event.defaultPrevented) return;
        const region = document.querySelector('[data-pv-navigation]');
        if (narrow.matches && opened(region)) {
            event.preventDefault();
            event.stopPropagation();
            setOpen(region, false, true);
            return;
        }
        if (event.target.closest('.pv-candidates-box [aria-expanded=true], .pv-candidate-menu:not([hidden])')) return;
        const details = event.target.closest(toolbarSelector);
        if (details?.open) {
            event.preventDefault();
            event.stopPropagation();
            details.open = false;
            details.querySelector('summary')?.focus({preventScroll:true});
        }
    }, true);
    narrow.addEventListener('change', () => {
        const region = document.querySelector('[data-pv-navigation]');
        if (!region) return;
        const focused = document.activeElement;
        if (narrow.matches && focused?.closest('.pv-nav')) {
            setOpen(region, false, true);
        } else {
            if (opened(region)) setOpen(region, false);
            if (!narrow.matches && focused === toggle(region)) {
                region.querySelector('.pv-tab[aria-current=page]:not(:disabled)')?.focus();
            }
        }
    });

    const workCards = '[data-testid=bookmark-card], .pv-library-page-card';
    const workPages = '[data-testid=bookmark-host], [data-testid=library-view]';
    const lastWork = new WeakMap();
    document.addEventListener('focusin', event => {
        const card = event.target.closest(workCards);
        const page = card?.closest(workPages);
        if (!page) return;
        lastWork.set(page, {
            id: card.dataset.workId,
            page: card.dataset.pageNo,
            action: event.target.closest('[data-testid=bookmark-select]') ? '[data-testid=bookmark-select]' :
                '[data-testid=bookmark-open]'
        });
    });

    function workTarget(page) {
        const last = lastWork.get(page);
        if (!last) return null;
        const matches = [...page.querySelectorAll(workCards)].filter(card =>
            card.dataset.workId === last.id && card.getClientRects().length);
        const card = matches.find(card => card.dataset.pageNo === last.page) || matches[0];
        if (!card) lastWork.delete(page);
        if (!card) return null;
        const control = card.matches('.pv-library-page-card') ? card : card.querySelector(last.action);
        return control?.tabIndex === 0 ? control : null;
    }

    function focusPage(content, opener) {
        if (!content?.isConnected) return;
        const library = [...content.querySelectorAll(workPages)].find(node => node.getClientRects().length);
        if (!narrow.matches && !library) return;
        const page = library || [...content.children].find(node => node.getClientRects().length);
        const selector = '[data-testid=library-detail-heading], [data-pv-focus-heading], .pv-library-heading, .pv-ud-title, [role=heading], h1, h2';
        const detail = page?.querySelector('[data-testid=library-detail-heading]');
        const heading = detail?.getClientRects().length ? detail :
            [...(page?.querySelectorAll(selector) || [])].find(node => node.getClientRects().length);
        const origin = (library && workTarget(library)) || heading || page;
        if (origin) {
            if (!origin.hasAttribute('tabindex')) origin.setAttribute('tabindex', '-1');
            window.pvFocus.restore({origin, excluded: opener, retiring:true});
        }
    }
    window.pvNavigation = {focusPage};
})();
