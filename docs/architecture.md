# Architecture: AgentArmor with Summarizer as its session core

Version 1, 2026-11-13. A design for review, not a description of what is built: section 9
says what exists today. The user set the direction that day: Summarizer becomes a core
part of AgentArmor and the real work happens in this repository; sessions are searched
through SQLite, exactly, by full text and by vector; history can be deleted and compacted
as a session runs for hours or days; nothing exists twice; the code is simple and robust.

AgentArmor decides whether a tool call an LLM coding agent proposes may run. The release
this design serves adds decisions that depend on earlier turns and earlier sessions, and
must keep what the model already does on single tool calls and on injected content.

## 1. The parts

```text
 agent (Claude Code, Codex)
   │  every hook event
   ▼
 hook ───────────────► journal            one append-only file per session
 (observer, never waits)   │
                           ▼
                   ┌─ session core ─────────────────────────────────────────┐
                   │ summarizer: cuts events into pieces, names what they    │
                   │ name, marks what looks suspicious, keeps the store      │
                   │                                                         │
                   │ store (SQLite): pieces, names, words, vectors,          │
                   │                 what was removed                        │
                   │ lookup: what earlier turns and sessions hold for one    │
                   │         proposed call, as quoted lines with their ties  │
                   └──────────────┬──────────────────────────────────────────┘
                                  │ packet: quoted lines, an account of what is missing
                                  ▼
                   ┌─ monitor ──────────────────────────────────────────────┐
                   │ local server: owns session scope, the call's identity,  │
                   │ the rules and the handoff; the gate asks it             │
                   └──────────────┬──────────────────────────────────────────┘
                                  │ state: rules, packet, proposed call; a registered question
                                  ▼
                               Kai-4B          on this machine, or at a configured address
                                  │ allow or deny, with probabilities
                                  ▼
                            the gate answers the agent's hook
```

| Part | Where | Does | Never does |
|---|---|---|---|
| Session core | `src/summarizer` (package `summarizer`) | Captures, cuts, names, marks, stores, searches, and builds the packet | Decides, blocks, or writes text of its own |
| Monitor | `src/monitor` (`agentarmor_monitor`) | Runs the local server and the gate, builds the state, asks Kai, records what was answered | Re-implements anything of the core: it imports it |
| Serving | `src/vllm` (`agentarmor_vllm`) | Serves Kai-4B | Sees a session except through a request |
| Host facts | `src/hostfacts` | Deterministic facts shared with Kai's training | |

## 2. Rules the design is held to

1. **The hook is an observer.** It appends to its session's journal and returns. It uses
   the standard library only, never waits on another process, never blocks or denies.
2. **Who said it comes from the channel.** A line is the user's, the agent's or lower-trust
   content because of the hook event that delivered it, never because of what it says.
3. **Extractive.** No model writes a word that Kai reads. Models rank, link, mark and find.
4. **Honest coverage.** Whatever was dropped, clipped, removed, unread or made by another
   model is counted where the reader sees it. Nothing found is never reported as nothing
   there.
5. **Evidence and leads are different things.** A line reaches a packet as evidence only
   through a tie that can be checked: the same path, the same destination, the same
   identifier, who wrote a file, what the user said. A search hit, by words or by vector,
   is a lead. It is shown as a candidate, under a cap, and never in place of evidence.
6. **Facts and opinions are stored apart.** What the journal says of a piece is one layer.
   What a model or a rule said of it (a mark, a vector) is another, named by what made it,
   so that replacing a model invalidates its layer and nothing else.
7. **One of everything.** One code path reads a hook event. One store holds a session. One
   place builds a packet. A component imports another; it never copies from it.
8. **The summarizer tags, Kai decides, the server owns the handoff.** Recall comes first in
   tagging; Kai is the stage that must keep false denials low.

## 3. The store

One SQLite file for a spool, written by the daemon alone and read by anyone.

| Table | Holds | Layer |
|---|---|---|
| `sessions` | identity, working directory, times, how far the journal has been read | fact |
| `units` | one row for each piece: when, who, where from, the tool, what became of a call, the text | fact |
| `names` | the paths, hosts and identifiers each piece names | fact |
| `words` | full-text index of the pieces' text (FTS5) | fact |
| `vectors` | one embedding for each piece worth finding, with the encoder that made it | opinion |
| `marks` | what a detector said of a piece: level, labels, basis, with the detector that said it | opinion |
| `removed` | what compaction took away: session, range, how many pieces of which kind, why, when | fact |

The first four exist (section 9). The hook does not write here: many short-lived hook
processes writing one database would wait on each other, and rule 1 forbids waiting. The
journal is the hand-over between the two.

### Deleting and compacting

