"""Tests for the claim schema and the arithmetic that rules on it.

This is the ground truth of the entire project: if verification is wrong, every
downstream claim about "we caught the errors" is also wrong. So the fixtures below
are small hand-built event lists whose momentum can be worked out on paper, rather
than generated matches whose expected values came from the same functions under
test.

The distinction the tests keep insisting on is between a claim that is *malformed*
and a claim that is *wrong*. The first needs the model to speak the protocol; the
second needs the model to be corrected. Collapsing them would make the retry prompt
useless in one of the two cases.
"""

from __future__ import annotations

import unittest

from pitchframe.claims import (
    Claim,
    ClaimError,
    baseline_window_for,
    extract_json_payloads,
    iter_claim_payloads,
    parse_claim,
    summarise,
    validate_claim,
    verify_claim,
)
from pitchframe.schema import MatchEvent, Player

_HOME = Player(id="H1", name="Alaro", team="home", position="CM", shirt=8)
_AWAY = Player(id="A1", name="Brenvik", team="away", position="CM", shirt=8)


def ev(
    kind: str,
    *,
    team: str = "home",
    clock: int = 0,
    outcome: str | None = None,
    pressure: float | None = None,
) -> MatchEvent:
    """A minimal event; only the fields a metric needs are populated."""
    return MatchEvent(
        id=f"e{clock}-{kind}-{team}",
        match_id="T",
        clock=clock,
        type=kind,  # type: ignore[arg-type]
        team=team,  # type: ignore[arg-type]
        player=_HOME if team == "home" else _AWAY,
        outcome=outcome,  # type: ignore[arg-type]
        pressure=pressure,
    )


#: Momentum weights are shot 3, goal 8, tackle 1, pass 0.2, signed by team.
#: Baseline [0,100) scores +3 (one home shot); window [100,200) scores 0
#: (one home shot, one away shot). So momentum fell by 3.
_MOMENTUM_FALLS = [
    ev("shot", team="home", clock=10),
    ev("shot", team="home", clock=110),
    ev("shot", team="away", clock=120),
    # A trailing event past the window's end. Without it the window has not
    # "happened" yet, every claim about it is rejected as premature, and the
    # direction checks these fixtures exist to exercise would never run.
    ev("pass", team="home", clock=300, outcome="success"),
]


class TestBaselineWindow(unittest.TestCase):
    def test_immediately_precedes_with_equal_length(self) -> None:
        self.assertEqual(baseline_window_for((300, 400)), (200, 300))

    def test_rejects_reversed_window(self) -> None:
        with self.assertRaises(ValueError):
            baseline_window_for((400, 300))

    def test_rejects_empty_window(self) -> None:
        with self.assertRaises(ValueError):
            baseline_window_for((100, 100))

    def test_rejects_window_with_no_room_before_kickoff(self) -> None:
        """A first-minute claim has nothing to be compared against."""
        with self.assertRaises(ValueError):
            baseline_window_for((0, 60))


