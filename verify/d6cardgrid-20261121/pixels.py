"""Compare fully loaded baseline and candidate ranking windows."""
import json
from pathlib import Path
from PIL import Image, ImageChops, ImageStat

HERE = Path(__file__).resolve().parent
result = {}
for width in (1280, 1920):
    with Image.open(HERE / f'stable-base-{width}' / 'stable.png') as before, \
            Image.open(HERE / f'stable-a3-{width}' / 'stable.png') as after:
        assert before.size == after.size
        diff = ImageChops.difference(before.convert('RGB'), after.convert('RGB'))
    red, green, blue = diff.split()
    changed = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    histogram = changed.histogram()
    total = changed.width * changed.height
    result[str(width)] = dict(size=list(changed.size), different=total - histogram[0],
        percent=round(100 * (total - histogram[0]) / total, 4),
        mean_abs_channel_error=round(sum(ImageStat.Stat(diff).mean) / 3, 6),
        bounds=changed.getbbox())
    diff.save(HERE / f'pixel-diff-{width}.png')
(HERE / 'pixel-summary.json').write_text(json.dumps(result, indent=2), encoding='ascii')
