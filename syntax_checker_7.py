import ipaddress
import re
import time
import glob
import os

from extract_amf_facts_4 import extract_amf_slice_facts
from extract_nssf_facts import extract_nssf_slice_facts
from extract_smf_facts_7 import extract_smf_slice_facts
from extract_gnb_facts import extract_gnb_slice_facts
from scallop_solver_6 import run_scallop_evaluation, run_dynamic_query

def validate_intent_structure(intent_json):
    """
    Validates structural correctness of the LLM-generated JSON payload.
    Returns a tuple: (status: str, message: str).
    Statuses: "valid", "invalid", "bounds_error", or "clarification_needed"
    """
    if not isinstance(intent_json, dict):
        return "invalid", "Output must be a valid JSON dictionary object."

    action = intent_json.get("action")

    # Gracefully strip optional instance-level keys if LLM includes them on mutate_config
    if action == "mutate_config":
        for opt_key in ("guami", "smf_name", "node"):
            intent_json.pop(opt_key, None)
    elif action in ("validate_current_state", "unsupported"):
        for extra_k in [k for k in list(intent_json.keys()) if k != "action"]:
            intent_json.pop(extra_k, None)

    allowed_root_keys = {"action", "must_exist_in", "must_not_exist_in", "filters", "operation", "nf_targets", "parameters"}
    unauthorized_keys = [k for k in intent_json.keys() if k not in allowed_root_keys]
    
    if unauthorized_keys:
        return "invalid", f"Unauthorized keys found in payload: {unauthorized_keys}. You must strictly adhere to the defined schema."

    allowed_actions = {"validate_current_state", "logical_query", "mutate_config", "unsupported"}
    
    if action not in allowed_actions:
        return "invalid", (
            f"Action '{action}' is strictly forbidden. Allowed actions are: {list(allowed_actions)}. "
            f"For slice lookup or retrieval, use 'logical_query'."
        )

    # 1. Rules for Logical Query
    if action == "logical_query":
        allowed_nfs = {"amf", "smf", "nssf", "gnb"}
        for key in ["must_exist_in", "must_not_exist_in"]:
            comps = intent_json.get(key, [])
            if not isinstance(comps, list):
                return "invalid", f"Field '{key}' must be a list of strings."
            bad_comps = [comp for comp in comps if str(comp).lower() not in allowed_nfs]
            if bad_comps:
                return "invalid", (
                    f"Component(s) {bad_comps} in '{key}' are invalid. Allowed NFs are strictly: ['amf', 'smf', 'nssf', 'gnb'] "
                    f"('access and mobility manager'='amf', 'session management controller'='smf', "
                    f"'slice selection repository'='nssf', 'radio base station'='gnb')."
                )

        filters = intent_json.get("filters", {})
        if not isinstance(filters, dict):
            return "invalid", "Field 'filters' must be a JSON object dictionary."

        # Strip empty strings, None, empty lists [], or empty dicts {} from filters
        keys_to_remove = [k for k, v in filters.items() if v in ("", None, [], {})]
        for k in keys_to_remove:
            del filters[k]

        allowed_filters = {"sst", "sd", "cidr", "plmn", "node", "guami", "smf_name"}
        bad_filters = [k for k in filters.keys() if k not in allowed_filters]
        if bad_filters:
            return "invalid", (
                f"Filter(s) {bad_filters} are unrecognized. Allowed filters are strictly: {sorted(list(allowed_filters))}. "
                f"Map 'service type' to integer filter 'sst' and omit unrecognized keys."
            )
        for k, v in filters.items():
            if k == "sst" and not isinstance(v, int):
                return "invalid", "Filter 'sst' must be an integer, not a string. Do not assign Set IDs to SST."
            if k == "sd" and not isinstance(v, str):
                return "invalid", "Filter 'sd' must be a string."

    # 2. Rules for Mutate Config
    if action == "mutate_config":
        required_keys = ("operation", "nf_targets", "parameters")
        if not all(k in intent_json for k in required_keys):
             return "invalid", "Mutations must explicitly include 'operation', 'nf_targets', and 'parameters'."
        
        if not isinstance(intent_json["nf_targets"], list):
             return "invalid", "'nf_targets' must be a JSON list of network components (e.g., ['amf', 'smf'])."
             
        allowed_nfs = {"amf", "smf", "nssf", "gnb"}
        bad_nfs = [nf for nf in intent_json["nf_targets"] if str(nf).lower() not in allowed_nfs]
        targets = [str(nf).lower() for nf in intent_json["nf_targets"] if str(nf).lower() in allowed_nfs]

        operation = str(intent_json.get("operation", "")).strip().lower().replace(" ", "_")
        params_raw = intent_json.get("parameters", {})
        
        if isinstance(params_raw, dict):
            params_list = [params_raw]
        elif isinstance(params_raw, list):
            params_list = [p for p in params_raw if isinstance(p, dict)]
        else:
            return "invalid", "'parameters' must be a JSON object or a list of JSON objects."

        if not params_list:
            return "invalid", "Mutation 'parameters' cannot be empty."

        allowed_param_keys = {"plmn", "sst", "sd", "cidr"}
        param_errors = []

        for params in params_list:
            for opt_key in ("guami", "smf_name", "node"):
                params.pop(opt_key, None)

            extra_p_keys = [k for k in params.keys() if k not in allowed_param_keys]
            if extra_p_keys:
                param_errors.append(
                    f"Unauthorized key(s) {extra_p_keys} in 'parameters'. Allowed parameter keys are strictly: ['plmn', 'sst', 'sd', 'cidr'] "
                    f"(map 'service type' to integer 'sst' and 'differentiator' to 6-char hex string 'sd')."
                )

            sst = params.get("sst")
            sd = params.get("sd")
            plmn = params.get("plmn")
            cidr = params.get("cidr")
            
            # PHASE 1A: Explicit Adversarial Bounds Checks (Halts pipeline immediately as bounds_error)
            if cidr is not None and str(cidr).strip() != "":
                try:
                    ipaddress.ip_network(cidr, strict=False)
                except ValueError:
                    return "bounds_error", f"Parameter 'cidr' must be a valid IPv4 subnet. Received malformed CIDR: {cidr}"

            if sd is not None and str(sd).strip().upper() == "INVALID":
                return "bounds_error", f"SD must be a 6-character hex string. Received malformed SD: {sd}"

            if sst is not None and str(sst).strip() != "":
                sst_str = str(sst).strip()
                try:
                    sst_int = int(sst_str)
                    if sst_int == 0 or len(sst_str) == 6:
                        param_errors.append(
                            f"Parameter 'sst' must be an integer between 1 and 255, or '' if omitted. "
                            f"Do not place 0 or a 6-character SD differentiator ({sst_str}) into 'sst'."
                        )
                    elif sst_int < 1 or sst_int > 255:
                        return "bounds_error", f"SST must be between 1 and 255. Received out-of-bounds SST: {sst_int}"
                except ValueError:
                    param_errors.append(
                        f"Parameter 'sst' must be an integer (or '' if omitted). "
                        f"For hyphenated 'S-NSSAI X-Y', put integer X in 'sst' and 6-char hex string 'Y' in 'sd'. "
                        f"Do not put service names or hyphenated strings ('{sst_str}') into 'sst'."
                    )

            # PHASE 1B: Recoverable LLM Schema/Type Formatting Checks (Triggers AST Self-Correction Re-Prompt)
            if sd is not None and str(sd).strip() != "":
                sd_str = str(sd).strip()
                if not re.fullmatch(r'[0-9a-fA-F]{6}', sd_str):
                    if re.fullmatch(r'-?\d+-[0-9a-fA-F]{6}', sd_str):
                        x_part, y_part = sd_str.split("-", 1)
                        try:
                            if int(x_part) < 1 or int(x_part) > 255:
                                return "bounds_error", f"SST must be between 1 and 255. Received out-of-bounds SST: {int(x_part)}"
                        except ValueError:
                            pass
                        param_errors.append(
                            f"Invalid S-NSSAI format '{sd_str}' in 'sd'. For hyphenated 'S-NSSAI {x_part}-{y_part}', "
                            f"you MUST set integer \"sst\": {x_part} and 6-character hex string \"sd\": \"{y_part}\"."
                        )
                    else:
                        param_errors.append(
                            f"SD must be a 6-character hex string (e.g., '010203'), or an empty string '' if not explicitly "
                            f"provided as a 6-character hex value in the request. Do not put service names ('{sd_str}') in 'sd'."
                        )

        if bad_nfs or param_errors:
            combined_msgs = []
            if bad_nfs:
                combined_msgs.append(
                    f"Target NF(s) {bad_nfs} are invalid. Allowed NFs are strictly: ['amf', 'smf', 'nssf', 'gnb'] "
                    f"('access and mobility manager'='amf', 'session management controller'='smf', "
                    f"'slice selection repository'='nssf', 'radio base station'='gnb')."
                )
            combined_msgs.extend(param_errors)
            return "invalid", " ".join(combined_msgs)

        for params in params_list:
            sst = params.get("sst")
            sd = params.get("sd")
            plmn = params.get("plmn")
            cidr = params.get("cidr")

            # PHASE 2: Missing Parameter Checks
            if sst is None or str(sst).strip() == "":
                return "clarification_needed", "Parameter 'sst' is missing."
                
            if sd is None or str(sd).strip() == "":
                return "clarification_needed", "Parameter 'sd' is missing."
                
            if plmn is None or str(plmn).strip() == "":
                return "clarification_needed", "Parameter 'plmn' (tenant ID) is missing."

            if "add" in operation and "smf" in targets:
                if cidr is None or str(cidr).strip() == "":
                    return "clarification_needed", f"A CIDR subnet is required when adding a slice to the SMF (SST {sst})."

    return "valid", ""


