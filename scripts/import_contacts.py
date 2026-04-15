#!/usr/bin/env python3
"""Unified contact importer: parse various CSV formats, deduplicate against
existing CRM contacts, merge additive data, and create new contact files.

Usage:
    python scripts/import_contacts.py --source phone --file path/to/contacts.csv --dry-run
    python scripts/import_contacts.py --source phone --file path/to/contacts.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTACTS_DIR = REPO_ROOT / "contacts"

# Last-name values that are actually labels, not real surnames.
# Customize this set for your data — phone exports often put contextual labels
# (e.g., "Neighbor", "Gym") in the last-name field instead of a real surname.
LABEL_LAST_NAMES = {
    "student", "neighbor", "friend", "gym", "maid",
    "landlord", "doctor", "dentist", "plumber", "realtor",
}

# ─── Phone number helpers ────────────────────────────────────────────────────

def normalize_phone(raw: str) -> str:
    """Strip a phone string to digits. Drop leading 1 if 11 digits (US)."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) >= 7 else ""


# ─── YAML helpers (mirrored from rebuild_index.py / import_google_contacts.py)

def _clean_value(value: str) -> str:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def parse_yaml_simple(filepath: Path) -> dict:
    """Minimal YAML parser for our known contact schema (zero dependencies)."""
    content = filepath.read_text(encoding="utf-8")
    data: dict = {}
    current_list_key: str | None = None
    current_list: list = []
    current_item: dict = {}

    for line in content.split("\n"):
        stripped = line.strip()

        if not stripped or stripped.startswith("#"):
            if current_list_key and current_item:
                current_list.append(current_item)
                current_item = {}
            continue

        if stripped.startswith("- "):
            if current_list_key:
                if current_item:
                    current_list.append(current_item)
                    current_item = {}
                item_content = stripped[2:]
                if ":" in item_content:
                    k, v = item_content.split(":", 1)
                    current_item[k.strip()] = _clean_value(v.strip())
                else:
                    current_list.append(_clean_value(item_content))
                    current_item = {}
            continue

        if line.startswith("    ") and current_list_key and current_item is not None:
            if ":" in stripped:
                k, v = stripped.split(":", 1)
                current_item[k.strip()] = _clean_value(v.strip())
            continue

        if ":" in stripped and not stripped.startswith("-"):
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
                inner = value[1:-1].strip()
                data[key] = [_clean_value(v.strip()) for v in inner.split(",")] if inner else []
            elif value == "" or value is None:
                current_list_key = key
                current_list = []
                current_item = {}
            else:
                data[key] = _clean_value(value)

    if current_list_key:
        if current_item:
            current_list.append(current_item)
        data[current_list_key] = current_list

    return data


