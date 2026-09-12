"""Unit tests for the silent-substitution probe (no network)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parents[2] / "project"
sys.path.insert(0, str(PROJECT))

from probe_relay_substitution import (  # noqa: E402
    AGREE,
    AGREE_EMPTY,
    AGREE_ERROR,
    BLOCKING,
    DIFFERS,
    INCONCLUSIVE,
    REFERENCE_UNKNOWN_API_ERROR,
    SUBSTITUTION,
    ProbeCase,
    ProbeResult,
    blocking,
    classify_empty_probe,
    classify_error_probe,
    cleared,
    redact_result,
    report,
    run_probe,
)

EMPTY_CASE = ProbeCase("unknown_symbol", "daily", {"ts_code": "999999.SZ"}, "empty")
ERROR_CASE = ProbeCase("unknown_api", "nope", {}, "error")


class FakeClient:
    """One transport stand-in: answers per endpoint, or raises per endpoint."""

    def __init__(self, *, frames=None, errors=None):
        self.frames = frames or {}
        self.errors = errors or {}
        self.calls: list[tuple[str, dict]] = []

    def query(self, endpoint, **params):
        self.calls.append((endpoint, dict(params)))
        if endpoint in self.errors:
            raise RuntimeError(self.errors[endpoint])
        return self.frames.get(endpoint, pd.DataFrame())


def test_classify_empty_probe_agrees_when_both_are_empty():
    assert classify_empty_probe(pd.DataFrame(), pd.DataFrame()) == AGREE_EMPTY


def test_classify_empty_probe_flags_a_non_empty_relay_answer():
    # The relay answered where the official side legitimately has nothing:
    # that is the promax failure mode (fallback_on_empty), which is exactly
    # what no amount of bit-comparison on *populated* responses can detect.
    assert classify_empty_probe(pd.DataFrame(), pd.DataFrame({"x": [1]})) == (
        SUBSTITUTION
    )


def test_classify_empty_probe_is_inconclusive_when_official_is_not_empty():
    # The probe only means something when the official answer really is empty.
    assert classify_empty_probe(pd.DataFrame({"x": [1]}), pd.DataFrame()) == (
        INCONCLUSIVE
    )


def test_classify_error_probe_compares_the_message_verbatim():
    assert (
        classify_error_probe(
            REFERENCE_UNKNOWN_API_ERROR, REFERENCE_UNKNOWN_API_ERROR + " "
        )
        == AGREE_ERROR
    )
    assert classify_error_probe(REFERENCE_UNKNOWN_API_ERROR, "bad api") == DIFFERS


def test_the_reference_guard_tolerates_a_sdk_wrapper_on_both_sides():
    """The guard is a membership test; the judgement itself stays verbatim.

    Both sides answer through the same tushare SDK, so if the SDK wraps the
    server's text in an exception of its own it does so on both sides alike,
    and the two rendered messages are still equal.  The wrap on one side only
    is *not* tolerated -- see the next test: that asymmetry is itself the
    divergence this probe exists to catch.
    """
    wrapped = f"Exception: {REFERENCE_UNKNOWN_API_ERROR}"
    assert classify_error_probe(wrapped, wrapped) == AGREE_ERROR


def test_a_wrapper_on_one_side_only_is_a_divergence():
    # A relay that renders the same server error differently is not running
    # the same pipeline we audited.  Blocking, not a pass -- and Step 7 says to
    # re-run once before treating a blocking verdict as real.
    assert (
        classify_error_probe(
            f"Exception: {REFERENCE_UNKNOWN_API_ERROR}", REFERENCE_UNKNOWN_API_ERROR
        )
        == DIFFERS
    )
    assert (
        classify_error_probe(
            REFERENCE_UNKNOWN_API_ERROR, f"Exception: {REFERENCE_UNKNOWN_API_ERROR}"
        )
        == DIFFERS
    )


def test_an_official_side_that_failed_on_its_own_is_never_evidence():
    """A rejected credential must not be read as relay misconduct.

    The official API answers an unusable token with its own message, which of
    course differs from the relay's -- that difference is ours, not the
    relay's.  Feeding it to ``DIFFERS`` would send a viable relay back for
    review on the strength of a stale `.env`, and would return exit 1 (whose
    playbook is "relay 主供决策回炉") instead of the exit 2 this contract
    reserves for a gate that cannot be opened.
    """
    for official in ("您的token不对，请确认。", "抱歉，您每分钟最多访问该接口1次"):
        assert classify_error_probe(official, REFERENCE_UNKNOWN_API_ERROR) == (
            INCONCLUSIVE
        )
        assert classify_error_probe(official, "internal error") == INCONCLUSIVE


def test_blocking_is_the_two_disqualifying_verdicts():
    assert BLOCKING == frozenset({SUBSTITUTION, DIFFERS})


def test_agree_is_the_two_evidential_verdicts():
    assert AGREE == frozenset({AGREE_EMPTY, AGREE_ERROR})


def test_run_probe_marks_a_substitution_as_blocking():
    relay = FakeClient(frames={"daily": pd.DataFrame({"ts_code": ["000001.SZ"]})})
    official = FakeClient()
    results = run_probe(official, relay, (EMPTY_CASE,))
    assert [result.verdict for result in results] == [SUBSTITUTION]
    assert blocking(results) is True
    assert cleared(results) is False
    assert SUBSTITUTION in report(results)


def test_run_probe_passes_when_both_sides_are_empty():
    results = run_probe(FakeClient(), FakeClient(), (EMPTY_CASE,))
    assert results[0].verdict == AGREE_EMPTY
    assert blocking(results) is False
    assert cleared(results) is True


def test_run_probe_is_inconclusive_when_official_itself_raises():
    official = FakeClient(errors={"daily": "rate limited"})
    results = run_probe(official, FakeClient(), (EMPTY_CASE,))
    assert results[0].verdict == INCONCLUSIVE
    # Inconclusive is not evidence of substitution, but it is not a pass
    # either: a throttled official side must not open a hard gate.
    assert blocking(results) is False
    assert cleared(results) is False


def test_a_throttled_official_side_leaves_the_gate_closed_in_the_report():
    results = run_probe(
        FakeClient(errors={"daily": "rate limited"}), FakeClient(), (EMPTY_CASE,)
    )
    text = report(results)
    assert "阶段 1 闸门保持关闭" in text
    assert "闸门放行" not in text


def test_an_empty_case_list_never_clears_the_gate():
    assert blocking(()) is False
    assert cleared(()) is False


def test_run_probe_compares_error_strings_for_error_cases():
    official = FakeClient(errors={"nope": REFERENCE_UNKNOWN_API_ERROR})
    agreeing = FakeClient(errors={"nope": REFERENCE_UNKNOWN_API_ERROR})
    results = run_probe(official, agreeing, (ERROR_CASE,))
    assert results[0].verdict == AGREE_ERROR
    assert cleared(results) is True

    diverging = FakeClient(errors={"nope": "internal error"})
    results = run_probe(official, diverging, (ERROR_CASE,))
    assert results[0].verdict == DIFFERS
    assert blocking(results) is True


def test_a_stale_official_credential_cannot_produce_a_blocking_verdict():
    # End to end through run_probe: this is the shape the live run actually
    # produced, and it must land as "no evidence", not "relay disqualified".
    results = run_probe(
        FakeClient(errors={"nope": "您的token不对，请确认。"}),
        FakeClient(errors={"nope": "token不对，您传过来的是XXX请确认"}),
        (ERROR_CASE,),
    )
    assert results[0].verdict == INCONCLUSIVE
    assert blocking(results) is False
    assert cleared(results) is False


def test_an_error_case_is_inconclusive_when_one_side_succeeds():
    results = run_probe(
        FakeClient(frames={"nope": pd.DataFrame({"x": [1]})}),
        FakeClient(errors={"nope": REFERENCE_UNKNOWN_API_ERROR}),
        (ERROR_CASE,),
    )
    assert results[0].verdict == INCONCLUSIVE
    assert cleared(results) is False


def test_one_inconclusive_case_keeps_the_whole_run_from_clearing():
    # The gate is all-or-nothing: three agreements do not excuse one hole.
    results = run_probe(
        FakeClient(frames={"daily": pd.DataFrame()}, errors={"nope": "rate limited"}),
        FakeClient(frames={"daily": pd.DataFrame()}),
        (EMPTY_CASE, ERROR_CASE),
    )
    assert [result.verdict for result in results] == [AGREE_EMPTY, INCONCLUSIVE]
    assert blocking(results) is False
    assert cleared(results) is False


SECRET = "relay-key-that-the-remote-echoes"


def test_redact_result_scrubs_both_answers():
    result = ProbeResult(
        name="n",
        endpoint="e",
        official=f"error: rejected {SECRET}",
        relay=f"error: you sent {SECRET}",
        verdict=INCONCLUSIVE,
    )
    scrubbed = redact_result(result, (SECRET,))
    assert SECRET not in scrubbed.official
    assert SECRET not in scrubbed.relay
    assert scrubbed.verdict == result.verdict  # scrubbing never re-judges


def test_a_credential_straddling_the_clip_is_not_partially_leaked():
    """Redact first, cut second.

    Clipping before scrubbing leaves the credential's opening characters in the
    output: too short to match the full value any more, so ``redact_secrets``
    never removes them.  A partial secret is still a secret.

    Driven through ``run_probe`` rather than ``redact_result`` on purpose: the
    leak lives in the *interaction* between the clipping and the scrubbing, so
    a test that hands ``redact_result`` an untruncated message would pass under
    the old code too and pin nothing.
    """
    padding = "x" * 105
    echoing = FakeClient(errors={"nope": f"{padding}{SECRET} tail"})
    results = run_probe(
        FakeClient(errors={"nope": "rate limited"}),
        echoing,
        (ERROR_CASE,),
        secrets=(SECRET,),
    )
    assert SECRET[:8] not in results[0].relay
    assert len(results[0].relay) <= 120  # still clipped for the report


def test_run_probe_never_returns_a_credential():
    """The guard sits at the emission boundary, not at the print site.

    A caller that forgets to scrub would otherwise write the credential to the
    committable report -- which is exactly how the live run leaked it once.
    """
    echoing = FakeClient(errors={"nope": f"token不对，您传过来的是{SECRET}请确认"})
    results = run_probe(
        FakeClient(errors={"nope": "您的token不对，请确认。"}),
        echoing,
        (ERROR_CASE,),
        secrets=(SECRET,),
    )
    assert SECRET not in results[0].relay
    assert "<redacted>" in results[0].relay


def test_report_scrubs_a_result_handed_to_it_directly():
    # Defense in depth: `report` is public and can be called with results that
    # never went through `run_probe`.
    result = ProbeResult(
        name="n",
        endpoint="e",
        official="ok: 0 rows",
        relay=f"error: you sent {SECRET}",
        verdict=INCONCLUSIVE,
    )
    text = report([result], secrets=(SECRET,))
    assert SECRET not in text
    assert "<redacted>" in text