A session that runs for days produces far more than a decision will ever need, and most
of it is repetition. SQLite lets the store shrink as the session goes on. What may be
removed is decided by what a piece is, never by what it says:

| Always kept while the session is retained | Removable, oldest first |
|---|---|
| What the user said | Exact repeats of a piece already held (the sighting is counted) |
| Every call the agent proposed, and what became of it | Member names and other schema text of tool results |
| The piece in which each party first named a path, host or identifier | Tool output that names nothing, is marked by nothing and is tied to no write, beyond a size for each call |
| Any piece a detector ever marked, at any level | Earlier reads of a file that was read again unchanged |
| What stood before and after a write: the request, the content shown, the result | |

- A piece is kept whole or removed whole. Nothing is cut to fit.
- Every removal leaves a row in `removed`, and the session's coverage counts it. A lookup
  that reaches into a compacted range says so.
- The kept classes are what the lookup's ties are made from today. That does not prove
  that compaction removes no evidence: an unmarked result or an observation can matter to
  a later call, as the lookup gaps of 2026-11-13 showed (MR143). So a body that repeats one
  already held is dropped while its occurrence, provenance and place in time stay; every
  deletion and every cap on a search is a coverage gap that the account carries; an index
  is bound to its source, its encoder and its cutoff; and a search that finds nothing in
  a compacted range is never reported as a clean finding.
- An attacker controls lower-trust text and so can make noise, which is removed, or make
  many pieces of a kept class, which fills a quota. Quotas are explicit, and a session
  that hits one is incomplete and is treated as that: undecided, never clear.
- The journal is the record until the store has read it. After that a journal segment is
  kept for a retention period and then deleted. From then on the store is the record of
  that part of the session, and a rebuild from journals reaches only as far back as they
  do. The period is the operator's to set; the default is to keep everything, as today.

## 4. Three ways to search, and what each is allowed to mean

| Search | Finds | In a packet it is |
|---|---|---|
| By name | The same path, host or identifier, exactly | A tie: evidence, with its relation |
| By words | A phrase in a piece's text | A candidate, labelled as found by its words |
| By vector | Text close in meaning to the proposed call or the user's request | A candidate, labelled as found by likeness |

Candidates are capped for each session and each source, are ordered after every tie, and
come only from sessions in the call's scope. A candidate from lower-trust content is shown
as lower-trust content. Vectors are compared only when one encoder made both.

Vector search needs no index structure at this size, and no native code of ours. An exact
scan with NumPy, which the daemon and the monitor already depend on, measured on the
development machine (Apple M5 Max, NumPy 2.5 on Accelerate, random unit vectors, one
query, the ten nearest):

| Vectors | 256 dimensions | 768 dimensions |
|---:|---:|---:|
| 10,000 | 0.04 ms | 0.15 ms |
| 100,000 | 0.8 ms | 2.0 ms |
| 1,000,000 | 9.8 ms | 18.7 ms |

Loading 100,000 vectors of 256 dimensions from SQLite takes 12 ms when they are stored in
blocks of 1,024 and 40 ms as one row each, so the scan runs over a matrix held in memory
and SQLite is where the vectors are kept.

The `sqlite-vector` extension the user asked about was read at its tag 1.2.0. It has been
under Apache-2.0 since tag 1.1.2 (2026-09-10), so its scalar and NEON kernels, about 300
lines, could be copied here with their origin recorded. They are not needed: its
publisher's own figure for an exact scan of a million 768-dimension vectors is 484 ms on
an M5 Pro, and 37.6 ms after quantising to eight bits. Copying them would add a C build,
code to audit (its last two releases fixed seven memory-safety faults), and a second way
to do what NumPy does through the platform's own SIMD library on Apple silicon and on
generic CPUs. So the design uses NumPy, behind one function, and copies nothing. If a
target appears without NumPy, or a measurement shows a need, that function is the one
place to change.

## 5. The encoder

EmbeddingGemma 2 has four jobs here, and a fine-tune is judged on all four against the
unmodified model:

| Job | Query | Document | Used for |
|---|---|---|---|
| Mark | A few descriptions of a pattern | A piece of lower-trust content | Which pieces are suspicious enough to ask Kai about |
| Link | An action the agent took | The user's requests; content the agent read | Whether an action follows the user or follows something it read |
| Select | The proposed call | Pieces of the session | Which lines keep their place when the packet is short |
| Find | The proposed call | Pieces of earlier sessions | Leads that exact ties miss: a renamed file, a destination in other words, a limit phrased indirectly |

What a fit should be trained to do, as a proposal to be revised with the research:

- One asymmetric objective for Select and Find: the proposed call as the query, the
  pieces that bear on it as positives. Supervision comes from authored training campaigns
  whose answer key names those pieces. Development and held-out campaigns train nothing.
