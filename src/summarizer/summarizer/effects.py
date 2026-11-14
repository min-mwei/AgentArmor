"""How a shell command line uses files: its words, its simple commands and what each may change.

One reading for the campaign checker (training/summarizer/check_campaigns.py) and for the history
lookup's object account (history.py), so the two never read a command differently (MR196). Moved
here from the checker, behaviour unchanged. Standard library only.

- shell_words(): the words of a line as a shell cuts them, each with whether its tilde is the
  home, or None outside what this reads (not summarizer.facts.shell_words, the hook's plain words);
- commands(): its simple commands, each as (words, whether it has a redirection, what its output
  redirections write), or None when a substitution or an operator inside a word stops the cut;
- effect(): "look", "named" or "unknown", what one simple command may do to files; targets(): the
  paths a "named" command may change;
- read_form(): (file, what it prints) for a command in one of the read forms the checker compares.
"""

import os
import re


def shell_words(command):
    """The words of one command line as a shell cuts them, each as (the word with its quotes
    and backslashes taken off, whether it begins with a tilde the shell expands to the home
    directory). None when the line is outside what this reads.

    Written for this harness and apart from the builder's reader. Whether a tilde is
    expanded is a fact about one place in the command and is kept with the word it is in:
    it is never worked out afterwards from how the word reads once its quotes are gone,
    since two words can then read alike and differ in this.

    A tilde is expanded when it is the first character of a word and neither it nor the
    slash after it is quoted or escaped ("~" alone, "~/x", and "~/'x y'"). A tilde with a
    quote or a backslash before its first slash ("'~/x'", "~'/x'", "\\~/x") stays a
    tilde. "~name" is another user's home, which is not read here. Outside what this
    reads: an unclosed quote; a $ or a backquote outside single quotes; an unquoted ; | &
    < > ( ) other than one "&&", which is returned as a word; and a # that begins a word,
    where a comment starts.
    """
    words, word, state, quote, at = [], None, "", "", 0

    def close():
        nonlocal word, state
        if word is not None:
            words.append(("".join(word), state in ("open", "home")))
        word, state = None, ""

    def begin():
        nonlocal word, state
        if word is None:
            word, state = [], "plain"

    while at < len(command):
        char = command[at]
        if quote == "'":
            if char == "'":
                quote = ""
            else:
                word.append(char)
        elif quote == '"':
            if char == '"':
                quote = ""
            elif char == "\\" and command[at + 1 : at + 2] in ('"', "\\", "$", "`"):
                at += 1
                word.append(command[at])
            elif char in "$`":
                return None
            else:
                word.append(char)
        elif char in "'\"" or (char == "\\" and at + 1 < len(command)):
            begin()
            if state == "open":  # quoted or escaped before the first slash: no expansion
                state = "plain"
            if char == "\\":
                at += 1
                word.append(command[at])
            else:
                quote = char
        elif char.isspace():
            close()
        elif command[at : at + 2] == "&&":
            close()
            words.append(("&&", False))
            at += 1
        elif char in ";|&<>()`$" or (char == "#" and word is None):
            return None
        elif word is None:
            word, state = [char], "open" if char == "~" else "plain"
        else:
            if state == "open":
                if char != "/":
                    return None  # "~name": another user's home
                state = "home"
            word.append(char)
        at += 1
    if quote:
        return None
    close()
    return words


def resolved(word, cwd, home=""):
    """A word of a command as the absolute path it names: ~ is home, a relative path is cwd's."""
    path = home + word[1:] if home and (word == "~" or word.startswith("~/")) else word
    return os.path.normpath(path if os.path.isabs(path) else os.path.join(cwd, path))


ONLY_LOOK = {
    "cat",
    "head",
    "tail",
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "wc",
    "sha256sum",
    "sha1sum",
    "shasum",
    "md5sum",
    "cksum",
    "file",
    "ls",
    "stat",
    "test",
    "[",
    "diff",
    "cmp",
    "less",
    "more",
    "nl",
    "cut",
    "tac",
    "jq",
    "realpath",
    "readlink",
    "basename",
    "dirname",
    "du",
    "pwd",
    "echo",
    "printf",
    "true",
    "false",
    "date",
    "whoami",
    "id",
    "uname",
    "hostname",
    "which",
    "shellcheck",
}
# Commands that may change only the files they name (a named directory takes in what is under it).
CHANGE_NAMED = {
    "cp",
    "mv",
    "install",
    "tee",
    "rm",
    "rmdir",
    "mkdir",
    "touch",
    "chmod",
    "chown",
    "ln",
    "unlink",
    "truncate",
    "dd",
    "shred",
    "zip",
    "scp",
    "rsync",
}
GIT_ONLY_LOOK = {
    "status",
    "log",
    "show",
    "diff",
    "rev-parse",
    "ls-files",
    "ls-tree",
    "blame",
    "grep",
    "describe",
    "cat-file",
    "shortlog",
}
GIT_VALUED = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--super-prefix",
    "--exec-path",
}
JSON_TOOL_FLAGS = {"--sort-keys", "--no-ensure-ascii", "--json-lines", "--tab", "--compact"}
FIND_ACTS = {
    "-delete",
    "-exec",
    "-execdir",
    "-ok",
    "-okdir",
    "-fprint",
    "-fprint0",
    "-fprintf",
    "-fls",
}
CURL_ELSEWHERE = ("-O", "--remote-name", "-J", "--remote-header-name", "-K", "--config")
CONTROL = {"|", "||", "&&", ";", "&"}
REDIRECT = re.compile(r"(\d*)(>>?|<|&>)(.*)")