def yaml_str(value: str) -> str:
    if not value:
        return '""'
    if any(c in value for c in ":{}\n[]&*?|>!%@`#,") or value.startswith(("-", " ")):
        return f'"{value}"'
    if value.lower() in ("true", "false", "yes", "no", "null", "on", "off"):
        return f'"{value}"'
    return value


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def write_contact_yaml(filepath: Path, contact: dict):
    """Write a full contact dict to YAML, preserving history if present."""
    lines: list[str] = []
    lines.append(f"id: {contact['id']}")
    lines.append(f"first_name: {yaml_str(contact['first_name'])}")
    lines.append(f"last_name: {yaml_str(contact['last_name'])}")
    lines.append(f"slug: {contact['slug']}")
    lines.append("")

    if contact.get("emails"):
        lines.append("emails:")
        for e in contact["emails"]:
            lines.append(f"  - label: {e['label']}")
            lines.append(f"    value: {yaml_str(e['value'])}")
    else:
        lines.append("emails: []")
    lines.append("")

    if contact.get("phones"):
        lines.append("phones:")
        for p in contact["phones"]:
            lines.append(f"  - label: {p['label']}")
            lines.append(f"    value: {yaml_str(p['value'])}")
    else:
        lines.append("phones: []")
    lines.append("")

    lines.append(f"company: {yaml_str(contact.get('company', ''))}")
    lines.append(f"job_title: {yaml_str(contact.get('job_title', ''))}")
    lines.append(f"industry: {yaml_str(contact.get('industry', ''))}")
    lines.append(f"geography: {yaml_str(contact.get('geography', ''))}")
    lines.append(f"birthday: {yaml_str(contact.get('birthday', ''))}")
    lines.append("")
    lines.append(f"warmth: {contact.get('warmth', 0)}")

    # Tags
    tags = contact.get("tags", [])
    if isinstance(tags, list) and tags:
        lines.append("tags:")
        for t in tags:
            lines.append(f"  - {t}")
    elif isinstance(tags, str) and tags not in ("[]", ""):
        lines.append(f"tags: {tags}")
    else:
        lines.append("tags: []")
    lines.append("")

    lines.append(f"notes: {yaml_str(contact.get('notes', ''))}")
    lines.append("")
    lines.append(f"next_contact: {yaml_str(contact.get('next_contact', ''))}")
    lines.append(f"last_contacted: {yaml_str(contact.get('last_contacted', ''))}")
    lines.append("")

    # History — preserve existing entries
    history = contact.get("history", [])
    if isinstance(history, list) and history:
        lines.append("history:")
        for entry in history:
            if isinstance(entry, dict):
                lines.append(f"  - date: {yaml_str(entry.get('date', ''))}")
                if entry.get("type"):
                    lines.append(f"    type: {entry['type']}")
                if entry.get("summary"):
                    lines.append(f"    summary: {yaml_str(entry['summary'])}")
            else:
                lines.append(f"  - {entry}")
    else:
        lines.append("history: []")
    lines.append("")

    filepath.write_text("\n".join(lines), encoding="utf-8")


# ─── Phone CSV parser ────────────────────────────────────────────────────────

# Column header -> CRM phone label mapping for phone contact exports.
# Phone apps export with headers like "Phone : mobile", "Phone : X-MAIN", etc.
PHONE_LABEL_MAP = {
    "Phone : mobile": "mobile",
    "Phone : X-MAIN": "main",
    "Phone : home": "home",
    "Phone : work": "work",
    "Phone : X-Mobile": "mobile",
    "Phone : X-Home": "home",
    "Phone : VOICE": "mobile",
    "Phone : ": "other",
    "Phone : X-WhatsApp": "whatsapp",
}

EMAIL_LABEL_MAP = {
    "Email : X-INTERNET": "primary",
    "Email : home": "home",
    "Email : work": "work",
}


def _clean_name_part(s: str) -> str:
    """Strip common prefixes and whitespace from a name component."""
    s = s.strip()
    for prefix in ("Mr.", "Mrs.", "Ms.", "Dr.", "Capt."):
        if s.startswith(prefix):
            s = s[len(prefix):].strip()
    return s


