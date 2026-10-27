import yaml

def extract_nssf_slice_facts(yaml_path):
    with open(yaml_path, 'r') as file:
        config = yaml.load(file, Loader=yaml.BaseLoader)
    
    facts = []
    try:
        # NSSF stores slices under a different hierarchy than the AMF
        plmn_list = config['configuration']['supportedNssaiInPlmnList']
        
        for entry in plmn_list:
            mcc = entry['plmnId']['mcc']
            mnc = entry['plmnId']['mnc']
            
            plmn = mcc + mnc
            for slice_item in entry['supportedSnssaiList']:
                sst = int(slice_item['sst'])
                sd = slice_item['sd'].zfill(6)
                
                # 3-arity tuple: (plmn, sst, sd)
                facts.append((plmn, sst, sd))
                
    except KeyError as e:
        print(f"Error parsing NSSF YAML structure: {e}")
        
    return facts

if __name__ == "__main__":
    yaml_file = "/home/ubuntu/free5gc/config/nssfcfg.yaml"
    print("NSSF Facts:", extract_nssf_slice_facts(yaml_file))