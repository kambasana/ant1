"""Intelligence, Surveillance, Reconnaissance (ISR) system.

Graduates enemy contacts through NATO-style intelligence levels
based on observation persistence, data quality, and time decay.

Contact Intelligence Levels:
  0 PENDING   — Anomaly in fog data, unconfirmed
  1 DETECTED  — Something seen briefly, position only
  2 TRACKED   — Observed multiple turns, position + movement vector
  3 CLASSIFIED — Unit category known (infantry/vehicle/building)
  4 IDENTIFIED — Full data: specific type, health, stance, capabilities

Contacts persist in the database even when they leave line of sight.
Intelligence degrades over time without observation (IDENTIFIED → TRACKED
after N turns without visual). Destroyed contacts are marked KIA.

The system consumes OpenRA-RL's visible_enemies and spatial tensor
fog channel to produce a unified intelligence picture.
"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional

from openra_env.models import (
    BuildingInfoModel,
    OpenRAObservation,
    UnitInfoModel,
)


class IntelLevel(IntEnum):
    PENDING = 0
    DETECTED = 1
    TRACKED = 2
    CLASSIFIED = 3
    IDENTIFIED = 4


class ContactType(str):
    UNKNOWN = "unknown"
    INFANTRY = "infantry"
    VEHICLE = "vehicle"
    BUILDING = "building"
    AIRCRAFT = "aircraft"
    NAVAL = "naval"


INFANTRY_TYPES = frozenset({
    "e1", "e2", "e3", "e4", "e6", "e7", "spy", "medi", "dog",
    "shok", "thf", "gnrl", "chan", "einstein",
})

VEHICLE_TYPES = frozenset({
    "1tnk", "2tnk", "3tnk", "4tnk", "arty", "v2rl", "harv",
    "apc", "jeep", "mnly", "mgg", "ttnk", "ctnk", "mrj",
    "truk", "dtrk", "stnk", "qtnk",
})

NAVAL_TYPES = frozenset({
    "ss", "msub", "dd", "ca", "pt", "lst",
})

AIRCRAFT_TYPES = frozenset({
    "heli", "hind", "mig", "yak", "tran", "badr", "u2",
})


def classify_unit_type(type_code: str) -> str:
    """Determine broad category from unit type code."""
    t = type_code.lower()
    if t in INFANTRY_TYPES:
        return ContactType.INFANTRY
    if t in VEHICLE_TYPES:
        return ContactType.VEHICLE
    if t in NAVAL_TYPES:
        return ContactType.NAVAL
    if t in AIRCRAFT_TYPES:
        return ContactType.AIRCRAFT
    return ContactType.UNKNOWN


@dataclass
class Contact:
    """A tracked enemy contact in the intelligence database."""

    contact_id: str
    actor_id: int
    intel_level: IntelLevel = IntelLevel.PENDING

    # Position (last known)
    cell_x: int = 0
    cell_y: int = 0
    pos_x: int = 0
    pos_y: int = 0

    # Movement tracking
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    predicted_x: int = 0
    predicted_y: int = 0

    # Classification
    category: str = ContactType.UNKNOWN
    unit_type: str = ""
    is_building: bool = False

    # Identification (full data, only at IDENTIFIED level)
    hp_percent: float = 0.0
    stance: int = -1
    speed: int = 0
    attack_range: int = 0
    can_attack: bool = False
    current_activity: str = ""
    facing: int = 0
    experience_level: int = 0

    # Tracking metadata
    first_seen_turn: int = 0
    last_seen_turn: int = 0
    turns_observed: int = 0
    turns_since_seen: int = 0
    is_visible: bool = False
    is_destroyed: bool = False

    # Position history for movement analysis
    position_history: list = field(default_factory=list)

    @property
    def intel_label(self) -> str:
        return IntelLevel(self.intel_level).name

    @property
    def is_stale(self) -> bool:
        return self.turns_since_seen > 5

    @property
    def confidence(self) -> float:
        """0.0-1.0 confidence based on intel level and staleness."""
        base = self.intel_level / 4.0
        decay = min(self.turns_since_seen * 0.1, 0.6)
        return max(0.0, base - decay)


@dataclass
class IntelSummary:
    """Aggregated intelligence picture."""

    total_contacts: int = 0
    active_contacts: int = 0
    stale_contacts: int = 0
    destroyed_contacts: int = 0

    by_level: dict = field(default_factory=lambda: {
        "PENDING": 0, "DETECTED": 0, "TRACKED": 0,
        "CLASSIFIED": 0, "IDENTIFIED": 0,
    })
    by_category: dict = field(default_factory=lambda: {
        "infantry": 0, "vehicle": 0, "building": 0,
        "aircraft": 0, "naval": 0, "unknown": 0,
    })

    threat_bearing: Optional[tuple[int, int]] = None
    estimated_enemy_strength: int = 0


class ISRManager:
    """Manages the intelligence picture across WEGO turns.

    Consumes observation data each turn to:
      1. Detect new contacts
      2. Update existing contacts with fresh data
      3. Graduate intel levels based on observation persistence
      4. Degrade stale contacts that haven't been seen
      5. Mark destroyed contacts
      6. Predict positions for unobserved contacts

    Args:
        turns_to_track: Consecutive observations to reach TRACKED.
        turns_to_classify: Observations to reach CLASSIFIED.
        turns_to_identify: Observations to reach IDENTIFIED.
        decay_rate: Turns without observation before intel degrades.
        max_stale_turns: Turns before a contact is considered lost.
    """

    def __init__(
        self,
        turns_to_track: int = 2,
        turns_to_classify: int = 1,
        turns_to_identify: int = 3,
        decay_rate: int = 3,
        max_stale_turns: int = 15,
    ):
        self._contacts: dict[int, Contact] = {}
        self._next_contact_id = 1
        self._current_turn = 0
        self._turns_to_track = turns_to_track
        self._turns_to_classify = turns_to_classify
        self._turns_to_identify = turns_to_identify
        self._decay_rate = decay_rate
        self._max_stale = max_stale_turns

        # Base position for threat bearing calculation
        self._base_x = 0
        self._base_y = 0

    @property
    def contacts(self) -> dict[int, Contact]:
        return dict(self._contacts)

    @property
    def active_contacts(self) -> list[Contact]:
        return [c for c in self._contacts.values()
                if not c.is_destroyed and not c.is_stale]

    @property
    def all_known_contacts(self) -> list[Contact]:
        return [c for c in self._contacts.values()
                if not c.is_destroyed]

    def process_observation(
        self,
        obs: OpenRAObservation,
        turn_number: int,
    ) -> IntelSummary:
        """Process a new observation and update the intelligence picture.

        Call this once per WEGO turn during the review phase.

        Args:
            obs: Current game observation.
            turn_number: Current WEGO turn number.

        Returns:
            Updated intelligence summary.
        """
        self._current_turn = turn_number
        self._update_base_position(obs)

        # Track which actor_ids are currently visible
        visible_ids = set()

        # Process visible enemy units
        for enemy in obs.visible_enemies:
            visible_ids.add(enemy.actor_id)
            self._process_enemy_unit(enemy, turn_number)

        # Process visible enemy buildings
        for bldg in obs.visible_enemy_buildings:
            visible_ids.add(bldg.actor_id)
            self._process_enemy_building(bldg, turn_number)

        # Update contacts that are NOT currently visible
        for actor_id, contact in self._contacts.items():
            if actor_id not in visible_ids and not contact.is_destroyed:
                contact.is_visible = False
                contact.turns_since_seen = turn_number - contact.last_seen_turn
                self._apply_decay(contact)
                self._predict_position(contact)

        # Detect destroyed contacts (were visible last turn, now gone,
        # and we can still see their last position)
        for actor_id, contact in self._contacts.items():
            if (contact.is_visible and
                actor_id not in visible_ids and
                not contact.is_building and
                contact.turns_since_seen == 0):
                # Was visible, now gone — could be destroyed or moved out of sight
                # Mark as not visible; if area is still in LOS, likely destroyed
                contact.is_visible = False
                contact.turns_since_seen = 1

        return self.get_summary()

    def _process_enemy_unit(self, enemy: UnitInfoModel, turn: int) -> Contact:
        """Update or create a contact from a visible enemy unit."""
        contact = self._contacts.get(enemy.actor_id)

        if contact is None:
            contact = Contact(
                contact_id=f"C{self._next_contact_id:04d}",
                actor_id=enemy.actor_id,
                intel_level=IntelLevel.DETECTED,
                first_seen_turn=turn,
            )
            self._next_contact_id += 1
            self._contacts[enemy.actor_id] = contact

        # Update position
        prev_x, prev_y = contact.cell_x, contact.cell_y
        contact.cell_x = enemy.cell_x
        contact.cell_y = enemy.cell_y
        contact.pos_x = enemy.pos_x
        contact.pos_y = enemy.pos_y

        # Compute velocity if we have previous position
        if contact.turns_observed > 0 and (prev_x != 0 or prev_y != 0):
            contact.velocity_x = enemy.cell_x - prev_x
            contact.velocity_y = enemy.cell_y - prev_y

        # Position history (keep last 10)
        contact.position_history.append((enemy.cell_x, enemy.cell_y, turn))
        if len(contact.position_history) > 10:
            contact.position_history = contact.position_history[-10:]

        # Update tracking metadata
        contact.last_seen_turn = turn
        contact.turns_observed += 1
        contact.turns_since_seen = 0
        contact.is_visible = True
        contact.is_building = False

        # Store full identification data
        contact.unit_type = enemy.type
        contact.hp_percent = enemy.hp_percent
        contact.stance = enemy.stance
        contact.speed = enemy.speed
        contact.attack_range = enemy.attack_range
        contact.can_attack = enemy.can_attack
        contact.current_activity = enemy.current_activity
        contact.facing = enemy.facing
        contact.experience_level = enemy.experience_level

        # Classify
        contact.category = classify_unit_type(enemy.type)

        # Graduate intel level
        self._graduate(contact)

        return contact

    def _process_enemy_building(self, bldg: BuildingInfoModel, turn: int) -> Contact:
        """Update or create a contact from a visible enemy building."""
        contact = self._contacts.get(bldg.actor_id)

        if contact is None:
            contact = Contact(
                contact_id=f"C{self._next_contact_id:04d}",
                actor_id=bldg.actor_id,
                intel_level=IntelLevel.DETECTED,
                first_seen_turn=turn,
                is_building=True,
            )
            self._next_contact_id += 1
            self._contacts[bldg.actor_id] = contact

        contact.cell_x = bldg.cell_x
        contact.cell_y = bldg.cell_y
        contact.pos_x = bldg.pos_x
        contact.pos_y = bldg.pos_y
        contact.last_seen_turn = turn
        contact.turns_observed += 1
        contact.turns_since_seen = 0
        contact.is_visible = True
        contact.is_building = True

        contact.unit_type = bldg.type
        contact.hp_percent = bldg.hp_percent
        contact.category = ContactType.BUILDING

        # Buildings are immediately classifiable
        self._graduate(contact)

        return contact

    def _graduate(self, contact: Contact) -> None:
        """Promote intel level based on observation data."""
        obs_count = contact.turns_observed

        if contact.intel_level < IntelLevel.DETECTED:
            contact.intel_level = IntelLevel.DETECTED

        if (obs_count >= self._turns_to_track and
                contact.intel_level < IntelLevel.TRACKED):
            contact.intel_level = IntelLevel.TRACKED

        if (contact.category != ContactType.UNKNOWN and
                obs_count >= self._turns_to_classify and
                contact.intel_level < IntelLevel.CLASSIFIED):
            contact.intel_level = IntelLevel.CLASSIFIED

        if (contact.unit_type and
                contact.hp_percent > 0 and
                obs_count >= self._turns_to_identify and
                contact.intel_level < IntelLevel.IDENTIFIED):
            contact.intel_level = IntelLevel.IDENTIFIED

    def _apply_decay(self, contact: Contact) -> None:
        """Degrade intel level for contacts not currently observed."""
        if contact.turns_since_seen <= 0:
            return

        decay_steps = contact.turns_since_seen // self._decay_rate
        if decay_steps > 0:
            new_level = max(
                IntelLevel.DETECTED,
                contact.intel_level - decay_steps,
            )
            contact.intel_level = IntelLevel(new_level)

        if contact.turns_since_seen >= self._max_stale:
            pass  # Contact stays in DB but marked stale via property

    def _predict_position(self, contact: Contact) -> None:
        """Extrapolate position for unobserved mobile contacts."""
        if contact.is_building:
            contact.predicted_x = contact.cell_x
            contact.predicted_y = contact.cell_y
            return

        dt = contact.turns_since_seen
        contact.predicted_x = int(contact.cell_x + contact.velocity_x * dt)
        contact.predicted_y = int(contact.cell_y + contact.velocity_y * dt)

    def _update_base_position(self, obs: OpenRAObservation) -> None:
        """Update friendly base centroid for threat bearing calc."""
        positions = []
        for b in obs.buildings:
            positions.append((b.cell_x, b.cell_y))
        for u in obs.units:
            positions.append((u.cell_x, u.cell_y))
        if positions:
            self._base_x = sum(p[0] for p in positions) // len(positions)
            self._base_y = sum(p[1] for p in positions) // len(positions)

    def mark_destroyed(self, actor_id: int) -> None:
        """Manually mark a contact as destroyed (KIA)."""
        if actor_id in self._contacts:
            self._contacts[actor_id].is_destroyed = True

    def get_contact(self, actor_id: int) -> Optional[Contact]:
        return self._contacts.get(actor_id)

    def get_contacts_by_level(self, level: IntelLevel) -> list[Contact]:
        return [c for c in self._contacts.values()
                if c.intel_level >= level and not c.is_destroyed]

    def get_contacts_in_area(
        self, cx: int, cy: int, radius: int
    ) -> list[Contact]:
        """Get all contacts within radius cells of a point."""
        r2 = radius * radius
        return [
            c for c in self._contacts.values()
            if not c.is_destroyed and
            (c.cell_x - cx) ** 2 + (c.cell_y - cy) ** 2 <= r2
        ]

    def get_threat_axis(self) -> Optional[tuple[int, int]]:
        """Compute bearing to center of mass of known enemy positions."""
        enemies = [c for c in self._contacts.values()
                   if not c.is_destroyed and not c.is_stale]
        if not enemies:
            return None
        avg_x = sum(c.cell_x for c in enemies) // len(enemies)
        avg_y = sum(c.cell_y for c in enemies) // len(enemies)
        return (avg_x - self._base_x, avg_y - self._base_y)

    def get_summary(self) -> IntelSummary:
        """Generate aggregated intelligence summary."""
        summary = IntelSummary()
        summary.total_contacts = len(self._contacts)

        for contact in self._contacts.values():
            if contact.is_destroyed:
                summary.destroyed_contacts += 1
                continue

            if contact.is_stale:
                summary.stale_contacts += 1
            else:
                summary.active_contacts += 1

            summary.by_level[contact.intel_label] = (
                summary.by_level.get(contact.intel_label, 0) + 1
            )
            summary.by_category[contact.category] = (
                summary.by_category.get(contact.category, 0) + 1
            )

        # Estimate enemy strength from identified contacts
        for c in self._contacts.values():
            if not c.is_destroyed and c.intel_level >= IntelLevel.CLASSIFIED:
                if c.category == ContactType.VEHICLE:
                    summary.estimated_enemy_strength += 800
                elif c.category == ContactType.INFANTRY:
                    summary.estimated_enemy_strength += 100
                elif c.category == ContactType.BUILDING:
                    summary.estimated_enemy_strength += 500

        summary.threat_bearing = self.get_threat_axis()
        return summary

    def format_intel_report(self) -> str:
        """Format the intelligence picture as a text report."""
        summary = self.get_summary()
        lines = [
            f"=== INTELLIGENCE REPORT — Turn {self._current_turn} ===",
            f"Contacts: {summary.active_contacts} active, "
            f"{summary.stale_contacts} stale, "
            f"{summary.destroyed_contacts} KIA",
            "",
        ]

        # By intel level
        lines.append("BY INTELLIGENCE LEVEL:")
        for level_name, count in summary.by_level.items():
            if count > 0:
                lines.append(f"  {level_name:12s}: {count}")

        # By category
        lines.append("\nBY CATEGORY:")
        for cat, count in summary.by_category.items():
            if count > 0:
                lines.append(f"  {cat:12s}: {count}")

        lines.append(f"\nESTIMATED ENEMY STRENGTH: ${summary.estimated_enemy_strength}")

        if summary.threat_bearing:
            dx, dy = summary.threat_bearing
            lines.append(f"THREAT AXIS: ({dx:+d}, {dy:+d}) from base")

        # Contact details (identified first)
        identified = sorted(
            [c for c in self._contacts.values() if not c.is_destroyed],
            key=lambda c: (-c.intel_level, c.contact_id),
        )

        if identified:
            lines.extend(["", "CONTACT DETAILS:"])
            for c in identified[:20]:
                vis = "VIS" if c.is_visible else f"T-{c.turns_since_seen}"
                pred = ""
                if not c.is_visible and not c.is_building:
                    pred = f" pred=({c.predicted_x},{c.predicted_y})"
                lines.append(
                    f"  {c.contact_id} [{c.intel_label:10s}] "
                    f"{c.unit_type or '???':8s} "
                    f"at ({c.cell_x},{c.cell_y}) "
                    f"HP:{c.hp_percent:.0%} "
                    f"{vis}{pred} "
                    f"conf={c.confidence:.0%}"
                )

        return "\n".join(lines)
