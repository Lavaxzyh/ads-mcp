"""Read actual native layout geometry for solver/mesh correspondence checks."""


def layout_geometry_snapshot(a):
    import hashlib
    import json
    from pathlib import Path
    from keysight.ads import de
    from keysight.ads.de import db_uu as db
    name=a['lib']+':'+a['cell']+':'+a.get('view','layout')
    design=db.open_design(name,db.DesignMode.READ_ONLY)
    count=0
    def point(p):return [float(p.x),float(p.y)]
    def box(b):return point(b.lower_left)+point(b.upper_right)
    def outline(o):
        points=[point(p) for p in o.points]
        curves=[]
        if o.has_arcs:
            for index in range(len(points)):
                c=o.curve_info(index)
                if c is not None:
                    curves.append({'index':index,'start':point(c.start_pt),'end':point(c.end_pt),
                        'center':point(c.center_pt),'radius':c.radius,'bulge':c.bulge,'clockwise':c.is_clockwise})
        return {'points':points,'curves':curves}
    def snapshot(d,depth):
        nonlocal count
        if depth>20:
            raise ValueError('Native geometry hierarchy exceeds the verified depth limit')
        shapes=[]
        for s in d.shapes:
            count+=1
            if count>100000:
                raise ValueError('Native geometry fingerprint exceeds the shape budget')
            lid=s.layer_id
            row={'type':type(s).__name__,'layer':[lid.layer,lid.purpose] if lid else None,'bbox':box(s.bbox)}
            if isinstance(s,(db.Dot,db.Rect)):
                pass
            else:
                poly=s.get_gen_polygon()
                row['outline']=outline(poly.outer_boundary.outline)
                row['holes']=[outline(p.outline) for p in poly.inner_boundaries]
            shapes.append(row)
        instances=[]
        for i in d.instances:
            if i.master is None:
                raise ValueError('Unresolved native artwork: '+i.name)
            instances.append({'name':i.name,'cell':[i.library_name,i.cell_name,i.view_name],
                'origin':point(i.origin),'orient':str(i.orient),'bbox':box(i.bbox),
                'parameters':{p.name:p.value for p in i.parameters},'master':snapshot(i.master,depth+1)})
        ports=[]
        for t in d.terms:
            ports.append({'number':t.number,'name':t.name,'net':t.net.name if t.net else None,
                          'pins':[{'point':point(p.snap_point),'bbox':box(p.get_pinfig_bbox())} for p in t.pins]})
        return {'units':d.unit_name,'shapes':sorted(shapes,key=lambda v:json.dumps(v,sort_keys=True)),
                'instances':sorted(instances,key=lambda v:v['name']),'ports':sorted(ports,key=lambda v:v['number'])}
    data=snapshot(design,0)
    # Include local physical definitions so a changed substrate/material or
    # layer mapping cannot reuse a previously accepted geometry/mesh proof.
    library=Path(de.get_open_library(a['lib']).path).resolve()
    files=[p for p in library.iterdir() if p.is_file() and (
        p.suffix.lower() in ('.subst','.matdb') or p.name in ('library.tech','tech.db','eesof_lib.cfg'))]
    data['physical_library_files']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}
    data['fingerprint_schema']=2
    digest=hashlib.sha256(json.dumps(data,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    return {'design':name,'sha256':digest,'shape_count':count,'snapshot':data}
