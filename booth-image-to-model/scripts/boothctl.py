"""Small deterministic CLI: runtime probe, guarded edits, fingerprints, delivery gate.
Does not make model-provider calls or claim to install a hosted ChatGPT Skill.
"""
from __future__ import annotations
import argparse
import copy
import importlib.util
import json
import shutil
import subprocess
import sys
import time
import os
from pathlib import Path
from common import read_json,write_json,digest,file_sha,code_hash,fingerprints,cache_valid,find_blender as locate_blender
from validate_spec import validate
from material_patterns import material_set as rewrite_materials,has_projected,MATERIAL_SETS,LOOKS


MIN_BLENDER=(4,0)        # tested with 5.2.2


def doctor(blender=None):
    """What this machine can do: Python packages and Blender (found as find_blender does). Prints the next step when something is missing."""
    candidate=locate_blender(blender)
    deps={k:importlib.util.find_spec(k) is not None for k in ('numpy','PIL','scipy','jsonschema','trimesh')}
    result={'python':sys.version.split()[0],'blender_path':candidate,'blender_available':False,'dependencies':deps,
            'hosted_skill_updated':False}
    if candidate:
        try:
            p=subprocess.run([candidate,'--version'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60,check=False)
            result['blender_available']=p.returncode==0
            result['blender_version']=(p.stdout or '').splitlines()[0] if p.stdout else (p.stderr or '')[:200]
            import re
            m=re.search(r'Blender (\d+)\.(\d+)',result['blender_version'] or '')
            if m and (int(m.group(1)),int(m.group(2)))<MIN_BLENDER:result['blender_warning']='Blender %s.%s is older than 4.0; 5.2 is tested'%m.groups()
        except (OSError,subprocess.SubprocessError) as ex:result['blender_error']=str(ex)
    result['status']='ready_for_build' if result['blender_available'] else 'blocked_native_blender'
    steps=[]
    missing=[k if k!='PIL' else 'Pillow' for k,ok in deps.items() if not ok]
    if missing:steps.append('python -m pip install -r <SKILL_DIR>/requirements.txt   (missing: '+', '.join(missing)+')')
    if not result['blender_available']:steps.append('install Blender 4.x or newer and pass --blender <path> or set BLENDER_EXE (only renders and .blend need it)')
    result['next_steps']=steps
    return result


def patch_component(spec, ident, field, value):
    """Allow only real, existing geometry/appearance fields. Metadata cannot be
    edited through this shortcut to bypass scope/evidence/quality obligations."""
    allowed_roots={'params','location','rotation_deg','material'}
    keys=field.split('.')
    if keys[0] not in allowed_roots or any(k.startswith('_') for k in keys):
        raise ValueError('PROTECTED_PATCH: scope, source, evidence, IDs and quality require explicit re-review')
    out=copy.deepcopy(spec);matches=[c for c in out['components'] if c['id']==ident]
    if len(matches)!=1:raise ValueError('Unknown or duplicate component ID: '+ident)
    dest=matches[0]
    for key in keys[:-1]:
        if not isinstance(dest,dict) or key not in dest:raise ValueError('Patch path does not exist')
        dest=dest[key]
    if not isinstance(dest,dict) or keys[-1] not in dest:raise ValueError('Patch path does not exist; no silently unused parameters')
    dest[keys[-1]]=value
    if keys[0] in ('params','location','rotation_deg'):out['camera']['fit_status']='unfitted'
    result=validate(out)
    if not result['valid']:raise ValueError('PATCH_REJECTED: '+json.dumps(result['errors'],ensure_ascii=False))
    old,new=fingerprints(spec),fingerprints(out)
    return out,{'component_id':ident,'field':field,'invalidated_stages':[k for k in old if old[k]!=new[k]],
                'before_sha256':digest(spec),'after_sha256':digest(out)}


def delivery_gate(spec, evidence, root):
    """Evidence-based gate, not a numerical aesthetic score. Missing proof fails.
    An unsigned reviewer record documents a review, not cryptographic attestation.
    """
    failures=[];root=Path(root).resolve();validation=validate(spec)
    if not validation['valid']:failures.append('invalid_spec')
    if evidence.get('spec_sha256')!=digest(spec):failures.append('stale_or_missing_spec_hash')
    if evidence.get('builder_sha256')!=code_hash():failures.append('stale_or_missing_builder_hash')
    if evidence.get('native_blender_reopened') is not True:failures.append('native_blend_not_reopened')
    if evidence.get('geometry_verified') is not True:failures.append('actual_geometry_not_verified')
    artifacts=evidence.get('artifacts',[]);roles=[]
    if not artifacts:failures.append('missing_artifacts')
    for a in artifacts:
        role=a.get('role');roles.append(role)
        p=(root/a.get('path','')).resolve()
        if not p.is_relative_to(root) or not p.is_file() or p.stat().st_size==0:
            failures.append('missing_or_unsafe_artifact:'+str(role));continue
        if file_sha(p)!=a.get('sha256'):failures.append('artifact_hash_mismatch:'+str(role))
    for role in ('blend','reference_render','inspection_render','source_reference'):
        if role not in roles:failures.append('missing_role:'+role)
    if spec.get('camera',{}).get('fit_status')!='reviewed':failures.append('camera_not_reviewed')
    for u in spec.get('uncertainties',[]):
        if u.get('critical') and u.get('status')=='open':failures.append('open_critical_uncertainty:'+u['id'])
    metric=evidence.get('metrics',{});q=spec.get('quality',{})
    checks=(('silhouette_iou','silhouette_iou_min',True),('boundary_distance_diag','boundary_distance_max',False),
            ('landmark_median_diag','landmark_median_max',False),('landmark_p90_diag','landmark_p90_max',False),
            ('held_out_landmark_median_diag','landmark_median_max',False),
            ('held_out_landmark_p90_diag','landmark_p90_max',False))
    for key,threshold,is_min in checks:
        val=metric.get(key);limit=q.get(threshold)
        if not isinstance(val,(float,int)) or isinstance(val,bool) or not 0<=val<=1 or limit is None:
            failures.append('missing_or_invalid_metric:'+key)
        elif (val<limit if is_min else val>limit):failures.append('metric_failed:'+key)
    # Fit anchors cannot dilute the independent camera check. Evidence from an
    # older assessment must be recomputed to include the held-out errors.
    held_count=metric.get('held_out_anchor_count')
    if isinstance(held_count,bool) or not isinstance(held_count,int) or held_count<2:
        failures.append('missing_held_out_anchors')
    for c in spec.get('components',[]):
        if c.get('critical') and c.get('evidence',{}).get('status')=='observed':
            pm=evidence.get('part_metrics',{}).get(c['id'],{})
            if isinstance(pm.get('iou'),bool) or not isinstance(pm.get('iou'),(float,int)) or not q.get('part_iou_min',1)<=pm['iou']<=1:
                failures.append('critical_part_metric_failed:'+c['id'])
    review=evidence.get('visual_review',{})
    for key in ('topology','ownership','layout','alternate_view','appearance'):
        if review.get(key)!='pass':failures.append('visual_review_not_passed:'+key)
    if not review.get('reviewer') or not review.get('notes'):failures.append('missing_visual_review_record')
    # Mask correctness is independently reviewed, not inferred from mask IoU.
    if evidence.get('reference_annotation_review')!='pass':failures.append('unreviewed_reference_annotations')
    return {'status':'review_required' if failures else 'verified_visual_reconstruction',
            'deliver_as_finished':not failures,'failures':failures,
            'note':'Only visual reconstruction; no inference of measured dimensions or engineering certification.'}


def find_blender(blender=None):
    candidate=locate_blender(blender)
    if not candidate:
        raise ValueError('Blender not found: pass --blender, set BLENDER_EXE, put blender on PATH or install it in the usual place')
    return candidate


BLENDER_FLAGS=['--background','--factory-startup','--disable-autoexec','--python-exit-code','1']


def run_blender_script(script,script_args,blender=None,timeout=7200):
    """Run one of this skill's Blender scripts in a fresh background Blender. Returns (returncode, log tail)."""
    cmd=[find_blender(blender)]+BLENDER_FLAGS+['--python',str(Path(__file__).resolve().parent/script),'--']+list(script_args)
    proc=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,check=False)
    return proc.returncode,((proc.stdout or '')+(proc.stderr or ''))[-3000:]


