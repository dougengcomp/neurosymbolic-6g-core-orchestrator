import time
import scallopy

def run_scallop_evaluation(amf_facts, nssf_facts, smf_facts, gnb_facts, python_sd_errors, python_cidr_errors, python_overlaps):
    ctx = scallopy.ScallopContext()
    
    # 1. Define Relations (6-arity for both AMF and SMF to support true disjoint Sets)
    ctx.add_relation("amf_slice", (str, str, str, int, str)) # guami, pool_id, plmn, sst, sd
    ctx.add_relation("nssf_slice", (str, int, str))          # plmn, sst, sd
    ctx.add_relation("smf_pool", (str, str, str, int, str, str, str)) # smf_name, smf_set_id, plmn, sst, sd, dnn, cidr
    ctx.add_relation("gnb_slice", (str, int, str))           # plmn, sst, sd
    
    ctx.add_relation("python_invalid_sd", (str,))
    ctx.add_relation("python_invalid_cidr", (str,))
    ctx.add_relation("python_subnet_overlap", (str, str))
    
    # 2. Inject Facts
    ctx.add_facts("amf_slice", amf_facts)
    ctx.add_facts("nssf_slice", nssf_facts)
    ctx.add_facts("smf_pool", smf_facts)
    ctx.add_facts("gnb_slice", gnb_facts)
    ctx.add_facts("python_invalid_sd", python_sd_errors)
    ctx.add_facts("python_invalid_cidr", python_cidr_errors)
    ctx.add_facts("python_subnet_overlap", python_overlaps)
    
    # 3. Guardrails: Topological Synchronization (STRICT PLMN BINDING)
    ctx.add_rule("orphan_amf(plmn, sst, sd) = amf_slice(_, _, plmn, sst, sd), not nssf_slice(plmn, sst, sd)")
    ctx.add_rule("orphan_nssf(plmn, sst, sd) = nssf_slice(plmn, sst, sd), not amf_slice(_, _, plmn, sst, sd)")
    ctx.add_rule("orphan_smf(plmn, sst, sd) = smf_pool(_, _, plmn, sst, sd, _, _), not amf_slice(_, _, plmn, sst, sd)")
    ctx.add_rule("orphan_gnb(plmn, sst, sd) = gnb_slice(plmn, sst, sd), not amf_slice(_, _, plmn, sst, sd)")
    ctx.add_rule("missing_smf_pool(plmn, sst, sd) = amf_slice(_, _, plmn, sst, sd), not smf_pool(_, _, plmn, sst, sd, _, _)")

    # 4. Guardrails: Well-Formedness
    # Ensure network_slice binds the PLMN globally
    ctx.add_rule("network_slice(plmn, sst, sd) = amf_slice(_, _, plmn, sst, sd)")
    ctx.add_rule("network_slice(plmn, sst, sd) = smf_pool(_, _, plmn, sst, sd, _, _)")
    ctx.add_rule("network_slice(plmn, sst, sd) = nssf_slice(plmn, sst, sd)")
    ctx.add_rule("network_slice(plmn, sst, sd) = gnb_slice(plmn, sst, sd)")

    # Bounds check against ANY component
    ctx.add_rule("invalid_sst(plmn, sst) = network_slice(plmn, sst, _), sst < 1")
    ctx.add_rule("invalid_sst(plmn, sst) = network_slice(plmn, sst, _), sst > 255")

    # Format check against ANY component
    ctx.add_rule("malformed_sd(plmn, sst, sd) = network_slice(plmn, sst, sd), python_invalid_sd(sd)")
    ctx.add_rule("malformed_cidr(plmn, sst, sd, cidr) = smf_pool(_, _, plmn, sst, sd, _, cidr), python_invalid_cidr(cidr)")
    
    ctx.add_rule("""
        pool_collision(sst1, sd1, sst2, sd2, c1, c2) = 
            smf_pool(_, _, _, sst1, sd1, _, c1), 
            smf_pool(_, _, _, sst2, sd2, _, c2), 
            python_subnet_overlap(c1, c2),
            (sst1 != sst2 or sd1 != sd2)
    """)
    
    ctx.add_rule("""
        intra_amf_mismatch(guami1, guami2, plmn, sst, sd) = 
            amf_slice(guami1, pool_id, plmn, sst, sd), 
            amf_slice(guami2, pool_id, _, _, _), 
            not amf_slice(guami2, pool_id, plmn, sst, sd)
    """)
    
    ctx.add_rule("""
        intra_smf_mismatch(smf1, smf2, plmn, sst, sd) = 
            smf_pool(smf1, smf_set_id, plmn, sst, sd, _, _), 
            smf_pool(smf2, smf_set_id, _, _, _, _, _), 
            not smf_pool(smf2, smf_set_id, plmn, sst, sd, _, _)
    """)
    
    t_start = time.perf_counter()
    ctx.run()
    reasoning_time = time.perf_counter() - t_start
    
    results = {
        "topology_errors": {
            "missing_in_nssf": list(ctx.relation("orphan_amf")),
            "missing_in_amf": list(ctx.relation("orphan_nssf")),
            "missing_signaling_for_smf": list(ctx.relation("orphan_smf")),
            "gnb_broadcasting_unsupported_slice": list(ctx.relation("orphan_gnb")),
            "missing_smf_data_pool": list(ctx.relation("missing_smf_pool")),
            "intra_amf_mismatch": list(ctx.relation("intra_amf_mismatch")),
            "intra_smf_mismatch": list(ctx.relation("intra_smf_mismatch"))
        },
        "format_errors": {
            "invalid_ssts": list(ctx.relation("invalid_sst")),
            "malformed_sds": list(ctx.relation("malformed_sd")),
            "malformed_cidrs": list(ctx.relation("malformed_cidr"))
        },
        "routing_errors": {
            "pool_collisions": list(ctx.relation("pool_collision"))
        }
    }
    
    return results, reasoning_time

