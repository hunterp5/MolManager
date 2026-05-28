"""In-app HTML user guides for MolManager menus, tools, and external integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QAction,
    QDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from .qt_widget_utils import make_window_minimizable

if TYPE_CHECKING:
    from PyQt5.QtWidgets import QWidget


@dataclass(frozen=True)
class GuideEntry:
    """One help topic: stable id, short menu label, sidebar label, and tooltip blurb."""

    guide_id: str
    menu_label: str
    list_label: str
    blurb: str


@dataclass(frozen=True)
class GuideSection:
    """A group of related help topics (shown as a submenu and sidebar section)."""

    title: str
    entries: tuple[GuideEntry, ...]


GUIDE_SECTIONS: tuple[GuideSection, ...] = (
    GuideSection(
        "Getting started",
        (
            GuideEntry(
                "overview",
                "Overview",
                "Overview — how MolManager is organized",
                "What the app does, where menus live, and how background work is shown.",
            ),
        ),
    ),
    GuideSection(
        "Main window",
        (
            GuideEntry(
                "file_menu",
                "File",
                "File — open, import, sessions, export",
                "Load data, save your working session, and export rows or the full table.",
            ),
            GuideEntry(
                "edit_menu",
                "Edit",
                "Edit — undo, clipboard, selection",
                "Undo/redo, copy/paste, and row selection shortcuts.",
            ),
            GuideEntry(
                "table",
                "Table",
                "Table — columns, filters, context menus",
                "Sort columns, filter rows, and chemistry actions from cell menus.",
            ),
        ),
    ),
    GuideSection(
        "Tools — structures",
        (
            GuideEntry(
                "tools_chem",
                "Prepare & descriptors",
                "Prepare structures, descriptors, conformers",
                "Fast prepare, neutralize, 2D render, RDKit descriptors, and conformer generation.",
            ),
            GuideEntry(
                "smina_dock",
                "Dock (Smina)",
                "Dock (Smina) — rigid receptor–ligand docking",
                "Run smina on PDBQT files with a defined search box; cancel from Processes.",
            ),
        ),
    ),
    GuideSection(
        "Tools — analysis",
        (
            GuideEntry(
                "tools_adv",
                "Decomposition & modeling",
                "R-group tools, fingerprints, QSAR",
                "BRICS/RECAP, core decomposition, similarity columns, and QSAR models.",
            ),
            GuideEntry(
                "tools_ion",
                "Ionization & ADME",
                "pKa, protomers, permeability",
                "Microstate pKa, protomer enumeration, and permeability prediction.",
            ),
            GuideEntry(
                "tools_filter",
                "Filters & search",
                "Filters, search panel, query syntax",
                "Substructure and numeric filters plus multi-column in-table search.",
            ),
        ),
    ),
    GuideSection(
        "Tools — visualization",
        (
            GuideEntry(
                "tools_viz",
                "Plotter & sketcher",
                "Plotter, radar, calculator, sketcher",
                "Interactive plots, radar charts, custom column formulas, and structure drawing.",
            ),
        ),
    ),
    GuideSection(
        "Data menu",
        (
            GuideEntry(
                "data",
                "Analyze & cluster",
                "Analyze Table and clustering",
                "Column statistics, outliers, correlations, and fingerprint clustering.",
            ),
            GuideEntry(
                "data_viz",
                "Plots & embeddings",
                "PCA, t-SNE, UMAP, medchem space",
                "Dimensionality reduction and BOILED-Egg / golden-triangle plots.",
            ),
        ),
    ),
    GuideSection(
        "External sources",
        (
            GuideEntry(
                "sql",
                "SQL database",
                "SQL — load query or table results",
                "Connect with SQLAlchemy and import rows (best with a SMILES column).",
            ),
            GuideEntry(
                "pubchem",
                "PubChem",
                "PubChem — identity and similarity",
                "Look up compounds or run 2D similarity searches against PubChem.",
            ),
            GuideEntry(
                "chembl",
                "ChEMBL",
                "ChEMBL — identity, similarity, bioactivity",
                "Resolve SMILES in ChEMBL and optionally pull activities or targets.",
            ),
            GuideEntry(
                "patents",
                "Patents",
                "Patents — SureChEMBL similarity",
                "Find patent-associated chemistry similar to your query structure.",
            ),
        ),
    ),
    GuideSection(
        "Background work",
        (
            GuideEntry(
                "processes",
                "Processes",
                "Processes — queue, cancel, Render 2D, Smina",
                "See running and queued jobs; cancel cooperatively or stop external tools.",
            ),
        ),
    ),
)

# Flat list for tests and legacy callers.
GUIDE_MENU: list[tuple[str, str]] = [
    (entry.guide_id, entry.list_label) for section in GUIDE_SECTIONS for entry in section.entries
]


def iter_guide_entries() -> list[GuideEntry]:
    """All topics in menu order."""
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
<h2>MolManager overview</h2>
<p>MolManager is a desktop compound table for medicinal and computational chemistry. Each row is one
compound (or record); the <b>Structure</b> column shows 2D depictions when RDKit can parse the chemistry.
Long-running work runs in the background so the table stays usable on large sets.</p>
<h3>Main menus</h3>
<ul>
<li><b>File</b> — open or import SD-type and related files, save/load <b>sessions</b>, export, and open the
<b>Browser</b> for the current selection.</li>
<li><b>Edit</b> — undo/redo, clipboard, delete rows, invert/clear selection, clear the table.</li>
<li><b>Tools</b> — structure preparation, descriptors, conformers, decomposition, similarity, QSAR, pKa,
permeability, protomers, filters, search, calculator, sketcher, and <b>Dock (Smina)</b>.</li>
<li><b>Data</b> — <b>Analyze Table</b>, <b>Cluster</b>, PCA / t-SNE / UMAP, medchem plots (BOILED-Egg,
golden triangle), <b>Radar Plot</b>, and the <b>Plotter</b> (dockable beside the table).</li>
<li><b>External</b> — SQL, PubChem, ChEMBL, and patent chemistry (SureChEMBL).</li>
<li><b>Help</b> (or <b>F1</b>) — grouped topics in this guide; menu tooltips summarize each command.</li>
</ul>
<h3>Status and background work</h3>
<p>The <b>status line</b> at the bottom reports progress for queued tools. <b>Processes</b> (top-right of the
menu bar) lists the process queue, an active <b>Render 2D</b> batch, and an active <b>Smina</b> docking run.
Use it to cancel or dequeue work without closing the app.</p>
<p><b>Tip:</b> Hover any menu item for a short description of what it does before opening a dialog.</p>
""",
    "file_menu": """
<h2>File menu</h2>
<p>Use <b>File</b> to bring data in, persist your session, and write results out. Structures are parsed with
RDKit when possible; failed rows may still load as text.</p>
<h3>Open and import</h3>
<ul>
<li><b>Open File</b> (<b>Ctrl+O</b>) — load a supported file into the current session (replaces or merges
depending on the dialog).</li>
<li><b>Import Data</b> — append or merge from another file with column mapping so fields align with your table.</li>
</ul>
<h3>Sessions</h3>
<ul>
<li><b>Open Session</b> / <b>Save Session</b> — restore or store the working table, columns, filters, and
related UI state so you can resume later.</li>
<li><b>New Session</b> — start empty (with confirmation if you have unsaved work).</li>
<li><b>Duplicate Session</b> — open a second window with a copy of the current data.</li>
</ul>
<h3>Export and browser</h3>
<ul>
<li><b>Export All</b> (<b>Ctrl+S</b>) — every row; <b>Export Selected</b> — only highlighted rows. Choose format
and columns in the export dialog.</li>
<li><b>Browser</b> — review and operate on the current row selection in a dedicated window.</li>
</ul>
""",
    "edit_menu": """
<h2>Edit menu</h2>
<p>Standard editing for the compound table. Selection follows the visible (filtered) rows unless noted.</p>
<ul>
<li><b>Undo / Redo</b> — reverses many table edits (column changes, cell edits, row add/delete).</li>
<li><b>Copy / Paste</b> — works on the current cell selection; SMILES-oriented paste is supported where applicable.</li>
<li><b>Delete Selection</b> (<b>Delete</b>) — removes selected rows permanently.</li>
<li><b>Invert Selection</b> — selects every row that is not currently selected (includes rows hidden by filters).</li>
<li><b>Clear Selection</b> (<b>Ctrl+Shift+D</b>) — clears highlights without deleting data.</li>
<li><b>Clear Table</b> (<b>Ctrl+Shift+Backspace</b>) — removes all rows after confirmation.</li>
</ul>
""",
    "table": """
<h2>Table, columns, and filters</h2>
<p>The grid is the center of the app. Column 0 (<b>ID_HIDDEN</b>) stores internal row IDs (OIDs); the
<b>Structure</b> column shows 2D images when available.</p>
<h3>Column header menu</h3>
<p>Right-click a <b>column name</b> to sort, rename, duplicate, or delete the column; <b>Select</b> picks the
first row per distinct value (on <b>Structure</b>, “empty” means no parseable structure). Use header menus for
numeric vs text sort where supported.</p>
<h3>Cell and row menus</h3>
<p>Right-click a <b>cell</b> for copy/paste, edit, clear, duplicate/delete row, and chemistry actions on
structure-capable columns: <b>Open in Sketcher</b>, <b>View Conformers</b>, <b>View in 3D</b> / <b>2D</b>,
<b>Render 2D</b> (draws into the column you clicked).</p>
<h3>Filter panel</h3>
<p>Toggle with <b>Tools → Filter → Toggle Panel</b> or <b>Ctrl+Shift+L</b>. Cards restrict visible rows;
combine substructure, numeric slider, text, and category filters. <b>Disable All Filters</b> turns cards off
without deleting them; <b>Delete All Filters</b> removes every card.</p>
<p>Plots and many tools use <b>visible (filtered) rows</b> unless a dialog offers “selected rows only”.</p>
""",
    "tools_chem": """
<h2>Prepare structures, descriptors, and conformers</h2>
<p>These tools read chemistry from a chosen structure column (or in-memory molecules) and write results back
to the table or to packed <b>confs</b> data.</p>
<h3>Tools → Prepare Structures</h3>
<ul>
<li><b>Fast Prepare</b> — disconnect largest fragment, neutralize, then batch <b>Render 2D</b> in one queued job.</li>
<li><b>Disconnect Largest Fragments</b> — split salts and multi-component SMILES; keep the largest fragment.</li>
<li><b>Neutralize</b> — adjust protonation toward net charge 0 for the dominant form.</li>
<li><b>Render 2D</b> — batch 2D depictions for a column; appears in <b>Processes</b> while running.</li>
</ul>
<h3>Calculate Descriptors</h3>
<p>Pick categories (Lipinski-style drug-likeness, atom/bond counts, fingerprint on-bits, LogD/LogS when
pkasolver-backed descriptors are enabled, etc.). Scope can be all visible rows or <b>only selected rows</b>.
New numeric columns are added to the right of the table.</p>
<h3>Tools → Conformations</h3>
<ul>
<li><b>Generate Conformations</b> — ensembles stored in a <b>confs</b> column.</li>
<li><b>Generate Single Conformation</b> — one minimized geometry per row.</li>
<li><b>Superpose Conformers</b> — align structures already in <b>confs</b> (reference conformer, optional SMARTS).</li>
</ul>
""",
    "tools_adv": """
<h2>Decomposition, similarity, and QSAR</h2>
<h3>R-group decomposition (Tools → R-Group Decomposition)</h3>
<ul>
<li><b>Core-Based Decomposition</b> — labeled core SMARTS/SMILES and substituent columns per attachment point.</li>
<li><b>BRICS</b> / <b>RECAP Decomposition</b> — retrosynthetic fragments as new SMILES columns (2D render optional).</li>
<li><b>BRICS</b> / <b>RECAP Recomposition</b> — combine fragment columns into new product rows.</li>
</ul>
<h3>Fingerprint Similarity</h3>
<p>Choose a fingerprint type (Morgan/FCFP, RDK path, MACCS, atom pair, topological torsion, pattern, and
related variants), a query from row ID or SMILES, and Tanimoto/Dice/cosine. <b>Compute and Add Column</b>
writes scores for all rows in scope; missing structures show <b>N/A</b>.</p>
<h3>QSAR</h3>
<p>Train regression or classification models: activity column (Y), numeric descriptors and/or 2D fingerprints (X),
scikit-learn model (ridge, random forest, gradient boosting, logistic/SVM). Review hold-out and cross-validation
metrics, then predict in-scope rows into a new column.</p>
""",
    "tools_ion": """
<h2>Ionization and permeability</h2>
<h3>pKa Predictor</h3>
<p>Estimates microstate pKa values from a structure column or a single SMILES. Filter to most basic or most
acidic sites; limit scope to selected rows for large tables. Runs through the <b>process queue</b>.</p>
<h3>Generate Protomers</h3>
<p>Enumerates protonation/tautomer states with approximate populations at a chosen pH; append results as new
rows or review in the dialog.</p>
<h3>Predict Permeability</h3>
<p>Predicts Caco-2 and MDCK permeability / efflux-style endpoints when the optional Chemprop model stack is
installed. Requires parseable structures in scope; progress appears in the status line and <b>Processes</b>.</p>
""",
    "tools_filter": """
<h2>Filters and in-table search</h2>
<h3>Filter cards (Tools → Filter)</h3>
<ul>
<li><b>Add Substructure</b> — SMARTS filter on a structure-capable column.</li>
<li><b>Add Slider</b> — numeric range on a column.</li>
<li><b>Add Text</b> — contains / equals style match.</li>
<li><b>Add Category</b> — multi-select discrete values.</li>
</ul>
<p>Toggle the sidebar with <b>Toggle Panel</b> or <b>Ctrl+Shift+L</b>.</p>
<h3>Search (Tools → Search, Ctrl+F)</h3>
<p>Search one or more columns; <b>Add</b> builds another criterion row; choose <b>AND</b>/<b>OR</b> between rows.
Within a row:</p>
<ul>
<li>Combine terms with <code>&amp;</code> (AND) or <code>|</code> / comma (OR), e.g. <code>&gt;10 &amp; &lt;500</code>.</li>
<li>Quote strings: <code>"sodium chloride"</code> so commas and operators inside text are literal.</li>
<li>Numeric comparisons (<code>&gt;5</code>), <code>empty</code> / <code>not empty</code>, <code>NOT "text"</code>,
<code>= "exact"</code>, wildcards in quotes (<code>"eth*"</code>).</li>
<li><b>Substructure</b> mode matches SMILES/SMARTS; use parentheses if SMARTS contains <code>|</code>.</li>
</ul>
""",
    "tools_viz": """
<h2>Plotter, radar, calculator, and sketcher</h2>
<h3>Plotter (Data → Plotter)</h3>
<p>Open a modeless plot window or dock the panel beside the table (<b>Toggle Panel</b>, <b>Ctrl+Shift+P</b>).
Choose <b>Plot type</b> (scatter/histogram by default, plus line, heatmap, box, violin). Map numeric columns to
X, Y, Z; leave axes as <b>None</b> when unused. Uses <b>visible (filtered) rows</b>. Lasso/box selection in the
plot can drive table row selection when sync is enabled.</p>
<h3>Radar Plot (Data → Radar Plot)</h3>
<p>Compare 2–6 numeric properties as a spider chart. Fill <b>Entry 1–6</b> with OIDs or row numbers, or leave
empty to plot all rows in scope. Values are min–max normalized across scope; click a trace to select that row.</p>
<h3>Calculator</h3>
<p>Build a new numeric column from expressions referencing <code>[ColumnName]</code>, with sqrt, log10, exp, and
standard operators. Can be disabled via <code>MOLMANAGER_DISABLE_CUSTOM_CALC</code>.</p>
<h3>Sketcher</h3>
<p>Draw or edit structures modelessly; send results to the table or export to a file. Wildcard element sets are
configurable in the sketcher tools.</p>
""",
    "data": """
<h2>Analyze Table and clustering</h2>
<h3>Analyze Table (Data → Analyze Table)</h3>
<p>Statistical summaries for numeric columns and overviews of categorical/text columns for rows passing
filters (optional <b>only selected rows</b>). Includes correlation matrices, percentiles, <b>outlier detection</b>
(IQR, Z-score, modified Z) with <b>Select in Table</b>, curve fits, and common <b>statistical tests</b> when
SciPy is available.</p>
<h3>Cluster (Data → Cluster)</h3>
<p>Cluster by fingerprint with several algorithms. <b>Exploratory mode</b> tries many parameter sets; review
metrics, then apply a trial to add a cluster ID column. For very large tables prefer K-means or
<b>Sphere exclusion (RDKit Leader)</b>; Butina /
Jarvis–Patrick scale more steeply with compound count.</p>
""",
    "data_viz": """
<h2>Embeddings and medchem property space</h2>
<p>These plots use <b>visible (filtered) rows</b> unless the dialog limits scope to a selection. Click or lasso
points to select matching rows in the table when plot sync is active.</p>
<h3>Dimensionality reduction</h3>
<ul>
<li><b>Principal Component Analysis</b> — linear projection; choose descriptor columns and optional color-by column.</li>
<li><b>t-SNE Visualization</b> — nonlinear 2D embedding (slower on large sets; subsampling may apply).</li>
<li><b>UMAP Visualization</b> — alternative nonlinear embedding with tunable neighborhood parameters.</li>
</ul>
<h3>Medicinal chemistry plots</h3>
<ul>
<li><b>BOILED-Egg plot</b> — TPSA vs LogP with GIA (white) and BBB (yellow) regions for oral absorption / BBB context.</li>
<li><b>Golden Triangle plot</b> — molecular weight vs LogP with the drug-likeness triangle overlay.</li>
</ul>
<p>Color points by a numeric column to highlight property gradients across the set.</p>
""",
    "processes": """
<h2>Processes — background jobs</h2>
<p>Open <b>Processes</b> from the top-right of the menu bar. The table lists active and queued work:</p>
<ul>
<li><b>Process queue</b> — one running job (descriptor batches, clustering, import/export helpers, etc.) with
<b>Cancel</b> when the worker supports cooperative cancellation; plus <b>queued</b> jobs waiting to start.</li>
<li><b>Render 2D</b> — while a structure depiction batch is in progress.</li>
<li><b>Dock (Smina)</b> — while <code>smina</code> is running from <b>Tools → Dock (Smina)</b>.</li>
</ul>
<p><b>Cancel</b> on a selected row stops that job type. <b>Clear queue</b> removes all waiting queue jobs without
stopping the one currently executing. Closing the main window cancels queue work and terminates external
tools where possible.</p>
""",
    "sql": """
<h2>External — SQL database</h2>
<p>Connect with a <b>SQLAlchemy</b> URL (templates for SQLite, PostgreSQL, MySQL, and SQL Server are provided).
Either run a <b>SQL query</b> or read a whole <b>table name</b>; set row limits for large sources.</p>
<p>Imported rows map best when the result includes a <b>SMILES</b> (or similarly named) column; the app tries
common column names to build structures automatically.</p>
""",
    "pubchem": """
<h2>External — PubChem</h2>
<h3>Identity</h3>
<p>Look up each SMILES (or selected table rows) via PubChemPy and add returned fields as columns.</p>
<h3>Similarity (2D)</h3>
<p>One query SMILES, minimum Tanimoto, and max hits. Hits below your threshold may still be filtered using
the app’s Morgan Tanimoto in <b>Tanimoto Similarity</b> (PubChem’s server cutoff can be looser than yours).</p>
<h3>Options</h3>
<p>Under <b>Retrieve fields</b>, enable only what you need—or <b>Retrieve all fields</b> for the heaviest
request. <b>Add unique structures only</b> skips rows that match existing canonical SMILES in the table.</p>
""",
    "chembl": """
<h2>External — ChEMBL</h2>
<h3>Identity</h3>
<p>Resolve canonical SMILES through the ChEMBL web resource client and pull molecule properties.</p>
<h3>Similarity</h3>
<p>Cartridge search with threshold 70–100 on ChEMBL’s scale (values below 0.70 are raised). Hits sort by
decreasing similarity; <b>Tanimoto Similarity</b> is stored when returned.</p>
<h3>Activities and targets</h3>
<p>Optional bioactivity and target fields are off by default—enable for smaller batches. <b>Retrieve all fields</b>
turns on the full field set at once. <b>Add unique structures only</b> avoids duplicate rows.</p>
""",
    "patents": """
<h2>External — Query Patents (SureChEMBL)</h2>
<p>Similarity search against <b>SureChEMBL</b> (EMBL-EBI): chemistry linked to patents and documents. Uses
server-side Tanimoto on RDKit Morgan fingerprints.</p>
<p>Each hit can include <b>Tanimoto Similarity</b>. Use <b>Add unique structures only</b> to skip structures
already in the table.</p>
""",
    "smina_dock": """
<h2>Tools — Dock (Smina)</h2>
<p><b>Smina</b> performs rigid receptor–ligand docking in a user-defined box (Vina-compatible CLI flags).
Install Smina and put <code>smina</code> on <code>PATH</code>, or set the executable path in the dialog
(bundled binaries may live under <code>resources/bin/&lt;platform&gt;/</code> when shipped).</p>
<h3>Inputs</h3>
<ul>
<li><b>Receptor</b> and <b>ligand</b> — PDBQT (e.g. from MGLTools <code>prepare_receptor4.py</code> /
<code>prepare_ligand4.py</code> or Open Babel).</li>
<li><b>Search box</b> — center (Å) and side lengths aligned with the receptor coordinate frame.</li>
<li><b>Output</b> — PDBQT path for docked poses.</li>
</ul>
<h3>Run and monitor</h3>
<p>Optional exhaustiveness, number of modes, energy range, CPU threads, working directory, and extra CLI args.
<b>Run Smina</b> streams stdout/stderr to the log; <b>Stop</b> kills the subprocess. The run appears in
<b>Processes</b> as <b>(smina)</b>; cancel from there or from the dialog.</p>
""",
}


