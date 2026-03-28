"""Seat-local deduction, sampling, and action-ranking helpers."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
import math
import random
import time
from typing import Any

from clue_core.constants import CARD_CATEGORIES, SUSPECTS, WEAPONS, card_category


CASE_FILE_OWNER = "case_file"


@dataclass(slots=True)
class ToolSnapshot:
    """Seat-local deduction summary exposed to heuristic and LLM policies."""

    envelope_marginals: dict[str, dict[str, float]] = field(default_factory=dict)
    top_hypotheses: list[dict[str, Any]] = field(default_factory=list)
    suggestion_ranking: list[dict[str, Any]] = field(default_factory=list)
    accusation: dict[str, Any] = field(default_factory=dict)
    belief_summary: dict[str, Any] = field(default_factory=dict)
    opponent_model: dict[str, Any] = field(default_factory=dict)
    generation: dict[str, Any] = field(default_factory=dict)
    sample_count: int = 0


class ClueBeliefTracker:
    """Maintain seat-local ownership constraints over cards."""

    def __init__(
        self,
        *,
        seat_ids: list[str],
        hand_counts: dict[str, int],
        perspective_seat_id: str,
        own_hand: list[str],
    ) -> None:
        """Initialize ownership domains, hand quotas, and known private cards."""

        self.seat_ids = list(seat_ids)
        self.hand_counts = dict(hand_counts)
        self.perspective_seat_id = perspective_seat_id
        self.owners = [*self.seat_ids, CASE_FILE_OWNER]
        self.possible: dict[str, set[str]] = {}
        for category_cards in CARD_CATEGORIES.values():
            for card_name in category_cards:
                self.possible[card_name] = set(self.owners)
        self._clauses: list[tuple[str, set[str]]] = []
        self._public_refutations_by_seat = {seat_id: 0 for seat_id in self.seat_ids}
        self._public_passes_by_seat = {seat_id: 0 for seat_id in self.seat_ids}
        self._public_accusations_by_seat = {seat_id: [] for seat_id in self.seat_ids}
        self._seat_suggestion_history = {seat_id: [] for seat_id in self.seat_ids}
        self._recent_suggestions: list[dict[str, Any]] = []
        self._suggestion_outcomes: list[dict[str, Any]] = []
        for card_name in own_hand:
            self.set_owner(card_name, perspective_seat_id)
        self._propagate()

    def set_owner(self, card_name: str, owner: str) -> None:
        """Collapse one card to a single known owner and propagate consequences."""

        self.possible[card_name] = {owner}
        self._propagate()

    def eliminate(self, card_name: str, owner: str) -> None:
        """Remove one owner from the candidate set for a card."""

        options = self.possible[card_name]
        if owner not in options:
            return
        if len(options) == 1:
            raise ValueError(f"Cannot eliminate the only possible owner for {card_name}.")
        options.remove(owner)
        self._propagate()

    def owner_if_known(self, card_name: str) -> str | None:
        """Return the resolved owner when the tracker has only one possibility left."""

        options = self.possible[card_name]
        if len(options) == 1:
            return next(iter(options))
        return None

    def apply_event(self, event: dict[str, Any]) -> None:
        """Update the tracker from one visible public or private gameplay event."""

        event_type = str(event.get("event_type", ""))
        payload = dict(event.get("payload") or {})
        if event_type == "private_card_shown":
            if str(payload.get("to_seat_id", "")) == self.perspective_seat_id:
                self.set_owner(str(payload["card"]), str(payload["from_seat_id"]))
            return
        if event_type == "refute_passed":
            seat_id = str(payload["seat_id"])
            self._public_passes_by_seat[seat_id] = self._public_passes_by_seat.get(seat_id, 0) + 1
            suggestion = self._latest_suggestion(payload)
            if suggestion:
                self._suggestion_outcomes.append({"result": "pass", "seat_id": seat_id, "suggestion": suggestion})
                for card_name in suggestion.values():
                    self.eliminate(card_name, seat_id)
            return
        if event_type == "suggestion_refuted":
            seat_id = str(payload["seat_id"])
            self._public_refutations_by_seat[seat_id] = self._public_refutations_by_seat.get(seat_id, 0) + 1
            suggestion = self._latest_suggestion(payload)
            if suggestion:
                self._suggestion_outcomes.append({"result": "refuted", "seat_id": seat_id, "suggestion": suggestion})
                self._clauses.append((seat_id, set(suggestion.values())))
                self._propagate()
            return
        if event_type == "suggestion_unanswered":
            suggestion = dict(payload.get("suggestion") or {})
            if suggestion:
                self._suggestion_outcomes.append({"result": "unanswered", "suggestion": suggestion})
            for seat_id in self.seat_ids:
                if seat_id == str(payload.get("seat_id", "")):
                    continue
                for card_name in suggestion.values():
                    self.eliminate(card_name, seat_id)
            return
        if event_type == "accusation_made":
            seat_id = str(payload.get("seat_id", ""))
            accusation = dict(payload.get("accusation") or {})
            if seat_id in self._public_accusations_by_seat and accusation:
                self._public_accusations_by_seat[seat_id].append(accusation)

    def apply_events(self, events: list[dict[str, Any]]) -> None:
        """Replay a visible event stream into the tracker in order."""

        latest_suggestion: dict[str, str] | None = None
        for event in events:
            event_type = str(event.get("event_type", ""))
            if event_type == "suggestion_made":
                payload = dict(event.get("payload") or {})
                latest_suggestion = dict(payload.get("suggestion") or {})
                seat_id = str(payload.get("seat_id", ""))
                if seat_id in self._seat_suggestion_history and latest_suggestion:
                    self._seat_suggestion_history[seat_id].append(latest_suggestion)
                    self._recent_suggestions.append({"seat_id": seat_id, "suggestion": latest_suggestion})
                continue
            if latest_suggestion is not None:
                event = dict(event)
                payload = dict(event.get("payload") or {})
                payload["_latest_suggestion"] = latest_suggestion
                event["payload"] = payload
            self.apply_event(event)

    def marginal_probabilities(
        self,
        *,
        samples: int = 128,
        seed: int = 0,
        time_budget_ms: int | None = None,
    ) -> tuple[dict[str, dict[str, float]], list[dict[str, str]], bool]:
        """Estimate envelope marginals by sampling assignments consistent with current knowledge."""

        assignments, timed_out = self.sample_assignments(samples=samples, seed=seed, time_budget_ms=time_budget_ms)
        envelope_counts = {category: {card_name: 0 for card_name in names} for category, names in CARD_CATEGORIES.items()}
        for assignment in assignments:
            for category, card_name in assignment["case_file"].items():
                envelope_counts[category][card_name] += 1
        if not assignments:
            fallback = {}
            for category, names in CARD_CATEGORIES.items():
                case_candidates = [card_name for card_name in names if CASE_FILE_OWNER in self.possible[card_name]]
                total = max(len(case_candidates), 1)
                fallback[category] = {
                    card_name: (1.0 / total if CASE_FILE_OWNER in self.possible[card_name] else 0.0)
                    for card_name in names
                }
            return fallback, [], timed_out
        return {
            category: {card_name: count / len(assignments) for card_name, count in card_counts.items()}
            for category, card_counts in envelope_counts.items()
        }, assignments, timed_out

    def sample_assignments(
        self,
        *,
        samples: int = 128,
        seed: int = 0,
        time_budget_ms: int | None = None,
    ) -> tuple[list[dict[str, str]], bool]:
        """Generate consistent complete ownership assignments with backtracking search."""

        rng = random.Random(seed)
        cards = sorted(self.possible.keys(), key=lambda card_name: (len(self.possible[card_name]), card_name))
        results: list[dict[str, str]] = []
        deadline = time.perf_counter() + (time_budget_ms / 1000.0) if time_budget_ms else None
        timed_out = False

        def is_clause_consistent(assignments: dict[str, str]) -> bool:
            """Check whether each pending refutation clause can still be satisfied."""

            for owner, cards_in_clause in self._clauses:
                if any(assignments.get(card_name) == owner for card_name in cards_in_clause):
                    continue
                unresolved = [card_name for card_name in cards_in_clause if card_name not in assignments and owner in self.possible[card_name]]
                if unresolved:
                    continue
                return False
            return True

        def backtrack(index: int, assignments: dict[str, str], seat_loads: dict[str, int], case_file_by_category: dict[str, str]) -> None:
            """Explore one branch of the consistent-deal search tree."""

            nonlocal timed_out
            if len(results) >= samples or timed_out:
                return
            if deadline is not None and time.perf_counter() >= deadline:
                timed_out = True
                return
            if index >= len(cards):
                if is_clause_consistent(assignments) and len(case_file_by_category) == len(CARD_CATEGORIES):
                    results.append(dict(assignments))
                return
            card_name = cards[index]
            category = card_category(card_name)
            candidates = list(self.possible[card_name])
            rng.shuffle(candidates)
            for owner in candidates:
                if timed_out:
                    return
                if owner == CASE_FILE_OWNER:
                    existing = case_file_by_category.get(category)
                    if existing is not None and existing != card_name:
                        continue
                else:
                    if seat_loads[owner] >= self.hand_counts[owner]:
                        continue
                assignments[card_name] = owner
                if owner == CASE_FILE_OWNER:
                    case_file_by_category[category] = card_name
                else:
                    seat_loads[owner] += 1
                if is_clause_consistent(assignments):
                    backtrack(index + 1, assignments, seat_loads, case_file_by_category)
                if owner == CASE_FILE_OWNER:
                    if case_file_by_category.get(category) == card_name:
                        case_file_by_category.pop(category, None)
                else:
                    seat_loads[owner] -= 1
                assignments.pop(card_name, None)

        backtrack(0, {}, {seat_id: 0 for seat_id in self.seat_ids}, {})
        normalized = []
        for assignment in results:
            case_file = {}
            for card_name, owner in assignment.items():
                if owner == CASE_FILE_OWNER:
                    case_file[card_category(card_name)] = card_name
            normalized.append({"owners": assignment, "case_file": case_file})
        return normalized, timed_out

    def suggestion_ranking(
        self,
        room_name: str,
        *,
        marginals: dict[str, dict[str, float]] | None = None,
        assignments: list[dict[str, Any]] | None = None,
        case_distribution: dict[tuple[str, str, str], float] | None = None,
        samples: int = 128,
        seed: int = 0,
        time_budget_ms: int | None = None,
    ) -> list[dict[str, Any]]:
        """Score suspect/weapon suggestions using information gain and leak-aware penalties."""

        if marginals is None or assignments is None:
            marginals, assignments, _ = self.marginal_probabilities(samples=samples, seed=seed, time_budget_ms=time_budget_ms)
        case_distribution = case_distribution or self.case_file_distribution(marginals=marginals, assignments=assignments)
        prior_entropy = self._entropy(case_distribution.values())
        ranking = []
        for suspect in SUSPECTS:
            for weapon in WEAPONS:
                suggestion = {"suspect": suspect, "weapon": weapon, "room": room_name}
                observation_distribution = self._suggestion_observation_distribution(assignments or [], suggestion)
                if observation_distribution:
                    refuter_distribution = self._refuter_distribution(observation_distribution)
                    expected_posterior_entropy = sum(
                        float(branch["probability"]) * self._entropy(branch["case_distribution"].values())
                        for branch in observation_distribution.values()
                    )
                    information_gain = max(prior_entropy - expected_posterior_entropy, 0.0)
                    unanswered_probability = float(refuter_distribution.get("unanswered", 0.0))
                else:
                    refuter_distribution = self._fallback_refuter_distribution(suggestion)
                    unanswered_probability = float(refuter_distribution.get("unanswered", 0.0))
                    information_gain = 0.0
                    expected_posterior_entropy = prior_entropy
                repeat_penalty = self._repeat_penalty(suggestion)
                leak_penalty = self._opponent_leak_penalty(refuter_distribution)
                case_bias = float(marginals["suspect"].get(suspect, 0.0) + marginals["weapon"].get(weapon, 0.0))
                score = (information_gain * 1.8) + (unanswered_probability * 0.4) + (case_bias * 0.18) - repeat_penalty - leak_penalty
                likely_refuter = max(refuter_distribution.items(), key=lambda item: item[1])[0] if refuter_distribution else "unanswered"
                ranking.append(
                    {
                        "suspect": suspect,
                        "weapon": weapon,
                        "room": room_name,
                        "score": round(score, 4),
                        "expected_information_gain": round(information_gain, 4),
                        "expected_posterior_entropy": round(expected_posterior_entropy, 4),
                        "prior_entropy": round(prior_entropy, 4),
                        "case_bias": round(case_bias, 4),
                        "unanswered_probability": round(unanswered_probability, 4),
                        "repeat_penalty": round(repeat_penalty, 4),
                        "opponent_leak_penalty": round(leak_penalty, 4),
                        "likely_refuter": likely_refuter,
                        "refuter_distribution": {key: round(float(value), 4) for key, value in refuter_distribution.items()},
                        "why": (
                            f"IG {information_gain:.3f} bits, unanswered {unanswered_probability:.2f}, "
                            f"repeat {repeat_penalty:.2f}, leak {leak_penalty:.2f}."
                        ),
                    }
                )
        ranking.sort(
            key=lambda item: (
                -float(item["score"]),
                -float(item["expected_information_gain"]),
                item["suspect"],
                item["weapon"],
            )
        )
        return ranking[:10]

    def accusation_recommendation(
        self,
        *,
        case_distribution: dict[tuple[str, str, str], float] | None = None,
        marginals: dict[str, dict[str, float]] | None = None,
        assignments: list[dict[str, Any]] | None = None,
        samples: int = 128,
        seed: int = 0,
        time_budget_ms: int | None = None,
    ) -> dict[str, Any]:
        """Return the highest-probability accusation plus confidence and entropy detail."""

        if marginals is None or assignments is None:
            marginals, assignments, _ = self.marginal_probabilities(samples=samples, seed=seed, time_budget_ms=time_budget_ms)
        case_distribution = case_distribution or self.case_file_distribution(marginals=marginals, assignments=assignments)
        ranked = sorted(case_distribution.items(), key=lambda item: (-float(item[1]), *item[0]))
        if not ranked:
            return {
                "accusation": {},
                "confidence": 0.0,
                "confidence_gap": 0.0,
                "entropy_bits": 0.0,
                "should_accuse": False,
                "sample_count": 0,
                "top_alternatives": [],
            }
        best_key, best_probability = ranked[0]
        second_probability = float(ranked[1][1]) if len(ranked) > 1 else 0.0
        accusation = {"suspect": best_key[0], "weapon": best_key[1], "room": best_key[2]}
        return {
            "accusation": accusation,
            "confidence": round(float(best_probability), 4),
            "confidence_gap": round(float(best_probability) - second_probability, 4),
            "entropy_bits": round(self._entropy(case_distribution.values()), 4),
            "should_accuse": float(best_probability) >= 0.65,
            "sample_count": len(assignments or []),
            "top_alternatives": [
                {
                    "suspect": key[0],
                    "weapon": key[1],
                    "room": key[2],
                    "p": round(float(probability), 4),
                }
                for key, probability in ranked[1:4]
            ],
        }

    def case_file_distribution(
        self,
        *,
        marginals: dict[str, dict[str, float]],
        assignments: list[dict[str, Any]],
    ) -> dict[tuple[str, str, str], float]:
        """Build a normalized joint case-file distribution from samples or marginals."""

        if assignments:
            counts = Counter(
                (
                    str(sample["case_file"].get("suspect", "")),
                    str(sample["case_file"].get("weapon", "")),
                    str(sample["case_file"].get("room", "")),
                )
                for sample in assignments
                if sample["case_file"]
            )
            total = float(sum(counts.values()) or 1.0)
            return {key: count / total for key, count in counts.items() if all(key)}
        weights: dict[tuple[str, str, str], float] = {}
        total = 0.0
        for suspect in SUSPECTS:
            for weapon in WEAPONS:
                for room in CARD_CATEGORIES["room"]:
                    probability = (
                        float(marginals["suspect"].get(suspect, 0.0))
                        * float(marginals["weapon"].get(weapon, 0.0))
                        * float(marginals["room"].get(room, 0.0))
                    )
                    if probability <= 0.0:
                        continue
                    key = (suspect, weapon, room)
                    weights[key] = probability
                    total += probability
        total = total or 1.0
        return {key: value / total for key, value in weights.items()}

    def top_hypotheses(self, case_distribution: dict[tuple[str, str, str], float], *, limit: int = 5) -> list[dict[str, Any]]:
        """Return the highest-probability case-file hypotheses."""

        ranked = sorted(case_distribution.items(), key=lambda item: (-float(item[1]), *item[0]))
        return [
            {
                "suspect": key[0],
                "weapon": key[1],
                "room": key[2],
                "p": round(float(probability), 4),
            }
            for key, probability in ranked[:limit]
        ]

    def belief_summary(
        self,
        *,
        marginals: dict[str, dict[str, float]],
        case_distribution: dict[tuple[str, str, str], float],
        sample_target: int,
        sample_count: int,
        timed_out: bool,
    ) -> dict[str, Any]:
        """Summarize entropy and ownership state for debugging and evaluation."""

        category_entropy = {
            category: round(self._entropy(card_probs.values()), 4)
            for category, card_probs in marginals.items()
        }
        resolved_cards = sum(1 for owners in self.possible.values() if len(owners) == 1)
        unresolved_cards = len(self.possible) - resolved_cards
        return {
            "category_entropy_bits": category_entropy,
            "joint_case_entropy_bits": round(self._entropy(case_distribution.values()), 4),
            "resolved_cards": resolved_cards,
            "unresolved_cards": unresolved_cards,
            "case_file_candidate_counts": {
                category: sum(1 for card_name in names if CASE_FILE_OWNER in self.possible[card_name])
                for category, names in CARD_CATEGORIES.items()
            },
            "sampling": {
                "sample_target": int(sample_target),
                "sample_count": int(sample_count),
                "timed_out": bool(timed_out),
            },
        }

    def opponent_model(self) -> dict[str, Any]:
        """Summarize public-history hooks that can shape leak-aware ranking."""

        recent_pairs = [
            f"{item['suggestion']['suspect']} / {item['suggestion']['weapon']}"
            for item in self._recent_suggestions[-6:]
            if item.get("suggestion")
        ]
        perspective_pairs = [
            f"{item['suspect']} / {item['weapon']}"
            for item in self._seat_suggestion_history.get(self.perspective_seat_id, [])[-4:]
        ]
        return {
            "perspective_recent_pairs": perspective_pairs,
            "recent_public_pairs": recent_pairs,
            "recent_outcomes": self._suggestion_outcomes[-6:],
            "seats": {
                seat_id: {
                    "public_refutations_made": int(self._public_refutations_by_seat.get(seat_id, 0)),
                    "public_refute_passes": int(self._public_passes_by_seat.get(seat_id, 0)),
                    "recent_suggestions": self._seat_suggestion_history.get(seat_id, [])[-3:],
                    "public_accusations": self._public_accusations_by_seat.get(seat_id, [])[-2:],
                }
                for seat_id in self.seat_ids
            },
        }

    def _suggestion_observation_distribution(
        self,
        assignments: list[dict[str, Any]],
        suggestion: dict[str, str],
    ) -> dict[tuple[str, str, str], dict[str, Any]]:
        """Estimate observation branches for one suggestion from consistent deals."""

        if not assignments:
            return {}
        base_weight = 1.0 / len(assignments)
        order = self._refute_order()
        branches: dict[tuple[str, str, str], dict[str, Any]] = {}
        for sample in assignments:
            owners = sample["owners"]
            case_key = (
                str(sample["case_file"].get("suspect", "")),
                str(sample["case_file"].get("weapon", "")),
                str(sample["case_file"].get("room", "")),
            )
            if not all(case_key):
                continue
            refuter = ""
            refute_cards: list[str] = []
            for seat_id in order:
                refute_cards = sorted([card_name for card_name in suggestion.values() if owners.get(card_name) == seat_id])
                if refute_cards:
                    refuter = seat_id
                    break
            if not refuter:
                key = ("unanswered", "unanswered", "")
                branch = branches.setdefault(key, {"probability": 0.0, "case_distribution": defaultdict(float)})
                branch["probability"] += base_weight
                branch["case_distribution"][case_key] += base_weight
                continue
            share = base_weight / len(refute_cards)
            for card_name in refute_cards:
                key = ("refuted", refuter, card_name)
                branch = branches.setdefault(key, {"probability": 0.0, "case_distribution": defaultdict(float)})
                branch["probability"] += share
                branch["case_distribution"][case_key] += share
        for branch in branches.values():
            total = float(sum(branch["case_distribution"].values()) or 1.0)
            branch["case_distribution"] = {
                key: float(weight) / total
                for key, weight in branch["case_distribution"].items()
            }
        return branches

    def _refuter_distribution(self, observation_distribution: dict[tuple[str, str, str], dict[str, Any]]) -> dict[str, float]:
        """Collapse observation branches into who likely answers the suggestion."""

        refuter_distribution: defaultdict[str, float] = defaultdict(float)
        for (_, refuter, _), branch in observation_distribution.items():
            refuter_distribution[refuter] += float(branch["probability"])
        return dict(refuter_distribution)

    def _fallback_refuter_distribution(self, suggestion: dict[str, str]) -> dict[str, float]:
        """Approximate who might refute when no concrete assignment samples are available."""

        likelihoods: defaultdict[str, float] = defaultdict(float)
        for owner in self._refute_order():
            likelihood = max(float(self._owner_probability(card_name, owner)) for card_name in suggestion.values())
            if likelihood > 0.0:
                likelihoods[owner] = likelihood
        unanswered = max(
            0.0,
            1.0 - max(float(self._owner_probability(card_name, owner)) for owner in self.seat_ids for card_name in suggestion.values()),
        )
        if unanswered > 0.0:
            likelihoods["unanswered"] = unanswered
        total = float(sum(likelihoods.values()) or 1.0)
        return {seat_id: value / total for seat_id, value in likelihoods.items()}

    def _owner_probability(self, card_name: str, owner: str) -> float:
        """Estimate one owner's chance for a card from the remaining ownership domain."""

        options = self.possible[card_name]
        if owner not in options:
            return 0.0
        return 1.0 / len(options)

    def _repeat_penalty(self, suggestion: dict[str, str]) -> float:
        """Penalize recently repeated public suggestions to reduce local loops."""

        penalty = 0.0
        recent_self = list(self._seat_suggestion_history.get(self.perspective_seat_id, [])[-4:])
        for index, prior in enumerate(reversed(recent_self), start=1):
            decay = 1.0 / index
            if prior == suggestion:
                penalty += 0.45 * decay
            elif prior.get("suspect") == suggestion["suspect"] and prior.get("weapon") == suggestion["weapon"]:
                penalty += 0.3 * decay
            elif prior.get("suspect") == suggestion["suspect"] or prior.get("weapon") == suggestion["weapon"]:
                penalty += 0.12 * decay
        recent_public = list(self._recent_suggestions[-8:])
        for index, prior in enumerate(reversed(recent_public), start=1):
            other_suggestion = dict(prior.get("suggestion") or {})
            if not other_suggestion:
                continue
            decay = 1.0 / (index + 1)
            if other_suggestion.get("suspect") == suggestion["suspect"] and other_suggestion.get("weapon") == suggestion["weapon"]:
                penalty += 0.08 * decay
        return penalty

    def _opponent_leak_penalty(self, refuter_distribution: dict[str, float]) -> float:
        """Penalize suggestions that most likely feed the same information-rich opponents."""

        penalty = 0.0
        for seat_id, probability in refuter_distribution.items():
            if seat_id == "unanswered":
                continue
            public_refutations = float(self._public_refutations_by_seat.get(seat_id, 0))
            recent_suggestions = float(len(self._seat_suggestion_history.get(seat_id, [])))
            seat_pressure = 0.08 + (public_refutations * 0.025) + (recent_suggestions * 0.01)
            penalty += float(probability) * seat_pressure
        return penalty

    def _refute_order(self) -> list[str]:
        """Return the seat order that would be used to search for a refuter."""

        start = self.seat_ids.index(self.perspective_seat_id)
        return [self.seat_ids[(start + offset) % len(self.seat_ids)] for offset in range(1, len(self.seat_ids))]

    @staticmethod
    def _entropy(probabilities: Any) -> float:
        """Compute Shannon entropy in bits for any probability iterable."""

        values = [float(value) for value in probabilities if float(value) > 0.0]
        if not values:
            return 0.0
        total = sum(values)
        if total <= 0.0:
            return 0.0
        normalized = [value / total for value in values]
        return -sum(value * math.log2(value) for value in normalized if value > 0.0)

    def _latest_suggestion(self, payload: dict[str, Any]) -> dict[str, str] | None:
        """Read the most recent suggestion cached onto a downstream refutation event."""

        latest = payload.get("_latest_suggestion")
        if latest:
            return dict(latest)
        return None

    def _propagate(self) -> None:
        """Enforce simple ownership, quota, and refutation-clause constraints until stable."""

        changed = True
        while changed:
            changed = False
            for card_name, owners in list(self.possible.items()):
                if not owners:
                    raise ValueError(f"No possible owners remain for {card_name}.")
                if len(owners) == 1:
                    owner = next(iter(owners))
                    for other_card, other_owners in self.possible.items():
                        if other_card == card_name or owner not in other_owners:
                            continue
                        if owner == CASE_FILE_OWNER:
                            if card_category(other_card) == card_category(card_name) and len(other_owners) > 1:
                                other_owners.remove(owner)
                                changed = True
                        elif len(other_owners) > 1:
                            pass
            for category, cards in CARD_CATEGORIES.items():
                case_candidates = [card_name for card_name in cards if CASE_FILE_OWNER in self.possible[card_name]]
                if len(case_candidates) == 1 and len(self.possible[case_candidates[0]]) > 1:
                    self.possible[case_candidates[0]] = {CASE_FILE_OWNER}
                    changed = True
            for seat_id, quota in self.hand_counts.items():
                fixed_cards = [card_name for card_name, owners in self.possible.items() if owners == {seat_id}]
                if len(fixed_cards) > quota:
                    raise ValueError(f"Seat {seat_id} exceeds its hand quota.")
                if len(fixed_cards) == quota:
                    for card_name, owners in self.possible.items():
                        if owners != {seat_id} and seat_id in owners and len(owners) > 1:
                            owners.remove(seat_id)
                            changed = True
                remaining_cards = [card_name for card_name, owners in self.possible.items() if seat_id in owners]
                if len(remaining_cards) == quota:
                    for card_name in remaining_cards:
                        if self.possible[card_name] != {seat_id}:
                            self.possible[card_name] = {seat_id}
                            changed = True
            for owner, cards_in_clause in list(self._clauses):
                if any(self.possible[card_name] == {owner} for card_name in cards_in_clause):
                    self._clauses.remove((owner, cards_in_clause))
                    changed = True
                    continue
                feasible = [card_name for card_name in cards_in_clause if owner in self.possible[card_name]]
                if not feasible:
                    raise ValueError(f"Clause for {owner} became impossible.")
                if len(feasible) == 1 and self.possible[feasible[0]] != {owner}:
                    self.possible[feasible[0]] = {owner}
                    changed = True


