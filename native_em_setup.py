"""Normalize an owned native EM Setup before fingerprinting or dispatching it."""


def save_native_em_setup(a):
    import hashlib
    import json
    import re
    import xml.etree.ElementTree as ET
    from decimal import Decimal, InvalidOperation
    from pathlib import Path
    from keysight.ads import de
    try:
        from PySide6.QtWidgets import QApplication
        from PySide6.QtGui import QAction
        from shiboken6 import isValid
    except ImportError:
        from PySide2.QtWidgets import QApplication,QAction
        from shiboken2 import isValid
    ws=Path(de.active_workspace().path).resolve()
    xml=Path(de._pde.get_path_to_cellview(a['lib'],a['cell'],a['setup'])).resolve()/'emStateFile.xml'
    if not xml.is_relative_to(ws):
        raise ValueError('Native setup normalization requires a workspace-local EM Setup')
    before=xml.read_bytes()
    digest=hashlib.sha256(before).hexdigest()
    history=ws/'.ads-mcp'/'configuration_history'
    history.mkdir(parents=True,exist_ok=True)
    backup=history/('native-save-'+digest+'.xml')
    if not backup.exists():backup.write_bytes(before)
    identity=a['lib']+':'+a['cell']+':'+a['setup']
    de.ael.deem_open_em_setup(a['lib'],a['cell'],a['setup'])
    QApplication.processEvents()
    windows=[w for w in QApplication.topLevelWidgets() if isValid(w) and w.isVisible()
        and w.metaObject().className()=='MainWindowWidget_qt'
        and re.fullmatch(re.escape(identity)+r'\s*\*?\s*\(EM Setup for simulation\)',w.windowTitle())]
    if len(windows)!=1:raise RuntimeError('A unique native EM Setup editor could not be identified')
    actions=[x for x in windows[0].findChildren(QAction) if x.objectName()=='actionSave' and x.isEnabled()]
    if len(actions)!=1:raise RuntimeError('Native EM Setup Save action is unavailable')
    actions[0].trigger()
    after=xml.read_bytes()
    def flat(data):
        out={}
        def visit(node,path):
            text=(node.text or '').strip()
            if text:out[path]=text
            counts={}
            for c in node:
                counts[c.tag]=counts.get(c.tag,0)+1
                visit(c,path+'/'+c.tag+'['+str(counts[c.tag])+']')
        visit(ET.fromstring(data),'EmSimSetup')
        return out
    left,right=flat(before),flat(after)
    microwave=ET.fromstring(before).findtext('MainWindow/SimSetupOverview/simulator')=='0'
    changed={k:{'before':v,'after':right.get(k)} for k,v in left.items() if right.get(k)!=v}
    unexpected={}
    for k,change in changed.items():
        old,new=change['before'],change['after']
        # ADS normalizes numeric spellings and dataset decoration on native save.
        # Keep every physical, frequency and port value under explicit comparison.
        if k.endswith(('/addDecoration[1]','/ds_addDecoration[1]')):continue
        # This path dispatches Momentum microwave, never the inactive FEM setup.
        if microwave and '/FemOptions[1]/' in k:continue
        if (microwave and '/MomOptions[1]/optionsMap[1]/' in k
            and k.endswith(('/keyName[1]','/optionName[1]'))
            and old=='RFBoard' and new=='<(inPreparation)>'
            and len(ET.fromstring(before).findall('MainWindow/MomOptions/optionsMap/MomOptionDef'))==1):
            continue
        try:
            old_numbers=[Decimal(x) for x in old.split(':')]
            new_numbers=[Decimal(x) for x in (new or '').split(':')]
            if len(old_numbers)==len(new_numbers) and all(abs(x-y)<=max(abs(x),abs(y),Decimal('1'))*Decimal('1e-12') for x,y in zip(old_numbers,new_numbers)):
                continue
        except InvalidOperation:
            pass
        unexpected[k]=change
    result={'setup_file':str(xml),'before_sha256':digest,'after_sha256':hashlib.sha256(after).hexdigest(),
        'backup':str(backup),'native_save':True,'changed_existing_fields':changed,
        'native_added_field_count':len(right.keys()-left.keys()),'unexpected_changes':unexpected}
    evidence=history/('native-save-'+digest+'.json')
    evidence.write_text(json.dumps(result,indent=2),encoding='utf-8')
    if unexpected:
        raise RuntimeError('Native EM Setup save changed explicit settings; inspect '+str(evidence))
    # This exact setup is now saved; close only its editor to avoid accumulated windows.
    result['editor_closed']=bool(windows[0].close())
    return result
