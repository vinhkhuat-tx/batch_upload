#!/usr/bin/env python3
"""
Script to upload Dictionary into OpenMetadata.

Reads Excel files from S3, validates, then imports directly.
Usage: python upload_dictionary.py

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

# ──────────────────────────────────────────────
# CONFIG - edit here or use env vars
# ──────────────────────────────────────────────
MAIN_URL = os.getenv("MAIN_URL", "http://localhost:8585/api")
OM_TOKEN = os.getenv("OM_TOKEN", "")
S3_BUCKET = os.getenv("DICTIONARY_BUCKET", "pvc-temp")
S3_FILES = os.getenv("DICTIONARY_FILES", "openmetadata/valid_dictionary.xlsx")

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

HEADERS_PUT = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {OM_TOKEN}",
    "Accept": "application/json, text/plain, */*",
}



def get_entity_by_name(entity_name: str, fqn: str):
    """Query OpenMetadata entity by FQN. Return dict or None."""
    url = f"{MAIN_URL}/v1/{entity_name}/name/{fqn}".replace(" ", "%20")
    resp = requests.get(url, headers=HEADERS)
    return resp.json() if resp.status_code == 200 else None


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
    "Service Name",
    "Database Name",
    "Schema Name",
    "Table Name",
    "Column Name",
    "Table Description",
    "Column Description",
    "Tags",
    "Glossary Term",
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
    for col in ["Service Name", "Database Name", "Schema Name", "Table Name", "Column Name"]:
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
        fqn = f"{row['Service Name']}.{row['Database Name']}.{row['Schema Name']}.{row['Table Name']}"
        table = get_entity_by_name("tables", f"{fqn}?fields=columns,tags")
        if not table:
            errors.append({"file": row["file_name"], "msg": f"Table does not exist: {fqn}"})
            continue

        columns = [c["name"] for c in table["columns"]]
        if row["Column Name"] not in columns:
            errors.append({
                "file": row["file_name"],
                "msg": f"Column '{row['Column Name']}' does not exist in {fqn}",
            })

        # check tag
        if str(row.get("Tags", "nan")) != "nan":
            for tag in str(row["Tags"]).split(";"):
                tag_fqn = tag.strip().replace("_", ".")
                if not get_entity_by_name("tags", tag_fqn):
                    errors.append({"file": row["file_name"], "msg": f"Tag does not exist: {tag}"})

        # check glossary term
        if str(row.get("Glossary Term", "nan")) != "nan":
            for term in str(row["Glossary Term"]).split(";"):
                term_fqn = term.strip().replace("_", ".")
                if not get_entity_by_name("glossaryTerms", term_fqn):
                    errors.append({"file": row["file_name"], "msg": f"Glossary Term does not exist: {term}"})

    return errors


# ──────────────────────────────────────────────
# 3. IMPORT
# ──────────────────────────────────────────────
def _patch_tags_and_glossary(column_name, table_id, columns, list_tag, list_term):
    """Remove old tags/glossary on column then reassign new ones."""
    idx = None
    col = None
    for i, c in enumerate(columns):
        if c["name"] == column_name:
            idx, col = i, c
            break
    if idx is None:
        log.warning(f"  Column '{column_name}' not found in columns list, skipping tags/glossary")
        return

    old_tags = col.get("tags", [])

    # Remove all old tags
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

    # Collect new tags + glossary
    add_ops = []
    existing_classification = [t for t in old_tags if t["source"] == "Classification"]
    existing_glossary = [t for t in old_tags if t["source"] == "Glossary"]

    # Classification tags
    tags_to_add = list_tag if list_tag else [t["tagFQN"] for t in existing_classification]
    for tag in tags_to_add:
        fqn = tag.replace("_", ".") if tag not in [t["tagFQN"] for t in existing_classification] else tag
        add_ops.append({
            "op": "add",
            "path": f"/columns/{idx}/tags/{len(add_ops)}",
            "value": {
                "tagFQN": fqn,
                "source": "Classification",
                "labelType": "Automated",
                "state": "Confirmed",
            },
        })

    # Glossary terms
    terms_to_add = list_term if list_term else [t["tagFQN"] for t in existing_glossary]
    for term in terms_to_add:
        fqn = term.replace("_", ".") if term not in [t["tagFQN"] for t in existing_glossary] else term
        add_ops.append({
            "op": "add",
            "path": f"/columns/{idx}/tags/{len(add_ops)}",
            "value": {
                "tagFQN": fqn,
                "source": "Glossary",
                "labelType": "Automated",
                "state": "Confirmed",
            },
        })

    if add_ops:
        requests.patch(
            f"{MAIN_URL}/v1/tables/{table_id}",
            data=json.dumps(add_ops),
            headers=HEADERS_PATCH,
        )


def _update_description(table_id, description, path_prefix):
    """Patch description for a table."""
    body = json.dumps([{"op": "add", "path": path_prefix, "value": description}])
    print("  Updating description with body:", body)
    requests.patch(f"{MAIN_URL}/v1/tables/{table_id}", data=body, headers=HEADERS_PATCH)
    
    
def _update_description_column(table_fqn, description, column_name):
    """Patch description for a column."""
    body = json.dumps({"description": description})
    print("  Updating column description with body:", body)
    try:
        requests.put(f"{MAIN_URL}/v1/columns/name/{table_fqn}.{column_name}?entityType=table", data=body, headers=HEADERS_PUT)
    except Exception as e:
        log.error(f"  Error updating description for column {column_name}: {e}")

def import_dictionary(df: pd.DataFrame):
    """Import each row from the DataFrame into OpenMetadata."""
    total = len(df)
    success = 0
    fail = 0

    for i, row in df.iterrows():
        fqn = f"{row['Service Name']}.{row['Database Name']}.{row['Schema Name']}.{row['Table Name']}"
        col_name = row["Column Name"]
        log.info(f"[{i+1}/{total}] {fqn}.{col_name}")

        try:
            table = get_entity_by_name("tables", f"{fqn}?fields=columns,tags")
            if not table:
                log.error(f"  Table not found: {fqn}")
                fail += 1
                continue

            table_id = table["id"]
            columns = table["columns"]

            # Tags + Glossary
            tags_val = str(row.get("Tags", "nan"))
            term_val = str(row.get("Glossary Term", "nan"))
            if tags_val != "nan" or term_val != "nan":
                list_tag = [t.strip() for t in tags_val.split(";")] if tags_val != "nan" else []
                list_term = [t.strip() for t in term_val.split(";")] if term_val != "nan" else []
                _patch_tags_and_glossary(col_name, table_id, columns, list_tag, list_term)

            # Column description
            col_desc = str(row.get("Column Description", "nan"))
            if col_desc != "nan":
                _update_description_column(fqn, col_desc, col_name)

            # Table description
            tbl_desc = str(row.get("Table Description", "nan"))
            if tbl_desc != "nan":
                _update_description(table_id, tbl_desc, "/description")

            success += 1
        except Exception as e:
            log.error(f"  Error: {e}")
            fail += 1

    log.info(f"Completed: {success} succeeded, {fail} failed / {total} total")


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
def upload_dictionary(bucket: str = S3_BUCKET, file_keys: str = S3_FILES):
    """Main function: read S3 -> validate -> import."""
    log.info("=" * 60)
    log.info("START UPLOAD DICTIONARY")
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
    log.info("Importing dictionary...")
    import_dictionary(df)
    log.info("COMPLETED ✓")


if __name__ == "__main__":
    upload_dictionary()