def resolve_preview_engine(out_root,engine):
    """A Workbench crash on this machine is sticky: later previews in the same folder go straight to Cycles."""
    sticky=Path(out_root)/'.preview_engine.json'
    if engine=='workbench' and sticky.is_file():
        try:
            if json.loads(sticky.read_text(encoding='utf-8')).get('engine')=='cycles':return 'cycles',True
        except (ValueError,OSError):pass
    return engine,False


LOOK_COMPARE_NOTE=('This preview used the %s look (dark world, an opaque floor plate, dimmed rig lights): booth_pixel_fraction and the suggested '
                   'client exposure are not comparable with neutral previews. Score with neutral previews only.')


def annotate_compare(run,look):
    """Record the look in compare.json and say its pixel numbers are not comparable with a neutral preview. Neutral previews are left as they are."""
    path=Path(run)/'compare.json';data=read_json(path)
    data['look']=look;data['look_note']=LOOK_COMPARE_NOTE%look
    write_json(path,data)


def preview(spec_path,out_root,blender=None,engine=None,samples=8,timeout=600,context=None,client_look=False,client_exposure=None,material_set='neutral',look='neutral'):
    """Validate -> fresh numbered run -> one matched-camera render -> comparison sheet.
    Iteration aid only; the final delivery still uses a full --render build and blender_verify.
    engine: workbench (default) or cycles; with projected materials (the material set or parts written in the spec) the default is cycles.
    material_set: neutral (the spec's own materials) or projected (the picture projected through the fitted camera).
    look: neutral (default, nothing changes) or dark_hall (dark world and floor; emissive parts light their surroundings; Cycles only)."""
    spec_path=Path(spec_path).resolve();spec=read_json(spec_path)
    check=validate(spec,spec_path.parent)
    if not check['valid']:return {'status':'invalid_spec','errors':check['errors'][:10]}
    if material_set not in MATERIAL_SETS:return {'status':'invalid_spec','errors':[{'code':'MATERIAL_SET','path':'material_set','message':'Unknown material set; known sets: '+', '.join(MATERIAL_SETS)}]}
    if material_set!='neutral':     # the same in-memory rewrite the build does; refuses before Blender starts
        look_check=validate(rewrite_materials(spec,material_set),spec_path.parent)
        if not look_check['valid']:return {'status':'invalid_spec','errors':look_check['errors'][:10]}
    if look not in LOOKS:return {'status':'invalid_options','message':'Unknown look; known looks: '+', '.join(LOOKS)}
    if look=='dark_hall' and engine=='workbench':     # Workbench draws no lights, so the dark look would show nothing
        return {'status':'invalid_options','message':'The dark_hall look only shows in Cycles; Workbench draws no lights. Use --engine cycles or leave --engine out.'}
    if look=='dark_hall' and engine is None:engine='cycles'
    material_look=rewrite_materials(spec,material_set)
    if has_projected(material_look):             # Workbench shows only each part's flat colour, never the projected picture
        if engine=='workbench':     # also with --client-look: an explicit choice is refused, never switched silently
            return {'status':'invalid_options','message':'The projected materials only show in Cycles; Workbench would show flat colours. Use --engine cycles or leave --engine out.'}
        if engine is None:engine='cycles'
    if engine is None:engine='workbench'
    out_root=Path(out_root).resolve();out_root.mkdir(parents=True,exist_ok=True)
    if client_look:engine='cycles'      # the customer look needs the path tracer
    engine,sticky_used=resolve_preview_engine(out_root,engine)
    n=1+max([int(p.name.split('_')[-1]) for p in out_root.glob('preview_*') if p.name.split('_')[-1].isdigit()]+[0])
    run=out_root/f'preview_{n:03d}'
    cmd=[find_blender(blender),'--background','--factory-startup','--disable-autoexec','--python-exit-code','1',
         '--python',str(Path(__file__).resolve().parent/'blender_build.py'),'--','--spec',str(spec_path),'--out',str(run),
         '--preview','--preview-engine',engine,'--samples',str(samples)]+(['--preview-client-look'] if client_look else [])+(['--client-exposure',str(client_exposure)] if client_exposure is not None else [])+(['--material-set',material_set] if material_set!='neutral' else [])+(['--look',look] if look!='neutral' else [])
    started=time.time()
    proc=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,check=False)
    fallback=None
    if proc.returncode!=0 and engine=='workbench':
        # Integrated-GPU OpenGL drivers can crash headless Workbench; Cycles CPU needs no GPU.
        fallback={'engine':'cycles','failed_run':str(run),'reason':((proc.stdout or '')+(proc.stderr or ''))[-400:]}
        run=out_root/f'preview_{n:03d}b';cmd[cmd.index('--out')+1]=str(run);cmd[cmd.index('--preview-engine')+1]='cycles'
        proc=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,check=False)
        if proc.returncode==0:
            write_json(out_root/'.preview_engine.json',{'engine':'cycles','reason':'Workbench failed headless on this machine','first_failed_run':fallback['failed_run']})
    seconds=round(time.time()-started,1)
    if proc.returncode!=0:
        return {'status':'build_failed','run':str(run),'seconds':seconds,'fallback':fallback,'log_tail':((proc.stdout or '')+(proc.stderr or ''))[-2000:]}
    from preview_compare import sheet  # numpy/Pillow only for this optional step
    summary=sheet(run)
    if look!='neutral':annotate_compare(run,look)
    report=read_json(run/'build_report.json')
    stages=report.get('stage_seconds') or {}
    design=None
    try:
        from design_check import run as design_run  # advisory findings only; never blocks the preview
        full=design_run(spec,read_json(context) if context else {})
        design={'status':full['status'],'counts':full['counts'],'findings':full['findings'][:12],'not_checked':full['not_checked'],'note':full['note']}
    except (ValueError,KeyError,OSError) as ex:design={'status':'unavailable','reason':str(ex)[:200]}
    inside=stages.get('total_in_script')
    return {'status':'preview_ready','run':str(run),'seconds':seconds,'fallback':fallback,'engine_used':'cycles' if (fallback or sticky_used) else engine,
            'stage_seconds':stages,'design_findings':design,'client_look':summary.get('client_look'),'startup_overhead_s':round(seconds-inside,1) if isinstance(inside,(int,float)) else None,
            **({'look':look} if look!='neutral' else {}),'sheet':summary['sheet'],
            'landmark_median_px':summary['landmark_median_px'],'landmark_max_px':summary['landmark_max_px'],
            'worst_landmarks':sorted(summary['landmarks'],key=lambda r:-r['err_px'])[:5],
            'warnings':[{k:w[k] for k in ('code','ids','plane') if k in w} for w in check['warnings']][:20]}


