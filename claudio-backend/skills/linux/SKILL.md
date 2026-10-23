---
name: linux
description: 'Linux command-line and system administration: shell and bash scripting,
  file and process management, permissions, services, networking, and performance
  triage.'
license: mixed — see Credits
metadata:
  last_reviewed: '2026-10-23'
  sources:
  - owner: github
    name: centos-linux-triage
    license: unknown
    url: https://skillmd.com/skills/github/centos-linux-triage
  - owner: antigravity
    name: bash-linux
    license: unknown
    url: https://skillmd.com/skills/antigravity/bash-linux
  - owner: dokhacgiakhoa
    name: bash-linux
    license: unknown
    url: https://skillmd.com/skills/dokhacgiakhoa/bash-linux
  - owner: neuralblitz
    name: bash
    license: MIT
    url: https://skillmd.com/skills/neuralblitz/bash
  - owner: github
    name: fedora-linux-triage
    license: unknown
    url: https://skillmd.com/skills/github/fedora-linux-triage
---

You are a CentOS Linux expert. Diagnose and resolve the user’s issue with RHEL-compatible commands and practices.

## Inputs

- `${input:CentOSVersion}` (optional)
- `${input:ProblemSummary}`
- `${input:Constraints}` (optional)

## Instructions

1. Confirm CentOS release (Stream vs. legacy) and environment assumptions.
2. Provide triage steps using `systemctl`, `journalctl`, `dnf`/`yum`, and logs.
3. Offer remediation steps with copy-paste-ready commands.
4. Include verification commands after each major change.
5. Address SELinux and `firewalld` considerations where relevant.
6. Provide rollback or cleanup steps.

## Output Format

- **Summary**
- **Triage Steps** (numbered)
- **Remediation Commands** (code blocks)
- **Validation** (code blocks)
- **Rollback/Cleanup**

> Essential patterns for Bash on Linux/macOS.

---

## 1. Operator Syntax

### Chaining Commands

| Operator | Meaning | Example |
|----------|---------|---------|
| `;` | Run sequentially | `cmd1; cmd2` |
| `&&` | Run if previous succeeded | `npm install && npm run dev` |
| `\|\|` | Run if previous failed | `npm test \|\| echo "Tests failed"` |
| `\|` | Pipe output | `ls \| grep ".js"` |

---

## 2. File Operations

### Essential Commands

| Task | Command |
|------|---------|
| List all | `ls -la` |
| Find files | `find . -name "*.js" -type f` |
| File content | `cat file.txt` |
| First N lines | `head -n 20 file.txt` |
| Last N lines | `tail -n 20 file.txt` |
| Follow log | `tail -f log.txt` |
| Search in files | `grep -r "pattern" --include="*.js"` |
| File size | `du -sh *` |
| Disk usage | `df -h` |

---

## 3. Process Management

| Task | Command |
|------|---------|
| List processes | `ps aux` |
| Find by name | `ps aux \| grep node` |
| Kill by PID | `kill -9 <PID>` |
| Find port user | `lsof -i :3000` |
| Kill port | `kill -9 $(lsof -t -i :3000)` |
| Background | `npm run dev &` |
| Jobs | `jobs -l` |
| Bring to front | `fg %1` |

---

## 4. Text Processing

### Core Tools

| Tool | Purpose | Example |
|------|---------|---------|
| `grep` | Search | `grep -rn "TODO" src/` |
| `sed` | Replace | `sed -i 's/old/new/g' file.txt` |
| `awk` | Extract columns | `awk '{print $1}' file.txt` |
| `cut` | Cut fields | `cut -d',' -f1 data.csv` |
| `sort` | Sort lines | `sort -u file.txt` |
| `uniq` | Unique lines | `sort file.txt \| uniq -c` |
| `wc` | Count | `wc -l file.txt` |

---

## 5. Environment Variables

| Task | Command |
|------|---------|
| View all | `env` or `printenv` |
| View one | `echo $PATH` |
| Set temporary | `export VAR="value"` |
| Set in script | `VAR="value" command` |
| Add to PATH | `export PATH="$PATH:/new/path"` |