def build_tool_snapshot(
    *,
    seat_id: str,
    seat_hand: list[str],
    hand_counts: dict[str, int],
    visible_events: list[dict[str, Any]],
    room_name: str | None,
    sample_count: int = 128,
    time_budget_ms: int | None = None,
) -> ToolSnapshot:
    """Build the tool payload consumed by autonomous seat policies for one turn."""

    started = time.perf_counter()
    tracker = ClueBeliefTracker(
        seat_ids=list(hand_counts.keys()),
        hand_counts=hand_counts,
        perspective_seat_id=seat_id,
        own_hand=seat_hand,
    )
    tracker.apply_events(visible_events)
    marginals, assignments, timed_out = tracker.marginal_probabilities(
        samples=sample_count,
        seed=sample_count,
        time_budget_ms=time_budget_ms,
    )
    case_distribution = tracker.case_file_distribution(marginals=marginals, assignments=assignments)
    suggestion_ranking = (
        tracker.suggestion_ranking(
            room_name,
            marginals=marginals,
            assignments=assignments,
            case_distribution=case_distribution,
        )
        if room_name
        else []
    )
    belief_summary = tracker.belief_summary(
        marginals=marginals,
        case_distribution=case_distribution,
        sample_target=sample_count,
        sample_count=len(assignments),
        timed_out=timed_out,
    )
    if suggestion_ranking:
        belief_summary["top_suggestion_why"] = suggestion_ranking[0]["why"]
    return ToolSnapshot(
        envelope_marginals=marginals,
        top_hypotheses=tracker.top_hypotheses(case_distribution),
        suggestion_ranking=suggestion_ranking,
        accusation=tracker.accusation_recommendation(
            case_distribution=case_distribution,
            marginals=marginals,
            assignments=assignments,
        ),
        belief_summary=belief_summary,
        opponent_model=tracker.opponent_model(),
        generation={
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 2),
            "sample_target": int(sample_count),
            "sample_count": len(assignments),
            "sampling_timed_out": bool(timed_out),
            "room_name": room_name,
        },
        sample_count=len(assignments),
    )
