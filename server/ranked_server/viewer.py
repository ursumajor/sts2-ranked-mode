"""Builds the data the run viewer draws: each act's map, the player's path through it, and per-floor details.

Map sources per act, best first:
  1. map_log sent by the mod (0.4.0+): the real map and visited coordinates, captured during play.
  2. The run JSON's own saved_map / visited_map_coords, which the game only keeps for the final act.
  3. Maps rebuilt from the seed by tools/MapGen and uploaded per window (backfill for older runs).
Without visited coordinates, the path is reconstructed by matching each floor's room type against the map.

Path quality per act: "exact" (recorded), "reconstructed" (only one route fits), "approximate" (several routes
fit, one is shown), "none" (no route fits, e.g. a relic replaced the map mid-run), "no_map".

Rows: the start point (ancient) is row 0, rooms are rows 1..N, the boss is row N+1 (and a second boss N+2).
A path visits one point per row, so history entry k of an act is the point on row k.
"""

from __future__ import annotations

import re
from typing import Any

Coord = tuple[int, int]

_SUFFIXES = ("_ELITE", "_BOSS", "_NORMAL", "_WEAK", "_STRONG", "_EVENT_ENCOUNTER", "_ENCOUNTER")


def pretty(model_id: Any) -> str:
    """'CARD.STRIKE_IRONCLAD' -> 'Strike Ironclad'; 'ENCOUNTER.BYRDONIS_ELITE' -> 'Byrdonis'."""
    if not model_id or not isinstance(model_id, str) or model_id in ("NONE.NONE",):
        return ""
    category, _, entry = model_id.partition(".")
    entry = entry or category
    if category == "ENCOUNTER":
        for s in _SUFFIXES:
            if entry.endswith(s):
                entry = entry[: -len(s)]
                break
    return re.sub(r"\s+", " ", entry.replace("_", " ")).strip().title()


def _loc_key(title: Any) -> str:
    """Event/ancient titles are loc keys like {'key': 'SCROLL_BOXES.title'}."""
    if isinstance(title, dict):
        title = title.get("key", "")
    return pretty(str(title).split(".")[0]) if title else ""


def _coord(c: dict) -> Coord:
    return (int(c["col"]), int(c["row"]))


def _cid(c: Coord) -> str:
    return f"{c[0]},{c[1]}"


class ActMap:
    def __init__(self, saved: dict):
        self.points: dict[Coord, dict] = {}
        for p in saved.get("points") or []:
            self.points[_coord(p["coord"])] = {"type": p.get("type", "unknown"), "children": [_coord(c) for c in p.get("children") or []]}
        self.start = _coord(saved["start"]["coord"])
        self.points[self.start] = {"type": saved["start"].get("type", "ancient"),
                                   "children": [_coord(c) for c in saved["start"].get("children") or []]}
        self.boss = _coord(saved["boss"]["coord"])
        self.points[self.boss] = {"type": "boss", "children": [_coord(c) for c in saved["boss"].get("children") or []]}
        self.second_boss = None
        if saved.get("second_boss"):
            self.second_boss = _coord(saved["second_boss"]["coord"])
            self.points[self.second_boss] = {"type": "boss", "children": []}
            if self.second_boss not in self.points[self.boss]["children"]:
                self.points[self.boss]["children"].append(self.second_boss)
        self.width = int(saved.get("width") or 7)

    def is_chain(self, path: list[Coord]) -> bool:
        if not path or path[0] != self.start:
            return False
        return all(b in self.points.get(a, {}).get("children", []) for a, b in zip(path, path[1:]))

    def match_paths(self, types: list[str], limit: int = 2) -> list[list[Coord]]:
        """Routes from the start whose point types equal `types` row by row (at most `limit`)."""
        found: list[list[Coord]] = []
        if not types:
            return found

        def norm(t: str) -> str:
            return t.replace("_", "").lower()

        want = [norm(t) for t in types]

        def walk(c: Coord, path: list[Coord]) -> None:
            if len(found) >= limit:
                return
            if len(path) == len(want):
                found.append(list(path))
                return
            for child in self.points[c]["children"]:
                if child in self.points and norm(self.points[child]["type"]) == want[len(path)]:
                    path.append(child)
                    walk(child, path)
                    path.pop()

        if norm(self.points[self.start]["type"]) == want[0] or want[0] == "ancient":
            walk(self.start, [self.start])
        return found


