"""原生导出的动画组解析、全局时间对齐和文件保护。"""

from array import array
from pathlib import Path
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET
import wave

from app.video.mux import MuxCancelled
from app.video.native_media import join_narration, probe_media
from app.video.powerpoint_timing import (
    EffectTiming, P, SlideTiming, build_powerpoint_schedule, parse_slide_timing,
)
from app.video.timeline import SAMPLE_RATE, VideoTimeline


def effect(kind, delay=0):
    return f'<p:par><p:cTn nodeType="{kind}"><p:stCondLst><p:cond delay="{delay}"/></p:stCondLst></p:cTn></p:par>'


def group(effects, clicked=False, delay=0):
    trigger = '<p:cond delay="indefinite"/>' if clicked else '<p:cond delay="0"/>'
    return (f'<p:par><p:cTn><p:stCondLst>{trigger}</p:stCondLst><p:childTnLst>'
            f'<p:par><p:cTn><p:stCondLst><p:cond delay="{delay}"/></p:stCondLst>'
            f'<p:childTnLst>{effects}</p:childTnLst></p:cTn></p:par></p:childTnLst></p:cTn></p:par>')


def slide(groups):
    return ET.fromstring(f'<p:sld xmlns:p="{P}"><p:timing><p:tnLst><p:seq>'
        f'<p:cTn nodeType="mainSeq"><p:childTnLst>{groups}</p:childTnLst></p:cTn>'
        '</p:seq></p:tnLst></p:timing></p:sld>')


class PowerPointTimingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def timeline(self, pages, seconds=2):
        path = self.root / 'voice.wav'
        with wave.open(str(path), 'wb') as writer:
            writer.setparams((1, 2, SAMPLE_RATE, 0, 'NONE', 'not compressed'))
            writer.writeframes(array('h', [1000] * round(seconds * SAMPLE_RATE)).tobytes())
        return VideoTimeline(pages, [path] * len(pages), lead_ms=0, tail_ms=0)

    def test_without_animation(self):
        parsed = parse_slide_timing(ET.fromstring(f'<p:sld xmlns:p="{P}"/>'), 3)
        self.assertEqual(parsed, SlideTiming(3, 0, ()))

    def test_native_copy_removes_only_effects_of_hidden_targets_in_reverse_order(self):
        from app.video.powerpoint import _remove_hidden_effects
        document = Mock()
        sequence = document.Slides.Item.return_value.TimeLine.MainSequence
        sequence.Count = 4
        effects = [Mock() for _ in range(4)]
        for effect, identifier in zip(effects, (2, 3, 4, 3)):
            effect.Shape.Id = identifier
        sequence.Item.side_effect = lambda index: effects[index - 1]
        xml = ET.fromstring(f'<p:sld xmlns:p="{P}"><p:cSld><p:spTree><p:sp><p:nvSpPr>'
            '<p:cNvPr id="3" hidden="1"/></p:nvSpPr></p:sp></p:spTree></p:cSld></p:sld>')
        with patch('app.video.powerpoint.read_slide_elements', return_value=(xml,)):
            _remove_hidden_effects(document, self.root / 'copy.pptx', [2])
        self.assertEqual([call.args[0] for call in sequence.Item.call_args_list], [4, 3, 2, 1])
        effects[0].Delete.assert_not_called()
        effects[2].Delete.assert_not_called()
        effects[1].Delete.assert_called_once()
        effects[3].Delete.assert_called_once()
        document.Save.assert_called_once()

    def test_native_copy_without_hidden_objects_does_not_rebuild_sequence(self):
        from app.video.powerpoint import _remove_hidden_effects
        document = Mock()
        xml = ET.fromstring(f'<p:sld xmlns:p="{P}"/>')
        with patch('app.video.powerpoint.read_slide_elements', return_value=(xml,)):
            _remove_hidden_effects(document, self.root / 'copy.pptx', [1])
        document.Slides.Item.assert_not_called()
        document.Save.assert_not_called()

    def test_parallel_effect_uses_xml_group_origin(self):
        parsed = parse_slide_timing(slide(group(effect('withEffect')) +
            group(effect('clickEffect', 1500) + effect('withEffect', 200), True, 500)), 2)
        self.assertEqual(parsed.clicks, 1)
        self.assertEqual(parsed.effects, (EffectTiming(1, 0, 0), EffectTiming(2, 1, 2), EffectTiming(3, 1, .7)))

    def test_multiple_click_groups_keep_relative_offsets(self):
        parsed = parse_slide_timing(slide(group(effect('clickEffect'), True) +
            group(effect('clickEffect') + effect('afterEffect', 600), True)), 1)
        self.assertEqual([(e.step, e.delay) for e in parsed.effects], [(1, 0), (2, 0), (2, .6)])

    def test_automatic_group_delay_is_preserved(self):
        document = slide(group(effect('withEffect', 200)))
        sequence = next(node for node in document.iter(f'{{{P}}}cTn') if node.get('nodeType') == 'mainSeq')
        root = sequence.find('p:childTnLst/p:par/p:cTn', {'p': P})
        root.find('p:stCondLst/p:cond', {'p': P}).set('delay', '600')
        self.assertAlmostEqual(parse_slide_timing(document, 1).effects[0].delay, .8)

    def test_interactive_effects_are_rejected_and_looping_effects_are_identified(self):
        interactive = slide(group(effect('clickEffect'), True))
        ET.SubElement(interactive.find(f'{{{P}}}timing'), f'{{{P}}}cTn', {'nodeType': 'interactiveSeq'})
        with self.assertRaisesRegex(ValueError, '指定对象'):
            parse_slide_timing(interactive, 1)
        repeated = slide(group(effect('withEffect')))
        node = next(n for n in repeated.iter(f'{{{P}}}cTn') if n.get('nodeType') == 'withEffect')
        node.set('repeatCount', 'indefinite')
        parsed = parse_slide_timing(repeated, 1)
        self.assertTrue(parsed.effects[0].indefinite)

    def test_finite_repeats_and_internal_auto_reverse_preserve_effect_nodes(self):
        document = slide(group(effect('clickEffect') + effect('afterEffect', 500), True))
        node = next(n for n in document.iter(f'{{{P}}}cTn') if n.get('nodeType') == 'afterEffect')
        node.set('repeatCount', '2000')
        internal = ET.SubElement(node, f'{{{P}}}childTnLst')
        behavior = ET.SubElement(internal, f'{{{P}}}anim')
        ET.SubElement(behavior, f'{{{P}}}cTn', {'autoRev': '1', 'dur': '250'})
        before = ET.tostring(document)
        parsed = parse_slide_timing(document, 5)
        self.assertEqual(parsed.clicks, 1)
        self.assertEqual([(e.index, e.step, e.delay) for e in parsed.effects], [(1, 1, 0), (2, 1, .5)])
        self.assertEqual(ET.tostring(document), before)

    def test_multiple_effects_targeting_same_object_keep_their_separate_steps(self):
        document = slide(group(effect('clickEffect') + effect('afterEffect', 500), True)
                         + group(effect('clickEffect'), True))
        for node in document.iter(f'{{{P}}}cTn'):
            if node.get('nodeType') in ('clickEffect', 'afterEffect'):
                target = ET.SubElement(node, f'{{{P}}}tgtEl')
                ET.SubElement(target, f'{{{P}}}spTgt', {'spid': '42'})
        parsed = parse_slide_timing(document, 1)
        self.assertEqual(parsed.clicks, 2)
        self.assertEqual([(e.index, e.step) for e in parsed.effects], [(1, 1), (2, 1), (3, 2)])

    def test_container_repeats_are_not_confused_with_effect_repeats(self):
        document = slide(group(effect('clickEffect'), True))
        root = next(n for n in document.iter(f'{{{P}}}cTn') if n.find(f'{{{P}}}childTnLst') is not None
                    and n.get('nodeType') != 'mainSeq')
        root.set('repeatCount', '2000')
        with self.assertRaisesRegex(ValueError, '整组循环'):
            parse_slide_timing(document, 1)

    def test_effect_duration_accounts_for_outer_repeat_reverse_and_time_limit(self):
        from app.video.powerpoint import _effect_duration
        timing = NS(Duration=.5, RepeatCount=2, RepeatDuration=0, AutoReverse=0)
        self.assertEqual(_effect_duration(timing, 5), 1)
        timing.AutoReverse = -1
        self.assertEqual(_effect_duration(timing, 5), 2)
        timing.RepeatDuration = .7
        self.assertEqual(_effect_duration(timing, 5), .7)
        timing.RepeatCount = float('inf')
        with self.assertRaisesRegex(ValueError, '第 5 页'):
            _effect_duration(timing, 5)

    def test_media_is_kept_and_identified_for_bounded_native_playback(self):
        document = slide('')
        ET.SubElement(document, f'{{{P}}}audio')
        self.assertTrue(parse_slide_timing(document, 1).has_media)

    def test_loop_until_next_click_and_until_slide_end_use_distinct_boundaries(self):
        document = slide(group(effect('withEffect')) + group(effect('clickEffect'), True)
                         + group(effect('clickEffect'), True))
        first = next(n for n in document.iter(f'{{{P}}}cTn') if n.get('nodeType') == 'withEffect')
        first.set('repeatCount', 'indefinite')
        end = ET.SubElement(first, f'{{{P}}}endCondLst')
        ET.SubElement(end, f'{{{P}}}cond', {'evt': 'onNext', 'delay': '0'})
        last = list(n for n in document.iter(f'{{{P}}}cTn') if n.get('nodeType') == 'clickEffect')[-1]
        last.set('repeatCount', 'indefinite')
        timeline = self.timeline([1, 1, 1])
        parsed = parse_slide_timing(document, 1)
        plan = build_powerpoint_schedule(timeline, [parsed], [0], 33000)
        initial, middle, final = plan.slides[0].effects
        self.assertTrue(initial.indefinite)
        self.assertEqual(initial.end_frame, middle.frame)
        self.assertEqual(final.end_frame, plan.total_frames)

    def test_bounded_effects_keep_speed_and_honor_existing_shorter_repeat_limit(self):
        from app.video.powerpoint import _bound_effect
        timing = NS(Duration=1, RepeatCount=-2147483648, RepeatDuration=0, AutoReverse=0)
        effect = NS(frame=30, end_frame=90, indefinite=True, media=False)
        _bound_effect(timing, effect, 33000, 29)
        self.assertAlmostEqual(timing.RepeatDuration, 1.97175)
        self.assertEqual(timing.RepeatCount, 2)

        timing.RepeatDuration = .7
        _bound_effect(timing, effect, 33000, 29)
        self.assertEqual(timing.RepeatDuration, .7)
        timing = NS(Duration=4, RepeatCount=1, RepeatDuration=0, AutoReverse=0)
        effect.indefinite = False
        _bound_effect(timing, effect, 33000, 29)
        self.assertEqual(timing.Duration, 4)
        self.assertAlmostEqual(timing.RepeatDuration, 1.97175)

    def test_delayed_loop_with_click_end_is_fast_forwarded_at_that_click(self):
        timeline = self.timeline([1, 1, 1, 1])
        timings = [SlideTiming(1, 3, (EffectTiming(1, 0, 5, True, True),))]
        plan = build_powerpoint_schedule(timeline, timings, [0], 33000)
        loop = plan.slides[0].effects[0]
        self.assertEqual(loop.frame, round(2 / .033))
        self.assertEqual(loop.end_frame, round(2 / .033))
        self.assertTrue(loop.instant)

    def test_preflight_reports_all_unsupported_pages_together(self):
        from app.video.powerpoint_timing import scan_slide_timings
        document = slide(group(effect('clickEffect'), True))
        ET.SubElement(document.find(f'{{{P}}}timing'), f'{{{P}}}cTn', {'nodeType': 'interactiveSeq'})
        with patch('app.video.powerpoint_timing.read_slide_elements', return_value=(document, document)):
            timings, issues = scan_slide_timings(self.root / 'source.pptx', [2, 7])
        self.assertEqual(timings, (None, None))
        self.assertEqual(len(issues), 2)
        self.assertIn('第 2 页', issues[0])
        self.assertIn('第 7 页', issues[1])

    def test_collection_reordering_does_not_change_which_original_effect_is_configured(self):
        from app.video.powerpoint import _configure_slide
        from app.video.powerpoint_timing import ScheduledEffect
        def timing(duration):
            return NS(Duration=duration, RepeatCount=1, RepeatDuration=0, AutoReverse=0,
                      TriggerType=1, TriggerDelayTime=0)
        first, second = NS(Timing=timing(6)), NS(Timing=timing(.01))
        order = [first, second]
        sequence = Mock()
        sequence.Item.side_effect = lambda index: order[index - 1]
        def clone(original, index):
            self.assertIs(original, first)
            final = NS(Timing=NS(**vars(original.Timing)))
            # 模拟 PowerPoint 在触发分组改变时把新增条目移到原有第二条之前。
            order.insert(1, final)
            return final
        sequence.Clone.side_effect = clone
        slide = NS(TimeLine=NS(MainSequence=sequence), SlideShowTransition=NS())
        plan = NS(page=1, end_frame=120, advance_time=4, effects=(ScheduledEffect(1, .99, 30, 60),
                                                 ScheduledEffect(2, 1.98, 60, 120)))
        _configure_slide(slide, plan, NS(has_media=False), 33000)
        self.assertEqual(second.Timing.TriggerType, 2)
        self.assertEqual(second.Timing.TriggerDelayTime, 1.98)
        self.assertEqual(first.Timing.Duration, 6)
        self.assertLess(first.Timing.RepeatDuration, 1)
        self.assertEqual(first.Timing.RewindAtEnd, -1)
        self.assertEqual(order[1].Timing.Duration, .033)

    def test_long_media_is_trimmed_to_page_boundary_and_short_media_keeps_full_loop(self):
        from app.video.powerpoint import _bound_media
        from app.video.powerpoint_timing import ScheduledEffect
        play = NS(LoopUntilStopped=-1, StopAfterSlides=0)
        media = NS(StartPoint=500, EndPoint=20000, Length=20000)
        shape = NS(Type=16, Id=7, AnimationSettings=NS(PlaySettings=play), MediaFormat=media)
        shapes = NS(Count=1, Item=lambda index: shape)
        slide = NS(Shapes=shapes)
        plan = NS(start_frame=0, entry_frames=0, end_frame=300,
                  effects=(ScheduledEffect(1, 0, 0, 300, media=True),))
        _bound_media(slide, plan, 33000, {1: NS(Shape=shape)})
        self.assertEqual(media.EndPoint, 10392)
        self.assertEqual(media.StartPoint, 500)
        self.assertEqual(media.Length, 20000)
        self.assertEqual(play.LoopUntilStopped, -1)
        self.assertEqual(play.StopAfterSlides, 0)
        media.EndPoint = 3000
        _bound_media(slide, plan, 33000, {1: NS(Shape=shape)})
        self.assertEqual(media.EndPoint, 3000)

    def test_media_boundary_does_not_touch_legacy_play_settings(self):
        from app.video.powerpoint import _bound_media
        class Shape:
            Type = 16
            Id = 7
            MediaFormat = NS(StartPoint=0, EndPoint=2000, Length=2000)

            @property
            def AnimationSettings(self):
                raise AssertionError('旧接口会重建媒体播放步骤')
        shape = Shape()
        plan = NS(start_frame=0, entry_frames=0, end_frame=300, effects=())
        _bound_media(NS(Shapes=NS(Count=1, Item=lambda index: shape)), plan, 33000, {})
        self.assertEqual(shape.MediaFormat.EndPoint, 2000)

    def test_media_play_command_does_not_invent_a_repeat_duration(self):
        from app.video.powerpoint import _bound_effect
        timing = NS(Duration=2, RepeatCount=1, RepeatDuration=0, AutoReverse=0)
        before = dict(vars(timing))
        _bound_effect(timing, NS(frame=0, end_frame=300, indefinite=False, media=True), 33000, 1)
        self.assertEqual(vars(timing), before)

    def test_standard_video_pause_control_is_not_a_custom_animation_trigger(self):
        from test_animation_check import media_timing_xml
        document = slide(group(effect('withEffect')))
        root = document.find(f'{{{P}}}timing/{{{P}}}tnLst')
        for child in ET.fromstring(f'<p:tnLst xmlns:p="{P}">{media_timing_xml(interactive=True)}</p:tnLst>'):
            root.append(child)
        parsed = parse_slide_timing(document, 1)
        self.assertEqual(parsed.clicks, 0)
        self.assertEqual(len(parsed.effects), 1)
        control = next(n for n in document.iter(f'{{{P}}}cTn') if n.get('nodeType') == 'interactiveSeq')
        control.find('p:stCondLst/p:cond/p:tgtEl/p:spTgt', {'p': P}).set('spid', '9')
        with self.assertRaisesRegex(ValueError, '指定对象'):
            parse_slide_timing(document, 1)

    def test_schedule_reports_multiple_pages_with_timing_conflicts_together(self):
        timeline = self.timeline([1, 2], .5)
        timings = (SlideTiming(1, 0, ()), SlideTiming(2, 0, ()))
        with self.assertRaises(ValueError) as error:
            build_powerpoint_schedule(timeline, timings, [20, 20], 33000)
        self.assertIn('第 1 页', str(error.exception))
        self.assertIn('第 2 页', str(error.exception))

    def test_real_audio_samples_determine_page_and_click_time(self):
        timeline = self.timeline([1, 2, 2, 3], .973)
        timings = (SlideTiming(1, 0, ()), SlideTiming(2, 1, (
            EffectTiming(1, 0, 0), EffectTiming(2, 1, 0), EffectTiming(3, 1, .2))), SlideTiming(3, 0, ()))
        original = [(cue.action_frame, cue.audio_frame, cue.end_frame) for cue in timeline.cues]
        plan = build_powerpoint_schedule(timeline, timings, [11, 24, 17], 33000)
        self.assertEqual([(cue.action_frame, cue.audio_frame, cue.end_frame) for cue in timeline.cues], original)
        self.assertAlmostEqual(plan.slides[1].start_frame * .033, .973, delta=.0165)
        self.assertAlmostEqual(plan.slides[1].effects[1].frame * .033, 1.946, delta=.0165)
        self.assertAlmostEqual(plan.slides[1].effects[2].frame * .033, 2.146, delta=.0165)
        self.assertEqual(plan.slides[1].effects[0].delay, 0)

    def test_page_rounding_does_not_accumulate(self):
        timeline = self.timeline(list(range(1, 101)), 1.013)
        plan = build_powerpoint_schedule(timeline, tuple(SlideTiming(p, 0, ()) for p in range(1, 101)), [11] * 100, 33000)
        for cue, item in zip(timeline.cues, plan.slides):
            self.assertLessEqual(abs(item.start_frame * .033 - cue.action_frame / SAMPLE_RATE), .0165 + 1e-9)

    def test_click_count_mismatch_is_not_ignored(self):
        timeline = self.timeline([1, 1])
        with self.assertRaisesRegex(ValueError, '需要 1 段'):
            build_powerpoint_schedule(timeline, (SlideTiming(1, 0, ()),), [0], 33000)

    def test_media_padding_from_real_60_fps_failure_is_accepted_but_large_errors_are_not(self):
        from app.video.powerpoint import _video_clock_matches
        plan = NS(total_frames=65139, tick_us=16000)
        info = NS(duration_us=1042368656, video_packets=65148, tick_us=16000, width=1920, height=1080)
        self.assertTrue(_video_clock_matches(info, plan, True))
        self.assertFalse(_video_clock_matches(info, plan, False))
        for difference in (-200001, 200001, -1000000, 1000000):
            info.duration_us = plan.total_frames * plan.tick_us + difference
            self.assertFalse(_video_clock_matches(info, plan, True))

    def test_media_tolerance_does_not_shrink_at_high_fps_or_expand_for_long_presentations(self):
        from app.video.powerpoint import _video_clock_matches
        for fps in (24, 25, 30, 50, 60):
            for duration in (5, 3600):
                plan = NS(total_frames=duration*fps, tick_us=1000000//fps)
                info = NS(duration_us=plan.total_frames*plan.tick_us+145000,
                          video_packets=plan.total_frames+9, tick_us=0, width=1280, height=720)
                self.assertTrue(_video_clock_matches(info, plan, True), (fps, duration))
                info.duration_us += 100000
                self.assertFalse(_video_clock_matches(info, plan, True), (fps, duration))

    def test_ordinary_video_keeps_frame_count_check_and_empty_picture_is_rejected(self):
        from app.video.powerpoint import _video_clock_matches
        plan = NS(total_frames=60, tick_us=33000)
        info = NS(duration_us=1980000, video_packets=60, tick_us=33000, width=1280, height=720)
        self.assertTrue(_video_clock_matches(info, plan, False))
        info.video_packets += 1
        self.assertFalse(_video_clock_matches(info, plan, False))
        info.video_packets = 0
        self.assertFalse(_video_clock_matches(info, plan, True))

    def test_click_during_entry_transition_is_rejected(self):
        timeline = self.timeline([1, 1], .5)
        with self.assertRaisesRegex(ValueError, '转场结束前'):
            build_powerpoint_schedule(timeline, (SlideTiming(1, 1, (EffectTiming(1, 1, 0),)),), [24], 33000)

    def test_page_shorter_than_transition_is_rejected(self):
        with self.assertRaisesRegex(ValueError, '短于转场'):
            build_powerpoint_schedule(self.timeline([1], .5), (SlideTiming(1, 0, ()),), [24], 33000)

    def test_join_does_not_overwrite_sources_or_existing_output(self):
        video, audio, target = self.root / 'video.mp4', self.root / 'audio.m4a', self.root / 'output.mp4'
        video.write_bytes(b'original video')
        audio.write_bytes(b'original audio')
        target.write_bytes(b'keep this output')
        for destination in (video, audio, target):
            with self.assertRaises(ValueError):
                join_narration(video, audio, destination)
        self.assertEqual(target.read_bytes(), b'keep this output')

    def test_probe_and_join_honor_cancellation_before_io(self):
        with self.assertRaises(MuxCancelled):
            probe_media(self.root / 'missing.mp4', cancelled=lambda: True)
        video, audio = self.root / 'video', self.root / 'audio'
        video.touch()
        audio.touch()
        with self.assertRaises(MuxCancelled):
            join_narration(video, audio, self.root / 'new.mp4', cancelled=lambda: True)

    def test_timing_failure_preserves_complete_picture_outside_worker_temp_directory(self):
        from app.video.powerpoint import PowerPointExportTask, PowerPointTimingError
        from app.video.settings import VideoRecordingOptions
        source = self.root / '原文稿.pptx'
        source.write_bytes(b'original presentation')
        target = self.root / 'output.mp4'
        picture_path = []
        def fail(_source, _timeline, directory, *_args, **_kwargs):
            directory.mkdir(parents=True)
            picture = directory / 'picture.mp4'
            picture.write_bytes(b'complete native picture')
            picture_path.append(picture)
            raise PowerPointTimingError(picture, 2000000, 4000000)
        task = PowerPointExportTask(source, self.timeline([1]), [{'page': 1, 'text': '讲稿'}],
                                   target, VideoRecordingOptions())
        with patch('app.video.powerpoint.export_powerpoint_video', side_effect=fail):
            task.run()
        saved = list(self.root.glob('*画面待检查-*.mp4'))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_bytes(), b'complete native picture')
        self.assertIn(str(saved[0]), task.error)
        self.assertIn('未合并讲稿配音', task.error)
        self.assertFalse(target.exists())
        self.assertFalse(picture_path[0].exists())
        self.assertEqual(source.read_bytes(), b'original presentation')

    def test_preserving_failed_picture_never_replaces_existing_files(self):
        from app.video.powerpoint import PowerPointExportTask
        from app.video.settings import VideoRecordingOptions
        picture = self.root / 'picture.mp4'
        picture.write_bytes(b'complete native picture')
        task = PowerPointExportTask(self.root / '原文稿.pptx', self.timeline([1]), [],
                                   self.root / 'output.mp4', VideoRecordingOptions())
        first = task._preserve_failed_picture(picture)
        second = task._preserve_failed_picture(picture)
        self.assertNotEqual(first, second)
        self.assertEqual(first.read_bytes(), picture.read_bytes())
        self.assertEqual(second.read_bytes(), picture.read_bytes())

    def test_compatible_wps_registration_is_identified_separately_from_powerpoint(self):
        from app.video.powerpoint import _require_video_export, VideoExportUnavailable
        app, library = Mock(), Mock()
        app._oleobj_.GetTypeInfo.return_value.GetContainingTypeLib.return_value = (library, 0)
        library.GetLibAttr.return_value = ('{44720440-94BF-4940-926D-4F38FECF2A48}',)
        self.assertEqual(_require_video_export(app), 'WPS 演示')
        library.GetLibAttr.return_value = ('{91493440-5A91-11CF-8700-00AA0060263B}',)
        self.assertEqual(_require_video_export(app), 'PowerPoint')
        library.GetLibAttr.return_value = ('unknown',)
        with self.assertRaises(VideoExportUnavailable):
            _require_video_export(app)

    def test_partial_staging_file_is_removed_on_failure_or_cancellation(self):
        from app.video.powerpoint import PowerPointExportTask
        from app.video.settings import VideoRecordingOptions
        for failure in (RuntimeError('export failed'),MuxCancelled()):
            with self.subTest(failure=type(failure).__name__):
                target = self.root / 'staging.mp4'
                target.touch()
                def fail(*_args,**_kwargs):
                    target.write_bytes(b'incomplete output')
                    raise failure
                task = PowerPointExportTask(self.root/'source.pptx',self.timeline([1]),[],
                                           target,VideoRecordingOptions())
                with patch('app.video.powerpoint.export_powerpoint_video',side_effect=fail):
                    task.run()
                self.assertFalse(target.exists())
                self.assertEqual(task.was_cancelled,isinstance(failure,MuxCancelled))

    def test_worker_keeps_fractional_progress_in_signal(self):
        from app.video.powerpoint import PowerPointExportTask
        from app.video.settings import VideoRecordingOptions
        task=PowerPointExportTask(self.root/'source.pptx',self.timeline([1]),[],
                                 self.root/'staging.mp4',VideoRecordingOptions())
        received=[]
        estimates=[]
        task.progress.connect(lambda _text,value:received.append(value))
        task.estimated_time.connect(estimates.append)
        task.preference = 'wps'
        def report_then_stop(*_args,**kwargs):
            self.assertEqual(kwargs['preference'], 'wps')
            kwargs['progress']('正在生成视频画面…',57.703)
            kwargs['progress']('正在生成视频画面…',57.708)
            kwargs['estimate']('预计剩余约 3 分钟')
            raise RuntimeError('stop after progress')
        with patch('app.video.powerpoint.export_powerpoint_video',side_effect=report_then_stop):
            task.run()
        self.assertEqual(len(received),2)
        self.assertAlmostEqual(received[0],57.703)
        self.assertAlmostEqual(received[1],57.708)
        self.assertGreater(received[1],received[0])
        self.assertEqual(estimates, ['', '预计剩余约 3 分钟', ''])

    def test_success_failure_and_cancellation_release_encoder_and_workspace(self):
        from PySide6.QtCore import QObject,Signal
        from PySide6.QtWidgets import QApplication
        from app.video.powerpoint import PowerPointExportTask
        from app.video.settings import VideoRecordingOptions
        qt = QApplication.instance() or QApplication([])
        for stage in ('audio','join','mux','cancel-mux','done'):
            with self.subTest(stage=stage):
                target=self.root/f'{stage}.mp4'
                target.touch()
                encoders,workspaces=[],[]
                class Encoder(QObject):
                    finished=Signal(str)
                    failed=Signal(str)
                    cancelled=Signal()
                    progress=Signal(float)
                    def __init__(self,_timeline,path):
                        super().__init__()
                        self.target=path
                        self.session=Mock()
                        encoders.append(self)
                    def cancel(self):
                        self.cancelled.emit()
                    def start(self):
                        self.progress.emit(.2)
                        self.target.write_bytes(b'encoded audio')
                        if stage=='audio':self.failed.emit('audio failed')
                        else:self.finished.emit(str(self.target))
                factory=tempfile.TemporaryDirectory
                def temporary(*args,**kwargs):
                    kwargs['dir']=self.root
                    workspace=factory(*args,**kwargs)
                    workspaces.append(Path(workspace.name))
                    return workspace
                def picture(_source,_timeline,directory,_options,**_kwargs):
                    _kwargs['stage']('rendering')
                    _kwargs['progress']('正在生成视频画面…', 12)
                    directory.mkdir()
                    path=directory/'picture.mp4'
                    path.write_bytes(b'picture')
                    return path
                def join(_picture,_audio,output,**_kwargs):
                    self.assertTrue(_audio.is_file())
                    if stage=='join':raise RuntimeError('join failed')
                    output.write_bytes(b'joined')
                def mux(_joined,output,*_args,**_kwargs):
                    output.write_bytes(b'incomplete final output')
                    _kwargs['progress'](5, 10)
                    if stage=='done':return
                    if stage=='cancel-mux':raise MuxCancelled()
                    raise RuntimeError('mux failed')
                task=PowerPointExportTask(self.root/'source.pptx',self.timeline([1]),[],
                                         target,VideoRecordingOptions())
                stages, progress = [], []
                task.stage_changed.connect(stages.append)
                task.progress.connect(lambda message, value: progress.append((task._stage, value)))
                with patch('app.video.powerpoint.tempfile.TemporaryDirectory',side_effect=temporary), \
                        patch('app.video.powerpoint.export_powerpoint_video',side_effect=picture), \
                        patch('app.video.powerpoint.NarrationEncoder',Encoder), \
                        patch('app.video.powerpoint.join_narration',side_effect=join), \
                        patch('app.video.powerpoint.mux_video',side_effect=mux):
                    task.run()
                qt.processEvents()
                self.assertEqual(target.exists(), stage=='done')
                self.assertFalse(any(path.exists() for path in workspaces))
                encoders[0].session.setRecorder.assert_called_once_with(None)
                encoders[0].session.setAudioBufferInput.assert_called_once_with(None)
                self.assertEqual(task.was_cancelled,stage=='cancel-mux')
                if stage not in ('cancel-mux', 'done'):self.assertIn(stage,task.error)
                if stage=='done':
                    self.assertFalse(task.error)
                    self.assertEqual(stages, ['preparing', 'rendering', 'narration', 'joining', 'saving'])
                    for item in (('rendering', 12), ('narration', 0), ('narration', 20),
                                 ('joining', -1), ('saving', 0), ('saving', 50), ('saving', 100)):
                        self.assertIn(item, progress)

    def test_nonempty_destination_is_never_removed_after_rejection(self):
        from app.video.powerpoint import PowerPointExportTask
        from app.video.settings import VideoRecordingOptions
        target = self.root/'old.mp4'
        target.write_bytes(b'user video')
        task = PowerPointExportTask(self.root/'source.pptx',self.timeline([1]),[],
                                   target,VideoRecordingOptions())
        with patch('app.video.powerpoint.export_powerpoint_video') as export:
            task.run()
        export.assert_not_called()
        self.assertEqual(target.read_bytes(),b'user video')

    def test_locked_partial_output_logs_cleanup_error_without_masking_export_error(self):
        from app.video.powerpoint import PowerPointExportTask
        from app.video.settings import VideoRecordingOptions
        target=self.root/'locked-staging.mp4'
        target.touch()
        task=PowerPointExportTask(self.root/'source.pptx',self.timeline([1]),[],
                                 target,VideoRecordingOptions())
        with patch('app.video.powerpoint.export_powerpoint_video',side_effect=RuntimeError('original failure')), \
                patch('pathlib.Path.unlink',side_effect=PermissionError('locked')), \
                self.assertLogs('pptreviewer.video.powerpoint',level='WARNING') as logs:
            task.run()
        self.assertEqual(task.error,'original failure')
        self.assertTrue(any(str(target) in message for message in logs.output))

    def test_workspace_retries_transient_windows_file_lock(self):
        from app.video.powerpoint import _export_workspace
        workspace = tempfile.TemporaryDirectory(dir=self.root)
        cleanup = workspace.cleanup
        calls = []
        def locked_once():
            calls.append(True)
            if len(calls)==1:
                raise PermissionError('temporary Office file lock')
            cleanup()
        with patch('app.video.powerpoint.tempfile.TemporaryDirectory',return_value=workspace), \
                patch.object(workspace,'cleanup',side_effect=locked_once), \
                patch('app.video.powerpoint.time.sleep'):
            with _export_workspace() as directory:
                (directory/'source.pptx').write_bytes(b'private copy')
        self.assertEqual(len(calls),2)
        self.assertFalse(Path(workspace.name).exists())

    def test_locked_workspace_does_not_hide_export_error_or_invalidate_finished_video(self):
        from app.video.powerpoint import _export_workspace
        workspace = NS(name=str(self.root/'locked'),cleanup=Mock(side_effect=PermissionError('locked')))
        with patch('app.video.powerpoint.tempfile.TemporaryDirectory',return_value=workspace), \
                patch('app.video.powerpoint.time.sleep'), \
                self.assertLogs('pptreviewer.video.powerpoint',level='WARNING') as logs:
            with _export_workspace():
                pass
        self.assertIn(workspace.name,logs.output[0])
        self.assertEqual(workspace.cleanup.call_count,5)
        with patch('app.video.powerpoint.tempfile.TemporaryDirectory',return_value=workspace), \
                patch('app.video.powerpoint.time.sleep'), \
                self.assertLogs('pptreviewer.video.powerpoint',level='WARNING'), \
                self.assertRaisesRegex(ValueError,'original failure'):
            with _export_workspace():
                raise ValueError('original failure')


class NarrationEncoderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from PySide6.QtCore import QEventLoop, QTimer
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.loop = QEventLoop()
        self.timer = QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.loop.quit)

    def tearDown(self):
        from PySide6.QtCore import QCoreApplication, QEvent
        self.timer.stop()
        self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.directory.cleanup()

    def make_encoder(self):
        from app.video.narration import NarrationEncoder
        voice = self.root / 'voice.wav'
        with wave.open(str(voice), 'wb') as writer:
            writer.setparams((1, 2, SAMPLE_RATE, 0, 'NONE', 'not compressed'))
            writer.writeframes(array('h', [1000] * SAMPLE_RATE).tobytes())
        timeline = VideoTimeline([1] * 120, [voice] * 120, lead_ms=0, tail_ms=0)
        return NarrationEncoder(timeline, self.root / 'narration.m4a')

    def test_cancel_after_streaming_started_releases_encoder(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtMultimedia import QMediaRecorder
        encoder = self.make_encoder()
        outcomes = []
        encoder.cancelled.connect(lambda: (outcomes.append('cancelled'), self.loop.quit()))
        encoder.finished.connect(lambda _path: (outcomes.append('finished'), self.loop.quit()))
        encoder.failed.connect(lambda text: (outcomes.append(text), self.loop.quit()))
        poll = QTimer()
        poll.setInterval(5)
        poll.timeout.connect(lambda: encoder.cancel() if encoder.cursor > 0 else None)
        poll.start()
        self.timer.start(15000)
        QTimer.singleShot(0, encoder.start)
        self.loop.exec()
        poll.stop()
        self.assertGreater(encoder.cursor, 0)
        self.assertEqual(outcomes, ['cancelled'])
        self.assertEqual(encoder.recorder.recorderState(), QMediaRecorder.RecorderState.StoppedState)
        encoder.deleteLater()

    def test_existing_audio_output_is_preserved(self):
        from PySide6.QtCore import QTimer
        encoder = self.make_encoder()
        encoder.target.write_bytes(b'user audio')
        errors = []
        encoder.failed.connect(lambda text: (errors.append(text), self.loop.quit()))
        self.timer.start(5000)
        QTimer.singleShot(0, encoder.start)
        self.loop.exec()
        self.assertTrue(errors)
        self.assertEqual(encoder.target.read_bytes(), b'user audio')
        encoder.deleteLater()


if __name__ == '__main__':
    unittest.main()