---

## 6. Network

| Task | Command |
|------|---------|
| Download | `curl -O https://example.com/file` |
| API request | `curl -X GET https://api.example.com` |
| POST JSON | `curl -X POST -H "Content-Type: application/json" -d '{"key":"value"}' URL` |
| Check port | `nc -zv localhost 3000` |
| Network info | `ifconfig` or `ip addr` |

---

## 7. Script Template

```bash
#!/bin/bash
set -euo pipefail  # Exit on error, undefined var, pipe fail

# Colors (optional)
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Functions
log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

# Main
main() {
    log_info "Starting..."
    # Your logic here
    log_info "Done!"
}

main "$@"
```

---

## 8. Common Patterns

### Check if command exists

```bash
if command -v node &> /dev/null; then
    echo "Node is installed"
fi
```

### Default variable value

```bash
NAME=${1:-"default_value"}
```

### Read file line by line

```bash
while IFS= read -r line; do
    echo "$line"
done < file.txt
```

### Loop over files

```bash
for file in *.js; do
    echo "Processing $file"
done
```

---

## 9. Differences from PowerShell

| Task | PowerShell | Bash |
|------|------------|------|
| List files | `Get-ChildItem` | `ls -la` |
| Find files | `Get-ChildItem -Recurse` | `find . -type f` |
| Environment | `$env:VAR` | `$VAR` |
| String concat | `"$a$b"` | `"$a$b"` (same) |
| Null check | `if ($x)` | `if [ -n "$x" ]` |
| Pipeline | Object-based | Text-based |

---

## 10. Error Handling

### Set options

```bash
set -e          # Exit on error
set -u          # Exit on undefined variable
set -o pipefail # Exit on pipe failure
set -x          # Debug: print commands
```

### Trap for cleanup

```bash
cleanup() {
    echo "Cleaning up..."
    rm -f /tmp/tempfile
}
trap cleanup EXIT
```

---

> **Remember:** Bash is text-based. Use `&&` for success chains, `set -e` for safety, and quote your variables!

## When to Use
This skill is applicable to execute the workflow or actions described in the overview.

## Limitations
- Use this skill only when the task clearly matches the scope described above.
- Do not treat the output as a substitute for environment-specific validation, testing, or expert review.
- Stop and ask for clarification if required inputs, permissions, safety boundaries, or success criteria are missing.

## 1. Operator Syntax

## 🧠 Knowledge Modules (Fractal Skills)

### 1. [Chaining Commands](./sub-skills/chaining-commands.md)
### 2. [Essential Commands](./sub-skills/essential-commands.md)
### 3. [Core Tools](./sub-skills/core-tools.md)
### 4. [Check if command exists](./sub-skills/check-if-command-exists.md)
### 5. [Default variable value](./sub-skills/default-variable-value.md)
### 6. [Read file line by line](./sub-skills/read-file-line-by-line.md)
### 7. [Loop over files](./sub-skills/loop-over-files.md)
### 8. [Set options](./sub-skills/set-options.md)
### 9. [Trap for cleanup](./sub-skills/trap-for-cleanup.md)

## What I do
- Write bash scripts for automation
- Process text with awk and sed
- Use find and xargs effectively
- Handle command-line arguments
- Manage environment variables
- Implement error handling
- Create utility functions

## When to use me
When automating tasks, building CLI tools, or managing systems.

## Basics
```bash
#!/bin/bash

# Variables
name="World"
echo "Hello, $name!"

# Command substitution
current_date=$(date +%Y-%m-%d)
files=$(ls)

# Arrays
colors=("red" "green" "blue")
echo "${colors[0]}"
echo "${colors[@]}"

# Dictionaries (bash 4+)
declare -A config
config[host]="localhost"
config[port]="8080"
echo "${config[host]}"
```

