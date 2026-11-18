"""Verify the ledger chain and every hashed record (SCHEMA.md section 7).

python3 -I qa/runner/verify.py [--head]

Reports each record whose file no longer matches the sha256 the ledger holds, and each run's evidence
file that no longer matches its manifest (the manifest itself is checked against the run record).
Exit code 0 when the chain and all records verify, 1 otherwise.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from records import QA_DIR, WORKSPACE, ZERO, iter_ledger, sha256_bytes, sha256_file  # noqa: E402

RECORD_DIRS = ("cases", "runs", "issues", "devices")


def main(argv):
    chain_errors = []
    hashed = {}  # path -> (id, kind, sha, seq)
    rewrites = []
    prev_sha, prev_seq = ZERO, 0
    head = (0, ZERO)
    for path, raw in iter_ledger():
        try:
            e = json.loads(raw)
        except ValueError:
            chain_errors.append(f"{os.path.basename(path)}: unparsable line after seq {prev_seq}")
            prev_sha = sha256_bytes(raw)
            continue
        if e.get("seq") != prev_seq + 1:
            chain_errors.append(f"seq {e.get('seq')} follows {prev_seq}")
        if e.get("prev") != prev_sha:
            chain_errors.append(f"seq {e.get('seq')}: prev hash does not match line {prev_seq}")
        prev_sha, prev_seq = sha256_bytes(raw), e.get("seq", prev_seq + 1)
        head = (prev_seq, prev_sha)
        if e.get("src") == "record":
            r = e["ref"]
            if r["path"] in hashed and hashed[r["path"]][2] != r["file_sha256"]:
                rewrites.append(r["path"])
            hashed[r["path"]] = (r["id"], r["kind"], r["file_sha256"], e["seq"])

    failed, ok = [], 0
    for p, (rid, kind, sha, seq) in sorted(hashed.items()):
        ap = os.path.join(WORKSPACE, p)
        if not os.path.exists(ap):
            failed.append((rid, kind, p, "file missing"))
        elif sha256_file(ap) != sha:
            failed.append((rid, kind, p, f"sha256 differs from ledger seq {seq}"))
        else:
            ok += 1

    unhashed = []
    for d in RECORD_DIRS:
        for root, _, files in os.walk(os.path.join(QA_DIR, d)):
            for f in files:
                if f.endswith(".json"):
                    p = os.path.relpath(os.path.join(root, f), WORKSPACE)
                    if p not in hashed:
                        unhashed.append(p)

    ev_ok, ev_fail = 0, []
    for p, (rid, kind, sha, seq) in sorted(hashed.items()):
        if kind != "run" or not os.path.exists(os.path.join(WORKSPACE, p)):
            continue
        rec = json.load(open(os.path.join(WORKSPACE, p)))
        if not rec.get("manifest_sha256"):
            continue
        mp = os.path.join(WORKSPACE, rec["evidence_dir"], f"{rid}.manifest.json")
        if not os.path.exists(mp) or sha256_file(mp) != rec["manifest_sha256"]:
            ev_fail.append((rid, os.path.relpath(mp, WORKSPACE), "manifest missing or changed"))
            continue
        for ep, esha in json.load(open(mp))["files"].items():
            ap = os.path.join(WORKSPACE, ep)
            if not os.path.exists(ap):
                ev_fail.append((rid, ep, "file missing"))
            elif sha256_file(ap) != esha:
                ev_fail.append((rid, ep, "sha256 differs from the manifest"))
            else:
                ev_ok += 1

    print(f"ledger: {head[0]} lines, head sha256 {head[1]}")
    print(f"chain: {'OK' if not chain_errors else 'BROKEN'}")
    for c in chain_errors:
        print(f"  CHAIN {c}")
    print(f"records: {ok} verify, {len(failed)} fail, {len(unhashed)} not in ledger")
    for rid, kind, p, why in failed:
        print(f"  FAIL {kind} {rid}  {p}  ({why})")
    for p in rewrites:
        print(f"  REHASHED {p}  (hashed more than once with different content)")
    print(f"evidence: {ev_ok} files verify against their run manifests, {len(ev_fail)} fail")
    for rid, ep, why in ev_fail:
        print(f"  FAIL evidence {rid}  {ep}  ({why})")
    if "--unhashed" in argv:
        for p in sorted(unhashed):
            print(f"  UNHASHED {p}")
    return 1 if (failed or chain_errors or rewrites or ev_fail) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
