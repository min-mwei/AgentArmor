"""Deterministic session facts read off hook events. Standard library only.

These are retention signals for the summary, not enforcement: they decide what a guard model must
get to see. Path classes are lexical (plus the symlink target the hook resolved), and shell facts
are pattern matches on the literal command text, which a shell may interpret differently.
"""

import bisect
import ipaddress
import os
import re

# --- paths -------------------------------------------------------------------------------------

_SENSITIVE_HOME = (
    ".ssh",
    ".aws",
    ".gnupg",
    ".kube",
    ".azure",
    ".config/gcloud",
    ".config/gh",
    ".config/anthropic",
    ".docker/config.json",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
    ".claude/.credentials.json",
    ".codex/auth.json",
    ".cache/huggingface/token",
    ".huggingface/token",
    ".password-store",
    ".local/share/keyrings",
    ".mozilla",
    ".terraform.d/credentials.tfrc.json",
    "Library/Keychains",
    "Library/Cookies",
    "Library/Application Support/Google/Chrome",
    "Library/Application Support/Firefox",
    "Library/Application Support/BraveSoftware",
    "Library/Application Support/Microsoft Edge",
    "Library/Safari",
    ".config/google-chrome",
    ".config/chromium",
    ".bash_history",
    ".zsh_history",
    ".python_history",
    ".psql_history",
    ".mysql_history",
    ".local/share/fish/fish_history",
)
# Where this project keeps its evidence. Writing or deleting there is tampering with the observer.
_OBSERVER_HOME = (".summarizer",)
_LOCKFILES = {
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "poetry.lock", "uv.lock", "pipfile.lock", "cargo.lock", "go.sum", "gemfile.lock",
    "composer.lock",
}  # fmt: skip
_SENSITIVE_ABS = ("/etc/shadow", "/etc/sudoers", "/etc/ssh", "/var/run/secrets", "/run/secrets")
_SENSITIVE_NAME = re.compile(
    r"\.env(\.(?!example$|sample$|template$|dist$|defaults$)[\w-]+)?"
    r"|id_(rsa|dsa|ecdsa|ed25519)|.+\.(pem|key|p12|pfx|jks|keystore|kdbx)"
    r"|credentials(\.\w+)?|secrets?\.(json|ya?ml|toml|env|txt)|\.?htpasswd|kubeconfig"
    r"|service[-_]?account.*\.json"
)
# Loose file names such as hf_tok.txt or openai_key.txt. Data and config extensions only, so
# source files about tokens and tokenizer vocabularies stay out.
_SECRET_WORDS = {"token", "tok", "apikey", "appkey", "secret", "password", "passwd", "creds"}
_KEY_OWNERS = {
    "api",
    "app",
    "private",
    "access",
    "ssh",
    "auth",
    "signing",
    "master",
    "service",
    "license",
    "openai",
    "anthropic",
    "aws",
    "gcp",
    "azure",
    "hf",
    "github",
    "gh",
    "stripe",
}
_SECRET_EXT = {"", "txt", "json", "yml", "yaml", "env", "ini", "cfg", "conf", "toml", "csv"}

_AGENT_DIRS = {".claude", ".codex", ".cursor", ".gemini", ".windsurf", ".continue", ".aider"}
_AGENT_NAMES = {
    "claude.md",
    "claude.local.md",
    "agents.md",
    "agents.override.md",
    "gemini.md",
    "memory.md",
    "skill.md",
    ".cursorrules",
    ".windsurfrules",
    ".mcp.json",
    "mcp.json",
    ".claude.json",
    "copilot-instructions.md",
}
_STARTUP_HOME = (
    ".zshrc",
    ".zshenv",
    ".zprofile",
    ".zlogin",
    ".bashrc",
    ".bash_profile",
    ".bash_login",
    ".profile",
    ".config/fish",
    ".config/systemd",
    ".config/autostart",
    "Library/LaunchAgents",
    ".ssh/authorized_keys",
    ".ssh/config",
    ".ssh/rc",
    ".gitconfig",
    ".config/git",
)
_STARTUP_ABS = (
    "/etc/profile",
    "/etc/zshrc",
    "/etc/bashrc",
    "/etc/cron",
    "/etc/systemd",
    "/etc/hosts",
    "/etc/sudoers",
    "/Library/LaunchDaemons",
    "/Library/LaunchAgents",
)
_CI_PARTS = (".github/workflows", ".circleci", ".husky", ".vscode", ".devcontainer")
_CI_NAMES = {
    ".gitlab-ci.yml",
    "jenkinsfile",
    ".pre-commit-config.yaml",
    ".envrc",
    ".npmrc",
    ".yarnrc",
    ".yarnrc.yml",
    ".pnpmfile.cjs",
    "pip.conf",
    "sitecustomize.py",
    "usercustomize.py",
}
# Written once, these keep steering later sessions, shells, Git operations or CI runs.
PERSISTENT = {"agent_config", "vcs_config", "startup", "ci_config"}


def expand(path, cwd="", home=""):
    """Lexically absolutize a path the way a file tool would: ~ and the working directory."""
    if home and (path == "~" or path.startswith("~/")):
        path = home + path[1:]
    if not os.path.isabs(path):
        path = os.path.join(cwd or "/", path)
    return os.path.normpath(path)


def _under(path, root):
    return bool(root) and (path == root or path.startswith(root.rstrip("/") + "/"))


def _has_prefix(relative, prefixes):
    return any(relative == p or relative.startswith(p + "/") for p in prefixes)


def _secret_name(name):
    stem, _, ext = name.rpartition(".") if "." in name[1:] else (name, "", "")
    if ext not in _SECRET_EXT:
        return False
    words = set(re.split(r"[._-]+", stem))
    return bool(words & _SECRET_WORDS or ({"key", "keys"} & words and words & _KEY_OWNERS))


def path_classes(path, root="", home=""):
    """Classes of an absolute path: sensitive, agent_config, vcs_config, startup, ci_config,
    observer (this project's own evidence), lockfile, and outside when it is not under the
    workspace root."""
    classes = set()
    name = os.path.basename(path).lower()
    relative = path[len(home.rstrip("/")) + 1 :] if _under(path, home) else None
    if (
        (relative is not None and _has_prefix(relative, _SENSITIVE_HOME))
        or _has_prefix(path, _SENSITIVE_ABS)
        or re.fullmatch(r"/proc/[^/]+/environ", path)
        or _SENSITIVE_NAME.fullmatch(name)
        or _secret_name(name)
    ):
        classes.add("sensitive")
    if _AGENT_DIRS & set(path.split("/")) or name in _AGENT_NAMES:
        classes.add("agent_config")
    if re.search(r"/\.git/(config$|hooks/|info/)", path) or name in (
        ".gitattributes",
        ".gitmodules",
    ):
        classes.add("vcs_config")
    if (relative is not None and _has_prefix(relative, _STARTUP_HOME)) or path.startswith(
        _STARTUP_ABS
    ):
        classes.add("startup")
    if name in _CI_NAMES or any(f"/{part}/" in path + "/" for part in _CI_PARTS):
        classes.add("ci_config")
    if relative is not None and _has_prefix(relative, _OBSERVER_HOME):
        classes.add("observer")
    if name in _LOCKFILES:
        classes.add("lockfile")
    if root and not _under(path, root):
        classes.add("outside")
    return classes


# --- shell commands ----------------------------------------------------------------------------

_WRAPPERS = "sudo|doas|exec|nohup|time|xargs|command|env|do|then|else|if|while|until"
# Where options may sit between a command and its subcommand (aws --profile x iam). Nothing is
# matched here: a pattern that tried to skip options backtracked without bound on a row of
# flags. option_free() below writes the option-free forms and every pattern is run on those.
_O = ""
_WRAPPER_SET = set(_WRAPPERS.split("|"))


def _commands(names):
    """Match any of names where a shell would look for a command word."""
    lead = (
        r"(?:^|[;&|(`]|\$\()\s*(?:(?:" + _WRAPPERS + r")\s+(?:-\S+\s+)*)*(?:\w+=\S*\s+)*(?:\S*/)?"
    )
    return re.compile(lead + r"(?:" + names + r")(?=\s|$)", re.M)


