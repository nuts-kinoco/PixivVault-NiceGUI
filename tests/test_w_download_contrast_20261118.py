import re

from pixivvault_nicegui.ui import theme, unified_download_view


def contrast(foreground, background):
    def luminance(color):
        rgb = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        rgb = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in rgb]
        return sum(a * b for a, b in zip(rgb, (.2126, .7152, .0722)))
    a, b = sorted((luminance(foreground), luminance(background)))
    return (b + .05) / (a + .05)


def test_download_text_tokens_have_readable_contrast():
    root = theme.CSS.split('body.body--light')[0]
    light = theme.CSS.split('body.body--light', 1)[1].split('}', 1)[0]
    def token(block, name):
        return re.search(r'--' + name + r':\s*(#[0-9a-fA-F]{6})', block).group(1)
    for block, backgrounds in ((light, ('#ffffff', '#eef1f8')),
                               (root, ('#15161c', '#1e202d'))):
        for name in ('pv-download-accent-text', 'pv-download-danger-text'):
            for background in backgrounds:
                assert contrast(token(block, name), background) >= 4.5
    css = unified_download_view.CSS
    for selector, token_name in (
        ('.pv-ud-star[data-on=true]', 'accent'), ('.pv-ud-cardhead .b', 'accent'),
        ('.pv-ud-fav .num', 'accent'), ('.pv-ud-chip .st[data-kind=in_progress]', 'accent'),
        ('.pv-ud-chip .st[data-kind=failed]', 'danger'), ('.pv-ud-bmbox .warn', 'danger'),
        ('.pv-ud-sumbox .v[data-danger=true]', 'danger'),
    ):
        rule = css.split(selector + ' {', 1)[1].split('}', 1)[0]
        assert f'var(--pv-download-{token_name}-text)' in rule