def validate_current_state(custom_paths=None):
    amf_facts = []
    smf_facts = []
    nssf_facts = []
    gnb_facts = []
    
    t_extract_start = time.perf_counter()
    if custom_paths:
        for p in custom_paths.get('amf', []):
            amf_facts.extend(extract_amf_slice_facts(p))
        for p in custom_paths.get('smf', []):
            smf_facts.extend(extract_smf_slice_facts(p))
        for p in custom_paths.get('nssf', []):
            nssf_facts.extend(extract_nssf_slice_facts(p))
        for p in custom_paths.get('gnb', []):
            gnb_facts.extend(extract_gnb_slice_facts(p))
    else:
        core_path = "/home/ubuntu/free5gc/config/"
        for p in sorted(glob.glob(os.path.join(core_path, "multiAMF", "*.yaml"))):
            amf_facts.extend(extract_amf_slice_facts(p))
        for p in sorted(glob.glob(os.path.join(core_path, "multiSMF", "*.yaml"))):
            smf_facts.extend(extract_smf_slice_facts(p))
        nssf_facts.extend(extract_nssf_slice_facts(os.path.join(core_path, "nssfcfg.yaml")))
        for p in sorted(glob.glob("/home/ubuntu/UERANSIM/config/free5gc-gnb*.yaml")):
            gnb_facts.extend(extract_gnb_slice_facts(p))
    extraction_time = time.perf_counter() - t_extract_start
    
    t_preproc_start = time.perf_counter()
    invalid_sd_facts = []
    
    all_sds = set([f[4] for f in amf_facts] + 
                  [f[2] for f in nssf_facts + gnb_facts] + 
                  [f[4] for f in smf_facts])
    
    for sd in all_sds:
        if len(sd) != 6 or not re.fullmatch(r'[0-9a-fA-F]{6}', sd):
            invalid_sd_facts.append((sd,))

    overlap_facts = []
    invalid_cidr_facts = []
    
    pools = list(set([(f[3], f[4], f[6]) for f in smf_facts]))
    
    for i in range(len(pools)):
        sst1, sd1, cidr1 = pools[i]
        try:
            net1 = ipaddress.ip_network(cidr1, strict=False)
        except ValueError:
            invalid_cidr_facts.append((cidr1,))
            continue
            
        for j in range(i + 1, len(pools)):
            sst2, sd2, cidr2 = pools[j]
            try:
                net2 = ipaddress.ip_network(cidr2, strict=False)
                if net1.overlaps(net2):
                    overlap_facts.append((cidr1, cidr2))
            except ValueError:
                pass

    preproc_time = time.perf_counter() - t_preproc_start

    raw_errors, reasoning_time = run_scallop_evaluation(
        amf_facts, nssf_facts, smf_facts, gnb_facts, 
        list(set(invalid_sd_facts)), list(set(invalid_cidr_facts)), list(set(overlap_facts))
    )
    
    is_valid = not any(any(err_list) for cat in raw_errors.values() for err_list in cat.values())
                
    return {
        "status": "success",
        "action_executed": "validate_current_state",
        "is_valid": is_valid,
        "details": raw_errors,
        "metrics": {
            "fact_extraction_ms": extraction_time * 1000,
            "python_preproc_ms": preproc_time * 1000,
            "datalog_compilation_ms": 0.0,
            "scallop_reasoning_ms": reasoning_time * 1000
        }
    }