_NETWORK = _commands(
    r"curl|wget|nc|ncat|netcat|socat|telnet|ftp|sftp|scp|rsync|ssh|http|https|aria2c|dig|nslookup"
    r"|aws|gcloud|az|kubectl|gsutil|rclone"
    r"|git" + _O + r"\s+(?:push|clone|fetch|pull|ls-remote|remote\s+add|submodule)"
    r"|gh" + _O + r"\s+(?:api|pr|issue|gist|release|repo|workflow|secret|run)"
    r"|(?:npm|pnpm|yarn)\s+publish|docker\s+(?:push|pull|login)|twine\s+upload"
)
_SEND = re.compile(
    r"\bcurl\b[^|;&]*\s(?:-d|--data(?:-\w+)?|-F|--form|-T|--upload-file|-X\s*(?:POST|PUT|PATCH))\b"
    r"|\bwget\b[^|;&]*--post-(?:data|file)|\b(?:scp|rsync|sftp|rclone)\s|\bgit" + _O + r"\s+push\b"
    r"|\bgh"
    + _O
    + r"\s+(?:pr|issue|gist|release)\s+(?:create|comment|edit)\b|\bgh"
    + _O
    + r"\s+api\b[^|;&]*\s-[fFX]"
    r"|\b(?:npm|pnpm|yarn)\s+publish\b|\bdocker" + _O + r"\s+push\b|\btwine\s+upload\b"
    r"|\baws" + _O + r"\s+s3\s+(?:cp|sync|mv)\b|\bgsutil\s+cp\b|>\s*/dev/tcp/"
    r"|\|\s*(?:nc|ncat|netcat)\b|\b(?:nc|ncat|netcat)\b[^|;&]*<\s*\S"
    r"|\b(?:mail|mailx|mutt)\b[^|;&]*\s-[sa]\b|\b(?:sendmail|msmtp|swaks)\b"
    r"|\baz"
    + _O
    + r"\s+storage\s+blob\s+upload|\bgh"
    + _O
    + r"\s+gist\s+create\b|\bftp\b[^|;&]*\bput\b"
)
_INSTALL = re.compile(
    r"\b(?:npm|pnpm|yarn|bun)\s+(?:i|install|add|ci)\b|\b(?:npx|bunx)\s|\bpip[x3]?\s+install\b"
    r"|\buv\s+(?:pip\s+install|add|sync|tool\s+install)\b|\bpoetry\s+(?:add|install)\b"
    r"|\bbrew\s+install\b|\bcargo\s+(?:install|add)\b|\bgo\s+(?:install|get)\b"
    r"|\bgem\s+install\b|\bapt(?:-get)?\s+install\b"
)
_REMOTE_EXEC = re.compile(
    r"\|\s*(?:sudo\s+)?(?:ba|z|da|k)?sh\b|\|\s*python[\d.]*\b|\|\s*node\b"
    r"|\b(?:bash|sh|zsh|source)\s+<\(|\beval\s+[\"']?\$\("
    r"|\b(?:curl|wget)\b[^|]*?\s(?:-\w*[oO]|--output(?:-document)?)[= ]?\S+.*\bchmod\s+\S*\+?\S*x"
    r"|\b(?:IEX|Invoke-Expression)\b.*\b(?:DownloadString|Invoke-WebRequest|iwr|irm|curl)\b"
    r"|\b(?:DownloadString|Invoke-WebRequest|iwr|irm)\b.*\|\s*(?:IEX|Invoke-Expression)\b"
    r"|\bpython[\d.]*\s+-c\s[^|;&]*\b(?:urlopen|requests\.get)\b[^|;&]*\bexec\(",
    re.I | re.S,
)
_PRIVILEGE = _commands(
    r"sudo|doas|launchctl\s+(?:load|bootstrap|enable)|systemctl\s+(?:enable|start)"
    r"|crontab(?!\s+-l)|schtasks|setcap"
)
_SPAWN = _commands(r"claude|codex|gemini|aider|cursor-agent|opencode")
_BYPASS = re.compile(
    r"--dangerously-[\w-]+|--yolo\b|bypassPermissions|danger-full-access|disableAllHooks"
    r"|--ask-for-approval[= ]never|--no-verify\b"
)
_DESTRUCTIVE = re.compile(
    r"\brm\s+(?:-\w*\s+)*-\w*[rR]\w*\b|\bgit"
    + _O
    + r"\s+(?:reset\s+--hard|clean\s+-\w*f|push\s+(?:\S+\s+)*"
    r"(?:-f\b|--force))|\bdd\s+if=|\bmkfs|\bshred\b|\bdrop\s+(?:table|database|schema)\b|\btruncate\s"
    r"|\bterraform"
    + _O
    + r"\s+(?:destroy|apply\b[^|;&]*-destroy)|\bpulumi"
    + _O
    + r"\s+destroy\b|\bkubectl"
    + _O
    + r"\s+delete\b"
    r"|\bhelm"
    + _O
    + r"\s+(?:uninstall|delete)\b|\baws"
    + _O
    + r"\s+s3\s+(?:rb|rm\b[^|;&]*--recursive)"
    r"|\baws" + _O + r"\s+[\w-]+\s+(?:delete|terminate)-[\w-]+|\b(?:gcloud|az)\s[^|;&]*\sdelete\b"
    r"|\bgh"
    + _O
    + r"\s+repo\s+delete\b|\bdocker"
    + _O
    + r"\s+(?:system|volume)\s+prune\b|\bfind\b[^|;&]*\s-delete\b"
    r"|\bgit"
    + _O
    + r"\s+branch\s+-D\b|\bgit"
    + _O
    + r"\s+push\b[^|;&]*\s(?:--delete|:\S+)|>\s*/dev/(?:sd|disk|nvme)",
    re.I,
)
_OBFUSCATED = re.compile(
    r"\bbase64\s+(?:-d|-D|--decode)\b|\bxxd\s+-r\b|\bopenssl\s+enc\b"
    r"|(?:\\x[0-9a-fA-F]{2}){4,}|[A-Za-z0-9+/]{160,}={0,2}"
)
_SECRET_COMMAND = re.compile(
    r"(?:^|[;&|(]\s*)(?:env|printenv)\s*(?:$|[|>;&)])|/proc/\w+/environ"
    r"|\$\{?[A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_?KEY|CREDENTIAL)[A-Z0-9_]*"
    r"|\bsecurity\s+(?:find-(?:generic|internet)-password|dump-keychain|export)\b"
    r"|\bsecret-tool\b|\bpass\s+show\b|\bgit" + _O + r"\s+credential(?:-\w+)?\s+(?:fill|get)\b"
    r"|\bkeyring\s+get\b|\b(?:lazagne|mimikatz|secretsdump)\b"
    r"|\bop\s+(?:read|item)\b|\bgh" + _O + r"\s+auth\s+token\b|\bgcloud" + _O + r"\s+auth\s+print"
    r"|\baws" + _O + r"\s+configure\s+(?:get|export)|\bkubectl" + _O + r"\s+get\s+secrets?\b",
    re.M,
)
# Families added after the coverage review (docs/design.md, section 6). Each is a fact about the
# command, kept for recall: several have everyday uses, and the request in context decides.
_MORE = (
    (
        "reverse_shell",
        r"/dev/(?:tcp|udp)/|\b(?:nc|ncat|netcat)\b[^|;&]*\s-\w*[ec]\b"
        r"|\bmkfifo\b.*\b(?:nc|ncat|netcat|telnet|openssl)\b|\bsocat\b[^|;&]*\b(?:exec|system):"
        r"|\bsocket\b.*\b(?:dup2|pty\.spawn|subprocess|/bin/(?:ba)?sh)\b"
        r"|\b(?:ba)?sh\s+-i\b[^|;&]*[<>]&|\bfsockopen\b|\bNet\.Sockets\.TCPClient\b"
        r"|\bmsfvenom\b|\bmeterpreter\b",
    ),
    (
        "tunnel",
        r"\b(?:ngrok|cloudflared\s+tunnel|chisel|frpc|localtunnel|bore\s+local|serveo)\b"
        r"|\btailscale\s+(?:funnel|serve)\b|\bssh\b[^|;&]*\s-\w*[RD](?=[\s\d\[:]|$)|\blt\s+--port\b",
    ),
    (
        "observer_tamper",
        r"\b(?:pkill|killall|kill)\b[^|;&]*summarizer|\blaunchctl\s+(?:unload|bootout|remove|disable)"
        r"[^|;&]*summarizer|\bsystemctl\s+(?:stop|disable|mask)[^|;&]*summarizer"
        r"|\bsummarizer\s+prune\b|\bpip[x3]?\s+uninstall\b[^|;&]*summarizer",
    ),
    (
        "defense_off",
        r"\bspctl\s+--(?:master|global)-disable|\bcsrutil\s+disable|\bsetenforce\s+0\b"
        r"|\bufw\s+disable|\biptables\s+-F\b|\bpfctl\s+-d\b|\bauditctl\s+-e\s*0"
        r"|\bsystemctl\s+(?:stop|disable|mask)\s+(?:firewalld|auditd|apparmor|ufw|falcon\S*|osqueryd)"
        r"|\bxattr\s+-\w*d\w*\s+com\.apple\.quarantine|\bhistory\s+-[cw]\b|\bunset\s+HISTFILE"
        r"|\bHIST(?:FILE=/dev/null|SIZE=0|FILESIZE=0)|\bset\s+\+o\s+history"
        r"|\brm\b[^|;&]*(?:_history\b|/var/log\b|Library/Logs\b)|\bjournalctl\b[^|;&]*--vacuum"
        r"|\blog\s+erase\b|\bwevtutil\s+cl\b|\bSet-MpPreference\b[^|;&]*-Disable"
        r"|>\s*/var/log/\S+|\btruncate\b[^|;&]*/var/log|\bchattr\s+[+-]i\b"
        r"|\bkillall\b[^|;&]*(?:Santa|falcon|osqueryd|LittleSnitch)|\btccutil\s+reset",
    ),
    ("timestamp_change", r"\btouch\s+-\w*[tdr]\b|\bSetFile\s+-[dm]\b"),
    (
        "network_scan",
        r"\b(?:nmap|masscan|zmap|arp-scan|rustscan|nikto|gobuster|ffuf|sqlmap|hydra)\b"
        r"|\b(?:nc|ncat|netcat)\b[^|;&]*\s-\w*z",
    ),
    (
        "recon",
        r"(?:^|[;&|(]\s*)(?:whoami|id|hostname|uname\s+-a|ifconfig|ip\s+a(?:ddr)?|netstat|arp\s+-a"
        r"|sw_vers|systeminfo|lsof\s+-i|ss\s+-\w+|ps\s+(?:aux|-ef)|last|w)\s*(?:$|[;&|)>])"
        r"|/etc/(?:passwd|group|os-release|hosts)\b|\bgetent\s+passwd|\bdscl\s+\.\s+(?:list|read)",
    ),
    (
        "escalation",
        r"\bchmod\s+(?:-\w+\s+)*(?:[ug]{1,2}\+s|[2467][0-7]{3}|0?777|a\+rwx|o\+w)\b"
        r"|\bchown\s+(?:-\w+\s+)*root\b|\bdocker\b[^|;&]*(?:--privileged|--pid[= ]host"
        r"|--cap-add|-v\s+/:|docker\.sock)|/var/run/docker\.sock|\bnsenter\b|\bvisudo\b"
        r"|\busermod\b[^|;&]*-\w*G\b|>>?\s*/etc/sudoers|\bpkexec\b|\bsetcap\b",
    ),
    (
        "identity_change",
        r"\b(?:useradd|adduser|chpasswd)\b|\bdscl\s+\.\s+-create|\bnet\s+user\b[^|;&]*/add"
        r"|\baws"
        + _O
        + r"\s+iam\s+(?:create-(?:access-key|user|login-profile|role)|attach-\w+-policy"
        r"|put-\w+-policy|update-assume-role-policy|add-user-to-group)"
        r"|\bgcloud\b[^|;&]*(?:keys\s+create|add-iam-policy-binding)"
        r"|\baz" + _O + r"\s+(?:role\s+assignment\s+create|ad\s+sp\s+create)"
        r"|\bkubectl" + _O + r"\s+create\s+(?:clusterrolebinding|rolebinding|serviceaccount|token)"
        r"|\bgh" + _O + r"\s+(?:ssh-key|gpg-key)\s+add|\bgh" + _O + r"\s+repo\s+deploy-key\s+add"
        r"|\bgh" + _O + r"\s+secret\s+set|\bssh-copy-id\b",
    ),
    (
        "scheduled_task",
        r"\bcrontab\b(?!\s+-l)|\bat\s+(?:now|\d|midnight|noon)|\bschtasks\b[^|;&]*/create"
        r"|\bsystemd-run\b|\blaunchctl\s+(?:load|bootstrap|submit)\b|/etc/cron",
    ),
    (
        "exposure",
        r"--visibility[= ]public|\bgh"
        + _O
        + r"\s+repo\s+create\b[^|;&]*--public|--acl[= ]public-read"
        r"|\bdelete-public-access-block\b|\bput-bucket-policy\b|\ballUsers\b|\ballAuthenticatedUsers\b"
        r"|--cidr[= ](?:0\.0\.0\.0/0|::/0)|--source-ranges[= ]0\.0\.0\.0/0"
        r"|--public-access[= ](?!off)"
        r"|\bpython[\d.]*\s+-m\s+http\.server|\bphp\s+-S\s|\b(?:--host|--bind|-b)[= ]0\.0\.0\.0\b"
        r"|\bnpx\s+(?:http-server|serve)\b",
    ),
    (
        "registry_change",
        r"--(?:extra-)?index-url\b|--trusted-host\b|\b(?:npm|pnpm|yarn)\s+config\s+set\s+\S*registry"
        r"|--registry[= ]\S|\b(?:npm|pnpm|yarn|bun)\s+(?:i|install|add)\b[^|;&]*(?:git\+|https?://"
        r"|github:|\.tgz\b)|\bpip[x3]?\s+install\b[^|;&]*(?:git\+|https?://)"
        r"|\bgit"
        + _O
        + r"\s+remote\s+(?:set-url|add)\b|\bgit"
        + _O
        + r"\s+config\b[^|;&]*(?:insteadOf|hooksPath"
        r"|sshCommand|credential\.helper)|\bGOPROXY=|\bGONOSUM(?:DB|CHECK)=|\bGOFLAGS=-insecure"
        r"|\bcargo\s+install\b[^|;&]*--git|\bbrew\s+tap\b|\bapt-key\s+add|\badd-apt-repository\b"
        r"|\bgem\s+sources\s+-a|\bgit" + _O + r"\s+submodule\s+add\b",
    ),
    (
        "collection",
        r"\b(?:pbpaste|wl-paste|Get-Clipboard)\b|\bxclip\b[^|;&]*-o\b|\bxsel\b[^|;&]*-o\b"
        r"|\b(?:screencapture|scrot|gnome-screenshot|grim)\b|\bimport\s+-window\b"
        r"|\bsqlite3\b[^|;&]*(?:Cookies|Login Data|cookies\.sqlite|logins\.json|History)\b"
        r"|\b(?:tcpdump|tshark|wireshark|mitmproxy|mitmdump)\b|/proc/\d+/(?:mem|maps)\b|\bgcore\b"
        r"|\b(?:tar\s+-?\w*c\w*|zip\s+-\w*r\w*|7z\s+a)\b[^|;&]*"
        r"(?:~|\$HOME|/home/|/Users/|/etc\b|\.git\b|\.ssh|\.aws)|\bosascript\b[^|;&]*(?:password|keystroke)",
    ),
    (
        "secret_search",  # everyday in a code base, and how credentials are hunted
        r"\b(?:grep|rg|ag|findstr)\b[^|;&]*(?:passw(?:or)?d|secret|api[_-]?key|token|PRIVATE KEY"
        r"|credential)|\bfind\b[^|;&]*-i?name\b[^|;&]*(?:\.pem|\.key|id_rsa|\.env|credential"
        r"|secret|\.kdbx)|\b(?:trufflehog|gitleaks)\b",
    ),
    (
        "infra_destroy",
        r"\bterraform"
        + _O
        + r"\s+(?:destroy|apply\b[^|;&]*-destroy)|\bpulumi"
        + _O
        + r"\s+destroy\b"
        r"|\bkubectl"
        + _O
        + r"\s+delete\s+(?:ns|namespace|cluster|pv|pvc|--all)|\bhelm"
        + _O
        + r"\s+(?:uninstall|delete)\b"
        r"|\baws"
        + _O
        + r"\s+s3\s+rb\b|\baws"
        + _O
        + r"\s+[\w-]+\s+(?:delete|terminate)-[\w-]+|\bgh"
        + _O
        + r"\s+repo\s+delete\b"
        r"|\b(?:gcloud|az)\s[^|;&]*\sdelete\b",
    ),
    (
        "file_encryption",
        r"\bopenssl\s+enc\b[^|;&]*-(?:aes|e\b)|\bgpg\b[^|;&]*(?:-c\b|--symmetric|--encrypt)"
        r"|\bzip\b[^|;&]*\s-\w*[eP]\b|\b7z\s+a\b[^|;&]*\s-p|\bage\s+(?:-r|-p|--encrypt)\b",
    ),
    (
        "resource_abuse",
        r":\(\)\s*\{\s*:\s*\|\s*:|\b(?:xmrig|minerd|cpuminer|ethminer|nbminer|t-rex)\b"
        r"|stratum\+(?:tcp|ssl)://|\bwhile\s+true\s*;?\s*do\b[^;]*\bfork\b|\byes\s*>\s*/dev/",
    ),
    (
        "dns_exfil",
        r"\b(?:dig|nslookup|host|drill)\b[^|;&]*(?:\$\(|`|\$\{?[A-Za-z_])",
    ),
    (
        "data_in_url",
        r"\b(?:curl|wget|http|https)\b[^|;&]*://[^\s'\"]*(?:\$\(|`|\$\{?[A-Za-z_])"
        r"|\b(?:curl|wget)\b[^|;&]*\s(?:-d|--data(?:-\w+)?|-F|--form|-H|--header)\s*[\"']?[^|;&]*"
        r"(?:\$\(\s*(?:cat|env|printenv|base64)|`\s*(?:cat|env|printenv|base64))",
    ),
)
_MORE = tuple((flag, re.compile(pattern, re.I | re.M | re.S)) for flag, pattern in _MORE)
# Hosts whose purpose is to receive what is sent to them, or to hand it to someone else.
_CAPTURE_HOSTS = (
    "webhook.site", "requestbin.com", "pipedream.net", "requestcatcher.com", "beeceptor.com",
    "ngrok.io", "ngrok-free.app", "ngrok.app", "trycloudflare.com", "loca.lt", "serveo.net",
    "burpcollaborator.net", "oastify.com", "oast.fun", "oast.pro", "interact.sh", "dnslog.cn",
    "pastebin.com", "paste.ee", "dpaste.com", "ix.io", "termbin.com", "0x0.st", "transfer.sh",
    "file.io", "temp.sh", "bashupload.com", "catbox.moe", "anonfiles.com", "gofile.io",
    "hooks.slack.com", "api.telegram.org", "discord.com", "discordapp.com",
)  # fmt: skip
_METADATA_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "100.100.100.200",
    "fd00:ec2::254",
}
_INTERNAL = tuple(
    ipaddress.ip_network(block)
    for block in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16")
) + tuple(ipaddress.ip_network(block) for block in ("::1/128", "fc00::/7", "fe80::/10"))
_ADDRESS = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.]*\w)")