- Relevance to the query is annotated, and a decision label is never a relevance label:
  for one and the same call the restriction and its benign counterpart, an approval, are
  both evidence the decision needs (MR143-3). Positives are several for a call: permissions,
  withdrawals, outcomes, links from source to target. Siblings and other valid lines of
  the same campaign are masked, never negatives. A negative is mined only where its
  irrelevance is supported, among candidates already eligible by namespace and session.
- "Bears on" is the label, not "violates": a limit is relevant to the call that keeps it
  and to the call that breaks it. The encoder finds; it does not judge.
- The pattern families stay as a second objective on the same aggregate that is served.
- Truncated dimensions are trained as they will be stored. The fit is anchored to the
  base model on neutral text so that it cannot drift on what it was not trained for. The
  token table stays frozen.
- Adoption only against the base model and against plain full-text search, on: evidence
  kept at short budgets and in long sessions; leads found for reworded references; and
  what Kai then decides. Confirmed once on a set written for it (test 003).

## 6. What the monitor calls

| Need | Call |
|---|---|
| Log an event without waiting | `summarizer.hook.handle` |
| Keep summaries and the store current | `summarizer.daemon.Daemon` |
| The session as a detector reads it | `summarizer.store.load_summary`, `summarizer.text.digest` |
| What earlier sessions hold for a call | `summarizer.history.packet` |
| Leads | `summarizer.index.Index` |

The monitor owns what the core cannot know: which sessions are in a call's scope, the
moment the call was proposed, and whether an answer still applies when it arrives. Its
adapter carries the lookup's account (what was omitted, which sources were unresolved,
which summaries another encoder made, whether history is complete) into the decision
record. An incomplete history never inherits readiness from the current session.

## 7. Nothing twice

| Today there are two | There will be one |
|---|---|
| A Summarizer the monitor installs from another repository, and the tree here | The tree here. The monitor depends on it by path |
| Hook templates under `agents/` and under `src/summarizer/agents/` | Done: `agents/` holds the one generated installation (the gate, in shadow, in the observer's place); the deny-all templates are in `agents/*/fixture`, test only |
| `summarizer doctor` and `agentarmor-monitor doctor` | The monitor's report calls the core's; each fact is read in one place |
| Two test suites with helper modules of the same names | One runner; the helpers are named for their component |
| Two definitions of a completed Kai read (Kai's retention tool and the campaign tool) | One verifier, imported by both |
| A proof-of-concept tool set under `training/summarizer` | The contracts and counters that tests and measurements use; the encoder's tools rebuilt to section 5; the rest removed, each removal listed |

## 8. Order of work

| Step | What | Gate |
|---|---|---|
| 1 | The core in this repository, with the search index | The held-out measurement has finished; reviewer |
| 2 | The monitor depends on the tree here | Kai's board is told first; the rendered requests are byte-identical |
| 3 | The lookup in the local server, in shadow | The adapter's fixtures; real server and client evidence |
| 4 | Candidates by words, then by vector | Campaigns with reworded references show a gap; the licence question is settled |
| 5 | Compaction and retention for long sessions | Tests that evidence survives and that removal is counted |
| 6 | The encoder fit | Accepted data and registration; test 003 |
| 7 | Release qualification | Retention on ToolCall and XPIA; serving identity; rollback |

## 9. What exists today

- The core's package, tests, tools and documents are under `src/summarizer`, copied from
  the proof of concept at `362fd15`. Its history, boards and data stayed there.
- `summarizer.index`: `sessions`, `units`, `names`, `words` and `vectors`, written by the
  daemon when `summarizer serve` runs. The fact tables are rebuilt from journals without a
  model and a rebuild leaves the vectors in place. `summarizer search` by name and by
  words, `summarizer index status`, and `Index.like()` for the exact scan by vector, which
  only compares vectors one encoder made.
- The lookup (`summarizer.history`) is reviewed and measured offline. No server calls it.
- The monitor still installs Summarizer `38e5bc6`. Its tests pass against the tree here.
- Not built: `marks`, `removed`, compaction, retention, and candidates in a packet. No
  search result reaches Kai yet.

## 10. Open decisions

| Decision | Whose | Needed for |
|---|---|---|
| The licence statement for the proof of concept's code, which had no licence file and is under this repository's Apache-2.0 here | User | Step 1 |
| Whether to copy the `sqlite-vector` kernels after all. Its licence allows it; section 4 gives the measurements for not doing so | User | Step 4 |
| The journal's retention period, and so when the store becomes the only record | User | Step 5 |
| What becomes of the proof-of-concept repository | User | After step 2 |
| Whether the package keeps the name `summarizer` | User | Any time; a rename is mechanical |
