from track_model.ibt_parser import IBTFile
ibt = IBTFile('ibtfiles/bmw_seb_latest.ibt')
print(type(ibt.session_info))
si = ibt.session_info
if isinstance(si, dict):
    print('DICT - keys:', list(si.keys())[:10])
elif isinstance(si, str):
    print('STRING - first 200:', si[:200])
else:
    print('OTHER:', type(si))
