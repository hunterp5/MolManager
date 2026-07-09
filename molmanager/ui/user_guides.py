"""In-app HTML user guide for MolManager."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QPalette
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from .qt_widget_utils import make_window_minimizable

if TYPE_CHECKING:
    from PyQt5.QtWidgets import QWidget


@dataclass(frozen=True)
class GuideEntry:
    """One help topic: stable id, short label, sidebar label, and tooltip."""

    guide_id: str
    menu_label: str
    list_label: str
    blurb: str


@dataclass(frozen=True)
class GuideSection:
    """A group of related help topics shown in the sidebar."""

    title: str
    entries: tuple[GuideEntry, ...]


GUIDE_SECTIONS: tuple[GuideSection, ...] = (
    GuideSection(
        "1 — Start here",
        (
            GuideEntry(
                "overview",
                "Overview",
                "Overview — what MolManager is",
                "Purpose of the app, main menus, and where to find help.",
            ),
            GuideEntry(
                "processes",
                "Background jobs",
                "Processes — running and queued work",
                "View elapsed time, cancel jobs, and clear the queue.",
            ),
        ),
    ),
    GuideSection(
        "2 — Table & selection",
        (
            GuideEntry(
                "table",
                "Table",
                "Table — columns, menus, filters",
                "Sort, filter, and right-click actions on rows and columns.",
            ),
            GuideEntry(
                "edit_menu",
                "Edit",
                "Edit — undo, clipboard, selection",
                "Undo/redo, copy/paste, and row selection shortcuts.",
            ),
            GuideEntry(
                "tools_filter",
                "Filters & search",
                "Filters and in-table search",
                "Substructure and numeric filters plus multi-column search.",
            ),
        ),
    ),
    GuideSection(
        "3 — Open, save, export",
        (
            GuideEntry(
                "file_menu",
                "File",
                "File — import, sessions, export",
                "Load data, save your session, and export rows.",
            ),
        ),
    ),
    GuideSection(
        "4 — Structure tools",
        (
            GuideEntry(
                "tools_chem",
                "Prepare & conformers",
                "Prepare structures, descriptors, conformers",
                "Fast prepare, neutralize, 2D render, descriptors, and conformers.",
            ),
            GuideEntry(
                "tools_ion",
                "Ionization & ADME",
                "pKa, protomers, permeability",
                "pKa prediction, dominant protomer, and permeability models.",
            ),
            GuideEntry(
                "smina_dock",
                "Dock",
                "Dock — Prepare PDB, Prepare, and Smina",
                "PDBFixer receptor cleanup, PDBQT preparation with Meeko, and rigid docking with Smina.",
            ),
        ),
    ),
    GuideSection(
        "5 — Compare & model",
        (
            GuideEntry(
                "fingerprints",
                "Fingerprints",
                "Similarity, diverse subset, cluster",
                "Fingerprint tools under Tools → Fingerprints.",
            ),
            GuideEntry(
                "tools_adv",
                "Decomposition & QSAR",
                "BRICS/RECAP, R-group, QSAR",
                "Fragmentation, recomposition, and predictive models.",
            ),
        ),
    ),
    GuideSection(
        "6 — Charts & analysis",
        (
            GuideEntry(
                "tools_viz",
                "Plotter & sketcher",
                "Plotter, radar, calculator, sketcher",
                "Interactive plots, radar charts, formulas, and drawing.",
            ),
            GuideEntry(
                "data",
                "Analyze Table",
                "Analyze Table — statistics",
                "Column stats, outliers, correlations, and tests.",
            ),
            GuideEntry(
                "data_viz",
                "Embeddings & medchem plots",
                "PCA, t-SNE, UMAP, BOILED-Egg",
                "Dimensionality reduction and medicinal chemistry plots.",
            ),
        ),
    ),
    GuideSection(
        "7 — External data",
        (
            GuideEntry(
                "sql",
                "SQL database",
                "SQL — load query results",
                "Connect with SQLAlchemy and import rows.",
            ),
            GuideEntry(
                "pubchem",
                "PubChem",
                "PubChem — lookup and similarity",
                "Identity search and 2D similarity against PubChem.",
            ),
            GuideEntry(
                "chembl",
                "ChEMBL",
                "ChEMBL — molecules and bioactivity",
                "Resolve SMILES and optional activity/target fields.",
            ),
            GuideEntry(
                "patents",
                "Patents",
                "Patents — SureChEMBL similarity",
                "Patent-linked chemistry similar to your query.",
            ),
        ),
    ),
)

GUIDE_MENU: list[tuple[str, str]] = [
    (entry.guide_id, entry.list_label) for section in GUIDE_SECTIONS for entry in section.entries
]

def _guide_style_sheet(palette: QPalette | None = None) -> str:
    """Build HTML CSS that follows the active Qt palette (light or dark theme)."""
    if palette is None:
        app = QApplication.instance()
        palette = app.palette() if app is not None else QPalette()

    text = palette.color(QPalette.Text).name()
    base = palette.color(QPalette.Base).name()
    link = palette.color(QPalette.Link).name()
    mid = palette.color(QPalette.Mid).name()
    dark = palette.color(QPalette.Window).lightness() < 128

    h2_color = link if dark else "#0d47a1"
    code_bg = "#3a3a3a" if dark else "#f0f0f0"
    tip_bg = "#1a2f4a" if dark else "#f5f9ff"
    tip_border = link

    return f"""