def run_dynamic_query(amf_facts, nssf_facts, smf_facts, gnb_facts, dynamic_rule_string, return_vars):
    ctx = scallopy.ScallopContext()
    
    # --- UPDATED 3GPP PLMN ARITIES ---
    ctx.add_relation("amf_slice", (str, str, str, int, str))     # 5-arity: guami, pool_id, plmn, sst, sd
    ctx.add_relation("nssf_slice", (str, int, str))              # 3-arity: plmn, sst, sd
    ctx.add_relation("smf_pool", (str, str, str, int, str, str, str)) # smf_name, smf_set_id, plmn, sst, sd, dnn, cidr
    ctx.add_relation("gnb_slice", (str, int, str))               # 3-arity: plmn, sst, sd
    
    # --- UPDATED WILDCARDS ---
    ctx.add_rule("network_slice(plmn, sst, sd) = amf_slice(_, _, plmn, sst, sd)")
    ctx.add_rule("network_slice(plmn, sst, sd) = smf_pool(_, _, plmn, sst, sd, _, _)")
    ctx.add_rule("network_slice(plmn, sst, sd) = nssf_slice(plmn, sst, sd)")
    ctx.add_rule("network_slice(plmn, sst, sd) = gnb_slice(plmn, sst, sd)")
    
    ctx.add_facts("amf_slice", amf_facts)
    ctx.add_facts("nssf_slice", nssf_facts)
    ctx.add_facts("smf_pool", smf_facts)
    ctx.add_facts("gnb_slice", gnb_facts)
    
    t_start = time.perf_counter()
    try:
        ctx.add_rule(dynamic_rule_string)
        ctx.run()
    except Exception as e:
        reasoning_time = time.perf_counter() - t_start
        return {"status": "error", "message": f"Scallop rule/execution error: {str(e)}"}, reasoning_time
        
    reasoning_time = time.perf_counter() - t_start
    
    results = []
    try:
        query_output = list(ctx.relation("user_query"))
        for row in query_output:
            row_dict = {return_vars[i]: row[i] for i in range(len(return_vars))}
            results.append(row_dict)
    except Exception as e:
        return {"status": "error", "message": f"Scallop evaluation projection failed: {str(e)}"}, reasoning_time
        
    return results, reasoning_time