def floor_detail(entry: dict) -> dict:
    stats = (entry.get("player_stats") or [{}])[0]
    rooms = entry.get("rooms") or []

    def ids(key):
        return [pretty(x) for x in stats.get(key) or [] if x]

    def cards(key):
        return [pretty(c.get("id")) for c in stats.get(key) or [] if isinstance(c, dict)]

    d: dict[str, Any] = {
        "point_type": entry.get("map_point_type"),
        "rooms": [{"name": pretty(r.get("model_id")), "type": r.get("room_type"), "turns": r.get("turns_taken")} for r in rooms],
        "hp": stats.get("current_hp"),
        "max_hp": stats.get("max_hp"),
        "gold": stats.get("current_gold"),
    }
    for key in ("damage_taken", "hp_healed", "max_hp_gained", "max_hp_lost", "gold_gained", "gold_spent", "gold_lost", "gold_stolen"):
        if stats.get(key):
            d[key] = stats[key]
    lists = {
        "card_choices": [{"name": pretty((c.get("card") or {}).get("id")), "picked": bool(c.get("was_picked"))} for c in stats.get("card_choices") or []],
        "relic_choices": [{"name": pretty(c.get("choice")), "picked": bool(c.get("was_picked"))} for c in stats.get("relic_choices") or []],
        "potion_choices": [{"name": pretty(c.get("choice")), "picked": bool(c.get("was_picked"))} for c in stats.get("potion_choices") or []],
        "ancient_choices": [{"name": _loc_key(c.get("title")) or pretty(c.get("TextKey")), "picked": bool(c.get("was_chosen"))}
                            for c in stats.get("ancient_choice") or []],
        "events": [_loc_key(c.get("title")) for c in stats.get("event_choices") or [] if c.get("title")],
        "rest_site": [str(x).replace("_", " ").title() for x in stats.get("rest_site_choices") or []],
        "cards_gained": cards("cards_gained"),
        "cards_removed": cards("cards_removed"),
        "upgraded": ids("upgraded_cards"),
        "transformed": [{"from": pretty((t.get("original_card") or {}).get("id")), "to": pretty((t.get("final_card") or {}).get("id"))}
                        for t in stats.get("cards_transformed") or []],
        "enchanted": [{"card": pretty((t.get("card") or {}).get("id")), "enchantment": pretty(t.get("enchantment"))}
                      for t in stats.get("cards_enchanted") or []],
        "bought": ids("bought_relics") + ids("bought_potions") + ids("bought_colorless"),
        "potions_used": ids("potion_used"),
        "potions_discarded": ids("potion_discarded"),
        "relics_removed": ids("relics_removed"),
    }
    d.update({k: v for k, v in lists.items() if v})
    return d


CardKey = tuple[str, int]  # (card id, upgrade level)


def _card_key(c: Any) -> CardKey:
    return (str(c.get("id")), int(c.get("current_upgrade_level") or 0)) if isinstance(c, dict) else ("", 0)


def card_name(key: CardKey) -> str:
    return pretty(key[0]) + "+" * key[1]


def _stats(entry: dict) -> dict:
    return (entry.get("player_stats") or [{}])[0]


def deck_timeline(deck: list, history: list[dict]) -> tuple[list[list[str]], int]:
    """The deck going into each floor, rebuilt by undoing each floor's changes backwards from the final deck.

    Returns (decks, exact_from): decks[g] is the sorted card names before floor g's changes. If a floor's
    changes don't match the deck (something the history doesn't log), floors before exact_from are approximate.
    """
    from collections import Counter

    cur = Counter(_card_key(c) for c in deck if isinstance(c, dict))
    out: list[list[str]] = [[] for _ in history]
    exact_from = 0

    def take(key: CardKey, any_upgrade: bool = False) -> bool:
        if cur[key] > 0:
            cur[key] -= 1
            return True
        if any_upgrade:  # e.g. gained, then upgraded on the same floor
            for k in sorted(cur, key=lambda k: k[1]):
                if k[0] == key[0] and cur[k] > 0:
                    cur[k] -= 1
                    return True
        return False

    for g in range(len(history) - 1, -1, -1):
        st = _stats(history[g])
        ok = True
        for c in st.get("cards_removed") or []:  # undo the latest kind of change first
            cur[_card_key(c)] += 1
        for t in st.get("cards_transformed") or []:
            ok &= take(_card_key(t.get("final_card") or {}), any_upgrade=True)
            cur[_card_key(t.get("original_card") or {})] += 1
        for cid in st.get("upgraded_cards") or []:
            ups = [k for k in cur if k[0] == cid and k[1] > 0 and cur[k] > 0]
            if ups:
                k = max(ups, key=lambda k: k[1])
                cur[k] -= 1
                cur[(cid, k[1] - 1)] += 1
            else:
                ok = False
        for c in st.get("cards_gained") or []:
            ok &= take(_card_key(c), any_upgrade=True)
        cur = +cur
        if not ok and not exact_from:
            exact_from = g + 1
        out[g] = sorted(card_name(k) for k in cur.elements())
    return out, exact_from


