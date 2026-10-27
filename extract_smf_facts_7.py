import yaml

def extract_smf_slice_facts(yaml_path):
    with open(yaml_path, 'r') as file:
        config = yaml.load(file, Loader=yaml.BaseLoader)
    
    facts = []
    try:
        smf_name = config['configuration'].get('smfName', 'UNKNOWN_SMF')
        
        # --- AI PIPELINE FIX ---
        # Group SMFs into redundant sets based on the Data Plane UPFs they manage
        up_nodes = config['configuration'].get('userplaneInformation', {}).get('upNodes', {})
        upf_names = sorted([name for name, info in up_nodes.items() if info.get('type') == 'UPF'])
        
        smf_set_id = config['configuration'].get('locality')
        if not smf_set_id:
            smf_set_id = "pool-" + "-".join(upf_names) if upf_names else "default_pool"
        # -----------------------
        
        # 1. Extract ALL supported PLMN tenants for this SMF from plmnList
        supported_plmns = []
        for plmn_item in config['configuration'].get('plmnList', []):
            mcc = plmn_item.get('mcc', '')
            mnc = plmn_item.get('mnc', '')
            supported_plmns.append(mcc + mnc)
            
        if not supported_plmns:
            supported_plmns = ["UNKNOWN_PLMN"]
        
        # 2. Extract Control Plane slices from the native flat list
        cp_slices = []
        for info in config['configuration'].get('snssaiInfos', []):
            if 'sNssai' in info:
                sst = int(info['sNssai']['sst'])
                sd = str(info['sNssai'].get('sd', '000000')).zfill(6)
                cp_slices.append((sst, sd))
        
        # 3. Extract Data Plane and bind to the supported PLMNs
        for node_name, node_info in up_nodes.items():
            if node_info.get('type') == 'UPF':
                for slice_item in node_info.get('sNssaiUpfInfos', []):
                    if 'sNssai' not in slice_item: continue
                    sst = int(slice_item['sNssai']['sst'])
                    sd = str(slice_item['sNssai'].get('sd', '000000')).zfill(6)
                    
                    # Ensure this data plane slice actually exists in the control plane
                    is_in_cp = (sst, sd) in cp_slices
                    plmns_to_bind = supported_plmns if is_in_cp else ["UNKNOWN_PLMN"]
                        
                    for dnn_info in slice_item.get('dnnUpfInfoList', []):
                        dnn = dnn_info.get('dnn', 'internet')
                        for pool in dnn_info.get('pools', []):
                            cidr = pool.get('cidr', '0.0.0.0/0')
                            
                            # Output the 7-arity tuple bound correctly
                            for plmn in plmns_to_bind:
                                facts.append((smf_name, smf_set_id, plmn, sst, sd, dnn, cidr))
                                
    except KeyError as e:
        print(f"Error parsing SMF YAML structure: {e}")
        
    return facts