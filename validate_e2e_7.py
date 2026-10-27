import scallopy
import ipaddress
import re
import glob
import os
from extract_amf_facts_4 import extract_amf_slice_facts
from extract_nssf_facts import extract_nssf_slice_facts
from extract_smf_facts_7 import extract_smf_slice_facts
from extract_gnb_facts import extract_gnb_slice_facts

def run_e2e_validator():
    core_path = "/home/ubuntu/free5gc/config/"
    gnb_file = "/home/ubuntu/UERANSIM/config/free5gc-gnb.yaml"
    
    amf_facts = []
    for path in glob.glob(os.path.join(core_path, "multiAMF", "*.yaml")):
        amf_facts.extend(extract_amf_slice_facts(path))
        
    smf_facts = []
    for path in glob.glob(os.path.join(core_path, "multiSMF", "*.yaml")):
        smf_facts.extend(extract_smf_slice_facts(path))
        
    nssf_facts = extract_nssf_slice_facts(os.path.join(core_path, "nssfcfg.yaml"))
    gnb_facts = []
    for path in glob.glob("/home/ubuntu/UERANSIM/config/free5gc-gnb*.yaml"):
        gnb_facts.extend(extract_gnb_slice_facts(path))
    
    invalid_sd_facts = []
    
    # --- FIX 1: Corrected tuple indices for SD extraction ---
    # AMF is f[4] due to (guami, pool_id, plmn, sst, sd)
    # NSSF/gNB is f[2] due to (plmn, sst, sd)
    # SMF is f[3] due to (smf_name, smf_set_id, sst, sd, dnn, cidr)
    all_sds = set([f[4] for f in amf_facts] + 
                  [f[2] for f in nssf_facts + gnb_facts] + 
                  [f[4] for f in smf_facts])
    
    for sd in all_sds:
        if len(sd) != 6 or not re.fullmatch(r'[0-9a-fA-F]{6}', sd):
            invalid_sd_facts.append((sd,))

    overlap_facts = []
    invalid_cidr_facts = []
    
    # Extract (sst, sd, cidr). SMF is f[2]=sst, f[3]=sd, f[5]=cidr
    # Extract and DEDUPLICATE (sst, sd, cidr) to prevent multi-PLMN self-collisions
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

    ctx = scallopy.ScallopContext()
    
    ctx.add_relation("amf_slice", (str, str, str, int, str)) # guami, pool_id, plmn, sst, sd
    ctx.add_relation("nssf_slice", (str, int, str))          # plmn, sst, sd
    ctx.add_relation("smf_pool", (str, str, str, int, str, str, str)) # smf_name, smf_set_id, plmn, sst, sd, dnn, cidr
    ctx.add_relation("gnb_slice", (str, int, str))           # plmn, sst, sd
    
    ctx.add_relation("python_invalid_sd", (str,))
    ctx.add_relation("python_invalid_cidr", (str,))
    ctx.add_relation("python_subnet_overlap", (str, str))
    
    ctx.add_facts("amf_slice", amf_facts)
    ctx.add_facts("nssf_slice", nssf_facts)
    ctx.add_facts("smf_pool", smf_facts)
    ctx.add_facts("gnb_slice", gnb_facts)
    ctx.add_facts("python_invalid_sd", list(set(invalid_sd_facts)))
    ctx.add_facts("python_invalid_cidr", list(set(invalid_cidr_facts)))
    ctx.add_facts("python_subnet_overlap", list(set(overlap_facts)))
    
    # --- FIX 2: Strict PLMN binding and corrected arity wildcards ---
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
    
    ctx.run()
    
    orphans = [
        list(ctx.relation("orphan_amf")), list(ctx.relation("orphan_nssf")),
        list(ctx.relation("orphan_smf")), list(ctx.relation("orphan_gnb"))
    ]
    missing_smf_pools = list(ctx.relation("missing_smf_pool"))
    invalid_ssts = list(ctx.relation("invalid_sst"))
    malformed_sds = list(ctx.relation("malformed_sd"))
    malformed_cidrs = list(ctx.relation("malformed_cidr"))
    collisions = list(ctx.relation("pool_collision"))
    amf_mismatch = list(ctx.relation("intra_amf_mismatch"))
    smf_mismatch = list(ctx.relation("intra_smf_mismatch"))
    print("\n=== Comprehensive End-to-End 5G Validation Report ===")
    
    has_errors = any(orphans) or any([invalid_ssts, malformed_sds, malformed_cidrs, collisions, missing_smf_pools, amf_mismatch, smf_mismatch])
    
    if not has_errors:
        print("✅ PASS: E2E network topology is logically sound and mathematically well-formed.")
    
    if orphans[3]: print(f"❌ FAIL (Topology): Slices broadcasted by gNodeB but missing in Core AMF: {orphans[3]}")
    if orphans[0]: print(f"❌ FAIL (Topology): Slices in AMF but missing in NSSF: {orphans[0]}")
    if orphans[1]: print(f"❌ FAIL (Topology): Slices in NSSF but missing in AMF: {orphans[1]}")
    if orphans[2]: print(f"❌ FAIL (Topology): Slices defined in SMF but missing in AMF: {orphans[2]}")
    if missing_smf_pools: print(f"❌ FAIL (Topology): Slices defined in AMF but missing a data pool in SMF: {missing_smf_pools}")
    if amf_mismatch: print(f"❌ FAIL (Intra-Set Synchronization): AMF Set inconsistency detected: {amf_mismatch}")
    if smf_mismatch: print(f"❌ FAIL (Intra-Set Synchronization): SMF Set inconsistency detected: {smf_mismatch}")
    
    if invalid_ssts: print(f"❌ FAIL (Format): Invalid SST found (must be 1-255): {invalid_ssts}")
    if malformed_sds: print(f"❌ FAIL (Format): Malformed SD found (must be 6 hex characters): {malformed_sds}")
    if malformed_cidrs: print(f"❌ FAIL (Format): Invalid IP Subnet format detected: {malformed_cidrs}")
    if collisions: print(f"❌ FAIL (Routing): Subnet OVERLAP detected between slices: {collisions}")

if __name__ == "__main__":
    run_e2e_validator()