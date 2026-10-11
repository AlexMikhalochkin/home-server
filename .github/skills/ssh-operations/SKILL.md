---
name: ssh-operations
description: Use whenever the user asks to connect to their working server over SSH, inspect it, run remote commands, or make a change there. Use the explicitly named host or default to SSH alias chris. Read-only work is allowed; requested reversible changes can be performed directly, while destructive, broad-impact, security-sensitive, or hard-to-undo changes require separate approval.
---

# SSH operations

Use the user's existing SSH configuration and authentication. If the user does not name a host, connect to `chris`. If they name another host, use that host; do not guess or try alternatives.

## Connect and inspect

- Prefer a bounded remote command, such as `ssh HOST 'hostname && uptime'`, over an interactive shell.
- Use SSH keys and host-key checking already configured on the machine. Never bypass host-key checks, pass passwords in commands, or expose credentials, private keys, tokens, or sensitive command output.
- Read-only commands are allowed by default and should stay within the user's request. If access fails, report the error plainly; do not switch hosts or weaken authentication.
- Before changing remote state, run a read-only identity check and confirm it is the intended host.

## Make changes safely

- Perform a specific, reversible change when the user requests it. Inspect the target first, preserve existing content, and back up an existing file before editing it when practical. Removing an exact disposable artifact created during the current task is part of that task; deleting other data is not.
- Require a separate, explicit approval before destructive, broad-impact, security-sensitive, or hard-to-undo work. This includes deleting or pruning existing data, modifying disks or filesystems, rebooting or shutting down, changing SSH access, users, keys, permissions, firewall or routing, changing secrets, upgrading packages, or deploying/recreating a broad service stack.
- Before pausing for approval, state the target host, exact proposed action, likely impact, and rollback or recovery path. Do not run the risky command while awaiting approval.
- Use elevated privileges only when needed for the requested task. If authentication requires credentials the user has not supplied through an approved mechanism, stop and ask the user to handle it.
- After a change, verify the requested state. If verification fails, report that clearly and do not claim success.

## Report back

Give a concise summary of the host, actions and results, any changes and backups, and how the result was verified. Redact secrets and omit unrelated output.
