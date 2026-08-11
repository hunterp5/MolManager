# This file is part of MolManager.
# Copyright (C) 2026 Hunter Picard
#
# MolManager is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# MolManager is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MolManager.  If not, see <https://www.gnu.org/licenses/>.

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