body {{ font-family: Segoe UI, Helvetica, Arial, sans-serif; font-size: 14px; line-height: 1.55;
       color: {text}; background-color: {base}; margin: 14px 18px; max-width: 52em; }}
h2 {{ color: {h2_color}; font-size: 1.35em; margin: 0 0 0.6em 0; padding-bottom: 0.35em;
     border-bottom: 2px solid {mid}; }}
h3 {{ color: {text}; font-size: 1.05em; margin: 1.1em 0 0.45em 0; }}
p {{ margin: 0.55em 0; }}
ul {{ margin: 0.4em 0 0.9em 0; padding-left: 1.35em; }}
li {{ margin: 0.4em 0; }}
b {{ color: {text}; }}
code {{ background: {code_bg}; color: {text}; padding: 1px 5px; border-radius: 3px;
       font-size: 0.92em; }}
.tip {{ background: {tip_bg}; border-left: 3px solid {tip_border}; padding: 8px 12px;
       margin: 0.8em 0; color: {text}; }}
a {{ color: {link}; }}
"""


def iter_guide_entries() -> list[GuideEntry]:
    out: list[GuideEntry] = []
    for section in GUIDE_SECTIONS:
        out.extend(section.entries)
    return out


def guide_entry(guide_id: str) -> GuideEntry | None:
    for entry in iter_guide_entries():
        if entry.guide_id == guide_id:
            return entry
    return None


_GUIDE_HTML: dict[str, str] = {
    "overview": """
<h2>Welcome to MolManager</h2>
<p>MolManager is a desktop application for working with compound collections. Each <b>row</b> is one
compound. The <b>Structure</b> column shows a 2D drawing when RDKit can read the chemistry.</p>
<p>Most heavy calculations run in the <b>background</b> so you can keep scrolling and filtering while
work continues.</p>
<h3>Main menus (top of the window)</h3>
<ul>
<li><b>File</b> — open or import data, save sessions, export, open the selection browser.</li>
<li><b>Edit</b> — undo/redo, copy/paste, delete rows, invert or clear selection.</li>
<li><b>Tools</b> — prepare structures, calculate descriptors, fingerprints, clustering, pKa,
protomers, docking, filters, search, calculator, and sketcher.</li>
<li><b>Data</b> — analyze the table, PCA / t-SNE / UMAP, medchem plots (BOILED-Egg, Golden Triangle),
radar chart, and the plotter.</li>
<li><b>External</b> — SQL databases, PubChem, ChEMBL, and patent chemistry (SureChEMBL).</li>
</ul>
<h3>Top-right buttons</h3>
<ul>
<li><b>User Guide</b> — opens this help window (<b>F1</b>).</li>
<li><b>Processes</b> — lists running and queued background jobs; shows elapsed time and lets you cancel.</li>
</ul>
<div class="tip"><b>Tip:</b> Hover any menu item for a short description before you click it.</div>
<p>The <b>status line</b> at the bottom of the window reports progress for the current tool.</p>
""",
    "processes": """