def parse_phone_csv(filepath: Path) -> list[dict]:
    """Parse phone contacts CSV into candidate contact dicts.

    Expected CSV layout (common phone contact export format):
      Row 0: empty (all commas)
      Row 1: headers
      Row 2+: data

    Columns: (empty), Last name, Prefix, First name, Middle name, Job title,
    Company, Phone:mobile, Phone:X-MAIN, Phone:home, Phone:work, Phone:X-Mobile,
    Phone:X-Home, Phone:VOICE, Phone:(unnamed), Phone:X-WhatsApp,
    Email:X-INTERNET, Email:home, Email:work,
    Address:home (Street/City/State/Country/ZIP),
    Address:work (Street/City/State/Country/ZIP),
    URL:homepage, Birthday
    """
    candidates: list[dict] = []

    with open(filepath, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)

    if len(rows) < 3:
        return candidates

    # Row 0 is empty, row 1 is headers, data starts at row 2
    headers = rows[1]

    # Build column index map for phone/email columns
    phone_cols: list[tuple[int, str]] = []
    email_cols: list[tuple[int, str]] = []
    for idx, h in enumerate(headers):
        h_stripped = h.strip()
        if h_stripped in PHONE_LABEL_MAP:
            phone_cols.append((idx, PHONE_LABEL_MAP[h_stripped]))
        elif h_stripped in EMAIL_LABEL_MAP:
            email_cols.append((idx, EMAIL_LABEL_MAP[h_stripped]))

    for row in rows[2:]:
        if len(row) < 2:
            continue

        # Pad row to at least 31 columns
        while len(row) < 31:
            row.append("")

        raw_last = row[1].strip()
        raw_first = row[3].strip()
        prefix = row[2].strip()
        middle = row[4].strip()
        job_title = row[5].strip()
        company = row[6].strip()

        # ── Name cleaning ──

        first_name = _clean_name_part(raw_first)
        last_name = _clean_name_part(raw_last)

        # Move descriptive last names to tags
        import_tags: list[str] = []
        if last_name.lower() in LABEL_LAST_NAMES:
            import_tags.append(slugify(last_name))
            last_name = ""

        # If first_name contains a full name and last_name is empty, split
        if " " in first_name and not last_name:
            # Check for "Last, First" pattern
            if "," in first_name:
                parts = first_name.split(",", 1)
                last_name = parts[0].strip()
                first_name = parts[1].strip()
            else:
                # Simple "First Last" split (only if exactly 2 words)
                words = first_name.split()
                if len(words) == 2:
                    first_name = words[0]
                    last_name = words[1]

        # ── Phones ──
        phones: list[dict] = []
        seen_phone_digits: set[str] = set()
        for col_idx, label in phone_cols:
            val = row[col_idx].strip()
            if val:
                norm = normalize_phone(val)
                if norm and norm not in seen_phone_digits:
                    phones.append({"label": label, "value": val})
                    seen_phone_digits.add(norm)

        # ── Emails ──
        emails: list[dict] = []
        seen_emails: set[str] = set()
        for col_idx, label in email_cols:
            val = row[col_idx].strip()
            if val and "@" in val:
                lower = val.lower()
                if lower not in seen_emails:
                    emails.append({"label": label, "value": val})
                    seen_emails.add(lower)

        # ── Geography from address ──
        home_city = row[20].strip()
        home_state = row[21].strip()
        if home_city and home_state:
            geography = f"{home_city}, {home_state}"
        elif home_city:
            geography = home_city
        elif home_state:
            geography = home_state
        else:
            # Try work address
            work_city = row[25].strip()
            work_state = row[26].strip()
            if work_city and work_state:
                geography = f"{work_city}, {work_state}"
            elif work_city:
                geography = work_city
            elif work_state:
                geography = work_state
            else:
                geography = ""

        # ── Birthday ──
        birthday = row[30].strip() if len(row) > 30 else ""

        # ── Skip truly empty rows ──
        if not first_name and not last_name and not phones and not emails:
            continue

        candidates.append({
            "first_name": first_name,
            "last_name": last_name,
            "phones": phones,
            "emails": emails,
            "company": company,
            "job_title": job_title,
            "geography": geography,
            "birthday": birthday,
            "import_tags": import_tags,
        })

    return candidates


# ─── Contact loader & index builder ──────────────────────────────────────────