class TestExtractJson(unittest.TestCase):
    def test_bare_object(self) -> None:
        self.assertEqual(extract_json_payloads('{"a": 1}'), ({"a": 1},))

    def test_bare_array(self) -> None:
        self.assertEqual(extract_json_payloads('[{"a": 1}]'), ([{"a": 1}],))

    def test_wrapped_in_prose(self) -> None:
        text = 'Here are the claims:\n[{"a": 1}]\nLet me know if you want more.'
        self.assertEqual(extract_json_payloads(text), ([{"a": 1}],))

    def test_wrapped_in_code_fence(self) -> None:
        text = 'Sure!\n```json\n[{"a": 1}]\n```\n'
        self.assertEqual(extract_json_payloads(text), ([{"a": 1}],))

    def test_multiple_payloads_in_order(self) -> None:
        text = '{"n": 1} then {"n": 2}'
        self.assertEqual(extract_json_payloads(text), ({"n": 1}, {"n": 2}))

    def test_braces_inside_strings_do_not_confuse_depth(self) -> None:
        text = '{"text": "a } brace and a { bracket"}'
        self.assertEqual(extract_json_payloads(text), ({"text": "a } brace and a { bracket"},))

    def test_escaped_quote_inside_string(self) -> None:
        text = r'{"text": "he said \"up\" loudly"}'
        self.assertEqual(extract_json_payloads(text), ({"text": 'he said "up" loudly'},))

    def test_nested_objects(self) -> None:
        text = '{"outer": {"inner": 1}}'
        self.assertEqual(extract_json_payloads(text), ({"outer": {"inner": 1}},))

    def test_unterminated_object_yields_nothing(self) -> None:
        self.assertEqual(extract_json_payloads('{"a": 1'), ())

    def test_no_json_at_all(self) -> None:
        self.assertEqual(extract_json_payloads("I cannot help with that."), ())

    def test_unbalanced_brace_in_prose_does_not_discard_later_json(self) -> None:
        """Real prose carries stray braces; one must not cost the whole reply."""
        text = 'I could not use {window 60-90 because of the restart\n[{"a": 1}]'
        self.assertEqual(extract_json_payloads(text), ([{"a": 1}],))

    def test_unbalanced_bracket_in_prose_does_not_discard_later_json(self) -> None:
        text = 'Notes on the period [see below\n[{"a": 1}]'
        self.assertEqual(extract_json_payloads(text), ([{"a": 1}],))

    def test_unbalanced_opener_after_valid_json_keeps_the_valid_payload(self) -> None:
        text = '[{"a": 1}] and then, for what it is worth {unclosed'
        self.assertEqual(extract_json_payloads(text), ([{"a": 1}],))

    def test_recovery_from_a_stray_opener_still_finds_every_later_payload(self) -> None:
        text = 'bad { brace\n{"n": 1} and {"n": 2}'
        self.assertEqual(extract_json_payloads(text), ({"n": 1}, {"n": 2}))


class TestIterClaimPayloads(unittest.TestCase):
    """The containers a model wraps its claims in. All of them mean "a list"."""

    def _claim(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "claim_id": "C1",
            "metric": "momentum",
            "team": None,
            "window": [100, 200],
            "direction": "up",
            "magnitude": 3.0,
            "text": "t",
        }
        base.update(overrides)
        return base

    def test_flat_object_is_yielded_unchanged(self) -> None:
        payload = self._claim()
        self.assertEqual(list(iter_claim_payloads(payload)), [payload])

    def test_bare_array_is_flattened(self) -> None:
        claims = [self._claim(claim_id="C1"), self._claim(claim_id="C2")]
        self.assertEqual(list(iter_claim_payloads(claims)), claims)

    def test_doubly_nested_array_is_flattened(self) -> None:
        claim = self._claim()
        self.assertEqual(list(iter_claim_payloads([[claim]])), [claim])

    def test_named_envelope_is_unwrapped(self) -> None:
        claim = self._claim()
        self.assertEqual(list(iter_claim_payloads({"claims": [claim]})), [claim])

    def test_singular_envelope_is_unwrapped(self) -> None:
        claim = self._claim()
        self.assertEqual(list(iter_claim_payloads({"claim": claim})), [claim])

    def test_stacked_envelopes_are_unwrapped(self) -> None:
        claim = self._claim()
        self.assertEqual(list(iter_claim_payloads({"data": {"claims": [claim]}})), [claim])

    def test_scalars_mixed_into_an_array_are_skipped(self) -> None:
        claim = self._claim()
        self.assertEqual(list(iter_claim_payloads([None, 1, "text", claim])), [claim])

    def test_unrecognised_object_is_yielded_so_the_error_can_be_precise(self) -> None:
        """Swallowing it here would turn a diagnosable failure into silence."""
        payload = {"metric": "momentum"}  # no direction, no envelope key
        self.assertEqual(list(iter_claim_payloads(payload)), [payload])

    def test_an_envelope_key_inside_a_real_claim_does_not_double_yield(self) -> None:
        claim = self._claim(results=[{"claim_id": "nested"}])
        self.assertEqual(list(iter_claim_payloads(claim)), [claim])

    def test_a_scalar_payload_yields_nothing(self) -> None:
        self.assertEqual(list(iter_claim_payloads("no claims here")), [])