def compile_json_to_datalog(query_json):
    pos_schema = {
        "amf": "amf_slice(guami, _, plmn, sst, sd)", 
        "smf": "smf_pool(smf_name, _, plmn, sst, sd, _, cidr)", 
        "nssf": "nssf_slice(plmn, sst, sd)",
        "gnb": "gnb_slice(plmn, sst, sd)"
    }
    
    neg_schema = {
        "amf": "not amf_slice(_, _, plmn, sst, sd)",
        "smf": "not smf_pool(_, _, plmn, sst, sd, _, _)",        
        "nssf": "not nssf_slice(plmn, sst, sd)",
        "gnb": "not gnb_slice(plmn, sst, sd)"
    }
    
    conditions = []
    return_vars = {"sst", "sd"}
    has_positive_relation = False
    
    must_exist = query_json.get("must_exist_in", [])
    for comp in must_exist:
        comp_key = str(comp).lower()
        if comp_key in pos_schema:
            conditions.append(pos_schema[comp_key])
            has_positive_relation = True
            
            if comp_key in ["amf", "smf", "nssf", "gnb"]:
                return_vars.add("plmn")
                
            if comp_key == "smf":
                return_vars.add("cidr")
                return_vars.add("smf_name")
            if comp_key == "amf":
                return_vars.add("guami")
                
    if not has_positive_relation:
        conditions.append("network_slice(plmn, sst, sd)")
        return_vars.add("plmn")
        
    for comp in query_json.get("must_not_exist_in", []):
        comp_key = str(comp).lower()
        if comp_key in neg_schema:
            conditions.append(neg_schema[comp_key])
            
    filters = query_json.get("filters", {})
    if "sst" in filters and filters["sst"] is not None:
        conditions.append(f"sst == {filters['sst']}")
    if "sd" in filters and filters["sd"]:
        conditions.append(f'sd == "{filters["sd"]}"')
    if "cidr" in filters and filters["cidr"] and "cidr" in return_vars:
        conditions.append(f'cidr == "{filters["cidr"]}"')
    if "guami" in filters and filters["guami"] and "guami" in return_vars:
        conditions.append(f'guami == "{filters["guami"]}"')
    if "smf_name" in filters and filters["smf_name"] and "smf_name" in return_vars:
        conditions.append(f'smf_name == "{filters["smf_name"]}"')
        
    plmn_val = filters.get("plmn") or filters.get("node")
    if plmn_val and "plmn" in return_vars:
        conditions.append(f'plmn == "{plmn_val}"')
        
    ordered_vars = ["sst", "sd"]
    if "guami" in return_vars: ordered_vars.append("guami")
    if "smf_name" in return_vars: ordered_vars.append("smf_name")
    if "plmn" in return_vars: ordered_vars.append("plmn")
    if "cidr" in return_vars: ordered_vars.append("cidr")
        
    rule_head = f"user_query({', '.join(ordered_vars)})"
    rule_body = ", ".join(conditions)
    
    dynamic_rule = f"{rule_head} = {rule_body}"
    return dynamic_rule, ordered_vars