## Conditionals
```bash
# If statement
if [ "$name" == "admin" ]; then
    echo "Welcome admin"
elif [ "$age" -ge 18 ]; then
    echo "Adult"
else
    echo "Minor"
fi

# File tests
if [ -f "$file" ]; then
    echo "Regular file"
fi
if [ -d "$dir" ]; then
    echo "Directory"
fi
if [ -z "$var" ]; then
    echo "Empty"
fi

# String comparison
if [[ "$str" == *"pattern"* ]]; then
    echo "Contains pattern"
fi
```

## Loops
```bash
# For loop
for i in {1..5}; do
    echo "Number: $i"
done

for file in *.txt; do
    echo "Processing $file"
done

# While loop
while read line; do
    echo "$line"
done < file.txt

# Iterate over array
for color in "${colors[@]}"; do
    echo "$color"
done
```

## Functions
```bash
function greet() {
    local name="$1"  # local variable
    echo "Hello, $name!"
}

greet "World"

# Return value
function get_sum() {
    local a=$1
    local b=$2
    echo $((a + b))
}

result=$(get_sum 5 10)
```

## Arguments
```bash
#!/bin/bash

# $@ = all arguments
# $# = argument count
# $0 = script name
# $1, $2, ... = arguments

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            help=true
            ;;
        -n|--name)
            name="$2"
            shift 2
            ;;
        -v|--verbose)
            verbose=true
            shift
            ;;
        *)
            echo "Unknown: $1"
            shift
            ;;
    esac
done
```

## Error Handling
```bash
set -e  # Exit on error
set -u  # Exit on undefined variable
set -o pipefail  # Pipeline fails on error

# Trap errors
trap 'echo "Error on line $LINENO"' ERR

# Check command success
if ! command -v git &> /dev/null; then
    echo "Git not found"
    exit 1
fi
```

## Text Processing
```bash
# Awk
awk -F',' '{print $1, $3}' file.csv
awk 'NR>1 {sum+=$2} END {print sum}' file.txt

# Sed
sed 's/old/new/g' file.txt
sed -i 's/old/new/g' file.txt  # in-place
sed '/pattern/d' file.txt  # delete lines with pattern

# Find
find . -name "*.txt" -type f
find . -mtime -7  # modified in last 7 days
find . -size +1M  # larger than 1MB

# xargs
find . -name "*.log" -type f | xargs rm
find . -name "*.txt" | xargs -I {} mv {} ./backup/
```

## Useful Patterns
```bash
# Check if command exists
command -v docker >/dev/null 2>&1 || { echo "Docker required"; exit 1; }

# Heredoc
cat << EOF > config.txt
name=$name
value=$value
EOF

# Parallel execution
cat hosts | xargs -P 10 -I {} ssh {} 'uptime'

# Progress bar
for i in {1..100}; do
    echo -ne "Progress: $i%\r"
    sleep 0.1
done
```

You are a Fedora Linux expert. Diagnose and resolve the user’s issue using Fedora-appropriate tooling and practices.

## Inputs

- `${input:FedoraRelease}` (optional)
- `${input:ProblemSummary}`
- `${input:Constraints}` (optional)

## Instructions

1. Confirm Fedora release and environment assumptions.
2. Provide a step-by-step triage plan using `systemctl`, `journalctl`, and `dnf`.
3. Offer remediation steps with copy-paste-ready commands.
4. Include verification commands after each major change.
5. Address SELinux and `firewalld` considerations where relevant.
6. Provide rollback or cleanup steps.

## Credits

- **github/centos-linux-triage** — license: unknown — https://skillmd.com/skills/github/centos-linux-triage
- **antigravity/bash-linux** — license: unknown — https://skillmd.com/skills/antigravity/bash-linux
- **dokhacgiakhoa/bash-linux** — license: unknown — https://skillmd.com/skills/dokhacgiakhoa/bash-linux
- **neuralblitz/bash** — license: MIT — https://skillmd.com/skills/neuralblitz/bash
- **github/fedora-linux-triage** — license: unknown — https://skillmd.com/skills/github/fedora-linux-triage