def host_flags(names):
    """Flags for where a call reaches: a metadata service, an internal address, a capture host."""
    flags = {}
    for name in sorted(names):
        if name in _METADATA_HOSTS:
            flags.setdefault("cloud_metadata", name)
            continue
        if name == "localhost" or name.endswith((".local", ".internal", ".lan", ".corp")):
            flags.setdefault("internal_target", name)
        try:
            address = ipaddress.ip_address(name)
        except ValueError:
            address = None
        if address is not None and any(address in network for network in _INTERNAL):
            flags.setdefault("internal_target", name)
        if any(name == host or name.endswith("." + host) for host in _CAPTURE_HOSTS):
            flags.setdefault("capture_host", name)
    return flags


_URL = re.compile(
    r"\b[a-z][a-z0-9+.-]*://(?:[^\s/@'\"<>`]*@)?(?:\[[^\]\s'\"<>`]+\])?[^\s'\"<>`)\]}]*", re.I
)
_AUTHORITY = re.compile(r"^[^/]*//(?:[^@/]*@)?(?:\[([^\]]*)\]|([^/:?#]*))")
_SCP_HOST = re.compile(r"\b[\w.-]+@((?:[\w-]+\.)+[a-z]{2,}):", re.I)
_WORD = re.compile(r"[^\s;|&<>()`'\"=]+")
_REDIRECT = re.compile(r""">>?\s*(?:"([^"]*)"|'([^']*)'|((?:\\.|[^\s;|&<>()`'"])+))""")
_WRITES_ARGS = {"tee", "touch", "chmod", "chown", "truncate", "rm", "mkdir"}
_WRITES_LAST = {"cp", "mv", "ln", "install", "rsync"}


