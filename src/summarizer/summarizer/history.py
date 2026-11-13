"""What earlier sessions hold for one proposed call. Standard library only.

A summary covers one session. A call can be dangerous, or harmless, for reasons that lie in
another: the file it runs was written last week because a web page said to; the user limited
this very thing in an earlier session. select() finds those things from the summaries of the
other sessions as they stood when the call was proposed, and says what it found, what ties
each thing to the call, and how complete the search was. It selects and never judges: it
does not decide whether a call is an attack, and nothing here raises or lowers a level.

docs/multi-session-contract.md is the contract (sections 4 to 6 and 11). This is its first
part and works from summaries alone: the joins it makes are the ones a summary's entries
can support. It is not yet used by the daemon or by a monitor.

What ties an entry of another session to the call, and only these:

- writer: that session proposed a write to the very path this call runs or reads, or to a
  control file within reach of this call. Whatever became of the write is on its own line:
  a write that failed or was refused is still shown, as that.
- before_write: lower-trust content that session was shown before that write. When, and
  never why.
- shown: what came back for that write; and lower-trust content that source was shown after
  the write which names the written file or is a captured read of it. Later, and said to be
  no more than shown: it is never called a reason for a write it came after.
- request: the request that session was working on when it wrote.
- earlier: what the user said in an earlier session of the call's own project. No claim is
  made that it limits or permits this call: that is for whoever reads the packet.
- said: what the user said in another project, shown only when it names this call's path.
- recent: another call of an earlier session that names what this call names. When it is
  a look at a file this call names, or at a startup file its shell is documented to read,
  what came back is shown with it, as key evidence: what the file held is part of its
  history, and a session that only looked may be the only one that saw it. So is a call
  that names a place this call sends to: what that session asked of the destination, or
  sent there, with what came back. Such a call asks for nothing by itself.

A file the call runs or loads is program text. What an earlier session wrote into it names
the files and the places the call will touch when it runs: a script that holds "curl -T
cache/sample.csv https://intake.example" makes that file and that place this call's. They
are counted with what the call's own words name, for every tie here: a write of that file
is kept with what stands around it, a look at it with what came back, what was seen of
that place, and a bound the user stated on either. One step, and no further. And only as
far as the write was kept: where its text was cut, or it lists as many observables as an
entry can, what else it names is not known, and the search is unfinished.

Most calls hand a file to a program this module does not read the grammar of ("curl -T
out/bundle.txt …", "pkgtool --config config/packages.toml …"). Whether the program reads
the file is not known from its words, so no bridge is claimed. But the call names the file,
and an earlier write of that very file is the nearest thing to its history. Such a write is
kept with what stands around it, as a writer is (the request it was working on, what that
session was shown before it, what came back, later looks at the file), under ties that say
no more than is known: "recent" for the write, "shown" for content, "request" for the
request. The same holds for an earlier write whose own text names a file or a destination
of this call (a record of a claimed approval, say), and for lower-trust content of an earlier
session that names one (who named it first, below the user).

What the user said in an earlier session of the project that states a bound is kept whether
or not it names anything of this call: a limit on another file shows that a limit was
narrower than the call. So is a bound stated in another project on a file that sits there
where a file of this call sits here: the same name in another place, said to be elsewhere
and bearing on nothing. A write of such a file with no bound stated on it is not shown:
another project's file is not this call's. With a statement come the first writes that
session proposed under it, each with what stands around it: what was done under a limit
is how the limit was read at the time. That is context, except under a statement that
bears on the call, where it is key evidence: what that session was told below the user
while the limit stood, and what it then wrote, is what the limit is held against. The
newest few other requests of the project's earlier sessions are context, said earlier and
tied to nothing.

A statement bears on the call, and the call is then put to the decision model whatever its
level, when it states a bound (not merely a task, and not a clause that only points back at
earlier limits) and is tied to the call by a file the call names, by a word of its command,
or by the kind of action the call is: "do not run …" and a command, "never alter …" and a
write. That holds for what the user said in this very session too. The tie is a candidate
and says so by asking: nothing here decides whether the call keeps to what was said. When
such a statement names a thing by an identifier ("until ticket RV-44 is closed"), what a
session of the project observed of that identifier is kept as evidence of the condition.

A write of an earlier session bears on the route when something marks it: a level of its
own, a level on the lower-trust content it is nearest to, or lower-trust content shown
before it that names the file it writes or a place its text names.

A write of an earlier session that fits a wildcard of the call, or a place where a module
the call names could be, is no bridge: whether the call uses that file is not proved. It
is kept as a candidate with what stands around it (the request, what was shown before it,
what came back), in a group of its own, so that whoever decides has the chain and the
doubt together. The search is then unfinished and the route undecided.

A source in a namespace known to be another (a container, another machine) is not searched:
the same path there is another file. A source whose namespace is not known, or one searched
for a call whose own namespace is not known, is searched and said to be unresolved: what
cannot be told apart is not excluded. A searched source whose log has a gap leaves the
history incomplete whether or not anything matched in it, and so does one whose summary
was made by another encoder, other probes or other rules than the call's own session: it
is searched, and it is not indexed for this lookup until it is made again.

What a shell command runs or reads is read from its words (uses()), by a small grammar that
runs nothing. An option is read as what it is: a flag, a plain value, the directory the command
runs in, or a file or module it loads. A command of that grammar whose file cannot be told
from its words is said to be unsupported, and the search is then unfinished: a path that
could not be read is never taken to show that no earlier session wrote it. A module named
and not given by a path is a candidate where it could be in the working directory, and a
write of an earlier session that fits a candidate or a wildcard leaves the search unfinished.

The items are in groups, and a group is kept or left out whole by whoever renders it: the
writes of one file in one source with the request, what was shown before them and what
became of them; or one thing the user said. A group is key evidence, or context: a key
group that finds no room leaves the history incomplete, and context that finds none is
counted and no more.
"""

import fnmatch
import os
import re

from .events import ENTRY_IOCS
from .facts import PERSISTENT, expand, path_classes, shell_segments

WRITES = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit", "apply_patch"})
READS = frozenset({"Read"})
SHELLS = frozenset({"Bash", "exec_command", "shell", "local_shell"})

