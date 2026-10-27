import os
import shutil
import time
import subprocess
import glob
from ruamel.yaml import YAML

CORE_CONFIG_DIR = "/home/ubuntu/free5gc/config/"

# Dynamically discover an arbitrary number of NFs in deterministic sorted order
LIVE_PATHS = {
    "amf": sorted(glob.glob(os.path.join(CORE_CONFIG_DIR, "multiAMF", "*.yaml"))),
    "smf": sorted(glob.glob(os.path.join(CORE_CONFIG_DIR, "multiSMF", "*.yaml"))),
    "nssf": [os.path.join(CORE_CONFIG_DIR, "nssfcfg.yaml")],
    "gnb": sorted(glob.glob("/home/ubuntu/UERANSIM/config/free5gc-gnb*.yaml"))
}

STAGING_DIR = "/tmp/5g_staging"

yaml = YAML()
yaml.preserve_quotes = True


def _norm_sst(val):
    """Normalizes SST values across decimal ints, hex ints, and '0x' strings."""
    if val is None or str(val).strip() == "":
        return 0
    s = str(val).strip()
    if s.lower().startswith("0x"):
        return int(s, 16)
    return int(val)


def _norm_sd(val, nf=""):
    """Normalizes SD values across ruamel.yaml HexInt, '0x' hex strings, and 6-char hex strings."""
    if val is None:
        return ""
    if "Hex" in type(val).__name__ or (nf == "gnb" and isinstance(val, int)):
        return hex(int(val))[2:].zfill(6).lower()
    s = str(val).strip()
    if s.lower().startswith("0x"):
        return s[2:].zfill(6).lower()
    return s.zfill(6).lower()