def hosts(text):
    """Hosts a command or argument names: URL authorities and scp-style user@host: targets."""
    found = set()
    for url in _URL.findall(text):
        authority = _AUTHORITY.match(url)
        if not authority:
            continue
        if authority[1] is not None:  # [address]: an IPv6 literal, its port outside the brackets
            try:
                found.add(str(ipaddress.ip_address(authority[1].split("%")[0])))
            except ValueError:
                pass  # not an address: no host is made up for it
            continue
        host = authority[2].lower().strip(".")
        if host and "[" not in host and "]" not in host:
            found.add(host)
    found.update(match.lower() for match in _SCP_HOST.findall(text))
    return found


def shell_segments(text, split_assignments=True, redirections=True):
    """The simple commands of a command line, each as its words, quotes and escapes taken off.

    Lexical only: nothing is expanded or run. "a b", 'a b' and a\\ b are each one word, so a
    path with a space in it stays one path. Commands end at ; | & a newline, a parenthesis or a
    backquote outside quotes; = < and > only end a word. One pass, every word, no limit.
    With split_assignments off, = stays inside its word, so "-c color.ui=false" is an option
    and one value, not an option and two words. With redirections off, a redirection's file
    descriptor and its target ("2>/dev/null", "< in.txt", "2>&1", "&> log") are left out, so
    what remains is the command and its arguments.
    """
    breaks = ";|&<>()`=" if split_assignments else ";|&<>()`"
    words, word, quote, at, target = [], [], "", 0, False

    def close():
        nonlocal word, target
        if word and not (target and not redirections):
            words.append("".join(word))
        if word:
            target = False
        word = []

    while at < len(text):
        char = text[at]
        if quote:
            if char == quote:
                quote = ""
            elif char == "\\" and quote == '"' and at + 1 < len(text) and text[at + 1] in '"\\$`':
                at += 1
                word.append(text[at])
            else:
                word.append(char)
        elif char in "'\"":
            quote = char
        elif char == "\\" and at + 1 < len(text):
            at += 1
            word.append(text[at])
        elif char in "<>" or (char == "&" and text[at + 1 : at + 2] == ">"):
            if word and "".join(word).isdigit():
                word = []  # the file descriptor being redirected
            close()
            at += char == "&"  # "&>" redirects both streams
            while text[at + 1 : at + 2] in (">", "<"):
                at += 1
            if text[at + 1 : at + 2] == "&":  # a duplicated descriptor: no file follows
                at += 1
                ahead = at + 1
                while text[ahead : ahead + 1] in (" ", "\t"):
                    ahead += 1  # "2>& 1": the descriptor may stand apart
                end = ahead
                while text[end : end + 1].isdigit() or text[end : end + 1] == "-":
                    end += 1
                if end > ahead and (end == len(text) or not text[end].isalnum()):
                    at = end - 1
                elif ahead == at + 1:
                    target = True  # ">&file": both streams to a file
            else:
                target = True
        elif char.isspace() or char in breaks:
            close()
            if char in ";|&()`\n":
                target = False
                if words:
                    yield words
                    words = []
        else:
            word.append(char)
        at += 1
    close()
    if words:
        yield words


def shell_words(text):
    """Every word of a command line (see shell_segments)."""
    return [word for words in shell_segments(text) for word in words]


