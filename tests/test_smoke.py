"""The test harness itself: lib/ is importable."""


def test_lib_imports():
    import common

    assert common.hms(3725) == "01:02:05"
