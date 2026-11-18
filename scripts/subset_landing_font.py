"""Generate the landing font from the official Pretendard v1.3.9 variable font.

Usage: python scripts/subset_landing_font.py /path/to/PretendardVariable.woff2
Requires fonttools[woff]. Re-run after adding Korean landing copy.
"""

from pathlib import Path
import sys

from fontTools import subset
from fontTools.ttLib import TTFont

root = Path(__file__).resolve().parents[1]
font = TTFont(sys.argv[1])
text = "".join(path.read_text() for path in (root / "src").rglob("*.tsx"))
options = subset.Options()
options.flavor = "woff2"
options.name_IDs = ["*"]
options.name_languages = ["*"]
subsetter = subset.Subsetter(options=options)
subsetter.populate(unicodes=set(range(0x20, 0x100)) | set(range(0x2000, 0x2070)) | {ord(char) for char in text})
subsetter.subset(font)
# The OFL reserves the name Pretendard; this modified font uses a new name.
for record in font["name"].names:
    replacement = {
        1: "Stock Report Sans",
        3: "StockReportSans-Landing-1.0",
        4: "Stock Report Sans",
        6: "StockReportSans",
        16: "Stock Report Sans",
        25: "StockReportSans",
    }.get(record.nameID)
    if replacement:
        record.string = replacement.encode(record.getEncoding())
output = root / "public/fonts/stock-report-sans.woff2"
font.save(output)
print(f"Generated {output.name}: {output.stat().st_size:,} bytes")
