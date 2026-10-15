from dataclasses import dataclass
from Options import PerGameCommonOptions, Choice, Range, Toggle, DefaultOnToggle, DeathLink, OptionGroup


class ChecksPerMission(Range):
    """Checks awarded together on each mission victory. Bonus checks are not separate objectives."""
    display_name = "Checks Per Mission"
    range_start = 1
    range_end = 5
    default = 5


class StartingMissions(Range):
    """Number of campaign missions initially cleared for access, in story order.

    Applies to both progressive and individual mission unlock modes.
    """
    display_name = "Starting Missions"
    range_start = 1
    range_end = 12
    default = 1


class MissionUnlocks(Choice):
    """Progressive: each Campaign Clearance opens the next mission in story order.
    Individual: each mission has its own unlock item and can be played as soon as
    that item arrives, independently of earlier missions. Use mission-start saves
    to enter unlocked missions out of order. Starting Missions still opens the
    first N campaign missions in either mode."""
    display_name = 'Mission Unlocks'
    option_progressive = 0
    option_individual = 1
    default = 0


class WeaponUnlocks(Toggle):
    """Put one unlock for every player weapon/equipment family in the item pool.
    The pistol is always available and is never an item. Locked weapons are
    removed from Havoc's inventory, including pickups and weapons in old saves.
    Missing unlocked weapons are granted with ammo when gameplay resumes.
    Existing ammo, empty equipment and vehicle weapons are left alone.
    Includes repair gun, all three C4 types and both strike beacons.
    Requires enough checks for 22 weapon unlocks, mission unlocks and upgrades.
    Weapon items are helpful; mission access logic remains map based."""
    display_name = 'Weapon Unlocks'


class SecondaryObjectives(DefaultOnToggle):
    """Add real stock secondary-objective checks. Requires a compatible native client.

    Objective IDs are scoped to each mission. These are independent of victory bonuses.
    """
    display_name = "Secondary Objectives"


class PrimaryObjectives(DefaultOnToggle):
    """Add 53 stock primary-objective checks, separate from mission victory bonuses.
    Escape/protection objectives without a completion callback finish on victory.
    The Scorpion Hunters uses help text and has no separate objective entries.
    """
    display_name = "Primary Objectives"


class DataDiscs(Toggle):
    """Add 22 physical data-disc pickups and the opening Black Hand intel reward as checks.
    EVA maps are fully revealed even with this option off. AP mission
    locks still apply. Discs collected before the client was running must be
    collected again from an earlier save; missing objects never grant checks.
    """
    display_name = 'Data Discs'


class HealthUpgrades(Range):
    """Helpful upgrades. Each adds 50 to guaranteed maximum health above the difficulty's starting value.
    Higher maxima from campaign pickups are preserved. Does not refill current health."""
    display_name = "Permanent Health Upgrades"
    range_start = 0
    range_end = 5
    default = 5


class ArmourUpgrades(HealthUpgrades):
    """Helpful upgrades. Each adds 50 to guaranteed maximum armour above the difficulty's starting value.
    Higher maxima from campaign pickups are preserved. Does not refill current armour."""
    display_name = "Permanent Armour Upgrades"


class TrapPercentage(Range):
    """Percentage of slots remaining after mission unlocks, weapon unlocks and
    permanent upgrades filled with traps.
    Includes Armour Loss, Butterfingers, Concrete Boots, Disco Havoc and Moon Boots.
    The rest contain supplies and temporary filler bonuses: filler automatically
    uses the remaining percentage, so no separate overall filler weight is needed.
    Zero disables traps and makes all remaining slots filler. If every trap
    weight is zero, the reserved trap slots become supplies."""
    display_name = "Trap Percentage"
    range_start = 0
    range_end = 100
    default = 50


class BuffPercentage(Range):
    """Percentage of non-trap filler slots filled with temporary bonuses.
    Bullet Hose, Vampiric Rounds and Hologram Havoc last 60 seconds of playable time.
    The rest are Health, Armour and Ammo Supplies, shared evenly where possible.
    With Trap Percentage 50 and this setting 50, remaining slots are roughly
    50% traps, 25% temporary bonuses and 25% supplies (counts round down).
    Zero disables these bonuses. If every bonus weight is zero, their slots
    become supplies."""
    display_name = 'Temporary Bonus Percentage'
    range_start = 0
    range_end = 100
    default = 50


class EffectWeight(Range):
    """Relative selection weight within its trap or bonus pool. 0 disables this
    item; 50 gives the default equal share. This is not an independent chance
    per check. Trap Percentage and Temporary Bonus Percentage set pool sizes.
    If every weight in a category is zero, its slots become supplies."""
    range_start = 0
    range_end = 100
    default = 50


class ArmourLossWeight(EffectWeight):
    display_name = 'Armour Loss Trap Weight'


class ButterfingersWeight(EffectWeight):
    display_name = 'Butterfingers Weight'


class ConcreteBootsWeight(EffectWeight):
    display_name = 'Concrete Boots Weight'


class DiscoHavocWeight(EffectWeight):
    display_name = 'Disco Havoc Weight'


class MoonBootsWeight(EffectWeight):
    display_name = 'Moon Boots Weight'


class BulletHoseWeight(EffectWeight):
    display_name = 'Bullet Hose Weight'


class VampiricRoundsWeight(EffectWeight):
    display_name = 'Vampiric Rounds Weight'


class HologramHavocWeight(EffectWeight):
    display_name = 'Hologram Havoc Weight'


@dataclass
class RenegadeOptions(PerGameCommonOptions):
    checks_per_mission: ChecksPerMission
    starting_missions: StartingMissions
    mission_unlocks: MissionUnlocks
    weapon_unlocks: WeaponUnlocks
    primary_objectives: PrimaryObjectives
    secondary_objectives: SecondaryObjectives
    data_discs: DataDiscs
    death_link: DeathLink
    health_upgrades: HealthUpgrades
    armour_upgrades: ArmourUpgrades
    trap_percentage: TrapPercentage
    armour_loss_weight: ArmourLossWeight
    butterfingers_weight: ButterfingersWeight
    concrete_boots_weight: ConcreteBootsWeight
    disco_havoc_weight: DiscoHavocWeight
    moon_boots_weight: MoonBootsWeight
    buff_percentage: BuffPercentage
    bullet_hose_weight: BulletHoseWeight
    vampiric_rounds_weight: VampiricRoundsWeight
    hologram_havoc_weight: HologramHavocWeight


option_groups = [
    OptionGroup("Gameplay Checks", [
        ChecksPerMission, StartingMissions, MissionUnlocks, WeaponUnlocks,
        PrimaryObjectives, SecondaryObjectives, DataDiscs,
    ]),
    OptionGroup("Death Link", [DeathLink]),
    OptionGroup("Permanent Upgrades", [HealthUpgrades, ArmourUpgrades]),
    OptionGroup("Traps", [
        TrapPercentage, ArmourLossWeight, ButterfingersWeight, ConcreteBootsWeight,
        DiscoHavocWeight, MoonBootsWeight,
    ]),
    OptionGroup("Filler Items", [
        BuffPercentage, BulletHoseWeight, VampiricRoundsWeight, HologramHavocWeight,
    ]),
]