# The grammar of uses(). A shell or an interpreter runs its first operand; a reader reads
# every operand; a command word with a slash in it is the file that is run. Nothing else
# in a command is said to run or read a file: its paths are only named.
_SHELL_NAMES = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
_RUNNERS = _SHELL_NAMES | {"source", ".", "python", "node", "ruby", "perl"}
_READERS = frozenset({"cat", "less", "more", "head", "tail"})
# Words that stand before the command word. For each: the options that take the next word
# as a plain value, those that name the directory the command then runs in, and its flags.
# An option in none of the three may take the next word or not, and may change what runs:
# where the command word is cannot then be told, and that is said.
_WRAPPERS = {
    "sudo": (
        "-u -g -h -p -C -R -T -U -r -t --user --group --host --prompt --role --type "
        "--close-from --other-user --command-timeout",
        "-D --chdir",
        "-A -b -E -H -K -k -n -P -S --askpass --background --preserve-env --set-home "
        "--reset-timestamp --non-interactive --preserve-groups --stdin",
    ),
    "doas": ("-u -C", "", "-n"),
    "env": ("-u --unset", "-C --chdir", "-i -0 -v --ignore-environment --null --debug"),
    "time": ("-o -f --output --format", "", "-p -v -a --portability --verbose --append"),
    "xargs": (
        "-n -P -I -L -s -d -E -a --max-args --max-procs --max-chars --delimiter --arg-file "
        "--replace --max-lines --eof",
        "",
        "-0 -r -t -x --null --no-run-if-empty --verbose --exit",
    ),
    "exec": ("-a", "", "-c -l"),
    "nohup": ("", "", ""),
    "command": ("", "", "-p"),
}
_WRAPPERS = {
    name: tuple(frozenset(options.split()) for options in kinds)
    for name, kinds in _WRAPPERS.items()
}
# Options that take the next word as their value. A shell's -o and +o are read in _scan.
_VALUED = {
    "bash": "-O +O",
    "zsh": "--emulate",
    "python": "-W -X --check-hash-based-pycs",
    "node": "-C --conditions",
    "ruby": "-I -E -F",
    "perl": "-I",
    "head": "-n -c --lines --bytes",
    "tail": "-n -c --lines --bytes -s --sleep-interval --pid --max-unchanged-stats",
    "less": "-b -h -j -k -o -O -p -P -t -T -x -y -z",
    "more": "-n",
}
_VALUED = {name: frozenset(options.split()) for name, options in _VALUED.items()}
# Options whose value is a file the program loads beside its operand, with the use the call
# makes of it; and options that name the directory the program runs in. Neither is a plain
# value: the first names a file an earlier session may have written, the second changes
# which file a relative operand is.
_LOADS = {
    "bash": {"--rcfile": "run", "--init-file": "run"},
    "node": {
        "-r": "run",
        "--require": "run",
        "--import": "run",
        "--loader": "run",
        "--experimental-loader": "run",
        "--env-file": "read",
    },
    "ruby": {"-r": "run"},
}
_CHDIR = {"ruby": frozenset({"-C", "-X"})}
_FILES = frozenset({"--rcfile", "--init-file", "--env-file"})  # their value is always a file
# The variable each program takes more places to look for a module from.
_SEARCH = {"python": ("PYTHONPATH",), "node": ("NODE_PATH",), "ruby": ("RUBYLIB",)}
_BY_PATH = ("./", "../", "/", "~")  # how a module named to node or ruby is a file, not a name
# Options after which the program is given in the call itself, in a language not read here.
_INLINE = {"python": "-c", "node": "-e --eval -p --print", "ruby": "-e", "perl": "-e -E"}
_INLINE = {name: frozenset(options.split()) for name, options in _INLINE.items()}
# Single letters that take no value, alone or in a cluster. None: any letters.
_FLAGS = {
    "python": "bBdEhiIOPqRsSuvVx",
    "node": "cihv",
    "ruby": "adhlnpsSvwWxy",
    "perl": "acnpsStTuUvwWX",
    "head": "qvz",
    "tail": "fFqvrz",
    "cat": None,
    "less": None,
    "more": None,
}
# Long options that take no value. One that is not listed and has no "=" may take the next
# word, so what follows it cannot be told. None: every long option of that program is a flag
# (zsh's are its option names, and the one that takes a value is listed above).
_LONG_FLAGS = {
    "bash": "--login --norc --noprofile --posix --restricted --verbose --debugger --noediting",
    "zsh": None,
    "python": "--version --help",
    "node": "--version --help --check --interactive --watch --test --use-strict "
    "--abort-on-uncaught-exception --pending-deprecation --throw-deprecation "
    "--preserve-symlinks --preserve-symlinks-main",
    "ruby": "--version --verbose --debug --yydebug --copyright --help",
    "perl": "--version --help",
    "cat": "--number --number-nonblank --show-all --show-ends --show-tabs --show-nonprinting "
    "--squeeze-blank",
    "head": "--quiet --silent --verbose --zero-terminated",
    "tail": "--follow --retry --quiet --silent --verbose --zero-terminated",
    "less": "--help --version --quit-if-one-screen --no-init --raw-control-chars "
    "--RAW-CONTROL-CHARS --chop-long-lines --line-numbers --LINE-NUMBERS --ignore-case "
    "--IGNORE-CASE --squeeze-blank-lines",
    "more": "--help --version --silent --logical --no-pause --print-over --clean-print "
    "--squeeze --plain",
}
_LONG_FLAGS = {
    name: None if options is None else frozenset(options.split())
    for name, options in _LONG_FLAGS.items()
}
# node's switches by their first words: none of these takes a separate value.
_LONG_STARTS = {"node": ("--no-", "--trace-", "--inspect", "--harmony", "--expose-", "--enable-")}
_PERL_ATTACHED = "IMmFilx0CdD"  # perl options whose value is written onto them
_PLUS = frozenset({"head", "tail", "less", "more"})  # "+5", "+G" are options there
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_PYTHON = re.compile(r"python[0-9.]*\Z")
_NESTED = 2  # how deep a shell's -c '<command>' is followed
CLIPPED = "…"  # how events.clip ends a call's text that it cut

# The one invocation whose startup files are documented well enough to expect a load: see
# docs/multi-session-contract.md, section 4. Anything else is a possible load.
_STARTUP = re.compile(r"zsh -(?P<options>[il]{0,2})c '[^']*'\Z")
# Words by which a user states a limit, a condition or a permission (contract section 11).
# They make a statement a candidate; they never decide what it means.
_BOUNDS = re.compile(
    r"\b(?:only|never|do not|don't|must not|may not|until|unless|except|without|no more than|"
    r"at most|not allowed|allowed|approved?|permission|permitted|forbidden|frozen|freeze|"
    r"keep|leave|limit(?:ed)?|restrict(?:ed)?)\b",
    re.I,
)
# A clause that only points back at earlier limits states none of its own: those limits are
# what the lookup finds, and "without waiving any earlier restriction" adds nothing to them.
_REFERS = re.compile(r"\b(?:without|not) waiving\b[^.;,]*", re.I)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{4,}")
# The kinds of action a statement can speak of and a call can be (contract section 11).
_KINDS = (
    ("run", r"run(?:s|ning)?|ran|execut(?:e|es|ed|ing)|invok(?:e|es|ed|ing)|launch(?:es|ed|ing)?"),
    ("write", r"writ(?:e|es|ing|ten)|edit(?:s|ed|ing)?|chang(?:e|es|ed|ing)|modif(?:y|ies|ied|ying)|"  # noqa: E501
     r"alter(?:s|ed|ing)?|updat(?:e|es|ed|ing)|overwrit(?:e|es|ing|ten)|read[- ]only"),  # noqa: E501
    ("delete", r"delet(?:e|es|ed|ing)|remov(?:e|es|ed|ing)|wip(?:e|es|ed|ing)|eras(?:e|es|ed|ing)"),  # noqa: E501
    ("send", r"send(?:s|ing)?|sent|upload(?:s|ed|ing)?|post(?:s|ed|ing)?|shar(?:e|es|ed|ing)|"
     r"export(?:s|ed|ing)?|email(?:s|ed|ing)?|notif(?:y|ies|ied|ying)"),
    ("publish", r"publish(?:es|ed|ing)?|deploy(?:s|ed|ing|ment)?|releas(?:e|es|ed|ing)"),
    ("install", r"install(?:s|ed|ing)?"),
)  # fmt: skip
_KINDS = tuple((name, re.compile(rf"\b(?:{words})\b", re.I)) for name, words in _KINDS)
# What a command's own words show of its kind, beside being a run: its program's name.
_COMMAND_KINDS = (
    ("delete", re.compile(r"\b(?:rm|rmdir|unlink|shred)\b")),
    (
        "send",
        re.compile(
            r"\b(?:scp|sftp|rsync|sendmail|notify)\b"
            r"|\bcurl\b.*\s(?:-T|-d|-F|--data|--upload-file)"
        ),
    ),
    ("publish", re.compile(r"\b(?:publish|deploy|release)[\w-]*", re.I)),
    ("install", re.compile(r"\b(?:pip|npm|brew|apt|apt-get|gem|cargo)\s+(?:install|add)\b")),
)  # fmt: skip
# A name that identifies one thing: letters and digits together ("RV-44", "Q7"). A condition
# the user states often names one, and what was observed of it elsewhere is its evidence.
_TOKEN = re.compile(r"(?<![\w.-])(?=[\w-]*[A-Za-z])(?=[\w-]*\d)\w[\w-]*(?![\w-])")
_PATHISH = re.compile(r"[\w.~{}/-]*[/.][\w.~{}/-]*")
LEVELS = {"none": 0, "low": 1, "medium": 2, "high": 3}
LOADS = ("load", "may_load")
PLACES = ("domain", "url", "ip", "email")  # observables that are somewhere a call can send to
# Labels content gets from the calls that followed it, not from what it says.
_FROM_CALLS = frozenset({"action_source", "destination_source"})
UNDER = 3  # writes shown of what a session did under a bound the user stated
EARLIER = 3  # other requests of the project's earlier sessions shown as context, the newest


def _program(word):
    """The name a command word is looked up by: its basename, any python as python."""
    name = os.path.basename(word)
    if _PYTHON.match(name):
        return "python"
    return "node" if name == "nodejs" else name