class TestFieldSpellings(unittest.TestCase):
    """Names and spellings a model reaches for when it drifts off the schema."""

    def _payload(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "claim_id": "C1",
            "metric": "momentum",
            "team": None,
            "window": [100, 200],
            "direction": "up",
            "magnitude": 3.0,
            "text": "t",
        }
        base.update(overrides)
        return base

    def test_alias_spellings_are_read_as_the_fields_they_mean(self) -> None:
        claim = parse_claim(
            {
                "id": "C1",
                "metric_name": "momentum",
                "side": "home",
                "period": [100, 200],
                "trend": "up",
                "amount": 3.0,
                "narrative": "home on top",
            }
        )
        self.assertEqual(claim.claim_id, "C1")
        self.assertEqual(claim.metric, "momentum")
        self.assertEqual(claim.team, "home")
        self.assertEqual(claim.window, (100, 200))
        self.assertEqual(claim.direction, "up")
        self.assertEqual(claim.magnitude, 3.0)
        self.assertEqual(claim.text, "home on top")

    def test_camel_case_spellings_are_read(self) -> None:
        claim = parse_claim(
            {
                "claimId": "C1",
                "metricName": "momentum",
                "windowSeconds": [100, 200],
                "direction": "up",
            }
        )
        self.assertEqual(claim.claim_id, "C1")
        self.assertEqual(claim.window, (100, 200))

    def test_the_canonical_spelling_wins_over_an_alias(self) -> None:
        """A payload carrying both must be read the way the schema says."""
        claim = parse_claim(self._payload(id="OTHER", metric_name="chaos_index"))
        self.assertEqual(claim.claim_id, "C1")
        self.assertEqual(claim.metric, "momentum")

    def test_unknown_key_names_report_the_keys_that_arrived(self) -> None:
        """That message is what the model is retried with, so it must be specific."""
        with self.assertRaises(ClaimError) as caught:
            parse_claim({"label": "C1", "score": "momentum", "when": [100, 200], "way": "up"})
        message = str(caught.exception)
        self.assertIn("missing required key", message)
        self.assertIn("label", message)

    def test_an_unlisted_key_is_not_guessed_at(self) -> None:
        """Tolerance covers spelling, not inference."""
        payload = self._payload()
        del payload["direction"]
        payload["vibe"] = "up"
        with self.assertRaises(ClaimError):
            parse_claim(payload)

    def test_extra_keys_are_ignored_rather_than_fatal(self) -> None:
        """Models volunteer confidence and reasoning; that is not malformation."""
        claim = parse_claim(self._payload(confidence=0.9, reasoning="they pressed high"))
        self.assertEqual(claim.claim_id, "C1")

    def test_direction_case_is_spelling_not_meaning(self) -> None:
        self.assertEqual(parse_claim(self._payload(direction="DOWN")).direction, "down")

    def test_a_synonym_for_direction_is_still_rejected(self) -> None:
        """'rising' is an interpretation of 'up', and this parser does not guess."""
        with self.assertRaises(ClaimError):
            parse_claim(self._payload(direction="rising"))

    def test_team_case_is_spelling(self) -> None:
        self.assertEqual(parse_claim(self._payload(team="HOME")).team, "home")

    def test_window_spelled_as_start_and_end_parses(self) -> None:
        claim = parse_claim(self._payload(window={"start": 100, "end": 200}))
        self.assertEqual(claim.window, (100, 200))

    def test_reversed_bounds_in_a_window_object_are_still_rejected(self) -> None:
        with self.assertRaises(ClaimError):
            parse_claim(self._payload(window={"start": 200, "end": 100}))


class TestRealisticReply(unittest.TestCase):
    """One reply carrying every shape at once, the way a real model produces them."""

    REPLY = """\
Quick notes first: I skipped {window 200-300 because it has not closed yet.

```json
{
  "claims": [
    {
      "claimId": "C1",
      "metric_name": "momentum",
      "period": {"start": 100, "end": 200},
      "trend": "down",
      "amount": 3.0,
      "sentence": "Momentum swung away from the home side."
    },
    {
      "id": "C2",
      "metric": "momentum",
      "window": [100, 200],
      "direction": "up",
      "magnitude": 3.0,
      "text": "Momentum swung their way."
    }
  ]
}
```

Let me know if you want the rest of the windows.
"""

    def _parse_reply(self, text: str) -> list[Claim]:
        return [
            parse_claim(item)
            for payload in extract_json_payloads(text)
            for item in iter_claim_payloads(payload)
        ]

    def test_every_shape_at_once_still_yields_both_claims(self) -> None:
        claims = self._parse_reply(self.REPLY)
        self.assertEqual([c.claim_id for c in claims], ["C1", "C2"])

    def test_aliased_fields_arrive_as_the_fields_they_mean(self) -> None:
        first, _ = self._parse_reply(self.REPLY)
        self.assertEqual(first.metric, "momentum")
        self.assertEqual(first.window, (100, 200))
        self.assertEqual(first.direction, "down")
        self.assertEqual(first.text, "Momentum swung away from the home side.")

    def test_a_realistic_reply_reaches_verification_intact(self) -> None:
        """The point of the tolerance: nothing is lost on the way to the arithmetic."""
        verdicts = [verify_claim(c, _MOMENTUM_FALLS) for c in self._parse_reply(self.REPLY)]
        self.assertEqual([v.status for v in verdicts], ["verified", "rejected"])
        self.assertEqual(verdicts[1].measured_direction, "down")


