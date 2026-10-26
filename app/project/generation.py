"""讲稿更新时的分段、片段调整匹配与配音结果装载。"""

from dataclasses import dataclass

from app.audio import segments as segment_audio
from app.project.model import ProjectSession


@dataclass
class GenerationWorkflow:
    project: ProjectSession
    resume_page: int | None = None

    def prepare_notes(self, *, comments_enabled):
        """记录更新前所在页，并将已有片段调整匹配到新讲稿。"""
        project = self.project
        previous = project.notes_list
        if project.session_record_path and previous and (project.audio_loaded or self.resume_page is None):
            index = min(max(project.current_index, 0), len(previous) - 1)
            self.resume_page = previous[index]['page']
        project.split_notes(comments_enabled=comments_enabled)
        project.audio_edits = segment_audio.match_edits(previous, project.audio_edits, project.notes_list)
        return previous

    def apply_result(self, result, engine):
        """装载按输入顺序排列的音频，沿用生成时的配置快照。"""
        project = self.project
        project.media_list = list(result.media_paths)
        project.notes_duration_list = list(result.durations)
        project.note_cache_keys = list(result.cache_keys)
        project.note_cache_exts = list(result.cache_exts)
        project.audio_edits = dict(getattr(result, 'audio_edits', {}) or {})
        project.cache_hit_count = result.cache_hit_count
        project.generation_profile = getattr(result, 'generation_profile', None) or engine.get_generation_profile()
        project.generation_speaker = getattr(result, 'speaker', '') or engine.get_selected_voice_name()
        self._restore_position()

    def _restore_position(self):
        """更新后回到原页的第一段，页被移除时选择距离最近的保留页。"""
        page, self.resume_page = self.resume_page, None
        project = self.project
        if page is None or not project.notes_list:
            return
        if not any(segment['page'] == page for segment in project.notes_list):
            page = min((segment['page'] for segment in project.notes_list),
                       key=lambda candidate: (abs(candidate - page), candidate < page))
        project.current_index = next(i for i, segment in enumerate(project.notes_list)
                                     if segment['page'] == page)
