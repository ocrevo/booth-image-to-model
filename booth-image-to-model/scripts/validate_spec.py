"""Fail-closed spec validation usable inside Blender without pip dependencies."""
from __future__ import annotations
import argparse
import re
from pathlib import Path
from common import finite_vector,file_sha,read_json,write_json
from geometry import mesh_for,positive,transform_point
from material_patterns import pattern_parameters

PARAM_KEYS={
 'box':({'size'},{'size','bevel_radius','bevel_mode','segments_per_corner'}),
 'cylinder':({'radius','height'},{'radius','height','segments'}),
 'rounded_ring':({'outer_size','inner_size','outer_radius','inner_radius','height'},
                 {'outer_size','inner_size','outer_radius','inner_radius','height','segments_per_corner'}),
 'rounded_portal':({'width','height','depth','side_width','beam_height','outer_radius','inner_radius'},
                   {'width','height','depth','side_width','beam_height','outer_radius','inner_radius','segments_per_corner'}),
 'text':({'body','size'},{'body','size','extrude'}),
 'swept_strip':({'control_points','width','depth'},{'control_points','width','depth','segments_per_curve'}),
 'profile_extrusion':({'profile','depth'},{'profile','depth'}),
 'arc_panel':({'radius','thickness','height','start_deg','end_deg'},
               {'radius','thickness','height','start_deg','end_deg','segments'}),
}


PARAM_TYPES={
 'box':{'size':list,'bevel_radius':'number','bevel_mode':str,'segments_per_corner':int},
 'cylinder':{'radius':'number','height':'number','segments':int},
 'rounded_ring':{'outer_size':list,'inner_size':list,'outer_radius':'number','inner_radius':'number','height':'number','segments_per_corner':int},
 'rounded_portal':{k:('integer' if k=='segments_per_corner' else 'number') for k in PARAM_KEYS['rounded_portal'][1]},
 'text':{'body':str,'size':'number','extrude':'number'},
 'swept_strip':{'control_points':list,'width':'number','depth':'number','segments_per_curve':int},
 'profile_extrusion':{'profile':list,'depth':'number'},
 'arc_panel':{k:('integer' if k=='segments' else 'number') for k in PARAM_KEYS['arc_panel'][1]},
}


def typed_object(value,path,required,types,error,missing_code='MISSING_FIELD',unknown_code='UNKNOWN_FIELD'):
    """Check this API's declared fields without a JSON Schema runtime dependency."""
    if not isinstance(value,dict):
        error('FIELD_TYPE',path,'Expected an object');return False
    valid=True
    for key in sorted(set(required)-set(value)):
        error(missing_code,path+'.'+key,'Required field missing');valid=False
    for key in sorted(set(value)-set(types)):
        error(unknown_code,path+'.'+key,'Unsupported/unused field');valid=False
    for key,expected in types.items():
        if key not in value:continue
        actual=value[key]
        if expected=='number':matches=finite_vector([actual],1)
        elif expected in (int,'integer'):matches=type(actual) is int
        elif expected is str:matches=isinstance(actual,str) and len(actual)>0
        else:matches=isinstance(actual,expected)
        if not matches:
            name=expected if isinstance(expected,str) else expected.__name__
            error('FIELD_TYPE',path+'.'+key,'Expected '+name+(' (nonempty)' if expected is str else ''))
            valid=False
    return valid


def _area2(poly):
    return sum(poly[i-1][0]*poly[i][1]-poly[i][0]*poly[i-1][1] for i in range(len(poly)))/2


def _convex_ccw(poly,tol=1e-9):
    """Return the polygon counter-clockwise if it is convex, otherwise None."""
    if len(poly)<3 or abs(_area2(poly))<=tol:return None
    if _area2(poly)<0:poly=poly[::-1]
    n=len(poly)
    for i in range(n):
        (x0,y0),(x1,y1),(x2,y2)=poly[i-1],poly[i],poly[(i+1)%n]
        if (x1-x0)*(y2-y1)-(y1-y0)*(x2-x1)<-tol:return None
    return poly


