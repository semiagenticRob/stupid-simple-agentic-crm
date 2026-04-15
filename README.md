# Agent CRM

A lightweight, version-controlled personal CRM that lives inside a GitHub repo. Designed to be read, searched, and updated by an AI agent.

Individual YAML files are the source of truth. A CSV index is auto-generated for fast scanning. Your agent operates the CRM by reading the README, following the rules, and committing changes.

## Quick Start

### 1. Fork this repo

Click **Fork** or clone it:

```bash
gh repo create my-crm --private --clone --template semiagenticRob/crm-template
```

### 2. Import your contacts

Export your contacts from Google Contacts (or any source) as CSV, then run the import script:

```bash
python scripts/import_google_contacts.py path/to/your/contacts.csv
python scripts/rebuild_index.py
```

The import script handles Google Contacts CSV format out of the box. For other formats, adapt the `parse_row` function in the script or create contacts manually.

### 3. Point your agent at this repo

Add the repo to your agent's tools or working directory. The agent reads this README as its operating manual. Everything it needs to know about how to interact with the CRM is below.

---

## File Structure

```
contacts/          -> One YAML file per contact (SOURCE OF TRUTH)
index/contacts.csv -> Auto-generated index for scanning (NEVER edit directly)
scripts/           -> rebuild_index.py, import_google_contacts.py
```

---

## How to Find a Contact

**By name (direct access):**
Read `contacts/{slug}.yaml` where slug = lowercased, hyphenated name.
Example: Alice Johnson -> `contacts/alice-johnson.yaml`

**By search:**
Grep the index or contact files.
- By company: `grep "Northwind" index/contacts.csv`
- By tag: `grep "vip" index/contacts.csv`
- By any field in detail files: `grep -rl "pattern" contacts/`

**Aggregate queries (who's overdue, upcoming birthdays, etc.):**
Read `index/contacts.csv` and filter by `next_contact`, `birthday`, or `warmth`.

---

## How to Update a Contact

1. Read `contacts/{slug}.yaml`
2. Modify the relevant fields
3. Write the file back
4. Run `python scripts/rebuild_index.py`
5. Commit both the YAML file and updated `index/contacts.csv`

---

## How to Add a New Contact

1. Determine the next sequential ID: check the last entry in `index/contacts.csv`
2. Create `contacts/{slug}.yaml` using the schema below
3. Run `python scripts/rebuild_index.py`
4. Commit the new YAML file and updated `index/contacts.csv`

---

## How to Log an Interaction

1. Read `contacts/{slug}.yaml`
2. Prepend a new entry to the `history` list (newest first):
   ```yaml
   history:
     - date: "2026-04-15"
       type: call
       summary: "Discussed project timeline"
     - date: "2026-03-01"
       type: meeting
       summary: "Previous interaction..."
   ```
3. Set `last_contacted` to today's date
4. Set a new `next_contact` date if appropriate
5. Run `python scripts/rebuild_index.py`
6. Commit changes

---

## Contact YAML Schema

```yaml
id: 1                       # Sequential integer
first_name: Alice
last_name: Johnson
slug: alice-johnson          # Matches filename (without .yaml)

emails:
  - label: work              # primary | home | work | personal | other
    value: alice@example.com

phones:
  - label: mobile            # mobile | main | home | work | other | fax
    value: "+1-555-123-4567"

company: ""
job_title: ""
industry: ""
geography: ""                # "City, State" or "City, Country"
birthday: ""                 # YYYY-MM-DD

warmth: 0                    # 0=unknown, 1=cold, 2=cool, 3=warm, 4=hot, 5=close
tags: []                     # lowercase-kebab-case: [project-name, vip]

notes: ""                    # Freeform relationship context

next_contact: ""             # YYYY-MM-DD
last_contacted: ""           # YYYY-MM-DD

history: []                  # Newest first
# - date: "YYYY-MM-DD"
#   type: meeting            # meeting | call | text | email | note
#   summary: "What happened"
```

---

## Rules

1. **Dates**: Always `YYYY-MM-DD`
2. **Commit messages**: `CRM: {verb} {First Last} — {detail}`
   - Example: `CRM: Update Alice Johnson — logged call, next contact 2026-06-15`
   - Example: `CRM: Add Jane Smith — new contact from conference`
3. **After any contact file change**: Run `python scripts/rebuild_index.py` and include the updated index in the same commit
4. **Never edit** `index/contacts.csv` directly — it is auto-generated
5. **Warmth values**: 0=unknown, 1=cold, 2=cool, 3=warm, 4=hot, 5=close
6. **Tags**: lowercase-kebab-case (e.g., `project-alpha`, not `Project Alpha`)
7. **History entries**: Newest first, so recent context appears at top of file
8. **Slugs**: lowercase, hyphenated, alpha-numeric only. Duplicates get `-2`, `-3` suffix
9. **Index tags**: Pipe-delimited in CSV (e.g., `vip|investor`)
10. **Never list the `contacts/` directory via API** — the GitHub Contents API caps at 1,000 items. Use `index/contacts.csv` for contact discovery. If a full directory listing is ever needed, use the Git Trees API.

---

## Architecture

```
                  ┌──────────────────────┐
                  │   Agent (Claude, etc) │
                  └──────┬───────────────┘
                         │ reads README for instructions
                         │
            ┌────────────┼────────────────┐
            │            │                │
            v            v                v
     ┌────────────┐ ┌──────────┐ ┌──────────────┐
     │ contacts/  │ │ index/   │ │ scripts/     │
     │ *.yaml     │ │ csv      │ │ rebuild,     │
     │ (truth)    │ │ (derived)│ │ import       │
     └────────────┘ └──────────┘ └──────────────┘
            │              ^
            │              │
            └──────────────┘
         rebuild_index.py generates
         CSV from YAML files
```

**Source of truth**: Individual YAML contact files in `contacts/`.
**Index**: Auto-generated CSV for fast scanning. Never edit directly.
**Agent workflow**: Read YAML -> modify -> write back -> rebuild index -> commit.

---

## Design Decisions

| Decision | Choice | Why |
|---|---|---|
| Source of truth | YAML files, not CSV | Eliminates sync bugs between two manually maintained files |
| File format | YAML over JSON | Fewer tokens, more readable, easier for agents to write correctly |
| File naming | Slugified names | Agent can access contacts directly by name without consulting an index |
| Index format | Auto-generated CSV | Lean scanning for aggregate queries; never drifts from source files |
| Tags in CSV | Pipe-delimited | Avoids CSV quoting issues with comma-separated values inside CSV fields |
| Warmth scale | 0-5 numeric | Sortable, filterable, unambiguous. 0=unknown allows clean cold-start imports |
| Emails/phones | Lists, not single values | Real contact data often has multiple emails and phones per person |

---

## Sample Contacts

This repo ships with 7 sample contacts demonstrating different scenarios:

| Contact | Demonstrates |
|---|---|
| Alice Johnson | Rich data: multiple emails, full history, tags, warmth, birthday |
| Bob Martinez | Business contact with pipeline-relevant notes |
| Carol Nguyen | Close friend (warmth=5), personal relationship |
| David Okafor | Cool lead (warmth=2), minimal interaction history |
| Elena Ross | Freelancer/contractor relationship |
| Frank | Sparse contact — just a first name and phone number |
| Grace Kim | Alumni network contact with multiple phones |

Delete these and import your own contacts to get started.

---

## License

MIT