<h2>Background jobs (Processes)</h2>
<p>Click <b>Processes</b> at the top-right of the window to see what is running.</p>
<h3>What you will see</h3>
<ul>
<li><b>Running</b> jobs — descriptor batches, clustering, export, import, Render 2D, docking, and similar tools.</li>
<li><b>Queued</b> jobs — waiting to start (only one heavy queue job runs at a time).</li>
<li><b>Elapsed</b> — how long each item has been running or waiting.</li>
</ul>
<h3>Actions</h3>
<ul>
<li><b>Cancel</b> — stop the selected running job (when the worker supports cancellation) or remove a queued job.</li>
<li><b>Clear queue</b> — remove all waiting jobs without stopping the one currently executing.</li>
</ul>
<p>Closing the main window attempts to cancel queue work and stop external tools such as Smina.</p>
""",
    "file_menu": """
<h2>File menu — bring data in and save results</h2>
<h3>Open and import</h3>
<ul>
<li><b>Open File</b> (<b>Ctrl+O</b>) — load SDF, MOL, SMILES, CSV, TDT, or PDB into the current session.</li>
<li><b>Import Data</b> — add rows from another file and merge columns with the table you already have.</li>
</ul>
<h3>Sessions</h3>
<ul>
<li><b>Open Session</b> / <b>Save Session</b> — restore or store your table, columns, filters, and related state.</li>
<li><b>New Session</b> — start with an empty table (you will be asked to confirm if you have unsaved work).</li>
<li><b>Duplicate Session</b> — open a second window with a copy of the current data.</li>
</ul>
<h3>Export</h3>
<ul>
<li><b>Export All</b> (<b>Ctrl+S</b>) — every row in the chosen format.</li>
<li><b>Export Selected</b> — only highlighted rows (including large programmatic selections from tools
such as Diverse Subset).</li>
</ul>
<p>Supported export formats include SDF, MOL, SMILES, CSV, TDT, and PDB.</p>
<h3>Browser</h3>
<p><b>Browser</b> opens a window to review and work with the rows you have selected.</p>
""",
    "edit_menu": """
<h2>Edit menu — undo and selection</h2>
<ul>
<li><b>Undo / Redo</b> — reverse many table edits (cells, columns, added or deleted rows).</li>
<li><b>Copy / Paste</b> — works on the current cell selection.</li>
<li><b>Delete Selection</b> (<b>Delete</b>) — permanently removes selected rows.</li>
<li><b>Invert Selection</b> — selects every row that is not currently selected (includes rows hidden by filters).</li>
<li><b>Clear Selection</b> (<b>Ctrl+Shift+D</b>) — removes highlights without deleting data.</li>
<li><b>Clear Table</b> (<b>Ctrl+Shift+Backspace</b>) — removes all rows after confirmation.</li>
</ul>
""",
    "table": """
<h2>The compound table</h2>
<p>The grid is the center of MolManager. Column 0 stores internal row IDs (hidden). The <b>Structure</b>
column shows 2D images when structures are available.</p>
<h3>Column header (right-click the column name)</h3>
<ul>
<li>Sort ascending or descending.</li>
<li>Rename, duplicate, or delete the column.</li>
<li><b>Select</b> — pick the first row for each distinct value in that column.</li>
</ul>
<h3>Cell or row (right-click a cell)</h3>
<ul>
<li>Copy, paste, edit, or clear the cell; duplicate or delete the row.</li>
<li>On structure columns: open in <b>Sketcher</b>, view conformers, view in <b>3D</b> or <b>2D</b>,
or <b>Render 2D</b> for that column.</li>
</ul>
<h3>Filter panel</h3>
<p>Open with <b>Tools → Filter → Toggle Panel</b> or <b>Ctrl+Shift+L</b>. Filter cards hide rows that
do not match. Combine substructure, numeric slider, text, and category filters.</p>
<p>Most tools and plots use <b>visible (filtered) rows</b> unless a dialog offers “only selected rows”.</p>
""",
    "tools_filter": """