def load_existing_contacts() -> tuple[
    dict[str, dict],          # slug -> parsed contact data
    dict[str, str],           # normalized_phone -> slug
    dict[str, str],           # email_lower -> slug
    dict[tuple[str, str], str],  # (first_lower, last_lower) -> slug
]:
    """Load all existing YAML contacts and build lookup indexes."""
    contacts: dict[str, dict] = {}
    phone_index: dict[str, str] = {}
    email_index: dict[str, str] = {}
    name_index: dict[tuple[str, str], str] = {}

    for filepath in sorted(CONTACTS_DIR.glob("*.yaml")):
        try:
            data = parse_yaml_simple(filepath)
        except Exception:
            continue

        slug = data.get("slug", filepath.stem)
        contacts[slug] = data

        # Index phones
        phone_list = data.get("phones", [])
        if isinstance(phone_list, list):
            for p in phone_list:
                if isinstance(p, dict):
                    norm = normalize_phone(p.get("value", ""))
                    if norm:
                        phone_index[norm] = slug

        # Index emails
        email_list = data.get("emails", [])
        if isinstance(email_list, list):
            for e in email_list:
                if isinstance(e, dict):
                    val = e.get("value", "").strip().lower()
                    if val:
                        email_index[val] = slug

        # Index name
        fn = data.get("first_name", "").strip().lower()
        ln = data.get("last_name", "").strip().lower()
        if fn or ln:
            name_index[(fn, ln)] = slug

    return contacts, phone_index, email_index, name_index


# ─── Matcher ─────────────────────────────────────────────────────────────────

def find_match(
    candidate: dict,
    phone_index: dict[str, str],
    email_index: dict[str, str],
    name_index: dict[tuple[str, str], str],
) -> tuple[str, str] | None:
    """Find matching CRM slug for a candidate. Returns (slug, method) or None."""

    # 1. Phone match (highest confidence)
    for p in candidate.get("phones", []):
        norm = normalize_phone(p.get("value", ""))
        if norm and norm in phone_index:
            return (phone_index[norm], "phone")

    # 2. Email match
    for e in candidate.get("emails", []):
        val = e.get("value", "").strip().lower()
        if val and val in email_index:
            return (email_index[val], "email")

    # 3. Name match (requires both parts, with length guards)
    fn = candidate.get("first_name", "").strip().lower()
    ln = candidate.get("last_name", "").strip().lower()
    if fn and ln and (len(fn) >= 2 or len(ln) >= 2):
        if (fn, ln) in name_index:
            return (name_index[(fn, ln)], "name")

    return None


# ─── Merger ──────────────────────────────────────────────────────────────────

def compute_merge(candidate: dict, existing: dict) -> dict[str, any]:
    """Compute additive-only changes to apply to an existing contact.

    Returns a dict of field_name -> new_value for fields that should change.
    Only adds data; never overwrites non-empty fields.
    """
    changes: dict = {}

    # ── Add new phone numbers ──
    existing_phones = existing.get("phones", [])
    if not isinstance(existing_phones, list):
        existing_phones = []
    existing_phone_digits = set()
    for p in existing_phones:
        if isinstance(p, dict):
            norm = normalize_phone(p.get("value", ""))
            if norm:
                existing_phone_digits.add(norm)

    new_phones: list[dict] = []
    for p in candidate.get("phones", []):
        norm = normalize_phone(p.get("value", ""))
        if norm and norm not in existing_phone_digits:
            new_phones.append(p)
            existing_phone_digits.add(norm)
    if new_phones:
        changes["phones_to_add"] = new_phones

    # ── Add new emails ──
    existing_emails = existing.get("emails", [])
    if not isinstance(existing_emails, list):
        existing_emails = []
    existing_email_set = set()
    for e in existing_emails:
        if isinstance(e, dict):
            val = e.get("value", "").strip().lower()
            if val:
                existing_email_set.add(val)

    new_emails: list[dict] = []
    for e in candidate.get("emails", []):
        val = e.get("value", "").strip().lower()
        if val and val not in existing_email_set:
            new_emails.append(e)
            existing_email_set.add(val)
    if new_emails:
        changes["emails_to_add"] = new_emails

    # ── Fill empty scalar fields ──
    for field in ("company", "job_title", "geography", "birthday"):
        existing_val = existing.get(field, "").strip()
        candidate_val = candidate.get(field, "").strip()
        if not existing_val and candidate_val:
            changes[field] = candidate_val

    # ── Add last_name if CRM has first-only and candidate provides one ──
    existing_ln = existing.get("last_name", "").strip()
    candidate_ln = candidate.get("last_name", "").strip()
    if not existing_ln and candidate_ln:
        changes["last_name"] = candidate_ln

    return changes