def main():
    p=argparse.ArgumentParser();sp=p.add_subparsers(dest='command',required=True)
    s=sp.add_parser('doctor');s.add_argument('--blender');s.add_argument('--output')
    s=sp.add_parser('fingerprint');s.add_argument('--spec',required=True);s.add_argument('--output')
    s=sp.add_parser('patch');s.add_argument('--spec',required=True);s.add_argument('--component',required=True);s.add_argument('--field',required=True);s.add_argument('--value',required=True);s.add_argument('--output',required=True)
    s=sp.add_parser('gate');s.add_argument('--spec',required=True);s.add_argument('--evidence',required=True);s.add_argument('--root',required=True);s.add_argument('--output',required=True)
    s=sp.add_parser('overlay',help='Draw the fitted box and 0.5 m grids over the source image (check the fit before modelling)')
    s.add_argument('--fit',required=True);s.add_argument('--image',required=True);s.add_argument('--out',required=True)
    s=sp.add_parser('design-check',help='Advisory design checks on a spec (no verdicts; missing context is reported as not checked)')
    s.add_argument('--spec',required=True);s.add_argument('--context');s.add_argument('--output')
    s=sp.add_parser('glb',help='Export the spec as a GLB (needs trimesh; no Blender) for viewers and the web editor')
    s.add_argument('--spec',required=True);s.add_argument('--out',required=True)
    s=sp.add_parser('compile',help='Compile a scene description (pixels + building blocks) into a spec; no model call')
    s.add_argument('--scene',required=True);s.add_argument('--image',required=True);s.add_argument('--out',required=True)
    s.add_argument('--fit',help='Existing box_fit.json to reuse');s.add_argument('--seeds',type=int,default=40)
    s=sp.add_parser('design-compile',help='Compile a design (design.json: brief, form, style, elements in metres) into a spec; no model call')
    s.add_argument('--design',required=True);s.add_argument('--out',required=True)
    s.add_argument('--canvas',default='1280x800',help='render size WIDTHxHEIGHT of the design canvas (default 1280x800)')
    s.add_argument('--fit',help='box_fit.json with status fitted: use its camera, footprint and floor height (needs --image)')
    s.add_argument('--image',help='the picture the fit was made on, used as the spec source (needs --fit)')
    s=sp.add_parser('build',help='Final build in a fresh background Blender: booth.blend and, with --render, the renders. Options after -- go to blender_build.py')
    s.add_argument('--spec',required=True);s.add_argument('--out',required=True);s.add_argument('--blender');s.add_argument('--timeout',type=int,default=7200)
    s.add_argument('build_args',nargs=argparse.REMAINDER,help='e.g. -- --render --samples 32 --hd-scale 3')
    s=sp.add_parser('verify',help='Reopen a built .blend in a separate Blender and check it against the spec')
    s.add_argument('--spec',required=True);s.add_argument('--blend',required=True);s.add_argument('--report',required=True);s.add_argument('--blender')
    s=sp.add_parser('preview',help='Fast iteration render + comparison sheet in a fresh numbered folder')
    s.add_argument('--spec',required=True);s.add_argument('--out-root',required=True);s.add_argument('--blender')
    s.add_argument('--engine',choices=('workbench','cycles'),default=None,help='default workbench, or cycles when the model has projected materials or the dark_hall look (Workbench cannot show them)');s.add_argument('--samples',type=int,default=8)
    s.add_argument('--context',help='Optional design-check context JSON (height limit, hanging allowed, open sides, LED module)')
    s.add_argument('--client-look',action='store_true',help='Also render the customer look (Cycles) and report clipping with a suggested --client-exposure')
    s.add_argument('--client-exposure',type=float,help='Exposure offset in EV for the customer look (default -1.0), to try the suggested value')
    s.add_argument('--material-set',choices=MATERIAL_SETS,default='neutral',help='neutral: the spec materials (default); projected: the source picture projected through the fitted camera onto every part (needs a fitted camera)')
    s.add_argument('--look',choices=LOOKS,default='neutral',help='neutral: the usual render (default); dark_hall: dark world and floor, glowing parts light the floor and walls (Cycles only; for comparison previews)')
    a=p.parse_args()
    try:
        if a.command=='design-check':
            from design_check import run as design_run
            result=design_run(read_json(a.spec),read_json(a.context) if a.context else {})
            if a.output:write_json(a.output,result)
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0
        if a.command=='glb':
            from export_glb import run as glb_run
            code,result=glb_run(a.spec,a.out);print(json.dumps(result,ensure_ascii=False,indent=2));return code
        if a.command=='compile':
            from draft_compile import run as compile_run
            code,result=compile_run(a.scene,a.image,a.out,a.fit,a.seeds);print(json.dumps(result,ensure_ascii=False,indent=2));return code
        if a.command=='design-compile':
            from design_compile import run as design_compile_run
            code,result=design_compile_run(a.design,a.out,a.canvas,a.fit,a.image);print(json.dumps(result,ensure_ascii=False,indent=2));return code
        if a.command=='overlay':
            from overlay import draw_overlay  # Pillow only for this optional step
            result=draw_overlay(read_json(a.fit),a.image,a.out);print(json.dumps(result,ensure_ascii=False,indent=2));return 0
        if a.command in ('build','verify'):
            if a.command=='build':
                extra=[x for x in a.build_args if x!='--']
                code,log=run_blender_script('blender_build.py',['--spec',a.spec,'--out',a.out]+extra,a.blender,a.timeout)
                report=Path(a.out)/'build_report.json'
            else:
                code,log=run_blender_script('blender_verify.py',['--spec',a.spec,'--blend',a.blend,'--report',a.report],a.blender)
                report=Path(a.report)
            result={'status':'ok' if code==0 else 'failed','returncode':code,'report':str(report) if report.is_file() else None}
            if code!=0:result['log_tail']=log
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if code==0 else 2
        if a.command=='preview':
            result=preview(a.spec,a.out_root,a.blender,a.engine,a.samples,context=a.context,client_look=a.client_look,client_exposure=a.client_exposure,material_set=a.material_set,look=a.look)
            print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result['status']=='preview_ready' else 2
        if a.command=='doctor':result=doctor(a.blender)
        elif a.command=='fingerprint':result=fingerprints(read_json(a.spec))
        elif a.command=='patch':
            spec,result=patch_component(read_json(a.spec),a.component,a.field,json.loads(a.value));write_json(a.output,spec)
            write_json(str(a.output)+'.patch.json',result);print(json.dumps(result,ensure_ascii=False,indent=2));return 0
        else:result=delivery_gate(read_json(a.spec),read_json(a.evidence),a.root)
        if getattr(a,'output',None):write_json(a.output,result)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 2 if result.get('status') in ('review_required','blocked_native_blender') else 0
    except (ValueError,KeyError,OSError,subprocess.SubprocessError) as ex:print(str(ex),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
