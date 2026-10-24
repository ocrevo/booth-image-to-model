"""Build v2 spec in a NEW Blender background process. No venue or ground mesh.

blender --background --factory-startup --disable-autoexec --python-exit-code 1 \
  --python scripts/blender_build.py -- --spec project/spec.json --out runs/001 --render --part-masks

This file requires native Blender. Pure-Python unit tests are not a Blender test.
"""
from __future__ import annotations
import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import bpy
from mathutils import Vector,Quaternion
from bpy_extras.object_utils import world_to_camera_view
from common import read_json,write_json,digest,code_hash,file_sha,render_rig,material_rgba,hd_scale_for
from validate_spec import validate
from geometry import mesh_for
from material_patterns import (apply_pattern,material_set,projected_parts,has_projected,MATERIAL_SETS,SOURCE_IMAGE,UV_LAYER,
                               PROJECTED_MODIFIER,PROJECTED_MODIFIER_TYPE,LOOKS,look_settings,look_lights,look_report,rig_energy_scale,dark_hall_floor)
NEUTRAL_PREVIEW_SAMPLE_CAP=8      # Cycles samples of an iteration preview in the neutral look, whatever --samples says


def arguments():
    argv=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []
    p=argparse.ArgumentParser();p.add_argument('--spec',required=True);p.add_argument('--out',required=True)
    p.add_argument('--render',action='store_true');p.add_argument('--part-masks',action='store_true');p.add_argument('--samples',type=int,default=16)
    p.add_argument('--client-exposure',type=float,default=-1.0,help='Exposure offset in EV, applied only to the customer images (default -1.0; 2.4.0-rc3 and earlier used -0.5)')
    p.add_argument('--preview',action='store_true',help='Fast iteration: render only the source camera to preview_render.png')
    p.add_argument('--preview-engine',choices=('workbench','cycles'),default='workbench')
    p.add_argument('--preview-client-look',action='store_true',help='With --preview: also render the customer look (gray backdrop, display transform, client exposure) to preview_client_look.png; uses Cycles')
    p.add_argument('--hd-scale',type=int,default=1,help='With --render, also write client/inspection *_hd.png at this integer scale (>1)')
    p.add_argument('--hd-samples',type=int,help='Cycles samples for the HD renders (default: --samples)')
    p.add_argument('--hd-max-mp',type=float,default=5.0,help='Cap for an HD frame in megapixels; the HD scale is lowered to fit (default 5)')
    p.add_argument('--hd-views',choices=('client','both'),default='both',help='HD images to write: the customer view only, or customer + second view')
    p.add_argument('--material-set',choices=MATERIAL_SETS,default='neutral',help='neutral: the materials in the spec (default); projected: every part shows the source picture through the fitted camera (in memory only, the spec file is not changed)')
    p.add_argument('--look',choices=LOOKS,default='neutral',help='neutral: the usual world and rig (default); dark_hall: dark world and floor, rig lights dimmed, glowing parts brighter and spotlight parts get a spot light (Cycles iteration previews only; it adds a floor plate)')
    a=p.parse_args(argv)
    if a.look!='neutral' and not a.preview:raise ValueError('--look %s adds a floor plate and lights that are not part of the booth; use it with --preview only'%a.look)
    if a.look!='neutral' and a.preview_engine=='workbench' and not a.preview_client_look:      # (--preview is already required above)
        raise ValueError('--look %s only shows in Cycles (Workbench draws no lights); use --preview-engine cycles'%a.look)
    if a.hd_scale<1 or a.hd_scale>8:raise ValueError('hd-scale must be an integer from 1 to 8')
    if not a.hd_max_mp>0:raise ValueError('hd-max-mp must be positive')
    if a.preview and (a.render or a.part_masks):raise ValueError('--preview replaces --render/--part-masks; run them in a separate final build')
    if a.preview_client_look and not a.preview:raise ValueError('--preview-client-look needs --preview')
    return a