def _command(words):
    """(where the command word of one simple command is, whether xargs supplies its
    operands, the directories its wrappers move to in order, why that cannot be told or
    None, what its own words do to its environment).

    The last is {"set": {name: value}, "unset": {names}, "cleared": bool}: the assignments
    before the command word, and what env takes away. Where a program looks for what it
    loads by name can be set there (PYTHONPATH), so an assignment is read and not skipped.

    Assignments, wrappers such as sudo and env, and the wrappers' own options come before
    the command word. A wrapper's option is a plain value, a directory to run in, or a flag;
    one that is none of these is not guessed at. Each directory is taken from where the one
    before it led ("env -C a env -C b" runs in a/b), so they are kept in order, all of them.
    """
    at, wrapper, supplied, chdir = 0, None, False, []
    around = {"set": {}, "unset": set(), "cleared": False}
    while at < len(words):
        word = words[at]
        name = os.path.basename(word)
        if wrapper == "env" and word in ("-i", "--ignore-environment"):
            around["set"].clear()
            around["cleared"], at = True, at + 1
        elif wrapper == "env" and (word in ("-u", "--unset") or word.startswith("--unset=")):
            gone = word.partition("=")[2] or "".join(words[at + 1 : at + 2])
            around["set"].pop(gone, None)
            around["unset"].add(gone)
            at += 1 if "=" in word else 2
        elif wrapper is not None and word == "--":
            wrapper, at = None, at + 1
        elif wrapper is not None and word.startswith("-") and len(word) > 1:
            valued, moves, flags = _WRAPPERS[wrapper]
            option, written, value = word.partition("=") if word[:2] == "--" else (word, "", "")
            short = word[:2] if word[:2] != "--" and len(word) > 2 else None
            if option in moves or short in moves:
                attached = value if written else word[2:] if short in moves else None
                # (the directory, whether it is a word of its own: one written onto its
                # option is not a word the shell expands a tilde at the start of)
                separate = "".join(words[at + 1 : at + 2])
                chdir.append((separate, True) if attached is None else (attached, False))
                at += 1 if attached is not None else 2
            elif option in valued:
                at += 1 if written else 2
            elif short in valued:
                at += 1  # the value is written onto the option: -uroot
            elif word in flags or (
                word[:2] != "--" and all(f"-{letter}" in flags for letter in word[1:])
            ):
                at += 1
            else:
                return None, supplied, chdir, "unknown_option", around
        elif name in _WRAPPERS:
            wrapper, at = name, at + 1
            supplied = supplied or name == "xargs"
        elif _ASSIGNMENT.match(word):
            variable, _, value = word.partition("=")
            around["set"][variable] = value
            around["unset"].discard(variable)
            at += 1
        else:
            return at, supplied, chdir, None, around
    return None, supplied, chdir, None, around


def _setting(variable, around, shell):
    """(the value a command's own words give a variable, whether that is known).

    The command's own assignment wins; env -u and env -i take the variable away. Else it
    is what the line's shell holds for it (shell: its variables, which of them it exported,
    which it unset). A variable the line set and exported reaches the command with its
    latest value, also when it was exported first and assigned again after. One the line
    unset does not reach it. One it set without exporting, or exported without setting,
    reaches the command or not, or with a value from somewhere else: that is not known.
    """
    if variable in around["set"]:
        return around["set"][variable], True
    if variable in around["unset"] or around["cleared"] or variable in shell["unset"]:
        return None, True
    held, exported = variable in shell["set"], variable in shell["exported"]
    if held and exported:
        return shell["set"][variable], True
    return None, not (held or exported)


def _scan(name, rest, every):
    """What follows a command word of the grammar, as

        {"operands": [...], "inline": a shell's own command or None,
         "loads": [(use, word, bare)], "candidates": [words],
         "chdir": [(word, bare), in order],
         "by_name": [modules named and not given by a path],
         "ignores_environment", "no_working_directory": what python was told of its search,
         "problem": why none of it can be told, or None}

    A runner's first operand is the file it runs and the rest are that file's own
    arguments; a reader's operands are all files (every). An option is a flag, a plain
    value, a directory to run in (chdir), or a file or module to load (loads, and for a
    module named and not given by a path, candidates: where in the working directory it
    could be). One this does not know may or may not take the next word, so what follows
    it cannot be told: that is said, and no operand is guessed.
    """
    told = {"operands": [], "inline": None, "loads": [], "candidates": [], "chdir": []}
    told |= {"by_name": [], "ignores_environment": False, "no_working_directory": False}
    told["problem"] = None
    if name in ("source", "."):
        return told | {"operands": rest[:1]}
    shell = name in _SHELL_NAMES
    valued, inline, flags = _VALUED.get(name, ()), _INLINE.get(name, ()), _FLAGS.get(name)
    loads, moves = _LOADS.get(name, {}), _CHDIR.get(name, ())
    library, at = ["."], 0  # where ruby looks for what -r names, as -I adds to it
    switches = set()  # the letters read as flags so far, and nothing read as a value

    def given(option, value, bare):
        # bare: the value is a word of its own, where the shell expands a leading tilde.
        if value is None:
            return
        if option in moves:
            told["chdir"].append((value, bare))
        elif option in loads and (option in _FILES or value.startswith(_BY_PATH)):
            told["loads"].append((loads[option], value, bare))
        elif option in loads and name == "node":
            told["candidates"] += [f"node_modules/{value}", f"node_modules/{value}.js"]
            told["candidates"].append(f"node_modules/{value}/*")
            told["by_name"].append(value)
        elif option in loads and name == "ruby":
            told["candidates"] += [f"{folder}/{value}.rb" for folder in library]
            told["candidates"].append(f"lib/{value}.rb")
            told["by_name"].append(value)
        elif option == "-I" and name == "ruby":
            library.append(value)

    while at < len(rest):
        word = rest[at]
        plus = word.startswith("+") and (shell or name in _PLUS)
        option, written, value = word.partition("=") if word[:2] == "--" else (word, "", "")
        short = word[:2] if word[:2] != "--" and len(word) > 2 and not shell else None
        takes = option in valued or option in loads or option in moves
        if word == "--":
            told["operands"] += rest[at + 1 :] if every else rest[at + 1 : at + 2]
            break
        if not ((word.startswith("-") or plus) and len(word) > 1):
            told["operands"].append(word)
            if not every:
                break
            at += 1
        elif takes:
            given(option, value if written else "".join(rest[at + 1 : at + 2]) or None, not written)
            at += 1 if written else 2
        elif short in valued or short in loads or short in moves:
            given(short, word[2:], False)  # the value is written onto the option: -Wignore
            at += 1
        elif name == "python" and word == "-m":
            # A module named and not given by a path: where it could be under the working
            # directory. Running a.b.c imports the packages above it first, so their
            # initializers run as well, and c may itself be a package. These are places it
            # could be here and no more: where else python looks is not in the words, and
            # the module is said to be named and not found (by_name).
            # -E and -I make python ignore PYTHONPATH, and -I and -P keep the working
            # directory out of where it looks: a place that is switched off is no candidate.
            # They are the flags read so far as flags: a letter inside the value of
            # another option ("-Wignore::ImportWarning") is no flag.
            told["ignores_environment"] = bool(switches & set("EI"))
            told["no_working_directory"] = bool(switches & set("IP"))
            parts = [part for part in "".join(rest[at + 1 : at + 2]).split(".") if part]
            for depth in range(1, len(parts)):
                told["candidates"].append("/".join(parts[:depth]) + "/__init__.py")
            if parts:
                module = "/".join(parts)
                told["candidates"] += [f"{module}.py", f"{module}/__init__.py"]
                told["candidates"].append(f"{module}/__main__.py")
                told["by_name"].append(".".join(parts))
            break
        elif word in inline:
            break  # code given in the call itself: no file of its own
        elif word.startswith("--"):
            known = _LONG_FLAGS.get(name, frozenset())
            flag = known is None or word in known or word.startswith(_LONG_STARTS.get(name, ()))
            if not written and not flag:
                return told | {"problem": "unknown_option"}
            at += 1  # a long flag, or an option with its value written onto it
        elif shell:
            letters = word[1:]
            if not letters.isalpha():
                return told | {"problem": "unknown_option"}
            if word[0] == "-" and "c" in letters:
                return told | {"inline": "".join(rest[at + 1 : at + 2])}
            if word[0] == "-" and "s" in letters:
                break  # commands come from standard input
            at += 2 if letters.endswith("o") else 1
        elif flags is None or all(letter in flags for letter in word[1:]):
            switches |= set(word[1:])
            at += 1
        elif name in ("head", "tail") and word[1:].isdigit():
            at += 1
        elif name == "perl" and word[1] in _PERL_ATTACHED:
            at += 1
        else:
            return told | {"problem": "unknown_option"}
    return told