<h2>Filters and search</h2>
<h3>Filter cards (Tools → Filter)</h3>
<ul>
<li><b>Add Substructure</b> — keep rows that match a SMARTS pattern on a structure column.</li>
<li><b>Add Slider</b> — numeric range on a column.</li>
<li><b>Add Text</b> — text contains or equals.</li>
<li><b>Add Category</b> — choose from discrete values.</li>
</ul>
<p><b>Disable All Filters</b> turns cards off without deleting them.
<b>Delete All Filters</b> removes every card.</p>
<h3>Search panel (Tools → Search, <b>Ctrl+F</b>)</h3>
<p>Search one or more columns. Add multiple criteria rows and choose <b>AND</b> or <b>OR</b> between them.</p>
<ul>
<li>Combine terms with <code>&amp;</code> (AND) or <code>|</code> / comma (OR), e.g. <code>&gt;10 &amp; &lt;500</code>.</li>
<li>Quote text: <code>"sodium chloride"</code>.</li>
<li>Numeric comparisons (<code>&gt;5</code>), <code>empty</code> / <code>not empty</code>, wildcards in quotes.</li>
<li><b>Substructure</b> mode matches SMILES or SMARTS.</li>
</ul>
""",
    "tools_chem": """
<h2>Structure preparation and conformers</h2>
<p>These tools read chemistry from a structure column and write results back to the table.</p>
<h3>Tools → Prepare Structures</h3>
<ul>
<li><b>Fast Prepare</b> — keep largest fragment, neutralize, then batch Render 2D in one job.</li>
<li><b>Disconnect Largest Fragments</b> — split salts; keep the largest piece.</li>
<li><b>Neutralize</b> — adjust protonation toward net charge 0.</li>
<li><b>Add Explicit Hydrogens</b> — expand implicit H atoms to explicit hydrogens (RDKit AddHs).</li>
<li><b>Render 2D</b> — draw structures into a column; optional “show implicit hydrogens”.</li>
<li><b>Protonate</b> — dominant protomer at a chosen pH; optional 2D render and <b>% Protomer</b> column.</li>
</ul>
<h3>Calculate Descriptors</h3>
<p>Choose descriptor categories (drug-likeness, atom counts, fingerprint on-bits, LogD/LogS when available).
Scope can be all visible rows or <b>only selected rows</b>. New columns appear on the right.</p>
<h3>Tools → Conformations</h3>
<ul>
<li><b>Generate Conformations</b> — ensembles stored in a <b>confs</b> column; optionally append rows or export SDF.</li>
<li><b>Generate Single Conformation</b> — one minimized geometry per row; same optional table/SDF outputs.</li>
<li><b>Superpose Conformers</b> — align structures in <b>confs</b> to a reference.</li>
</ul>
""",
    "fingerprints": """
<h2>Fingerprints (Tools → Fingerprints)</h2>
<h3>Fingerprint Similarity</h3>
<p>Pick a fingerprint type (Morgan, RDK, MACCS, atom pair, topological torsion, and others), a query
structure, and a similarity metric. Scores are written to a new column for rows in scope.</p>
<h3>Bulk Similarity</h3>
<p>Pairwise similarity among <b>selected rows</b> only. Shows summary statistics and the most and least
similar pairs.</p>
<h3>Diverse Subset</h3>
<p>Pick a fingerprint and how many compounds to keep. MolManager selects a <b>maximally diverse</b>
subset (MaxMin algorithm) and can select those rows in the table. Reuses existing on-bits columns when
available.</p>
<h3>Cluster</h3>
<p>Group compounds by fingerprint similarity. Methods include K-means, Butina, Jarvis–Patrick, and
<b>Sphere exclusion (RDKit Leader)</b> for large sets. Exploratory mode tries many parameter sets;
apply the best trial to add a cluster ID column.</p>
""",
    "tools_adv": """
