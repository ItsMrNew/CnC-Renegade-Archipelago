"""Read stock EVA maps and placed data-disc positions without touching the game.

MIX1 tables, saved MapMgr transforms, PowerUp/BaseGameObj fields and the saved
render matrix are parsed from local files. Reveal-area coordinates are never
used as pickup positions. Run through build_tracker.py --game-data PATH.
"""
from io import BytesIO
import hashlib
import math
from pathlib import Path
import struct

from renegade.data import MISSIONS, WEAPONS
from renegade.native.savefiles import chunks, fields, inspect_save

DISC_DEFINITION = 0x04E27593  # Stock POW_Data_Disc; checked against objects.ddb.


class MixArchive:
    def __init__(self, path):
        self.path = Path(path)
        size = self.path.stat().st_size
        with self.path.open('rb') as stream:
            magic, index, names, _ = struct.unpack('<4sIII', stream.read(16))
            if magic != b'MIX1' or not 16 <= index < names < size:
                raise ValueError('Invalid MIX1 archive')
            stream.seek(index)
            count = struct.unpack('<I', stream.read(4))[0]
            if count > 100000 or index + 4 + count*12 > names:
                raise ValueError('Invalid MIX1 index')
            records = [struct.unpack('<3I', stream.read(12)) for _ in range(count)]
            stream.seek(names)
            if struct.unpack('<I', stream.read(4))[0] != count:
                raise ValueError('MIX1 filename count differs')
            self.entries = {}
            for _, offset, length in records:
                n = stream.read(1)[0]
                raw = stream.read(n)
                if len(raw) != n or not raw.endswith(b'\0') or offset+length > index:
                    raise ValueError('Invalid MIX1 entry')
                name = raw[:-1].decode('cp1252').lower()
                if name in self.entries:
                    raise ValueError('Duplicate MIX1 filename')
                self.entries[name] = offset, length

    def read(self, name):
        offset, length = self.entries[name.lower()]
        with self.path.open('rb') as stream:
            stream.seek(offset)
            return stream.read(length)


def children(raw):
    # Some engine chunks use the container flag for custom opaque data.
    try:
        return list(chunks(raw))
    except ValueError:
        return []


def descendants(raw):
    for kind, size, payload in children(raw):
        yield kind, size, payload
        if size & 0x80000000:
            yield from descendants(payload)


def unique_fields(raw, kind):
    matches = [v for k, _, v in descendants(raw) if k == kind]
    if len(matches) != 1:
        raise ValueError(f'Expected one saved chunk {kind:#x}, got {len(matches)}')
    return dict(fields(matches[0]))


def saved_physics(raw):
    physical = {}
    # Find the enclosing PhysClass with its adjacent saved model.
    def visit(data):
        parts = children(data)
        variables = [v for k,_,v in parts if k == 0x00660055]
        models = [v for k,_,v in parts if k == 0x00660056]
        if variables and models:
            if len(variables) != 1 or len(models) != 1:
                raise ValueError('Ambiguous saved physics object')
            f = dict(fields(variables[0]))
            matrices = [dict(fields(v)) for k,_,v in descendants(models[0]) if k == 0x555040]
            if len(matrices) == 1 and len(matrices[0].get(2,b'')) == 48:
                matrix = struct.unpack('<12f', matrices[0][2])
                xyz = matrix[3], matrix[7], matrix[11]
                if all(math.isfinite(v) for v in xyz):
                    physical[int.from_bytes(f[0],'little')] = xyz
        for _, flags, payload in parts:
            if flags & 0x80000000: visit(payload)
    visit(raw)
    return physical


def object_positions(raw):
    """Map saved game-object IDs to their physical world positions."""
    physical = saved_physics(raw)
    result = {}
    for kind, _, payload in descendants(raw):
        if not 0x40000 <= kind < 0x50000:
            continue
        try:
            base = unique_fields(payload, 910991407)
        except ValueError:
            continue
        position = None
        for chunk_id in (207011121, 922991807):
            # Building.Position / ScriptZone.OBBox (Basis matrix, Center, Extent).
            matches = [v for k,_,v in descendants(payload) if k == chunk_id]
            if len(matches) == 1:
                value = dict(fields(matches[0])).get(1,b'')
                offset = 36 if chunk_id == 922991807 else 0
                if len(value) >= offset+12: position = struct.unpack_from('<3f',value,offset)
        if position is None:
            try:
                ref = unique_fields(payload, 910991146)
            except ValueError:
                continue
            position = physical.get(int.from_bytes(ref.get(10,b''), 'little'))
        if position is not None:
            result[int.from_bytes(base[3], 'little')] = position
    return result


