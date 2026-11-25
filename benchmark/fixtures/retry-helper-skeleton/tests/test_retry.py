import pytest

from app.retry import RetryExhausted, retry_call


def test_returns_first_success_without_extra_sleep():
    calls = []
    sleeps = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("boom")
        return "ok"

    result = retry_call(flaky, attempts=5, delays=(0.5, 1.5), sleep=sleeps.append)
    assert result == "ok"
    assert len(calls) == 3
    assert sleeps == [0.5, 1.5]


def test_last_delay_is_reused_and_cause_is_kept():
    sleeps = []

    def always_fail():
        raise KeyError("nope")

    with pytest.raises(RetryExhausted) as exc:
        retry_call(always_fail, attempts=4, delays=(0.2,), sleep=sleeps.append,
                   exceptions=(KeyError,))
    assert sleeps == [0.2, 0.2, 0.2]
    assert "4" in str(exc.value)
    assert isinstance(exc.value.__cause__, KeyError)


def test_unlisted_exception_propagates_immediately():
    calls = []

    def boom():
        calls.append(1)
        raise TypeError("bad")

    with pytest.raises(TypeError):
        retry_call(boom, attempts=3, delays=(0.1,), sleep=lambda s: None,
                   exceptions=(ValueError,))
    assert calls == [1]


def test_single_attempt_never_sleeps():
    sleeps = []

    def boom():
        raise ValueError("once")

    with pytest.raises(RetryExhausted):
        retry_call(boom, attempts=1, delays=(0.1,), sleep=sleeps.append)
    assert sleeps == []


def test_attempts_must_be_positive():
    with pytest.raises(ValueError):
        retry_call(lambda: 1, attempts=0)