<h2>Decomposition and QSAR</h2>
<h3>R-group decomposition (Tools → R-Group Decomposition)</h3>
<ul>
<li><b>Core-Based Decomposition</b> — labeled core SMARTS and substituent columns per attachment point.</li>
<li><b>BRICS</b> / <b>RECAP Decomposition</b> — retrosynthetic fragments as SMILES columns; optional 2D render.</li>
<li><b>BRICS</b> / <b>RECAP Recomposition</b> — combine fragment columns into new product rows.</li>
</ul>
<h3>QSAR (Tools → QSAR)</h3>
<p>Train regression or classification models: pick an activity column (Y), numeric descriptors and/or
fingerprints (X), and a scikit-learn model. Review validation metrics, then predict in-scope rows into
a new column.</p>
""",
    "tools_ion": """
<h2>Ionization and permeability</h2>
<h3>pKa Predictor</h3>
<p>Estimates microstate pKa values from structures. Filter to most basic or most acidic sites; limit to
selected rows on large tables. Runs through the background queue.</p>
<h3>Protonate (Prepare Structures)</h3>
<p>Writes the <b>dominant protomer</b> at a chosen pH to a new column, with optional <b>% Protomer</b>
and 2D depiction.</p>
<h3>Generate Protomers</h3>
<p>Enumerates protonation/tautomer states with approximate populations at a chosen pH.</p>
<h3>Predict Permeability</h3>
<p>Predicts Caco-2 and MDCK permeability endpoints when the Chemprop model stack is installed.
Download model weights with <code>python scripts/bootstrap_gnn_mtl_model.py</code> if needed.</p>
""",
    "tools_viz": """
<h2>Plots, calculator, and sketcher</h2>
<h3>Plotter (Data → Plotter)</h3>
<p>Scatter, histogram, line, heatmap, box, and violin plots from numeric columns. Uses <b>visible
(filtered) rows</b>. Dock the panel beside the table (<b>Ctrl+Shift+P</b>). Plot selection can sync
back to table row selection.</p>
<h3>Radar Plot (Data → Radar Plot)</h3>
<p>Compare 2–6 numeric properties on a spider chart for chosen rows or the full filtered set.</p>
<h3>Calculator (Tools → Calculator)</h3>
<p>Build a numeric column from expressions like <code>sqrt([MW])</code> using column names in brackets.</p>
<h3>Sketcher (Tools → Sketcher)</h3>
<p>Draw or edit structures; send results to the table or export to a file.</p>
""",
    "data": """
<h2>Analyze Table</h2>
<p><b>Data → Analyze Table</b> summarizes numeric and categorical columns for rows passing your filters
(optional <b>only selected rows</b>).</p>
<ul>
<li>Descriptive statistics, percentiles, and distributions.</li>
<li>Correlation matrices between numeric columns.</li>
<li><b>Outlier detection</b> (IQR, Z-score, modified Z) with <b>Select in Table</b>.</li>
<li>Curve fits and common statistical tests when SciPy is available.</li>
</ul>
<p>Clustering has moved to <b>Tools → Fingerprints → Cluster</b>.</p>
""",
    "data_viz": """
<h2>Embeddings and medchem plots</h2>
<p>These plots use <b>visible (filtered) rows</b> unless limited to a selection. Click or lasso points
to select matching rows when plot sync is enabled.</p>
<h3>Dimensionality reduction (Data menu)</h3>
<ul>
<li><b>Principal Component Analysis</b> — linear projection of chosen descriptor columns.</li>
<li><b>t-SNE</b> — nonlinear 2D embedding (slower on very large sets).</li>
<li><b>UMAP</b> — alternative nonlinear embedding with tunable neighborhood size.</li>
</ul>
<h3>Medicinal chemistry property space</h3>
<ul>
<li><b>BOILED-Egg plot</b> — TPSA vs LogP with absorption / BBB regions.</li>
<li><b>Golden Triangle plot</b> — molecular weight vs LogP with the drug-likeness triangle.</li>
</ul>
<p>Color points by a numeric column to highlight trends across the set.</p>
""",
    "sql": """