class TestParseClaim(unittest.TestCase):
    def _payload(self, **overrides: object) -> dict[str, object]:
        base: dict[str, object] = {
            "claim_id": "C1",
            "metric": "momentum",
            "team": None,
            "window": [100, 200],
            "direction": "up",
            "magnitude": 3.0,
            "text": "momentum shifted",
        }
        base.update(overrides)
        return base

    def test_valid_payload(self) -> None:
        claim = parse_claim(self._payload())
        self.assertEqual(claim.claim_id, "C1")
        self.assertEqual(claim.window, (100, 200))
        self.assertEqual(claim.direction, "up")

    def test_missing_metric_is_an_error(self) -> None:
        payload = self._payload()
        del payload["metric"]
        with self.assertRaises(ClaimError):
            parse_claim(payload)

    def test_bad_direction_is_an_error(self) -> None:
        with self.assertRaises(ClaimError):
            parse_claim(self._payload(direction="sideways"))

    def test_bad_team_is_an_error(self) -> None:
        with self.assertRaises(ClaimError):
            parse_claim(self._payload(team="both"))

    def test_reversed_window_is_an_error(self) -> None:
        with self.assertRaises(ClaimError):
            parse_claim(self._payload(window=[200, 100]))

    def test_non_numeric_magnitude_is_an_error(self) -> None:
        with self.assertRaises(ClaimError):
            parse_claim(self._payload(magnitude="a lot"))

    def test_empty_claim_id_is_an_error(self) -> None:
        with self.assertRaises(ClaimError):
            parse_claim(self._payload(claim_id="   "))

    def test_team_may_be_omitted_entirely(self) -> None:
        payload = self._payload()
        del payload["team"]
        self.assertIsNone(parse_claim(payload).team)


class TestValidateClaim(unittest.TestCase):
    def _claim(self, **overrides: object) -> Claim:
        return parse_claim(
            {
                "claim_id": "C1",
                "metric": "momentum",
                "team": None,
                "window": [100, 200],
                "direction": "up",
                "magnitude": 1.0,
                **overrides,
            }
        )

    def test_well_formed_claim_has_no_problems(self) -> None:
        self.assertEqual(validate_claim(self._claim(), _MOMENTUM_FALLS), ())

    def test_unknown_metric_is_caught(self) -> None:
        problems = validate_claim(self._claim(metric="vibes"), _MOMENTUM_FALLS)
        self.assertTrue(any("unknown metric" in p for p in problems))

    def test_unknown_metric_lists_the_real_ones(self) -> None:
        """The message is what the retry prompt reads, so it must be actionable."""
        problems = validate_claim(self._claim(metric="vibes"), _MOMENTUM_FALLS)
        self.assertIn("momentum", problems[0])

    def test_window_beyond_the_data_is_caught(self) -> None:
        problems = validate_claim(self._claim(window=[900, 1000]), _MOMENTUM_FALLS)
        self.assertTrue(any("extends past" in p for p in problems))

    def test_unclosed_window_is_caught_when_the_analyser_says_so(self) -> None:
        """The live-pipeline race: a claim about a window that has not closed."""
        problems = validate_claim(
            self._claim(window=[100, 200]),
            _MOMENTUM_FALLS,
            closed_windows=[(0, 100)],
        )
        self.assertTrue(any("has not been closed" in p for p in problems))

    def test_team_scoped_metric_without_a_team_is_caught(self) -> None:
        problems = validate_claim(self._claim(metric="pass_accuracy"), _MOMENTUM_FALLS)
        self.assertTrue(any("requires a team" in p for p in problems))

    def test_whole_match_metric_with_a_team_is_caught(self) -> None:
        problems = validate_claim(
            self._claim(metric="chaos_index", team="home"), _MOMENTUM_FALLS
        )
        self.assertTrue(any("takes no team" in p for p in problems))

    def test_per_team_metric_accepts_a_team(self) -> None:
        claim = self._claim(metric="possession_share", team="home")
        self.assertEqual(validate_claim(claim, _MOMENTUM_FALLS), ())

    def test_claim_with_no_baseline_room_is_caught(self) -> None:
        problems = validate_claim(self._claim(window=[0, 100]), _MOMENTUM_FALLS)
        self.assertTrue(any("no baseline" in p for p in problems))