def material(name,params,emission_only=False,component=None,emission_scale=1.0):
    m=bpy.data.materials.new(name);m.use_nodes=True
    n=m.node_tree.nodes;n.clear();out=n.new('ShaderNodeOutputMaterial')
    color=material_rgba(params)
    # Viewport/Workbench colour mirrors the shader so Solid view and previews read correctly.
    m.diffuse_color=color;m.roughness=params.get('roughness',.5);m.metallic=params.get('metallic',0)
    if emission_only:
        shader=n.new('ShaderNodeEmission');shader.inputs['Color'].default_value=color;shader.inputs['Strength'].default_value=1
    else:
        shader=n.new('ShaderNodeBsdfPrincipled');shader.inputs['Base Color'].default_value=color
        shader.inputs['Alpha'].default_value=color[3]
        shader.inputs['Roughness'].default_value=params.get('roughness',.5)
        shader.inputs['Metallic'].default_value=params.get('metallic',0)
        shader.inputs['Emission Color'].default_value=color
        shader.inputs['Emission Strength'].default_value=params.get('emission',0)*emission_scale
        if component is not None:apply_pattern(n,m.node_tree.links,shader,params,component)
    m.node_tree.links.new(shader.outputs[0],out.inputs['Surface']);return m


PROJECTED_UV_NOTE=('projected parts: every projected part gets a UV Project modifier with the spec camera as projector; projected text parts '
                   'are first converted to meshes. A part that could not be converted or projected is hidden from renders and listed '
                   'with its reason in projected_uv_skipped (it is never left textured from a single picture pixel)')


def text_to_mesh(c,curve,collections):
    """Evaluate a text (FONT) part into a mesh datablock named after the part. Returns (mesh, None) or (None, reason).
    On success the temporary object and the curve are removed; on failure the curve is kept for the fallback object."""
    tmp=bpy.data.objects.new(c['id']+'_text_to_mesh',curve)
    try:
        collections['BOOTH_GRAPHICS'].objects.link(tmp)
        bpy.context.view_layer.update()
        mesh=bpy.data.meshes.new_from_object(tmp);mesh.name=c['id']
    except (RuntimeError,ValueError,TypeError,AttributeError) as ex:
        return None,'text to mesh conversion failed: '+str(ex)[:200]
    finally:bpy.data.objects.remove(tmp)
    bpy.data.curves.remove(curve)
    return mesh,None


def prepare_projection(look,source):
    """Load and pack the picture the projected nodes read (once, only if some part of the look is projected)."""
    if not has_projected(look):return False
    image=bpy.data.images.load(str(source),check_existing=False);image.name=SOURCE_IMAGE
    if not image.has_data:_=image.pixels[0]
    image.pack()   # packed so the .blend stays self-contained
    return True


def build_parts(look,basis,collections,emission_scale=1.0):
    """One Blender object per component of the look (the spec with the material set applied). Returns {id: object}."""
    projected=set(projected_parts(look))
    built={};unprojectable={}
    for c in look['components']:
        if c['kind']=='text':
            data=bpy.data.curves.new(c['id'],'FONT');data.body=c['params']['body'];data.size=c['params']['size']
            data.align_x='CENTER';data.align_y='CENTER';data.extrude=c['params'].get('extrude',.001)
            # Keep Blender's built-in font. No container font files are distributed.
            if c['id'] in projected:
                # A FONT object has no UV map to project into: evaluate it into a mesh first (neutral builds keep the FONT object).
                mesh,reason=text_to_mesh(c,data,collections)
                if mesh is None:unprojectable[c['id']]=reason
                else:data=mesh;data.uv_layers.new(name=UV_LAYER)
        else:
            v,f=mesh_for(c);data=bpy.data.meshes.new(c['id']);data.from_pydata(v,[],f);data.update()
            if c['id'] in projected:data.uv_layers.new(name=UV_LAYER)
            if c['kind'] in ('rounded_ring','cylinder'):
                for i,p in enumerate(data.polygons):p.use_smooth=(i%4<2) if c['kind']=='rounded_ring' else (i%3==0)
            elif c['kind']=='arc_panel':
                # Only the curved inner/outer sides interpolate normals;
                # level caps and radial end cuts remain flat.
                for i,p in enumerate(data.polygons):p.use_smooth=i<len(data.polygons)-2 and i%4<2
            elif c['kind']=='swept_strip':
                for i,p in enumerate(data.polygons):p.use_smooth=i<len(data.polygons)-2 and i%4 in (1,3)
            elif c['kind']=='box' and c['params'].get('bevel_radius',0)>0:
                for p in data.polygons:
                    coords=[data.vertices[i].co for i in p.vertices]
                    # Large planar panels stay flat; spherical and cylindrical
                    # bevel patches shade continuously without live modifiers.
                    p.use_smooth=not any(max(v[axis] for v in coords)-min(v[axis] for v in coords)<1e-7 for axis in range(3))
        obj=bpy.data.objects.new(c['id'],data)
        group='BOOTH_GRAPHICS' if c['kind']=='text' else ('BOOTH_STRUCTURE' if c['critical'] else 'BOOTH_FURNITURE')
        collections[group].objects.link(obj);obj.location=c['location'];obj.rotation_euler=[math.radians(x) for x in c['rotation_deg']]
        obj['ownership']='booth';obj['component_id']=c['id'];obj['scope_id']=c['scope_id'];obj['primitive_kind']=c['kind']
        if c.get('group'):obj['group']=c['group']
        obj['dimension_basis']=basis;obj['evidence_status']=c['evidence']['status']
        if c['id'] in unprojectable:obj.hide_render=True;obj['projection_skipped']=unprojectable[c['id']]
        obj.data.materials.append(material(c['id']+'_material',c['material'],component=c,emission_scale=emission_scale));built[c['id']]=obj
    return built