def apply_merge(existing: dict, changes: dict) -> dict:
    """Apply computed merge changes to a copy of the existing contact dict."""
    merged = {}
    # Deep-copy the relevant fields
    for k, v in existing.items():
        if isinstance(v, list):
            merged[k] = list(v)
        elif isinstance(v, dict):
            merged[k] = dict(v)
        else:
            merged[k] = v

    if "phones_to_add" in changes:
        phones = merged.get("phones", [])
        if not isinstance(phones, list):
            phones = []
        phones = list(phones) + changes["phones_to_add"]
        merged["phones"] = phones

    if "emails_to_add" in changes:
        emails = merged.get("emails", [])
        if not isinstance(emails, list):
            emails = []
        emails = list(emails) + changes["emails_to_add"]
        merged["emails"] = emails

    for field in ("company", "job_title", "geography", "birthday", "last_name"):
        if field in changes:
            merged[field] = changes[field]

    return merged


# ─── New contact creation ────────────────────────────────────────────────────

def make_new_contact(candidate: dict, next_id: int, used_slugs: set[str]) -> dict:
    """Create a full contact dict for a new CRM entry."""
    fn = candidate.get("first_name", "")
    ln = candidate.get("last_name", "")

    # Build display name for slug
    if fn or ln:
        display = f"{fn} {ln}".strip()
    elif candidate.get("company"):
        display = candidate["company"]
    elif candidate.get("phones"):
        # Last resort: use phone digits
        display = normalize_phone(candidate["phones"][0]["value"])
    else:
        display = f"unknown-{next_id}"

    base_slug = slugify(display)
    if not base_slug:
        base_slug = f"contact-{next_id}"

    # Handle slug collisions
    slug = base_slug
    counter = 2
    while slug in used_slugs:
        slug = f"{base_slug}-{counter}"
        counter += 1
    used_slugs.add(slug)

    return {
        "id": next_id,
        "first_name": fn,
        "last_name": ln,
        "slug": slug,
        "emails": candidate.get("emails", []),
        "phones": candidate.get("phones", []),
        "company": candidate.get("company", ""),
        "job_title": candidate.get("job_title", ""),
        "industry": "",
        "geography": candidate.get("geography", ""),
        "birthday": candidate.get("birthday", ""),
        "warmth": 0,
        "tags": candidate.get("import_tags", []),
        "notes": "",
        "next_contact": "",
        "last_contacted": "",
        "history": [],
    }


# ─── Dry-run report ─────────────────────────────────────────────────────────