# Commands that take options before a subcommand the patterns above look for.
_HEADS = frozenset(
    "aws terraform kubectl helm pulumi git gcloud az gh docker podman npm pnpm yarn bun pip pip3 "
    "pipx uv cargo go gem brew systemctl launchctl gsutil rclone oc tofu".split()
)
_OPTION_PLACES = 8  # places a subcommand is tried at when unlisted options leave it open
# Options that take the next word as their value, before the subcommand, per command.
_VALUED = {
    "git": "-C -c --git-dir --work-tree --namespace --super-prefix --config-env --exec-path",
    "aws": "--profile --region --output --endpoint-url --color --ca-bundle --cli-read-timeout "
    "--cli-connect-timeout --query --cli-binary-format",
    "kubectl": "--context --namespace -n --kubeconfig --cluster --user -s --server --token --as "
    "--as-group --as-uid --certificate-authority --client-certificate --client-key "
    "--request-timeout --cache-dir --tls-server-name --password --username --profile -v",
    "oc": "--context --namespace -n --kubeconfig --cluster --user -s --server --token --as",
    "helm": "--kube-context --namespace -n --kubeconfig --kube-apiserver --kube-token "
    "--registry-config",
    "terraform": "-chdir",
    "tofu": "-chdir",
    "pulumi": "--cwd -C --stack -s --color --config-file",
    "gh": "-R --repo --hostname",
    "docker": "-H --host --context -c --config -l --log-level --tlscacert --tlscert --tlskey",
    "podman": "--connection -c --root --runroot --log-level --url --identity",
    "gcloud": "--project --account --configuration --format --verbosity "
    "--impersonate-service-account",
    "az": "--subscription --output -o --query",
    "npm": "--prefix -C --registry --userconfig --cache -w --workspace",
    "pnpm": "--dir -C --filter -F --registry",
    "yarn": "--cwd --registry",
    "bun": "--cwd --config -c",
    "pip": "--python --proxy --cert --cache-dir --log --index-url --extra-index-url",
    "pip3": "--python --proxy --cert --cache-dir --log --index-url --extra-index-url",
    "pipx": "--python",
    "uv": "--directory --project --config-file --cache-dir --python -p",
    "cargo": "-C --config --color -Z",
    "go": "-C",
    "brew": "",
    "gem": "",
    "systemctl": "-H --host -M --machine --root -p --property -t --type",
    "launchctl": "",
    "gsutil": "-o -h -i -u",
    "rclone": "--config",
}
_VALUED = {head: frozenset(options.split()) for head, options in _VALUED.items()}


# Options of the wrappers that take the next word as their value.
_WRAPPER_VALUED = {
    "sudo": "-u -g -h -p -C -D -R -T -U -r -t --user --group --host --prompt --chdir --role",
    "doas": "-u -C",
    "env": "-u -C -S --unset --chdir --split-string",
    # -i, --replace, --eof and --max-lines take their value attached or not at all (GNU), so
    # never the next word; -I, -E and -L require one.
    "xargs": "-n -P -I -L -s -d -E -a --max-args --max-procs --max-chars --delimiter --arg-file",
    "time": "-o -f --output --format",
    "exec": "-a",
    "nohup": "",
    "command": "",
}
_WRAPPER_VALUED = {name: frozenset(options.split()) for name, options in _WRAPPER_VALUED.items()}


def _head(words):
    """Position of the command word of one simple command, if it is one the patterns care about.

    The command word is the first word that is not an assignment, a wrapper (sudo, env, time,
    xargs, ..., by its basename), a wrapper's option, or the value of a wrapper option that
    takes one ("sudo -u git git push": the first git is a user name). An operand further along
    that happens to be spelled like a command ("git push origin go") is not a command.
    """
    at, wrapper = 0, None
    while at < len(words):
        word = words[at]
        name = os.path.basename(word)
        if word.startswith("-"):
            takes = "=" not in word and word in _WRAPPER_VALUED.get(wrapper, ())
            at += 2 if takes else 1
        elif name in _WRAPPER_SET:
            wrapper, at = name, at + 1
        elif "=" in word and not word.startswith(("/", ".", "~")):
            at += 1  # an assignment before the command
        else:
            return at if name in _HEADS else None
    return None