def _ungrouped(command):
    """A line with the brackets of a group or subshell, "(du -sh a b) >/dev/null", taken out of
    its words: an opening one begins a command, and a closing one ends nothing, so a redirection
    after the group stays with the group's last command and the file it writes is still counted.
    A bracket inside quotes is part of its word, as the shell has it."""
    out, quote = [], ""
    for char in command:
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "(":
            char = " ; "
        elif char == ")":
            char = " "
        out.append(char)
    return "".join(out)


def commands(command):
    """The simple commands of a command line, each as (its words without redirections, whether it
    has a redirection, the files its output redirections write), or None when the line holds a
    substitution or an operator inside a word, which this plain reading cannot cut. An output
    redirection (">", ">>", "1>", "2>", "&>", with or without its target attached) writes its
    target; an input redirection ("<") and a descriptor duplication (">&2", "2>&1") write no file
    (MR181-3). A command with a redirection is never compared as a read (MR180-2)."""
    found = shell_words(command)
    if found is not None:
        words = [word for word, _ in found]
    elif "$(" in command or "`" in command:
        return None
    else:
        words = _ungrouped(command).split()
    commands, current, redirected, writes, skip = [], [], False, [], None
    for word in words:
        if skip is not None:
            if skip:
                writes.append(word)  # "> file": the file is the next word
            skip = None
            continue
        if word in CONTROL:
            if current:
                commands.append((current, redirected, writes))
            current, redirected, writes = [], False, []
            continue
        redirect = REDIRECT.fullmatch(word)
        if redirect:
            redirected = True
            _, op, target = redirect.groups()
            writing = op != "<" and not target.startswith("&")
            if not target:
                skip = writing
            elif writing:
                writes.append(target)
            continue
        if any(char in word for char in "|;&<>"):
            return None
        current.append(word)
    if current:
        commands.append((current, redirected, writes))
    return commands


def git_subcommand(words):
    at = 1
    while at < len(words) and words[at].startswith("-"):
        at += 2 if words[at] in GIT_VALUED else 1
    return words[at] if at < len(words) else None


def targets(words, cwd, home=""):
    """Every path a command that may change only what it names could change, from the same words
    effect() reads (MR180-4, MR181-2): each word after the first, the value of an option written
    --option=value or of an operand written name=value (dd of=FILE), and what follows the letter of
    a short option written with its value attached (-oFILE). Over-approximated on purpose: a path
    taken for a target is only looked at again."""
    found = set()
    for word in words[1:]:
        if word.startswith("--"):
            values = [word.split("=", 1)[1]] if "=" in word else []
        elif word.startswith("-"):
            values = [word[2:]]
        else:
            values = [word, word.split("=", 1)[1]] if "=" in word else [word]  # dd of=FILE
        for value in values:
            if value:
                found.add(resolved(value, cwd, home))
    return found


def effect(words):
    """ "look", "named" or "unknown": what one simple command may do to files (see above)."""
    if not words:
        return "look"
    base, args = os.path.basename(words[0]), words[1:]
    if base in ONLY_LOOK:
        return "look"
    if base in CHANGE_NAMED:
        return "named"
    if base == "sort":
        return "named" if any(w.startswith(("-o", "--output")) for w in args) else "look"
    if base == "uniq":
        return "named" if len([w for w in args if not w.startswith("-")]) > 1 else "look"
    if base == "find":
        return "unknown" if any(w in FIND_ACTS for w in args) else "look"
    if base == "sed":
        return "look" if read_form(words) is not None else "unknown"
    if base == "tar":
        return (
            "unknown"
            if any(w in ("-x", "--extract", "--get") for w in args)
            or (args and not args[0].startswith("-") and "x" in args[0])
            or any(w.startswith("-") and not w.startswith("--") and "x" in w for w in args)
            else "named"
        )
    if base == "curl":
        return "unknown" if any(w.startswith(CURL_ELSEWHERE) for w in args) else "named"
    if base == "git":
        sub = git_subcommand(words)
        if sub in GIT_ONLY_LOOK and not any(w.startswith("--output") for w in args):
            return "look"
        return "unknown"
    if base in ("python", "python3") and args[:2] == ["-m", "json.tool"]:
        rest, at, files = args[2:], 0, []
        while at < len(rest):
            if rest[at] == "--indent":
                at += 2
                continue
            if rest[at] in JSON_TOOL_FLAGS:
                at += 1
                continue
            if rest[at].startswith("-"):
                return "unknown"
            files.append(rest[at])
            at += 1
        return "look" if len(files) <= 1 else "named"
    return "unknown"