def print_report(
    parsed_count: int,
    skipped_count: int,
    matches: list[tuple[dict, str, str, dict]],   # (candidate, slug, method, changes)
    new_contacts: list[dict],
    flagged: list[tuple[dict, str]],               # (candidate, reason)
):
    """Print a summary of what the import would do."""
    print(f"\n{'='*60}")
    print(f"  IMPORT DRY-RUN REPORT")
    print(f"{'='*60}")
    print(f"  Parsed:  {parsed_count} contacts from CSV")
    print(f"  Skipped: {skipped_count} (no identifying info)")
    print()

    # Match stats
    by_phone = sum(1 for _, _, m, _ in matches if m == "phone")
    by_email = sum(1 for _, _, m, _ in matches if m == "email")
    by_name  = sum(1 for _, _, m, _ in matches if m == "name")
    print(f"  MATCHES: {len(matches)}")
    print(f"    By phone: {by_phone}")
    print(f"    By email: {by_email}")
    print(f"    By name:  {by_name}")
    print()

    # Merge stats
    merges_with_changes = [(c, s, m, ch) for c, s, m, ch in matches if ch]
    phones_added = sum(1 for _, _, _, ch in merges_with_changes if "phones_to_add" in ch)
    emails_added = sum(1 for _, _, _, ch in merges_with_changes if "emails_to_add" in ch)
    company_filled = sum(1 for _, _, _, ch in merges_with_changes if "company" in ch)
    job_filled = sum(1 for _, _, _, ch in merges_with_changes if "job_title" in ch)
    geo_filled = sum(1 for _, _, _, ch in merges_with_changes if "geography" in ch)
    bday_filled = sum(1 for _, _, _, ch in merges_with_changes if "birthday" in ch)
    ln_filled = sum(1 for _, _, _, ch in merges_with_changes if "last_name" in ch)

    print(f"  MERGES (additive changes to existing contacts): {len(merges_with_changes)}")
    if merges_with_changes:
        print(f"    New phones added:     {phones_added} contacts")
        print(f"    New emails added:     {emails_added} contacts")
        print(f"    Company filled:       {company_filled} contacts")
        print(f"    Job title filled:     {job_filled} contacts")
        print(f"    Geography filled:     {geo_filled} contacts")
        print(f"    Birthday filled:      {bday_filled} contacts")
        print(f"    Last name added:      {ln_filled} contacts")
    print()

    print(f"  NEW CONTACTS: {len(new_contacts)}")
    print()

    if flagged:
        print(f"  FLAGGED FOR REVIEW: {len(flagged)}")
        for cand, reason in flagged[:20]:
            fn = cand.get("first_name", "")
            ln = cand.get("last_name", "")
            name = f"{fn} {ln}".strip() or "(no name)"
            print(f"    - {name!r} -- {reason}")
        if len(flagged) > 20:
            print(f"    ... and {len(flagged) - 20} more")
        print()

    print(f"{'='*60}")
    print(f"  Revert all changes: git checkout -- contacts/")
    print(f"{'='*60}\n")


def write_review_csv(
    filepath: Path,
    matches: list[tuple[dict, str, str, dict]],
    new_contacts: list[dict],
    flagged: list[tuple[dict, str]],
):
    """Write a CSV file for manual review of proposed import actions."""
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "action", "match_method", "matched_slug",
            "import_first", "import_last", "import_phone", "import_email",
            "changes_summary", "flag_reason",
        ])
        for cand, slug, method, changes in matches:
            phone = cand["phones"][0]["value"] if cand.get("phones") else ""
            email = cand["emails"][0]["value"] if cand.get("emails") else ""
            change_parts = []
            if "phones_to_add" in changes:
                change_parts.append(f"+{len(changes['phones_to_add'])} phones")
            if "emails_to_add" in changes:
                change_parts.append(f"+{len(changes['emails_to_add'])} emails")
            for f_name in ("company", "job_title", "geography", "birthday", "last_name"):
                if f_name in changes:
                    change_parts.append(f"+{f_name}")
            action = "merge" if changes else "match-only"
            writer.writerow([
                action, method, slug,
                cand.get("first_name", ""), cand.get("last_name", ""),
                phone, email,
                "; ".join(change_parts), "",
            ])
        for contact in new_contacts:
            phone = contact["phones"][0]["value"] if contact.get("phones") else ""
            email = contact["emails"][0]["value"] if contact.get("emails") else ""
            writer.writerow([
                "create", "", contact["slug"],
                contact.get("first_name", ""), contact.get("last_name", ""),
                phone, email,
                "", "",
            ])
        for cand, reason in flagged:
            phone = cand["phones"][0]["value"] if cand.get("phones") else ""
            email = cand["emails"][0]["value"] if cand.get("emails") else ""
            writer.writerow([
                "flagged", "", "",
                cand.get("first_name", ""), cand.get("last_name", ""),
                phone, email,
                "", reason,
            ])
    print(f"Review CSV written to: {filepath}")


# ─── Main pipeline ───────────────────────────────────────────────────────────

