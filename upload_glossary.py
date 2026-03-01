#!/usr/bin/env python3
"""
Script to upload Glossary Terms into OpenMetadata.

Reads Excel files from S3, validates, then imports directly.
Usage: python upload_glossary.py

Configure via environment variables or edit the values below.
"""

import json
import logging
import os
import sys
from io import BytesIO

import boto3
import pandas as pd
import requests
import validators

# ──────────────────────────────────────────────
# CONFIG - edit here or use env vars
# ──────────────────────────────────────────────
MAIN_URL = os.getenv("MAIN_URL", "http://localhost:8585/api")
OM_TOKEN = os.getenv("OM_TOKEN", "")
S3_BUCKET = os.getenv("GLOSSARY_BUCKET", "pvc-temp")
S3_FILES = os.getenv("GLOSSARY_FILES", "openmetadata/valid_grossary.xlsx")  

# ──────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# HELPER
# ──────────────────────────────────────────────
s3 = boto3.client("s3")

HEADERS = {"Authorization": f"Bearer {OM_TOKEN}"}
HEADERS_PATCH = {
    "Content-Type": "application/json-patch+json",
    "Authorization": f"Bearer {OM_TOKEN}",
    "Accept": "application/json, text/plain, */*",
}


def get_entity_by_name(entity_name: str, fqn: str):
    """Query OpenMetadata entity by FQN. Return dict or None."""
    url = f"{MAIN_URL}/v1/{entity_name}/name/{fqn}".replace(" ", "%20")
    resp = requests.get(url, headers=HEADERS)
    return resp.json() if resp.status_code == 200 else None


def _is_nan(val) -> bool:
    """Check for NaN/empty value safely."""
    return val is None or str(val).strip() in ("nan", "")


# ──────────────────────────────────────────────
# 1. READ FILES FROM S3
# ──────────────────────────────────────────────
def read_files_from_s3(bucket: str, file_keys: str) -> pd.DataFrame:
    """Read one or more Excel files from S3 and return a combined DataFrame."""
    frames = []
    for key in file_keys.split(","):
        key = key.strip()
        log.info(f"Reading s3://{bucket}/{key}")
        obj = s3.get_object(Bucket=bucket, Key=key)
        df = pd.read_excel(BytesIO(obj["Body"].read()))
        df["file_name"] = key
        frames.append(df)
    combined = pd.concat(frames, ignore_index=True)
    log.info(f"Total: {len(combined)} rows from {len(frames)} file(s)")
    return combined


# ──────────────────────────────────────────────
# 2. VALIDATE
# ──────────────────────────────────────────────
REQUIRED_COLUMNS = [
    "Glossary",
    "Parent",
    "Term Name",
    "Display Name",
    "Description",
    "Synonyms",
    "Related Terms",
    "Owner",
    "Reviewers",
    "References",
    "Tags",
    "Service Name",
    "Database Name",
    "Schema Name",
    "Table Name",
    "Column Name",
]


