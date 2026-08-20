#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
from pathlib import Path

# Allow scripts/ entry points to import modules from the repository root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from buffer_client import graphql, resolve_x_channel_id


def main() -> None:
    # Validate API key and account access without publishing anything.
    payload = graphql(
        """
        query GetOrganizations {
          account {
            organizations { id name }
          }
        }
        """
    )
    orgs = payload.get("data", {}).get("account", {}).get("organizations", []) or []
    if not orgs:
        raise RuntimeError("Buffer API key is valid, but no organization was found.")

    channel_id = resolve_x_channel_id()
    masked = channel_id[:4] + "…" + channel_id[-4:] if len(channel_id) > 10 else "(detected)"
    print(f"OK: Buffer API authenticated; organizations={len(orgs)}; X channel={masked}")
    print("No post was created by this test.")
    print("Connection test completed successfully.")


if __name__ == "__main__":
    main()
