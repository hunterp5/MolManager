"""2D structure column layout defaults (shared by UI and background render workers)."""

# Default RDKit draw → QPixmap size for the Structure column.
STRUCTURE_DEPICT_WIDTH = 242
STRUCTURE_DEPICT_HEIGHT = 202
STRUCTURE_ROW_DEFAULT_HEIGHT = 212
# Bond stroke at 1× table resolution (default RDKit is 2.0; thinner lines without supersampling).
STRUCTURE_DEPICT_BOND_LINE_WIDTH = 1.0
# Extra horizontal space in the table column beyond the pixmap (margins / scrollbar slop).
STRUCTURE_COLUMN_HORIZONTAL_PADDING = 28

# Tools → Browser structure preview (higher than the table column pixmap).
BROWSER_STRUCTURE_PREVIEW_MIN_WIDTH = 480
BROWSER_STRUCTURE_PREVIEW_MIN_HEIGHT = 360
