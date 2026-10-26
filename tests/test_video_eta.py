"""当前任务测速、速度变化和预估展示，不使用任何设备耗时基准。"""

import math
import unittest

from app.video.eta import RemainingTime, RemainingTimeDisplay, RemainingTimeEstimator, format_remaining


class RemainingTimeTests(unittest.TestCase):
    @staticmethod
    def measure(duration, elapsed):
        estimator = RemainingTimeEstimator()
        result = None
        for second in range(elapsed + 1):
            result = estimator.observe(second / duration, second)
        return result

    def test_warmup_requires_measured_progress_in_this_task(self):
        estimator = RemainingTimeEstimator()
        for second in range(10):
            self.assertIsNone(estimator.observe(second / 100, second))
        result = estimator.observe(.1, 10)
        self.assertLess(result.lower, 90)
        self.assertGreater(result.upper, 90)
        self.assertIsNone(RemainingTimeEstimator().observe(.5, 200))

    def test_different_device_speeds_use_only_their_own_elapsed_time(self):
        fast = self.measure(100, 20)
        slow = self.measure(200, 40)
        self.assertAlmostEqual(slow.lower, fast.lower * 2)
        self.assertAlmostEqual(slow.upper, fast.upper * 2)

    def test_estimates_follow_a_slowdown_and_recover_after_acceleration(self):
        estimator = RemainingTimeEstimator()
        for second in range(21):
            before = estimator.observe(second * .005, second)
        for second in range(21, 46):
            slower = estimator.observe(.1 + (second - 20) * .001, second)
        self.assertGreater(slower.upper, before.upper * 2)
        for second in range(46, 81):
            faster = estimator.observe(.125 + (second - 45) * .01, second)
        self.assertLess(faster.upper, slower.lower)

    def test_stalled_progress_stops_showing_the_old_estimate(self):
        estimator = RemainingTimeEstimator()
        for second in range(16):
            result = estimator.observe(second / 100, second)
        self.assertIsNotNone(result)
        for second in range(16, 21):
            result = estimator.observe(.15, second)
        self.assertIsNone(result)
        estimator.reset()
        self.assertIsNone(estimator.observe(.3, 30))

    def test_gaps_regression_and_backward_clock_restart_warmup(self):
        for timestamp, fraction in ((30, .3), (16, .1), (14, .2)):
            with self.subTest(timestamp=timestamp, fraction=fraction):
                estimator = RemainingTimeEstimator()
                for second in range(16):
                    estimator.observe(second / 100, second)
                self.assertIsNone(estimator.observe(fraction, timestamp))
                self.assertEqual(len(estimator.samples), 1)

    def test_completion_invalid_values_and_insufficient_motion(self):
        for value in (1, 1.1, -.1, math.inf, math.nan):
            estimator = RemainingTimeEstimator()
            for second in range(11):
                estimator.observe(second / 100, second)
            self.assertIsNone(estimator.observe(value, 11))
            self.assertFalse(estimator.samples)
        estimator = RemainingTimeEstimator()
        for second in range(20):
            self.assertIsNone(estimator.observe(0, second))
        self.assertIsNone(estimator.observe(.1, math.nan))
        self.assertFalse(estimator.samples)
        self.assertIsNone(self.measure(10.1, 10))

    def test_long_tasks_keep_only_a_short_sample_window(self):
        estimator = RemainingTimeEstimator()
        for tick in range(10001):
            result = estimator.observe(tick / 20000, tick / 10)
        self.assertIsNotNone(result)
        self.assertLessEqual(len(estimator.samples), 122)
        self.assertLessEqual(estimator.samples[-1][0] - estimator.samples[0][0], 61)

    def test_display_uses_one_approximate_time_instead_of_a_range(self):
        cases = (
            (None, '正在估算时间…'),
            (RemainingTime(1, 8), '预计剩余约 10 秒'),
            (RemainingTime(24, 46), '预计剩余约 40 秒'),
            (RemainingTime(80, 170), '预计剩余约 3 分钟'),
            (RemainingTime(100, 110), '预计剩余约 2 分钟'),
            (RemainingTime(3700, 9000), '预计剩余约 2 小时'),
        )
        for estimate, expected in cases:
            with self.subTest(estimate=estimate):
                self.assertEqual(format_remaining(estimate), expected)


class RemainingTimeDisplayTests(unittest.TestCase):
    def test_short_speed_fluctuations_do_not_change_the_display(self):
        display = RemainingTimeDisplay()
        first = display.update(RemainingTime(320, 360), 0)
        for tick in range(1, 121):
            now = tick / 4
            spike = 100 if 10 <= now < 12 else 0
            text = display.update(RemainingTime(320 - now + spike, 360 - now + spike), now)
            self.assertEqual(text, first)

    def test_persistent_changes_are_followed_without_rapid_text_changes(self):
        display = RemainingTimeDisplay()
        previous = display.update(RemainingTime(300, 340), 0)
        changes = [0]
        for tick in range(1, 521):
            now = tick / 4
            estimate = RemainingTime(600, 660) if now <= 35 else RemainingTime(50, 55)
            text = display.update(estimate, now)
            if text != previous:
                changes.append(now)
                previous = text
            if now == 30:
                self.assertNotEqual(text, '预计剩余约 6 分钟')
        self.assertGreater(len(changes), 2)
        self.assertTrue(all(after - before >= 10 for before, after in zip(changes, changes[1:])))
        self.assertIn('秒', previous)

    def test_regular_countdown_does_not_acquire_smoothing_delay(self):
        display = RemainingTimeDisplay()
        for second in range(156):
            text = display.update(RemainingTime(180 - second, 200 - second), second)
        # 显示节流和十秒取整允许少量滞后，不应再叠加平滑窗口的二十秒。
        self.assertIn(text, ('预计剩余约 40 秒', '预计剩余约 50 秒'))

    def test_brief_rounding_boundary_crossing_does_not_flash_another_value(self):
        display = RemainingTimeDisplay()
        first = display.update(RemainingTime(100, 125), 0)
        # 同一完成时刻接近两分钟分界；短暂越界不应立即改写文案。
        for second in range(1, 7):
            self.assertEqual(display.update(RemainingTime(100 - second, 125 - second), second), first)
        for second in range(7, 11):
            self.assertEqual(display.update(RemainingTime(100 - second, 200 - second), second), first)

    def test_lost_progress_new_jobs_and_time_discontinuities_discard_old_display(self):
        display = RemainingTimeDisplay()
        display.update(RemainingTime(3000, 3500), 0)
        self.assertEqual(display.update(None, 1), '正在估算时间…')
        self.assertEqual(display.update(RemainingTime(10, 20), 2), '预计剩余约 20 秒')
        display.reset()
        self.assertEqual(display.update(RemainingTime(100, 110), 3), '预计剩余约 2 分钟')
        self.assertEqual(display.update(RemainingTime(10, 20), 30), '预计剩余约 20 秒')
        self.assertEqual(display.update(RemainingTime(100, 110), 29), '预计剩余约 2 分钟')
        self.assertEqual(RemainingTimeDisplay().update(RemainingTime(10, 20), 100),
                         '预计剩余约 20 秒')


if __name__ == '__main__':
    unittest.main()