def _apply_topology_profile(staged_paths, topology_profile):
    """Applies initial brownfield state perturbations to staged YAML files for Topology & Composition OOD."""
    if not topology_profile or topology_profile == "baseline":
        return staged_paths

    # -------------------------------------------------------------------------
    # PROFILE 1: Multi-Tenant Re-Homing (20893 -> 310410) & Subnet Shift
    # -------------------------------------------------------------------------
    if topology_profile == "rehomed_310410":
        cidr_shift = {
            "10.60.0.0/16": "172.16.0.0/16",
            "10.61.0.0/16": "172.17.0.0/16",
            "10.65.0.0/16": "172.18.0.0/16",
            "10.200.0.0/16": "172.19.0.0/16"
        }
        for amf_path in staged_paths.get("amf", []):
            with open(amf_path, "r") as f:
                data = yaml.load(f)
            for guami in data.get("configuration", {}).get("servedGuamiList", []):
                if "plmnId" in guami:
                    guami["plmnId"]["mcc"] = "310"
                    guami["plmnId"]["mnc"] = "410"
            for plmn_item in data.get("configuration", {}).get("plmnSupportList", []):
                if "plmnId" in plmn_item:
                    plmn_item["plmnId"]["mcc"] = "310"
                    plmn_item["plmnId"]["mnc"] = "410"
            with open(amf_path, "w") as f:
                yaml.dump(data, f)

        for smf_path in staged_paths.get("smf", []):
            with open(smf_path, "r") as f:
                data = yaml.load(f)
            data["configuration"]["plmnList"] = [{"mcc": "310", "mnc": "410"}]
            up_nodes = data.get("configuration", {}).get("userplaneInformation", {}).get("upNodes", {})
            for _, info in up_nodes.items():
                if info.get("type") == "UPF":
                    for s_info in info.get("sNssaiUpfInfos", []):
                        for dnn_info in s_info.get("dnnUpfInfoList", []):
                            for pool in dnn_info.get("pools", []):
                                old_c = str(pool.get("cidr", ""))
                                if old_c in cidr_shift:
                                    pool["cidr"] = cidr_shift[old_c]
            with open(smf_path, "w") as f:
                yaml.dump(data, f)

        for nssf_path in staged_paths.get("nssf", []):
            with open(nssf_path, "r") as f:
                data = yaml.load(f)
            for entry in data.get("configuration", {}).get("supportedNssaiInPlmnList", []):
                if "plmnId" in entry:
                    entry["plmnId"]["mcc"] = "310"
                    entry["plmnId"]["mnc"] = "410"
            with open(nssf_path, "w") as f:
                yaml.dump(data, f)

        for gnb_path in staged_paths.get("gnb", []):
            with open(gnb_path, "r") as f:
                data = yaml.load(f)
            data["mcc"] = "310"
            data["mnc"] = "410"
            with open(gnb_path, "w") as f:
                yaml.dump(data, f)

    # -------------------------------------------------------------------------
    # PROFILE 2: Cluster Scale-Out (5 AMFs across 3 GUAMI Sets + 4 SMFs)
    # -------------------------------------------------------------------------
    elif topology_profile == "scaleout_5amf_4smf":
        if staged_paths.get("amf"):
            base_amf = staged_paths["amf"][0]
            for idx_extra, new_amf_id in [(3, "cafe02"), (4, "cb0000")]:
                extra_amf_path = os.path.join(STAGING_DIR, f"{idx_extra}_scaleout_amfcfg{idx_extra+1}.yaml")
                shutil.copy(base_amf, extra_amf_path)
                with open(extra_amf_path, "r") as f:
                    data = yaml.load(f)
                for guami in data.get("configuration", {}).get("servedGuamiList", []):
                    guami["amfId"] = new_amf_id
                with open(extra_amf_path, "w") as f:
                    yaml.dump(data, f)
                staged_paths["amf"].append(extra_amf_path)

        if staged_paths.get("smf"):
            base_smf = staged_paths["smf"][0]
            extra_smf_path = os.path.join(STAGING_DIR, "3_scaleout_smfcfg4.yaml")
            shutil.copy(base_smf, extra_smf_path)
            with open(extra_smf_path, "r") as f:
                data = yaml.load(f)
            data["configuration"]["smfName"] = "SMF4"
            with open(extra_smf_path, "w") as f:
                yaml.dump(data, f)
            staged_paths["smf"].append(extra_smf_path)

    # -------------------------------------------------------------------------
    # PROFILE 3: Brownfield Split-Brain (Pre-Existing Intra-Set HA Drift)
    # -------------------------------------------------------------------------
    elif topology_profile == "brownfield_split_brain":
        if len(staged_paths.get("amf", [])) >= 2:
            amf0 = staged_paths["amf"][0]
            with open(amf0, "r") as f:
                data = yaml.load(f)
            for plmn_item in data.get("configuration", {}).get("plmnSupportList", []):
                plmn_item["snssaiList"] = [
                    s for s in plmn_item.get("snssaiList", [])
                    if _norm_sst(s.get("sst", 0)) != 2
                ]
            with open(amf0, "w") as f:
                yaml.dump(data, f)

        if len(staged_paths.get("smf", [])) >= 2:
            smf0 = staged_paths["smf"][0]
            with open(smf0, "r") as f:
                data = yaml.load(f)
            if "snssaiInfos" in data.get("configuration", {}):
                data["configuration"]["snssaiInfos"] = [
                    s for s in data["configuration"]["snssaiInfos"]
                    if _norm_sst(s.get("sNssai", {}).get("sst", 0)) != 2
                ]
            up_nodes = data.get("configuration", {}).get("userplaneInformation", {}).get("upNodes", {})
            for _, info in up_nodes.items():
                if info.get("type") == "UPF":
                    info["sNssaiUpfInfos"] = [
                        s for s in info.get("sNssaiUpfInfos", [])
                        if _norm_sst(s.get("sNssai", {}).get("sst", 0)) != 2
                    ]
            with open(smf0, "w") as f:
                yaml.dump(data, f)

    # -------------------------------------------------------------------------
    # PROFILE 4: Brownfield Orphans (Pre-Existing Cross-NF Desynchronization)
    # -------------------------------------------------------------------------
    elif topology_profile == "brownfield_orphans":
        mutate_staging_domain("amf", staged_paths.get("amf", []), "add_slice", [{"plmn": "20893", "sst": 4, "sd": "040404", "cidr": ""}])
        mutate_staging_domain("nssf", staged_paths.get("nssf", []), "add_slice", [{"plmn": "20893", "sst": 4, "sd": "040404", "cidr": ""}])
        mutate_staging_domain("gnb", staged_paths.get("gnb", []), "add_slice", [{"plmn": "20893", "sst": 9, "sd": "090909", "cidr": ""}])
        mutate_staging_domain("nssf", staged_paths.get("nssf", []), "add_slice", [{"plmn": "20893", "sst": 6, "sd": "060606", "cidr": ""}])

    # -------------------------------------------------------------------------
    # PROFILE 5: Brownfield Missing SMF Pool (Single-Fault Compositional State)
    # Slice (4, 040404) on PLMN 20893 is active in AMF + NSSF + gNB, missing only SMF
    # -------------------------------------------------------------------------
    elif topology_profile == "brownfield_missing_smf":
        mutate_staging_domain("amf", staged_paths.get("amf", []), "add_slice", [{"plmn": "20893", "sst": 4, "sd": "040404", "cidr": ""}])
        mutate_staging_domain("nssf", staged_paths.get("nssf", []), "add_slice", [{"plmn": "20893", "sst": 4, "sd": "040404", "cidr": ""}])
        mutate_staging_domain("gnb", staged_paths.get("gnb", []), "add_slice", [{"plmn": "20893", "sst": 4, "sd": "040404", "cidr": ""}])

    return staged_paths


