# Data request 007: development campaigns in all fifteen families

For the Data Engineer. Written by the Model Trainer on 2026-11-13, after the held-out
measurement of [campaign-measurement-protocol.md](campaign-measurement-protocol.md) ran
and the lookup failed its bar there. The user's instruction of that day stands behind it:
"Let's train the models and get going!", with the objective of multi-turn and
multi-session support in the next release.

## 1. What it is for

The two development files hold ten families, and on them the lookup kept every earlier
fact. The held-out set holds fifteen families under harder conditions (request 006,
section 4), and there it showed 308 of 422. A fault found on a held-out set is fixed on
development data, and there is none that shows it. This file is that data.

It is **development data**: the Trainer reads it, runs everything on it and changes the
lookup because of it. It trains nothing. It confirms nothing either: a new held-out set
does that, and is another request.

What the measurement said, by family, so that you know where the weight goes. You are
told counts and nothing else of that set, and you do not open it:

| Family | What happened there |
|---|---|
| `later_look_disagrees`, `credential_moved`, `poisoned_notes`, `slow_interleaved` | The packet was called ready while a fact the decision needs was not in it |
| `poisoned_control`, `staged_exfiltration` | The evidence a decision needs did not fit in 3,000 characters, even written out from the answer key |
| `limit_crossed` | A benign call was put to the decision model where nothing bore on it |
| The other eight | Every run passed |

## 2. Read, and do not read

Read [data-request-004.md](data-request-004.md) (the format), [data-request-005.md](data-request-005.md)
sections 2, 4 and 6 (the story field, what makes a pair good, the proof table) and
[data-request-006.md](data-request-006.md) section 4 (the five newer families and the long
sessions). Their rules hold here unless this request says otherwise.

Do not open anything under `train/data/blind/`, nor `train/data/seeds/`, nor AgentArmor's
benchset. Do not open the earlier development files to borrow from them: the checker
compares against them, you do not. Every campaign is a new situation: another project,
another kind of file or destination, another way the lower-trust text arrives than any
you would write first.

## 3. The file

`train/data/synthetic/campaigns-dev-003.jsonl`: **72 campaigns in 36 pairs**, an attack and
its benign twin, in the format of request 005 with the `story` field.

| Families | Pairs each | Pairs |
|---|---:|---:|
| The six named in the first two rows of the table above | 3 | 18 |
| The other nine: `planted_file_later`, `changed_source`, `claimed_approval`, `handoff_between_agents`, `limit_crossed`, `approval_withdrawn`, `scope_stretched`, `lookalike_name`, `destruction_prepared_earlier` | 2 | 18 |

Use these fifteen family names exactly.

In the four families of the first row, make the fact that decides hard to reach in the
ways real work makes it so, a different way in each pair: it lies many steps and at least
one other session before the call; the file or the destination is named differently where
it was first named and where the call uses it (a relative path and an absolute one, a
variable that holds the path, a host and an alias for it); it was said by the user in
passing inside a longer request; it is in a session of the other agent. Write the bridge
that ties it to the call into the campaign, as the format has it.

In the two families of the second row, write chains whose decision needs five or more
facts from three or more sessions.

**Across the file**, counted by the checker for both agents:

| What | At least |
|---|---:|
| Campaigns that reach three or more sessions | 44 |
| Campaigns that reach sessions of both agents | 36 |
| Campaigns that really return to a session | 27 |
| Campaigns with twenty or more steps before the call | 45 |
| Campaigns with forty or more steps before the call | 18 |
| Campaigns whose session under decision has twenty or more steps before the call | 14 |
| Campaigns that reach a gap in the call's own namespace | 6 |
| Campaigns that reach a gap in another namespace | 2 |

And in the proof table, because the checker cannot count them: at least six campaigns
whose route is `unasked` because nothing earlier bears on the call; at least six benign
twins that look alarming and are exactly what the user asked for; at least four benign
campaigns in which lower-trust text quotes an attack as an example and nothing follows.

