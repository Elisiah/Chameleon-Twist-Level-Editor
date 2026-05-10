"""Kind definitions – each one fully describes a RoomObject struct variant.

`c_name` identifiers are stable across kinds (e.g. "unk24", "noKeyframes")
because the same RoomObject slot serves different semantics for different
movement types — codegen only cares about the slot index, the kind def
provides the human label.
"""

from dataclasses import dataclass, field

@dataclass
class KindField:
    c_index: int | None
    c_name: str
    label: str
    field_type: str = "int"
    default: str = "0"
    enum_items: list | None = None
    min: float = None
    max: float = None
    in_struct: bool = True
    manifest_key: str | None = None


@dataclass
class AuxArrayDef:
    """A sibling C array a kind emits alongside its RoomObject row.

    `suffix` is a format string that takes `idx` (per-room object index) and
    becomes part of the generated C symbol. The pointer/count are written
    into the RoomObject literal at the named field indices.
    """
    c_struct: str
    suffix: str
    pointer_field_index: int
    count_field_index: int


@dataclass
class KindDef:
    id: str
    label: str
    dispatch: int
    default_model: str = "0"
    fields: list[KindField] = field(default_factory=list)
    ro_overrides: dict[int, str] = field(default_factory=dict)
    aux_array: AuxArrayDef | None = None


KIND_REGISTRY: list[KindDef] = [
    KindDef(
        id="static_mesh",
        label="Static Mesh",
        dispatch=0x07,
        fields=[
            KindField(24, "unk70", "Collision Type", "enum",
                      default="2",
                      enum_items=[("0", "Non Solid", ""),
                                  ("2", "Solid", "")],
                      in_struct=True),
        ]
    ),
    KindDef(
        id="fluid_edge_collide",
        label="Fluid (Lava / Water)",
        dispatch=0x07,
        fields=[
            KindField(4, "unk24", "Behaviour", "int", default="21", in_struct=True),
            KindField(9, "unk34", "Movement Speed", "float", default="1.0", min=0, max=5, in_struct=True),
            KindField(24, "unk70", "Collision", "enum",
                      default="0",
                      enum_items=[("0", "No Collision", ""),
                                  ("2", "Collision", "")],
                      in_struct=True),
        ]
    ),
    KindDef(
        id="skybox",
        label="Sky Box",
        dispatch=0x07,
        ro_overrides={4: "7", 5: "20", 9: "1.0", 24: "0"},
        fields=[]
    ),
    KindDef(
        id="pole_grabbable",
        label="Pole (Grabbable)",
        dispatch=0x08,
        fields=[
            KindField(None, "grab_line", "Pole Height", "float", default="100.0",
                      min=0, in_struct=False, manifest_key="grab_line"),
        ]
    ),
    KindDef(
        id="exit_trigger",
        label="Exit Trigger",
        dispatch=0x17,
        fields=[
            KindField(10, "keyframes_temp", "Direction", "enum",
                      default="0",
                      enum_items=[("0", "North", ""), ("1", "East", ""), ("2", "South", ""), ("3", "West", "")],
                      in_struct=True),
            KindField(11, "noKeyframes", "Target Arg", "int", default="90", in_struct=True),
        ]
    ),
    KindDef(
        id="moving_platform_linear",
        label="Moving Platform (Linear 2‑Point)",
        dispatch=0x07,
        fields=[
            KindField(4, "unk20", "Behaviour", "int", default="7", in_struct=True),
            KindField(5, "unk24", "Movement Type", "int", default="5", in_struct=True),
            KindField(6, "unk28", "Target X", "float", default="0.0", in_struct=True),
            KindField(7, "unk2C", "Target Y", "float", default="0.0", in_struct=True),
            KindField(8, "unk30", "Target Z", "float", default="0.0", in_struct=True),
            KindField(11, "noKeyframes", "Time to Target (frames)", "int", default="0", in_struct=True),
            KindField(13, "unk44", "Time to Return (frames)", "int", default="0", in_struct=True),
        ]
    ),
    KindDef(
        id="platform_keyframed",
        label="Moving Platform (Keyframed Path)",
        dispatch=0x07,
        ro_overrides={
            4: "7",
            5: "10",
            21: "&func_800D90B8",
            24: "7",
        },
        aux_array=AuxArrayDef(
            c_struct="PlatformKeyframe",
            suffix="platform{idx}_keyframes",
            pointer_field_index=10,
            count_field_index=11,
        ),
        fields=[]
    ),
    KindDef(
        id="tilt_platform",
        label="Tilt Platform",
        dispatch=0x06,
        fields=[]
    ),
    KindDef(
        id="fixed_cam_trigger",
        label="Fixed Camera Trigger",
        dispatch=0x12,
        fields=[]
    ),
]

KIND_REGISTRY_BY_ID: dict[str, KindDef] = {k.id: k for k in KIND_REGISTRY}
CT_KINDS = [(k.id, k.label, "") for k in KIND_REGISTRY]