def _shell(text, base, home, found, depth=0, moved=False):
    """Add to found what a shell command runs and reads, by its words alone.

    base is the directory a relative path is read from, or None once a cd went somewhere
    the words do not say. moved says a cd came before: inside parentheses or backquotes it
    may not hold for what follows, and a relative path is then not resolved.
    """

    def note(reason):
        if reason not in found["unsupported"]:
            found["unsupported"].append(reason)

    def rooted(word, bare=True):
        """Whether a word names a place by itself: an absolute path, or one under the home
        directory. A tilde is that only where the shell expands it: at the start of a word
        of its own (bare) that is not quoted (see tilde below)."""
        return word.startswith("/") or (bare and word.startswith("~") and tilde == "home")

    def resolve(word, where, bare=True):
        return expand(word, where or "/", home if bare and tilde == "home" else "")

    def into(target, where, bare=True):
        """The directory target names, seen from where; None when the words do not say."""
        literal = target != "-" and not set(target) & set("$`{}*?[")
        if bare and target.startswith("~") and tilde == "unclear":
            return None
        known = literal and (where is not None or rooted(target, bare))
        return resolve(target, where, bare) if known else None

    def place(use, word, where, shifted, doubtful, bare=True):
        if word == "-":
            return
        if set(word) & set("$`{}"):
            return note("operand_not_literal")
        if bare and word.startswith("~") and tilde == "unclear":
            return note("tilde_quoting_unclear")
        relative = not rooted(word, bare)
        if relative and shifted and where is None:
            return note("directory_unknown")
        if relative and doubtful:
            return note("directory_uncertain")
        path = resolve(word, where, bare)
        if use == "candidate" or set(word) & set("*?["):
            found["patterns"].append(path)
        elif path not in found[use]:
            found[use].append(path)

    if text.endswith(CLIPPED):
        note("call_clipped")
    nested = any(mark in text for mark in "()`")
    redirected = "<" in text.replace("<<", "")
    segments = list(shell_segments(text, split_assignments=False, redirections=False))
    # A word taken out of its quotes is not a word the shell has expanded: a tilde is the
    # home directory only where it begins an unquoted word. The words here have lost their
    # quotes, so the command's own text is asked: when every word that begins with a tilde
    # is quoted or escaped there, a tilde is a tilde and the path is under the working
    # directory; when none is, it is the home directory; when only some are, which is
    # which cannot be told from the words, and that is said.
    begins = sum(1 for words in segments for word in words if word.startswith("~"))
    held = len(re.findall(r"""['"\\]~""", text))
    tilde = "home" if not held else "literal" if held == begins else "unclear"
    if re.search(r"""(?:^|[\s;|&(])~['"\\]""", text):  # an unquoted tilde, then a quote
        tilde = "unclear"  # quoted between the tilde and its first slash: it stays a tilde
    # What the line's own shell holds as it goes: the variables it set with their latest
    # values, which of them it exported, and which it unset.
    shell = {"set": {}, "exported": set(), "unset": set()}
    for words in segments:
        at, supplied, chdir, problem, around = _command(words)
        if problem:  # where this command's own word is cannot be told
            note(problem)
            continue
        if at is None:  # assignments and no command: the shell's own variables
            shell["set"] |= around["set"]
            shell["unset"] -= set(around["set"])
            continue
        name, rest = _program(words[at]), words[at + 1 :]
        if name in ("export", "unset"):
            for word in (word for word in rest if not word.startswith("-")):
                variable, assigned, value = word.partition("=")
                if name == "unset":
                    shell["set"].pop(variable, None)
                    shell["exported"].discard(variable)
                    shell["unset"].add(variable)
                    continue
                shell["exported"].add(variable)
                shell["unset"].discard(variable)
                if assigned:
                    shell["set"][variable] = value
            continue
        if name == "cd":
            target = next((word for word in rest if word == "-" or word[:1] != "-"), "~")
            base, moved = into(target, base), True
            continue
        # A directory a wrapper names holds for this one command, and holds for sure; a cd
        # that came before holds unless a parenthesis may have ended it. Each one is taken
        # from the directory reached so far, and one the words do not say leaves it unknown.
        here, shifted, doubtful = base, moved, moved and nested

        def move(targets):
            nonlocal here, shifted, doubtful
            for target, bare in targets:
                reached = here is not None or rooted(target, bare)
                here = into(target, here, bare) if reached else None
                shifted, doubtful = True, False

        move(chdir)
        if name in _RUNNERS or name in _READERS:
            every = name in _READERS
            told = _scan(name, rest, every)
            if told["problem"]:
                note(told["problem"])
                continue
            move(told["chdir"])
            for use, word, bare in told["loads"]:
                place(use, word, here, shifted, doubtful, bare)
            if told["by_name"]:
                # Where a module named and not given by a path is looked for, as far as the
                # command's own words say: the directory it runs in, and for python the
                # places PYTHONPATH names, each unless python was told to leave it out.
                # A place the words name and this cannot read leaves the search unfinished.
                roots = [] if told["no_working_directory"] else [(here, shifted, doubtful)]
                for variable in _SEARCH.get(name, ()):
                    value, known = _setting(variable, around, shell)
                    if not known:
                        note("search_path_unclear")
                    if value is None or (name == "python" and told["ignores_environment"]):
                        continue
                    if name != "python":
                        note("search_path_unsupported")
                        continue
                    for entry in filter(None, value.split(":")):
                        if set(entry) & set("$`{}*?["):
                            note("search_path_not_literal")
                            continue
                        reached = here is not None or rooted(entry, False)
                        roots.append((into(entry, here, False) if reached else None, True, False))
                for where, turned, unsure in roots:
                    for word in told["candidates"]:
                        place("candidate", word, where, turned, unsure)
                searched = [where for where, _, _ in roots if where is not None]
                for module in told["by_name"]:  # named, and looked for in these places only
                    found["by_name"].append({"module": module, "searched": searched})
            if told["inline"] is not None:
                if depth < _NESTED:
                    _shell(told["inline"], here, home, found, depth + 1, shifted)
                else:
                    note("nested_command")
            elif told["operands"]:
                for word in told["operands"]:
                    place("read" if every else "run", word, here, shifted, doubtful)
            elif supplied:
                note("operands_from_input")
            elif redirected:
                note("input_redirected")
        elif "/" in words[at]:
            place("run", words[at], here, shifted, doubtful)


def uses(entry, cwd, home):
    """What a call's own words show of the files it uses:

        {"run": [paths], "read": [paths], "write": path or None, "named": {paths},
         "patterns": [paths with a wildcard that it runs or reads, and the places under the
                      working directory where a module it names could be],
         "by_name": [{"module", "searched": [the places its words say it is looked for]}:
                     where else a module loaded by name is looked for is unknown],
         "unsupported": [why what it runs or reads could not be told, if it could not]}

    Only a file tool and the grammar at the top of this module say run, read or write.
    Every other path in the call is named and no more.
    """
    tool, target = entry.get("tool"), str(entry.get("target") or "")
    found = {"run": [], "read": [], "write": None, "named": set()}
    found |= {"patterns": [], "unsupported": [], "by_name": []}
    if tool in WRITES:
        found["write"] = expand(target, cwd, home)
    elif tool in READS:
        found["read"].append(expand(target, cwd, home))
    elif tool in SHELLS:
        _shell(target, cwd, home, found)
    for piece in _PATHISH.findall(target):
        piece = piece.strip("'\"`.,;:()")
        if "/" in piece or "." in piece.strip("."):
            found["named"].add(expand(piece, cwd, home))
    found["named"] |= {*found["run"], *found["read"]} | ({found["write"]} - {None})
    return found


def reaches(path, cwd, home):
    """Whether a written control file could govern a command run in cwd, by where it is."""
    if not path_classes(path, cwd, home) & PERSISTENT:
        return False
    folder = os.path.dirname(path)
    everywhere = folder == home or folder.startswith((f"{home}/.claude", f"{home}/.codex"))
    return everywhere or path.startswith(cwd + "/")


def _use(path, mine_uses, command, call, cwd, home):
    """How the call uses a file, as far as its own words show: it runs it, it reads it, or
    it is a control file within reach, which its shell loads where zsh documents that this
    very invocation reads it, and may load otherwise. None when its words show none."""
    if path in mine_uses["run"]:
        return "run"
    if path in mine_uses["read"]:
        return "read"
    if call.get("tool") in SHELLS and reaches(path, cwd, home):
        return "load" if startup_expected(command, path, home) else "may_load"
    return None


def startup_expected(command, path, home):
    """Whether zsh documents that this exact invocation reads that startup file."""
    started = _STARTUP.match(command)
    if not started or os.path.dirname(path) != home:
        return False
    options = started.group("options")
    if len(set(options)) != len(options):
        return False
    login, interactive = "l" in options, "i" in options
    reads = {".zshenv": True, ".zprofile": login, ".zlogin": login, ".zshrc": interactive}
    return bool(reads.get(os.path.basename(path)))


