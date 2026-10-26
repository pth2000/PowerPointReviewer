"""独立于窗口和 Qt 控件的当前工程数据。

一个工程对象由首页、工具页和审听面板共享。页面负责发送界面更新信号，
工程对象负责讲稿、配音引用和关联信息，避免把某个页面作为数据仓库。
"""

from dataclasses import dataclass, field, fields
from enum import Enum
from pathlib import Path

from app.documents.text import spoken_notes, spoken_text
from app.presentation.link import PPT_SUFFIXES


class UnavailableReason(Enum):
    NOTES = 'notes'
    BUSY = 'busy'
    PRESENTATION = 'presentation'
    AUDIO = 'audio'
    PREPARING = 'preparing'


@dataclass(frozen=True)
class ProjectAvailability:
    has_notes: bool
    linked: bool
    audio_loaded: bool
    audio_available: bool
    durations_available: bool
    busy: bool
    preparing: bool

    def unavailable_for(self, *, audio=False, presentation=False, durations=False):
        """返回操作不能执行的首要原因，供按钮与实际执行入口共用。"""
        if not self.has_notes:
            return UnavailableReason.NOTES
        if self.busy:
            return UnavailableReason.BUSY
        if presentation and not self.linked:
            return UnavailableReason.PRESENTATION
        if (audio and not self.audio_available) or (durations and not self.durations_available):
            return UnavailableReason.AUDIO
        return None

    def sync_unavailable_reason(self):
        if not self.has_notes:
            return UnavailableReason.NOTES
        if self.preparing:
            return UnavailableReason.PREPARING
        if self.busy:
            return UnavailableReason.BUSY
        if not self.audio_loaded:
            return UnavailableReason.AUDIO
        if not self.linked:
            return UnavailableReason.PRESENTATION
        return None


@dataclass
class ProjectSession:
    notes: dict = field(default_factory=dict)
    notes_list: list = field(default_factory=list)
    media_list: list = field(default_factory=list)
    notes_duration_list: list = field(default_factory=list)
    note_cache_keys: list = field(default_factory=list)
    note_cache_exts: list = field(default_factory=list)
    audio_edits: dict = field(default_factory=dict)
    generation_profile: dict | None = None
    generation_speaker: str = ''
    cache_hit_count: int = 0
    note_file_path: Path | None = None
    note_file_name: str = ''
    presentation_link: dict = field(default_factory=dict)
    presentation_path: Path | None = None
    session_record_path: Path | None = None
    mark: str = '●'
    script_comments_enabled: bool = False
    current_index: int = 0
    audio_loaded: bool = False

    def reading_notes(self):
        return spoken_notes(self.notes, self.script_comments_enabled)

    def split_notes(self, *, comments_enabled):
        """按真实页码切分正文，保留空白页并忽略末尾分隔符产生的空段。"""
        notes_list = []
        for page in sorted(self.notes):
            text = spoken_text(self.notes[page], comments_enabled).strip()
            if self.mark and text.endswith(self.mark):
                text = text[:-len(self.mark)]
            notes_list.extend({'page': page, 'text': part.strip()} for part in text.split(self.mark))
        self.script_comments_enabled = bool(comments_enabled)
        self.notes_list = notes_list

    def clear_audio(self):
        """清空当前配音与位置，保留讲稿、关联信息和待应用的片段调整。"""
        self.media_list = []
        self.current_index = 0
        self.notes_duration_list = []
        self.note_cache_keys = []
        self.note_cache_exts = []
        self.cache_hit_count = 0
        self.generation_profile = None
        self.generation_speaker = ''
        self.audio_loaded = False

    def replace_from(self, other):
        """装载已校验的工程，同时保留各界面共享的对象身份。"""
        for item in fields(self):
            setattr(self, item.name, getattr(other, item.name))

    def has_presentation(self):
        source = self.presentation_path
        return bool(source and source.is_file() and source.suffix.lower() in PPT_SUFFIXES)

    def audio_available(self):
        if not self.audio_loaded or not self.notes_list or len(self.media_list) != len(self.notes_list):
            return False
        try:
            return all(Path(path).is_file() and Path(path).stat().st_size > 0 for path in self.media_list)
        except OSError:
            return False

    def availability(self, *, busy=False, preparing=False, verify_audio=True):
        """生成一次操作所用的状态快照；普通播放刷新不必逐个检查音频文件。"""
        available = self.audio_available() if verify_audio else self.audio_loaded
        return ProjectAvailability(
            has_notes=bool(self.notes), linked=self.has_presentation(),
            audio_loaded=self.audio_loaded, audio_available=available,
            durations_available=available and len(self.notes_duration_list) == len(self.notes_list),
            busy=bool(busy), preparing=bool(preparing),
        )
