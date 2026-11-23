(() => {
    if (window.pvFocus) return;

    const scopeSelector = '.q-dialog__inner,.q-menu,.q-notification,[data-pv-focus-region],.pv-content,.pv-header,.pv-shell';
    const stopSelector = 'a[href],button,input,select,textarea,[tabindex],[contenteditable="true"]';
    const valid = (element, scope = document.body) =>
        element instanceof HTMLElement && element.isConnected && scope?.contains(element)
        && element !== document.body && !element.matches(':disabled')
        && !element.closest('[hidden],[inert],[aria-hidden="true"],[aria-disabled="true"]')
        && element.checkVisibility({visibilityProperty: true});

    function centerHit(element, retiring) {
        const rect = element.getBoundingClientRect();
        const x = rect.left + rect.width / 2;
        const y = rect.top + rect.height / 2;
        const hits = rect.width && rect.height ? document.elementsFromPoint(x, y) : [];
        const hit = hits.find(node => !retiring?.contains(node)) ?? null;
        return {y, hit};
    }

    function scrollAwayFromOverlay(element, {y, hit}) {
        if (!hit || hit.contains(element)) return;
        // A toolbar may cover even the center of a short nested scrollport.
        let top = 0, bottom = innerHeight, scrollport = null;
        const documentScroller = document.scrollingElement;
        for (let parent = element.parentElement; parent && parent !== documentScroller; parent = parent.parentElement) {
            const overflow = getComputedStyle(parent).overflowY;
            if (overflow === 'visible') continue;
            const edge = parent.getBoundingClientRect().top + parent.clientTop;
            top = Math.max(top, edge);
            bottom = Math.min(bottom, edge + parent.clientHeight);
            if (!scrollport && overflow !== 'clip' && parent.scrollHeight > parent.clientHeight)
                scrollport = parent;
        }
        const cover = hit.getBoundingClientRect();
        const positions = [cover.top - 8, cover.bottom + 8]
            .filter(position => position > top && position < bottom)
            .sort((a, b) => Math.abs(a - y) - Math.abs(b - y));
        if (positions.length) (scrollport ?? documentScroller)?.scrollBy({top: y - positions[0], behavior: 'instant'});
    }

    function focusVisible(element, {scope = document.body, retiring, preserveScroll = false} = {}) {
        if (!valid(element, scope)) return false;
        if (!element.contains(centerHit(element, retiring).hit)) {
            if (preserveScroll) return false;
            element.scrollIntoView({block: 'center', inline: 'center', behavior: 'instant'});
            const centered = centerHit(element, retiring);
            if (!element.contains(centered.hit)) {
                scrollAwayFromOverlay(element, centered);
                if (!element.contains(centerHit(element, retiring).hit)) return false;
            }
        }
        element.focus({preventScroll: true});
        return document.activeElement === element;
    }

    function modalScope(excluded) {
        const dialogs = [...document.querySelectorAll('.q-dialog--modal .q-dialog__inner')];
        return dialogs.filter(dialog => !excluded?.contains(dialog) && valid(dialog)).at(-1) ?? document.body;
    }

    function stops(scope) {
        return [...(scope?.querySelectorAll(stopSelector) ?? [])]
            .filter(element => element.tabIndex >= 0 && valid(element, scope));
    }

    let deferred = null;
    let provisionalFocus = null;
    let viewportVersion = 0;

    function claimViewport(event) {
        if (!event.isTrusted) return;
        viewportVersion += 1;
        provisionalFocus = null;
        deferred = null;
    }
    for (const event of ['wheel', 'touchmove']) {
        document.addEventListener(event, claimViewport, {capture: true, passive: true});
    }

    function unclaimed(excluded, retiring = false) {
        const active = document.activeElement;
        return active === document.body ||
            (excluded?.contains(active) && (retiring || !valid(active)));
    }

    function restore({origin, excluded, near, alternatives = [], retiring = false,
                      fallbackFor = null, replaceFallback = null, preserveScroll = false,
                      atViewport = viewportVersion}) {
        if (atViewport !== viewportVersion) return;
        // An ordered redraw may finish after recovery has already focused a
        // stand-in. Only the surface that placed it may reclaim that focus.
        const replacing = replaceFallback && provisionalFocus?.surface === replaceFallback &&
            provisionalFocus.element === document.activeElement;
        if (!unclaimed(excluded, retiring) && !replacing) return;
        if (!document.hasFocus()) {
            deferred = {origin, excluded, near, alternatives, fallbackFor, replaceFallback, preserveScroll, atViewport};
            return;
        }
        const scope = modalScope(excluded);
        function focus(element) {
            if (excluded?.contains(element) ||
                !focusVisible(element, {scope, retiring: retiring ? excluded : null, preserveScroll})) return false;
            provisionalFocus = fallbackFor ? {element, surface: fallbackFor} : null;
            return true;
        }

        if (replacing) {
            // Keep the existing safe location if the exact opener is gone.
            focus(origin);
            return;
        }

        // An explicit opener may belong to the main content while the
        // disappearing notification belongs to the header.
        if (focus(origin) || alternatives.some(focus)) return;
        for (let area = near; area && area !== document.body; area = area.parentElement?.closest(scopeSelector)) {
            if (area.matches('.pv-shell,.pv-content') && area !== near) break;
            if (stops(area).some(focus)) return;
            if (area === scope) break;
        }
        if ((scope !== document.body || !document.querySelector('.pv-shell')) && stops(scope).some(focus)) return;

        const heading = [...scope.querySelectorAll('[data-pv-focus-heading], h1, h2, [role="heading"]')]
            .find(element => valid(element, scope) && !excluded?.contains(element));
        const fallback = heading ?? (scope !== document.body ? scope : null);
        if (fallback) {
            if (!fallback.hasAttribute('tabindex')) fallback.tabIndex = -1;
            focus(fallback);
        }
    }

    function originOf(surface) {
        return document.getElementById(surface?.dataset.pvFocusOrigin ?? '') ?? surface?.pvFocusOrigin;
    }

    let tracked = null;
    document.addEventListener('focusin', event => {
        const element = event.target;
        if (!(element instanceof HTMLElement) || element === document.body) return;
        deferred = null;
        if (provisionalFocus?.element !== element) provisionalFocus = null;
        const surface = element.closest('[data-pv-focus-region]');
        const regions = [];
        for (let region = element.closest(scopeSelector); region; region = region.parentElement?.closest(scopeSelector)) {
            regions.push(region);
        }
        const adjacent = element.closest('tr,[role="row"],.pv-card') ?? regions[0];
        const peers = adjacent && !adjacent.matches('.pv-content,.pv-shell') ? stops(adjacent) : [];
        const index = peers.indexOf(element);
        const alternatives = [...peers.slice(index + 1), ...peers.slice(0, Math.max(0, index)).reverse()];
        tracked = {element, surface, origin: originOf(surface), regions, alternatives,
                   viewport: viewportVersion};
    });
    const scrollKeys = new Set(['PageUp','PageDown','Home','End','ArrowUp','ArrowDown']);
    document.addEventListener('keydown', event => {
        if (event.isTrusted && event.key === 'Tab') provisionalFocus = null;
        if (scrollKeys.has(event.key) && !event.target?.closest?.('input,textarea,select,[contenteditable]'))
            claimViewport(event);
    }, true);
    document.addEventListener('pointerdown', event => {
        // Explicitly choosing the stand-in itself also claims it.
        claimViewport(event);
        // A pointer action away from the tracked control is a new user intent.
        if (!tracked?.element.contains(event.target)) {
            tracked = null;
            deferred = null;
        }
    }, true);

    function recover(changes) {
        if (!tracked || !document.hasFocus() || !unclaimed(tracked.element)) return;
        const {element, surface, origin, regions, alternatives, viewport} = tracked;
        if (changes && element.isConnected && !changes.some(change => change.target.contains(element))) return;
        if (valid(element)) return;
        tracked = null;
        // Quasar can keep the outer dialog visible after removing its contents.
        const dialogContent = regions.find(region => region.matches('.q-dialog__inner'));
        if (surface?.hasAttribute('data-pv-focus-after-update') && !valid(dialogContent ?? surface)) return;
        const near = regions.find(region => valid(region)) ?? null;
        // A continuing surface can nominate the successor to a disabled
        // control. When that surface closes, use its recorded page opener.
        const replacement = valid(surface) ? surface.pvFocusReplacement?.(element) : null;
        const fallbackFor = provisionalFocus?.element === element ? provisionalFocus.surface : surface;
        const preserveScroll = viewport !== viewportVersion;
        provisionalFocus = null;
        restore({origin: replacement ?? origin ?? originOf(surface), excluded: element, near, alternatives,
                 fallbackFor: preserveScroll ? null : fallbackFor, preserveScroll});
    }
    // A mutation may have arrived while the window was inactive.
    window.addEventListener('focus', () => {
        const interrupted = deferred;
        deferred = null;
        if (interrupted) restore(interrupted);
        recover();
    });
    window.addEventListener('resize', () => recover());
    const observer = new MutationObserver(recover);
    observer.observe(document.documentElement, {
        childList: true, subtree: true, attributes: true,
        attributeFilter: ['class','style','hidden','inert','disabled','aria-hidden','aria-disabled','tabindex'],
    });

    window.pvFocus = {valid, stops, restore, originOf, focusVisible, viewportVersion: () => viewportVersion};
})();