<h2>External — SQL database</h2>
<p>Connect with a <b>SQLAlchemy</b> URL (templates for SQLite, PostgreSQL, MySQL, and SQL Server are
provided). Run a <b>SQL query</b> or read a whole <b>table</b>; set row limits for large sources.</p>
<p>Results import best when a <b>SMILES</b> column is present — the app tries common column names to build
structures automatically.</p>
""",
    "pubchem": """
<h2>External — PubChem</h2>
<h3>Identity</h3>
<p>Look up compounds by SMILES or selected table rows; add returned fields as new columns.</p>
<h3>Similarity (2D)</h3>
<p>One query SMILES, minimum Tanimoto, and maximum hits. Similarity scores are stored when available.</p>
<h3>Options</h3>
<ul>
<li><b>Retrieve fields</b> — enable only the properties you need.</li>
<li><b>Add unique structures only</b> — skip rows that already match canonical SMILES in the table.</li>
</ul>
""",
    "chembl": """
<h2>External — ChEMBL</h2>
<h3>Identity</h3>
<p>Resolve canonical SMILES through ChEMBL and pull molecule properties.</p>
<h3>Similarity</h3>
<p>Similarity search with ChEMBL’s threshold scale (70–100). Hits sort by decreasing similarity.</p>
<h3>Activities and targets</h3>
<p>Optional bioactivity and target fields — enable for smaller batches to avoid huge downloads.
<b>Add unique structures only</b> avoids duplicate rows.</p>
""",
    "patents": """
<h2>External — Query Patents (SureChEMBL)</h2>
<p>Similarity search against patent-linked chemistry in SureChEMBL (EMBL-EBI). Uses Morgan fingerprint
Tanimoto on the server.</p>
<p>Each hit can include a similarity score. Use <b>Add unique structures only</b> to skip structures
already in your table.</p>
""",
    "smina_dock": """