def _bound(prompt):
    """Whether what the user said states a limit, a condition or a permission, by its words
    or by the notes the request carries."""
    said = str(prompt.get("text") or "")
    text = _REFERS.sub(" ", said)
    # The notes a request carries are evidence of a bound, except where the request only
    # points back at earlier limits: the note is then of that clause, which states none.
    return bool(_BOUNDS.search(text) or (prompt.get("notes") and text == said))


def _words(text):
    """The words of a text that can tie it to another: five letters or more, a compound
    ("release-tool", "inventory-q7.txt") also by its parts, without the stop at its end."""
    found = set()
    for word in _WORD.findall(text):
        word = word.strip(".-_").lower()
        found |= {piece for piece in (word, *re.split(r"[-_.]", word)) if len(piece) >= 5}
    return found


def _kinds_said(text):
    return {name for name, pattern in _KINDS if pattern.search(text)}


def _kinds_of(call, command):
    """The kinds of action a call is: by its tool, and for a command by its own words."""
    tool = call.get("tool")
    if tool in WRITES:
        return {"write"}
    if tool not in SHELLS:
        return set()
    return {"run"} | {name for name, pattern in _COMMAND_KINDS if pattern.search(command)}


def _tokens(entry):
    """The identifiers an entry's text names: letters and digits together."""
    return set(_TOKEN.findall(str(entry.get("text") or "")))


def _bears(prompt, call_text, named, cwd, home, kinds=()):
    """Whether a statement of the user states a bound and is tied to the call: it names one
    of the call's paths, a word of its command, or a kind of action the call is. A
    candidate link, said to be no more: it asks for review and settles nothing."""
    text = str(prompt.get("text") or "")
    if not _bound(prompt):
        return False
    if _kinds_said(_REFERS.sub(" ", text)) & set(kinds):
        return True
    said = set()
    for piece in _PATHISH.findall(text):
        piece = piece.strip("'\"`.,;:()")
        if piece:
            said.add(expand(piece, cwd, home))
    if said & named:
        return True
    return bool(_words(call_text) & _words(text))


def _made(summary):
    """What a summary says made it: the encoder with its probes, and what marks entries."""
    session = summary["session"]
    return session.get("embedding"), session.get("detector")


def _known(namespace):
    """Whether a namespace is one that can be told from another: None and "" are not."""
    return namespace not in (None, "")


def select(mine, call_id, others, home):
    """What the other sessions hold for the call call_id of the session mine.

    mine and each of others is {"key", "summary", "cwd", "namespace"}; others are the
    sessions as they stood when the call was proposed, oldest first. Returns

        {"sources": [key, ...],              every source something is shown from
         "items": [{"key", "seq", "type", "relation", "use", "of", "bears", "groups"}, ...],
         "groups": [{"id", "kind", "key", "bears"}, ...],
         "bridges": [{"key", "seq", "resource", "use", "bears"}, ...],
         "candidates": [{"key", "seq", "resource", "bears"}, ...],   writes of a file the
                                             call may use: no bridge, and shown all the same
         "why": [...],                       why the history is incomplete, or empty
         "unsupported": [...],               why what the call uses could not be told
         "by_name": [{"module", "searched"}],  modules the call loads by name, and the
                                             places they were looked for: no others
         "unresolved": {key: reason},        sources searched without knowing they are near
         "searched": [...], "excluded": {key: reason},
         "route": "asked" | "unasked" | "undecided", "route_reason": ...}

    Items are in the order they should be shown: by source, then by event. Each is in one
    or more groups. A group of kind "writer" or "statement" is key evidence: it is shown
    whole or it is left out and said to be, and what came back for a write is part of its
    group whatever the write's status. A group of kind "recent" is context. Nothing is cut
    to a budget here.
    """
    entries = mine["summary"]["entries"]
    call = next(e for e in entries if e.get("call_id") == call_id and e["type"] == "action")
    cwd, namespace = mine["cwd"], mine["namespace"]
    mine_uses = uses(call, cwd, home)
    command = str(call.get("target") or "")
    items, bridges, groups, why, excluded, searched, candidates = [], [], [], [], {}, [], []
    unresolved, unsupported, sources = {}, list(mine_uses["unsupported"]), []
    kinds, shown_in, tokens, plain = _kinds_of(call, command), {}, set(), []
    for other in others:
        near = _known(namespace) and _known(other["namespace"])
        if near and other["namespace"] != namespace:
            excluded[other["key"]] = "other_namespace"
            continue
        searched.append(other["key"])
        if not near:  # eligible as the call's own namespace, and said not to be known
            unresolved[other["key"]] = "namespace_unknown"
        actions = (e for e in other["summary"]["entries"] if e["type"] == "action")
        sources.append((other, [(e, uses(e, other["cwd"], home)) for e in actions]))
    # One step on. A file the call runs or loads is program text, and what an earlier
    # session wrote into it names the files and the places the call will touch when it
    # runs: "curl -T cache/sample.csv https://intake.example" in a script is this call's
    # file and this call's destination. They are counted with what the call's own words
    # name, for every tie below. One step and no further, and only as far as the write
    # was kept: where its text was cut, or it lists as many observables as an entry can,
    # what else it names is not known, and the search is said to be unfinished.
    places = _places(call)
    for _, acted in sources:
        for entry, theirs in acted:
            path = theirs["write"]
            if path and _use(path, mine_uses, command, call, cwd, home) in ("run", *LOADS):
                mine_uses["named"] |= _said(entry, cwd, home) - {path}
                places |= _places(entry)
                cut = "text_chars" in entry or len(entry.get("iocs") or ()) >= ENTRY_IOCS
                if cut and "program_text_cut" not in unsupported:
                    unsupported.append("program_text_cut")
    # What the user said in this very session that states a bound the call can be held
    # against. It is in the session's own packet already; here it decides the route.
    stated = [
        e
        for e in entries
        if e["type"] == "prompt"
        and e["seq"] < call["seq"]
        and _bears(e, command, mine_uses["named"], cwd, home, kinds)
    ]
    for prompt in stated:
        tokens |= _tokens(prompt)
    for other, acted in sources:
        summary, there = other["summary"], other["cwd"]
        if not summary["session"]["coverage"]["scanned_all_captured"]:
            why.append("capture_gap")
        # A summary another encoder, other probes or other rules made is searched all the
        # same: what it holds is still what that session did. But its marks and links are
        # not this session's kind, and what bears on the route is read off them. Until it
        # is made again it is not indexed for this lookup, and the history says so.
        if _made(summary) != _made(mine["summary"]):
            why.append("not_indexed")
        found = _from(other, acted, mine_uses, places, command, call, cwd, home)
        items += found["items"]
        bridges += found["bridges"]
        groups += found["groups"]
        if found["pattern"] and "candidate_matches_a_write" not in unsupported:
            unsupported.append("candidate_matches_a_write")
        candidates += found["candidates"]
        same_project = there == cwd
        taken = {(item["seq"], item["type"]): item for item in found["items"]}
        shown_in[other["key"]] = taken
        for prompt in (e for e in summary["entries"] if e["type"] == "prompt"):
            named = _bears(prompt, command, mine_uses["named"], there, home, kinds)
            if same_project and named:
                tokens |= _tokens(prompt)
            shown = taken.get((prompt["seq"], "prompt"))
            if shown is not None:
                # Already there as the request of a write. When it also states a bound on
                # what this call names it bears on the route, as it would by itself.
                if same_project and named:
                    shown["bears"] = True
                continue
            group = {"id": f"{other['key']}:said:{prompt['seq']}", "kind": "statement"}
            if same_project and named:
                items.append(_item(other, prompt, "earlier", bears=True, groups=[group["id"]]))
                groups.append(group | {"key": True, "bears": True})
            elif same_project and _bound(prompt):
                # A bound on something else. Nothing is claimed of what it is about; it
                # can show that a limit or a permission was narrower than this call.
                items.append(_item(other, prompt, "earlier", groups=[group["id"]]))
                groups.append(group | {"key": False, "bears": False})
            elif not same_project and (
                _names(prompt, mine_uses["named"], there, home)
                or (_bound(prompt) and _alike(prompt, there, mine_uses["named"], cwd, home))
            ):
                # Said in another project of this call's very path, or a bound stated there
                # on a file that sits where a file of this call sits here. The second is
                # another file. It is shown as what was said elsewhere, and bears on nothing:
                # whether that project is this one is for whoever decides.
                items.append(_item(other, prompt, "said", groups=[group["id"]]))
                groups.append(group | {"key": True, "bears": False})
            else:
                if same_project:  # said earlier in this project, of something else
                    plain.append((other, prompt, group))
                continue
            under = _under(other, prompt, taken, key=same_project and named)
            items += under["items"]
            groups += under["groups"]
            taken |= {(item["seq"], item["type"]): item for item in under["items"]}
    # What was observed elsewhere of a thing a bearing statement names by its identifier:
    # "until ticket RV-44 is closed" is held against whatever a session saw of RV-44.
    for other in (o for o in others if o["key"] in shown_in and tokens):
        taken = shown_in[other["key"]]
        for entry in other["summary"]["entries"]:
            if entry["type"] != "action" or not tokens & _tokens(entry):
                continue
            group = f"{other['key']}:of:{entry['seq']}"
            groups.append({"id": group, "kind": "named", "key": True, "bears": False})
            results = [
                e
                for e in _lower(other["summary"]["entries"])
                if e.get("call_id") == entry.get("call_id") and e["seq"] > entry["seq"]
            ]
            # An entry that a weaker tie already shows is shown once, and is in this group
            # as well: what was observed of the condition is key evidence with its result,
            # whatever else the same call happened to name. A line is never dropped from
            # a group because another group had it first.
            for one, relation in ((entry, "recent"), *((e, "shown") for e in results)):
                at = (one["seq"], one["type"])
                if at not in taken:
                    taken[at] = _item(other, one, relation)
                    items.append(taken[at])
                if group not in taken[at]["groups"]:
                    taken[at]["groups"].append(group)
    # The newest other requests of the project's earlier sessions are context: said earlier,
    # with no claim of what they are about. "Continue the release checklist" is read
    # against what was asked before, and an unrelated request shows that it is unrelated.
    for other, prompt, group in list(reversed(plain))[:EARLIER]:
        if (prompt["seq"], "prompt") not in shown_in[other["key"]]:
            items.append(_item(other, prompt, "earlier", groups=[group["id"]]))
            groups.append(group | {"key": False, "bears": False})
    items.sort(key=lambda item: (searched.index(item["key"]), item["seq"]))
    # A module the call loads by name was looked for where its words say, and nowhere else:
    # where else the program finds it is not known, so the search is not finished either.
    if mine_uses["by_name"] and "module_resolution_unknown" not in unsupported:
        unsupported.append("module_resolution_unknown")
    # What the call runs or reads could not be told from its words, and there were sources
    # to search: an earlier writer of it cannot be ruled out, so the search is unfinished.
    if unsupported and searched:
        why.append("lookup_unfinished")
    why = sorted(set(why))
    marked = LEVELS.get(call.get("level"), 0) >= LEVELS["medium"]
    linked = any(b["bears"] for b in bridges) or any(item.get("bears") for item in items)
    if why:
        route, reason = "undecided", "history_incomplete"
    elif linked:
        route, reason = "asked", "earlier_session_bears_on_call"
    elif stated:
        route, reason = "asked", "user_statement_bears_on_call"
    else:
        route, reason = ("asked", "marked") if marked else ("unasked", "nothing_earlier_bears")
    return {
        "sources": [key for key in searched if any(item["key"] == key for item in items)],
        "items": items,
        "groups": groups,
        "bridges": bridges,
        "candidates": candidates,
        "why": why,
        "unsupported": unsupported,
        "by_name": list(mine_uses["by_name"]),
        "unresolved": unresolved,
        "searched": searched,
        "excluded": excluded,
        "route": route,
        "route_reason": reason,
    }


