"""展示配音合并进度，并在后台任务结束后关闭。"""

from PySide6.QtCore import Qt
from qfluentwidgets import BodyLabel, ProgressBar, PushButton

from tasks.audio_merge_task import AudioMergeTask
from ui.dialogs.base import ThemedDialog


class AudioMergeDialog(ThemedDialog):
    def __init__(self, sources, target, parent=None):
        super().__init__(parent)
        self.setWindowTitle('合并配音')
        self.setMinimumWidth(400)
        layout = self.create_form_layout(400, (24, 24, 24, 24), 16)
        self.label = BodyLabel('正在准备配音…', self)
        layout.addWidget(self.label)
        self.progress = ProgressBar(self)
        layout.addWidget(self.progress)
        layout.addStretch(1)
        self.cancel_button = PushButton('取消', self)
        self.cancel_button.setMinimumWidth(88)
        self.cancel_button.clicked.connect(self.cancel)
        layout.addWidget(self.cancel_button, alignment=Qt.AlignmentFlag.AlignRight)
        self.error = ''
        self.success = False
        self.task = AudioMergeTask(sources, target, self)
        self.task.progress.connect(self._progress)
        self.task.failed.connect(self._failed)
        self.task.ready.connect(self._ready)
        self.task.finished.connect(self._finished)

    def showEvent(self, event):
        super().showEvent(event)
        if not self.task.isRunning() and not self.task.isFinished():
            self.task.start()

    def _progress(self, done, total):
        self.label.setText(f'正在合并配音 {done} / {total}')
        self.progress.setValue(round(done * 100 / total))

    def _ready(self):
        self.success = True

    def _failed(self, message):
        self.error = message

    def _finished(self):
        self.done(self.DialogCode.Accepted if self.success else self.DialogCode.Rejected)

    def cancel(self):
        self.task.requestInterruption()
        self.label.setText('正在结束合并…')
        self.cancel_button.setEnabled(False)

    def reject(self):
        if self.task.isRunning():
            self.cancel()
        else:
            super().reject()

    def closeEvent(self, event):
        if self.task.isRunning():
            self.cancel()
            event.ignore()
        else:
            super().closeEvent(event)