def validate(df: pd.DataFrame) -> list[dict]:
    """Validate format and data. Returns a list of errors (empty = OK)."""
    errors: list[dict] = []

    # --- Check required columns ---
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        errors.append({"msg": f"File missing columns: {missing}"})
        return errors

    # --- Check for empty values in required columns ---
    for col in ["Glossary", "Term Name", "Display Name", "Description"]:
        if df[col].isna().any():
            first = df[df[col].isna()].iloc[0]
            errors.append({
                "file": first["file_name"],
                "msg": f"Column '{col}' has empty rows",
            })

    if errors:
        return errors

    # --- Check each row ---
    for _, row in df.iterrows():
        fname = row["file_name"]

        # Glossary exists?
        glossary_name = row["Glossary"]
        glossary = get_entity_by_name("glossaries", glossary_name)
        if not glossary:
            errors.append({"file": fname, "msg": f"Glossary does not exist: {glossary_name}"})
            continue

        # Parent term exists?
        if not _is_nan(row["Parent"]):
            parent_fqn = str(row["Parent"]).replace("_", ".")
            if not get_entity_by_name("glossaryTerms", parent_fqn):
                errors.append({"file": fname, "msg": f"Parent term does not exist: {row['Parent']}"})

        # DB reference (if all 5 fields are provided)
        db_infos = [str(row[c]) for c in ["Service Name", "Database Name", "Schema Name", "Table Name", "Column Name"]]
        if "nan" not in db_infos:
            fqn = f"{row['Service Name']}.{row['Database Name']}.{row['Schema Name']}.{row['Table Name']}"
            table = get_entity_by_name("tables", f"{fqn}?fields=columns")
            if not table:
                errors.append({"file": fname, "msg": f"Table does not exist: {fqn}"})
            else:
                columns = [c["name"] for c in table["columns"]]
                if row["Column Name"] not in columns:
                    errors.append({"file": fname, "msg": f"Column '{row['Column Name']}' does not exist in {fqn}"})
        elif len(set(db_infos)) > 1 and len(set(db_infos)) < 5:
            errors.append({"file": fname, "msg": f"Incomplete DB info: {'.'.join(db_infos)}"})

        # Owner exists?
        if not _is_nan(row["Owner"]):
            if not get_entity_by_name("users", row["Owner"]):
                errors.append({"file": fname, "msg": f"Owner does not exist: {row['Owner']}"})

        # Reviewer exists?
        if not _is_nan(row["Reviewers"]):
            if not get_entity_by_name("users", row["Reviewers"]):
                errors.append({"file": fname, "msg": f"Reviewer does not exist: {row['Reviewers']}"})

        # Related terms exist?
        if not _is_nan(row["Related Terms"]):
            for term in str(row["Related Terms"]).split(";"):
                term_fqn = term.strip().replace("_", ".")
                if not get_entity_by_name("glossaryTerms", term_fqn):
                    errors.append({"file": fname, "msg": f"Related term does not exist: {term}"})

        # References URL valid?
        if not _is_nan(row["References"]):
            for url in str(row["References"]).split(";"):
                if not validators.url(url.strip()):
                    errors.append({"file": fname, "msg": f"Invalid URL: {url}"})

        # Tags exist?
        if not _is_nan(row["Tags"]):
            for tag in str(row["Tags"]).split(";"):
                tag_fqn = tag.strip().replace("_", ".")
                if not get_entity_by_name("tags", tag_fqn):
                    errors.append({"file": fname, "msg": f"Tag does not exist: {tag}"})

    return errors


# ──────────────────────────────────────────────
# 3. IMPORT
# ──────────────────────────────────────────────
def _format_owner(owner):
    if _is_nan(owner):
        return None
    info = get_entity_by_name("users", owner)
    return {"id": info["id"], "type": "user"} if info else None


def _format_reviewer(reviewer):
    if _is_nan(reviewer):
        return None
    info = get_entity_by_name("users", reviewer)
    return [{"id": info["id"], "type": "user"}] if info else None


def _format_related_terms(val):
    if _is_nan(val):
        return None
    return [t.strip().replace("_", ".") for t in str(val).split(";")]


def _format_parent(val):
    if _is_nan(val):
        return None
    return str(val).strip().replace("_", ".")


def _format_references(val):
    if _is_nan(val):
        return None
    return [{"name": "Reference", "endpoint": u.strip()} for u in str(val).split(";")]


def _format_tags(val):
    if _is_nan(val):
        return None
    return [
        {
            "tagFQN": t.strip().replace("_", "."),
            "source": "Classification",
            "labelType": "Automated",
            "state": "Confirmed",
        }
        for t in str(val).split(";")
    ]


def _patch_tags_on_glossary_term(term_id: str, tags: list):
    """Remove old tags then assign new tags to a glossary term."""
    term_resp = requests.get(
        f"{MAIN_URL}/v1/glossaryTerms/{term_id}?fields=tags", headers=HEADERS
    )
    existing_tags = term_resp.json().get("tags", [])

    # Remove old tags
    if existing_tags:
        delete_ops = [
            {"op": "remove", "path": f"/tags/{i}"}
            for i in range(len(existing_tags) - 1, -1, -1)
        ]
        requests.patch(
            f"{MAIN_URL}/v1/glossaryTerms/{term_id}",
            data=json.dumps(delete_ops),
            headers=HEADERS_PATCH,
        )

    # Add new tags
    add_ops = [
        {"op": "add", "path": f"/tags/{i}", "value": tags[i]}
        for i in range(len(tags))
    ]
    requests.patch(
        f"{MAIN_URL}/v1/glossaryTerms/{term_id}",
        data=json.dumps(add_ops),
        headers=HEADERS_PATCH,
    )