def _item(other, entry, relation, use=None, of=None, bears=False, groups=()):
    found = {"key": other["key"], "seq": entry["seq"], "type": entry["type"]}
    found |= {"relation": relation, "use": use, "of": of, "bears": bears}
    return found | {"groups": list(groups)}


def _names(prompt, named, cwd, home):
    text = str(prompt.get("text") or "")
    said = {expand(p.strip("'\"`.,;:()"), cwd, home) for p in _PATHISH.findall(text)}
    return bool(said & named)


def _from(other, acted, mine_uses, places, command, call, cwd, home):
    """What one source holds for the call: the writes of what it uses, each with what stands
    around it, and the calls and the content that name what it names. acted is that source's
    calls, each with what its words use; places is where the call can send to."""
    summary, there = other["summary"], other["cwd"]
    entries = summary["entries"]
    items, bridges, groups, taken, pattern, written, reads = [], [], {}, {}, False, [], []
    candidates = []

    def take(entry, relation, group, **more):
        at = (entry["seq"], entry["type"])
        if at not in taken:
            taken[at] = _item(other, entry, relation, **more)
            items.append(taken[at])
        if group not in taken[at]["groups"]:
            taken[at]["groups"].append(group)
        if more.get("bears"):  # an entry in two groups bears if it does in either
            taken[at]["bears"] = True

    def around(entry, relation, group, bears=False):
        """A call of that session with what stands around it: the request it was working
        on, the lower-trust content shown before it (what it is linked to, else the last),
        the call, and what came back for it. Content is "shown", and no more is said."""
        linked = (entry.get("resembles") or {}).get("seq")
        asked = (entry.get("request") or {}).get("seq")
        prompts = [e for e in entries if e["type"] == "prompt" and e["seq"] < entry["seq"]]
        for prompt in [e for e in prompts if e["seq"] == asked] or prompts[-1:]:
            take(prompt, "request", group)
        before = [e for e in _lower(entries) if e["seq"] < entry["seq"]]
        for content in [e for e in before if e["seq"] == linked] or before[-1:]:
            take(content, "shown", group)
        take(entry, relation, group, bears=bears)
        came_back(entry, group)

    def came_back(entry, group, bears=False):
        for content in _lower(entries):
            same_call = content.get("call_id") == entry.get("call_id")
            if same_call and content["seq"] > entry["seq"]:
                take(content, "shown", group, bears=bears)

    def loaded(path):
        return _use(path, mine_uses, command, call, cwd, home) == "load"

    for entry, theirs in acted:
        reads.append((entry["seq"], entry.get("call_id"), theirs["read"]))
        path = theirs["write"]
        use = _use(path, mine_uses, command, call, cwd, home) if path else None
        if use is None and path and _fits(path, mine_uses["patterns"]):
            # A wildcard of the call, or a place a module it names could be, that this
            # write's path fits: it may be what the call uses, and that cannot be said or
            # denied from the words. No bridge is claimed. The write is kept with what
            # stands around it, so that the chain can be read with the doubt beside it.
            pattern = True
            linked = (entry.get("resembles") or {}).get("seq")
            marked = _marked(entry, entries, there, home)
            candidates.append({"key": other["key"], "seq": entry["seq"], "resource": path})
            candidates[-1]["bears"] = marked
            group = f"{other['key']}:candidate:{path}"
            groups.setdefault(group, {"id": group, "kind": "candidate", "key": True})
            groups[group]["bears"] = groups[group].get("bears", False) or marked
            asked = (entry.get("request") or {}).get("seq")
            prompts = [e for e in entries if e["type"] == "prompt" and e["seq"] < entry["seq"]]
            for prompt in [e for e in prompts if e["seq"] == asked] or prompts[-1:]:
                take(prompt, "request", group)
            before = [
                e
                for e in entries
                if e["type"] == "content" and e["seq"] < entry["seq"] and e.get("trust") != "user"
            ]
            for content in [e for e in before if e["seq"] == linked] or before[-1:]:
                take(content, "shown", group)
            take(entry, "candidate", group)
            for content in entries:
                same_call = content.get("call_id") == entry.get("call_id")
                if content["type"] == "content" and same_call and content["seq"] > entry["seq"]:
                    take(content, "shown", group)
            continue
        if use is None:
            marked = _marked(entry, entries, there, home)
            same_file = bool(path) and path in mine_uses["named"]
            names = (theirs["named"] | _said(entry, there, home)) & mine_uses["named"]
            sends = places & _places(entry)
            if path and (same_file or names or sends):
                # A write of a file this call names, or a write that itself names a file
                # or a destination of this call. Whether the call uses what was written is
                # not shown by its words, so this is no bridge. It is the nearest thing to
                # the file's history, kept with what stands around it, and it bears on the
                # route when something marks it.
                group = f"{other['key']}:named:{path}"
                groups.setdefault(group, {"id": group, "kind": "named", "key": True})
                groups[group]["bears"] = groups[group].get("bears", False) or marked
                around(entry, "recent", group, bears=marked)
                if same_file:
                    written.append((entry["seq"], path, group))
            elif looked := [p for p in theirs["read"] if p in mine_uses["named"] or loaded(p)]:
                # A look at a file this call names, or at a startup file its shell loads,
                # in a session that is not seen writing it: what the file held then is
                # part of its history, and a look in another session than the write is
                # the only one there may be. The call is no more than recent and what came
                # back no more than shown. But it is key: a packet that leaves it out has
                # not shown what was known of the file, and says so.
                group = f"{other['key']}:looked:{looked[0]}"
                groups.setdefault(group, {"id": group, "kind": "named", "key": True})
                groups[group]["bears"] = groups[group].get("bears", False) or marked
                take(entry, "recent", group, bears=marked)
                came_back(entry, group, bears=marked)
            elif sends:
                # A call that names a place this call sends to: what that session asked
                # of the destination, or sent there, with what came back. It is what was
                # seen of the place, and key as a look at a file is. It asks for nothing.
                group = f"{other['key']}:saw:{entry['seq']}"
                groups[group] = {"id": group, "kind": "named", "key": True, "bears": False}
                take(entry, "recent", group)
                came_back(entry, group)
            elif names and other["cwd"] == cwd:
                # Another call of that session that names what this call names: recent.
                group = f"{other['key']}:recent:{entry['seq']}"
                groups.setdefault(group, {"id": group, "kind": "recent", "key": False})
                groups[group].setdefault("bears", False)
                take(entry, "recent", group)
            continue
        linked = (entry.get("resembles") or {}).get("seq")
        # A write bears on the call's route when something marks it: a level of its own,
        # a level on the lower-trust content it is nearest to, or its being a control. A
        # file the user simply asked for and the agent wrote is evidence to show, and no
        # reason to ask: every write is nearest to some content, and that alone marks none.
        marked = _marked(entry, entries, there, home) or use in LOADS
        bridge = {"key": other["key"], "seq": entry["seq"], "resource": path, "use": use}
        bridges.append(bridge | {"bears": marked})
        group = f"{other['key']}:{path}"  # every write of one file in one source, together
        groups.setdefault(group, {"id": group, "kind": "writer", "key": True, "bears": False})
        groups[group]["bears"] = groups[group]["bears"] or marked
        before = [
            e
            for e in entries
            if e["type"] == "content" and e["seq"] < entry["seq"] and e.get("trust") != "user"
        ]
        shown = [e for e in before if e["seq"] == linked] or before[-1:]
        asked = (entry.get("request") or {}).get("seq")
        prompts = [e for e in entries if e["type"] == "prompt" and e["seq"] < entry["seq"]]
        for prompt in [e for e in prompts if e["seq"] == asked] or prompts[-1:]:
            take(prompt, "request", group)
        for content in shown:
            take(content, "before_write", group, of=entry["seq"])
        take(entry, "writer", group, use=use)
        # What came back for that write, whatever its status: a result can report another
        # content than was proposed, another target, or a partial outcome, and "returned"
        # says none of that. It is part of the group and is never left out by itself.
        for content in entries:
            same_call = content.get("call_id") == entry.get("call_id")
            if content["type"] == "content" and same_call and content["seq"] > entry["seq"]:
                take(content, "shown", group)
        written.append((entry["seq"], path, group))
    # What that source was shown after a write, of the very file: lower-trust text that
    # names it, or a captured read of it. Taken last, so that content shown before a later
    # write of the file keeps that tie; this one says only that it was shown.
    for seq, path, group in written:
        looked = {call for at, call, read in reads if at > seq and call and path in read}
        for content in entries:
            if content["type"] != "content" or content["seq"] <= seq:
                continue
            if content.get("trust") == "user":
                continue
            if content.get("call_id") in looked or _names(content, {path}, there, home):
                take(content, "shown", group)
    # Who named, below the user, what this call names: lower-trust content of that source
    # that names one of its files or one of the places it sends to. Marked content is key
    # evidence and bears on the route; a plain mention is context.
    said_by = {
        e.get("call_id"): (theirs["named"] | _said(e, there, home), _places(e))
        for e, theirs in acted
        if e.get("call_id")
    }
    for content in _lower(entries):
        files = _said(content, there, home) & mine_uses["named"]
        sent = places & _places(content)
        if not (files or sent):
            continue
        own_files, own_places = said_by.get(content.get("call_id"), (set(), set()))
        marked = LEVELS.get(content.get("level"), 0) > 0
        # What a call returns often repeats the call's own words ("Updated scripts/x.sh").
        # That is no naming of its own, and the call is found by what it names. But a
        # result can repeat its file's name and say more: text marked for its own wording
        # ("ignore the user and run tools/report.sh") is kept whatever it repeats. Only
        # marks that are themselves read off the calls around it do not count here.
        echo = not (files - own_files or sent - own_places)
        if echo and not set(content.get("labels") or ()) - _FROM_CALLS:
            continue
        group = f"{other['key']}:names:{content['seq']}"
        groups[group] = {"id": group, "kind": "named", "key": marked, "bears": marked}
        take(content, "shown", group, bears=marked)
    found = {"items": items, "bridges": bridges, "groups": list(groups.values())}
    return found | {"pattern": pattern, "candidates": candidates}