class TestVerifyClaim(unittest.TestCase):
    def _claim(self, direction: str, **overrides: object) -> Claim:
        return parse_claim(
            {
                "claim_id": "C1",
                "metric": "momentum",
                "team": None,
                "window": [100, 200],
                "direction": direction,
                "magnitude": 3.0,
                **overrides,
            }
        )

    def test_correct_claim_verifies(self) -> None:
        verdict = verify_claim(self._claim("down"), _MOMENTUM_FALLS)
        self.assertTrue(verdict.is_verified)
        self.assertEqual(verdict.measured_direction, "down")

    def test_inverted_claim_is_rejected(self) -> None:
        """The canonical failure this whole system exists to catch."""
        verdict = verify_claim(self._claim("up"), _MOMENTUM_FALLS)
        self.assertFalse(verdict.is_verified)
        self.assertEqual(verdict.measured_direction, "down")
        self.assertEqual(verdict.claimed_direction, "up")

    def test_rejection_carries_the_numbers(self) -> None:
        verdict = verify_claim(self._claim("up"), _MOMENTUM_FALLS)
        self.assertEqual(verdict.measured_value, 0.0)
        self.assertEqual(verdict.baseline_value, 3.0)
        self.assertEqual(verdict.delta, -3.0)

    def test_discrepancy_puts_both_sides_side_by_side(self) -> None:
        verdict = verify_claim(self._claim("up"), _MOMENTUM_FALLS)
        text = verdict.discrepancy()
        self.assertIn("claimed up", text)
        self.assertIn("measured down", text)

    def test_flat_metric_is_rejected_in_both_directions(self) -> None:
        """A directional claim about an unchanged metric is true in neither direction."""
        events = [
            ev("shot", team="home", clock=10),
            ev("shot", team="home", clock=110),
            ev("pass", team="home", clock=300, outcome="success"),
        ]
        self.assertFalse(verify_claim(self._claim("up"), events).is_verified)
        self.assertFalse(verify_claim(self._claim("down"), events).is_verified)

    def test_flat_rejection_says_flat(self) -> None:
        events = [
            ev("shot", team="home", clock=10),
            ev("shot", team="home", clock=110),
            ev("pass", team="home", clock=300, outcome="success"),
        ]
        self.assertIn("flat", verify_claim(self._claim("up"), events).reason)

    def test_unverifiable_claim_is_rejected_not_verified(self) -> None:
        """Malformed input must never default to 'verified'."""
        verdict = verify_claim(self._claim("down", metric="vibes"), _MOMENTUM_FALLS)
        self.assertFalse(verdict.is_verified)
        self.assertIsNone(verdict.measured_direction)

    def test_verification_ignores_the_claimed_magnitude(self) -> None:
        """Only direction is checked; a wrong magnitude does not rescue a wrong claim."""
        verdict = verify_claim(self._claim("up", magnitude=999.0), _MOMENTUM_FALLS)
        self.assertFalse(verdict.is_verified)


class TestSummarise(unittest.TestCase):
    def test_counts_by_status(self) -> None:
        claim = parse_claim(
            {
                "claim_id": "C1",
                "metric": "momentum",
                "team": None,
                "window": [100, 200],
                "direction": "down",
                "magnitude": 3.0,
            }
        )
        counts = summarise([verify_claim(claim, _MOMENTUM_FALLS)])
        self.assertEqual(counts["verified"], 1)


if __name__ == "__main__":
    unittest.main()
