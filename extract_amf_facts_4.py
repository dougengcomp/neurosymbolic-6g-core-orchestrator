import yaml

def extract_amf_slice_facts(yaml_path):
    with open(yaml_path, 'r') as file:
        config = yaml.load(file, Loader=yaml.BaseLoader)
    
    facts = []
    try:
        guami_list = config['configuration'].get('servedGuamiList', [])
        plmn_support_list = config['configuration'].get('plmnSupportList', [])
        
        # 1. Loop through the actual slice definitions FIRST
        for plmn_item in plmn_support_list:
            mcc = plmn_item['plmnId']['mcc']
            mnc = plmn_item['plmnId']['mnc']
            plmn = mcc + mnc
            
            # 2. Find all matching GUAMIs for this specific PLMN
            matching_guamis = []
            for guami_info in guami_list:
                if guami_info['plmnId']['mcc'] == mcc and guami_info['plmnId']['mnc'] == mnc:
                    amf_id = guami_info['amfId']  # e.g., 'cafe00'
                    
                    # 3GPP TS 23.003: Extract Region (8 bits) and Set ID (10 bits)
                    amf_id_int = int(amf_id, 16)
                    region_id = (amf_id_int >> 16) & 0xFF
                    set_id = (amf_id_int >> 6) & 0x3FF
                    
                    pool_id = f"{plmn}-{region_id}-{set_id}"
                    guami = f"{plmn}{amf_id}"
                    matching_guamis.append((guami, pool_id))
            
            # 3. If the mutator added a PLMN but no GUAMI, record the slice's existence anyway
            if not matching_guamis:
                matching_guamis.append(("NO_GUAMI", "NO_POOL"))
                
            # 4. Extract the slices and bind them to the GUAMIs (or the dummy values)
            for slice_item in plmn_item.get('snssaiList', []):
                sst = int(slice_item['sst'])
                sd = str(slice_item.get('sd', '000000')).zfill(6)
                
                for guami, pool_id in matching_guamis:
                    # 5-arity tuple: (guami, pool_id, plmn, sst, sd)
                    facts.append((guami, pool_id, plmn, sst, sd))
                    
    except KeyError as e:
        print(f"Error parsing AMF YAML structure: {e}")
        
    return facts