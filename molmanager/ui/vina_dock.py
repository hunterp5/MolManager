"""Modeless UI to run AutoDock Vina for rigid receptor–ligand docking."""

from __future__ import annotations

import shlex
import shutil
import time
from pathlib import Path

from PyQt5.QtCore import QProcess, QProcessEnvironment, Qt, QTimer
from PyQt5.QtGui import QCloseEvent, QKeySequence
from PyQt5.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..bundled_paths import default_external_executable
from .qt_widget_utils import apply_monospace_to_text_edit, make_window_minimizable


class VinaDockDialog(QDialog):
    """
    Front-end for the AutoDock Vina CLI.

    Expects a rigid receptor and ligand in PDBQT format and a search box (center + size in Å).
    Install Vina separately and ensure ``vina`` is on PATH, or set the executable path below.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._main_window = parent
        self.setWindowTitle("Dock — AutoDock Vina")
        self.setModal(False)
        self.setWindowModality(Qt.NonModal)
        self.resize(780, 640)
        self._proc = QProcess(self)
        self._proc.finished.connect(self._on_proc_finished)
        self._proc.readyReadStandardOutput.connect(self._append_stdout)
        self._proc.readyReadStandardError.connect(self._append_stderr)
        self._proc.started.connect(self._on_proc_started)
        self._stdout_buf = ""
        self._stderr_buf = ""
        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(2000)
        self._heartbeat.timeout.connect(self._on_heartbeat)

        root = QVBoxLayout(self)
        intro = QLabel(
            "<p><b>AutoDock Vina</b> scores docked poses in a user-defined box. "
            "Prepare rigid <b>receptor</b> and <b>ligand</b> as PDBQT (e.g. with MGLTools / "
            "<code>prepare_receptor4.py</code>, <code>prepare_ligand4.py</code>, or Open Babel). "
            "See the <a href=\"https://vina.scripps.edu\">Vina documentation</a> for details.</p>"
        )
        intro.setWordWrap(True)
        intro.setOpenExternalLinks(True)
        intro.setTextFormat(Qt.RichText)
        intro.setStyleSheet("color: palette(mid);")
        root.addWidget(intro)

        exe_row = QHBoxLayout()
        exe_row.addWidget(QLabel("Vina executable:"))
        self.edit_exe = QLineEdit(default_external_executable("vina"))
        self.edit_exe.setToolTip(
            "Path to the vina binary. Uses molmanager/resources/bin/<platform>/vina when bundled, "
            "otherwise PATH (e.g. C:\\Program Files\\vina\\vina.exe)."
        )
        exe_row.addWidget(self.edit_exe, 1)
        root.addLayout(exe_row)

        io_gb = QGroupBox("Input / output (PDBQT)")
        io_form = QFormLayout(io_gb)
        self.edit_receptor = QLineEdit()
        self.edit_receptor.setPlaceholderText("Path to receptor.pdbqt")
        self.edit_receptor.setToolTip("Rigid receptor in PDBQT format.")
        br = QHBoxLayout()
        br.addWidget(self.edit_receptor, 1)
        btn_r = QPushButton("Browse…")
        btn_r.clicked.connect(self._browse_receptor)
        br.addWidget(btn_r)
        rw = QWidget()
        rw.setLayout(br)
        io_form.addRow("Receptor:", rw)

        self.edit_ligand = QLineEdit()
        self.edit_ligand.setPlaceholderText("Path to ligand.pdbqt")
        self.edit_ligand.setToolTip("Ligand in PDBQT format.")
        bl = QHBoxLayout()
        bl.addWidget(self.edit_ligand, 1)
        btn_l = QPushButton("Browse…")
        btn_l.clicked.connect(self._browse_ligand)
        bl.addWidget(btn_l)
        lw = QWidget()
        lw.setLayout(bl)
        io_form.addRow("Ligand:", lw)

        self.edit_out = QLineEdit()
        self.edit_out.setPlaceholderText("Path for docked poses (e.g. out.pdbqt)")
        self.edit_out.setToolTip("Vina writes docked ligand poses (PDBQT) here.")
        bo = QHBoxLayout()
        bo.addWidget(self.edit_out, 1)
        btn_o = QPushButton("Browse…")
        btn_o.clicked.connect(self._browse_out)
        bo.addWidget(btn_o)
        ow = QWidget()
        ow.setLayout(bo)
        io_form.addRow("Output:", ow)
        root.addWidget(io_gb)

        box_gb = QGroupBox("Search box (Å)")
        box_form = QFormLayout(box_gb)
        self.spin_cx = QDoubleSpinBox()
        self.spin_cy = QDoubleSpinBox()
        self.spin_cz = QDoubleSpinBox()
        for sp in (self.spin_cx, self.spin_cy, self.spin_cz):
            sp.setRange(-10_000.0, 10_000.0)
            sp.setDecimals(3)
            sp.setSingleStep(0.5)
        self.spin_cx.setToolTip("Box center X (same coordinates as receptor PDBQT).")
        self.spin_cy.setToolTip("Box center Y.")
        self.spin_cz.setToolTip("Box center Z.")
        box_form.addRow("Center X:", self.spin_cx)
        box_form.addRow("Center Y:", self.spin_cy)
        box_form.addRow("Center Z:", self.spin_cz)

        self.spin_sx = QDoubleSpinBox()
        self.spin_sy = QDoubleSpinBox()
        self.spin_sz = QDoubleSpinBox()
        for sp in (self.spin_sx, self.spin_sy, self.spin_sz):
            sp.setRange(1.0, 500.0)
            sp.setDecimals(2)
            sp.setSingleStep(1.0)
            sp.setValue(20.0)
        self.spin_sx.setToolTip("Box side length along X (default 20 Å).")
        box_form.addRow("Size X:", self.spin_sx)
        box_form.addRow("Size Y:", self.spin_sy)
        box_form.addRow("Size Z:", self.spin_sz)
        root.addWidget(box_gb)

        opt_gb = QGroupBox("Search parameters")
        opt_form = QFormLayout(opt_gb)
        self.spin_exhaust = QSpinBox()
        self.spin_exhaust.setRange(1, 32)
        self.spin_exhaust.setValue(8)
        self.spin_exhaust.setToolTip("Exhaustiveness of the global search (typical 8).")
        opt_form.addRow("Exhaustiveness:", self.spin_exhaust)

        self.spin_modes = QSpinBox()
        self.spin_modes.setRange(1, 100)
        self.spin_modes.setValue(9)
        self.spin_modes.setToolTip("Maximum number of binding modes to generate.")
        opt_form.addRow("Num modes:", self.spin_modes)

        self.spin_energy_range = QDoubleSpinBox()
        self.spin_energy_range.setRange(0.5, 50.0)
        self.spin_energy_range.setDecimals(2)
        self.spin_energy_range.setValue(3.0)
        self.spin_energy_range.setToolTip("Maximum energy difference (kcal/mol) from best mode to keep.")
        opt_form.addRow("Energy range:", self.spin_energy_range)

        self.spin_cpu = QSpinBox()
        self.spin_cpu.setRange(0, 128)
        self.spin_cpu.setValue(0)
        self.spin_cpu.setSpecialValueText("auto")
        self.spin_cpu.setToolTip("CPU threads for Vina; 0 / auto omits --cpu (Vina default).")
        opt_form.addRow("CPU threads:", self.spin_cpu)

        self.edit_wd = QLineEdit()
        self.edit_wd.setPlaceholderText("Optional working directory (empty = inherit)")
        self.edit_wd.setToolTip("If set, Vina starts with this as the current directory (relative paths resolve here).")
        opt_form.addRow("Working dir:", self.edit_wd)

        self.edit_extra = QLineEdit()
        self.edit_extra.setPlaceholderText('Optional extra args (e.g. --seed 42)')
        opt_form.addRow("Extra args:", self.edit_extra)
        root.addWidget(opt_gb)

        self.progress = QLabel("Idle.")
        self.progress.setWordWrap(True)
        self.progress.setStyleSheet("color: palette(mid);")
        root.addWidget(self.progress)

        log_gb = QGroupBox("Log")
        log_v = QVBoxLayout(log_gb)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        apply_monospace_to_text_edit(self.log)
        log_v.addWidget(self.log)
        root.addWidget(log_gb, 1)

        btn_row = QHBoxLayout()
        self.btn_run = QPushButton("Run Vina")
        self.btn_run.clicked.connect(self._run_vina)
        btn_row.addWidget(self.btn_run)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_proc)
        btn_row.addWidget(self.btn_stop)
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        root.addLayout(btn_row)

        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._run_vina)
        make_window_minimizable(self)

    def is_vina_running(self) -> bool:
        return self._proc.state() != QProcess.NotRunning

    def cancel_vina(self) -> bool:
        if self._proc.state() == QProcess.NotRunning:
            return False
        self._stop_proc()
        return True

    def _notify_activity(self) -> None:
        w = self._main_window
        hub = getattr(w, "background_activity", None) if w is not None else None
        if hub is not None:
            hub.notify_changed()

    def _browse_receptor(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Receptor PDBQT", "", "PDBQT (*.pdbqt);;All files (*.*)"
        )
        if path:
            self.edit_receptor.setText(path)

    def _browse_ligand(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Ligand PDBQT", "", "PDBQT (*.pdbqt);;All files (*.*)"
        )
        if path:
            self.edit_ligand.setText(path)

    def _browse_out(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Docked output PDBQT", "", "PDBQT (*.pdbqt);;All files (*.*)"
        )
        if path:
            self.edit_out.setText(path)

    def _vina_executable(self) -> str:
        return (self.edit_exe.text() or "").strip() or "vina"

    def _build_argv(self) -> list[str]:
        rec = (self.edit_receptor.text() or "").strip()
        lig = (self.edit_ligand.text() or "").strip()
        out = (self.edit_out.text() or "").strip()
        args: list[str] = [
            "--receptor",
            rec,
            "--ligand",
            lig,
            "--out",
            out,
            "--center_x",
            f"{self.spin_cx.value():.6g}",
            "--center_y",
            f"{self.spin_cy.value():.6g}",
            "--center_z",
            f"{self.spin_cz.value():.6g}",
            "--size_x",
            f"{self.spin_sx.value():.6g}",
            "--size_y",
            f"{self.spin_sy.value():.6g}",
            "--size_z",
            f"{self.spin_sz.value():.6g}",
            "--exhaustiveness",
            str(int(self.spin_exhaust.value())),
            "--num_modes",
            str(int(self.spin_modes.value())),
            "--energy_range",
            f"{self.spin_energy_range.value():.6g}",
        ]
        cpu = int(self.spin_cpu.value())
        if cpu > 0:
            args.extend(["--cpu", str(cpu)])
        extra = (self.edit_extra.text() or "").strip()
        if extra:
            try:
                args.extend(shlex.split(extra, posix=False))
            except ValueError:
                args.extend(extra.split())
        return args

    def _run_vina(self) -> None:
        if self._proc.state() != QProcess.NotRunning:
            QMessageBox.information(self, "Dock", "A Vina run is already in progress.")
            return
        exe = self._vina_executable()
        if shutil.which(exe) is None and not Path(exe).is_file():
            self.log.append(f"Note: '{exe}' not found on PATH — the run may fail unless the path is correct.\n")

        rec = (self.edit_receptor.text() or "").strip()
        lig = (self.edit_ligand.text() or "").strip()
        out = (self.edit_out.text() or "").strip()
        if not rec or not lig or not out:
            QMessageBox.warning(self, "Dock", "Set receptor, ligand, and output PDBQT paths.")
            return
        if not Path(rec).is_file():
            QMessageBox.warning(self, "Dock", "Receptor file does not exist.")
            return
        if not Path(lig).is_file():
            QMessageBox.warning(self, "Dock", "Ligand file does not exist.")
            return
        out_p = Path(out)
        if not out_p.parent.is_dir():
            QMessageBox.warning(self, "Dock", "Output directory does not exist.")
            return

        argv = self._build_argv()
        cmd = [exe] + argv
        stamp = time.strftime("%H:%M:%S")
        self.log.append(f"[{stamp}][system] $ " + " ".join(cmd) + "\n")

        self.btn_run.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._heartbeat.start()
        self.progress.setText("Starting Vina…")
        self._notify_activity()

        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONUNBUFFERED", "1")
        self._proc.setProcessEnvironment(env)
        wd = (self.edit_wd.text() or "").strip()
        if wd:
            self._proc.setWorkingDirectory(wd)
        self._proc.setProgram(exe)
        self._proc.setArguments(argv)
        self._proc.start()

    def _append_stream(self, channel: str, chunk: bytes) -> None:
        if not chunk:
            return
        buf_attr = "_stdout_buf" if channel == "stdout" else "_stderr_buf"
        buf = getattr(self, buf_attr) + chunk.decode("utf-8", errors="replace")
        lines = buf.split("\n")
        setattr(self, buf_attr, lines[-1])
        for line in lines[:-1]:
            line = line.rstrip("\r")
            if not line.strip():
                continue
            stamp = time.strftime("%H:%M:%S")
            self.log.append(f"[{stamp}][{channel}] {line}")

    def _append_stdout(self) -> None:
        self._append_stream("stdout", bytes(self._proc.readAllStandardOutput()))

    def _append_stderr(self) -> None:
        self._append_stream("stderr", bytes(self._proc.readAllStandardError()))

    def _on_proc_started(self) -> None:
        try:
            pid = int(self._proc.pid())
        except Exception:
            pid = -1
        stamp = time.strftime("%H:%M:%S")
        self.log.append(f"[{stamp}][system] Vina started (PID {pid}).")
        self.progress.setText(f"Running Vina (PID {pid})…")

    def _on_heartbeat(self) -> None:
        if self._proc.state() == QProcess.NotRunning:
            self._heartbeat.stop()

    def _on_proc_finished(self, code: int = 0, _status: int = 0) -> None:
        self._heartbeat.stop()
        for ch, attr in (("stdout", "_stdout_buf"), ("stderr", "_stderr_buf")):
            tail = (getattr(self, attr, "") or "").strip("\r\n")
            setattr(self, attr, "")
            if tail.strip():
                stamp = time.strftime("%H:%M:%S")
                self.log.append(f"[{stamp}][{ch}] {tail}")
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        stamp = time.strftime("%H:%M:%S")
        self.log.append(f"\n[{stamp}][system] Finished with exit code {code}.\n")
        self.progress.setText(f"Finished — exit code {code}.")
        self._notify_activity()

    def _stop_proc(self) -> None:
        if self._proc.state() != QProcess.NotRunning:
            self._proc.kill()
            self._heartbeat.stop()
            stamp = time.strftime("%H:%M:%S")
            self.log.append(f"\n[{stamp}][system] Stopped by user.\n")
            self.progress.setText("Stopped.")
        self.btn_run.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._notify_activity()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._heartbeat.stop()
        if self._proc.state() != QProcess.NotRunning:
            self._proc.kill()
            self._proc.waitForFinished(3000)
        self._notify_activity()
        super().closeEvent(event)