def _clip(subject,clip):
    """Intersection polygon of two convex counter-clockwise polygons (Sutherland-Hodgman)."""
    def cross(u,v):return u[0]*v[1]-u[1]*v[0]
    out=subject
    for i in range(len(clip)):
        a,b=clip[i-1],clip[i];e=(b[0]-a[0],b[1]-a[1])
        def inside(p):return cross(e,(p[0]-a[0],p[1]-a[1]))>=-1e-12
        def cut(p,q):
            d=(q[0]-p[0],q[1]-p[1]);den=cross(e,d)
            t=-cross(e,(p[0]-a[0],p[1]-a[1]))/den if abs(den)>1e-15 else 0.0
            return (p[0]+t*d[0],p[1]+t*d[1])
        src,out=out,[]
        for j in range(len(src)):
            p,q=src[j-1],src[j]
            if inside(q):
                if not inside(p):out.append(cut(p,q))
                out.append(q)
            elif inside(p):out.append(cut(p,q))
        if not out:return []
    return out if len(out)>=3 else []


def _clip_area(subject,clip):
    out=_clip(subject,clip)
    return abs(_area2(out)) if out else 0.0


def coplanar_overlaps(components,meshes,tol=1e-4,min_area=1e-4,limit=50):
    """Exposed axis-aligned faces of different parts lying in one plane and facing
    the same way. Renderers shade such pairs with black or speckled z-fighting.
    Touching faces that point opposite ways (contact) are not reported, nor is an
    overlap hidden against another part's opposite face (two backs on one wall,
    two bases on one floor). Concave faces are skipped rather than guessed."""
    groups={}
    for c in components:
        mesh=meshes.get(c.get('id'))
        if not mesh:continue
        verts,faces=mesh
        world=[transform_point(p,c['location'],c['rotation_deg']) for p in verts]
        lo=[min(p[i] for p in world) for i in range(3)];hi=[max(p[i] for p in world) for i in range(3)]
        for face in faces:
            pts=[world[i] for i in face]
            for ax in range(3):
                vals=[p[ax] for p in pts]
                if max(vals)-min(vals)>tol:continue
                coord=sum(vals)/len(vals)
                if abs(coord-hi[ax])<=tol:sign=1
                elif abs(coord-lo[ax])<=tol:sign=-1
                else:break
                a,b=[k for k in range(3) if k!=ax]
                poly=_convex_ccw([(p[a],p[b]) for p in pts])
                if poly and abs(_area2(poly))>=min_area:
                    box=(min(p[0] for p in poly),max(p[0] for p in poly),min(p[1] for p in poly),max(p[1] for p in poly))
                    groups.setdefault((ax,sign,round(coord,4)),[]).append((c['id'],box,poly))
                break
    found=[];seen=set()
    for (ax,sign,coord),items in sorted(groups.items()):
        for i in range(len(items)):
            for j in range(i+1,len(items)):
                (ca,ra,pa),(cb,rb,pb)=items[i],items[j]
                if ca==cb:continue
                key=tuple(sorted((ca,cb)))+(ax,sign,coord)
                if key in seen:continue
                if min(ra[1],rb[1])-max(ra[0],rb[0])<=tol or min(ra[3],rb[3])-max(ra[2],rb[2])<=tol:continue
                shared=_clip(pa,pb);area=abs(_area2(shared)) if shared else 0.0
                if area<min_area:continue
                hidden=any(other!=ca and other!=cb and _clip_area(shared,po)>=.9*area
                           for other,_,po in groups.get((ax,-sign,coord),[]))
                if not hidden:
                    seen.add(key)
                    found.append({'code':'COPLANAR_OVERLAP','ids':[ca,cb],'plane':('+' if sign>0 else '-')+'xyz'[ax]+f'={coord:g}',
                                  'area_m2':round(area,4),'message':'Same-facing faces share a plane; renders show black/speckled z-fighting. Make the parts adjacent or offset one by >=1 mm.'})
                    if len(found)>=limit:return found
    return found


