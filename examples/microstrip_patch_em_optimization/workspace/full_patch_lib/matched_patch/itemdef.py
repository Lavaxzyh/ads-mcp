from keysight.ads import de
def create_itemdef(cell):
    fs=de.db.model_lib.formsets['StdFormSet']
    params=[]
    for name,value in [('Wm','0.7035945810262776'),('Lm','19.96929794249173')]:
        p=de.db.ModelParam(name,name,fs,de.db.ModelUnitType.NO_UNIT,de.db.ModelParamType.REAL)
        p.default_value=de.db.std_string_param(value)
        params.append(p)
    model=de.db.ModelDef(cell.name,'Full EM feed, matching line and patch')
    model.is_sub_design=True
    model.inst_name_prefix='X'
    model.parameters=params
    de.add_model_definition(cell.library,model)
