"""统一阅读区域的滚轮步长，避免长列表项放大滚动距离。"""

from PySide6.QtWidgets import QAbstractItemView


def configure_reading_scroll(widget):
    """保留 Fluent 平滑滚动，取消额外加速；像素列表使用固定步长。"""
    if isinstance(widget, QAbstractItemView):
        widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        # 显式设置后，Qt 不再根据列表项的平均高度推算滚轮步长。
        widget.verticalScrollBar().setSingleStep(20)

    delegate = getattr(widget, 'scrollDelegate', None)
    if delegate is None:
        delegate = getattr(widget, 'scrollDelagate', None)
    if delegate is None:
        return
    for smooth in (delegate.verticalSmoothScroll, delegate.horizonSmoothScroll):
        for engine in (smooth.fixedStepScrollEngine, smooth.adaptiveScrollEngine):
            engine.stepRatio = 1
            engine.acceleration = 0
            engine.duration = 200