def run_import(source: str, filepath: Path, dry_run: bool, review_file: Path | None):
    """Run the full import pipeline."""

    # 1. Parse source CSV
    print(f"Parsing {source} CSV: {filepath}")
    if source == "phone":
        candidates = parse_phone_csv(filepath)
    else:
        print(f"Error: source '{source}' not yet implemented in this script.")
        print("For Google Contacts, use: python scripts/import_google_contacts.py")
        sys.exit(1)

    print(f"  Parsed {len(candidates)} contacts")

    # 2. Load existing CRM contacts
    print("Loading existing CRM contacts...")
    existing_contacts, phone_idx, email_idx, name_idx = load_existing_contacts()
    print(f"  Loaded {len(existing_contacts)} existing contacts")

    # 3. Match, merge, and collect results
    matches: list[tuple[dict, str, str, dict]] = []  # (candidate, slug, method, changes)
    to_create: list[dict] = []
    flagged: list[tuple[dict, str]] = []

    # Track used slugs for collision avoidance
    used_slugs = set(existing_contacts.keys())
    max_id = max((int(c.get("id", 0)) for c in existing_contacts.values()), default=0)
    next_id = max_id + 1

    skipped = 0
    for cand in candidates:
        match = find_match(cand, phone_idx, email_idx, name_idx)

        if match:
            slug, method = match
            existing = existing_contacts[slug]
            changes = compute_merge(cand, existing)
            matches.append((cand, slug, method, changes))
        else:
            # Flag contacts with no name
            fn = cand.get("first_name", "").strip()
            ln = cand.get("last_name", "").strip()
            if not fn and not ln:
                if cand.get("phones") or cand.get("emails"):
                    flagged.append((cand, "no name — phone/email only"))
                else:
                    skipped += 1
                continue

            new_contact = make_new_contact(cand, next_id, used_slugs)
            to_create.append(new_contact)
            next_id += 1

    # 4. Report
    total_parsed = len(candidates)
    print_report(total_parsed, skipped, matches, to_create, flagged)

    if review_file:
        write_review_csv(review_file, matches, to_create, flagged)

    if dry_run:
        print("DRY RUN — no files written.")
        return

    # 5. Apply merges
    merge_count = 0
    for cand, slug, method, changes in matches:
        if not changes:
            continue
        existing = existing_contacts[slug]
        merged = apply_merge(existing, changes)
        fpath = CONTACTS_DIR / f"{slug}.yaml"
        write_contact_yaml(fpath, merged)
        merge_count += 1

    # 6. Create new contacts
    for contact in to_create:
        fpath = CONTACTS_DIR / f"{contact['slug']}.yaml"
        write_contact_yaml(fpath, contact)

    print(f"\nImport complete:")
    print(f"  Merged:  {merge_count} existing contacts updated")
    print(f"  Created: {len(to_create)} new contacts")
    print(f"  Flagged: {len(flagged)} (not imported — review needed)")
    print(f"\nNext steps:")
    print(f"  python scripts/rebuild_index.py")
    print(f"  git diff --stat")
    print(f"  git add contacts/ index/ && git commit -m 'CRM: Import contacts'")


def main():
    parser = argparse.ArgumentParser(
        description="Import contacts from CSV into the CRM."
    )
    parser.add_argument(
        "--source", required=True, choices=["phone", "google"],
        help="CSV format: 'phone' for phone contacts export, 'google' for Google Contacts"
    )
    parser.add_argument(
        "--file", required=True, type=Path,
        help="Path to the CSV file"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would happen without writing any files"
    )
    parser.add_argument(
        "--review-file", type=Path, default=None,
        help="Write a CSV review file listing all proposed actions"
    )
    args = parser.parse_args()

    if not args.file.exists():
        print(f"Error: file not found: {args.file}")
        sys.exit(1)

    run_import(args.source, args.file, args.dry_run, args.review_file)


if __name__ == "__main__":
    main()