def guide_html(guide_id: str) -> str:
    body = _GUIDE_HTML.get(guide_id) or _GUIDE_HTML["overview"]
    return f"<html><body style='font-size:13px'>{body}</body></html>"


def _populate_guide_list(lst: QListWidget, *, select_guide_id: str | None = None) -> None:
    """Fill the sidebar with section headers and selectable topics."""
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
    """Modal window with grouped topics in the sidebar and HTML body."""
    dlg = QDialog(parent)
    dlg.setWindowTitle("MolManager — Help")
    dlg.resize(820, 560)
    outer = QVBoxLayout(dlg)

    content = QHBoxLayout()
    lst = QListWidget()
    lst.setMinimumWidth(260)
    _populate_guide_list(lst, select_guide_id=guide_id)

    browser = QTextBrowser()
    browser.setOpenExternalLinks(True)
    entry = guide_entry(guide_id)
    browser.setHtml(guide_html(guide_id))
    if entry is not None:
        dlg.setWindowTitle(f"MolManager — Help: {entry.menu_label}")
    content.addWidget(lst)
    content.addWidget(browser, 1)
    outer.addLayout(content)

    def on_pick(current: QListWidgetItem | None, _previous: QListWidgetItem | None = None) -> None:
        if current is None:
            return
        gid = current.data(Qt.UserRole)
        if not isinstance(gid, str):
            return
        browser.setHtml(guide_html(gid))
        ent = guide_entry(gid)
        if ent is not None:
            dlg.setWindowTitle(f"MolManager — Help: {ent.menu_label}")

    lst.currentItemChanged.connect(on_pick)

    close_btn = QPushButton("Close")
    close_btn.clicked.connect(dlg.accept)
    outer.addWidget(close_btn)
    make_window_minimizable(dlg)
    dlg.exec_()


def add_user_guide_menu_entries(menu: QMenu, parent: QWidget) -> None:
    """Append grouped submenus (one action per topic, with tooltips)."""
    menu.setToolTipsVisible(True)
    for section in GUIDE_SECTIONS:
        sub = menu.addMenu(section.title)
        sub.setToolTipsVisible(True)
        for entry in section.entries:
            act = QAction(entry.menu_label, parent)
            act.setToolTip(entry.blurb)
            act.triggered.connect(
                lambda checked=False, g=entry.guide_id: open_user_guide_dialog(parent, g)
            )
            sub.addAction(act)
