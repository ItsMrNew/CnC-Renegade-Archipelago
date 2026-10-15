"""Addresses derived from EA source and the exact stock Steam Game.dat below.

All addresses are RVAs. Nothing from the Zero Hour address map is reused.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path


class UnsupportedBuild(ValueError):
    pass


@dataclass(frozen=True)
class SteamProfile:
    game_sha256: str = "486d3e2eeb02fe6c6aea0faaad0b95c7863414f7406d84ad175c9a09b2e3c46c"
    scripts_sha256: str = "2c7c08525af4446ca3f5d851d12027ab885928d61a7806e827cdb8987517ef0f"
    commands: int = 0x45FD40
    commands_size: int = 0x330
    commands_version: int = 174
    mission_command_offset: int = 0x1AC
    mission_complete: int = 0x1AADD0
    game_data: int = 0x4559E8
    loaded_map: int = 0x457B98  # SaveGameManager::MapFilename, authoritative after save loading
    map_offset: int = 0x28
    game_mode: int = 0x456DC8  # CombatManager game type: 1 = mission
    pending_continue: int = 0x442EE4
    tick_iat: int = 0x3ED1E8
    scripts_commands: int = 0x201ED0
    combat_think: int = 0x35D0
    combat_keyboard: int = 0x1040
    combat_think_slot: int = 0x3EEB1C  # CombatGameMode vtable member 13
    message_dialog: int = 0x95E60  # DlgMsgBox::DoDialog(WCHAR*, WCHAR*, type, observer, data)
    message_count: int = 0x455A44
    end_game: int = 0x4A80
    end_game_menu: int = 0x4E50

    @property
    def menu_anchors(self):
        return {
            self.message_dialog: bytes.fromhex("535668380d0000e8a4b305008bf033db83c4043bf3"),
            0x95EC8: bytes.fromhex("a1445a85008b148dac058200408bce a3445a8500".replace(" ", "")),
            0x96398: bytes.fromhex("8b0d445a850049f7de1bf6890d445a8500"),
            self.end_game: bytes.fromhex("a1c86d850053bb010000003bc37424"),
            self.end_game_menu: bytes.fromhex("a1242f840083e803741d48740b6a00e85c1a0b0083c404c3"),
        }

    @property
    def gate_anchors(self):
        return {
            0x1226D6: bytes.fromhex("8b15987b8500a11c4a8300"),
            self.combat_think: bytes.fromhex("83ec1c578bf9897c240c8b07ff502084c00f84dd0200008bcfe852daffff"),
            self.combat_keyboard: bytes.fromhex("a0d676850053555684c07520a0d576850084c07517"),
            0x2A800: bytes.fromhex("8b510433c085d20f94c0c3"),  # Is_Active: State at +4 equals 0
        }

    @property
    def anchors(self):
        return {
            0x1226D6: bytes.fromhex("8b15987b8500a11c4a8300"),
            self.mission_complete: bytes.fromhex("8b44240450e8464cf4ff59c3"),
            0x7A470: bytes.fromhex("a1e8598500c3"),  # The_Game
            0x3960: bytes.fromhex("8b0dc86d8500b8010000003bc875158a4c240484c97408a2e42e8400c20400"),
        }


STEAM = SteamProfile()


def require_digest(path: Path, expected: str):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected:
        raise UnsupportedBuild(f"Unsupported build: {path.name}, SHA-256 {digest}. No hook installed.")
    return digest