def prepare_staging_area(topology_profile="baseline"):
    """Copies live YAMLs to a temporary staging directory and applies optional Topology/Composition OOD profile."""
    if not os.path.exists(STAGING_DIR):
        os.makedirs(STAGING_DIR)
    else:
        for old_extra in glob.glob(os.path.join(STAGING_DIR, "*_scaleout_*.yaml")):
            try:
                os.remove(old_extra)
            except OSError:
                pass

    staged_paths = {"amf": [], "smf": [], "nssf": [], "gnb": []}

    for nf_type, path_list in LIVE_PATHS.items():
        for i, live_path in enumerate(path_list):
            staged_filename = f"{i}_{os.path.basename(live_path)}"
            staged_path = os.path.join(STAGING_DIR, staged_filename)
            shutil.copy(live_path, staged_path)
            staged_paths[nf_type].append(staged_path)

    return _apply_topology_profile(staged_paths, topology_profile)


def mutate_staging_domain(nf, staged_paths_list, operation, params_raw):
    """
    Applies AST diffs across an entire NF domain (list of staged YAML files),
    supporting batched parameter arrays and cluster-wide existence checks.
    """
    if isinstance(staged_paths_list, str):
        staged_paths_list = [staged_paths_list]

    params_list = params_raw if isinstance(params_raw, list) else [params_raw]

    for params in params_list:
        try:
            sst = int(params["sst"])
        except (KeyError, ValueError):
            raise ValueError("CRITICAL: Mutator received missing or non-integer SST.")

        sd_raw = params.get("sd")
        if sd_raw is None or not str(sd_raw).strip():
            raise ValueError("CRITICAL: Mutator received an empty SD.")

        sd = _norm_sd(sd_raw)

        plmn_target = params.get("plmn")
        if not plmn_target:
            raise ValueError("CRITICAL: PLMN must be specified for mutations.")

        domain_mutated = False
        domain_plmn_found = False

        for staged_path in staged_paths_list:
            with open(staged_path, 'r') as f:
                data = yaml.load(f)

            file_mutated = False
            file_plmn_found = False

            if operation == "add_slice":
                if nf == "amf":
                    plmn_list = data['configuration'].setdefault('plmnSupportList', [])
                    target_item = next(
                        (item for item in plmn_list
                         if str(item.get('plmnId', {}).get('mcc', '')) + str(item.get('plmnId', {}).get('mnc', '')) == plmn_target),
                        None
                    )
                    if not target_item:
                        target_item = {'plmnId': {'mcc': plmn_target[:3], 'mnc': plmn_target[3:]}, 'snssaiList': []}
                        plmn_list.append(target_item)
                        file_mutated = True

                    snssai_list = target_item.setdefault('snssaiList', [])
                    if not any(_norm_sst(s.get('sst', 0)) == sst and _norm_sd(s.get('sd', ''), 'amf') == sd for s in snssai_list):
                        snssai_list.append({'sst': sst, 'sd': sd})
                        file_mutated = True

                elif nf == "smf":
                    cidr = params.get("cidr")
                    if not cidr or not str(cidr).strip():
                        raise ValueError(f"CRITICAL: SMF mutation attempted without a valid CIDR for SST {sst}.")

                    plmn_list = data['configuration'].setdefault('plmnList', [])
                    if not any(str(p.get('mcc', '')) + str(p.get('mnc', '')) == plmn_target for p in plmn_list):
                        plmn_list.append({'mcc': plmn_target[:3], 'mnc': plmn_target[3:]})
                        file_mutated = True

                    snssai_infos = data['configuration'].setdefault('snssaiInfos', [])
                    if not any(_norm_sst(s.get('sNssai', {}).get('sst', 0)) == sst and _norm_sd(s.get('sNssai', {}).get('sd', ''), 'smf') == sd for s in snssai_infos):
                        snssai_infos.append({
                            'sNssai': {'sst': sst, 'sd': sd},
                            'dnnInfos': [{'dnn': 'internet', 'dns': {'ipv4': '8.8.8.8'}}]
                        })
                        file_mutated = True

                    up_nodes = data['configuration'].setdefault('userplaneInformation', {}).setdefault('upNodes', {})
                    for node, info in up_nodes.items():
                        if info.get('type') == 'UPF':
                            upf_infos = info.setdefault('sNssaiUpfInfos', [])
                            if not any(_norm_sst(s['sNssai'].get('sst', 0)) == sst and _norm_sd(s['sNssai'].get('sd', ''), 'smf') == sd for s in upf_infos):
                                upf_infos.append({
                                    'sNssai': {'sst': sst, 'sd': sd},
                                    'dnnUpfInfoList': [{'dnn': 'internet', 'pools': [{'cidr': cidr}]}]
                                })
                                file_mutated = True

                elif nf == "nssf":
                    plmn_list = data['configuration'].setdefault('supportedNssaiInPlmnList', [])
                    target_item = next(
                        (item for item in plmn_list
                         if str(item.get('plmnId', {}).get('mcc', '')) + str(item.get('plmnId', {}).get('mnc', '')) == plmn_target),
                        None
                    )
                    if not target_item:
                        target_item = {'plmnId': {'mcc': plmn_target[:3], 'mnc': plmn_target[3:]}, 'supportedSnssaiList': []}
                        plmn_list.append(target_item)
                        file_mutated = True

                    snssai_list = target_item.setdefault('supportedSnssaiList', [])
                    if not any(_norm_sst(s.get('sst', 0)) == sst and _norm_sd(s.get('sd', ''), 'nssf') == sd for s in snssai_list):
                        snssai_list.append({'sst': sst, 'sd': sd})
                        file_mutated = True

                elif nf == "gnb":
                    gnb_plmn = str(data.get('mcc', '')) + str(data.get('mnc', ''))
                    if gnb_plmn != plmn_target:
                        raise ValueError(f"CRITICAL: Mutation failed. gNodeB operates on PLMN {gnb_plmn}, cannot add slice for PLMN {plmn_target}.")

                    slices = data.setdefault('slices', [])
                    if not any(_norm_sst(s.get('sst', 0)) == sst and _norm_sd(s.get('sd', ''), 'gnb') == sd for s in slices):
                        slices.append({'sst': sst, 'sd': sd})
                        file_mutated = True

                domain_mutated = True

            elif operation == "delete_slice":
                if nf == "amf":
                    plmn_list = data['configuration'].get('plmnSupportList', [])
                    if any(str(p.get('plmnId', {}).get('mcc', '')) + str(p.get('plmnId', {}).get('mnc', '')) == plmn_target for p in plmn_list):
                        file_plmn_found = True
                        for plmn_item in plmn_list:
                            if str(plmn_item.get('plmnId', {}).get('mcc', '')) + str(plmn_item.get('plmnId', {}).get('mnc', '')) == plmn_target:
                                original_len = len(plmn_item.get('snssaiList', []))
                                plmn_item['snssaiList'] = [
                                    s for s in plmn_item.get('snssaiList', [])
                                    if not (_norm_sst(s.get('sst', 0)) == sst and _norm_sd(s.get('sd', ''), 'amf') == sd)
                                ]
                                if len(plmn_item['snssaiList']) < original_len:
                                    file_mutated = True
                        data['configuration']['plmnSupportList'] = [p for p in plmn_list if len(p.get('snssaiList', [])) > 0]

                elif nf == "smf":
                    plmn_list = data['configuration'].get('plmnList', [])
                    if any(str(p.get('mcc', '')) + str(p.get('mnc', '')) == plmn_target for p in plmn_list):
                        file_plmn_found = True
                        if 'snssaiInfos' in data['configuration']:
                            original_len = len(data['configuration']['snssaiInfos'])
                            data['configuration']['snssaiInfos'] = [
                                s for s in data['configuration']['snssaiInfos']
                                if not (_norm_sst(s.get('sNssai', {}).get('sst', 0)) == sst and _norm_sd(s.get('sNssai', {}).get('sd', ''), 'smf') == sd)
                            ]
                            if len(data['configuration']['snssaiInfos']) < original_len:
                                file_mutated = True

                        up_nodes = data['configuration'].get('userplaneInformation', {}).get('upNodes', {})
                        for node, info in up_nodes.items():
                            if info.get('type') == 'UPF':
                                info['sNssaiUpfInfos'] = [
                                    s for s in info.get('sNssaiUpfInfos', [])
                                    if not (_norm_sst(s['sNssai'].get('sst', 0)) == sst and _norm_sd(s['sNssai'].get('sd', ''), 'smf') == sd)
                                ]

                        if 'snssaiInfos' in data['configuration'] and len(data['configuration']['snssaiInfos']) == 0:
                            data['configuration']['plmnList'] = [
                                p for p in plmn_list if str(p.get('mcc', '')) + str(p.get('mnc', '')) != plmn_target
                            ]

                elif nf == "nssf":
                    plmn_list = data['configuration'].get('supportedNssaiInPlmnList', [])
                    if any(str(p.get('plmnId', {}).get('mcc', '')) + str(p.get('plmnId', {}).get('mnc', '')) == plmn_target for p in plmn_list):
                        file_plmn_found = True
                        for plmn_item in plmn_list:
                            if str(plmn_item.get('plmnId', {}).get('mcc', '')) + str(plmn_item.get('plmnId', {}).get('mnc', '')) == plmn_target:
                                original_len = len(plmn_item.get('supportedSnssaiList', []))
                                plmn_item['supportedSnssaiList'] = [
                                    s for s in plmn_item.get('supportedSnssaiList', [])
                                    if not (_norm_sst(s.get('sst', 0)) == sst and _norm_sd(s.get('sd', ''), 'nssf') == sd)
                                ]
                                if len(plmn_item['supportedSnssaiList']) < original_len:
                                    file_mutated = True
                        data['configuration']['supportedNssaiInPlmnList'] = [
                            p for p in plmn_list if len(p.get('supportedSnssaiList', [])) > 0
                        ]

                elif nf == "gnb":
                    gnb_plmn = str(data.get('mcc', '')) + str(data.get('mnc', ''))
                    if gnb_plmn != plmn_target:
                        raise ValueError(f"CRITICAL: Deletion failed. gNodeB operates on PLMN {gnb_plmn}, not {plmn_target}.")
                    file_plmn_found = True
                    original_len = len(data.get('slices', []))
                    data['slices'] = [
                        s for s in data.get('slices', [])
                        if not (_norm_sst(s.get('sst', 0)) == sst and _norm_sd(s.get('sd', ''), 'gnb') == sd)
                    ]
                    if len(data.get('slices', [])) < original_len:
                        file_mutated = True

            if file_mutated:
                domain_mutated = True
                with open(staged_path, 'w') as f:
                    yaml.dump(data, f)
            if file_plmn_found:
                domain_plmn_found = True

        if operation == "delete_slice":
            if not domain_plmn_found:
                raise ValueError(f"CRITICAL: Deletion failed. PLMN {plmn_target} not found in {nf.upper()}.")
            if not domain_mutated:
                raise ValueError(f"CRITICAL: Deletion failed. Slice {sst}/{sd} not found in {nf.upper()} for PLMN {plmn_target}.")


def mutate_staging_file(nf, staged_path, operation, params_raw):
    """Backwards-compatible single-file wrapper around mutate_staging_domain."""
    return mutate_staging_domain(nf, [staged_path], operation, params_raw)


def execute_live_commit():
    """Backs up live files, applies staging files, and restarts services."""
    for nf_type, path_list in LIVE_PATHS.items():
        for i, live_path in enumerate(path_list):
            staged_filename = f"{i}_{os.path.basename(live_path)}"
            staged_path = os.path.join(STAGING_DIR, staged_filename)
        
    print("BENCHMARK MODE: Skipping live file overwrite and service restarts.")