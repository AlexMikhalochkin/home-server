import configparser
import json
import os
from pathlib import Path
import tempfile
import unittest

from test_infrastructure import JINJA, ROOT, TEMPLATES, compose, load, render, run, start_script


class MediaAccessTests(unittest.TestCase):
    def test_repository_contains_configuration_not_manual_migration_tools(self):
        role = ROOT / "ansible/roles/media_access"
        for name in ("migrate.yml", "apply.yml", "finalize.yml"):
            self.assertFalse((role / "tasks" / name).exists(), name)
        self.assertFalse((role / "files/media-permissions.py").exists())
        defaults = load("ansible/roles/media_access/defaults/main.yml")
        self.assertNotIn("media_access_migrate", defaults)
        workflow = load(".github/workflows/ansible-configure.yaml")
        dispatch = workflow.get("on", workflow.get(True))["workflow_dispatch"]
        self.assertNotIn("migrate_media_access", dispatch["inputs"])
        for path in ("ansible/roles/docker_stack/tasks/main.yml",
                     "ansible/roles/docker_stack/templates/start.sh.j2",
                     "ansible/roles/docker_stack/templates/storage-dependent-containers.service.j2"):
            contents = (ROOT / path).read_text()
            for token in ("media_access_state", "media_access_needs_migration", "tasks_from: finalize"):
                self.assertNotIn(token, contents, path)

    def test_media_identity_is_private_and_uses_os_assigned_ids(self):
        tasks = load("ansible/roles/media_access/tasks/main.yml")
        group = next(task["ansible.builtin.group"] for task in tasks if "ansible.builtin.group" in task)
        user = next(task["ansible.builtin.user"] for task in tasks if "ansible.builtin.user" in task)
        self.assertTrue(group["system"])
        self.assertNotIn("gid", group)
        self.assertTrue(user["system"])
        self.assertTrue(user["password_lock"])
        self.assertFalse(user["create_home"])
        self.assertEqual(user["shell"], "/usr/sbin/nologin")
        self.assertEqual(user["groups"], "")
        self.assertNotIn("uid", user)

    def test_only_full_chris_configure_prepares_media_before_git_and_samba(self):
        self.assertTrue(load("ansible/github/inventory/host_vars/chris/main.yml")["media_access_enabled"])
        self.assertFalse(load("ansible/github/inventory/host_vars/newton/main.yml").get("media_access_enabled", False))
        roles = [role["role"] for role in load("ansible/github/playbooks/site.yml")[1]["roles"]]
        self.assertLess(roles.index("docker_stack"), roles.index("samba"))
        tasks = load("ansible/roles/docker_stack/tasks/main.yml")
        identity = next(task for task in tasks
                        if task.get("ansible.builtin.include_role", {}).get("name") == "media_access")
        self.assertIn("prepare", identity["tags"])
        self.assertNotIn("deploy", identity["tags"])
        self.assertLess(tasks.index(identity), next(
            index for index, task in enumerate(tasks) if "ansible.builtin.git" in task
        ))

    def test_steady_state_tasks_do_not_recursively_rewrite_existing_media(self):
        for relative in ("ansible/roles/media_access/tasks/main.yml",
                         "ansible/roles/media_access/tasks/prepare.yml",
                         "ansible/roles/media_access/tasks/configuration.yml"):
            tasks = load(relative)
            self.assertNotIn("ansible.builtin.script", str(tasks))
            self.assertNotIn("docker, stop", str(tasks))
            self.assertNotIn("application-state.tar", str(tasks))
            for task in tasks:
                for module in ("ansible.builtin.file", "ansible.posix.acl"):
                    options = task.get(module, {})
                    self.assertFalse(options.get("recurse", options.get("recursive", False)), task["name"])

    def test_populated_roots_require_manual_preparation_but_empty_roots_allow_install(self):
        tasks = load("ansible/roles/media_access/tasks/prepare.yml")
        gate = next((task for task in tasks if task["name"] == "Require manual preparation of existing media data"), None)
        self.assertIsNotNone(gate)
        condition = gate["ansible.builtin.assert"]["that"][0]
        for private, populated, uid, gid, mode, expected in (
            (True, False, 0, 0, "0755", True),
            (True, True, 0, 0, "0755", False),
            (True, True, 912, 913, "0700", True),
            (False, True, 1000, 913, "2770", True),
            (False, True, 1000, 1000, "0755", False),
        ):
            with self.subTest(private=private, populated=populated, uid=uid, gid=gid):
                variables = {
                    "item": {"item": {"path": "/data", "private": private},
                             "stdout": "child" if populated else ""},
                    "media_access_root_stats": {"/data": {"uid": uid, "gid": gid, "mode": mode}},
                    "media_user_uid": 912, "media_user_gid": 913,
                }
                self.assertEqual(bool(JINJA.compile_expression(condition)(**variables)), expected)

    def test_git_never_discards_live_private_configuration(self):
        tasks = load("ansible/roles/docker_stack/tasks/main.yml")
        checkout = next(task for task in tasks
                        if task["name"] == "Clone or update home-server-configuration repository (private)")
        self.assertIn("ansible.builtin.git", checkout)
        self.assertFalse(checkout["ansible.builtin.git"]["force"])
        inventory = load("ansible/github/inventory/host_vars/chris/main.yml")
        self.assertEqual(inventory["docker_stack_start_script_path"], "{{ deploy_base_dir }}/start.sh")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name in ("home-server", "home-server-configuration"):
                (base / name).mkdir()
            executable = base / "git"
            executable.write_text(
                '#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALLS"\n'
                'if [ "$1" = diff ] && [ "${PWD##*/}" = home-server-configuration ]; then exit 1; fi\n'
            )
            executable.chmod(0o755)
            calls = base / "calls"
            result = run(["bash", "-c", start_script(base, home_server_configuration_enabled=True)],
                         env=os.environ | {"PATH": f"{base}:{os.environ['PATH']}", "CALLS": str(calls)})
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("restore", calls.read_text())
            self.assertNotIn("checkout", calls.read_text())
            self.assertIn("configuration", result.stderr)

    def test_known_qbittorrent_files_keep_git_and_media_acl_access(self):
        defaults = load("ansible/roles/media_access/defaults/main.yml")
        self.assertEqual({Path(path).name for path in defaults["media_access_repository_files"]},
                         {"categories.json", "qBittorrent.conf", "watched_folders.json"})
        path = ROOT / "ansible/roles/media_access/tasks/configuration.yml"
        self.assertTrue(path.is_file())
        tasks = load("ansible/roles/media_access/tasks/configuration.yml")
        acl = [task["ansible.posix.acl"] for task in tasks if "ansible.posix.acl" in task]
        self.assertTrue(any(item.get("entity") == "{{ deploy_user }}" and item["permissions"] == "rw"
                            for item in acl))
        self.assertTrue(any(item.get("default") for item in acl))
        self.assertTrue(any("realpath" in task.get("ansible.builtin.command", {}).get("argv", [])
                            for task in tasks))

    def test_samba_retains_authenticated_shared_group_policy(self):
        template = (ROOT / "ansible/roles/samba/templates/smb.conf.j2").read_text()
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.read_string(render(template, {
            "samba_workgroup": "WORKGROUP", "samba_user": "alex", "media_group": "media",
            "media_access_enabled": True,
            "samba_shares": [{"name": "downloads", "comment": "Downloads", "path": "/mnt/media/downloads"}],
        }))
        share = config["downloads"]
        self.assertEqual(share["valid users"], "alex")
        self.assertEqual(share["force user"], "alex")
        self.assertEqual(share["force group"], "media")
        self.assertEqual(share["guest ok"], "no")
        self.assertEqual(share["read only"], "no")
        self.assertEqual(int(share["force create mode"], 8), 0o660)
        self.assertEqual(int(share["force directory mode"], 8), 0o2770)

    def test_samba_only_configuration_resolves_media_group_without_prepare(self):
        result = run(["ansible-inventory", "--list"], cwd=ROOT / "ansible/github")
        self.assertEqual(result.returncode, 0, result.stderr)
        variables = json.loads(result.stdout)["_meta"]["hostvars"]["chris"]
        # Only show variable names on failure: inventory values may contain credentials.
        self.assertIn("media_group", variables.keys(), "Samba-only configuration must resolve the media group")
        self.assertEqual(variables["media_group"], "media")
        group = next(task["ansible.builtin.file"]["group"]
                     for task in load("ansible/roles/samba/tasks/main.yml")
                     if task["name"] == "Create Samba share directories")
        self.assertEqual(render(group, variables), "media")

    def test_final_compose_retains_dynamic_identities_and_writable_media(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            private = base / "private.yaml"
            private.write_text(
                "services:\n  qbittorrent:\n    image: linuxserver/qbittorrent:4.5.0\n"
                "    profiles: [chris]\n    environment: {PUID: '1000', PGID: '1000'}\n"
                "    ports: ['6004:8080']\n    volumes: ['${QBITTORRENT_ROOT_PATH}:/data']\n"
            )
            override = base / "media.yaml"
            template = (TEMPLATES / "media-compose.yaml.j2").read_text()
            override.write_text(render(template, {"private_home_server_enabled": True}))
            arguments = ("--env-file", ".env.example", "-f", "docker-compose.yaml",
                         "-f", str(private), "-f", str(override), "--profile", "*", "config")
            environment = os.environ | {
                "MEDIA_UID": "912", "MEDIA_GID": "913", "MEDIA_VIDEO_GID": "44",
                "MEDIA_RENDER_GID": "991", "QBITTORRENT_ROOT_PATH": "/mnt/media",
            }
            result = compose(*arguments, "--format", "json", env=environment)
            self.assertEqual(result.returncode, 0, result.stderr)
            services = json.loads(result.stdout)["services"]
            for service in ("plex", "qbittorrent"):
                self.assertEqual(services[service]["environment"]["PUID"], "912")
                self.assertEqual(services[service]["environment"]["PGID"], "913")
                self.assertEqual(services[service]["environment"]["UMASK"], "0007")
            self.assertIn("DAC_OVERRIDE", services["plex"].get("cap_add", []))
            self.assertEqual(services["plex"]["cap_drop"], ["ALL"])
            jellyfin = services["jellyfin"]
            self.assertEqual(jellyfin["user"], "912:913")
            self.assertEqual(set(map(str, jellyfin["group_add"])), {"44", "991"})
            mounts = [mount for mount in jellyfin["volumes"] if mount["target"] in ("/media", "/media2")]
            self.assertEqual(len(mounts), 2)
            self.assertTrue(all(not mount.get("read_only", False) for mount in mounts))
            state_mounts = [mount for mount in jellyfin["volumes"]
                            if mount["target"] in ("/config", "/cache")]
            self.assertEqual(len(state_mounts), 2)
            self.assertTrue(all(mount.get("volume", {}).get("nocopy", False) for mount in state_mounts),
                            "Docker must not replace prepared private volume ownership on first use")
            self.assertNotIn("cap_add", jellyfin)
            self.assertEqual(services["qbittorrent"]["ports"][0]["published"], "6004")
            self.assertEqual(services["home-assistant"]["network_mode"], "service:openvpn")
            self.assertNotIn("qbittorrent", render(template, {"private_home_server_enabled": False}))
            result = compose(*arguments, "--quiet", env={
                key: value for key, value in environment.items() if key != "MEDIA_UID"
            })
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("MEDIA_UID", result.stderr)


if __name__ == "__main__":
    unittest.main()
