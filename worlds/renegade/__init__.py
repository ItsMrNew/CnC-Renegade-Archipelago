from BaseClasses import Item, ItemClassification, Location, Region, Tutorial
from worlds.AutoWorld import World, WebWorld
from .Options import RenegadeOptions, option_groups
from .data import (ALL_LOCATIONS, GAME, ITEM_IDS, MISSIONS, slot_data,
                   enabled_objectives, objective_id, objective_name, location_ids, SUPPLIES,
                   HEALTH_UPGRADE, ARMOUR_UPGRADE, TRAPS, BUFFS, ITEM_FLAGS, mission_requirement, WEAPON_ITEMS, DATA_DISCS, disc_name, disc_id)


class RenegadeItem(Item):
    game = GAME


class RenegadeLocation(Location):
    game = GAME


class RenegadeWeb(WebWorld):
    option_groups = option_groups
    tutorials = [Tutorial("Setup", "Set up the Renegade Archipelago launcher and room.",
                          "English", "setup_en.md", "setup/en", ["Renegade Archipelago contributors"])]


class RenegadeWorld(World):
    """Single-player campaign integration for stock Steam and TT 4.8.4 b9000.

    Mission and weapon unlocks, objective/disc checks, supplies, bonuses, traps,
    checkpoints and optional DeathLink connect the campaign to Archipelago.
    """
    game = GAME
    options_dataclass = RenegadeOptions
    options: RenegadeOptions
    web = RenegadeWeb()
    required_client_version = (0, 6, 7)
    item_name_to_id = ITEM_IDS
    location_name_to_id = ALL_LOCATIONS

    def create_regions(self):
        menu = Region("Menu", self.player, self.multiworld)
        self.multiworld.regions.append(menu)
        for mission in MISSIONS:
            region = Region(mission.label, self.player, self.multiworld)
            item, required = mission_requirement(mission, self.options.mission_unlocks.current_key)
            menu.connect(region, rule=lambda state, name=item, n=required: state.has(name, self.player, n))
            for oid in enabled_objectives(mission.map_name, self.options.secondary_objectives.value,
                                          self.options.primary_objectives.value):
                region.locations.append(RenegadeLocation(self.player, objective_name(mission, oid),
                    objective_id(mission.map_name, oid), region))
            if self.options.data_discs.value:
                for oid in DATA_DISCS[mission.map_name]:
                    region.locations.append(RenegadeLocation(self.player, disc_name(mission, oid),
                        disc_id(mission.map_name, oid), region))
            for check in range(1, self.options.checks_per_mission.value + 1):
                region.locations.append(RenegadeLocation(
                    self.player, mission.check_name(check), mission.check_id(check), region))
            event = RenegadeLocation(self.player, f"{mission.label} - Victory", None, region)
            event.place_locked_item(RenegadeItem(f"Mission {mission.number} Victory",
                                               ItemClassification.progression, None, self.player))
            region.locations.append(event)
            self.multiworld.regions.append(region)

    def create_items(self):
        count = len(location_ids(self.options.checks_per_mission.value, self.options.secondary_objectives.value,
                                 self.options.primary_objectives.value, self.options.data_discs.value))
        unlocks = 12 - self.options.starting_missions.value
        helpful = self.options.health_upgrades.value + self.options.armour_upgrades.value
        weapons = WEAPON_ITEMS if self.options.weapon_unlocks.value else ()
        remaining = count - unlocks - helpful - len(weapons)
        if remaining < 0:
            raise ValueError("Not enough checks for mission unlocks, weapon unlocks and permanent upgrades. "
                             "Enable primary/secondary objectives, increase checks per mission, reduce upgrades or disable weapon unlocks.")
        self.multiworld.itempool.extend(self.create_item(name) for name in weapons)
        for mission in MISSIONS:
            name, _ = mission_requirement(mission, self.options.mission_unlocks.current_key)
            item = self.create_item(name)
            if mission.number <= self.options.starting_missions.value:
                self.multiworld.push_precollected(item)
            else:
                self.multiworld.itempool.append(item)
        for name, amount in ((HEALTH_UPGRADE, self.options.health_upgrades.value),
                             (ARMOUR_UPGRADE, self.options.armour_upgrades.value)):
            self.multiworld.itempool.extend(self.create_item(name) for _ in range(amount))
        traps = remaining * self.options.trap_percentage.value // 100
        buffs = (remaining - traps) * self.options.buff_percentage.value // 100
        trap_weights = [getattr(self.options, key + '_weight').value for key in
                        ('armour_loss', 'butterfingers', 'concrete_boots', 'disco_havoc', 'moon_boots')]
        if not any(trap_weights):
            traps = 0
        self.multiworld.itempool.extend(self.create_item(name) for name in
            (self.random.choices(TRAPS, weights=trap_weights, k=traps) if traps else ()))
        buff_weights = [getattr(self.options, key + '_weight').value for key in
                        ('bullet_hose', 'vampiric_rounds', 'hologram_havoc')]
        if not any(buff_weights):
            buffs = 0
        self.multiworld.itempool.extend(self.create_item(name) for name in
            (self.random.choices(BUFFS, weights=buff_weights, k=buffs) if buffs else ()))
        # Balanced variety, with seed-randomized order/remainder.
        supplies = list(SUPPLIES)
        self.random.shuffle(supplies)
        self.multiworld.itempool.extend(self.create_item(supplies[i % len(supplies)]) for i in range(remaining - traps - buffs))

    def create_item(self, name):
        classification = ItemClassification(ITEM_FLAGS[name])
        return RenegadeItem(name, classification, ITEM_IDS[name], self.player)

    def get_filler_item_name(self):
        return self.random.choice(SUPPLIES)

    def set_rules(self):
        self.multiworld.completion_condition[self.player] = lambda state: all(
            state.has(f"Mission {mission.number} Victory", self.player) for mission in MISSIONS)

    def fill_slot_data(self):
        return slot_data(self.options.checks_per_mission.value, self.options.starting_missions.value,
                         bool(self.options.secondary_objectives.value), bool(self.options.death_link.value), True, True, True,
                         self.options.mission_unlocks.current_key, bool(self.options.weapon_unlocks.value),
                         bool(self.options.primary_objectives.value), bool(self.options.data_discs.value))