def check_structure(spec,error):
    """Required/unknown/type checks shared by external Python and native Blender."""
    def obj(value,path,required,types,**codes):
        return typed_object(value,path,required,types,error,**codes)
    obj(spec['project'],'project',{'id'},{'id':str,'status':str})
    obj(spec['source'],'source',{'image','sha256','width','height'},
        {'image':str,'sha256':str,'width':int,'height':int},missing_code='SOURCE_FIELD')
    obj(spec['scale'],'scale',{'units','basis','footprint_m','notes'},
        {'units':str,'basis':str,'footprint_m':list,'notes':str})
    scope=spec['scope']
    if obj(scope,'scope',{'decisions'},{'decisions':list}) and isinstance(scope.get('decisions'),list):
        for i,d in enumerate(scope['decisions']):
            obj(d,f'scope.decisions[{i}]',{'id','owner','decision','evidence'},
                {'id':str,'owner':str,'decision':str,'evidence':str})
    component_types={'id':str,'scope_id':str,'owner':str,'kind':str,'role':str,'critical':bool,
                     'evidence':dict,'location':list,'rotation_deg':list,'params':dict,'material':dict,'group':str}
    for name in ('components','requirements','landmarks','uncertainties'):
        if not isinstance(spec[name],list):error('FIELD_TYPE',name,'Expected an array')
    if isinstance(spec['components'],list):
        if not spec['components']:error('MISSING_COMPONENT','components','At least one component required')
        for i,c in enumerate(spec['components']):
            path=f'components[{i}]'
            if not obj(c,path,set(component_types)-{'group'},component_types,
                       missing_code='MISSING_COMPONENT_FIELD',unknown_code='UNUSED_COMPONENT_FIELD'):continue
            obj(c['evidence'],path+'.evidence',{'status','note'},
                {'status':str,'note':str,'roi_xyxy_norm':list})
            kind=c['kind']
            if kind in PARAM_KEYS:
                obj(c['params'],path+'.params',PARAM_KEYS[kind][0],PARAM_TYPES[kind],
                    missing_code='MISSING_PARAMETER',unknown_code='UNUSED_PARAMETER')
            obj(c['material'],path+'.material',{'color'},
                {'color':list,'color_space':str,'roughness':'number','metallic':'number','emission':'number','pattern':dict},
                unknown_code='UNKNOWN_MATERIAL')
    item_types={
        'requirements':({'id','component_id'},{'id':str,'component_id':str,'kind':str,'must_be_critical':bool}),
        'landmarks':({'id','component_id','local_xyz','uv','visibility','fit'},
                     {'id':str,'component_id':str,'local_xyz':list,'uv':list,'visibility':str,'fit':bool,'weight':'number'}),
        'uncertainties':({'id','critical','status','description'},
                         {'id':str,'critical':bool,'status':str,'description':str})}
    for name,(required,types) in item_types.items():
        if isinstance(spec[name],list):
            for i,value in enumerate(spec[name]):obj(value,f'{name}[{i}]',required,types)
    cam_types={'projection':str,'position':list,'target':list,'lens_mm':'number','sensor_width_mm':'number',
               'ortho_scale':'number','principal_point':list,'pixel_aspect':list,'fit_status':str,'roll_deg':'number'}
    cam_required={'projection','position','target','principal_point','pixel_aspect','fit_status'}
    cam=spec['camera']
    if isinstance(cam,dict):
        if cam.get('projection')=='perspective':cam_required|={'lens_mm','sensor_width_mm'}
        elif cam.get('projection')=='orthographic':cam_required.add('ortho_scale')
    obj(cam,'camera',cam_required,cam_types,unknown_code='UNKNOWN_CAMERA_FIELD')
    quality_types={k:'number' for k in ('silhouette_iou_min','part_iou_min','landmark_median_max','landmark_p90_max','boundary_distance_max')}
    quality_types['threshold_basis']=str
    obj(spec['quality'],'quality',set(quality_types),quality_types)


