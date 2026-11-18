import unittest

import rejection_fixture


class RejectionFixtureTests(unittest.TestCase):
    """把 rejection_fixture 自检纳入统一核验入口。"""

    def test_recording_fixture(self):
        rejection_fixture.check_recording()

    def test_sqlite_commit_control(self):
        rejection_fixture.check_commit_control()

    def test_rejection_capture_and_certificate(self):
        rejection_fixture.check_rejection_capture()


if __name__ == '__main__':
    unittest.main()
