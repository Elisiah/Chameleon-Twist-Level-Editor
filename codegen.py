#!/usr/bin/env python3
"""
Chameleon Twist mod-manifest codegen.

Reads a <Land>_mod.json manifest, validates it against the kind catalog and
schema, and emits <Land>_mod.inc.c. Supports separate append lists for
objects, actors, collectables, and sprites.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
LEVELGROUP_DIR = REPO_ROOT / "src" / "levelGroup"


@dataclass(frozen=True)
class Kind:
    id: int
    extras: Tuple[str, ...] = ()
    keyframes_variant: str = "none"  # "none", "_keyframe", "_Vtx", "temp"
    section: str = "objects"         # "objects", "actors", "collectables", "sprites"


# Objects (RoomObject)
# Actors (RoomActor)
# Collectables (Collectable)
# Sprites (SpriteActor)
KINDS: Dict[str, Kind] = {
    # Objects
    "static_mesh":           Kind(id=0x00, section="objects"),
    "moving_platform":       Kind(id=0x05, extras=("keyframes",), keyframes_variant="_keyframe", section="objects"),
    "tilt_platform":         Kind(id=0x06, extras=("axis", "angle"), section="objects"),
    "spin_door":             Kind(id=0x07, section="objects"),
    "pole_grabbable":        Kind(id=0x08, extras=("grab_line",), keyframes_variant="_Vtx", section="objects"),
    "moving_object_simple":  Kind(id=0x09, extras=("velocity",), section="objects"),
    "tilt_object":           Kind(id=0x0A, extras=("axis", "angle"), section="objects"),
    "platform_with_switch":  Kind(id=0x0B, section="objects"),
    "conveyor_belt":         Kind(id=0x0C, extras=("velocity",), section="objects"),
    "ice_platform":          Kind(id=0x0D, section="objects"),
    "trampoline":            Kind(id=0x0E, section="objects"),
    "crumbling_platform":    Kind(id=0x0F, section="objects"),
    "spiked_platform":       Kind(id=0x10, section="objects"),
    "floating_object":       Kind(id=0x11, section="objects"),
    "fixed_cam_trigger":     Kind(id=0x12, section="objects"),
    "platform_keyframed":    Kind(id=0x13, extras=("keyframes",), keyframes_variant="_keyframe", section="objects"),
    "exit_trigger":          Kind(id=0x17, extras=("direction", "target_arg"), keyframes_variant="temp", section="objects"),
    "door":                  Kind(id=0x19, extras=("direction", "bounds", "target_arg"), keyframes_variant="temp", section="objects"),
    "shutter":               Kind(id=0x1A, section="objects"),
    "cannon_launcher":       Kind(id=0x1B, section="objects"),
    "spring_bounce":         Kind(id=0x1C, section="objects"),
    "climb_vine":            Kind(id=0x1D, section="objects"),
    "water_zone":            Kind(id=0x1E, extras=("zone_type", "bbox"), section="objects"),
    "lava_zone":             Kind(id=0x1F, extras=("zone_type", "bbox"), section="objects"),
    "slow_zone":             Kind(id=0x20, extras=("zone_type", "bbox"), section="objects"),
    "wind_zone":             Kind(id=0x22, extras=("zone_type", "bbox"), section="objects"),
    "boss_trigger":          Kind(id=0x23, section="objects"),
    "checkpoint_flag":       Kind(id=0x24, section="objects"),

    # Actors
    "red_ant":                Kind(id=0x01, section="actors"),
    "green_ant":              Kind(id=0x02, section="actors"),
    "grey_ant":               Kind(id=0x03, section="actors"),
    "bullet_hell_ant":        Kind(id=0x04, section="actors"),
    "ant_trio":               Kind(id=0x05, section="actors"),
    "yellow_ant":             Kind(id=0x06, section="actors"),
    "ant_queen":              Kind(id=0x07, section="actors"),
    "ant_queen_ant":          Kind(id=0x08, section="actors"),
    "grey_ant_spawner":       Kind(id=0x09, section="actors"),
    "ant_trio_spawner":       Kind(id=0x0A, section="actors"),
    "bullet_hell_ant_spawner": Kind(id=0x0B, section="actors"),
    "red_ant_spawner":        Kind(id=0x0C, section="actors"),
    "white_bomb":             Kind(id=0x0D, section="actors"),
    "grenade":                Kind(id=0x0E, section="actors"),
    "missile_spawner":        Kind(id=0x0F, section="actors"),
    "missile":                Kind(id=0x10, section="actors"),
    "cannon":                 Kind(id=0x11, section="actors"),
    "cannonball":             Kind(id=0x12, section="actors"),
    "bl_boss_segment":        Kind(id=0x13, section="actors"),
    "explosion":              Kind(id=0x14, section="actors"),
    "bl_boss_bombs":          Kind(id=0x15, section="actors"),
    "black_chameleon_projectile_spawner": Kind(id=0x16, section="actors"),
    "black_chameleon_projectile": Kind(id=0x17, section="actors"),
    "chomper":                Kind(id=0x18, section="actors"),
    "sand_crab":              Kind(id=0x19, section="actors"),
    "vulture":                Kind(id=0x1A, section="actors"),
    "arrow_spawner":          Kind(id=0x1B, section="actors"),
    "arrows":                 Kind(id=0x1C, section="actors"),
    "boulder":                Kind(id=0x1D, section="actors"),
    "armadillo":              Kind(id=0x1E, section="actors"),
    "popcorn":                Kind(id=0x20, section="actors"),
    "pogo":                   Kind(id=0x21, section="actors"),
    "ice_cream_sandwich":     Kind(id=0x24, section="actors"),
    "choco_kid":              Kind(id=0x25, section="actors"),
    "cake_boss":              Kind(id=0x26, section="actors"),
    "cake_boss_strawberry":   Kind(id=0x27, section="actors"),
    "cake_boss_choco_kid":    Kind(id=0x29, section="actors"),
    "bowling_ball":           Kind(id=0x2A, section="actors"),
    "bowling_pins":           Kind(id=0x2B, section="actors"),
    "cue_ball":               Kind(id=0x2C, section="actors"),
    "billiards_ball":         Kind(id=0x2D, section="actors"),
    "cup":                    Kind(id=0x30, section="actors"),
    "saucer":                 Kind(id=0x31, section="actors"),
    "metal_sheet":            Kind(id=0x32, section="actors"),
    "scroll":                 Kind(id=0x33, section="actors"),
    "rng_room_spawner":       Kind(id=0x34, section="actors"),
    "mirror":                 Kind(id=0x35, section="actors"),
    "barrel_jump_fire_spawner": Kind(id=0x36, section="actors"),
    "barrel_jump_fire":       Kind(id=0x37, section="actors"),
    "fire_spitter":           Kind(id=0x38, section="actors"),
    "candles":                Kind(id=0x39, section="actors"),
    "fire_spawner":           Kind(id=0x3A, section="actors"),
    "fire":                   Kind(id=0x3B, section="actors"),
    "sandal":                 Kind(id=0x3C, section="actors"),
    "pile_of_books":          Kind(id=0x3D, section="actors"),
    "pile_of_books_arm_segments": Kind(id=0x3E, section="actors"),
    "pile_of_books_arm_spitter": Kind(id=0x3F, section="actors"),
    "pile_of_books_projectile": Kind(id=0x40, section="actors"),
    "spider_spawner":         Kind(id=0x41, section="actors"),
    "spider":                 Kind(id=0x42, section="actors"),
    "spider_trio":            Kind(id=0x43, section="actors"),
    "golem":                  Kind(id=0x44, section="actors"),
    "hedgehog":               Kind(id=0x45, section="actors"),
    "fish":                   Kind(id=0x46, section="actors"),
    "lizard_kong_butterfly":  Kind(id=0x47, section="actors"),
    "golem_room_spider_spawner": Kind(id=0x48, section="actors"),
    "lizard_kong_butterfly_spawner": Kind(id=0x49, section="actors"),
    "lizard_kong_boulder":    Kind(id=0x4A, section="actors"),
    "lizard_kong":            Kind(id=0x4B, section="actors"),
    "popcorn_bucket_spawner": Kind(id=0x4C, section="actors"),
    "popcorn_bucket":         Kind(id=0x4D, section="actors"),
    "choco_kid_spawner":      Kind(id=0x50, section="actors"),
    "spawned_choco_kid":      Kind(id=0x51, section="actors"),
    "grey_ant_spawner_wrapper": Kind(id=0x52, section="actors"),
    "battle_mode_sand_crab_spawner": Kind(id=0x54, section="actors"),
    "battle_mode_sand_crab":  Kind(id=0x55, section="actors"),
    "battle_mode_fire_spawner": Kind(id=0x56, section="actors"),
    "battle_mode_fire":       Kind(id=0x57, section="actors"),
    "battle_mode_saucer_spawner": Kind(id=0x58, section="actors"),
    "battle_mode_saucer":     Kind(id=0x59, section="actors"),
    "power_up_spawner":       Kind(id=0x5C, section="actors"),
    "falling_grey_ant_spawner": Kind(id=0x5D, section="actors"),
    "falling_grey_ant":       Kind(id=0x5E, section="actors"),
    "unk_fire_spawner":       Kind(id=0x5F, section="actors"),

    # Collectables
    "r_heart":                Kind(id=0x60, section="collectables"),
    "falling_r_heart":        Kind(id=0x61, section="collectables"),
    "o_heart":                Kind(id=0x62, section="collectables"),
    "y_heart":                Kind(id=0x63, section="collectables"),
    "crown":                  Kind(id=0x64, section="collectables"),
    "carrot":                 Kind(id=0x65, section="collectables"),
    "time_stop_power_up":     Kind(id=0x66, section="collectables"),
    "big_feet_power_up":      Kind(id=0x67, section="collectables"),
    "big_head_power_up":      Kind(id=0x68, section="collectables"),
    "shrink_power_up":        Kind(id=0x69, section="collectables"),
    "shrink_enemy_power_up":  Kind(id=0x6A, section="collectables"),

    # Sprites
    "sprite":                 Kind(id=0, section="sprites"),  # id unused, will come from entry["sprite_type"]
}


UNK14_BUFFER_CAP = 131072


# Validation
@dataclass
class ValidationError:
    path: str
    message: str

    def __str__(self) -> str:
        return f"  {self.path}: {self.message}"


@dataclass
class ManifestReport:
    errors: List[ValidationError] = field(default_factory=list)
    warnings: List[ValidationError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _vec3(value: Any) -> bool:
    return isinstance(value, list) and len(value) == 3 and all(isinstance(c, (int, float)) for c in value)


def validate_object(obj: dict, path: str, known_models: set[str], report: ManifestReport) -> None:
    kind_name = obj.get("kind")
    if kind_name not in KINDS:
        report.errors.append(ValidationError(path, f"unknown kind {kind_name!r}; see object_kinds.md"))
        return
    kind = KINDS[kind_name]

    # Required fields depend on section
    if kind.section == "objects":
        for required in ("model", "pos", "scale"):
            if required not in obj:
                report.errors.append(ValidationError(path, f"missing required field {required!r}"))
    elif kind.section == "actors":
        if "pos" not in obj:
            report.errors.append(ValidationError(path, f"missing required field 'pos'"))
    elif kind.section == "collectables":
        if "collectable_type" not in obj:
            report.errors.append(ValidationError(path, f"missing required field 'collectable_type'"))
        if "pos" not in obj:
            report.errors.append(ValidationError(path, f"missing required field 'pos'"))
    elif kind.section == "sprites":
        if "sprite_type" not in obj:
            report.errors.append(ValidationError(path, f"missing required field 'sprite_type'"))
        if "pos" not in obj:
            report.errors.append(ValidationError(path, f"missing required field 'pos'"))

    if kind.section == "objects" and "pos" in obj and not _vec3(obj["pos"]):
        report.errors.append(ValidationError(path, "pos must be [x,y,z]"))
    if kind.section == "objects" and "scale" in obj and not _vec3(obj["scale"]):
        report.errors.append(ValidationError(path, "scale must be [x,y,z]"))

    model = obj.get("model")
    if model and known_models and model not in known_models:
        report.warnings.append(ValidationError(path, f"model {model!r} not in appended set; vanilla check deferred"))

    for extra in kind.extras:
        if extra not in obj:
            report.errors.append(ValidationError(path, f"kind {kind_name!r} requires field {extra!r}"))


def validate_manifest(manifest: dict, report: ManifestReport) -> None:
    if manifest.get("schema_version") != 0:
        report.errors.append(ValidationError("$.schema_version", "must be 0 (only v0 supported)"))

    land = manifest.get("land")
    if not land:
        report.errors.append(ValidationError("$.land", "missing"))
    elif not (LEVELGROUP_DIR / f"{land}.c").exists():
        report.errors.append(ValidationError("$.land", f"no src/levelGroup/{land}.c"))

    if manifest.get("inherit", "vanilla") != "vanilla":
        report.errors.append(ValidationError("$.inherit", "only 'vanilla' supported in v0"))

    appended = [m["name"] for m in manifest.get("stageModels", {}).get("append", []) if "name" in m]
    known_models: set[str] = set(appended)

    for room_id, room_patch in manifest.get("rooms", {}).items():
        path_room = f"$.rooms[{room_id}]"
        if "replace" in room_patch and any(k in room_patch for k in ("objects", "actors", "collectables", "sprites")):
            report.errors.append(ValidationError(path_room, "cannot mix 'replace' with per-section append lists"))

        for section in ("objects", "actors", "collectables", "sprites"):
            if section in room_patch:
                for i, obj in enumerate(room_patch[section].get("append", [])):
                    validate_object(obj, f"{path_room}.{section}.append[{i}]", known_models, report)


# Vanilla extractor
SECTION_PATTERNS: List[Tuple[str, str]] = [
    (r"^StageModel\s+(\w+)\[\]\s*=\s*\{",                   "stage_models"),
    (r"^unsigned\s+char\s+(\w+_rabobjects_Bin)\[\]\s*=\s*\{","rabobjects"),
    (r"^RoomObject\s+(\w+)\[\]\s*=\s*\{",                   "room_objects"),
    (r"^RoomActor\s+(\w+)\[\]\s*=\s*\{",                    "room_actors"),
    (r"^Collectable\s+(\w+)\[\]\s*=\s*\{",                  "room_collectables"),
    (r"^SpriteActor\s+(\w+)\[\]\s*=\s*\{",                  "room_sprites"),
    (r"^RoomInstance\s+(\w+_room_instances)\[\]\s*=\s*\{",  "room_instances"),
    (r"^RoomInstance\s+(\w+)\[\]\s*=\s*\{",                 "rmset"),
    (r"^StageMapData\s+(\w+)\s*=\s*\{",                     "map_data"),
    (r"^LevelScope\s+(\w+)\s*=\s*\{",                       "scope"),
    (r"^StageData\s+(\w+)\s*=\s*\{",                        "stage_data"),
]

_ROOM_SECTION_SUFFIX: Dict[str, str] = {
    "room_objects":      "objects",
    "room_actors":       "actors",
    "room_collectables": "collectables",
    "room_sprites":      "sprites",
}


@dataclass
class Section:
    kind: str           
    name: str           
    start_line: int     
    end_line: int       
    text: str           


def extract_sections(c_source: str) -> List[Section]:
    lines = c_source.splitlines(keepends=True)
    sections: List[Section] = []
    consumed_lines: Set[int] = set()

    for i, line in enumerate(lines):
        if i in consumed_lines:
            continue
        for pattern, kind in SECTION_PATTERNS:
            m = re.match(pattern, line)
            if not m:
                continue
            depth = 0
            end_idx = None
            for j in range(i, len(lines)):
                for ch in lines[j]:
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            end_idx = j
                            break
                if end_idx is not None:
                    break
            if end_idx is None:
                break
            for k in range(i, end_idx + 1):
                consumed_lines.add(k)
            sections.append(Section(
                kind=kind,
                name=m.group(1),
                start_line=i + 1,
                end_line=end_idx + 1,
                text="".join(lines[i:end_idx + 1]),
            ))
            break
    return sections


def collect_extern_decls(c_source: str) -> str:
    out: List[str] = []
    for line in c_source.splitlines(keepends=True):
        stripped = line.lstrip()
        if (stripped.startswith("extern ")
                or stripped.startswith("//")
                or stripped.startswith("#include")
                or stripped.startswith("/*")
                or stripped.strip() == ""):
            out.append(line)
            continue
        break
    return "".join(out)


def parse_stage_models(stage_models_text: str) -> List[str]:
    return [m.group(1) for m in re.finditer(r"\{\s*(\w+)_Gfx\b", stage_models_text)]


# Formatting functions for each section
RO_FIELD_DEFAULTS: Dict[str, str] = {
    "unk18": "0", "damages": "0.0",
    "unk20": "7", "unk24": "0",
    "unk28": "1400.0", "unk2C": "1000.0", "unk30": "-1000.0", "unk34": "0.0",
    "keyframes_temp": "0", "noKeyframes": "90",
    "unk40": "0", "unk44": "1000", "unk48": "0", "unk4C": "0",
    "unk54": "-1", "unk58": "-1", "unk5C": "-1",
    "unk68": "0", "unk6C": "0",
    "unk74": "4", "unk78": "4", "unk7C": "0",
    "unk80": "-1", "unk84": "0", "unk88": "0",
}

EXIT_DIRECTION_MAP: Dict[str, int] = {"N": 0, "E": 1, "S": 2, "W": 3}


def format_room_object(entry: dict, model_ref: object, dispatch_id: int) -> str:
    f = dict(RO_FIELD_DEFAULTS)
    kind = entry["kind"]
    if kind in ("exit_trigger", "door"):
        f["keyframes_temp"] = str(EXIT_DIRECTION_MAP.get(entry.get("direction", "N"), 0))
        f["noKeyframes"] = str(entry.get("target_arg", 0))

    pos = entry["pos"]
    scale = entry["scale"]
    fields = [
        f"{{{pos[0]:.4f}, {pos[1]:.4f}, {pos[2]:.4f}}}",
        f"{{{scale[0]:.4f}, {scale[1]:.4f}, {scale[2]:.4f}}}",
        f["unk18"], f["damages"], f["unk20"], f["unk24"],
        f["unk28"], f["unk2C"], f["unk30"], f["unk34"],
        f["keyframes_temp"], f["noKeyframes"],
        f["unk40"], f["unk44"], f["unk48"], f["unk4C"],
        str(model_ref),                  # 0x50 id  ← model index
        f["unk54"], f["unk58"], f["unk5C"],
        "NULL", "NULL",                  # 0x60/0x64 func1/func2 : bound at load
        f["unk68"], f["unk6C"],
        f"0x{dispatch_id:02X}",          # 0x70 dispatch type
        f["unk74"], f["unk78"], f["unk7C"], f["unk80"], f["unk84"], f["unk88"],
    ]
    return "    { " + ", ".join(fields) + " },\n"


def format_room_actor(entry: dict) -> str:
    # RoomActor struct: { actorID, {x,y,z}, angle, unk1, unk2, unk3, unk4, unk5, unk6, unk7, ... }
    kind = entry["kind"]
    kind_info = KINDS.get(kind)
    if not kind_info:
        raise ValueError(f"Unknown actor kind {kind}")
    actor_id = kind_info.id
    pos = entry.get("pos", [0,0,0])
    angle = entry.get("angle", 0.0)

    fields = [
        f"{actor_id}",
        f"{{{pos[0]:.4f}, {pos[1]:.4f}, {pos[2]:.4f}}}",
        f"{angle:.4f}", "0.0", "0.0", "0", "0.0", "0.0", "0.0", "0", "0.0", "0.0", "0.0", "0.0", "0", "0", "0", "0", "0", "0", "0", "0"
    ]
    return "    { " + ", ".join(fields) + " },\n"


def format_collectable(entry: dict) -> str:
    # Collectable struct: { type, {x,y,z}, unk1, unk2, unk3, unk4 }
    collectable_type = entry["collectable_type"].upper()
    pos = entry.get("pos", [0,0,0])
    unk1 = entry.get("unk1", -1)
    unk2 = entry.get("unk2", 0)
    unk3 = entry.get("unk3", 0)
    unk4 = entry.get("unk4", 0)
    return f"    {{{collectable_type}, {{{pos[0]:.4f}, {pos[1]:.4f}, {pos[2]:.4f}}}, {unk1}, {unk2}, {unk3}, {unk4}}},\n"


def format_sprite(entry: dict) -> str:
    # SpriteActor struct: { sprite_type, sprite_id, {x,y,z}, {w,h,d}, unk1, unk2, angle, flags, ... }
    sprite_type = entry.get("sprite_type", 0)
    sprite_id = entry.get("sprite_id", 0)
    pos = entry.get("pos", [0,0,0])
    scale = entry.get("scale", [1,1,1])
    angle = entry.get("angle", 0.0)
    flags = entry.get("flags", 0)

    return f"    {{{sprite_type}, {sprite_id}, {{{pos[0]:.4f}, {pos[1]:.4f}, {pos[2]:.4f}}}, {{{scale[0]:.4f}, {scale[1]:.4f}, {scale[2]:.4f}}}, 1, 0, {angle:.4f}, {flags}, 0, 0, 0, 0, {{0,0,0,0}}}},\n"


_SENTINEL_RE = re.compile(
    r"^\s*\{\s*\{\s*0(?:\.0+)?\s*,\s*0(?:\.0+)?\s*,\s*0(?:\.0+)?\s*\}\s*,"
    r"\s*\{\s*0(?:\.0+)?\s*,\s*0(?:\.0+)?\s*,\s*0(?:\.0+)?\s*\}"
)


def splice_before_sentinel(section_text: str, new_literals: List[str]) -> str:
    """Insert new_literals before the null-terminator sentinel in a C array."""
    if not new_literals:
        return section_text
    lines = section_text.splitlines(keepends=True)
    for i in range(len(lines) - 1, -1, -1):
        if _SENTINEL_RE.match(lines[i]):
            return "".join(lines[:i]) + "".join(new_literals) + "".join(lines[i:])
    closing = section_text.rfind("};")
    if closing == -1:
        return section_text
    return section_text[:closing] + "".join(new_literals) + section_text[closing:]


def splice_stage_models_append(stage_models_text: str, appended: List[dict]) -> str:
    if not appended:
        return stage_models_text
    new_entries = "".join(
        f"    {{{m.get('gfx_entry', m['name'] + '_Gfx')}, &{m['name']}_collision, {{0, 0, 0, 0, 0, 0, 0, 0, 0, 0}}, }},\n"
        for m in appended
    )
    closing = "};"
    idx = stage_models_text.rfind(closing)
    if idx == -1:
        return stage_models_text
    # Ensure the last vanilla entry has a trailing comma before we append.
    prefix = stage_models_text[:idx].rstrip()
    if prefix.endswith("}") and not prefix.endswith("},"):
        prefix += ","
    return prefix + "\n" + new_entries + stage_models_text[idx:]


# Emit

EMIT_HEADER = """\
/* generated by tools/LevelEditor/codegen.py : do not edit */
/* manifest: {manifest_path} */
/* land:     {land} */

