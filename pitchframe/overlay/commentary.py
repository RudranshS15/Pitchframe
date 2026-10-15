"""Turning a checked claim into a sentence a fan would read.

The hard rule in this project is that the narrative layer may not assert free text.
This module does not weaken that rule — it is a *renderer*, not a writer. It receives a
claim that has already been decided by arithmetic, and it chooses words for a fact it
did not determine:

* the direction comes from the verdict, never from the template;
* every gloss is fixed per ``(metric, direction)``, so the same movement always reads
  the same way and no phrasing can quietly drift into claiming more than the metric
  means;
* the glosses below are written to the definitions in :mod:`pitchframe.stats`, not to
  what sounds good. ``chaos_index`` measures the irregularity of the gaps between
  events, so "the game has opened up" is fair and "they are passing better" is not.

That last point is why the commentary cannot invent a cause. It can say the threat
tipped; it cannot say a substitution caused it, because nothing in the event stream
says so. Where a real incident from the window is quoted, it is quoted as something
that *happened alongside* the movement, never as its explanation.

Standard library only, so the overlay's text layer is testable without a model.
"""

from __future__ import annotations

__all__ = ["compose", "incident_clause"]

#: One gloss per metric and direction. ``{team}`` is the side the claim names,
#: ``{home}`` / ``{away}`` are the side names for metrics whose sign is home-relative.
_TEMPLATES: dict[str, dict[str, str]] = {
    # Net attacking threat, signed so that positive favours the home side. A claim
    # about it therefore names which way the balance tipped, not simply "up".
    "momentum": {
        "up": "The balance of threat has tipped toward {home}.",
        "down": "The balance of threat has tipped toward {away}.",
    },
    # Share of completed passes.
    "possession_share": {
        "up": "{team} are seeing more of the ball.",
        "down": "{team} are seeing less of the ball.",
    },
    # Completed passes as a fraction of attempted passes.
    "pass_accuracy": {
        "up": "{team} are finding their passes again.",
        "down": "{team} are giving the ball away.",
    },
    # Mean pass difficulty: distance blended with the pressure the passer was under.
    "pass_difficulty_mean": {
        "up": "{team} are attempting harder passes.",
        "down": "{team} are keeping it simple.",
    },
    # Mean pressure recorded across events, both sides.
    "pressure_index": {
        "up": "Neither side is being given time on the ball.",
        "down": "Both sides are being given room to play.",
    },
    # Irregularity of the gaps between events: 0 metronomic, 1 frantic.
    "chaos_index": {
        "up": "The game has opened up — end to end.",
        "down": "The game has settled into a rhythm.",
    },
    # Highest recorded ball speed.
    "peak_speed": {
        "up": "The ball is being moved faster than it was.",
        "down": "The pace has come off the game.",
    },
    # Longest run of consecutive completed passes under pressure.
    "longest_pressure_pass_streak": {
        "up": "{team} are stringing passes together under pressure.",
        "down": "{team} are no longer playing through the press.",
    },
}

#: How to describe an incident when one is quoted alongside a movement.
_INCIDENT_LABELS = {"goal": "goal", "shot": "shot", "tackle": "tackle"}


def incident_clause(event_type: str, player: str, minute: int) -> str:
    """A factual tail for a commentary line, or ``""`` if the event is unremarkable.

    Deliberately additive rather than causal: the window *also* contained this. The
    prompt-facing wording would be "because of" and there is nothing in the data that
    supports it.
    """
    label = _INCIDENT_LABELS.get(event_type)
    if label is None:
        return ""
    return f"The spell also brought a {label} from {player} ({minute}')."


def compose(
    metric: str,
    direction: str,
    *,
    team: str | None,
    home: str,
    away: str,
    incident: str = "",
) -> str:
    """Render one checked claim as a sentence.

    Args:
        metric: the claimed metric's name.
        direction: the direction to phrase — for a rejected claim this is what the
            model *said*, and for a published one it is what was *measured*. The
            caller decides which; this function has no opinion, which is what keeps
            it a renderer.
        team: the side the claim names, or ``None`` for a whole-match metric.
        home: home side's display name.
        away: away side's display name.
        incident: an optional clause from :func:`incident_clause`.

    Returns:
        A sentence. An unrecognised metric yields the bare measurement rather than a
        guess, because inventing a gloss for a metric nobody registered is exactly
        the behaviour the claim layer exists to prevent.
    """
    side = {"home": home, "away": away}.get(team or "", "")
    template = _TEMPLATES.get(metric, {}).get(direction)
    if template is None:
        return f"{metric.replace('_', ' ')} moved {direction}."

    sentence = template.format(team=side, home=home, away=away)
    return f"{sentence} {incident}".strip() if incident else sentence