def numbered(raw):
    return [f"{at:>6}\t{line}" for at, line in enumerate(raw, 1)]


COUNTS = {
    "-c": lambda text: len(text.encode("utf-8")),
    "-l": lambda text: text.count("\n"),
    "-w": lambda text: len(text.split()),
}


class Counted:
    """What a counting form prints, made from the file's whole text and compared with the runs
    of spaces wc pads its numbers with read as one."""

    def __init__(self, make):
        self.make = make


def read_form(words):
    """(file, what it prints) for a command in one of the forms the check compares (MR176-1), where
    what it prints turns the file's lines into the lines the command shows; None for any other form.

    wc -c, -l or -w FILE (the count of the file's bytes, newlines or words, then its name; MR181);
    cat FILE; cat -n FILE (each line numbered as cat numbers it); head or tail with -n N, -nN, -N or
    --lines=N (10 lines when no count is given); tail -n +N (the lines from line N on); sed -n 'Np',
    'M,Np' or 'M,$p'; grep with -n, -i, -F or -e and one pattern, a fixed string (with -F, or with
    none of . [ ] * ^ $ and backslash): every line holding it, in order, as "N:line" with -n."""
    base, args = words[0], words[1:]
    if base == "wc" and len(args) == 2 and args[0] in COUNTS and not args[1].startswith("-"):
        count, name = COUNTS[args[0]], args[1]
        return name, Counted(lambda text: [f"{count(text)} {name}"])
    if base == "cat":
        if len(args) == 1 and not args[0].startswith("-"):
            return args[0], lambda raw: raw
        if len(args) == 2 and args[0] == "-n" and not args[1].startswith("-"):
            return args[1], numbered
        return None
    if base in ("head", "tail"):
        value, rest = "10", list(args)
        if rest and rest[0] in ("-n", "--lines") and len(rest) > 1:
            value, rest = rest[1], rest[2:]
        elif rest and rest[0].startswith("--lines="):
            value, rest = rest[0][len("--lines=") :], rest[1:]
        elif rest and rest[0].startswith("-n") and len(rest[0]) > 2:
            value, rest = rest[0][2:], rest[1:]
        elif rest and re.fullmatch(r"-\d+", rest[0]):
            value, rest = rest[0][1:], rest[1:]
        if len(rest) != 1 or rest[0].startswith("-"):
            return None
        if base == "tail" and re.fullmatch(r"\+\d+", value):
            start = max(int(value[1:]) - 1, 0)
            return rest[0], lambda raw: raw[start:]
        if not value.isdigit():
            return None
        count = int(value)
        if base == "head":
            return rest[0], lambda raw: raw[:count]
        return rest[0], lambda raw: raw[-count:] if count else []
    if base == "sed":
        if len(args) == 3 and args[0] == "-n" and not args[2].startswith("-"):
            span = re.fullmatch(r"(\d+)(?:,(\d+|\$))?p", args[1])
            if span and int(span.group(1)) >= 1:
                first = int(span.group(1)) - 1
                end = span.group(2)
                last = None if end == "$" else int(end) if end else first + 1
                return args[2], lambda raw: raw[first:last]
        return None
    if base == "grep":
        flags, pattern, rest, at = set(), None, [], 0
        while at < len(args):
            word = args[at]
            if word == "--":
                rest += args[at + 1 :]
                break
            if word == "-e" and at + 1 < len(args) and pattern is None:
                pattern, at = args[at + 1], at + 2
                continue
            if word.startswith("-") and len(word) > 1:
                if word.startswith("--") or not set(word[1:]) <= {"n", "i", "F"}:
                    return None
                flags |= set(word[1:])
                at += 1
                continue
            rest.append(word)
            at += 1
        if pattern is None and rest:
            pattern, rest = rest[0], rest[1:]
        if pattern is None or len(rest) != 1:
            return None
        if "F" not in flags and any(char in pattern for char in ".[]*^$\\"):
            return None
        fold = "i" in flags
        needle = pattern.lower() if fold else pattern

        def held(raw):
            hits = [
                (at, line)
                for at, line in enumerate(raw, 1)
                if needle in (line.lower() if fold else line)
            ]
            return [f"{at}:{line}" if "n" in flags else line for at, line in hits]

        return rest[0], held
    return None


# Commands whose output comes from the files they name: shown in another form, it is looked at.