#include "common.h"
#include "common_structs.h"

"""


def emit(manifest: dict, manifest_path: Path, out_path: Path, report: ManifestReport) -> None:
    land = manifest["land"]
    appended = manifest.get("stageModels", {}).get("append", [])
    rooms = manifest.get("rooms", {})

    vanilla_path = LEVELGROUP_DIR / f"{land}.c"
    vanilla = vanilla_path.read_text()
    sections = extract_sections(vanilla)
    sections_by_kind: Dict[str, List[Section]] = {}
    for s in sections:
        sections_by_kind.setdefault(s.kind, []).append(s)

    sm = sections_by_kind.get("stage_models", [])
    vanilla_models = parse_stage_models(sm[0].text) if sm else []

    out: List[str] = [EMIT_HEADER.format(manifest_path=manifest_path.name, land=land)]
    out.append("/* --- 1. extern decls (copied from vanilla) --- */\n")
    out.append(collect_extern_decls(vanilla))

    # includes and ARRAY_COUNT define.
    if appended:
        out.append(f"\n#define ARRAY_COUNT_VANILLA_STAGEMODELS {len(vanilla_models)}\n")
        out.append("\n/* --- 2. appended-mod asset includes --- */\n")
        for m in appended:
            if m.get("gfx_entry"):
                # fast64-exported gfx: include the generated header + display list.
                out.append(f'#include "tools/LevelEditor/manifests/{land}/{m["name"]}/header.h"\n')
                out.append(f'#include "tools/LevelEditor/manifests/{land}/{m["name"]}/model.inc.c"\n')
            else:
                out.append(f"extern Gfx {m['name']}_Gfx[];\n")
            out.append(f'#include "tools/LevelEditor/manifests/{land}/{m["name"]}/{m["name"]}.collision.c"\n')
        out.append("\n")
        for i, m in enumerate(appended):
            out.append(f"#define {m['name'].upper()}_MODEL  (ARRAY_COUNT_VANILLA_STAGEMODELS + {i})\n")
        out.append("\n")

    # merged stageModels[]
    out.append("/* --- 3. stageModels[] (vanilla + appended) --- */\n")
    if not sm:
        report.errors.append(ValidationError("vanilla", f"no StageModel array found in {vanilla_path.name}"))
    else:
        out.append(splice_stage_models_append(sm[0].text, appended))
        out.append("\n")

    rb = sections_by_kind.get("rabobjects", [])
    if rb:
        out.append("/* --- 4. rabobjects_Bin (copied verbatim) --- */\n")
        out.append(rb[0].text)
        out.append("\n")

    appended_names = [m["name"] for m in appended]
    all_models = vanilla_models + appended_names

    # Build raw_replace overrides for each section
    raw_overrides: Dict[str, str] = {}
    for room_key, room_patch in rooms.items():
        km = re.match(r'^([a-z_]*)(\d+)$', str(room_key))
        rv, rn = (km.group(1), km.group(2)) if km else ("", str(room_key))
        for suffix, c_text in room_patch.get("raw_replace", {}).items():
            sym = f"{land}_{rv}room{rn}_{suffix}"
            raw_overrides[sym] = c_text

    # Process each of the four placement sections
    for section_kind, suffix in _ROOM_SECTION_SUFFIX.items():
        sections_list = sections_by_kind.get(section_kind, [])
        if not sections_list:
            continue
        label = section_kind.replace("_", " ").title()
        out.append(f"/* --- {label} --- */\n")
        for s in sections_list:
            new_literals: List[str] = []
            # The array name is e.g. AntLand_room0_objects or JungleLand_ext_room1_objects.
            room_match = re.search(r"_([a-z_]*)room(\d+)_", s.name)
            if room_match:
                room_key = room_match.group(1) + room_match.group(2)  # e.g. "ext_1" or "1"
                room_patch = rooms.get(room_key, {})
                append_list = room_patch.get(suffix, {}).get("append", [])
                if append_list:
                    # For each entry in the append list, format according to its kind's section
                    for entry in append_list:
                        kind_info = KINDS.get(entry["kind"])
                        if not kind_info:
                            continue
                        if kind_info.section != suffix:
                            report.warnings.append(ValidationError(
                                f"manifest {suffix}.append",
                                f"entry kind '{entry['kind']}' has section '{kind_info.section}' but placed in '{suffix}' – ignoring"))
                            continue
                        # Format based on section
                        if suffix == "objects":
                            model_sym = entry["model"]
                            if model_sym in all_models:
                                model_ref: object = all_models.index(model_sym)
                            else:
                                report.warnings.append(ValidationError(
                                    f"room {room_key} objects.append",
                                    f"model {model_sym!r} not in stageModels; emitting symbol verbatim"))
                                model_ref = model_sym
                            new_literals.append(format_room_object(entry, model_ref, kind_info.id))
                        elif suffix == "actors":
                            new_literals.append(format_room_actor(entry))
                        elif suffix == "collectables":
                            new_literals.append(format_collectable(entry))
                        elif suffix == "sprites":
                            new_literals.append(format_sprite(entry))
            # raw_replace takes precedence, else append-spliced version
            base = raw_overrides.get(s.name, s.text)
            final_text = splice_before_sentinel(base, new_literals) if new_literals else base
            out.append(final_text)
            out.append("\n")

    for kind in ("room_instances", "rmset", "map_data", "scope", "stage_data"):
        for s in sections_by_kind.get(kind, []):
            out.append(s.text)
            out.append("\n")

    out_path.write_text("".join(out))



def gate_vanilla(c_source: str, sections: List[Section], mod_include_relpath: str) -> str:
    lines = c_source.splitlines(keepends=True)
    spans = sorted(((s.start_line, s.end_line, s.name) for s in sections), reverse=True)
    for start, end, name in spans:
        s0, e0 = start - 1, end - 1
        lines.insert(e0 + 1, f"#endif /* !CT_MOD: {name} */\n")
        lines.insert(s0, f"#ifndef CT_MOD /* gated section: {name} */\n")
    if not c_source.endswith("\n"):
        lines.append("\n")
    lines.append(f"\n#ifdef CT_MOD\n#include \"{mod_include_relpath}\"\n#endif\n")
    return "".join(lines)


def prepare_mod(manifest_path: Path, build_root: Path, report: ManifestReport) -> Tuple[Path, Path] | None:
    manifest = json.loads(manifest_path.read_text())
    validate_manifest(manifest, report)
    if not report.ok:
        return None

    land = manifest["land"]
    vanilla_path = LEVELGROUP_DIR / f"{land}.c"
    out_dir = build_root / "mod" / "levelGroup"
    out_dir.mkdir(parents=True, exist_ok=True)

    mod_inc_path = out_dir / f"{land}_mod.inc.c"
    emit(manifest, manifest_path, mod_inc_path, report)
    if not report.ok:
        return None

    vanilla_text = vanilla_path.read_text()
    sections = extract_sections(vanilla_text)
    gated = gate_vanilla(vanilla_text, sections, mod_include_relpath=f"{land}_mod.inc.c")
    gated_path = out_dir / f"{land}.c"
    gated_path.write_text(gated)
    return gated_path, mod_inc_path



def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Chameleon Twist mod manifest codegen")
    p.add_argument("manifest", type=Path, help="path to <Land>_mod.json")
    p.add_argument("--out", type=Path, default=None, help="output .inc.c path (default: alongside manifest)")
    p.add_argument("--validate-only", action="store_true")
    p.add_argument("--prepare-mod", type=Path, default=None,
                   metavar="BUILD_ROOT",
                   help="emit build/mod/levelGroup/<Land>.c (gated) and <Land>_mod.inc.c under the given build root")
    args = p.parse_args(argv)

    try:
        manifest = json.loads(args.manifest.read_text())
    except FileNotFoundError:
        print(f"manifest not found: {args.manifest}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as e:
        print(f"manifest is not valid JSON: {e}", file=sys.stderr)
        return 2

    report = ManifestReport()
    validate_manifest(manifest, report)

    if report.warnings:
        print("warnings:")
        for w in report.warnings:
            print(w)
    if not report.ok:
        print("errors:")
        for e in report.errors:
            print(e)
        return 1

    print(f"manifest OK ({manifest.get('land')})")

    if args.validate_only:
        return 0

    if args.prepare_mod is not None:
        result = prepare_mod(args.manifest, args.prepare_mod, report)
        if result is None or not report.ok:
            print("prepare-mod errors:")
            for e in report.errors:
                print(e)
            return 1
        gated_c, mod_inc = result
        print(f"wrote {gated_c}")
        print(f"wrote {mod_inc}")
        return 0

    out_path = args.out or args.manifest.with_name(f"{manifest['land']}_mod.inc.c")
    emit(manifest, args.manifest, out_path, report)
    if not report.ok:
        print("emit errors:")
        for e in report.errors:
            print(e)
        return 1
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())