def validate(spec, source_root=None, source_size=None):
    errors=[]; warnings=[]
    def error(code,path,message): errors.append({'code':code,'path':path,'message':message})
    if not isinstance(spec,dict):
        return {'valid':False,'errors':[{'code':'INVALID_ROOT','path':'','message':'Expected JSON object'}],'warnings':[]}
    required={'schema_version','project','source','scale','scope','components','camera','requirements','landmarks','uncertainties','quality'}
    for k in sorted(required-set(spec)): error('MISSING_FIELD',k,'Required v2 field missing; do not silently migrate an old proxy')
    for k in sorted(set(spec)-required): error('UNKNOWN_FIELD',k,'Unknown root field; likely unused parameter')
    if spec.get('schema_version')!='2.0': error('SCHEMA_VERSION','schema_version','Expected 2.0')
    if errors: return {'valid':False,'errors':errors,'warnings':warnings}
    check_structure(spec,error)
    if errors: return {'valid':False,'errors':errors,'warnings':warnings}
    try:
        source=spec['source']
        for k in ('image','sha256','width','height'):
            if k not in source: error('SOURCE_FIELD','source.'+k,'Missing source identity')
        if not re.fullmatch('[a-f0-9]{64}',source.get('sha256','')): error('SOURCE_HASH','source.sha256','Expected a SHA-256 digest')
        for k in ('width','height'):
            if isinstance(source.get(k),bool) or not isinstance(source.get(k),int) or source[k]<=0: error('SOURCE_SIZE','source.'+k,'Positive integer required')
        if source_root is not None:
            p=(Path(source_root)/source.get('image','')).resolve()
            if not p.is_file(): error('SOURCE_MISSING','source.image',str(p))
            elif file_sha(p)!=source['sha256']: error('SOURCE_CHANGED','source.sha256','Source bytes do not match the recorded input')
        if source_size is not None:
            if not finite_vector(list(source_size),2) or any(v<=0 or int(v)!=v for v in source_size):
                error('SOURCE_SIZE','source','Decoded source size must contain two positive integer dimensions')
            elif list(source_size)!=[source['width'],source['height']]:
                error('SOURCE_DIMENSIONS','source','Recorded dimensions do not match decoded source size '+str(list(source_size)))
        scale=spec['scale']
        if scale.get('units')!='m': error('UNITS','scale.units','Only metres supported')
        if scale.get('basis') not in ('user_dimensions','user_assumption','single_image_estimate'): error('SCALE_BASIS','scale.basis','Record measured versus assumed scale')
        if not finite_vector(scale.get('footprint_m'),2) or min(scale['footprint_m'])<=0: error('FOOTPRINT','scale.footprint_m','Positive dimensions required')
        if not scale.get('notes'): error('SCALE_NOTES','scale.notes','Explain scale anchor and uncertainty')
        scope={}
        for n,d in enumerate(spec['scope']['decisions']):
            path=f'scope.decisions[{n}]'; ident=d.get('id')
            if not ident or ident in scope: error('DUPLICATE_SCOPE',path,'Unique nonempty scope ID required')
            scope[ident]=d
            if d.get('owner') not in ('booth','venue','neighbor','unknown'): error('OWNER',path,'Unknown owner')
            if d.get('decision') not in ('include','exclude','defer'): error('SCOPE_DECISION',path,'Unknown decision')
            if d.get('decision')=='include' and d.get('owner')!='booth': error('NON_BOOTH_INCLUDED',path,'Only confirmed booth components may be built')
            if not d.get('evidence'): error('SCOPE_EVIDENCE',path,'Record the visual or user basis of ownership')
        comps={};meshes={}
        allowed={'id','scope_id','owner','kind','role','critical','evidence','location','rotation_deg','params','material','group'}
        for i,c in enumerate(spec['components']):
            path=f'components[{i}]'; ident=c.get('id')
            if not isinstance(ident,str) or not re.fullmatch('[a-z][a-z0-9_]*',ident): error('COMPONENT_ID',path,'Use a stable lowercase identifier')
            if ident in comps: error('DUPLICATE_COMPONENT',path,ident)
            comps[ident]=c
            for k in sorted((allowed-{'group'})-set(c)): error('MISSING_COMPONENT_FIELD',path+'.'+k,'Required field missing')
            if 'group' in c and not (isinstance(c['group'],str) and re.fullmatch('[a-z][a-z0-9_]*',c['group'])): error('COMPONENT_GROUP',path+'.group','Optional editing group: a stable lowercase identifier')
            for k in sorted(set(c)-allowed): error('UNUSED_COMPONENT_FIELD',path+'.'+k,'Unknown component field')
            decision=scope.get(c.get('scope_id'),{})
            if decision.get('owner')!='booth' or decision.get('decision')!='include' or c.get('owner')!='booth':
                error('SCOPE_BLOCKED',path,'Component does not resolve to a confirmed booth inclusion')
            for key in ('location','rotation_deg'):
                if not finite_vector(c.get(key),3): error('TRANSFORM',path+'.'+key,'Three finite numeric values required')
            ev=c.get('evidence',{})
            if ev.get('status') not in ('observed','inferred'): error('EVIDENCE_STATUS',path,'Use observed or inferred')
            if not ev.get('note'): error('EVIDENCE_NOTE',path,'Document visible basis or hidden-geometry assumption')
            if ev.get('status')=='observed' or 'roi_xyxy_norm' in ev:
                roi=ev.get('roi_xyxy_norm')
                if not finite_vector(roi,4) or not (0<=roi[0]<roi[2]<=1 and 0<=roi[1]<roi[3]<=1): error('OBSERVATION_ROI',path,'Visible components require a valid reference-image ROI')
            if not isinstance(c.get('critical'),bool): error('CRITICAL_FLAG',path,'Boolean required')
            kind=c.get('kind'); params=c.get('params',{})
            if kind not in PARAM_KEYS:
                error('UNSUPPORTED_PRIMITIVE',path,'No box fallback: implement a suitable builder or stop')
                continue
            req,accepted=PARAM_KEYS[kind]
            for k in sorted(req-set(params)): error('MISSING_PARAMETER',path+'.params.'+k,'Required parameter missing')
            for k in sorted(set(params)-accepted): error('UNUSED_PARAMETER',path+'.params.'+k,'Parameter would not drive geometry')
            try:
                if kind=='text':
                    if not isinstance(params['body'],str) or not params['body'].strip(): raise ValueError('text body is empty')
                    positive('size',params['size'])
                    extrude=params.get('extrude',0)
                    if not finite_vector([extrude],1) or extrude<0: raise ValueError('text extrude must be a finite nonnegative number')
                else: meshes[ident]=mesh_for(c)
            except (KeyError,TypeError,ValueError,OverflowError) as ex: error('INVALID_GEOMETRY',path,str(ex))
            mat=c.get('material',{})
            if set(mat)-{'color','color_space','roughness','metallic','emission','pattern'}: error('UNKNOWN_MATERIAL',path,'Unsupported material field')
            if mat.get('color_space','linear') not in ('linear','srgb'): error('MATERIAL_COLOR_SPACE',path,'Use linear or srgb')
            if not finite_vector(mat.get('color'),4) or not all(0<=x<=1 for x in mat['color']): error('MATERIAL_COLOR',path,'RGBA values in [0,1] required')
            for key in ('roughness','metallic'):
                value=mat.get(key,0.5 if key=='roughness' else 0.0)
                if not finite_vector([value],1) or not 0<=value<=1: error('MATERIAL_PARAMETER',path,key)
            val=mat.get('emission',0)
            if not finite_vector([val],1) or val<0: error('MATERIAL_PARAMETER',path,'emission')
            try:
                pattern_parameters(mat,c)
                if isinstance(mat.get('pattern'),dict) and mat['pattern'].get('type')=='projected' and spec['camera'].get('fit_status')=='unfitted':
                    raise ValueError('projected pattern needs a fitted camera (camera.fit_status landmark_fitted or reviewed), not unfitted')
            except (KeyError,TypeError,ValueError,OverflowError) as ex:error('MATERIAL_PATTERN',path,str(ex))
        warnings.extend(coplanar_overlaps([c for c in spec['components'] if isinstance(c,dict) and c.get('id') in meshes],meshes))
        floors=[c for c in comps.values() if c.get('role')=='floor']
        if len(floors)!=1 or floors[0].get('kind')!='box':
            error('FOOTPRINT_GEOMETRY','components','v2.0 requires exactly one rectangular booth-owned floor/carpet component')
        elif any(abs(floors[0]['params']['size'][i]-scale['footprint_m'][i])>1e-6 for i in range(2)):
            error('FOOTPRINT_MISMATCH','scale.footprint_m','Scale metadata must match actual carpet/floor geometry')
        for d in scope.values():
            if d.get('decision')=='include' and not any(c.get('scope_id')==d['id'] for c in comps.values()):
                error('OMITTED_INCLUDED_OBJECT','scope.'+str(d['id']),'Included object has no corresponding geometry')
        for i,r in enumerate(spec['requirements']):
            path=f'requirements[{i}]'; cid=r.get('component_id')
            if cid not in comps: error('MISSING_REQUIRED_COMPONENT',path,str(cid)); continue
            if r.get('kind') is not None and comps[cid]['kind']!=r['kind']: error('WRONG_PRIMITIVE',path,f'{cid} must use {r["kind"]}')
            if r.get('must_be_critical') and not comps[cid]['critical']: error('PRIORITY_DOWNGRADE',path,'Critical structure cannot be downgraded to pass review')
        lmids=set()
        for i,lm in enumerate(spec['landmarks']):
            path=f'landmarks[{i}]'
            if lm.get('id') in lmids: error('DUPLICATE_LANDMARK',path,'Unique IDs required')
            lmids.add(lm.get('id'))
            if lm.get('component_id') not in comps: error('LANDMARK_COMPONENT',path,'Unknown component')
            if not finite_vector(lm.get('local_xyz'),3): error('LANDMARK_3D',path,'Expected a local 3D coordinate')
            if not finite_vector(lm.get('uv'),2) or not all(0<=x<=1 for x in lm['uv']): error('LANDMARK_2D',path,'Use original-image normalized coordinates')
            if lm.get('visibility') not in ('visible','occluded'): error('LANDMARK_VISIBILITY',path,'Explicit visibility required')
            if not isinstance(lm.get('fit'),bool): error('LANDMARK_SPLIT',path,'Mark fit or held-out anchor')
            if not finite_vector([lm.get('weight',1)],1) or lm.get('weight',1)<=0:
                error('LANDMARK_WEIGHT',path+'.weight','A finite positive fitting weight is required')
        cam=spec['camera']
        allowed_cam={'projection','position','target','lens_mm','sensor_width_mm','ortho_scale','principal_point','pixel_aspect','fit_status','roll_deg'}
        for key in set(cam)-allowed_cam:error('UNKNOWN_CAMERA_FIELD','camera.'+key,'Unsupported/unused camera parameter')
        for key in ('position','target'):
            if not finite_vector(cam.get(key),3): error('CAMERA_VECTOR','camera.'+key,'Three finite values required')
        if cam.get('position')==cam.get('target'): error('CAMERA_DEGENERATE','camera','Position equals target')
        if cam.get('projection') not in ('perspective','orthographic'): error('CAMERA_PROJECTION','camera','Explicit perspective or orthographic required')
        if cam.get('fit_status') not in ('unfitted','landmark_fitted','reviewed'): error('CAMERA_STATUS','camera.fit_status','Unknown fit state')
        for key in ('lens_mm','sensor_width_mm','ortho_scale'):
            if key in cam:positive(key,cam[key])
        if not finite_vector(cam.get('principal_point'),2) or not all(0<=x<=1 for x in cam['principal_point']): error('PRINCIPAL_POINT','camera','Expected normalized principal point')
        if not finite_vector([cam.get('roll_deg',0)],1): error('CAMERA_ROLL','camera','Finite roll in degrees required')
        if not finite_vector(cam.get('pixel_aspect'),2) or cam['pixel_aspect'] != [1,1]: error('PIXEL_ASPECT','camera','v2.0 renderer supports square pixels only')
        for u in spec['uncertainties']:
            if u.get('status') not in ('open','resolved_by_evidence','accepted_by_user'): error('UNCERTAINTY_STATUS','uncertainties','Explicit uncertainty state required')
            if u.get('status')=='open' and u.get('critical'):
                warnings.append({'code':'OPEN_CRITICAL_UNCERTAINTY','id':u.get('id'),'message':u.get('description')})
        quality=spec['quality']
        for k in ('silhouette_iou_min','part_iou_min','landmark_median_max','landmark_p90_max','boundary_distance_max'):
            value=quality.get(k)
            if not finite_vector([value],1) or not 0<value<=1: error('QUALITY_PROFILE','quality.'+k,'Required configurable threshold in (0,1]')
        if not quality.get('threshold_basis'): error('QUALITY_BASIS','quality','Thresholds must be labelled project heuristics, not measured accuracy')
    except (KeyError,TypeError,ValueError,AttributeError,IndexError) as ex:
        error('MALFORMED_SPEC','',str(ex))
    return {'valid':not errors,'errors':errors,'warnings':warnings}


def main():
    p=argparse.ArgumentParser();p.add_argument('--spec',required=True);p.add_argument('--report');p.add_argument('--check-source',action='store_true')
    a=p.parse_args();spec=read_json(a.spec);source_root=Path(a.spec).parent if a.check_source else None
    result=validate(spec,source_root)
    if a.check_source and result['valid']:
        try:
            from PIL import Image
            with Image.open((source_root/spec['source']['image']).resolve()) as image:
                source_size=list(image.size)
            result=validate(spec,source_root,source_size=source_size)
        except (ImportError,OSError,ValueError) as ex:
            result['valid']=False
            result['errors'].append({'code':'SOURCE_IMAGE','path':'source.image','message':str(ex)})
    if a.report:write_json(a.report,result)
    import json;print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['valid'] else 2

if __name__=='__main__': raise SystemExit(main())