def add_projectors(built,look,camera,resolution):
    """UV Project modifier on each projected mesh part: the spec camera is the projector, aspect ratio from the picture.
    Returns [{id, reason}] for projected parts that got none (a text part that could not become a mesh, or a modifier Blender
    refused). Those parts are hidden from renders: they must never show one picture pixel as a flat colour."""
    skipped=[]
    for ident in projected_parts(look):
        obj=built[ident]
        if obj.type!='MESH':
            obj.hide_render=True
            skipped.append({'id':ident,'reason':obj.get('projection_skipped','not a mesh, so it has no projected UV map')});continue
        try:
            mod=obj.modifiers.new(PROJECTED_MODIFIER,PROJECTED_MODIFIER_TYPE)
            mod.uv_layer=UV_LAYER
            mod.projector_count=1;mod.projectors[0].object=camera
            mod.aspect_x=float(resolution[0]);mod.aspect_y=float(resolution[1])
        except (RuntimeError,TypeError,AttributeError) as ex:
            if PROJECTED_MODIFIER in obj.modifiers:obj.modifiers.remove(obj.modifiers[PROJECTED_MODIFIER])
            obj.hide_render=True
            skipped.append({'id':ident,'reason':'UV Project modifier refused: '+str(ex)[:200]})
    return skipped


def add_look(settings,lights,bounds,world,collections):
    """The dark_hall look: dark world, a dark rough floor plate under the booth, and a spot light for each spotlight part.
    (The other glowing parts light the hall through their own surface; see emission_multiplier in main.)
    All of it belongs to the render rig (never to the booth, never written to the spec). Returns the floor object."""
    background=world.node_tree.nodes.get('Background')
    grey=settings['world_color'];background.inputs['Color'].default_value=(grey,grey,grey,1);background.inputs['Strength'].default_value=settings['world_strength']
    plate=dark_hall_floor(bounds);half=[plate['size'][0]/2,plate['size'][1]/2]
    mesh=bpy.data.meshes.new('LOOK_FLOOR')
    mesh.from_pydata([(-half[0],-half[1],0),(half[0],-half[1],0),(half[0],half[1],0),(-half[0],half[1],0)],[],[(0,1,2,3)]);mesh.update()
    floor=bpy.data.objects.new('LOOK_FLOOR',mesh);collections['RENDER_RIG'].objects.link(floor)
    floor.location=plate['location'];floor['ownership']='render_rig'
    floor.data.materials.append(material('LOOK_FLOOR_material',{'color':[settings['floor_color']]*3+[1],'roughness':settings['floor_roughness'],'metallic':0}))
    for index,light in enumerate(lights):
        name='LOOK_%s_%d'%(light['part'],index)
        data=bpy.data.lights.new(name,'SPOT');data.spot_size=math.radians(light['spot_angle_deg']);data.spot_blend=light['spot_blend'];data.shadow_soft_size=.05
        data.energy=light['power_w'];data.color=light['color']
        obj=bpy.data.objects.new(name,data);collections['RENDER_RIG'].objects.link(obj);obj.location=light['position'];obj['ownership']='render_rig'
        obj.rotation_mode='QUATERNION';obj.rotation_quaternion=Vector(light['direction']).to_track_quat('-Z','Y')
    return floor


