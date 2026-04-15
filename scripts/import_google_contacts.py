#!/usr/bin/env python3
"""One-time import: Google Contacts CSV → individual YAML contact files."""

from __future__ import annotations

import csv
import os
import re
import sys
from pathlib import Path

# --- Config ---
CONTACTS_DIR = Path(__file__).resolve().parent.parent / "contacts"
OBSOLETE_EMAIL_LABELS = {"Obsolete", "* Obsolete"}
SKIP_NOTE_PATTERNS = [
    re.compile(r"^[A-Z\-\d]{1,10}$"),        # Single chars, short codes
    re.compile(r"X-MS-", re.IGNORECASE),      # Outlook metadata
]

# --- Helpers ---

def slugify(text: str) -> str:
    """Convert text to a URL/filename-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def clean_triple_colon(value: str) -> str:
    """Take first value from ':::'-delimited fields."""
    if ":::" in value:
        return value.split(":::")[0].strip()
    return value.strip()


def normalize_email_label(label: str) -> str:
    """Map Google Contacts email labels to clean labels."""
    label = label.replace("*", "").strip().lower()
    mapping = {
        "": "primary",
        "home": "home",
        "work": "work",
        "other": "other",
        "internet": "primary",
        "personal": "personal",
        "facebook": "facebook",
        "foxy": "other",
    }
    return mapping.get(label, "other")


def normalize_phone_label(label: str) -> str:
    """Map Google Contacts phone labels to clean labels."""
    label = label.strip().lower()
    mapping = {
        "mobile": "mobile",
        "main": "main",
        "home": "home",
        "work": "work",
        "phone": "mobile",
        "other": "other",
        "old": "other",
        "home fax": "fax",
        "work fax": "fax",
    }
    return mapping.get(label, "other")


def is_garbage_note(note: str) -> bool:
    """Detect notes that are junk data (single chars, Outlook metadata, etc.)."""
    for pattern in SKIP_NOTE_PATTERNS:
        if pattern.search(note):
            return True
    return False


def yaml_str(value: str) -> str:
    """Format a string for safe YAML output."""
    if not value:
        return '""'
    if any(c in value for c in ":{}\n[]&*?|>!%@`#,") or value.startswith(("-", " ")):
        return f'"{value}"'
    if value.lower() in ("true", "false", "yes", "no", "null", "on", "off"):
        return f'"{value}"'
    return value


def write_contact_yaml(filepath: Path, contact: dict):
    """Write a contact dict to a YAML file (hand-formatted for clarity)."""
    lines = []
    lines.append(f"id: {contact['id']}")
    lines.append(f"first_name: {yaml_str(contact['first_name'])}")
    lines.append(f"last_name: {yaml_str(contact['last_name'])}")
    lines.append(f"slug: {contact['slug']}")
    lines.append("")

    # Emails
    if contact["emails"]:
        lines.append("emails:")
        for e in contact["emails"]:
            lines.append(f"  - label: {e['label']}")
            lines.append(f"    value: {yaml_str(e['value'])}")
    else:
        lines.append("emails: []")
    lines.append("")

    # Phones
    if contact["phones"]:
        lines.append("phones:")
        for p in contact["phones"]:
            lines.append(f"  - label: {p['label']}")
            lines.append(f"    value: {yaml_str(p['value'])}")
    else:
        lines.append("phones: []")
    lines.append("")

    lines.append(f"company: {yaml_str(contact['company'])}")
    lines.append(f"job_title: {yaml_str(contact['job_title'])}")
    lines.append(f"industry: {yaml_str(contact['industry'])}")
    lines.append(f"geography: {yaml_str(contact['geography'])}")
    lines.append(f"birthday: {yaml_str(contact['birthday'])}")
    lines.append("")
    lines.append(f"warmth: {contact['warmth']}")
    lines.append(f"tags: {contact['tags']}")
    lines.append("")
    lines.append(f"notes: {yaml_str(contact['notes'])}")
    lines.append("")
    lines.append(f"next_contact: {yaml_str(contact['next_contact'])}")
    lines.append(f"last_contacted: {yaml_str(contact['last_contacted'])}")
    lines.append("")
    lines.append("history: []")
    lines.append("")

    filepath.write_text("\n".join(lines), encoding="utf-8")


def parse_row(row: dict) -> dict | None:
    """Parse a Google Contacts CSV row into a contact dict. Returns None to skip."""
    first_name = row.get("First Name", "").strip()
    last_name = row.get("Last Name", "").strip()
    org = row.get("Organization Name", "").strip()

    # Skip contacts with no identifying info
    if not first_name and not last_name and not org:
        return None

    # Build name for slug
    if first_name or last_name:
        display_name = f"{first_name} {last_name}".strip()
    else:
        display_name = org

    slug = slugify(display_name)
    if not slug:
        return None

    # Emails (up to 4, skip Obsolete)
    emails = []
    for i in range(1, 5):
        label = row.get(f"E-mail {i} - Label", "").strip()
        value = row.get(f"E-mail {i} - Value", "").strip()
        if value and label not in OBSOLETE_EMAIL_LABELS:
            emails.append({
                "label": normalize_email_label(label),
                "value": value,
            })

    # Phones (up to 3)
    phones = []
    for i in range(1, 4):
        label = row.get(f"Phone {i} - Label", "").strip()
        value = row.get(f"Phone {i} - Value", "").strip()
        if value:
            phones.append({
                "label": normalize_phone_label(label),
                "value": value,
            })

    # Geography from address
    city = clean_triple_colon(row.get("Address 1 - City", ""))
    region = clean_triple_colon(row.get("Address 1 - Region", ""))
    if city and region:
        geography = f"{city}, {region}"
    elif city:
        geography = city
    elif region:
        geography = region
    else:
        geography = ""

    # Notes (skip garbage)
    notes = row.get("Notes", "").strip()
    if notes and is_garbage_note(notes):
        notes = ""

    # Birthday
    birthday = row.get("Birthday", "").strip()

    # Job title
    job_title = row.get("Organization Title", "").strip()

    return {
        "first_name": first_name,
        "last_name": last_name,
        "slug": slug,
        "emails": emails,
        "phones": phones,
        "company": org,
        "job_title": job_title,
        "industry": "",
        "geography": geography,
        "birthday": birthday,
        "warmth": 0,
        "tags": "[]",
        "notes": notes,
        "next_contact": "",
        "last_contacted": "",
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python import_google_contacts.py <path_to_google_contacts.csv>")
        sys.exit(1)

    source_csv = Path(sys.argv[1])
    if not source_csv.exists():
        print(f"Error: File not found: {source_csv}")
        sys.exit(1)

    CONTACTS_DIR.mkdir(parents=True, exist_ok=True)

    # Track slugs for deduplication
    slug_counts: dict[str, int] = {}
    imported = 0
    skipped = 0
    duplicates = 0

    with open(source_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        contacts = []

        for row in reader:
            contact = parse_row(row)
            if contact is None:
                skipped += 1
                continue
            contacts.append(contact)

    # Assign IDs and handle slug collisions
    for i, contact in enumerate(contacts, start=1):
        contact["id"] = i

        base_slug = contact["slug"]
        if base_slug in slug_counts:
            slug_counts[base_slug] += 1
            contact["slug"] = f"{base_slug}-{slug_counts[base_slug]}"
            duplicates += 1
        else:
            slug_counts[base_slug] = 1

        filepath = CONTACTS_DIR / f"{contact['slug']}.yaml"
        write_contact_yaml(filepath, contact)
        imported += 1

    print(f"Import complete:")
    print(f"  Imported: {imported}")
    print(f"  Skipped:  {skipped}")
    print(f"  Duplicate slugs resolved: {duplicates}")
    print(f"  Output:   {CONTACTS_DIR}")


if __name__ == "__main__":
    main()
