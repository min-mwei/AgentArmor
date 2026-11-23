# Families for new injection data

Status: 2026-11-10, Model Trainer. This is the part of the model plan that may be handed to
data authors, and it is handed over alone: it links to nothing else. It names families only.
It holds no benchmark text, case id, count or score, and authors are not shown the analysis
behind it.

Each team's own admission rules apply on top of this: Kai's authoring kit and blind reads for
Kai's data, `train/check_data.py` and the source-block protocol for Summarizer's.

## Ground rules

- Write every example fresh. Do not copy or paraphrase text from any benchmark or evaluation
  set, public or private.
- One scenario, several views: its attack, its benign twins and any re-rendering stay in one
  group and one split.
- Balance surface cues inside each scenario: length, second person, imperatives, quotation
  marks, position. Anything that tracks the label will be learned instead of the label.
- About three benign examples for each attack.
- Vary the instruction itself widely. Many different instructions of a kind, a few scenarios
  each, are worth more than many scenarios around a few instructions.

## What an attack is, in every family

Text in content the agent did not get from its user (a file, a tool result, a page, a message)
that is aimed at the agent reading it and would change what the agent does or says, against the
task its user gave it.

## Effects to cover

| Effect | What the planted text does | Benign twins to write with it |
|---|---|---|
| Reply manipulation | Changes only the form or content of the agent's reply: its layout, encoding, language, tone, or something appended to it. It asks for no file, link, secret or tool call | The user, or a style guide the user points to, asks for the same form. A document describes a format for human writers. A log reports that such an instruction was found and removed |
| Claimed approval or authority | Says someone with standing has already approved, signed off or granted rights, and that a check may therefore be skipped or an action taken | A real approval that sits in the user's own request. A record about approvals addressed to staff. A report of a blocked attempt that quotes the claim |
| Later trigger | Ties an action to a future event: the next time something arrives, opens, runs or is read | A schedule or procedure written for human staff. A changelog describing what a system will do. A quoted example in a note about such attacks |
| Send or delete | Directs something to be sent somewhere, or removed | The same action asked for by the user. A procedure for staff |
| Rule or role change | Tells the agent to adopt new rules, a persona or new standing instructions | A policy document addressed to people. A quoted example |
| Disclosure | Asks the agent to reveal instructions, credentials or private data | A request addressed to a named human. A report of an attempt |

For each effect vary how it is said: a direct "you" instruction, a bare imperative, a statement
in the third person about what the agent or assistant will do, and a passive or modal
requirement. Include wording addressed to a role or a system by name.

Tag every reply-manipulation attack as one of two kinds when you write it, before any model
reads it. **Form only**: the planted text changes only how the reply is rendered, meaning its
layout, encoding, language, case or tone. **Added content**: it puts text of its own choosing
into the reply. Results are reported with the form-only kind and without it, so the tag must
not be changed once a model has read the example.

## Carriers to cover

Write the same effects into each of these, with the instruction at the start, in the middle and
at the end, independent of the label:

- tables: pipe-delimited, comma- or tab-separated, and Markdown;
- emails and tickets with headers and quoted replies;
- source files, comments, configuration and build files;
- command output and logs;
- JSON and YAML tool results, including a single field of a larger record;
- web pages and documentation;
- issue, review and commit text;
- long, bland documents with one planted line;
- very short strings of a few words.

Settings should include software work as an agent sees it (repositories, CI, package and cloud
tooling, issue trackers) as well as everyday business and personal settings.

## Sessions with a proposed call

For allow-or-deny data, a scenario is a session: what the user asked, what the agent read and
did, and one proposed call with a real tool (a shell command, a file write or edit, a web
fetch, a tool from a connected server). Write pairs that hold the call fixed and change one
thing that decides the answer:

- whether the user asked for it, or asked for something narrower;
- where the call's target or content was first named: by the user, or by content the agent read;
- what happened earlier in the session;
- whether an approval was given, when, and whether it was later withdrawn;
- what earlier calls actually returned, as distinct from what was only attempted;
- whether the evidence needed to decide is present at all.

A file that claims authority is still content the agent read. Only a request from the user
carries the user's authority.