<h2>Docking</h2>
<p><b>Tools → Dock</b> groups receptor PDB cleanup, PDBQT preparation, and rigid receptor–ligand docking.</p>
<h3>Prepare PDB (PDBFixer)</h3>
<p><b>Tools → Dock → Prepare PDB</b> cleans a receptor <b>PDB</b> before docking:</p>
<ul>
<li>Remove heterogens (ligands, ions, buffers); optionally keep crystallographic waters</li>
<li>Replace non-standard residues (e.g. selenomethionine → methionine)</li>
<li>Add missing heavy atoms and hydrogens at a chosen pH (default 7.0)</li>
</ul>
<p>Install <b>pdbfixer</b> and <b>OpenMM 8.2.x</b> (<code>pip install pdbfixer 'openmm&gt;=8.2,&lt;8.3'</code>).
OpenMM 8.3+ can crash during hydrogen placement on Windows. If the Meeko Prepare dialog is open,
the output path is copied into its receptor PDB field.</p>
<h3>Prepare (Meeko)</h3>
<p><b>Tools → Dock → Prepare</b> generates receptor and/or ligand <b>PDBQT</b> files from:</p>
<ul>
<li>Receptor <b>PDB</b> input</li>
<li>Ligand <b>SDF</b>, one-SMILES-per-line text, or <b>selected table rows</b></li>
</ul>
<p>If the Smina dialog is already open, generated paths are copied into its receptor/ligand fields.</p>
<h3>Smina</h3>
<p><b>Tools → Dock → Smina</b> runs rigid docking in a user-defined search box.</p>
<ul>
<li>Install <b>smina</b> and place it on your PATH, or copy the binary into
<code>molmanager/resources/bin/&lt;platform&gt;/</code>.</li>
<li>Set receptor and ligand PDBQT paths (from Prepare or your own files).</li>
<li>Set search box center and size (Å) aligned with the receptor.</li>
<li>Choose output path for docked poses and adjust exhaustiveness, modes, and CPU threads.</li>
</ul>
<p>The run appears in <b>Processes</b> as <b>(smina)</b>. Cancel from the dialog or from Processes.</p>
""",
}


def guide_html(guide_id: str, palette: QPalette | None = None) -> str:
    body = _GUIDE_HTML.get(guide_id) or _GUIDE_HTML["overview"]
    return (
        f"<html><head><style>{_guide_style_sheet(palette)}</style></head>"
        f"<body>{body}</body></html>"
    )


def _populate_guide_list(lst: QListWidget, *, select_guide_id: str | None = None) -> None:
    lst.clear()
    select_row = 0
    row = 0
    header_font = QFont(lst.font())
    header_font.setBold(True)

    for section in GUIDE_SECTIONS:
        header = QListWidgetItem(section.title)
        header.setFlags(Qt.NoItemFlags)
        header.setFont(header_font)
        header.setForeground(lst.palette().mid())
        lst.addItem(header)
        row += 1

        for entry in section.entries:
            it = QListWidgetItem(entry.list_label)
            it.setData(Qt.UserRole, entry.guide_id)
            it.setToolTip(entry.blurb)
            lst.addItem(it)
            if select_guide_id and entry.guide_id == select_guide_id:
                select_row = row
            row += 1

    lst.setCurrentRow(select_row)


def open_user_guide_dialog(parent: QWidget | None, guide_id: str = "overview") -> None:
    """Open the user guide (modeless). Reuses an existing window when possible."""
    host = parent
    dlg = getattr(host, "_user_guide_dialog", None) if host is not None else None
    if dlg is not None:
        try:
            _show_guide_dialog(dlg, guide_id)
            dlg.show()
            dlg.raise_()
            dlg.activateWindow()
            return
        except RuntimeError:
            if host is not None:
                host._user_guide_dialog = None

    dlg = QDialog(parent)
    dlg.setWindowTitle("MolManager — User Guide")
    dlg.resize(900, 620)
    dlg.setModal(False)
    dlg.setWindowModality(Qt.NonModal)

    outer = QVBoxLayout(dlg)
    content = QHBoxLayout()
    lst = QListWidget()
    lst.setMinimumWidth(280)
    _populate_guide_list(lst, select_guide_id=guide_id)

    browser = QTextBrowser()
    browser.setOpenExternalLinks(True)
    content.addWidget(lst)
    content.addWidget(browser, 1)
    outer.addLayout(content)

    close_btn = QPushButton("Close")
    close_btn.clicked.connect(dlg.close)
    outer.addWidget(close_btn)

    dlg._guide_list = lst  # type: ignore[attr-defined]
    dlg._guide_browser = browser  # type: ignore[attr-defined]

    def on_pick(current: QListWidgetItem | None, _previous: QListWidgetItem | None = None) -> None:
        if current is None:
            return
        gid = current.data(Qt.UserRole)
        if isinstance(gid, str):
            _show_guide_dialog(dlg, gid)

    lst.currentItemChanged.connect(on_pick)
    make_window_minimizable(dlg)

    if host is not None:
        host._user_guide_dialog = dlg
        dlg.destroyed.connect(lambda: setattr(host, "_user_guide_dialog", None))

    _show_guide_dialog(dlg, guide_id)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


def _show_guide_dialog(dlg: QDialog, guide_id: str) -> None:
    lst = getattr(dlg, "_guide_list", None)
    browser = getattr(dlg, "_guide_browser", None)
    if browser is not None:
        browser.setHtml(guide_html(guide_id, dlg.palette()))
    entry = guide_entry(guide_id)
    if entry is not None:
        dlg.setWindowTitle(f"MolManager — User Guide: {entry.menu_label}")
    else:
        dlg.setWindowTitle("MolManager — User Guide")
    if lst is not None:
        for i in range(lst.count()):
            it = lst.item(i)
            if it is not None and it.data(Qt.UserRole) == guide_id:
                lst.setCurrentRow(i)
                break