def map_and_discs(raw):
    info = inspect_save(raw)
    metadata = unique_fields(raw, 0x07020522)
    texture = metadata[1].rstrip(b'\0').decode('cp1252').split('\\')[-1]
    center = struct.unpack('<2f', metadata[2])
    scale = struct.unpack('<2f', metadata[3])
    if not all(math.isfinite(v) for v in (*center, *scale)) or min(scale) <= 0:
        raise ValueError('Invalid map calibration')
    physical = saved_physics(raw)
    # CombatManager::TheStar -> ScriptableGameObj referenceable pointer ->
    # PhysicalGameObj physics reference. This is the actual mission-start save
    # position, not the camera position or a guessed edge of the EVA texture.
    stars=[v for k,_,v in descendants(raw) if k==916991712]
    start=None
    if len(stars)==1:
        refs=[dict(fields(v)).get(4) for k,_,v in children(stars[0])]
        if len(refs)==1 and refs[0]:
            soldiers=[]
            for k,_,v in descendants(raw):
                if k!=0x4010E:continue
                if unique_fields(v,627001123).get(1)==refs[0]:
                    ptr=int.from_bytes(unique_fields(v,910991146)[10],'little')
                    soldiers.append(physical.get(ptr))
            if len(soldiers)==1:start=soldiers[0]
    discs = []
    for kind, _, payload in descendants(raw):
        if kind != 0x40106:  # PowerUpGameObj persist factory.
            continue
        base = unique_fields(payload, 910991407)
        if int.from_bytes(base[2], 'little') != DISC_DEFINITION:
            continue
        if base.get(4) != b'\0':
            continue  # Pending-deletion powerups are not placed pickups.
        ref = unique_fields(payload, 910991146)
        pointer = int.from_bytes(ref[10], 'little')
        if pointer not in physical:
            raise ValueError('Data disc has no unambiguous saved position')
        x,y,z = physical[pointer]
        discs.append(dict(object_id=int.from_bytes(base[3], 'little'),
                          world=[x,y,z], pixel=[center[0]+x*scale[0], center[1]-y*scale[1]]))
    return dict(map=info['map_name'], texture=texture, center=center, scale=scale,
                save_sha256=info['sha256'], start=start, discs=sorted(discs,key=lambda d:d['object_id']))


def extract(game_data, destination):
    from PIL import Image
    game_data, destination = Path(game_data), Path(destination)
    archive = MixArchive(game_data/'always.dat')
    definitions = MixArchive(game_data/'always.dbs').read('objects.ddb')
    names = [dict(fields(v)) for k,_,v in descendants(definitions) if k == 0x100]
    assert any(int.from_bytes(f.get(1,b''),'little') == DISC_DEFINITION and
               f.get(3,b'').rstrip(b'\0') == b'POW_Data_Disc' for f in names), 'Stock disc definition differs'
    destination.mkdir(parents=True, exist_ok=True)
    result = []
    for mission in MISSIONS:
        saves = sorted((game_data/'save').glob(f'ap_start_*Soldier*_{mission.map_name}.sav'))
        if not saves:
            raise ValueError('Missing Soldier mission-start save for '+mission.map_name)
        record = map_and_discs(saves[-1].read_bytes())
        if record['start'] is None:
            raise ValueError('Mission-start player position is missing: '+mission.map_name)
        source = Path(record['texture']).with_suffix('.dds').name.lower()
        image = Image.open(BytesIO(archive.read(source)))
        image.load()
        record.update(image=mission.map_name+'.png', size=list(image.size), texture_sha256=hashlib.sha256(archive.read(source)).hexdigest())
        for disc in record['discs']:
            px,py = disc['pixel']
            disc['inside_map'] = 0 <= px < image.width and 0 <= py < image.height
        image.save(destination/record['image'])
        result.append(record)
    return result


def build_weapon_colour_icons(atlas, destination):
    """Split the generated colour atlas into the existing 160x96 UI slots.

    Cell boundaries follow the atlas's actual panel edges. No creative image
    edits happen here: this is the same crop/resize step as stock HUD extraction.
    """
    from PIL import Image
    atlas,destination=Path(atlas),Path(destination)
    source=Image.open(atlas).convert('RGB')
    if source.size!=(1619,972):raise ValueError('Weapon colour atlas dimensions changed; review its cell boundaries')
    xs=(0,329,661,993,1324,1619);ys=(0,196,393,588,782,972)
    destination.mkdir(parents=True,exist_ok=True)
    result={}
    for index,name in enumerate(('Pistol',*WEAPONS)):
        row,column=divmod(index,5)
        rect=(xs[column],ys[row],xs[column+1],ys[row+1])
        filename=name.lower().replace(' ','_')+'.png'
        source.crop(rect).resize((160,96),Image.Resampling.LANCZOS).save(destination/filename)
        result[name]=dict(image=filename,rect=rect)
    return dict(source='tracker/art/weapon-colours.png',sha256=hashlib.sha256(atlas.read_bytes()).hexdigest(),weapons=result)


