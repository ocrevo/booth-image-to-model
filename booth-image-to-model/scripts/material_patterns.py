"""Validated metre-based procedural surface patterns; no external image assets.

Only Blender graph construction/inspection uses its node API. Validation is
standard-library Python and shared by the spec validator and native builders.
"""
from __future__ import annotations
import copy
import math
from common import finite_vector,material_rgba
from geometry import positive,mesh_for,transform_point

MATERIAL_SETS=('neutral','projected')
SOURCE_IMAGE='Projected source'   # name of the picture datablock blender_build loads for the projected set
UV_LAYER='ProjectedUV'            # UV map the UV Project modifiers write and the projected nodes read


def grid_parameters(material,component):
    if 'pattern' not in material:return None
    pattern=material['pattern']
    required={'type','spacing','line_width','line_color'}
    allowed=required|{'coordinates','offset','arc_origin_deg'}
    if not isinstance(pattern,dict) or required-set(pattern) or set(pattern)-allowed:
        raise ValueError('grid pattern requires type, spacing, line_width, line_color; unknown pattern fields are rejected')
    if pattern['type']!='grid':raise ValueError('only the procedural grid pattern is supported')
    if not finite_vector(pattern['spacing'],2) or min(pattern['spacing'])<=0:
        raise ValueError('grid spacing requires two positive finite metre values')
    width=positive('grid line_width',pattern['line_width'])
    if width>=min(pattern['spacing']):raise ValueError('grid line_width must be smaller than both spacing values')
    color=pattern['line_color']
    if not finite_vector(color,3) or not all(0<=v<=1 for v in color):
        raise ValueError('grid line_color requires three RGB values in [0,1], in material.color_space')
    mode=pattern.get('coordinates','auto')
    if mode not in ('auto','xy','xz','yz','arc_length_z'):
        raise ValueError('unsupported grid coordinate mode')
    if mode=='auto':mode='arc_length_z' if component['kind']=='arc_panel' else 'xz'
    offset=pattern.get('offset',[0,0])
    if not finite_vector(offset,2):raise ValueError('grid offset requires two finite metre values')
    origin=pattern.get('arc_origin_deg',0)
    if isinstance(origin,bool) or not isinstance(origin,(int,float)) or not math.isfinite(origin):
        raise ValueError('grid arc_origin_deg must be finite')
    if mode!='arc_length_z' and 'arc_origin_deg' in pattern:
        raise ValueError('grid arc_origin_deg is only used by arc_length_z coordinates')
    radius=None
    if mode=='arc_length_z':
        if component['kind']!='arc_panel':raise ValueError('arc_length_z grid coordinates require arc_panel geometry')
        p=component['params'];radius=positive('radius',p['radius'])
        span=abs(p['end_deg']-p['start_deg'])
        midpoint=(p['start_deg']+p['end_deg'])/2-origin
        midpoint=(midpoint+180)%360-180
        if midpoint-span/2<-180 or midpoint+span/2>180:
            raise ValueError('arc grid crosses its angular seam; choose a shared arc_origin_deg near the panel group centre')
    return {'spacing':list(pattern['spacing']),'line_width':width,
            'line_color':material_rgba({'color':color+[1],'color_space':material.get('color_space','linear')}),
            'coordinates':mode,'offset':list(offset),'arc_origin_deg':origin,'radius':radius}