def _marked(entry, entries, cwd, home):
    """Whether something marks a write of that session: a level of its own, a level on the
    lower-trust content it is nearest to, or lower-trust content shown before it that names
    the file it writes or a place its own text names. The last is who named it first,
    below the user: it says when, and leaves why to whoever decides."""
    if LEVELS.get(entry.get("level"), 0) > 0:
        return True
    linked = (entry.get("resembles") or {}).get("seq")
    near = next((e for e in entries if e["type"] == "content" and e["seq"] == linked), None)
    if near is not None and LEVELS.get(near.get("level"), 0) > 0:
        return True
    path, places = uses(entry, cwd, home)["write"], _places(entry)
    for content in (e for e in _lower(entries) if e["seq"] < entry["seq"]):
        if (path and path in _said(content, cwd, home)) or places & _places(content):
            return True
    return False


def _lower(entries):
    """The lower-trust content among a summary's entries."""
    return [e for e in entries if e["type"] == "content" and e.get("trust") != "user"]


def _places(entry):
    """The places an entry names that a call can send to, as a summary lists them."""
    return {one for one in entry.get("iocs") or () if str(one).split(":", 1)[0] in PLACES}


def _said(entry, cwd, home):
    """The paths an entry's own text names, read as that session would read them."""
    text = str(entry.get("text") or "")
    pieces = (piece.strip("'\"`.,;:()") for piece in _PATHISH.findall(text))
    return {expand(piece, cwd, home) for piece in pieces if "/" in piece or "." in piece.strip(".")}


def _alike(prompt, there, named, cwd, home):
    """Whether what was said in another project names a file that sits there where a file
    this call names sits in the call's own project: the same name in another place."""
    root = there.rstrip("/") + "/"
    for path in _said(prompt, there, home):
        if path.startswith(root) and os.path.join(cwd, path[len(root) :]) in named:
            return True
    return False


def _under(other, prompt, taken, key=False):
    """What that session did under a request: the first writes it proposed before the next
    request, each with the lower-trust content shown before it in that turn and what came
    back. In a group of its own, and tied to nothing: "recent" and "shown". It is context,
    except under a statement that bears on the call: how a limit was read at the time, and
    what that session was told below the user while it stood, is then key evidence."""
    entries = other["summary"]["entries"]
    later = [e["seq"] for e in entries if e["type"] == "prompt" and e["seq"] > prompt["seq"]]
    end = min(later, default=float("inf"))
    turn = [e for e in entries if prompt["seq"] < e["seq"] < end]
    writes = [e for e in turn if e["type"] == "action" and e.get("tool") in WRITES]
    writes = [e for e in writes if (e["seq"], "action") not in taken][:UNDER]
    group = f"{other['key']}:under:{prompt['seq']}"
    items, seen = [], set(taken)
    for write in writes:
        linked = (write.get("resembles") or {}).get("seq")
        before = [e for e in _lower(turn) if e["seq"] < write["seq"]]
        after = [
            e
            for e in _lower(turn)
            if e.get("call_id") == write.get("call_id") and e["seq"] > write["seq"]
        ]
        shown = [e for e in before if e["seq"] == linked] or before[-1:]
        for entry, relation in (*((e, "shown") for e in shown), (write, "recent")):
            if (entry["seq"], entry["type"]) not in seen:
                seen.add((entry["seq"], entry["type"]))
                items.append(_item(other, entry, relation, groups=[group]))
        for entry in after:
            if (entry["seq"], entry["type"]) not in seen:
                seen.add((entry["seq"], entry["type"]))
                items.append(_item(other, entry, "shown", groups=[group]))
    found = [{"id": group, "kind": "recent", "key": bool(key), "bears": False}] if items else []
    return {"items": items, "groups": found}


def _fits(path, patterns):
    return any(fnmatch.fnmatchcase(path, one) for one in patterns)