def point_camera(obj,target,roll_deg=0):
    q=(Vector(target)-obj.location).to_track_quat('-Z','Y')
    obj.rotation_mode='QUATERNION';obj.rotation_quaternion=q@Quaternion((0,0,1),math.radians(roll_deg))


def main():
    if not bpy.app.background: raise RuntimeError('Use a new background process. Refusing to clear a potentially unsaved interactive scene.')
    a=arguments();spec_path=Path(a.spec).resolve();spec=read_json(spec_path)
    if not math.isfinite(a.client_exposure):raise ValueError('client exposure must be a finite EV offset')
    check=validate(spec,spec_path.parent)
    if not check['valid']:raise ValueError(json.dumps(check,ensure_ascii=False))
    source=(spec_path.parent/spec['source']['image']).resolve()
    # Blender decodes the real input; no Pillow/pip dependency is needed here.
    source_probe=bpy.data.images.load(str(source),check_existing=False)
    try:
        if not source_probe.has_data:_=source_probe.pixels[0]
        source_size=list(source_probe.size)
    finally:bpy.data.images.remove(source_probe)
    check=validate(spec,spec_path.parent,source_size=source_size)
    if not check['valid']:raise ValueError(json.dumps(check,ensure_ascii=False))
    # The material set is applied to a copy in memory; the spec (and the spec.json written below) stays as it is.
    look=material_set(spec,a.material_set)
    if look is not spec:
        look_check=validate(look,spec_path.parent,source_size=source_size)
        if not look_check['valid']:raise ValueError(json.dumps(look_check,ensure_ascii=False))
    out=Path(a.out).resolve();out.mkdir(parents=True,exist_ok=True)
    lock=out/'.build.lock'
    try:os.close(os.open(str(lock),os.O_CREAT|os.O_EXCL|os.O_WRONLY))
    except FileExistsError:raise FileExistsError('Another build is writing this run directory (remove .build.lock only if none is running): '+str(lock))
    stages={};clock=[time.perf_counter()]
    def stage(name):
        now=time.perf_counter();stages[name]=round(stages.get(name,0)+now-clock[0],2);clock[0]=now
    stage('validate_and_setup')
    try:
        blend=out/'booth.blend'
        if blend.exists():raise FileExistsError('Use a fresh run directory; existing .blend is not overwritten')
        write_json(out/'spec.json',spec)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene=bpy.context.scene;scene.unit_settings.system='METRIC';scene.unit_settings.scale_length=1
        scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=max(1,a.samples)
        scene.cycles.use_denoising=True
        # CPU-friendly defaults: stop converged pixels early, denoise, reuse scene data between renders.
        scene.cycles.use_adaptive_sampling=True
        try:scene.cycles.denoiser='OPENIMAGEDENOISE'
        except TypeError:pass
        # OIDN Fast prefilter + Balanced quality: measured ~34 s vs ~60 s per 1.4 MP frame on this class of CPU.
        for attr,value in (('denoising_prefilter','FAST'),('denoising_quality','BALANCED')):
            try:setattr(scene.cycles,attr,value)
            except (AttributeError,TypeError):pass
        scene.render.use_persistent_data=True
        scene.render.resolution_x=spec['source']['width'];scene.render.resolution_y=spec['source']['height'];scene.render.resolution_percentage=100
        scene.render.pixel_aspect_x=1;scene.render.pixel_aspect_y=1;scene.render.film_transparent=True
        scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGBA'
        world=bpy.data.worlds.new('Neutral world, no venue geometry');world.use_nodes=True;scene.world=world
        world.node_tree.nodes.get('Background').inputs['Color'].default_value=(.25,.25,.25,1)
        world.node_tree.nodes.get('Background').inputs['Strength'].default_value=.5
        collections={}
        for name in ('BOOTH_STRUCTURE','BOOTH_FURNITURE','BOOTH_GRAPHICS','RENDER_RIG'):
            col=bpy.data.collections.new(name);scene.collection.children.link(col);collections[name]=col
        # Projection is prepared from what the in-memory look contains, not from the name of the material set.
        prepare_projection(look,source)
        # Looks: neutral changes nothing. dark_hall (previews only) darkens the world, adds a floor plate, dims the rig, makes the glowing
        # parts brighter so Cycles lights the hall from their real surface, and derives a spot light for each spotlight part (rig only, not in the spec).
        look_cfg=look_settings(a.look);derived=look_lights(look,a.look);look_info=look_report(look,a.look)
        built=build_parts(look,spec['scale']['basis'],collections,emission_scale=1.0 if look_cfg is None else look_cfg['emission_multiplier'])
        bpy.context.view_layer.update()
        corners=[obj.matrix_world@Vector(corner) for obj in built.values() for corner in obj.bound_box]
        bounds=[[min(v[i] for v in corners) for i in range(3)],[max(v[i] for v in corners) for i in range(3)]]
        rig=render_rig(bounds,scene.render.resolution_x/scene.render.resolution_y,spec['camera'])
        def camera(name,config):
            data=bpy.data.cameras.new(name);obj=bpy.data.objects.new(name,data);collections['RENDER_RIG'].objects.link(obj)
            obj.location=config['position'];point_camera(obj,config['target'],config.get('roll_deg',0));obj['ownership']='render_rig'
            data.type='PERSP' if config['projection']=='perspective' else 'ORTHO';data.sensor_fit='HORIZONTAL'
            data.sensor_width=config.get('sensor_width_mm',36);data.lens=config.get('lens_mm',40);data.ortho_scale=config.get('ortho_scale',8)
            data.shift_x=.5-config['principal_point'][0]
            data.shift_y=(config['principal_point'][1]-.5)*scene.render.resolution_y/scene.render.resolution_x
            # Rasterised previews/viewport need a sane near plane; a micro clip_start
            # destroys depth precision (saw-tooth z-fighting). Cycles is unaffected.
            low,high=rig['bounds'];pos=config['position']
            gap=math.sqrt(sum(max(low[i]-pos[i],0,pos[i]-high[i])**2 for i in range(3)))
            data.clip_start=min(1.0,max(.01,.5*gap)) if gap>0 else .01
            data.clip_end=max((Vector(config['position'])-Vector(rig['centre'])).length+rig['extent']*3,rig['extent']*5)
            return obj
        hero=camera('CAM_REFERENCE',spec['camera'])
        inspection=camera('CAM_INSPECTION',rig['inspection_camera'])
        projected_skipped=add_projectors(built,look,hero,(scene.render.resolution_x,scene.render.resolution_y))
        for light in rig['lights']:
            data=bpy.data.lights.new(light['name'],'AREA');data.energy=light['energy']*rig_energy_scale(a.look);data.shape='DISK';data.size=light['size']
            obj=bpy.data.objects.new(light['name'],data);collections['RENDER_RIG'].objects.link(obj)
            obj.location=light['position'];point_camera(obj,light['target']);obj['ownership']='render_rig'
        if look_cfg is not None:
            add_look(look_cfg,derived,rig['bounds'],world,collections)
            if look_cfg['exposure_offset']:scene.view_settings.exposure+=look_cfg['exposure_offset']
        scene.camera=hero;scene['spec_sha256']=digest(spec);scene['builder_sha256']=code_hash();scene['scope']='booth_only'
        scene['delivery_status']='draft_unverified';scene['scale_basis']=spec['scale']['basis']
        scene['no_venue_geometry']=True
        scene['render_rig_json']=json.dumps(rig,ensure_ascii=False)
        tx=bpy.data.texts.new('SPEC.json');tx.write(json.dumps(spec,ensure_ascii=False,indent=2))
        im=bpy.data.images.load(str(source));im.name='Source reference (not rendered)'
        # Blender may lazily load the buffer. Read it before changing filepath_raw,
        # otherwise save() tries to load the as-yet nonexistent destination.
        if not im.has_data:_=im.pixels[0]
        # Encode a genuine PNG without a render/view transform. Renaming JPEG bytes
        # to .png confuses downstream file readers and is not a faithful artifact.
        im.file_format='PNG';im.filepath_raw=str(out/'source_reference.png');im.save();im.pack()
        bpy.context.view_layer.update()
        anchors=[]
        for lm in spec['landmarks']:
            co=built[lm['component_id']].matrix_world@Vector(lm['local_xyz'])
            ndc=world_to_camera_view(scene,hero,co)
            anchors.append({'id':lm['id'],'uv':[float(ndc.x),float(1-ndc.y)],'depth':float(ndc.z)})
        write_json(out/'projected_landmarks.json',anchors)
        bpy.ops.wm.save_as_mainfile(filepath=str(blend),compress=True)
        stage('scene_and_first_save')
        renders=[];preview_samples=[None]      # preview_samples: the Cycles samples a preview really used (None when it was not a Cycles preview)
        hd_info={'requested':a.hd_scale if a.render else None,'effective':None,'capped':False,'max_megapixels':a.hd_max_mp}
        def write_report(partial=False):
            """Build record. Written before the memory-heavy HD stage too, so a crash there still leaves a report."""
            artifact_names=[n for n in ['booth.blend','source_reference.png','projected_landmarks.json']+renders if (out/n).is_file()]
            artifact_names += [str(p.relative_to(out)) for p in sorted((out/'part_masks').glob('*.png'))] if (out/'part_masks').exists() else []
            hashed_artifacts=[{'path':name,'sha256':file_sha(out/name)} for name in artifact_names]
            write_json(out/'build_report.json',{'artifacts':hashed_artifacts,'status':'preview_only' if a.preview else 'built_not_verified','partial':partial,'blender_version':bpy.app.version_string,
                       'spec_sha256':digest(spec),'builder_sha256':code_hash(),'blend_sha256':file_sha(blend),
                       'booth_object_count':len(built),'renders':renders,'scope':'booth_only','render_rig':rig,
                       'material_set':a.material_set,'look':a.look,'emissive_parts':look_info['emissive_parts'],'derived_lights':look_info['derived_lights'],'look_unhandled':look_info['unhandled'],'preview_samples':preview_samples[0],**({'projected_uv_skipped':projected_skipped,'projected_uv_note':PROJECTED_UV_NOTE} if has_projected(look) else {}),
                       'client_exposure_ev':a.client_exposure,'hd_scale':hd_info['effective'] if hd_info['effective'] is not None else (a.hd_scale if a.render else None),'hd':hd_info,
                       'hd_views':a.hd_views if a.render and a.hd_scale>1 else None,
                       'stage_seconds':dict(stages,total_in_script=round(sum(stages.values()),2)),
                       'preview_engine':a.preview_engine if a.preview else None,
                       'render_purposes':{'reference_render.png':'transparent_acceptance','inspection_render.png':'transparent_secondary_view',
                                          'client_render.png':'neutral_customer_display','client_render_hd.png':'neutral_customer_display_hd',
                                          'inspection_render_hd.png':'neutral_secondary_view_hd','preview_render.png':'transparent_iteration_preview',
                                          'preview_client_look.png':'customer_look_iteration_preview'},
                       'notes':check['warnings']})
        background=world.node_tree.nodes.get('Background')
        def client_look(on,saved={}):
            """Neutral gray customer backdrop and display transform; restore exactly afterwards."""
            if on:
                saved.update(color=tuple(background.inputs['Color'].default_value),strength=background.inputs['Strength'].default_value,
                             transform=scene.view_settings.view_transform,exposure=scene.view_settings.exposure)
                try:scene.view_settings.view_transform='Khronos PBR Neutral'
                except TypeError:scene.view_settings.view_transform='Standard'
                scene.view_settings.exposure=saved['exposure']+a.client_exposure
                if look_cfg is None:background.inputs['Color'].default_value=(.8,.8,.8,1);background.inputs['Strength'].default_value=.8   # dark_hall keeps its dark world
                scene.render.film_transparent=False
            else:
                background.inputs['Color'].default_value=saved['color'];background.inputs['Strength'].default_value=saved['strength']
                scene.view_settings.view_transform=saved['transform'];scene.view_settings.exposure=saved['exposure']
                scene.render.film_transparent=True
        def shoot(cam,name):
            scene.camera=cam;scene.render.filepath=str(out/name);bpy.ops.render.render(write_still=True);renders.append(name);stage('render:'+name)
        if a.preview:
            # Iteration only: one matched-camera image, transparent so it can be laid over the source.
            if a.preview_engine=='workbench' and not a.preview_client_look:
                scene.render.engine='BLENDER_WORKBENCH'
                shading=scene.display.shading
                shading.light='STUDIO';shading.color_type='MATERIAL';shading.show_shadows=False;shading.show_cavity=False
                scene.display.render_aa='8';scene.view_settings.view_transform='Standard'
            else:
                preview_samples[0]=min(max(1,a.samples),NEUTRAL_PREVIEW_SAMPLE_CAP if look_cfg is None else look_cfg['preview_sample_cap'])
                scene.cycles.samples=preview_samples[0]
            shoot(hero,'preview_render.png')
            if a.preview_client_look:
                client_look(True);shoot(hero,'preview_client_look.png');client_look(False)
            scene.render.engine='CYCLES';scene.cycles.samples=max(1,a.samples)
        if a.render:
            for cam,name in ((hero,'reference_render.png'),(inspection,'inspection_render.png')):shoot(cam,name)
            # Customer display is a separate image, preserving the exact transparent
            # acceptance camera/image above and adding no venue or studio geometry.
            client_look(True)
            shoot(hero,'client_render.png')
            if a.hd_scale>1:
                write_report(partial=True)
                base=(scene.render.resolution_x,scene.render.resolution_y,scene.cycles.samples)
                eff=hd_scale_for(base[0],base[1],a.hd_scale,a.hd_max_mp)
                hd_info.update(effective=eff,capped=eff<a.hd_scale)
                if eff>1:
                    scene.render.use_persistent_data=False        # do not keep cached scene data next to the large frames
                    scene.render.resolution_x,scene.render.resolution_y=base[0]*eff,base[1]*eff
                    scene.cycles.samples=max(1,a.hd_samples or a.samples)
                    shoot(hero,'client_render_hd.png')
                    if a.hd_views=='both':shoot(inspection,'inspection_render_hd.png')
                    scene.render.resolution_x,scene.render.resolution_y,scene.cycles.samples=base
            client_look(False)
        if a.part_masks:
            old_view=(scene.view_settings.view_transform,scene.view_settings.look,scene.view_settings.exposure,scene.view_settings.gamma)
            scene.camera=hero;old={obj.name:list(obj.data.materials) for obj in built.values()}
            white=material('MASK_WHITE',{'color':(1,1,1,1)},True);black=material('MASK_BLACK',{'color':(0,0,0,1)},True)
            old_world=world.node_tree.nodes.get('Background').inputs['Strength'].default_value
            world.node_tree.nodes.get('Background').inputs['Strength'].default_value=0
            scene.render.film_transparent=False;scene.render.image_settings.color_mode='BW';scene.cycles.samples=1;scene.cycles.use_denoising=False
            scene.view_settings.view_transform='Standard';scene.view_settings.look='None';scene.view_settings.exposure=0;scene.view_settings.gamma=1
            masks=out/'part_masks';masks.mkdir(exist_ok=True)
            for c in spec['components']:
                if not c['critical']:continue
                for ident,obj in built.items():
                    obj.data.materials.clear();obj.data.materials.append(white if ident==c['id'] else black)
                scene.render.filepath=str(masks/(c['id']+'.png'));bpy.ops.render.render(write_still=True)
            for obj in built.values():
                obj.data.materials.clear()
                for m in old[obj.name]:obj.data.materials.append(m)
            world.node_tree.nodes.get('Background').inputs['Strength'].default_value=old_world
            scene.render.film_transparent=True;scene.render.image_settings.color_mode='RGBA';scene.cycles.samples=max(1,a.samples);scene.cycles.use_denoising=True
            scene.view_settings.view_transform=old_view[0];scene.view_settings.look=old_view[1];scene.view_settings.exposure=old_view[2];scene.view_settings.gamma=old_view[3]
        scene.camera=hero
        stage('part_masks')
        bpy.ops.wm.save_as_mainfile(filepath=str(blend),compress=True)
        stage('final_save')
        write_report()
    finally:
        lock.unlink(missing_ok=True)


if __name__=='__main__':main()
