"""Seat-local deduction, sampling, and action-ranking helpers."""

from __future__ import annotations

from dataclasses import dataclass
import random
from typing import Any

from clue_core.constants import CARD_CATEGORIES, SUSPECTS, WEAPONS, card_category


CASE_FILE_OWNER = "case_file"


@dataclass(slots=True)
class ToolSnapshot:
    envelope_marginals: dict[str, dict[str, float]]
    top_hypotheses: list[dict[str, Any]]
    suggestion_ranking: list[dict[str, Any]]
    accusation: dict[str, Any]
    sample_count: int


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
        self.seat_ids = list(seat_ids)
        self.hand_counts = dict(hand_counts)
        self.perspective_seat_id = perspective_seat_id
        self.owners = [*self.seat_ids, CASE_FILE_OWNER]
        self.possible: dict[str, set[str]] = {}
        for category_cards in CARD_CATEGORIES.values():
            for card_name in category_cards:
                self.possible[card_name] = set(self.owners)
        self._clauses: list[tuple[str, set[str]]] = []
        for card_name in own_hand:
            self.set_owner(card_name, perspective_seat_id)
        self._propagate()

    def set_owner(self, card_name: str, owner: str) -> None:
        self.possible[card_name] = {owner}
        self._propagate()

    def eliminate(self, card_name: str, owner: str) -> None:
        options = self.possible[card_name]
        if owner not in options:
            return
        if len(options) == 1:
            raise ValueError(f"Cannot eliminate the only possible owner for {card_name}.")
        options.remove(owner)
        self._propagate()

    def owner_if_known(self, card_name: str) -> str | None:
        options = self.possible[card_name]
        if len(options) == 1:
            return next(iter(options))
        return None

    def apply_event(self, event: dict[str, Any]) -> None:
        event_type = str(event.get("event_type", ""))
        payload = dict(event.get("payload") or {})
        if event_type == "private_card_shown":
            if str(payload.get("to_seat_id", "")) == self.perspective_seat_id:
                self.set_owner(str(payload["card"]), str(payload["from_seat_id"]))
            return
        if event_type == "refute_passed":
            seat_id = str(payload["seat_id"])
            suggestion = self._latest_suggestion(payload, event)
            if suggestion:
                for card_name in suggestion.values():
                    self.eliminate(card_name, seat_id)
            return
        if event_type == "suggestion_refuted":
            seat_id = str(payload["seat_id"])
            suggestion = self._latest_suggestion(payload, event)
            if suggestion:
                self._clauses.append((seat_id, set(suggestion.values())))
                self._propagate()
            return
        if event_type == "suggestion_unanswered":
            suggestion = dict(payload.get("suggestion") or {})
            for seat_id in self.seat_ids:
                if seat_id == str(payload.get("seat_id", "")):
                    continue
                for card_name in suggestion.values():
                    self.eliminate(card_name, seat_id)

    def apply_events(self, events: list[dict[str, Any]]) -> None:
        latest_suggestion: dict[str, str] | None = None
        for event in events:
            event_type = str(event.get("event_type", ""))
            if event_type == "suggestion_made":
                latest_suggestion = dict(event.get("payload", {}).get("suggestion") or {})
                continue
            if latest_suggestion is not None:
                event = dict(event)
                payload = dict(event.get("payload") or {})
                payload["_latest_suggestion"] = latest_suggestion
                event["payload"] = payload
            self.apply_event(event)

    def marginal_probabilities(self, *, samples: int = 128, seed: int = 0) -> tuple[dict[str, dict[str, float]], list[dict[str, str]]]:
        assignments = self.sample_assignments(samples=samples, seed=seed)
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
            return fallback, []
        return {
            category: {card_name: count / len(assignments) for card_name, count in card_counts.items()}
            for category, card_counts in envelope_counts.items()
        }, assignments

    def sample_assignments(self, *, samples: int = 128, seed: int = 0) -> list[dict[str, str]]:
        rng = random.Random(seed)
        cards = sorted(self.possible.keys(), key=lambda card_name: (len(self.possible[card_name]), card_name))
        results: list[dict[str, str]] = []

        def is_clause_consistent(assignments: dict[str, str]) -> bool:
            for owner, cards_in_clause in self._clauses:
                if any(assignments.get(card_name) == owner for card_name in cards_in_clause):
                    continue
                unresolved = [card_name for card_name in cards_in_clause if card_name not in assignments and owner in self.possible[card_name]]
                if unresolved:
                    continue
                return False
            return True

        def backtrack(index: int, assignments: dict[str, str], seat_loads: dict[str, int], case_file_by_category: dict[str, str]) -> None:
            if len(results) >= samples:
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
        return normalized

    def suggestion_ranking(self, room_name: str, *, samples: int = 128, seed: int = 0) -> list[dict[str, Any]]:
        marginals, assignments = self.marginal_probabilities(samples=samples, seed=seed)
        ranking = []
        for suspect in SUSPECTS:
            for weapon in WEAPONS:
                score = float(marginals["suspect"].get(suspect, 0.0) + marginals["weapon"].get(weapon, 0.0))
                score += float(len(self.possible[suspect]) + len(self.possible[weapon])) * 0.01
                ranking.append(
                    {
                        "suspect": suspect,
                        "weapon": weapon,
                        "room": room_name,
                        "score": round(score, 4),
                    }
                )
        ranking.sort(key=lambda item: (-float(item["score"]), item["suspect"], item["weapon"]))
        return ranking[:10]

    def accusation_recommendation(self, *, samples: int = 128, seed: int = 0) -> dict[str, Any]:
        marginals, assignments = self.marginal_probabilities(samples=samples, seed=seed)
        best = {
            "suspect": max(marginals["suspect"].items(), key=lambda item: item[1])[0],
            "weapon": max(marginals["weapon"].items(), key=lambda item: item[1])[0],
            "room": max(marginals["room"].items(), key=lambda item: item[1])[0],
        }
        confidence = (
            float(marginals["suspect"].get(best["suspect"], 0.0))
            * float(marginals["weapon"].get(best["weapon"], 0.0))
            * float(marginals["room"].get(best["room"], 0.0))
        )
        return {
            "accusation": best,
            "confidence": round(confidence, 4),
            "should_accuse": confidence >= 0.65,
            "sample_count": len(assignments),
        }

    def _latest_suggestion(self, payload: dict[str, Any], event: dict[str, Any]) -> dict[str, str] | None:
        latest = payload.get("_latest_suggestion")
        if latest:
            return dict(latest)
        return None

    def _propagate(self) -> None:
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
) -> ToolSnapshot:
    tracker = ClueBeliefTracker(
        seat_ids=list(hand_counts.keys()),
        hand_counts=hand_counts,
        perspective_seat_id=seat_id,
        own_hand=seat_hand,
    )
    tracker.apply_events(visible_events)
    marginals, assignments = tracker.marginal_probabilities(samples=sample_count, seed=sample_count)
    hypotheses = []
    seen: set[tuple[str, str, str]] = set()
    for sample in assignments:
        case_file = sample["case_file"]
        key = (
            str(case_file.get("suspect", "")),
            str(case_file.get("weapon", "")),
            str(case_file.get("room", "")),
        )
        if key in seen or not all(key):
            continue
        seen.add(key)
        hypotheses.append(
            {
                "suspect": key[0],
                "weapon": key[1],
                "room": key[2],
                "p": round(
                    float(marginals["suspect"].get(key[0], 0.0))
                    * float(marginals["weapon"].get(key[1], 0.0))
                    * float(marginals["room"].get(key[2], 0.0)),
                    4,
                ),
            }
        )
    hypotheses.sort(key=lambda item: (-float(item["p"]), item["suspect"], item["weapon"], item["room"]))
    return ToolSnapshot(
        envelope_marginals=marginals,
        top_hypotheses=hypotheses[:5],
        suggestion_ranking=tracker.suggestion_ranking(room_name, samples=sample_count, seed=sample_count) if room_name else [],
        accusation=tracker.accusation_recommendation(samples=sample_count, seed=sample_count),
        sample_count=len(assignments),
    )