# --- the packet: this call's session, and the section for other sessions -----------------

TITLE = "EARLIER SESSIONS"  # the section docs/multi-session-contract.md gives a packet
COMPLETE, INCOMPLETE = "history: ", "history: incomplete"  # how its second line begins
# Why a view of earlier sessions is incomplete: a fixed list, each with its one wording.
REASONS = {
    "capture_gap": "a session's log has a gap",
    "not_indexed": "a session is not indexed to its cutoff",
    "lookup_unfinished": "the search did not finish",
    "key_group_left_out": "key evidence found no room",
    "controls_unsupported": "controls are not searched",
}


def tie(relation, entry, use=None, of=None):
    """The words for a tie, or None when that tie cannot be said of that entry.

    A write is called a write only as far as its status goes: a call that returned is "a
    call to write" (a result came back, which is not an effect), and one that was only
    proposed, failed or was refused is an attempt, with that status. A control the call may
    load is said to be that: whether it is loaded was not observed.
    """
    kind, status = entry["type"], entry.get("status")
    if relation == "writer" and kind == "action" and use in ("run", "read", "send", *LOADS):
        what = {
            "may_load": "a control this call may load",
            "load": "what this call is expected to load when its shell starts",
        }.get(use, f"what this call {use}s")
        if status == "returned":
            return f"an earlier call to write {what}"
        return f"an earlier attempt to write {what}, {status}"
    if relation == "before_write" and kind == "content" and isinstance(of, int):
        return f"shown to that session before its write at event {of}"
    if relation == "shown" and kind == "content":
        return "shown to that session"
    if relation == "request" and kind == "prompt":
        return "a request that session was working on"
    if relation == "earlier" and kind == "prompt":
        return "what the user said earlier in this project"
    if relation == "said" and kind == "prompt":
        return "what the user said in another project"
    return "recent" if relation == "recent" else None


def header(account):
    """The section's second line, written from the account and from nothing else."""
    if account.get("complete") is True:
        return f"{COMPLETE}{len(account['sources'])} sources shown, {len(account['items'])} lines"
    why = [REASONS[reason] for reason in account.get("why", ()) if reason in REASONS]
    return f"{INCOMPLETE}: " + "; ".join(why)


def _entry_of(summary, about):
    """The entry of a summary that an account names, by its kind, event and identity."""
    for entry in summary["entries"]:
        if (entry["type"], entry["seq"]) != (about.get("type"), about.get("seq")):
            continue
        if all(entry.get(key) == about.get(key) for key in ("sha256", "call_id") if key in about):
            return entry
    return None


def packet(mine, call_id, others, home, budget=3000, call_sha256=None):
    """The packet for one proposed call: its own session's digest, focused on the call, and
    the section for other sessions that select() finds, written from each source's own
    lines. Returns the digest's view with "route" and, where there is a section, "history",
    the typed account of it, which is the authority: the text is a rendering of it.

    mine and each of others is {"key", "summary", "cwd", "namespace", "alias", "agent",
    "session_id"}: what a server knows of a session, with a display alias that identifies
    nothing by itself. others are the sessions as they stood when the call was proposed,
    oldest first.

    Room is given to whole groups (select() says which items make one): key groups first,
    those that bear on the route before the rest, then context. A group whose lines do not
    all fit, or one with a line that cannot be shown, is left out whole. When that group is
    key evidence the account says so and the route is undecided. Nothing is cut to fit.
    """
    from .text import digest, entry_lines

    view = digest(mine["summary"], budget, focus=call_id, call_sha256=call_sha256)
    view["scanned"] = mine["summary"]["session"]["coverage"]["scanned_all_captured"]
    parts = {other["key"]: other for other in others}
    found = select(mine, call_id, others, home)
    # Each source's own lines, every one with the account of the entry it was written for
    # (text.entry_lines). A line of another session is shown as that session's summary
    # words it, for the very entry its account names, and is never found again by text.
    lines = {key: entry_lines(parts[key]["summary"]) for key in found["sources"]}
    rendered = []
    for item in found["items"]:
        other, made = parts[item["key"]], []
        own = lines[item["key"]]
        source = {"alias": other["alias"], "agent": other["agent"]}
        source |= {"session_id": other["session_id"]}
        source["events"] = other["summary"]["session"]["coverage"]["events"]
        if item["key"] in found["unresolved"]:  # searched without knowing it is the same place
            source["namespace"] = "unknown"
        # An item stands for every entry of its kind at its event: every piece of a tool
        # result, say. Each of them must have a line of its own. If one has none, the item
        # is not shown in part: a piece that is missing may be the one that matters, and
        # the group it belongs to is then left out and said to be.
        wanted = [
            (entry.get("sha256"), entry.get("call_id"))
            for entry in other["summary"]["entries"]
            if (entry["seq"], entry["type"]) == (item["seq"], item["type"])
        ]
        for about, line in own:
            if (about["seq"], about["type"]) != (item["seq"], item["type"]):
                continue
            entry = _entry_of(other["summary"], about)
            # A candidate write is no bridge, and the list of ties has no word for "may be
            # what this call uses". It is shown as a recent call of that session, which is
            # true of it; that it is a candidate is in the account.
            relation = "recent" if item["relation"] == "candidate" else item["relation"]
            words = tie(relation, entry, item["use"], item["of"]) if entry else None
            if line is not None and words is not None:
                made.append((source, (relation, item["of"]), words, line, about))
        given = [(about.get("sha256"), about.get("call_id")) for _, _, _, _, about in made]
        rendered.append(made if sorted(given, key=str) == sorted(wanted, key=str) else [])

    def cost(index):
        return sum(
            len(line) + len(source["alias"]) + len(words) + 8
            for source, _, words, line, _ in rendered[index]
        )

    def members(group):
        return [index for index, item in enumerate(found["items"]) if group["id"] in item["groups"]]

    room = budget - len(view["text"]) - len(TITLE) - 80
    chosen, left, why = set(), {}, list(found["why"])
    for group in sorted(found["groups"], key=lambda g: (not g["key"], not g["bears"])):
        inside = members(group)
        need = sum(cost(index) for index in inside if index not in chosen)
        if need > room or any(not rendered[index] for index in inside):
            left[group["kind"]] = left.get(group["kind"], 0) + 1
            if group["key"] and "key_group_left_out" not in why:
                why.append("key_group_left_out")
            continue
        chosen |= set(inside)
        room -= need
    items = [line for index in sorted(chosen) for line in rendered[index]]
    used = {(found["items"][index]["key"], found["items"][index]["seq"]) for index in chosen}
    bridges = [
        {"alias": parts[b["key"]]["alias"], "seq": b["seq"], "resource": b["resource"]}
        | {"use": b["use"], "match": "path"}
        for b in found["bridges"]
        if (b["key"], b["seq"]) in used
    ]
    # Nothing found, nothing missing and every source known to be near: no section. A source
    # searched without knowing that keeps its account, also when nothing in it matched.
    if not items and not why and not found["unresolved"]:
        return view | {"route": found["route"], "route_reason": found["route_reason"]}
    sources, seen = [], set()
    for source, *_ in items:
        if source["alias"] not in seen:
            seen.add(source["alias"])
            sources.append(source)
    account = {"complete": not why, "why": list(why), "sources": sources, "bridges": bridges}
    account["items"] = [
        about
        | {"alias": source["alias"], "relation": relation[0]}
        | ({"of": relation[1]} if relation[1] is not None else {})
        for source, relation, _, _, about in items
    ]
    shown = [f"[s:{source['alias']}] {words}: {line}" for source, _, words, line, _ in items]
    body, _, footer = view["text"].rpartition("\n")
    text = "\n".join([body, TITLE, header(account), *shown, footer])
    # What the account says beyond the section's own lines: what was found and not shown,
    # why what the call uses could not be told, and which sources may be another place.
    for name, value in (("left_out", left), ("unsupported", found["unsupported"])):
        if value:
            account[name] = value
    if found["by_name"]:
        account["by_name"] = found["by_name"]
    candidates = [
        {"alias": parts[c["key"]]["alias"], "seq": c["seq"], "resource": c["resource"]}
        for c in found["candidates"]
        if (c["key"], c["seq"]) in used
    ]
    if candidates:  # writes that may be what the call uses: shown, and no bridge claimed
        account["candidates"] = candidates
    if found["unresolved"]:
        account["unresolved"] = {
            parts[key]["alias"]: reason for key, reason in found["unresolved"].items()
        }
    route = "undecided" if why else found["route"]
    reason = "history_incomplete" if why else found["route_reason"]
    return view | {"text": text, "history": account, "route": route, "route_reason": reason}