def extract_player_icon(game_data, destination):
    """Stock EVA Havoc marker: dlgevamaptab.cpp Add_Marker UV (0,0,31,31)."""
    from PIL import Image
    raw = MixArchive(Path(game_data)/'always.dat').read('mapicons.dds')
    source = Image.open(BytesIO(raw)).convert('RGBA')
    if source.size != (64, 64):
        raise ValueError('Stock EVA marker atlas dimensions changed')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.crop((0, 0, 31, 31)).save(destination)
    return dict(texture='mapicons.dds', rect=[0, 0, 31, 31],
                sha256=hashlib.sha256(raw).hexdigest(),
                source='EA Code/Commando/dlgevamaptab.cpp:103')


def extract_weapon_icons(game_data, destination):
    """Crop the stock inventory artwork using each weapon definition's UV rect."""
    from PIL import Image, ImageDraw
    from renegade.native.weapons import CATALOGUE
    game_data, destination = Path(game_data), Path(destination)
    archive = MixArchive(game_data/'always.dat')
    definitions = MixArchive(game_data/'always.dbs').read('objects.ddb')
    presets = {}
    for kind, _, payload in descendants(definitions):
        if kind != 0x100101 or not any(k == 1205091654 for k,_,_ in children(payload)):
            continue
        meta = unique_fields(payload, 0x100)
        presets[meta[3].rstrip(b'\0').decode('cp1252')] = unique_fields(payload, 1205091654)
    destination.mkdir(parents=True,exist_ok=True)
    result = {}
    for weapon in CATALOGUE:
        definition = presets[weapon.preset]
        texture = Path(definition[39].rstrip(b'\0').decode('cp1252').split('\\')[-1]).with_suffix('.dds').name
        rect = tuple(round(v) for v in struct.unpack('<4f',definition[40]))
        source = Image.open(BytesIO(archive.read(texture))).convert('RGBA')
        left,top,right,bottom = rect
        if not 0 <= left < right <= source.width or not 0 <= top < bottom <= source.height:
            raise ValueError('Invalid icon rectangle: '+weapon.name)
        icon = source.crop(rect)
        icon.thumbnail((144,74),Image.Resampling.LANCZOS)
        output = Image.new('RGBA',(160,96),'#182532')
        draw = ImageDraw.Draw(output)
        draw.rounded_rectangle((1,1,158,94),radius=10,outline='#6D8BE8',width=2)
        output.alpha_composite(icon,((160-icon.width)//2,(96-icon.height)//2))
        filename=weapon.name.lower().replace(' ','_')+'.png'
        output.save(destination/filename)
        result[weapon.name]=dict(image=filename,texture=texture,rect=rect,
                                definition=weapon.preset,definition_id=weapon.ids[0])
    return result


def floor_triangles(raw, z_min=-1000, z_max=1000, *, floorplan=False):
    """Read world-space upward faces from stock static W3D terrain.

    W3dMeshHeader3 / W3dTriangleStruct are defined in EA's ww3d2/w3d_file.h.
    Reject transformed hierarchies instead of silently plotting local vertices.
    """
    result = []
    for kind, _, payload in children(raw):
        if kind == 0x100:
            for k, _, value in children(payload):
                if k == 0x102:
                    if len(value) != 60:
                        raise ValueError('Terrain has a nontrivial bone hierarchy')
                    translation = struct.unpack_from('<3f', value, 20)
                    rotation = struct.unpack_from('<4f', value, 44)
                    if any(abs(v) > 0.0001 for v in translation) or any(abs(v-e) > .0001 for v,e in zip(rotation,(0,0,0,1))):
                        raise ValueError('Terrain has a transformed root')
        if kind != 0: continue
        parts = {k:v for k,_,v in children(payload)}
        if 2 not in parts or 0x20 not in parts: continue
        header = parts.get(0x1f,b'')
        if len(header)<8: raise ValueError('Missing W3D mesh header')
        attributes = struct.unpack_from('<I',header,4)[0]
        name=header[8:24].split(b'\0')[0].decode('ascii',errors='replace').upper()
        if floorplan:
            # Collision blockers, overhead fixtures and furniture are physical
            # geometry too, but obscure the actual corridors in a top-down plan.
            if any(word in name for word in ('BLOCKER','BLCKER','CEIL','ROOF','LIT FIX','LT FXTR','WORKST','WORK_DIS','WIN SILL','MUTIE CELL','DRILL','PRI_OB')):
                continue
            # These two cave meshes are the upper shell (z -51 to -72), above
            # the cave floor/bridge at z -78 to -104. Keep the floor meshes.
            if name in ('CV_AREA01','CV_AREA05'):
                continue
        # Exclude nonphysical backdrop/occlusion polygons and invisible blockers.
        # Otherwise e.g. S_LAB BLCKER draws a huge triangle across the M08 plan.
        if not attributes & 0x10 or attributes & 0x1000: continue
        vertices = list(struct.iter_unpack('<3f',parts[2]))
        for triangle in struct.iter_unpack('<4I4f',parts[0x20]):
            if triangle[6] < .45: continue
            points = [vertices[i] for i in triangle[:3]]
            z = sum(p[2] for p in points)/3
            if z_min <= z < z_max: result.append((z,points))
    return result