def relic_timeline(relics: list, history: list[dict]) -> list[list[str]]:
    """The relics held going into each floor, in the order they were gained.

    floor_added_to_deck is 1-based (history index + 1). Starter relics also say floor 1, so a floor-1 relic counts
    as gained at the first floor only if that floor's history says it was picked or bought.
    """
    def gained_at(g: int) -> set[str]:
        st = _stats(history[g]) if 0 <= g < len(history) else {}
        picked = {c.get("choice") for c in st.get("relic_choices") or [] if c.get("was_picked")}
        return picked | set(st.get("bought_relics") or [])

    held: list[tuple[int, int | None, str]] = []  # (gained at, removed at, id); gained at -1 = before the run
    for r in relics:
        if not isinstance(r, dict):
            continue
        g = int(r.get("floor_added_to_deck") or 1) - 1
        if g == 0 and r.get("id") not in gained_at(0):
            g = -1
        held.append((g, None, str(r.get("id"))))
    for j, entry in enumerate(history):
        for rid in _stats(entry).get("relics_removed") or []:
            g = next((k for k in range(j - 1, -1, -1) if rid in gained_at(k)), -1)
            held.append((g, j, str(rid)))
    held.sort(key=lambda h: h[0])
    return [[pretty(rid) for (a, removed, rid) in held if a < g and (removed is None or removed >= g)]
            for g in range(len(history))]


def build_viewer(run: dict, map_log: dict | None, window_maps: dict | None, meta: dict) -> dict:
    """run: the stored SerializableRun JSON. map_log: {"acts": {"0": {"map": saved_map, "visited": [...]}}}.
    window_maps: {"0": {"act_id": ..., "map": saved_map}} from MapGen backfill. meta: header fields."""
    history = run.get("map_point_history") or []
    player = (run.get("players") or [{}])[0]
    flat = [e for act in history for e in act]
    decks, deck_exact_from = deck_timeline(player.get("deck") or [], flat)
    relics_before = relic_timeline(player.get("relics") or [], flat)

    def detail(g: int, entry: dict) -> dict:
        """A floor's details plus the deck and relics going into it."""
        d = floor_detail(entry)
        if g < len(flat):
            d["deck_before"] = decks[g]
            d["relics_before"] = relics_before[g]
            if g < deck_exact_from:
                d["deck_approximate"] = True
        return d

    acts_out = []
    floor_base = 0
    current = int(run.get("current_act_index") or 0)
    logged = (map_log or {}).get("acts") or {}
    for i, act in enumerate(run.get("acts") or []):
        entries = history[i] if i < len(history) else []
        rooms = act.get("rooms") or {}
        out: dict[str, Any] = {
            "index": i + 1,
            "name": pretty(act.get("id")),
            "boss_name": pretty(rooms.get("boss_id")),
            "second_boss_name": pretty(rooms.get("second_boss_id")),
            "ancient_name": pretty(rooms.get("ancient_id")),
            "reached": bool(entries),
        }

        saved, visited = None, None
        log = logged.get(str(i))
        if log and log.get("map"):
            saved, visited = log["map"], log.get("visited")
        elif act.get("saved_map"):
            saved = act["saved_map"]
            if i == current:
                visited = run.get("visited_map_coords")
        elif window_maps and str(i) in window_maps and window_maps[str(i)].get("act_id") == act.get("id"):
            saved = window_maps[str(i)]["map"]

        if saved is None:
            out.update(quality="no_map", nodes=[], floors=[
                {"floor": floor_base + k, "act_floor": k, **detail(floor_base + k, e)} for k, e in enumerate(entries)])
            acts_out.append(out)
            floor_base += len(entries)
            continue

        m = ActMap(saved)
        path: list[Coord] = []
        quality = "none"
        if visited:
            path = [_coord(c) for c in visited]
            quality = "exact" if m.is_chain(path) else "none"
            if quality == "none":
                path = []
        if not path and entries:
            matches = m.match_paths([e.get("map_point_type", "") for e in entries])
            if matches:
                path = matches[0]
                quality = "reconstructed" if len(matches) == 1 else "approximate"
        if not entries:
            quality = "not_reached"

        on_path = {c: k for k, c in enumerate(path)}
        nodes = []
        for c, p in sorted(m.points.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            node: dict[str, Any] = {"id": _cid(c), "col": c[0], "row": c[1], "type": p["type"],
                                    "children": [_cid(k) for k in p["children"] if k in m.points]}
            if c == m.start:
                node["role"] = "start"
            elif c == m.boss:
                node["role"] = "boss"
            elif c == m.second_boss:
                node["role"] = "second_boss"
            k = on_path.get(c)
            if k is not None:
                node["visited"] = True
                node["floor"] = floor_base + k
                node["act_floor"] = k
                if k < len(entries):
                    node["detail"] = detail(floor_base + k, entries[k])
            nodes.append(node)
        out.update(quality=quality, width=m.width, rows=max(c[1] for c in m.points), nodes=nodes,
                   path=[_cid(c) for c in path])
        acts_out.append(out)
        floor_base += len(entries)

    return {
        "run": meta,
        "acts": acts_out,
        "deck": sorted(card_name(_card_key(c)) for c in player.get("deck") or [] if isinstance(c, dict)),
        "relics": [pretty(r.get("id")) for r in player.get("relics") or [] if isinstance(r, dict)],
    }