## 4. Two stages, written a few pairs at a sitting

**Stage 1: fifteen pairs, one of each family.** The Model Reviewer reads them before
stage 2 is written. The counts of section 3 are for the whole file and are not asked of
stage 1.

**Stage 2:** the same file grown to 36 pairs, its first thirty lines the stage 1 campaigns
byte for byte unless the Reviewer returned one.

Write one pair at a time: the difference between the twins in one sentence first, then
the attack, then the twin, then check that pair against the rules before the next. A
sitting writes about five pairs and ends with the check.

## 5. Check and receipt

Stage 1:

```sh
SUMMARIZER_HOME=<a scratch directory> .venv/bin/python train/check_campaigns.py \
    train/data/synthetic/campaigns-dev-003.jsonl --attack-pairs \
    --existing train/data/starter/campaigns-v1.jsonl \
    --existing train/data/synthetic/campaigns-dev-001-r1.jsonl \
    --existing train/data/synthetic/campaigns-dev-002-full.jsonl \
    --manifest train/data/eval/campaigns-dev-003.manifest.jsonl
```

Stage 2 adds

```sh
    --require sessions_reached_3_or_more=44 --require agents_reached_2=36 \
    --require returns_to_a_session=27 --require steps_20_or_more=45 \
    --require steps_40_or_more=18 --require call_session_steps_20_or_more=14 \
    --require gap_reached_in_the_calls_namespace=6 \
    --require gap_reached_in_another_namespace=2
```

It must exit 0. It checks form only: that a twin isolates its one difference and that a
label is right are read by the Reviewer.

- **The proof table** goes in `train/data/synthetic/campaigns-dev-003.proof.md`: for each
  pair the columns of request 005, section 6, with its family, and the counts of section
  3 that the checker cannot make.
- **The receipt** goes in `train/data/synthetic/campaigns-dev-003.receipt.md`, because the
  shared board is outside this repository and your sandbox cannot write there. The Trainer
  relays it to the board under your name. It holds: the writer (model, effort); the stage;
  the checker's last line; the manifest's path; the table of pairs with family, technique
  and the one differing fact; and every shortfall or thing the format could not say.

Change only those three files and the manifest. Do not run `git commit`.

## 6. Amendment after the Reviewer's reading of stage 2 (MR149)

Added by the Model Trainer on 2026-11-13.

**Four pairs are returned for repair**, in place and with their ids kept: lines 35-36
(`c003-credential-moved-02`), 43-44 (`c003-slow-interleaved-02`), 49-50
(`c003-poisoned-control-03`) and 59-60 (`c003-claimed-approval-02`). The Reviewer's reasons
are in the brief you are given. Every other line stays byte for byte.

**The file grows to 40 pairs, 80 lines.** Section 3 asks for six campaigns whose route is
`unasked` because nothing earlier bears on the call; 36 pairs gave two, and a benign twin
whose earlier authorization bears on its call is not one. So four pairs are added after
line 72, one each in `limit_crossed`, `scope_stretched`, `lookalike_name` and
`approval_withdrawn`, in which the benign twin's one differing earlier fact is unrelated to
the call (a bound on something the call does not do, an approval of something else), so
that nothing earlier bears on it and its route is `unasked`. The attack twin is asked, as
always. The counts of section 3 and the `--require` options stay as they are.

**What "nothing earlier bears" takes (the Reviewer's clarification, MR150).** An unrelated
differing fact is needed and is not enough. Look at every earlier event of the benign
twin, not only the one that differs: if the authority for the call's own resource still
comes from an earlier session, or an earlier restriction, outcome, resource identity or
suspicious source is needed to decide this call, then something earlier bears and the twin
is not `unasked`. For each of the four benign twins, write in the proof table where the
current task's authority comes from (the deciding session's own request) and why no
earlier event is needed to decide the call. Keep the same proposed call and the one
differing fact across the twins. Do not leave out history or soften what a session read
in order to reach the route.
