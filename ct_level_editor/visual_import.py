"""Parse .gfx.inc.c display lists into textured Blender mesh data."""

from __future__ import annotations
import re
from pathlib import Path


_UV_DENOM = 32768.0 / 8.0  # N64 fixed-point -> normalised, matches modelViewer.py


# CT vtx format: {{{ x, y, z }, flag, { u, v }, { r, g, b, a }}}
_VTX_RE = re.compile(
    r'\{\{\{\s*(-?\d+),\s*(-?\d+),\s*(-?\d+)\s*\},\s*\d+,\s*\{\s*(-?\d+),\s*(-?\d+)\s*\}'
)


def _parse_vtx_file(path: Path) -> list[tuple]:
    """Parse a .vtx.inc.c file -> list of (x, y, z, u, v) int tuples."""
    return [
        tuple(int(m.group(i)) for i in range(1, 6))
        for m in _VTX_RE.finditer(path.read_text())
    ]


def _png_search_dirs(symbol: str, asset_dir: Path, repo_root: Path | None) -> list[Path]:
    """Build an ordered list of directories to search for a *_PNG symbol's file.

    Search order:
      1. The model's own asset dir  (model-local textures)
      2. The model's land root      (e.g. JungleLand/ for JungleLand/doorFrame/)
      3. assets/levelGroup/<SymPrefix>/img/  (shared pool keyed by symbol prefix)
      4. assets/levelGroup/<SymPrefix>/      (symbol-prefix land root)
    """
    dirs = [asset_dir]
    # Model's own land root (parent of model dir)
    model_land_dir = asset_dir.parent
    if model_land_dir not in dirs:
        dirs.append(model_land_dir)
    if repo_root is None:
        return dirs
    sym_body = symbol[:-4] if symbol.endswith('_PNG') else symbol  # strip _PNG
    if '_' not in sym_body:
        return dirs
    sym_land_prefix = sym_body.split('_', 1)[0]
    sym_land_dir = Path(repo_root) / 'assets' / 'levelGroup' / sym_land_prefix
    if sym_land_dir / 'img' not in dirs:
        dirs.append(sym_land_dir / 'img')
    if sym_land_dir not in dirs:
        dirs.append(sym_land_dir)
    return dirs


def _symbol_to_file(symbol: str, asset_dir: Path, repo_root: Path | None = None) -> Path | None:
    """Resolve a Gfx symbol to a file.

    *_Vtx  -> *.vtx.inc.c in asset_dir (vertex data is always model-local)
    *_PNG  -> *.png, searched in model dir, land/img/, then land root
    """
    sym = symbol.strip().lstrip('&').split('[')[0]

    if sym.endswith('_Vtx'):
        for f in asset_dir.glob('*.vtx.inc.c'):
            stem = f.name.split('.vtx.inc.c')[0]
            if sym.endswith(stem + '_Vtx'):
                return f
        vtx_files = list(asset_dir.glob('*.vtx.inc.c'))
        return vtx_files[0] if vtx_files else None

    if sym.endswith('_PNG'):
        for search_dir in _png_search_dirs(sym, asset_dir, repo_root):
            if not search_dir.exists():
                continue
            for f in sorted(search_dir.glob('*.png')):
                stem_under = f.stem.replace('.', '_')
                if sym.endswith(stem_under + '_PNG'):
                    return f

    return None


def _preprocess_gfx(text: str) -> list[str]:
    """Strip // comments and join gsSP2Triangles calls that span two source lines."""
    lines = re.sub(r'//[^\n]*', '', text).splitlines()
    out = []
    i = 0
    while i < len(lines):
        line = lines[i].strip().rstrip(',')
        if line.startswith('gsSP2Triangles') and ')' not in line and i + 1 < len(lines):
            line = line + lines[i + 1].strip().rstrip(',')
            i += 1
        out.append(line)
        i += 1
    return out