def _patch_glossary_term_to_column(service_name, db_name, schema_name, table_name, column_name, term_fqn):
    """Attach a glossary term to a table column."""
    fqn = f"{service_name}.{db_name}.{schema_name}.{table_name}"
    table = get_entity_by_name("tables", f"{fqn}?fields=columns,tags")
    if not table:
        log.warning(f"  Table not found: {fqn}")
        return

    table_id = table["id"]
    columns = table["columns"]

    idx = None
    col = None
    for i, c in enumerate(columns):
        if c["name"] == column_name:
            idx, col = i, c
            break
    if idx is None:
        log.warning(f"  Column '{column_name}' not found in {fqn}")
        return

    old_tags = col.get("tags", [])
    classification_tags = [t for t in old_tags if t["source"] == "Classification"]

    # Remove old tags
    if old_tags:
        delete_ops = [
            {"op": "remove", "path": f"/columns/{idx}/tags/{i}"}
            for i in range(len(old_tags) - 1, -1, -1)
        ]
        requests.patch(
            f"{MAIN_URL}/v1/tables/{table_id}",
            data=json.dumps(delete_ops),
            headers=HEADERS_PATCH,
        )

    # Re-attach classification tags + new glossary term
    add_ops = []
    for tag in classification_tags:
        add_ops.append({
            "op": "add",
            "path": f"/columns/{idx}/tags/{len(add_ops)}",
            "value": {
                "tagFQN": tag["tagFQN"],
                "source": "Classification",
                "labelType": tag["labelType"],
                "state": tag["state"],
            },
        })
    add_ops.append({
        "op": "add",
        "path": f"/columns/{idx}/tags/{len(add_ops)}",
        "value": {
            "tagFQN": term_fqn,
            "source": "Glossary",
            "labelType": "Automated",
            "state": "Confirmed",
        },
    })

    requests.patch(
        f"{MAIN_URL}/v1/tables/{table_id}",
        data=json.dumps(add_ops),
        headers=HEADERS_PATCH,
    )


def import_glossary(df: pd.DataFrame):
    """Import each row from the DataFrame into OpenMetadata."""
    total = len(df)
    success = 0
    fail = 0

    for i, row in df.iterrows():
        term_name = row["Term Name"]
        glossary_name = str(row["Glossary"]).replace("_", ".")
        log.info(f"[{i+1}/{total}] {glossary_name} / {term_name}")

        try:
            # Create / update glossary term
            term_request = {
                "name": term_name,
                "glossary": glossary_name,
                "parent": _format_parent(row["Parent"]),
                "displayName": "" if _is_nan(row["Display Name"]) else str(row["Display Name"]),
                "description": row["Description"],
                "synonyms": [] if _is_nan(row["Synonyms"]) else str(row["Synonyms"]).split(";"),
                "owners": _format_owner(row["Owner"]),
                "reviewers": _format_reviewer(row["Reviewers"]),
                "relatedTerms": _format_related_terms(row["Related Terms"]),
                "references": _format_references(row["References"]),
            }

            resp = requests.put(
                f"{MAIN_URL}/v1/glossaryTerms",
                json=term_request,
                headers=HEADERS,
            )

            if resp.status_code not in (200, 201):
                log.error(f"  API error {resp.status_code}: {resp.text}")
                fail += 1
                continue

            data = resp.json()
            term_id = data["id"]
            term_fqn = data["fullyQualifiedName"]

            # Attach tags to glossary term
            tags = _format_tags(row["Tags"])
            if tags:
                _patch_tags_on_glossary_term(term_id, tags)

            # Attach glossary term to column (if all DB info is provided)
            db_infos = [str(row[c]) for c in ["Service Name", "Database Name", "Schema Name", "Table Name", "Column Name"]]
            if "nan" not in db_infos:
                _patch_glossary_term_to_column(
                    row["Service Name"],
                    row["Database Name"],
                    row["Schema Name"],
                    row["Table Name"],
                    row["Column Name"],
                    term_fqn,
                )

            success += 1
        except Exception as e:
            log.error(f"  Error: {e}")
            fail += 1

    log.info(f"Completed: {success} succeeded, {fail} failed / {total} total")


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
def upload_glossary(bucket: str = S3_BUCKET, file_keys: str = S3_FILES):
    """Main function: read S3 -> validate -> import."""
    log.info("=" * 60)
    log.info("START UPLOAD GLOSSARY")
    log.info(f"  Bucket : {bucket}")
    log.info(f"  Files  : {file_keys}")
    log.info(f"  API    : {MAIN_URL}")
    log.info("=" * 60)

    # 1. Read files
    df = read_files_from_s3(bucket, file_keys)

    # 2. Validate
    log.info("Validating data...")
    errors = validate(df)
    if errors:
        log.error(f"Validation failed with {len(errors)} error(s):")
        for e in errors:
            log.error(f"  - {e}")
        sys.exit(1)
    log.info("Validation OK ✓")

    # 3. Import
    log.info("Importing glossary terms...")
    import_glossary(df)
    log.info("COMPLETED ✓")


if __name__ == "__main__":
    upload_glossary()