def execute_logical_query(intent_json, custom_paths=None):
    t_compile_start = time.perf_counter()
    dynamic_rule, return_vars = compile_json_to_datalog(intent_json)
    compile_time = time.perf_counter() - t_compile_start
    
    if not dynamic_rule:
        return {"status": "error", "message": "Could not compile query from provided JSON."}
        
    t_extract_start = time.perf_counter()
    amf_facts = []
    smf_facts = []
    nssf_facts = []
    gnb_facts = []

    if custom_paths:
        for path in custom_paths.get('amf', []):
            amf_facts.extend(extract_amf_slice_facts(path))
        for path in custom_paths.get('smf', []):
            smf_facts.extend(extract_smf_slice_facts(path))
        for path in custom_paths.get('nssf', []):
            nssf_facts.extend(extract_nssf_slice_facts(path))
        for path in custom_paths.get('gnb', []):
            gnb_facts.extend(extract_gnb_slice_facts(path))
    else:
        core_path = "/home/ubuntu/free5gc/config/"
        for path in sorted(glob.glob(os.path.join(core_path, "multiAMF", "*.yaml"))):
            amf_facts.extend(extract_amf_slice_facts(path))
        for path in sorted(glob.glob(os.path.join(core_path, "multiSMF", "*.yaml"))):
            smf_facts.extend(extract_smf_slice_facts(path))
        nssf_facts = extract_nssf_slice_facts(os.path.join(core_path, "nssfcfg.yaml"))
        for path in sorted(glob.glob("/home/ubuntu/UERANSIM/config/free5gc-gnb*.yaml")):
            gnb_facts.extend(extract_gnb_slice_facts(path))
    extraction_time = time.perf_counter() - t_extract_start
    
    results, reasoning_time = run_dynamic_query(
        amf_facts, nssf_facts, smf_facts, gnb_facts, dynamic_rule, return_vars
    )
    
    if isinstance(results, dict) and results.get("status") == "error":
        return {
            "status": "error",
            "message": results.get("message"),
            "action_executed": "logical_query",
            "rule_compiled": dynamic_rule,
            "metrics": {
                "datalog_compilation_ms": compile_time * 1000,
                "fact_extraction_ms": extraction_time * 1000,
                "python_preproc_ms": 0.0,
                "scallop_reasoning_ms": reasoning_time * 1000
            }
        }
    
    return {
        "status": "success",
        "action_executed": "logical_query",
        "rule_compiled": dynamic_rule,
        "matches_found": len(results),
        "data": results,
        "metrics": {
            "datalog_compilation_ms": compile_time * 1000,
            "fact_extraction_ms": extraction_time * 1000,
            "python_preproc_ms": 0.0,
            "scallop_reasoning_ms": reasoning_time * 1000
        }
    }


def handle_intent(intent_json, custom_paths=None):
    status, message = validate_intent_structure(intent_json)
    if status in ("invalid", "bounds_error"):
        return {"status": "error", "message": message}
    if status == "clarification_needed":
        return {"status": "CLARIFICATION_NEEDED", "message": message}

    action = intent_json.get("action")
    if action == "validate_current_state":
        return validate_current_state(custom_paths=custom_paths)
    elif action == "logical_query":
        return execute_logical_query(intent_json, custom_paths=custom_paths)
    else:
        return {"status": "error", "message": f"Action '{action}' must be executed by the orchestration layer."}