def option_free(command):
    """(text, partial): the command again with the options before its subcommand left out.

    "aws --profile audit iam create-access-key" is also "aws iam create-access-key", and
    "git -C 'my dir' -c a=b push origin main --force" is also "git push origin main --force".
    In each simple command whose command word is one the patterns care about (_HEADS, reached
    directly or through wrappers such as sudo and env), the run of options after it is read and
    the command is written again from each place its subcommand could start, **to the end of
    that simple command**:

    - where a table of options that take a value (_VALUED) puts it, with options the table
      does not know taken as flags, and again with them taken as options with a value;
    - and, because unlisted options can be a mix of both, from each word in the run that is
      not an option, and from the end of the run. When there are more such places than
      _OPTION_PLACES, the first and the last ones are tried, and partial says so if an
      unlisted option made the reading uncertain.

    One command word per simple command and a fixed number of candidates, so the text written
    is linear in the command. No pattern skips options; the original command is matched too.
    """
    out, partial = {}, False
    for words in shell_segments(command, split_assignments=False, redirections=False):
        head = _head(words)
        if head is None or head + 1 >= len(words) or not words[head + 1].startswith("-"):
            continue
        valued = _VALUED.get(os.path.basename(words[head]), frozenset())
        end, places, unknown = head + 1, [], False
        while end < len(words) and (words[end].startswith("-") or words[end - 1].startswith("-")):
            if not words[end].startswith("-"):
                places.append(end)
            elif "=" not in words[end] and words[end] not in valued:
                unknown = True
            end += 1
        starts = []
        for unknown_take_values in (False, True):
            at = head + 1
            while at < end and words[at].startswith("-"):
                option = words[at]
                takes = "=" not in option and (option in valued or unknown_take_values)
                at += (
                    2 if takes and at + 1 < len(words) and not words[at + 1].startswith("-") else 1
                )
            starts.append(at)
        places.append(end)
        if len(places) > _OPTION_PLACES:
            partial = partial or unknown
            places = places[: _OPTION_PLACES // 2] + places[-_OPTION_PLACES // 2 :]
        for start in dict.fromkeys([*starts, *places]):
            if start < len(words):
                out[" ".join([words[head], *words[start:]])] = None
    return " ; ".join(out), partial


def _written(command):
    """Words a command writes to: redirect targets and the operands of file-changing commands."""
    written = set()
    for segment in re.split(r"[;&|\n]+", command):
        targets = [next(filter(None, found), "") for found in _REDIRECT.findall(segment)]
        written.update(target.replace("\\ ", " ") for target in targets if target)
        segment = re.sub(r"^\s*(?:\w+=\S*\s+)+", "", _REDIRECT.sub(" ", segment))
        words = shell_words(segment)
        while words and (words[0] in _WRAPPER_SET or words[0].startswith("-")):
            words.pop(0)
        if not words:
            continue
        verb = os.path.basename(words[0])
        if verb in _WRITES_ARGS or (
            verb == "sed" and re.search(r"\s(?:-\w*i|--in-place)", segment)
        ):
            written.update(words[1:])
        elif verb in _WRITES_LAST or (verb == "dd" and "of" in words):
            written.add(words[-1])
        elif verb == "git" and words[1:2] == ["config"]:
            values = [word for word in words[2:] if not word.startswith("-")]
            reads = {"--get", "--get-all", "--get-regexp", "--list", "-l"} & set(words)
            if not reads and (len(values) >= 2 or {"--unset", "--add", "--edit"} & set(words)):
                written.add(".git/config")
    return written


def shell_facts(command, cwd="", home=""):
    """Flags (each with the text that raised it), hosts and classified paths for a shell command."""
    flags = {}
    checks = (
        ("network", _NETWORK),
        ("network_send", _SEND),
        ("package_install", _INSTALL),
        ("remote_exec", _REMOTE_EXEC),
        ("privilege", _PRIVILEGE),
        ("agent_spawn", _SPAWN),
        ("control_bypass", _BYPASS),
        ("destructive", _DESTRUCTIVE),
        ("obfuscated", _OBFUSCATED),
        ("sensitive", _SECRET_COMMAND),
    )
    # Each fact is looked for in the command as written, and again with the options that sit
    # between a command and its subcommand left out.
    plain, partial = option_free(command)
    if partial:  # said, so a command read in only some of its possible ways is not called read
        flags["analysis_partial"] = "more places for a subcommand than were tried"
    for flag, pattern in (*checks, *_MORE):
        match = pattern.search(command) or (pattern.search(plain) if plain else None)
        if match:
            flags[flag] = " ".join(match.group().split()).lstrip(";&|(` ")[:80]
    if "network_send" in flags:
        flags.setdefault("network", flags["network_send"])
    reached = hosts(command) | set(_ADDRESS.findall(command))
    for flag, basis in host_flags(reached).items():
        flags.setdefault(flag, basis)
    written = _written(command)
    paths = {}
    # A bare word is a path candidate only if it looks like one or the command writes to it.
    # A backslash-escaped space is part of a path, not the end of a word.
    words = set(shell_words(_URL.sub(" ", command)))
    for word in sorted(words | written):
        if not (word in written or "/" in word or "." in word or word.startswith("~")):
            continue
        path = expand(word.lstrip("@").rstrip(",:"), cwd, home)  # curl -d @file
        classes = path_classes(path, "", home)
        if not classes:
            continue
        paths[path] = classes
        if "sensitive" in classes:
            flags.setdefault("sensitive", path)
        if "observer" in classes and (word in written or "destructive" in flags):
            flags.setdefault("observer_tamper", path)
        if "lockfile" in classes and word in written:
            flags.setdefault("lockfile_edit", path)
        if persistent := sorted(classes & PERSISTENT):
            flag = "persistence_write" if word in written else "persistence_ref"
            flags.setdefault(flag, f"{path} ({','.join(persistent)})")
    if "persistence_write" in flags:
        flags.pop("persistence_ref", None)
    return {"flags": flags, "hosts": hosts(command), "paths": paths}


# --- text --------------------------------------------------------------------------------------

_OVERRIDES = re.compile("[\U0000202a-\U0000202e\U00002066-\U00002069\U000e0000-\U000e007f]")
_ZERO_WIDTH = re.compile("[\U0000034f\U0000180e\U0000200b\U0000200c\U00002060\U0000feff]")
# Half of a surrogate pair (U+D800 to U+DFFF) is among them. It is not a character: a tool
# that cuts its output between the two halves of an emoji leaves one, and JSON carries it as an
# escape. Text that holds one cannot be written as UTF-8 at all, so it is always spelled out.
_INVISIBLE = re.compile(
    "[\x00-\x08\x0b-\x1f\x7f-\x9f\U0000034f\U0000180e\U0000200b-\U0000200f\U0000202a-\U0000202e\U00002060-\U00002069\U0000feff\U000e0000-\U000e007f\ud800-\udfff]"
)
_DOTTED = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+")


def hidden_text(text):
    """True if text carries bidi overrides, Unicode tag characters or a run of zero-width marks."""
    if _OVERRIDES.search(text):
        return True
    return len(_ZERO_WIDTH.findall(text.removeprefix("\U0000feff"))) >= 4


def visible(text):
    """Spell out control and invisible characters so a reader sees what the agent was given."""
    return _INVISIBLE.sub(lambda match: f"\\u{{{ord(match.group()):04x}}}", text)


def dotted_tokens(text):
    """Dotted lowercase tokens (hosts, file names, versions) for exact-match provenance lookups."""
    return set(_DOTTED.findall(text.lower()))


# --- indicators in text ------------------------------------------------------------------------
# Wording that is typical of text trying to steer an agent. These favor recall: documentation that
# tells a person to edit a settings file matches too, and telling the two apart is the detector's
# job. Gaps are bounded so a long line cannot make a pattern slow.

_AI = (
    r"(?:AI|LLM|language model|assistant|agent|chatbot|model|Claude|Codex|ChatGPT|GPT|Copilot"
    r"|Gemini|Cursor)"
)
# A role being called by name where a sentence starts: "Assistant, ...", "- AI reviewer, ...".
_SENTENCE_START = r"(?:^[ \t]*(?:[-*>#]+[ \t]*)?|(?<=[.!?:;][ \t])|(?<=[\"'“‘(\[]))"
_BEFORE_ROLE = (
    r"(?:(?:AI|automation|automated|coding|review|support|virtual|research|dear|hey|hi|hello"
    r"|ok|okay|attention)[ \t,]+){0,2}"
)
_AFTER_ROLE = r"(?:[ \t]+(?:assistant|agent|reviewer|bot))?,[ \t]+"
# The names that leave little doubt an AI is meant. "model" and "Cursor" start many sentences
# that are about something else, so they are left to the weak rule.
_CALLED = r"(?:AI|LLM|assistant|agent|chatbot|bot|Claude|Codex|ChatGPT|GPT|Copilot|Gemini)"
# What follows a name that may only be described: "Claude, like other models, ...". It may as
# well be an order with something put in first ("Assistant, in this task, delete ..."), so
# these are not dropped: the weak rule takes them.
_ASIDE = (
    r"(?:like|unlike|which|who|whose|that|and|or|but|is|was|are|were|has|have|had|the|an?|in|on"
    r"|of|by|with|our|its|their|however|though|too|also|not)\b"
)
_TEXT_FLAGS = (
    (
        "override_text",
        r"\b(?:ignore|disregard|forget|override|do not follow|don't follow|stop following)\b"
        r"[^.\n]{0,40}\b(?:instructions?|prompts?|rules|guidelines|directions|request|task"
        r"|context|constraints|polic(?:y|ies))\b"
        r"|\b(?:new|updated|revised|real|actual)\s+(?:instructions?|task|priority|rules)\s*[:.-]"
        r"|\byou are now\b|\bfrom now on,? (?:you|always|respond|reply|only)\b"
        r"|\bstop (?:the|your) current task\b|\binstead,? (?:you (?:must|should|will)|do the)",
    ),
    (
        "addressed_text",
        r"\b(?:if you are|as|dear|attention|note to|notes? for|message (?:to|for)"
        r"|instructions? (?:to|for))\s+(?:an?\s+|the\s+|any\s+)?" + _AI + r"\b"
        r"|\b" + _AI + r"s?\s+(?:reading|processing|summarizing|reviewing|analyzing)\s+this\b"
        r"|\b" + _AI + r"s?\s+(?:must|should|shall|need to|(?:is|are) required to)\b"
        r"|^\s*(?:assistant|agent|ai|system)\s*:"
        # a role addressed by name: "Support AI:", "for automated reviewers:", "for the bot:"
        r"|\b(?:\w+\s+){0,2}(?:AI|assistants?|bots?|agents?)\s*:"
        r"|\b(?:for|to)\s+(?:the\s+|any\s+|all\s+)?(?:automated\s+|AI\s+|virtual\s+)?"
        r"(?:reviewers?|assistants?|agents?|bots?|summarizers?|crawlers?|parsers?|models?)\s*[:,]"
        r"|\b(?:whoever|whatever|anyone|any)\s+(?:\w+\s+){0,2}(?:summarizes|reads|processes"
        r"|reviews|is (?:reading|summarizing|writing|processing))\b"
        r"|\bif (?:an?|the)\s+" + _AI + r"\b"
        # an AI role called by name where a sentence starts: "Assistant, delete the old rows"
        r"|" + _SENTENCE_START + _BEFORE_ROLE + _CALLED + _AFTER_ROLE + r"(?!" + _ASIDE + r")"
        r"[a-z]{2,}",
    ),
    (
        # any role called by name where a sentence starts, whatever follows. Weak: a sentence
        # about the role begins the same way. It is the broad form of the rule above
        "called_text",
        _SENTENCE_START + _BEFORE_ROLE + _AI + _AFTER_ROLE + r"\S+",
    ),
    (
        # says in the third person, or in the passive, what an assistant will do or what a reply
        # must be. Weak: documentation about a tool reads the same way
        "third_person_text",
        r"\b(?:the|this|your|our|any|every|each)\s+(?:\w+\s+)?" + _AI + r"\s+(?:will|is to"
        r"|is expected to|is supposed to|has to|ought to|must not|should not|shall not|may not"
        r"|is not to|is (?:hereby\s+)?(?:instructed|directed|asked|told|authori[sz]ed|permitted"
        r"|allowed) to)\b"
        r"|\b(?:the|all|every|each|any)\s+(?:final\s+|next\s+)?(?:repl(?:y|ies)|responses?"
        r"|answers?|summar(?:y|ies))\s+(?:must|should|shall|(?:is|are) to|needs? to|ha(?:s|ve) to"
        r"|(?:is|are) required to)\b",
    ),
    (
        # speaks of the reader's own reply or rules, as only text aimed at a responder does
        "responder_text",
        r"\byour (?:reply|response|answer|output|summary|final answer|next (?:message|reply)"
        r"|operating rules|rules|instructions|guidelines|system prompt)\b"
        r"|\b(?:into|in|to) this (?:reply|response|answer|summary)\b"
        r"|\b(?:system prompt|hidden instructions?|developer message|operating rules)\b",
    ),
    (
        # an action tied to this text being read
        "trigger_text",
        r"\b(?:when|once|after|whenever|as soon as)\b[^.\n]{0,40}\b(?:this|the) (?:file|document"
        r"|page|sheet|export|email|message|note|text|record|ticket|comment)\b[^.\n]{0,30}"
        r"\b(?:is|has been|gets)\s+(?:\w+\s+)?(?:read|opened|processed|summarized|parsed|loaded"
        r"|checked|reviewed)\b"
        r"|\bas this (?:file|note|page|document|message|sheet) "
        r"(?:directs|instructs|says|requires)\b",
    ),
    (
        # ties an action to something that has not happened yet. Weak: schedules, procedures and
        # changelogs say the same of people and programs
        "deferred_text",
        r"\b(?:the )?next time (?:you|the user|a user|someone|anyone|somebody|it|this|an?|the"
        r"|any)\b"
        r"|\bwhen(?:ever)? (?:you|the user) next\b"
        r"|\b(?:on|at|during|in) (?:the|your|its|their) next (?:run|session|message|reply"
        r"|response|request|turn|invocation|build|deploy(?:ment)?|log-?in|sync|start-?up|start"
        r"|launch|commit|push|update|conversation|task|job|release)\b"
        r"|\b(?:whenever|every time|each time|any ?time|as soon as|the moment)\s+"
        r"(?:an?|the|any|another|new|some)\s+(?:\w+\s+){0,3}(?:arrives?|comes? in|is received"
        r"|is opened|is created|is uploaded|is submitted|is filed|appears?|is mentioned"
        r"|logs? in|signs? in|asks?|requests?|is merged|is run|starts?)\b",
    ),
    (
        # second-person orders; weak, since documentation for people reads the same way
        "directive_text",
        r"\byou (?:must|should|shall|need to|have to|are (?:required|instructed) to|will now)\b"
        r"|^\s*[-#*>]?\s*you,\s",
    ),
    (
        "authority_text",
        r"</?\s*(?:system|developer|admin|instructions?|important)\s*>"
        r"|\[\s*(?:system|developer|admin)\s*(?:message|note|prompt|override)?\s*\]"
        r"|^\s*#{0,3}\s*(?:system|developer)\s+(?:prompt|message|note|override)\b"
        r"|\b(?-i:SYSTEM|ADMIN|DEVELOPER)\s*:"
        r"|\b(?:this is|message from|on behalf of|authorized by|approved by)\s+(?:the\s+|your\s+)?"
        r"(?:user|system|developer|administrator|admin|owner|security team)\b"
        r"|\bthe user (?:has |already ){0,2}"
        r"(?:authorized|approved|consented|agreed|wants you to)\b",
    ),
    (
        # says that someone, whoever it is, has already approved or cleared the thing. Weak: a
        # review record says the same
        "approval_text",
        r"\b(?:has|have|had)\s+(?:already\s+|previously\s+|just\s+|now\s+)?(?:approved"
        r"|authori[sz]ed|signed off on|cleared|green-?lit|okayed|sanctioned|consented to)\s+"
        r"(?:this|that|it|the|these|those|your|all)\b"
        r"|\b(?:this|that|it|the\s+\w+(?:\s+\w+)?)\s+(?:has been|have been|was|were|is)\s+"
        r"(?:already\s+|previously\s+|fully\s+)?(?:pre-?approved|approved|pre-?authori[sz]ed"
        r"|authori[sz]ed|signed off|pre-?cleared|cleared|green-?lit|sanctioned)\b"
        r"|\b(?:approval|authori[sz]ation|sign-?off|permission|consent|clearance)\s+"
        r"(?:has\s+(?:already\s+)?been|was|is)\s+(?:already\s+)?(?:granted|given|obtained"
        r"|received|on file|in place)\b"
        r"|\b(?:already|pre)[ -]?(?:approved|authori[sz]ed|cleared)\b"
        r"|\bstanding (?:approval|authori[sz]ation|permission)\b",
    ),
    (
        "concealment_text",
        r"\b(?:do not|don't|never|without)\s+(?:tell(?:ing)?|mention(?:ing)?|reveal(?:ing)?"
        r"|inform(?:ing)?|show(?:ing)?|disclos(?:e|ing)|notify(?:ing)?|alert(?:ing)?|ask(?:ing)?)\b"
        r"[^.\n]{0,30}\b(?:user|human|anyone|developer|owner|maintainer|this|it)\b"
        r"|\bkeep (?:this|it) (?:secret|hidden|quiet|confidential|between us)\b"
        r"|\b(?:omit|exclude|leave (?:this|it) out)\b[^.\n]{0,30}\b(?:summary|report|response"
        r"|answer|output|logs?)\b|\b(?:silently|secretly|covertly|discreetly)\b",
    ),
    (
        "bypass_text",
        r"\b(?:skip|bypass|disable|turn off|circumvent|suppress)\b[^.\n]{0,30}\b(?:approvals?"
        r"|confirmations?|permissions?|sandbox(?:ing)?|safety|security|guardrails?|hooks?"
        r"|checks?|review)\b"
        r"|--dangerously-[\w-]+|--yolo\b|bypassPermissions|danger-full-access|--no-verify\b"
        r"|\bwithout (?:asking|confirmation|approval|permission|prompting)\b"
        r"|\b(?:auto-?approve|approve (?:all|every))\b"
        # a check declared unnecessary: "no confirmation is needed", "no need to ask"
        r"|\bno (?:further |additional |more |extra |other )?(?:confirmation|approval|review"
        r"|verification|authori[sz]ation|permission|sign-?off|check|question)s?\s+"
        r"(?:(?:is|are|will be|should be)\s+)?(?:needed|required|necessary)\b"
        r"|\b(?:no need|not necessary|unnecessary|needn't|need not|(?:do|does)(?: not|n't) need)"
        r" to (?:ask|confirm|check|verify|review|wait|seek|request|get|obtain)\b",
    ),
    (
        "persistence_text",
        r"\b(?:add|append|write|save|insert|put|register|install|update|modify|edit|create)\b"
        r"[^.\n]{0,60}(?:CLAUDE\.md|AGENTS\.md|GEMINI\.md|\.cursorrules|\.mcp\.json"
        r"|settings(?:\.local)?\.json|config\.toml|hooks?\.json|\.claude\b|\.codex\b|\.bashrc"
        r"|\.zshrc|\.profile\b|crontab|authorized_keys|\.git/hooks|git config|mcp server"
        r"|system prompt|your (?:memory|instructions|configuration|rules))"
        r"|\b(?:in|for) (?:all )?(?:future|later|subsequent|every) (?:sessions?|conversations?"
        r"|runs?|tasks?)\b",
    ),
    (
        "exfiltration_text",
        r"\b(?:send|post|upload|forward|email|transmit|exfiltrate|submit|leak|share|push|append"
        r"|include|embed|encode)\b[^.\n]{0,80}\b(?:to|at|into|in|via)\b[^.\n]{0,20}"
        r"(?:https?://|ftp://|[\w.-]+@[\w-]+\.\w+|\bwebhook\b|\bpastebin\b|\bgist\b|\burl\b)"
        r"|!\[[^\]\n]*\]\(https?://[^)\s]*[?&=][^)\s]*\)"
        r"|\bcurl\b[^\n]{0,80}\s(?:-d|--data\S*|-F|-T|--upload-file)\b",
    ),
    (
        "secret_text",
        r"\b(?:read|print|show|cat|dump|send|share|reveal|include|collect|copy|export|output"
        r"|display|fetch|retrieve|extract)\b[^.\n]{0,60}(?:\bapi[ _-]?keys?\b|\btokens?\b"
        r"|\bpasswords?\b|\bcredentials?\b|\bsecrets?\b|\bprivate keys?\b|\bssh keys?\b|\.env\b"
        r"|\benvironment variables\b|\.aws\b|\.ssh\b|id_rsa|keychain|\bcookies\b"
        r"|system prompt|hidden instructions)",
    ),
)
# The rules above keep a phrase inside one sentence with [^.\n]. A dot followed by more of the same
# word (a path, a host, a file name) does not end a sentence, so the gap may run through it.
_GAP = r"(?:[^.\n]|\.(?=\S))"
_TEXT_FLAGS = tuple(
    (flag, re.compile(pattern.replace(r"[^.\n]", _GAP), re.I | re.M))
    for flag, pattern in _TEXT_FLAGS
)


# A weak rule and the stronger rule it is the broad form of. Where both match the same wording
# the stronger has spoken and the weak match is dropped. Matches that do not overlap are separate
# evidence and both stand.
BROAD_FORM_OF = {
    "called_text": "addressed_text",
    "third_person_text": "addressed_text",
    "approval_text": "authority_text",
    "deferred_text": "trigger_text",
}


def wording_rules():
    """The wording rules as plain data: what a hash of them has to cover."""
    rules = [[flag, pattern.pattern, pattern.flags] for flag, pattern in _TEXT_FLAGS]
    return {"rules": rules, "broad_form_of": dict(sorted(BROAD_FORM_OF.items()))}


def text_spans(text):
    """(start, end, flag, matched wording) for each piece of agent-steering wording in text.

    start and end are offsets in text. The wording is for display: whitespace collapsed and
    cut to 80 characters, so its length says nothing about where the match ends.

    A weak rule's match is left out where it overlaps a match of the rule it is the broad form
    of. The matches of one rule come in order and do not overlap, so their ends ascend and the
    one stronger match that could overlap a given span is found by bisection: the work grows
    with the matches, not with their square, however long the text.
    """
    matches = {
        flag: [
            (m.start(), m.end(), " ".join(m.group().split())[:80]) for m in pattern.finditer(text)
        ]
        for flag, pattern in _TEXT_FLAGS
    }
    ends = {}  # by stronger rule: where each of its matches ends
    found = []
    for flag, spans in matches.items():
        strong = matches.get(BROAD_FORM_OF.get(flag)) if spans else None
        if strong:
            closes = ends.setdefault(BROAD_FORM_OF[flag], [end for _, end, _ in strong])
            kept = []
            for start, end, basis in spans:
                at = bisect.bisect_right(closes, start)  # the first that ends after this starts
                if at == len(strong) or strong[at][0] >= end:
                    kept.append((start, end, basis))
            spans = kept
        found += [(start, end, flag, basis) for start, end, basis in spans]
    return found


def text_flags(text):
    """(start, flag, matched wording) for each kind of agent-steering wording found in text."""
    return [(start, flag, basis) for start, _, flag, basis in text_spans(text)]


def path_mentions(text, cwd="", home=""):
    """(start, flag, path) for sensitive or persistent paths that text names."""
    found = []
    for match in _WORD.finditer(text):
        word = match.group().lstrip("`*[").rstrip("`*],:;.")
        known = word.lower() in _AGENT_NAMES or word.lower() in _CI_NAMES
        if not (known or "/" in word or word.startswith(("~", "."))) or "://" in word:
            continue
        path = expand(word, cwd, home)
        classes = path_classes(path, "", home)
        if "sensitive" in classes:
            found.append((match.start(), "sensitive_reference", path))
        elif classes & PERSISTENT:
            found.append((match.start(), "config_reference", path))
    return found


# --- observables -------------------------------------------------------------------------------
# Hosts, addresses, URLs and mailboxes named anywhere in the session: candidates for indicators of
# compromise. A name without a scheme counts as a host only under a common top-level domain, which
# keeps file names and attribute access (config.yml, os.path) out.

_TLDS = frozenset(
    "com net org io dev ai co app xyz info biz us uk de fr ru cn jp br au ca nl eu edu gov cloud "
    "online site top club shop tech link live tk ml ga cf gq pw cc ws su ly gg tv example invalid "
    "test local onion internal lan corp".split()
)
_BARE_HOST = re.compile(
    r"(?<![\w@./-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+([a-z]{2,24}))(?![\w-]|\.[a-z0-9])",
    re.I,
)
_OCTET = r"(?:25[0-5]|2[0-4]\d|1?\d?\d)"
_IPV4 = re.compile(rf"(?<![\w.])(?:{_OCTET}\.){{3}}{_OCTET}(?![\w.])")
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@(?:[\w-]+\.)+[a-z]{2,24}(?![\w-]|\.[a-z]|:)", re.I)


def _blank(match):
    return " " * len(match.group())


def _is_address(host):
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def observables(text):
    """(start, kind, value) for each url, domain, ip and email named in text."""
    found = []
    for match in _URL.finditer(text):
        url = match.group().rstrip(".,;:!?'\"")
        found.append((match.start(), "url", url[:300]))
        for host in hosts(url):
            found.append((match.start(), "ip" if _is_address(host) else "domain", host))
    rest = _URL.sub(_blank, text)  # blanked, not removed, so later offsets still line up
    for match in _SCP_HOST.finditer(rest):
        found.append((match.start(), "domain", match[1].lower()))
    for match in _EMAIL.finditer(rest):
        found.append((match.start(), "email", match.group().lower()))
    rest = _EMAIL.sub(_blank, rest)
    for match in _BARE_HOST.finditer(rest):
        if match[2].lower() in _TLDS:
            found.append((match.start(), "domain", match[1].lower()))
    for match in _IPV4.finditer(rest):
        found.append((match.start(), "ip", match.group()))
    return found


_INSTALLERS = re.compile(
    r"\b(?:(npm|pnpm|yarn|bun)\s+(?:i|install|add)|(npx|bunx)|(pip[x3]?)\s+install"
    r"|(uv)\s+(?:pip\s+install|add|tool\s+install)|(poetry)\s+add|(cargo)\s+(?:install|add)"
    r"|(gem)\s+install|(brew)\s+install|(go)\s+(?:install|get))\s+([^;&|\n]+)"
)
_MANAGERS = {"pnpm": "npm", "yarn": "npm", "bun": "npm", "npx": "npm", "bunx": "npm"}
_MANAGERS |= {"pip3": "pip", "pipx": "pip", "uv": "pip", "poetry": "pip"}


def packages(command):
    """Package names a command installs or runs, as "manager:name"."""
    found = set()
    for match in _INSTALLERS.finditer(command):
        tool = next(group for group in match.groups()[:-1] if group)
        manager = _MANAGERS.get(tool, tool)
        skip = False
        for word in match.groups()[-1].split():
            word = word.strip("\"'")
            if skip or word.startswith(("-", ".", "/", "~", "$")) or "=" in word[:2]:
                skip = word in ("-r", "-c", "-e", "--requirement", "--constraint", "--editable")
                continue
            name = re.split(r"[=<>!~\[;]|(?<=.)@", word)[0]
            if re.fullmatch(r"@?[\w.-]+(?:/[\w.-]+)*", name):
                found.add(f"{manager}:{name}")
            if tool in ("npx", "bunx"):  # what follows the package is its own argument list
                break
    return found


# --- user requests -----------------------------------------------------------------------------
# Wording that makes a request worth keeping in a short digest: it limits, permits or withdraws
# something. These only decide what is retained; what a request means is the detector's to judge.

_PROMPT_NOTES = (
    (
        "constraint",
        r"\b(?:do not|don't|never|must not|mustn't|should not|shouldn't|cannot|can't|only|except"
        r"|without|avoid|refrain|not allowed|no \w+ing|leave \w+(?: \w+)? alone"
        r"|keep \w+ private)\b",
    ),
    (
        "approval",
        r"\b(?:yes|go ahead|approved?|you may|you can|allowed|ok(?:ay)? to|proceed|confirmed?"
        r"|permission|authoriz\w+)\b",
    ),
    (
        # makes something depend on an outcome: the result that settles it must stay available
        "condition",
        r"\b(?:only if|only when|only after|unless|provided (?:that)?|as long as"
        r"|(?:if|when|once|after)\b[^.\n]{0,40}\b(?:pass(?:es|ed)?|succeeds?|green|clean|approved?"
        r"|finish(?:es|ed)?|done|ok(?:ay)?|fails?|red))\b",
    ),
    (
        "revocation",
        r"\b(?:cancel|revert|undo|never mind|no longer|not anymore|abort|stop (?:doing|that|it)"
        r"|instead of that|scratch that)\b",
    ),
)
_PROMPT_NOTES = tuple((note, re.compile(pattern, re.I)) for note, pattern in _PROMPT_NOTES)


def prompt_notes(text):
    """Which of constraint, approval and revocation wording a user request contains."""
    return [note for note, pattern in _PROMPT_NOTES if pattern.search(text)]


def noted_sentences(text, limit=4):
    """The sentences of a request that carry such wording, so a long request can show them."""
    found = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        if sentence.strip() and any(pattern.search(sentence) for _, pattern in _PROMPT_NOTES):
            found.append(sentence.strip())
    return found[:limit]
