#!/usr/bin/env python3
"""Diagnose Buffer/X connectivity and engagement capabilities without posting."""
import json, os, sys
from datetime import datetime, timezone
from buffer_client import _organizations, _channels, resolve_x_channel_id, graphql, BufferError

def main():
    report={"checked_at":datetime.now(timezone.utc).isoformat(),"ok":False,"x_channel":None,
            "engagement_api":{"schema_detected":False,"comment_type":False,"mention_type":False},
            "notes":[]}
    try:
        orgs=_organizations()
        report["organizations"]=len(orgs)
        cid=resolve_x_channel_id()
        report["x_channel"]=cid
        # Introspection only: never fetches private engagements and never posts.
        q='''query EngagementSchemaProbe {
          engagementType: __type(name: "EngagementType") { enumValues { name } }
          queryType: __type(name: "Query") { fields { name } }
        }'''
        try:\n            p=graphql(q).get("data",{})
            vals=[v.get("name") for v in ((p.get("engagementType") or {}).get("enumValues") or [])]
            fields=[v.get("name") for v in ((p.get("queryType") or {}).get("fields") or [])]
            report["engagement_api"]["schema_detected"]=bool(p.get("engagementType"))
            report["engagement_api"]["comment_type"]="comment" in vals
            report["engagement_api"]["mention_type"]="mention" in vals
            report["engagement_api"]["query_fields_matching"]=[x for x in fields if any(k in x.lower() for k in ("engag","comment","mention"))]\n        except Exception as probe_exc:\n            report["engagement_api"]["probe_warning"]=f"{type(probe_exc).__name__}: {probe_exc}"
        report["ok"]=True
        report["notes"].append("Publishing connection is healthy if this check succeeds.")
        if not report["engagement_api"]["query_fields_matching"]:
            report["notes"].append("No public engagement query detected; use Buffer Community as source of truth.")
    except Exception as e:
        report["error"]=f"{type(e).__name__}: {e}"
    print(json.dumps(report,ensure_ascii=False,indent=2))
    os.makedirs("diagnostics",exist_ok=True)
    with open("diagnostics/buffer_engagement_health.json","w",encoding="utf-8") as f:
        json.dump(report,f,ensure_ascii=False,indent=2)
    return 0 if report["ok"] else 1

if __name__=="__main__":
    raise SystemExit(main())
