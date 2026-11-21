"""Compare deterministic placeholder screenshots with the round-one candidate."""
import json
from pathlib import Path

from PIL import Image, ImageChops

evidence = Path(__file__).resolve().parent
result = {}
for width in (1280, 1920):
    with (
        Image.open(evidence.parent / f"stable-a3-{width}" / "stable.png") as before,
        Image.open(evidence / f"stable-{width}.png") as after,
    ):
        assert before.size == after.size
        difference = ImageChops.difference(before.convert("RGB"), after.convert("RGB"))
        mask = ImageChops.lighter(ImageChops.lighter(*difference.split()[:2]),
                                  difference.getchannel("B"))
        changed = sum(mask.histogram()[1:])
        pixels = before.width * before.height
        result[str(width)] = {
            "size": list(before.size),
            "changed_pixels": changed,
            "fraction": changed / pixels,
            "bounding_box": difference.getbbox(),
            "header_changed_pixels": sum(mask.crop((0, 0, width, 60)).histogram()[1:]),
            "body_changed_pixels": sum(mask.crop((0, 60, width, before.height)).histogram()[1:]),
        }
(evidence / "pixel-summary.json").write_text(
    json.dumps(result, indent=2) + "\n", encoding="utf-8"
)