def apply_grid(nodes,links,shader,material,component):
    """Attach a repeatable grid to local unscaled coordinates, in metres.

    Arc panels unwrap polar angle into centreline arc length. A shared angular
    origin and offset keep separate material segments aligned. This is surface
    colour, not an invented perforation or fabrication hole.
    """
    p=grid_parameters(material,component)
    if p is None:return
    def node(name,kind):
        result=nodes.new(kind);result.name='grid_'+name;return result
    def math_node(name,operation,left,right=None):
        result=node(name,'ShaderNodeMath');result.operation=operation
        for index,value in enumerate((left,right)):
            if value is None:continue
            if isinstance(value,(int,float)):result.inputs[index].default_value=value
            else:links.new(value,result.inputs[index])
        return result.outputs[0]
    tex=node('coordinates','ShaderNodeTexCoord');separate=node('axes','ShaderNodeSeparateXYZ')
    links.new(tex.outputs['Object'],separate.inputs[0])
    axis={name:separate.outputs[name] for name in ('X','Y','Z')}
    if p['coordinates']=='arc_length_z':
        angle=math.radians(p['arc_origin_deg']);c,s=math.cos(angle),math.sin(angle)
        x=math_node('rotated_x','ADD',math_node('x_cos','MULTIPLY',axis['X'],c),math_node('y_sin','MULTIPLY',axis['Y'],s))
        y=math_node('rotated_y','SUBTRACT',math_node('y_cos','MULTIPLY',axis['Y'],c),math_node('x_sin','MULTIPLY',axis['X'],s))
        u=math_node('arc_length','MULTIPLY',math_node('polar_angle','ARCTAN2',y,x),p['radius'])
        coordinates=(u,axis['Z'])
    else:coordinates=tuple(axis[a.upper()] for a in p['coordinates'])
    masks=[]
    for index,(name,value) in enumerate(zip(('u','v'),coordinates)):
        shifted=math_node(name+'_offset','ADD',value,p['offset'][index])
        frequency=math_node(name+'_frequency','MULTIPLY',shifted,1/p['spacing'][index])
        fraction=math_node(name+'_fraction','FRACT',frequency)
        distance=math_node(name+'_distance','ABSOLUTE',math_node(name+'_centred','SUBTRACT',fraction,.5))
        masks.append(math_node(name+'_line','GREATER_THAN',distance,.5-p['line_width']/(2*p['spacing'][index])))
    mask=math_node('union','MAXIMUM',*masks)
    mix=node('color','ShaderNodeMixRGB');mix.blend_type='MIX'
    mix.inputs[1].default_value=material_rgba(material);mix.inputs[2].default_value=p['line_color']
    links.new(mask,mix.inputs[0]);links.new(mix.outputs[0],shader.inputs['Base Color'])
    links.new(mix.outputs[0],shader.inputs['Emission Color'])


def projected_parameters(material,component):
    """Projected pattern: the source picture seen through the fitted spec camera. No fields; the part keeps its own alpha."""
    pattern=material.get('pattern')
    if not isinstance(pattern,dict) or set(pattern)!={'type'} or pattern['type']!='projected':
        raise ValueError('projected pattern takes only {"type": "projected"}')
    return {'uv_layer':UV_LAYER,'alpha':material_rgba(material)[3]}


def apply_projected(nodes,links,shader,material,component):
    """Colour from the source picture through the UV map written by the UV Project modifier (spec camera as projector).

    The Alpha input is set from the part colour and never linked, so glass stays translucent. Faces the camera
    does not see, or that other parts hide, get the wrong pixels; that is accepted for this comparison set.
    """
    p=projected_parameters(material,component)
    import bpy
    image=bpy.data.images.get(SOURCE_IMAGE)
    if image is None:raise RuntimeError('projected material set needs the picture datablock '+SOURCE_IMAGE)
    def node(name,kind):
        result=nodes.new(kind);result.name='projected_'+name;return result
    uv=node('uv','ShaderNodeUVMap');uv.uv_map=p['uv_layer']
    tex=node('source','ShaderNodeTexImage');tex.image=image;tex.interpolation='Linear';tex.extension='EXTEND'
    links.new(uv.outputs['UV'],tex.inputs['Vector'])
    links.new(tex.outputs['Color'],shader.inputs['Base Color'])
    links.new(tex.outputs['Color'],shader.inputs['Emission Color'])
    shader.inputs['Alpha'].default_value=p['alpha']


# --- dots and grain ---
# Node descriptions are plain data (no bpy): nodes {name, type, operation, blend_type, settings, inputs}, links
# (from node, from output, to node, to input) and the colour output. The parameter functions validate; the graph
# functions turn the parameters into a description; build_graph instantiates a description in Blender. Tests read the
# description, so the maths of a pattern is checked without Blender.
DOT_AXES=('x','y','z')


