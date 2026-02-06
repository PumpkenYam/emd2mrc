import sys
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot, QThread
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFileDialog,
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QListWidget, QListWidgetItem, QTextEdit, QProgressBar, QMessageBox
)

import core


class Worker(QObject):
    log = Signal(str)
    progress = Signal(int)
    finished = Signal()
    error = Signal(str)

    def __init__(self, image_dir: Path, emd_dir: Path, output_dir: Path, selected_sources: list[str], groups: dict):
        super().__init__()
        self.image_dir = image_dir
        self.emd_dir = emd_dir
        self.output_dir = output_dir
        self.selected_sources = selected_sources
        self.groups = groups
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def is_cancelled(self):
        return self._cancel

    @Slot()
    def run(self):
        try:
            total_sources = len(self.selected_sources)
            if total_sources == 0:
                self.error.emit("No data sources selected.")
                return

            for i, source in enumerate(self.selected_sources):
                if self.is_cancelled():
                    self.log.emit("🛑 Cancelled.")
                    break

                files = self.groups.get(source, [])
                self.log.emit(f"\n🎯 Processing data source: {source} ({len(files)} images)")

                # 将每个 source 的进度映射到全局进度
                def progress_cb(p):
                    # p 是 0-100（单组），映射到全局
                    base = int(i / total_sources * 100)
                    span = int(1 / total_sources * 100)
                    self.progress.emit(min(100, base + int(p / 100 * span)))

                stack = core.load_images_sorted_by_alpha(
                    files,
                    self.emd_dir,
                    log_cb=lambda s: self.log.emit(s),
                    progress_cb=progress_cb,
                    cancel_flag=self.is_cancelled
                )

                if stack is None:
                    self.log.emit("⚠️ Invalid data source, skipping.")
                    continue

                out_name = source.replace(" ", "_")
                out_path = self.output_dir / f"{out_name}.mrc"
                core.write_mrc(stack, out_path)
                self.log.emit(f"✅ Successfully generated: {out_path}")

            self.progress.emit(100)
            self.finished.emit()

        except Exception as e:
            self.error.emit(repr(e))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("EMD to MRC ")

        self.groups = {}
        self.thread = None
        self.worker = None

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        # 目录选择区
        self.image_edit = QLineEdit()
        self.emd_edit = QLineEdit()
        self.out_edit = QLineEdit()

        layout.addLayout(self._row("Image directory（png/jpg/tiff）", self.image_edit, self._browse_image))
        layout.addLayout(self._row("EMD directory（.emd）", self.emd_edit, self._browse_emd))
        layout.addLayout(self._row("Output directory（mrc）", self.out_edit, self._browse_out))

        # Scan and list all data sources
        btn_row = QHBoxLayout()
        self.scan_btn = QPushButton("🔍 Scan data")
        self.run_btn = QPushButton("▶ Generate MRCs")
        self.cancel_btn = QPushButton("⏹ Cancel")
        self.cancel_btn.setEnabled(False)

        btn_row.addWidget(self.scan_btn)
        btn_row.addWidget(self.run_btn)
        btn_row.addWidget(self.cancel_btn)
        layout.addLayout(btn_row)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QListWidget.MultiSelection)
        layout.addWidget(QLabel("Select data sources (multiple allowed)："))
        layout.addWidget(self.list_widget)

        # 进度与日志
        self.progress = QProgressBar()
        self.log = QTextEdit()
        self.log.setReadOnly(True)

        layout.addWidget(self.progress)
        layout.addWidget(QLabel("Log："))
        layout.addWidget(self.log)

        # 信号绑定
        self.scan_btn.clicked.connect(self.scan_sources)
        self.run_btn.clicked.connect(self.start)
        self.cancel_btn.clicked.connect(self.cancel)

        # 给个默认值（可删）
        self.image_edit.setText("data/NiSi")
        self.emd_edit.setText("data/NiSi")
        self.out_edit.setText("mrc_output")

    def _row(self, label, edit, browse_fn):
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        row.addWidget(edit)
        b = QPushButton("Select…")
        b.clicked.connect(browse_fn)
        row.addWidget(b)
        return row

    def _browse_image(self):
        d = QFileDialog.getExistingDirectory(self, "Select Image directory")
        if d:
            self.image_edit.setText(d)

    def _browse_emd(self):
        d = QFileDialog.getExistingDirectory(self, "Select EMD directory")
        if d:
            self.emd_edit.setText(d)

    def _browse_out(self):
        d = QFileDialog.getExistingDirectory(self, "Select output directory")
        if d:
            self.out_edit.setText(d)

    def append_log(self, s: str):
        self.log.append(s)

    @Slot()
    def scan_sources(self):
        image_dir = Path(self.image_edit.text().strip())
        if not image_dir.exists():
            QMessageBox.warning(self, "Path error", "Image directory does not exist.")
            return

        self.append_log("🔍 Scanning image files…")
        self.groups = core.group_images_by_source(image_dir)

        self.list_widget.clear()
        for source, files in sorted(self.groups.items(), key=lambda kv: kv[0]):
            item = QListWidgetItem(f"{source}   ({len(files)} images)")
            item.setData(0x0100, source)  # Qt.UserRole = 0x0100
            self.list_widget.addItem(item)

        self.append_log(f"📁 Found {len(self.groups)} data sources.")

    @Slot()
    def start(self):
        if self.thread is not None:
            QMessageBox.information(self, "Running", "Task is already running.")
            return

        image_dir = Path(self.image_edit.text().strip())
        emd_dir = Path(self.emd_edit.text().strip())
        out_dir = Path(self.out_edit.text().strip())

        if not image_dir.exists():
            QMessageBox.warning(self, "Path error", "Image directory does not exist.")
            return
        if not emd_dir.exists():
            QMessageBox.warning(self, "Path error", "EMD directory does not exist.")
            return

        if not self.groups:
            # 允许不点扫描直接开始：自动扫描一次
            self.groups = core.group_images_by_source(image_dir)

        selected = []
        for item in self.list_widget.selectedItems():
            selected.append(item.data(0x0100))

        if not selected:
            QMessageBox.warning(self, "No selection", "Please select one or more data sources in the list.")
            return

        # UI 状态
        self.progress.setValue(0)
        self.cancel_btn.setEnabled(True)
        self.run_btn.setEnabled(False)
        self.scan_btn.setEnabled(False)

        # 启动线程
        self.thread = QThread()
        self.worker = Worker(image_dir, emd_dir, out_dir, selected, self.groups)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.log.connect(self.append_log)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.finished.connect(self.on_finished)
        self.worker.error.connect(self.on_error)

        self.thread.start()
        self.append_log("▶ Generating")

    @Slot()
    def cancel(self):
        if self.worker:
            self.worker.cancel()
            self.append_log("⏹ Cancelling...")

    @Slot()
    def on_finished(self):
        self.append_log("\n🎉 Completed")
        self.cleanup_thread()
        QMessageBox.information(self, "Complete", "MRC generation is complete.")

    @Slot(str)
    def on_error(self, msg: str):
        self.append_log(f"❌ Error: {msg}")
        self.cleanup_thread()
        QMessageBox.critical(self, "Error", msg)

    def cleanup_thread(self):
        self.cancel_btn.setEnabled(False)
        self.run_btn.setEnabled(True)
        self.scan_btn.setEnabled(True)

        if self.thread:
            self.thread.quit()
            self.thread.wait()
        self.thread = None
        self.worker = None


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.resize(900, 700)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
