#!/usr/bin/env python3
"""Apply an S3 lifecycle rule expiring the temporary ``uploads/`` prefix (#126).

The direct-to-S3 photo uploader (``photo_upload.py``, DECISIONS-007) PUTs each
phone photo straight into ``sawimages/uploads/{flow}/{session}/page_N.jpg`` using
presigned URLs. Those objects are a *transfer buffer*: once a book is registered
the prefix is cleaned up. But an abandoned or closed-tab upload leaves orphaned
objects behind, and **both data-cleanup tools deliberately exclude ``uploads/``**,
so nothing reclaims them. This script installs a belt-and-braces S3 **lifecycle
rule** that expires any object under ``uploads/`` after ``EXPIRY_DAYS`` (7) days.

This is a one-shot infrastructure change, not part of the running app. It runs
OUTSIDE Streamlit, so ``st.secrets`` is unavailable: it loads
``.streamlit/secrets.toml`` directly and builds its own boto3 S3 client mirroring
``photo_upload._s3_client`` (regional SigV4 endpoint for ``eu-north-1``).

SAFETY: dry-run by default — it only PRINTS the rule it WOULD apply. Real
application requires the ``--execute`` flag. It reads the bucket's existing
lifecycle configuration first and MERGES this rule in (replacing only a prior
rule with the same id), so it will not clobber unrelated lifecycle rules.

  *** DO NOT run this from an isolated worktree / CI — it has no real creds and   ***
  *** must be applied by Chris against the live ``sawimages`` bucket.             ***

Usage
-----
    # Dry-run (default — prints the rule, changes nothing):
    python scripts/set_uploads_lifecycle.py

    # Apply for real against the live bucket (Chris, from the project root):
    python scripts/set_uploads_lifecycle.py --execute

    # Inspect what is currently configured, without changing anything:
    python scripts/set_uploads_lifecycle.py --show

Equivalent AWS CLI (apply the same rule by hand)
------------------------------------------------
    aws s3api put-bucket-lifecycle-configuration \
        --bucket sawimages \
        --region eu-north-1 \
        --lifecycle-configuration '{
          "Rules": [
            {
              "ID": "expire-uploads-prefix",
              "Status": "Enabled",
              "Filter": {"Prefix": "uploads/"},
              "Expiration": {"Days": 7}
            }
          ]
        }'

  NOTE: ``put-bucket-lifecycle-configuration`` REPLACES the whole lifecycle
  config, so include any existing rules in the JSON. This script merges for you.

Equivalent AWS Console steps
----------------------------
    S3 -> Buckets -> ``sawimages`` -> Management -> Lifecycle rules
      -> Create lifecycle rule
      -> Rule name: ``expire-uploads-prefix``
      -> Limit scope with prefix ``uploads/``
      -> Lifecycle rule actions: "Expire current versions of objects"
      -> Days after object creation: ``7``
      -> Create rule
"""

from __future__ import annotations

import argparse
import json
import os
import sys

#: Default path to the Streamlit secrets file (mirrors scripts/data_cleanup.py).
DEFAULT_SECRETS = ".streamlit/secrets.toml"

#: Bucket and prefix must match photo_upload.S3_BUCKET / UPLOAD_PREFIX_ROOT.
S3_BUCKET = "sawimages"
UPLOAD_PREFIX = "uploads/"

#: Days after creation before an ``uploads/`` object is expired (deleted).
EXPIRY_DAYS = 7

#: Stable rule id so re-running this script updates (not duplicates) the rule,
#: and so it merges cleanly alongside any unrelated lifecycle rules.
RULE_ID = "expire-uploads-prefix"


def load_secrets(path: str) -> dict:
    """Load ``.streamlit/secrets.toml`` directly (no Streamlit dependency)."""
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"secrets file not found: {path} (run from the project root, or pass "
            "--secrets). This tool must run where the real secrets exist."
        )
    try:
        import tomllib  # type: ignore

        with open(path, "rb") as f:
            return tomllib.load(f)
    except ModuleNotFoundError:
        pass
    try:
        import tomli  # type: ignore

        with open(path, "rb") as f:
            return tomli.load(f)
    except ModuleNotFoundError:
        pass
    import toml  # type: ignore

    with open(path, "r", encoding="utf-8") as f:
        return toml.load(f)


def build_s3_client(secrets: dict):
    """Build a boto3 S3 client from the secrets, mirroring ``photo_upload``.

    The bucket lives in ``eu-north-1`` which only supports SigV4 and the regional
    endpoint, so pin ``s3v4`` and an explicit regional ``endpoint_url``.
    """
    import boto3
    from botocore.config import Config

    for required in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        if required not in secrets:
            raise KeyError(f"secrets is missing required '{required}'")

    region = secrets.get("AWS_DEFAULT_REGION")
    endpoint_url = f"https://s3.{region}.amazonaws.com" if region else None
    return boto3.client(
        "s3",
        region_name=region,
        endpoint_url=endpoint_url,
        aws_access_key_id=secrets["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=secrets["AWS_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    )


def uploads_rule() -> dict:
    """Return the lifecycle rule expiring ``uploads/`` objects after N days."""
    return {
        "ID": RULE_ID,
        "Status": "Enabled",
        "Filter": {"Prefix": UPLOAD_PREFIX},
        "Expiration": {"Days": EXPIRY_DAYS},
    }


def get_existing_rules(client) -> list:
    """Return the bucket's current lifecycle rules ([] if none configured)."""
    from botocore.exceptions import ClientError

    try:
        resp = client.get_bucket_lifecycle_configuration(Bucket=S3_BUCKET)
        return resp.get("Rules", [])
    except ClientError as exc:
        # A bucket with no lifecycle config returns this specific error code.
        if exc.response.get("Error", {}).get("Code") == "NoSuchLifecycleConfiguration":
            return []
        raise


def merge_rules(existing: list) -> list:
    """Merge our rule into ``existing``, replacing any rule with the same id."""
    merged = [r for r in existing if r.get("ID") != RULE_ID]
    merged.append(uploads_rule())
    return merged


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Apply (or preview) an S3 lifecycle rule expiring the uploads/ prefix "
            f"after {EXPIRY_DAYS} days on the {S3_BUCKET} bucket (#126)."
        )
    )
    parser.add_argument(
        "--secrets",
        default=DEFAULT_SECRETS,
        help=f"Path to secrets.toml (default: {DEFAULT_SECRETS}).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually apply the rule. Without this flag the script is a dry-run.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Print the bucket's current lifecycle configuration and exit.",
    )
    args = parser.parse_args(argv)

    rule = uploads_rule()
    print("Target bucket: ", S3_BUCKET)
    print("Rule to apply:")
    print(json.dumps(rule, indent=2))

    if not args.execute and not args.show:
        print(
            "\nDRY-RUN: nothing changed. Re-run with --execute (from the project "
            "root, where the real secrets live) to apply this rule."
        )
        return 0

    secrets = load_secrets(args.secrets)
    client = build_s3_client(secrets)
    existing = get_existing_rules(client)

    if args.show:
        print("\nCurrent lifecycle rules on the bucket:")
        print(json.dumps(existing, indent=2, default=str))
        return 0

    merged = merge_rules(existing)
    print(f"\nApplying merged lifecycle configuration ({len(merged)} rule(s))...")
    client.put_bucket_lifecycle_configuration(
        Bucket=S3_BUCKET,
        LifecycleConfiguration={"Rules": merged},
    )
    print("Done. The uploads/ prefix will now expire objects after "
          f"{EXPIRY_DAYS} days.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
