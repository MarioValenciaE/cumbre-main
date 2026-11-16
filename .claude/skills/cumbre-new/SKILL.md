---
name: cumbre-new
description: Take new client or project documents (files dropped in clients/_inbox/ or clients/<client>/inbox/, or files the user attaches or points to), work out which company they belong to, match them to an existing client folder or create a new one, file them into a discovery session, and run the full /cumbre-discovery analysis. Use when the user shares or drops documents for a client/prospect/new project, e.g. "/cumbre-new", "/cumbre-new ProPES", "here are the files from today's meeting".
argument-hint: "[client name] (optional; files can be attached or dropped in an inbox)"
---

# /cumbre-new

Flow: **find the files → match the client → file them into a session → analyze**. This skill handles the first three steps. For the analysis, follow [`.claude/skills/cumbre-discovery/SKILL.md`](../cumbre-discovery/SKILL.md), and do not restate or change its methodology.

Script: `python3 scripts/discovery/cumbre_session.py`, run from the repo root.

## 1. Find the new files

1. Run `python3 scripts/discovery/cumbre_session.py clients`. It lists the client folders with their known names, sessions and pending inbox files, the unsorted inbox `clients/_inbox/`, and the names in the public index `FOLDERS/`. It also creates `clients/_inbox/` if it is missing.
2. The batch consists of these files:
   - files the user attached or gave paths for in this conversation;
   - all pending files in `clients/_inbox/`;
   - pending files in `clients/<client>/inbox/`. Take only the named client's inbox when a client was named; otherwise take every client inbox that has pending files.
3. If a file is somewhere Git tracks (`FOLDERS/`, `docs/`, or anywhere outside `clients/`), **stop**. Tell the user that folder may be public, and offer to move the file to `clients/_inbox/`. Move it only after the user says yes.
4. If the batch is empty, tell the user to attach the files or drop them in `clients/_inbox/`, then run `/cumbre-new` again. Stop there.

## 2. Match each file to a client

Read each file and find the company it is about: look at the transcript header, the deck title, the names in documents, and the file names. Then compare against the `clients` listing (folder slug and `names=`) and the `FOLDERS/` names. Allow for spelling, case, accents, legal suffixes (S.A.S., Ltda.) and abbreviations, so that "Pro PES S.A.S." matches `propes`.

- **One clear match** (the user named the client, or the documents name a company that matches exactly one folder): use that folder and pass `--client-slug <slug>` to every command.
- **No match**: this is a new client. Use the company name as written in the documents, or as the user gave it, and omit `--client-slug` so the script derives the slug from the name. A `FOLDERS/<Name>/` entry with no private folder yet counts as an existing prospect: reuse its name so the slug matches.
- **Ask the user** when: more than one folder could match; the match is only partial (for example the same sector, or a similar but different name); the documents name no company and the user didn't either; or the user's name and the documents' name conflict.

A batch can hold several clients. Split it by client.

To match, read only the `clients` listing. Do not open other clients' `inputs/` or `outputs/`.

## 3. Sort the files into sessions

For each file, decide its **kind**. The kind sets the evidence tag, so be careful:

| Kind | Flag | What it is |
| --- | --- | --- |
| transcript | `--transcript` | Verbatim record of what was said (speaker lines, timestamps) |
| notes | `--notes` | Cumbre's own notes or impressions, written by a founder. These are never client facts |
| deck | `--deck` | The presentation used in the meeting |
| doc | `--doc` | A narrative document the client supplied (process document, catalog, contract, PDF) |
| data | `--data` | Tabular data from the client (CSV, Excel, system export), including counts and samples |
| external | `--external` | Public web or social research about the client, with its source |

Then decide its **session**:

- The meeting date applies to the whole batch for that client. One dated file, such as the transcript, also dates the undated files whose content clearly refers to the same meeting. A partial date such as "12 nov" takes its year from the dated file it matches. Never use today's date as a guess.
- If the client already has a session for that meeting, add the file to it with `add`. Material sent after a meeting goes into that meeting's session. Create a new session only for a new meeting.
- Ask the user when no file gives the date, when a file could be either client evidence or Cumbre notes, or when the target session is unclear.

Before you run any command, show the user one short table: file → client (existing / **new**) → kind → session. Then continue, unless one of the "ask" cases above applies.

If you can't read a file (for example audio, video or a scanned image), still file it under its likely kind and say it could not be read. Audio and video need a transcript before they can be analyzed.

## 4. Create or update the session

- **Files already inside `clients/`** (the inboxes): use `--move`. They become the session's only copy.
- **Attached files or paths outside `clients/`**: copy them, i.e. leave out `--move`. The user's originals stay where they are.
- **A session with both kinds**: `--move` applies to every file in one call. Run `new --move` with the inbox files, then `add <session>` (no `--move`) with the attached files.

```bash
# new client, or new meeting for an existing client
python3 scripts/discovery/cumbre_session.py new --client "<Company name>" --client-slug <slug> --date <YYYY-MM-DD> [--move] \
  --transcript "<file>" --notes "<file>" --deck "<file>" --doc "<file>" --data "<file>"
# more files for an existing session
python3 scripts/discovery/cumbre_session.py add clients/<slug>/sessions/<id> [--move] --doc "<file>"
```

Quote paths that contain spaces. Afterwards, run `clients` again. Every file you processed should be gone from the inboxes; report any that are left.

Tell the user which lines of `session.yaml` they may want to change: attendees (by role) and the meeting stage, if the files or the user give them, and approved rates, if the user gives them. Do not edit `session.yaml` yourself.

For a **new** client, ask whether to add a public-safe `FOLDERS/<Name>/README.md`. It would hold only the status, using the outline in `FOLDERS/README.md`. That file is tracked by Git, so create it only after the user says yes, and never put meeting content in it.

## 5. Analyze

For each new or updated session, continue with [`cumbre-discovery/SKILL.md`](../cumbre-discovery/SKILL.md), from **"2. Preflight"** through **"6. Final response"**. Process one session at a time, in date order. A later session of the **same** client may read that client's earlier outputs as prior analysis; cite them as prior analysis, not as new evidence.

Never commit, push, or copy client content into tracked files.
