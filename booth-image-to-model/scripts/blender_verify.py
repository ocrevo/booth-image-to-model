"""Reopen and inspect the actual .blend in a separate native Blender process."""
from __future__ import annotations
import argparse
import math
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import bpy
from common import read_json,write_json,digest,code_hash,file_sha,material_rgba
from geometry import mesh_for
from material_patterns import apply_pattern,pattern_mismatch,material_set,projected_parts,expected_modifiers,UV_LAYER


def main():
    argv=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
    p=argparse.ArgumentParser();p.add_argument('--spec',required=True);p.add_argument('--blend',required=True);p.add_argument('--report',required=True)
    a=p.parse_args(argv);spec=read_json(a.spec);errors=[]
    if not bpy.app.background:raise RuntimeError('Verification requires a separate background process')
    bpy.ops.wm.open_mainfile(filepath=str(Path(a.blend).resolve()),use_scripts=False)
    scene=bpy.context.scene
    if scene.get('spec_sha256')!=digest(spec):errors.append('stale_spec')
    if scene.get('builder_sha256')!=code_hash():errors.append('stale_builder')
    # The reopened file is compared with the spec as the build saw it: neutral is the spec itself, a projected build the copy with every
    # part projected (build_report.json beside the .blend names the set and lists projected parts it had to skip).
    report_path=Path(a.blend).resolve().parent/'build_report.json'
    build=read_json(report_path) if report_path.is_file() else {}
    set_name=build.get('material_set','neutral')
    try:look=material_set(spec,set_name)
    except (ValueError,TypeError):errors.append('unknown_material_set:'+str(set_name));look=spec
    projected=set(projected_parts(look))
    skipped={item['id'] for item in build.get('projected_uv_skipped') or [] if isinstance(item,dict) and 'id' in item}&projected
    expected={c['id']:c for c in look['components']};observed={}
    geometry_types={'MESH','FONT','CURVE','SURFACE','META','VOLUME','POINTCLOUD','GREASEPENCIL'}
    for obj in scene.objects:
        owner=obj.get('ownership')
        if owner not in ('booth','render_rig'):errors.append('unclassified_object:'+obj.name)
        if owner=='render_rig' and obj.type not in ('CAMERA','LIGHT'):errors.append('rig_contains_geometry:'+obj.name)
        if obj.type in geometry_types:
            ident=obj.get('component_id')
            if owner!='booth' or ident not in expected:errors.append('unplanned_geometry:'+obj.name);continue
            if ident in observed:errors.append('duplicate_component:'+ident)
            observed[ident]=obj;c=expected[ident]
            if obj.hide_render and ident not in skipped:errors.append('hidden_required_component:'+ident)
            if ident in skipped and not obj.hide_render:errors.append('skipped_part_visible:'+ident)
            if obj.get('scope_id')!=c['scope_id']:errors.append('scope_mismatch:'+ident)
            if any(abs(obj.location[i]-c['location'][i])>1e-5 for i in range(3)):errors.append('placement_mismatch:'+ident)
            if any(abs(obj.rotation_euler[i]-math.radians(c['rotation_deg'][i]))>1e-5 for i in range(3)):errors.append('rotation_mismatch:'+ident)
            if any(abs(v-1)>1e-6 for v in obj.scale):errors.append('unplanned_scale:'+ident)
            wanted=expected_modifiers(look,ident,skipped);found=[(m.name,m.type) for m in obj.modifiers]
            extra=list(found)
            for item in wanted:
                if item in extra:extra.remove(item)
            if extra:errors.append('unreviewed_modifiers:'+ident)
            if any(item not in found for item in wanted):errors.append('projected_modifier_missing:'+ident)
            converted=c['kind']=='text' and ident in projected and ident not in skipped
            if ident in projected and ident not in skipped and obj.type=='MESH' and obj.data.uv_layers.get(UV_LAYER) is None:
                errors.append('projected_uv_missing:'+ident)
            if len(obj.data.materials)!=1 or not obj.data.materials[0].use_nodes:
                errors.append('material_missing:'+ident)
            else:
                mat=obj.data.materials[0];shaders=[n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED']
                if len(shaders)!=1:errors.append('material_shader_mismatch:'+ident)
                else:
                    shader=shaders[0];params=c['material'];color=material_rgba(params)
                    for input_name,expected_value in (
                            ('Base Color',color),('Alpha',color[3]),('Roughness',params.get('roughness',.5)),
                            ('Metallic',params.get('metallic',0)),('Emission Color',color),('Emission Strength',params.get('emission',0))):
                        value=shader.inputs[input_name].default_value
                        if isinstance(expected_value,list):matches=all(abs(a-b)<1e-5 for a,b in zip(value,expected_value))
                        else:matches=abs(value-expected_value)<1e-5
                        if not matches:errors.append('material_parameter_mismatch:'+ident+':'+input_name)
                    # Compare the actual reopened graph to the shared pattern builder of its type (PATTERNS:
                    # grid, dots, grain or projected), including coordinate mapping, intervals and links.
                    expected_mat=bpy.data.materials.new('_verification_pattern');expected_mat.use_nodes=True
                    expected_mat.node_tree.nodes.clear()
                    expected_shader=expected_mat.node_tree.nodes.new('ShaderNodeBsdfPrincipled')
                    expected_shader.name=shader.name
                    pattern=params.get('pattern');kind=pattern.get('type') if isinstance(pattern,dict) else None
                    try:
                        apply_pattern(expected_mat.node_tree.nodes,expected_mat.node_tree.links,expected_shader,params,c)
                        if pattern_mismatch(mat.node_tree,expected_mat.node_tree,kind):errors.append('material_pattern_mismatch:'+ident)
                    except (ValueError,KeyError,TypeError,RuntimeError):errors.append('material_pattern_invalid:'+ident)
                    bpy.data.materials.remove(expected_mat)
            if converted:
                if obj.type!='MESH':errors.append('projected_text_not_converted:'+ident)
            elif c['kind']=='text':
                if obj.type!='FONT':errors.append('text_mismatch:'+ident)
                else:
                    params=c['params']
                    if obj.data.body!=params['body']:errors.append('text_mismatch:'+ident)
                    for name,value in (('size',params['size']),('extrude',params.get('extrude',.001))):
                        if not math.isclose(getattr(obj.data,name),value,rel_tol=1e-5,abs_tol=1e-7):
                            errors.append('text_parameter_mismatch:'+ident+':'+name)
                    for name in ('align_x','align_y'):
                        if getattr(obj.data,name)!='CENTER':
                            errors.append('text_alignment_mismatch:'+ident+':'+name)
            elif obj.type!='MESH':errors.append('mesh_type_mismatch:'+ident)
            else:
                v,f=mesh_for(c)
                if len(obj.data.vertices)!=len(v) or len(obj.data.polygons)!=len(f):errors.append('mesh_count_mismatch:'+ident)
                elif any(abs(obj.data.vertices[i].co[j]-v[i][j])>1e-5 for i in range(len(v)) for j in range(3)):errors.append('mesh_shape_mismatch:'+ident)
                elif any(tuple(obj.data.polygons[i].vertices)!=tuple(f[i]) for i in range(len(f))):errors.append('mesh_topology_mismatch:'+ident)
                if c['kind']=='arc_panel' and any(p.use_smooth!=(i<len(f)-2 and i%4<2) for i,p in enumerate(obj.data.polygons)):
                    errors.append('curved_panel_shading_mismatch:'+ident)
                if c['kind']=='profile_extrusion' and any(p.use_smooth for p in obj.data.polygons):
                    errors.append('profile_cap_shading_mismatch:'+ident)
    for ident in set(expected)-set(observed):errors.append('missing_component:'+ident)
    result={'native_blender_reopened':True,'geometry_verified':not errors,'errors':errors,
            'blender_version':bpy.app.version_string,'spec_sha256':digest(spec),'builder_sha256':code_hash(),
            'blend_sha256':file_sha(a.blend),'booth_object_count':len(observed),
            'note':'Native file/geometry verification only. Image fidelity still needs reference QA and visual review.'}
    write_json(a.report,result)
    if errors:raise RuntimeError('Blender verification failed: '+str(errors))

if __name__=='__main__':main()
