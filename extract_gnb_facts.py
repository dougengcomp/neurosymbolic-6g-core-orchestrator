import yaml

def extract_gnb_slice_facts(yaml_path):
    with open(yaml_path, 'r') as file:
        # Using BaseLoader to read everything as a literal string
        config = yaml.load(file, Loader=yaml.BaseLoader)
    
    facts = []
    try:
        mcc = config.get('mcc', '')
        mnc = config.get('mnc', '')
        
        slices = config.get('slices', [])
        for slice_item in slices:
            sst_raw = slice_item.get('sst', '')
            sd_raw = slice_item.get('sd', '')
            
            # Normalize SST: strip '0x' if present and convert to int
            if sst_raw.startswith('0x'):
                sst = int(sst_raw, 16)
            else:
                sst = int(sst_raw)
                
            # Normalize SD: strip '0x' if present and pad to 6 characters
            if sd_raw.startswith('0x'):
                sd = sd_raw[2:].zfill(6)
            else:
                sd = sd_raw.zfill(6)
                
            plmn = mcc + mnc
            # 3-arity tuple: (plmn, sst, sd)
            facts.append((plmn, sst, sd))
            
    except KeyError as e:
        print(f"Error parsing gNB YAML structure: {e}")
        
    return facts

if __name__ == "__main__":
    # Adjust this path if your UERANSIM config is located elsewhere
    yaml_file = "/home/ubuntu/UERANSIM/config/free5gc-gnb.yaml"
    print("gNB Facts:", extract_gnb_slice_facts(yaml_file))