def parse_gfx(gfx_path: Path, asset_dir: Path, repo_root: Path | None = None) -> dict:
    """Parse a .gfx.inc.c file and return mesh data:

    {
        verts:    [(x, y, z), ...]   Blender-space
        uvs:      [(u_raw, v_raw), ...] parallel to verts
        faces:    [(i, j, k), ...]
        face_tex: [int|None, ...]    texture index per face
        tex_paths:[Path, ...]        ordered list of PNG paths
    }
    """
    verts: list[tuple] = []
    uvs: list[tuple] = []
    faces: list[tuple] = []
    face_tex: list = []
    tex_paths: list[Path] = []

    bank: dict[int, int] = {}       # N64 vertex buffer slot -> verts list index
    current_tex: int | None = None
    vtx_cache: dict[Path, list] = {}

    for line in _preprocess_gfx(gfx_path.read_text()):
        if line.startswith('gsSPVertex'):
            inner = line.split('(', 1)
            if len(inner) < 2:
                continue
            args = inner[1].rstrip(')').split(',')
            if len(args) < 3:
                continue
            sym = args[0].strip()
            try:
                count = int(args[1].strip())
                bank_start = int(args[2].strip())
            except ValueError:
                continue

            array_offset = 0
            if '[' in sym:
                try:
                    array_offset = int(sym.split('[')[1].split(']')[0])
                except ValueError:
                    pass

            f = _symbol_to_file(sym, asset_dir, repo_root)
            if f is None:
                continue
            if f not in vtx_cache:
                vtx_cache[f] = _parse_vtx_file(f)
            vtx_data = vtx_cache[f]

            for k in range(count):
                idx = array_offset + k
                if idx >= len(vtx_data):
                    break
                x, y, z, u, v = vtx_data[idx]
                # CT Y-up (x,y,z) -> Blender Z-up (x,-z,y), raw CT units (no scaling)
                verts.append((float(x), float(-z), float(y)))
                uvs.append((u, v))
                bank[bank_start + k] = len(verts) - 1

        elif line.startswith('gsDPLoadTextureTile'):
            inner = line.split('(', 1)
            if len(inner) < 2:
                continue
            sym = inner[1].rstrip(')').split(',')[0].strip()
            f = _symbol_to_file(sym, asset_dir, repo_root)
            if f is not None:
                if f not in tex_paths:
                    tex_paths.append(f)
                current_tex = tex_paths.index(f)

        elif line.startswith('gsSP1Triangle'):
            inner = line.split('(', 1)
            if len(inner) < 2:
                continue
            args = inner[1].rstrip(')').split(',')
            try:
                tri = tuple(bank[int(args[j].strip())] for j in range(3))
                faces.append(tri)
                face_tex.append(current_tex)
            except (KeyError, ValueError, IndexError):
                pass

        elif line.startswith('gsSP2Triangles'):
            inner = line.split('(', 1)
            if len(inner) < 2:
                continue
            args = inner[1].rstrip(')').split(',')
            for t in range(2):
                off = t * 4
                try:
                    tri = tuple(bank[int(args[off + j].strip())] for j in range(3))
                    faces.append(tri)
                    face_tex.append(current_tex)
                except (KeyError, ValueError, IndexError):
                    pass

    return {
        'verts': verts,
        'uvs': uvs,
        'faces': faces,
        'face_tex': face_tex,
        'tex_paths': tex_paths,
    }


def find_gfx_file(repo_root: Path, collision_sym: str) -> Path | None:
    """Given Global_ALPole_collision -> assets/levelGroup/Global/ALPole/ALPole.gfx.inc.c"""
    base = collision_sym[:-len('_collision')] if collision_sym.endswith('_collision') else collision_sym
    if '_' not in base:
        return None
    land_prefix, short_name = base.split('_', 1)
    gfx = Path(repo_root) / 'assets' / 'levelGroup' / land_prefix / short_name / f'{short_name}.gfx.inc.c'
    return gfx if gfx.exists() else None