class _Graph:
    """Collects nodes and links of one pattern; every node name starts with the pattern's prefix (dots_, grain_)."""
    def __init__(self,prefix):
        self.prefix=prefix;self.nodes=[];self.links=[];self.color=None

    def add(self,name,kind,operation=None,blend_type=None,settings=None,inputs=None):
        node={'name':self.prefix+name,'type':kind,'operation':operation,'blend_type':blend_type,
              'settings':dict(settings or {}),'inputs':dict(inputs or {})}
        self.nodes.append(node);return node

    def connect(self,source,target,key):
        self.links.append((source[0],source[1],target['name'],key))

    def math(self,name,operation,left,right=None):
        """A maths node; each side is a number or the (node name, output) of an earlier node. Returns its output reference."""
        node=self.add(name,'ShaderNodeMath',operation=operation)
        for index,value in enumerate((left,right)):
            if value is None:continue
            if isinstance(value,tuple):self.connect(value,node,index)
            else:node['inputs'][index]=value
        return (node['name'],0)

    def result(self):
        return {'nodes':self.nodes,'links':self.links,'color':self.color}


def build_graph(graph,nodes,links,shader):
    """Create a described graph in a Blender material and send its colour output to the shader's Base Color and Emission Color."""
    created={}
    for described in graph['nodes']:
        node=nodes.new(described['type']);node.name=described['name']
        if described['operation']:node.operation=described['operation']
        if described['blend_type']:node.blend_type=described['blend_type']
        for attribute,value in described['settings'].items():setattr(node,attribute,value)
        for key,value in described['inputs'].items():node.inputs[key].default_value=value
        created[described['name']]=node
    for source,output,target,key in graph['links']:
        links.new(created[source].outputs[output],created[target].inputs[key])
    name,output=graph['color']
    for socket in ('Base Color','Emission Color'):links.new(created[name].outputs[output],shader.inputs[socket])


