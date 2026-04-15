#!/usr/bin/env python3
"""Rebuild index/contacts.csv from contacts/*.yaml files.

Run after any contact file change. Idempotent — safe to run repeatedly.
"""

import csv
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTACTS_DIR = REPO_ROOT / "contacts"
INDEX_DIR = REPO_ROOT / "index"
INDEX_CSV = INDEX_DIR / "contacts.csv"

INDEX_COLUMNS = [
    "id",
    "slug",
    "first_name",
    "last_name",
    "primary_email",
    "primary_phone",
    "company",
    "warmth",
    "tags",
    "next_contact",
    "last_contacted",
    "birthday",
]


def parse_yaml_simple(filepath: Path) -> dict:
    """Minimal YAML parser for our known contact schema.

    We avoid importing PyYAML to keep dependencies at zero.
    Handles our flat schema with simple list items.
    """
    content = filepath.read_text(encoding="utf-8")
    data = {}
    current_list_key = None
    current_list = []
    current_item = {}

    for line in content.split("\n"):
        stripped = line.strip()

        # Skip empty lines and comments
        if not stripped or stripped.startswith("#"):
            if current_list_key and current_list is not None:
                # Flush current item if any
                if current_item:
                    current_list.append(current_item)
                    current_item = {}
            continue

        # List item
        if stripped.startswith("- "):
            if current_list_key:
                if current_item:
                    current_list.append(current_item)
                    current_item = {}
                # Parse "- key: value"
                item_content = stripped[2:]
                if ":" in item_content:
                    k, v = item_content.split(":", 1)
                    current_item[k.strip()] = _clean_value(v.strip())
                else:
                    current_list.append(_clean_value(item_content))
                    current_item = {}
            continue

        # Indented continuation of list item (e.g., "    value: foo")
        if line.startswith("    ") and current_list_key and current_item is not None:
            if ":" in stripped:
                k, v = stripped.split(":", 1)
                current_item[k.strip()] = _clean_value(v.strip())
            continue

        # Top-level key: value
        if ":" in stripped and not stripped.startswith("-"):
            # Flush any pending list
            if current_list_key:
                if current_item:
                    current_list.append(current_item)
                    current_item = {}
                data[current_list_key] = current_list
                current_list_key = None
                current_list = []
                current_item = {}

            key, value = stripped.split(":", 1)
            key = key.strip()
            value = value.strip()

            if value == "[]":
                data[key] = []
            elif value.startswith("[") and value.endswith("]"):
                # Inline YAML list: [a, b, c]
                inner = value[1:-1].strip()
                if inner:
                    data[key] = [_clean_value(v.strip()) for v in inner.split(",")]
                else:
                    data[key] = []
            elif value == "" or value is None:
                # Could be start of a list
                current_list_key = key
                current_list = []
                current_item = {}
            else:
                data[key] = _clean_value(value)

    # Flush final list
    if current_list_key:
        if current_item:
            current_list.append(current_item)
        data[current_list_key] = current_list

    return data


def _clean_value(value: str) -> str:
    """Remove surrounding quotes from a YAML value."""
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def extract_index_row(data: dict) -> dict:
    """Extract index CSV fields from parsed contact data."""
    # Primary email: first in list
    emails = data.get("emails", [])
    primary_email = ""
    if emails and isinstance(emails, list) and len(emails) > 0:
        if isinstance(emails[0], dict):
            primary_email = emails[0].get("value", "")
        elif isinstance(emails[0], str):
            primary_email = emails[0]

    # Primary phone: first in list
    phones = data.get("phones", [])
    primary_phone = ""
    if phones and isinstance(phones, list) and len(phones) > 0:
        if isinstance(phones[0], dict):
            primary_phone = phones[0].get("value", "")
        elif isinstance(phones[0], str):
            primary_phone = phones[0]

    # Tags: convert list to pipe-delimited
    tags = data.get("tags", [])
    if isinstance(tags, list):
        tags_str = "|".join(str(t) for t in tags if t)
    elif isinstance(tags, str):
        tags_str = tags.replace(",", "|").strip("[]")
    else:
        tags_str = ""

    return {
        "id": data.get("id", ""),
        "slug": data.get("slug", ""),
        "first_name": data.get("first_name", ""),
        "last_name": data.get("last_name", ""),
        "primary_email": primary_email,
        "primary_phone": primary_phone,
        "company": data.get("company", ""),
        "warmth": data.get("warmth", "0"),
        "tags": tags_str,
        "next_contact": data.get("next_contact", ""),
        "last_contacted": data.get("last_contacted", ""),
        "birthday": data.get("birthday", ""),
    }


def main():
    if not CONTACTS_DIR.exists():
        print(f"Error: contacts directory not found: {CONTACTS_DIR}")
        sys.exit(1)

    INDEX_DIR.mkdir(parents=True, exist_ok=True)

    yaml_files = sorted(CONTACTS_DIR.glob("*.yaml"))
    if not yaml_files:
        print("No contact files found.")
        sys.exit(0)

    rows = []
    errors = []
    for filepath in yaml_files:
        try:
            data = parse_yaml_simple(filepath)
            row = extract_index_row(data)
            rows.append(row)
        except Exception as e:
            errors.append(f"  {filepath.name}: {e}")

    # Sort by id (numeric)
    rows.sort(key=lambda r: int(r.get("id", 0)))

    with open(INDEX_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=INDEX_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Index rebuilt: {len(rows)} contacts → {INDEX_CSV}")
    if errors:
        print(f"Errors ({len(errors)}):")
        for e in errors:
            print(e)


if __name__ == "__main__":
    main()