def _metre_value(label,value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError(label+' must be a finite number of metres')
    return float(value)


def _pattern_rgb(label,pattern,key,material):
    color=pattern[key]
    if not finite_vector(color,3) or not all(0<=v<=1 for v in color):
        raise ValueError('%s %s requires three RGB values in [0,1], in material.color_space'%(label,key))
    return material_rgba({'color':color+[1],'color_space':material.get('color_space','linear')})


def dots_parameters(material,component):
    """Validate a dots pattern: round dots in rows, in metres. None when the material has no pattern.

    Dots sit at the centres of the cells of the spacing grid (offset slides the grid) and have radius radius_m, which must be
    smaller than half the smaller spacing so neighbours never touch. fade (or null) shrinks the radius linearly along an
    object axis from its full size at from_m to nothing at to_m. coordinates: auto, xy, xz, yz, arc_length_z (arc_panel only)
    or box (per shading point, the plane whose axis is the main axis of the surface normal, so facades and undersides both
    carry dots).
    """
    if 'pattern' not in material:return None
    pattern=material['pattern']
    required={'type','spacing','radius_m','dot_color'}
    allowed=required|{'coordinates','offset','arc_origin_deg','fade'}
    if not isinstance(pattern,dict) or required-set(pattern) or set(pattern)-allowed:
        raise ValueError('dots pattern requires type, spacing, radius_m, dot_color; optional coordinates, offset, arc_origin_deg, fade; unknown pattern fields are rejected')
    if pattern['type']!='dots':raise ValueError('not a dots pattern')
    if not finite_vector(pattern['spacing'],2) or min(pattern['spacing'])<=0:
        raise ValueError('dots spacing requires two positive finite metre values')
    radius=positive('dots radius_m',pattern['radius_m'])
    if radius>=min(pattern['spacing'])/2:
        raise ValueError('dots radius_m must be smaller than half of the smaller spacing value')
    color=_pattern_rgb('dots',pattern,'dot_color',material)
    mode=pattern.get('coordinates','auto')
    if mode not in ('auto','xy','xz','yz','arc_length_z','box'):raise ValueError('unsupported dots coordinate mode')
    if mode=='auto':mode='arc_length_z' if component['kind']=='arc_panel' else 'xz'
    offset=pattern.get('offset',[0,0])
    if not finite_vector(offset,2):raise ValueError('dots offset requires two finite metre values')
    origin=pattern.get('arc_origin_deg',0)
    if isinstance(origin,bool) or not isinstance(origin,(int,float)) or not math.isfinite(origin):
        raise ValueError('dots arc_origin_deg must be finite')
    if mode!='arc_length_z' and 'arc_origin_deg' in pattern:
        raise ValueError('dots arc_origin_deg is only used by arc_length_z coordinates')
    arc_radius=None
    if mode=='arc_length_z':
        if component['kind']!='arc_panel':raise ValueError('arc_length_z dots coordinates require arc_panel geometry')
        p=component['params'];arc_radius=positive('radius',p['radius'])
        span=abs(p['end_deg']-p['start_deg'])
        midpoint=(p['start_deg']+p['end_deg'])/2-origin
        midpoint=(midpoint+180)%360-180
        if midpoint-span/2<-180 or midpoint+span/2>180:
            raise ValueError('arc dots cross their angular seam; choose a shared arc_origin_deg near the panel group centre')
    fade=pattern.get('fade')
    if fade is not None:
        if not isinstance(fade,dict) or set(fade)!={'axis','from_m','to_m'}:
            raise ValueError('dots fade is null or {axis, from_m, to_m}')
        if fade['axis'] not in DOT_AXES:raise ValueError('dots fade axis must be x, y or z')
        start=_metre_value('dots fade from_m',fade['from_m']);end=_metre_value('dots fade to_m',fade['to_m'])
        if start==end:raise ValueError('dots fade from_m and to_m must differ')
        fade={'axis':fade['axis'],'from_m':start,'to_m':end}
    return {'spacing':list(pattern['spacing']),'radius_m':radius,'dot_color':color,'base_color':material_rgba(material),
            'coordinates':mode,'offset':list(offset),'arc_origin_deg':origin,'arc_radius':arc_radius,'fade':fade}


def dots_radius(p,value):
    """Dot radius (m) where the object coordinate along the fade axis is value: full at from_m, zero at to_m, linear between."""
    fade=p['fade']
    if fade is None:return p['radius_m']
    t=(fade['to_m']-value)/(fade['to_m']-fade['from_m'])
    return p['radius_m']*min(1.0,max(0.0,t))


def box_plane(normal):
    """Plane (yz, xz or xy) of the main axis of a surface normal, in object space; ties go to x, then y."""
    ax,ay,az=(abs(v) for v in normal)
    if ax>=ay and ax>=az:return 'yz'
    return 'xz' if ay>=az else 'xy'


def dots_graph(p):
    """Node description of a dots pattern: the part colour, with dot_color wherever the point lies within the dot radius of a cell centre."""
    g=_Graph('dots_')
    tex=g.add('coordinates','ShaderNodeTexCoord')
    separate=g.add('axes','ShaderNodeSeparateXYZ');g.connect((tex['name'],'Object'),separate,0)
    axis={name:(separate['name'],name) for name in ('X','Y','Z')}
    if p['coordinates']=='arc_length_z':
        angle=math.radians(p['arc_origin_deg']);c,s=math.cos(angle),math.sin(angle)
        x=g.math('rotated_x','ADD',g.math('x_cos','MULTIPLY',axis['X'],c),g.math('y_sin','MULTIPLY',axis['Y'],s))
        y=g.math('rotated_y','SUBTRACT',g.math('y_cos','MULTIPLY',axis['Y'],c),g.math('x_sin','MULTIPLY',axis['X'],s))
        u=g.math('arc_length','MULTIPLY',g.math('polar_angle','ARCTAN2',y,x),p['arc_radius'])
        v=axis['Z']
    elif p['coordinates']=='box':
        normal=g.add('normal_axes','ShaderNodeSeparateXYZ');g.connect((tex['name'],'Normal'),normal,0)
        ax,ay,az=(g.math('abs_n'+name.lower(),'ABSOLUTE',(normal['name'],name)) for name in ('X','Y','Z'))
        y_over_x=g.math('y_over_x','GREATER_THAN',ay,ax)
        z_over_x=g.math('z_over_x','GREATER_THAN',az,ax)
        z_over_y=g.math('z_over_y','GREATER_THAN',az,ay)
        select_x=g.math('select_x','MULTIPLY',g.math('not_y_over_x','SUBTRACT',1,y_over_x),g.math('not_z_over_x','SUBTRACT',1,z_over_x))
        select_y=g.math('select_y','MULTIPLY',y_over_x,g.math('not_z_over_y','SUBTRACT',1,z_over_y))
        select_z=g.math('select_z','SUBTRACT',g.math('not_x','SUBTRACT',1,select_x),select_y)
        # yz plane: (y, z); xz plane: (x, z); xy plane: (x, y)
        u=g.math('u_plane','ADD',axis['X'],g.math('u_swap','MULTIPLY',select_x,g.math('y_minus_x','SUBTRACT',axis['Y'],axis['X'])))
        v=g.math('v_plane','ADD',axis['Z'],g.math('v_swap','MULTIPLY',select_z,g.math('y_minus_z','SUBTRACT',axis['Y'],axis['Z'])))
    else:
        u,v=(axis[a.upper()] for a in p['coordinates'])
    squares=[]
    for name,value,shift,spacing in (('u',u,p['offset'][0],p['spacing'][0]),('v',v,p['offset'][1],p['spacing'][1])):
        shifted=g.math(name+'_offset','ADD',value,shift)
        frequency=g.math(name+'_frequency','MULTIPLY',shifted,1/spacing)
        fraction=g.math(name+'_fraction','FRACT',frequency)
        metres=g.math(name+'_metres','MULTIPLY',g.math(name+'_centred','SUBTRACT',fraction,.5),spacing)
        squares.append(g.math(name+'_square','MULTIPLY',metres,metres))
    distance=g.math('distance','SQRT',g.math('distance_squared','ADD',*squares))
    fade=p['fade']
    if fade is None:radius=p['radius_m']
    else:
        remaining=g.math('fade_remaining','SUBTRACT',fade['to_m'],axis[fade['axis'].upper()])
        scaled=g.math('fade_fraction','MULTIPLY',remaining,1/(fade['to_m']-fade['from_m']))
        radius=g.math('fade_radius','MULTIPLY',g.math('fade_ceiling','MINIMUM',g.math('fade_floor','MAXIMUM',scaled,0.0),1.0),p['radius_m'])
    mask=g.math('mask','LESS_THAN',distance,radius)
    mix=g.add('color','ShaderNodeMixRGB',blend_type='MIX',inputs={1:list(p['base_color']),2:list(p['dot_color'])})
    g.connect(mask,mix,0)
    g.color=(mix['name'],0)
    return g.result()


def apply_dots(nodes,links,shader,material,component):
    """Round dots in rows, drawn on the part itself (surface colour, not a hole): see dots_parameters."""
    p=dots_parameters(material,component)
    if p is None:return
    build_graph(dots_graph(p),nodes,links,shader)


GRAIN_CONTRAST=3.0   # the noise texture stays near its middle, so its deviation from .5 is multiplied by this and clamped to [0,1]


def grain_parameters(material,component):
    """Validate a grain pattern (carpet-like speckle): None when the material has no pattern."""
    if 'pattern' not in material:return None
    pattern=material['pattern']
    required={'type','scale_m','strength','grain_color'}
    if not isinstance(pattern,dict) or required-set(pattern) or set(pattern)-required:
        raise ValueError('grain pattern requires type, scale_m, strength, grain_color; unknown pattern fields are rejected')
    if pattern['type']!='grain':raise ValueError('not a grain pattern')
    scale=positive('grain scale_m',pattern['scale_m'])
    strength=pattern['strength']
    if isinstance(strength,bool) or not isinstance(strength,(int,float)) or not math.isfinite(strength) or not 0<=strength<=1:
        raise ValueError('grain strength must be a number from 0 to 1')
    return {'scale_m':scale,'frequency':1/scale,'strength':strength,'grain_color':_pattern_rgb('grain',pattern,'grain_color',material),
            'base_color':material_rgba(material)}


def grain_graph(p):
    """Node description of a grain pattern: a 3D noise in object coordinates (metres) mixes the part colour with grain_color."""
    g=_Graph('grain_')
    tex=g.add('coordinates','ShaderNodeTexCoord')
    noise=g.add('noise','ShaderNodeTexNoise',settings={'noise_dimensions':'3D'},inputs={'Scale':p['frequency'],'Detail':6.0,'Roughness':.6})
    g.connect((tex['name'],'Object'),noise,'Vector')
    deviation=g.math('deviation','SUBTRACT',(noise['name'],'Fac'),.5)
    contrast=g.math('contrast','ADD',g.math('stretch','MULTIPLY',deviation,GRAIN_CONTRAST),.5)
    clamped=g.math('ceiling','MINIMUM',g.math('floor','MAXIMUM',contrast,0.0),1.0)
    factor=g.math('strength','MULTIPLY',clamped,p['strength'])
    mix=g.add('color','ShaderNodeMixRGB',blend_type='MIX',inputs={1:list(p['base_color']),2:list(p['grain_color'])})
    g.connect(factor,mix,0)
    g.color=(mix['name'],0)
    return g.result()


def apply_grain(nodes,links,shader,material,component):
    """Colour noise in metres on the part itself (carpet-like speckle): see grain_parameters."""
    p=grain_parameters(material,component)
    if p is None:return
    build_graph(grain_graph(p),nodes,links,shader)


PATTERNS={'grid':(grid_parameters,apply_grid),'projected':(projected_parameters,apply_projected),
          'dots':(dots_parameters,apply_dots),'grain':(grain_parameters,apply_grain)}


def pattern_parameters(material,component):
    """Validate the pattern of one material through the table; None when it has none. Unknown types list the known ones."""
    if 'pattern' not in material:return None
    pattern=material['pattern']
    kind=pattern.get('type') if isinstance(pattern,dict) else None
    if not isinstance(kind,str) or kind not in PATTERNS:
        raise ValueError('unknown pattern type %r; known types: %s'%(kind,', '.join(sorted(PATTERNS))))
    return PATTERNS[kind][0](material,component)


def apply_pattern(nodes,links,shader,material,component):
    """Build the pattern nodes of one material through the table (validation first)."""
    if pattern_parameters(material,component) is None:return
    PATTERNS[material['pattern']['type']][1](nodes,links,shader,material,component)


def projected_parts(spec):
    """Ids of the parts whose material is projected, whatever wrote it (the spec file or a material set)."""
    return [c['id'] for c in spec['components']
            if isinstance(c['material'].get('pattern'),dict) and c['material']['pattern'].get('type')=='projected']


def has_projected(spec):
    return bool(projected_parts(spec))


def material_set(spec,name):
    """The spec as a material set sees it, in memory only: neutral is the spec itself, projected a copy with every part projected."""
    if name not in MATERIAL_SETS:raise ValueError('unknown material set %r; known sets: %s'%(name,', '.join(MATERIAL_SETS)))
    if name=='neutral':return spec
    out=copy.deepcopy(spec)
    for component in out['components']:component['material']['pattern']={'type':'projected'}
    return out


def grid_graph_signature(tree):
    """Canonical native pattern graph, including constants and shader links."""
    def value(socket):
        if not hasattr(socket,'default_value'):return None
        data=socket.default_value
        if isinstance(data,(float,int)):return round(float(data),6)
        try:return [round(float(v),6) for v in data]
        except TypeError:return str(data)
    nodes=[]
    for node in tree.nodes:
        if not node.name.startswith('grid_'):continue
        nodes.append({'name':node.name,'type':node.bl_idname,
                      'operation':getattr(node,'operation',None),'blend_type':getattr(node,'blend_type',None),
                      'inputs':[(s.name,value(s)) for s in node.inputs]})
    links=[(link.from_node.name,link.from_socket.name,link.to_node.name,link.to_socket.name)
           for link in tree.links if link.from_node.name.startswith('grid_') or link.to_node.name.startswith('grid_')]
    return {'nodes':sorted(nodes,key=lambda n:n['name']),'links':sorted(links)}


PATTERN_PREFIX={'grid':'grid_','dots':'dots_','grain':'grain_','projected':'projected_'}   # node-name prefix of each pattern family
PROJECTED_MODIFIER='PROJECTED_UV'    # name of the UV Project modifier blender_build gives every projected mesh part
PROJECTED_MODIFIER_TYPE='UV_PROJECT'


def prefixed_graph_signature(tree,prefix):
    """Canonical graph of the nodes whose names start with prefix: type, operation, blend mode, texture and map settings,
    constants and links. The grid keeps its own function (grid_graph_signature) so its signature never changes."""
    def value(socket):
        if not hasattr(socket,'default_value'):return None
        data=socket.default_value
        if isinstance(data,(float,int)):return round(float(data),6)
        try:return [round(float(v),6) for v in data]
        except TypeError:return str(data)
    nodes=[]
    for node in tree.nodes:
        if not node.name.startswith(prefix):continue
        image=getattr(node,'image',None)
        nodes.append({'name':node.name,'type':node.bl_idname,'operation':getattr(node,'operation',None),'blend_type':getattr(node,'blend_type',None),
                      'noise_dimensions':getattr(node,'noise_dimensions',None),'uv_map':getattr(node,'uv_map',None),
                      'interpolation':getattr(node,'interpolation',None),'extension':getattr(node,'extension',None),
                      'image':getattr(image,'name',None),'inputs':[(s.name,value(s)) for s in node.inputs]})
    links=[(link.from_node.name,link.from_socket.name,link.to_node.name,link.to_socket.name)
           for link in tree.links if link.from_node.name.startswith(prefix) or link.to_node.name.startswith(prefix)]
    return {'nodes':sorted(nodes,key=lambda n:n['name']),'links':sorted(links)}


def dots_graph_signature(tree):return prefixed_graph_signature(tree,PATTERN_PREFIX['dots'])
def grain_graph_signature(tree):return prefixed_graph_signature(tree,PATTERN_PREFIX['grain'])
def projected_graph_signature(tree):return prefixed_graph_signature(tree,PATTERN_PREFIX['projected'])


SIGNATURES={'grid':grid_graph_signature,'dots':dots_graph_signature,'grain':grain_graph_signature,'projected':projected_graph_signature}


def pattern_mismatch(actual,expected,kind):
    """True when the pattern nodes of a reopened material tree differ from a freshly built one.

    kind is the pattern type of the part, or None when it has no pattern. The signature of that type is compared (looked up in
    SIGNATURES, like the builders in PATTERNS); nodes of any other pattern family on the tree count as a mismatch."""
    if kind is not None and kind not in SIGNATURES:
        raise ValueError('unknown pattern type %r; known types: %s'%(kind,', '.join(sorted(SIGNATURES))))
    signature=SIGNATURES['grid' if kind is None else kind]
    foreign=tuple(prefix for name,prefix in PATTERN_PREFIX.items() if name!=kind)
    if any(node.name.startswith(foreign) for node in actual.nodes):return True
    return signature(actual)!=signature(expected)


def expected_modifiers(look,ident,skipped=()):
    """[(name, type)] of the modifiers a reopened part should carry: the UV Project modifier of a projected part (unless the
    build listed it as skipped), nothing else."""
    if ident in skipped:return []
    return [(PROJECTED_MODIFIER,PROJECTED_MODIFIER_TYPE)] if ident in projected_parts(look) else []


# --- Looks: neutral (the default, nothing changes) and dark_hall (dark world and floor; glowing parts light their surroundings) ---
LOOKS=('neutral','dark_hall')
SPOT_WATT_PER_EMISSION_M2=4000.0  # derived spot power (W) = this x part emission x lamp disc area (m2); a first estimate, tune by eye in Cycles
SPOT_CONE_DEG=70.0                # full cone angle of a derived spot light
SPOT_BLEND=0.5
LIGHT_OFFSET_M=0.01               # a derived spot sits this far below the lamp body so it never starts inside the part
FLOOR_DROP_M=0.002                # the floor plate lies this far below the lowest point of the booth (no coplanar faces)


def dark_hall_settings():
    """The dark_hall look as a plain dict (a fresh copy each call); blender_build applies it, nothing here needs bpy.

    Glowing parts light the hall through their own surface: in this look their emission strength is multiplied by
    emission_multiplier and Cycles treats the glowing mesh as a light. Only spotlight parts also get a derived spot.
    """
    return {'world_color':0.02,'world_strength':1.0,'floor_color':0.05,'floor_roughness':0.7,
            'rig_energy_scale':0.12,'emission_multiplier':10.0,'preview_sample_cap':32,'exposure_offset':0}


def look_settings(look):
    """Settings of a look; None for neutral (nothing is changed)."""
    if look not in LOOKS:raise ValueError('unknown look %r; known looks: %s'%(look,', '.join(LOOKS)))
    return None if look=='neutral' else dark_hall_settings()


def rig_energy_scale(look):
    """Factor on the energy of the render rig lights: 1.0 for neutral."""
    settings=look_settings(look)
    return 1.0 if settings is None else settings['rig_energy_scale']


def dark_hall_floor(bounds):
    """Floor plate under the booth: location and size (x, y) in metres, from the booth bounds [[min xyz],[max xyz]]."""
    low,high=bounds;spans=[b-a for a,b in zip(low,high)]
    margin=max(2.0,max(spans))
    return {'location':[(low[0]+high[0])/2,(low[1]+high[1])/2,low[2]-FLOOR_DROP_M],'size':[spans[0]+2*margin,spans[1]+2*margin]}


def _emission(component):
    value=component['material'].get('emission',0)
    return value if isinstance(value,(int,float)) and not isinstance(value,bool) and value>0 else 0


def emissive_parts(spec):
    """Ids of the parts with material emission > 0, text included."""
    return [c['id'] for c in spec['components'] if _emission(c)]


def _local_extent(component):
    """Local bounding box (min, max) of one part from its mesh; None for kinds with no mesh (text)."""
    try:vertices,_=mesh_for(component)
    except ValueError:return None
    if not vertices:return None
    return [min(v[i] for v in vertices) for i in range(3)],[max(v[i] for v in vertices) for i in range(3)]


def _spot_for(component):
    """(light dict, None) for a spotlight part, or (None, reason) when no light can be derived from its shape."""
    box=_local_extent(component)
    if box is None:return None,'the part has no mesh, so there is no lamp body to put a spot light under'
    low,high=box;extent=[b-a for a,b in zip(low,high)]
    location,rotation=component['location'],component['rotation_deg']
    centre=transform_point([(a+b)/2 for a,b in zip(low,high)],location,rotation)
    bottom=min(transform_point([x,y,z],location,rotation)[2] for x in (low[0],high[0]) for y in (low[1],high[1]) for z in (low[2],high[2]))
    area=math.pi*(max(extent[0],extent[1])/2)**2
    colour=[float(v) for v in material_rgba(component['material'])[:3]]
    position=[round(float(v),9)+0.0 for v in (centre[0],centre[1],bottom-LIGHT_OFFSET_M)]
    return {'type':'SPOT','part':component['id'],'position':position,'direction':[0,0,-1],'spot_angle_deg':SPOT_CONE_DEG,
            'spot_blend':SPOT_BLEND,'color':colour,'power_w':SPOT_WATT_PER_EMISSION_M2*_emission(component)*area},None


def dark_hall_lights(spec):
    """Lights derived from the spec for the dark_hall look, as plain dicts (in memory only; nothing is written to the spec).

    Only parts with role spotlight and emission > 0 give a light: one downward SPOT at the lower end of the lamp body, power
    from emission and the lamp disc area. Every other glowing part (strips, frames, rings, text) gets no derived light: in this
    look its emission strength is multiplied instead (dark_hall_settings), so it lights the hall from its real glowing surface.
    """
    lights=[]
    for component in spec['components']:
        if component.get('role')=='spotlight' and _emission(component):
            light,_=_spot_for(component)
            if light is not None:lights.append(light)
    return lights


def look_lights(spec,look):
    """Derived lights of a look: none for neutral."""
    if look not in LOOKS:raise ValueError('unknown look %r; known looks: %s'%(look,', '.join(LOOKS)))
    return [] if look=='neutral' else dark_hall_lights(spec)


def look_report(spec,look):
    """What the look did with the glowing parts, for build_report.json: counts and any part it could not handle (with the reason)."""
    if look not in LOOKS:raise ValueError('unknown look %r; known looks: %s'%(look,', '.join(LOOKS)))
    unhandled=[]
    if look!='neutral':
        for component in spec['components']:
            if component.get('role')=='spotlight' and _emission(component):
                light,reason=_spot_for(component)
                if light is None:unhandled.append({'id':component['id'],'reason':reason})
    return {'look':look,'emissive_parts':len(emissive_parts(spec)),'derived_lights':len(look_lights(spec,look)),'unhandled':unhandled}
