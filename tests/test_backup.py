import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from jinja2 import Environment, StrictUndefined
import yaml


ROOT = Path(__file__).resolve().parents[1]
JINJA = Environment(undefined=StrictUndefined)
JINJA.filters["bool"] = lambda value: str(value).lower() in ("true", "yes", "1")


def load(path):
    return yaml.safe_load((ROOT / path).read_text())


SCRIPT = ROOT / "ansible/roles/backup/files/home-server-backup"
METRICS = "home_server_backup.prom"
CHECK_METRICS = "home_server_backup_check.prom"

FAKE_DOCKER = """#!/bin/bash
echo "docker $*" >> "$CAPTURE"
case "$1" in
  ps) for name in $FAKE_RUNNING; do echo "$name"; done ;;
  stop|start) exit "${FAKE_DOCKER_RC:-0}" ;;
esac
"""

FAKE_RESTIC = """#!/bin/bash
echo "restic $*" >> "$CAPTURE"
echo "$RESTIC_REPOSITORY" > "$CAPTURE.repository"
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  if [ "${args[i]}" = "--files-from" ]; then cp "${args[i+1]}" "$CAPTURE.files-from"; fi
done
case "$1" in
  cat) exit "${FAKE_REPO_EXISTS_RC:-0}" ;;
  backup)
    sleep "${FAKE_BACKUP_SLEEP:-0}"
    echo '{"message_type":"status","percent_done":1}'
    echo '{"message_type":"summary","data_added":1234,"snapshot_id":"snap123"}'
    exit "${FAKE_BACKUP_RC:-0}" ;;
  check) exit "${FAKE_CHECK_RC:-0}" ;;
  dump) printf '%s' "$FAKE_DUMP" ;;
esac
"""


def metrics(path):
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#"):
            name, value = line.split()
            values[name] = float(value)
    return values


class BackupScriptTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.tmp = Path(self.directory.name)
        self.config = self.tmp / "config"
        self.textfile = self.tmp / "textfile"
        self.state = self.tmp / "state"
        self.data = self.tmp / "data"
        bin_dir = self.tmp / "bin"
        for directory in (self.config, self.textfile, self.state, self.data, bin_dir):
            directory.mkdir()
        for name, content in (("docker", FAKE_DOCKER), ("restic", FAKE_RESTIC)):
            executable = bin_dir / name
            executable.write_text(content)
            executable.chmod(0o755)
        self.capture = self.tmp / "calls"
        self.capture.touch()
        self.write_config(paths=[str(self.data)], quiesce=["home-assistant"])
        self.environment = os.environ | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME_SERVER_BACKUP_CONFIG_DIR": str(self.config),
            "CAPTURE": str(self.capture),
        }

    def tearDown(self):
        self.directory.cleanup()

    def write_config(self, paths, quiesce, excludes=("*.log",)):
        (self.config / "env").write_text(
            "RESTIC_REPOSITORY=s3:s3.example.com/bucket/chris\nRESTIC_PASSWORD=secret\n"
        )
        (self.config / "paths").write_text("".join(f"{path}\n" for path in paths))
        (self.config / "excludes").write_text("".join(f"{item}\n" for item in excludes))
        (self.config / "quiesce-containers").write_text("".join(f"{n}\n" for n in quiesce))
        (self.config / "backup.conf").write_text(
            "KEEP_DAILY=7\nKEEP_WEEKLY=4\nKEEP_MONTHLY=6\nCHECK_READ_DATA_SUBSET=5%\n"
            f"TEXTFILE_DIR={self.textfile}\nSTATE_DIR={self.state}\n"
        )

    def run_cli(self, command, **fake):
        return subprocess.run(
            [sys.executable, str(SCRIPT), command],
            env=self.environment | {key.upper(): str(value) for key, value in fake.items()},
            text=True, capture_output=True, timeout=30,
        )

    def calls(self):
        return self.capture.read_text().splitlines()

    def test_backup_stops_only_running_quiesced_containers_and_restarts_them(self):
        self.write_config(paths=[str(self.data)],
                          quiesce=["home-assistant", "grafana", "zigbee2mqtt"])
        result = self.run_cli("backup", fake_running="home-assistant grafana traefik")
        self.assertEqual(result.returncode, 0, result.stderr)
        container_calls = [call for call in self.calls()
                           if call.startswith(("docker stop", "docker start", "restic backup"))]
        self.assertEqual(container_calls[0], "docker stop --time 120 home-assistant grafana")
        self.assertTrue(container_calls[1].startswith("restic backup"))
        self.assertEqual(container_calls[2], "docker start home-assistant grafana")
        self.assertEqual(len(container_calls), 3)

    def test_backup_uses_paths_excludes_canary_and_tag(self):
        result = self.run_cli("backup", fake_running="")
        self.assertEqual(result.returncode, 0, result.stderr)
        backup = next(call for call in self.calls() if call.startswith("restic backup"))
        arguments = backup.split()
        self.assertIn("--files-from", arguments)
        self.assertIn(f"--exclude-file {self.config / 'excludes'}", backup)
        self.assertIn("--tag nightly", backup)
        self.assertIn("--json", arguments)
        self.assertIn(str(self.state / "canary"), arguments)
        self.assertTrue((self.state / "canary").read_text().strip())
        self.assertEqual(Path(f"{self.capture}.repository").read_text().strip(),
                         "s3:s3.example.com/bucket/chris")

    def test_backup_skips_missing_paths(self):
        missing = self.tmp / "does-not-exist"
        self.write_config(paths=[str(self.data), str(missing)], quiesce=[])
        result = self.run_cli("backup", fake_running="")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(f"{self.capture}.files-from").read_text().split(),
                         [str(self.data)])
        self.assertIn(str(missing), result.stderr)
        self.assertEqual(metrics(self.textfile / METRICS)["home_server_backup_missing_paths"], 1)

    def test_backup_initialises_missing_repository_once(self):
        result = self.run_cli("backup", fake_running="", fake_repo_exists_rc=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        restic = [call.split()[1] for call in self.calls() if call.startswith("restic")]
        self.assertEqual(restic.count("init"), 1)
        self.assertLess(restic.index("init"), restic.index("backup"))

        self.capture.write_text("")
        result = self.run_cli("backup", fake_running="", fake_repo_exists_rc=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("init", [call.split()[1] for call in self.calls()
                                  if call.startswith("restic")])

    def test_backup_applies_retention_after_success(self):
        result = self.run_cli("backup", fake_running="home-assistant")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        forget = [index for index, call in enumerate(calls) if call.startswith("restic forget")]
        self.assertEqual(len(forget), 1)
        self.assertEqual(calls[forget[0]],
                         "restic forget --prune --keep-daily 7 --keep-weekly 4 "
                         "--keep-monthly 6 --tag nightly")
        self.assertGreater(forget[0], calls.index("docker start home-assistant"))

    def test_failed_or_partial_backup_restarts_containers_and_skips_retention(self):
        for code in (1, 3):
            with self.subTest(code=code):
                self.capture.write_text("")
                (self.textfile / METRICS).write_text(
                    "home_server_backup_last_success_timestamp_seconds 1700000000\n"
                )
                result = self.run_cli("backup", fake_running="home-assistant",
                                      fake_backup_rc=code)
                self.assertEqual(result.returncode, code)
                calls = self.calls()
                self.assertIn("docker start home-assistant", calls)
                self.assertFalse(any(call.startswith("restic forget") for call in calls))
                values = metrics(self.textfile / METRICS)
                self.assertEqual(values["home_server_backup_last_exit_code"], code)
                self.assertEqual(values["home_server_backup_last_success_timestamp_seconds"],
                                 1700000000)

    def test_missing_or_corrupt_previous_metrics_defaults_last_success_to_zero(self):
        for previous in (None, "garbage line\nhome_server_backup_last_success_timestamp_seconds x\n"):
            with self.subTest(previous=previous):
                path = self.textfile / METRICS
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(previous)
                result = self.run_cli("backup", fake_running="", fake_backup_rc=1)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(
                    metrics(path)["home_server_backup_last_success_timestamp_seconds"], 0
                )

    def test_successful_backup_writes_metrics(self):
        result = self.run_cli("backup", fake_running="")
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.textfile / METRICS
        values = metrics(path)
        self.assertEqual(values["home_server_backup_last_exit_code"], 0)
        self.assertEqual(values["home_server_backup_bytes_added"], 1234)
        self.assertAlmostEqual(values["home_server_backup_last_success_timestamp_seconds"],
                               time.time(), delta=5)
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        self.assertEqual(sorted(p.name for p in self.textfile.iterdir()), [METRICS])

    def test_sigterm_during_backup_restarts_containers(self):
        process = subprocess.Popen(
            [sys.executable, str(SCRIPT), "backup"],
            env=self.environment | {"FAKE_RUNNING": "home-assistant", "FAKE_BACKUP_SLEEP": "5"},
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        deadline = time.time() + 10
        while not any(call.startswith("restic backup") for call in self.calls()):
            self.assertLess(time.time(), deadline, "backup never started")
            time.sleep(0.05)
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=10)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(self.calls()[-1], "docker start home-assistant")
        self.assertNotEqual(metrics(self.textfile / METRICS)["home_server_backup_last_exit_code"], 0)

    def test_check_passes_when_canary_matches(self):
        (self.state / "last-success.json").write_text(json.dumps(
            {"snapshot_id": "snap123", "canary": "2026-10-30T04:00:00Z abc"}))
        result = self.run_cli("check", fake_dump="2026-10-30T04:00:00Z abc")
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertIn("restic check --read-data-subset=5%", calls)
        self.assertIn(f"restic dump snap123 {self.state / 'canary'}", calls)
        self.assertEqual(
            metrics(self.textfile / CHECK_METRICS)["home_server_backup_check_last_exit_code"], 0
        )

    def test_check_fails_when_canary_differs(self):
        (self.state / "last-success.json").write_text(json.dumps(
            {"snapshot_id": "snap123", "canary": "2026-10-30T04:00:00Z abc"}))
        result = self.run_cli("check", fake_dump="2026-10-29T04:00:00Z old")
        self.assertEqual(result.returncode, 10)
        self.assertEqual(
            metrics(self.textfile / CHECK_METRICS)["home_server_backup_check_last_exit_code"], 10
        )

    def test_check_fails_when_no_backup_ever_succeeded(self):
        result = self.run_cli("check")
        self.assertEqual(result.returncode, 10)
        self.assertFalse(any(call.startswith("restic dump") for call in self.calls()))

    def test_successful_backup_records_snapshot_and_canary_for_check(self):
        result = self.run_cli("backup", fake_running="")
        self.assertEqual(result.returncode, 0, result.stderr)
        recorded = json.loads((self.state / "last-success.json").read_text())
        self.assertEqual(recorded["snapshot_id"], "snap123")
        self.assertEqual(recorded["canary"], (self.state / "canary").read_text().strip())

    def test_failed_backup_keeps_previous_check_reference(self):
        previous = json.dumps({"snapshot_id": "old", "canary": "old canary"})
        (self.state / "last-success.json").write_text(previous)
        result = self.run_cli("backup", fake_running="", fake_backup_rc=3)
        self.assertEqual(result.returncode, 3)
        self.assertEqual((self.state / "last-success.json").read_text(), previous)

    def test_unexpected_error_is_reported_as_failure(self):
        (self.config / "backup.conf").write_text(
            "KEEP_DAILY=7\nKEEP_WEEKLY=4\nKEEP_MONTHLY=6\nCHECK_READ_DATA_SUBSET=5%\n"
            f"TEXTFILE_DIR={self.textfile}\nSTATE_DIR={self.tmp / 'state-is-a-file'}\n"
        )
        (self.tmp / "state-is-a-file").write_text("not a directory")
        (self.textfile / METRICS).write_text(
            "home_server_backup_last_success_timestamp_seconds 1700000000\n")
        result = self.run_cli("backup", fake_running="home-assistant")
        self.assertNotEqual(result.returncode, 0)
        values = metrics(self.textfile / METRICS)
        self.assertNotEqual(values["home_server_backup_last_exit_code"], 0)
        self.assertEqual(values["home_server_backup_last_success_timestamp_seconds"], 1700000000)

    def test_unexpected_error_during_check_is_reported_as_failure(self):
        (self.state / "last-success.json").write_text("{not json")
        result = self.run_cli("check")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(
            metrics(self.textfile / CHECK_METRICS)["home_server_backup_check_last_exit_code"], 0
        )


class BackupRoleTests(unittest.TestCase):
    ROLE = ROOT / "ansible/roles/backup"

    def tasks(self):
        return load("ansible/roles/backup/tasks/main.yml")

    def defaults(self):
        return load("ansible/roles/backup/defaults/main.yml")

    def render_template(self, name, **overrides):
        variables = self.defaults() | {
            "backup_restic_repository": "s3:s3.eu-central-1.amazonaws.com/bucket/chris",
            "backup_restic_password": "restic-secret",
            "backup_aws_access_key_id": "AKIAEXAMPLE",
            "backup_aws_secret_access_key": "aws-secret",
        } | overrides
        return JINJA.from_string((self.ROLE / "templates" / name).read_text()).render(variables)

    def test_backup_role_never_runs_under_fast_deploy(self):
        play = next(play for play in load("ansible/github/playbooks/site.yml")
                    if play.get("hosts") == "home_servers")
        entry = next(role for role in play["roles"] if role.get("role") == "backup")
        self.assertEqual(entry["tags"], ["backup"])
        self.assertEqual(entry["when"], "backup_enabled | bool")
        for task in self.tasks():
            self.assertFalse({"deploy", "always"} & set(task.get("tags", [])), task["name"])

    def test_restic_env_is_root_only_and_never_logged(self):
        task = next(task for task in self.tasks()
                    if task.get("ansible.builtin.template", {}).get("src") == "restic.env.j2")
        attributes = task["ansible.builtin.template"]
        self.assertEqual(attributes["dest"], "/etc/restic/env")
        self.assertEqual(attributes["owner"], "root")
        self.assertEqual(attributes["mode"], "0600")
        self.assertIs(task["no_log"], True)
        rendered = self.render_template("restic.env.j2").splitlines()
        self.assertIn("RESTIC_REPOSITORY=s3:s3.eu-central-1.amazonaws.com/bucket/chris", rendered)
        self.assertIn("RESTIC_PASSWORD=restic-secret", rendered)
        self.assertIn("AWS_ACCESS_KEY_ID=AKIAEXAMPLE", rendered)
        self.assertIn("AWS_SECRET_ACCESS_KEY=aws-secret", rendered)

    def test_role_refuses_to_run_without_secrets(self):
        first = self.tasks()[0]
        conditions = " ".join(first["ansible.builtin.assert"]["that"])
        for variable in ("backup_restic_repository", "backup_restic_password",
                         "backup_aws_access_key_id", "backup_aws_secret_access_key",
                         "backup_paths"):
            self.assertIn(variable, conditions)
        self.assertIn("ansible/vault/secrets.yml", first["ansible.builtin.assert"]["fail_msg"])

    def test_backup_conf_carries_spec_retention(self):
        rendered = self.render_template("backup.conf.j2").splitlines()
        for line in ("KEEP_DAILY=7", "KEEP_WEEKLY=4", "KEEP_MONTHLY=6",
                     "CHECK_READ_DATA_SUBSET=5%",
                     "TEXTFILE_DIR=/var/lib/node-exporter/textfile",
                     "STATE_DIR=/var/lib/home-server-backup"):
            self.assertIn(line, rendered)

    def test_timers_match_spec(self):
        backup_timer = self.render_template("home-server-backup.timer.j2").splitlines()
        self.assertIn("OnCalendar=*-*-* 04:00:00", backup_timer)
        self.assertIn("Persistent=true", backup_timer)
        self.assertIn("RandomizedDelaySec=10m", backup_timer)
        check_timer = self.render_template("home-server-backup-check.timer.j2").splitlines()
        self.assertIn("OnCalendar=*-*-01 05:00:00", check_timer)
        self.assertIn("Persistent=true", check_timer)
        for unit, command in (("home-server-backup.service.j2", "backup"),
                              ("home-server-backup-check.service.j2", "check")):
            rendered = self.render_template(unit).splitlines()
            self.assertIn("Type=oneshot", rendered)
            self.assertIn(f"ExecStart=/usr/local/sbin/home-server-backup {command}", rendered)

    def test_backup_service_has_a_stop_timeout_and_low_priority(self):
        rendered = self.render_template("home-server-backup.service.j2").splitlines()
        for line in ("TimeoutStartSec=2h", "Nice=10", "IOSchedulingClass=idle",
                     "After=docker.service network-online.target"):
            self.assertIn(line, rendered)

    def test_restic_keeps_a_persistent_cache_under_systemd(self):
        for unit in ("home-server-backup.service.j2", "home-server-backup-check.service.j2"):
            rendered = self.render_template(unit).splitlines()
            self.assertIn("CacheDirectory=restic", rendered, unit)
            self.assertIn("Environment=RESTIC_CACHE_DIR=/var/cache/restic", rendered, unit)

    def test_installed_files_match_the_cli_contract(self):
        destinations = {}
        for task in self.tasks():
            for module in ("ansible.builtin.copy", "ansible.builtin.template"):
                if module in task:
                    destinations[task[module]["dest"]] = task[module]
        cli = destinations["/usr/local/sbin/home-server-backup"]
        self.assertEqual(cli["src"], "home-server-backup")
        self.assertEqual(cli["mode"], "0755")
        for name in ("paths", "excludes", "quiesce-containers", "backup.conf"):
            self.assertIn(f"/etc/restic/{name}", destinations)


class BackupObservabilityTests(unittest.TestCase):
    def test_node_exporter_reads_backup_textfile_metrics(self):
        command = load("docker-compose.yaml")["services"]["node-exporter"]["command"]
        self.assertIn("--collector.textfile.directory=/host/var/lib/node-exporter/textfile",
                      command)
        self.assertIn("--path.rootfs=/host", command)
        defaults = load("ansible/roles/backup/defaults/main.yml")
        self.assertEqual(f"/host{defaults['backup_textfile_dir']}",
                         "/host/var/lib/node-exporter/textfile")

    def test_backup_dashboard_is_provisioned(self):
        dashboard = json.loads(
            (ROOT / "grafana/provisioning/dashboards/backups.json").read_text())
        self.assertEqual(dashboard["uid"], "home-server-backups")
        panels = dashboard["panels"]
        expressions = [target["expr"] for panel in panels for target in panel["targets"]]
        for expression in ("time() - home_server_backup_last_success_timestamp_seconds",
                           "home_server_backup_last_exit_code",
                           "home_server_backup_check_last_exit_code",
                           "home_server_backup_missing_paths"):
            self.assertIn(expression, expressions)
        for panel in panels:
            self.assertEqual(panel["datasource"]["uid"], "prometheus", panel["title"])
            for target in panel["targets"]:
                self.assertEqual(target["datasource"]["uid"], "prometheus", panel["title"])
        age = next(panel for panel in panels
                   if panel["targets"][0]["expr"].startswith("time() - "))
        steps = age["fieldConfig"]["defaults"]["thresholds"]["steps"]
        self.assertIn({"color": "red", "value": 129600}, steps)
        missing = next(panel for panel in panels
                       if panel["targets"][0]["expr"] == "home_server_backup_missing_paths")
        self.assertIn({"color": "red", "value": 1},
                      missing["fieldConfig"]["defaults"]["thresholds"]["steps"])


class ChrisBackupConfigTests(unittest.TestCase):
    PLEX = "/opt/github-deploy/plex/config/Library/Application Support/Plex Media Server"
    JELLYFIN = "/var/lib/docker/volumes/home-server_jellyfin-config/_data"

    def host_vars(self):
        variables = load("ansible/github/inventory/host_vars/chris/main.yml")
        variables["service_paths"] = {"content": "/c", "media2": "/m2"}

        def resolve(value):
            if isinstance(value, str):
                for _ in range(5):
                    value = JINJA.from_string(value).render(variables)
                return value
            if isinstance(value, list):
                return [resolve(item) for item in value]
            if isinstance(value, dict):
                return {key: resolve(item) for key, item in value.items()}
            return value

        return {key: resolve(value) for key, value in variables.items()
                if key.startswith("backup_")}

    def test_chris_backs_up_current_state_locations(self):
        variables = self.host_vars()
        self.assertIs(variables["backup_enabled"], True)
        self.assertEqual(variables["backup_restic_repository"],
                         "s3:s3.eu-central-1.amazonaws.com/alex-home-servers-backups/chris")
        self.assertEqual(variables["backup_paths"], [
            "/opt/github-deploy/homeassistant/config",
            "/opt/github-deploy/home-server-configuration/home-assistant",
            "/opt/github-deploy/home-server-configuration/zigbee2mqtt",
            "/opt/github-deploy/home-server-configuration/qbittorrent",
            "/opt/github-deploy/plex/config",
            "/var/lib/docker/volumes/home-server_grafana-data/_data",
            "/var/lib/docker/volumes/home-server_jellyfin-config/_data",
            "/var/lib/docker/volumes/home-server_qbittorrent-data/_data",
            "/var/lib/docker/volumes/home-server_jackett-data/_data",
        ])

    def test_chris_excludes_rebuildable_data(self):
        excludes = self.host_vars()["backup_excludes"]
        for name in ("Cache", "Media", "Metadata", "Codecs", "Logs", "Crash Reports", "Drivers"):
            self.assertIn(f"{self.PLEX}/{name}", excludes)
        for path in (f"{self.JELLYFIN}/metadata", f"{self.JELLYFIN}/log",
                     "/opt/github-deploy/home-server-configuration/zigbee2mqtt/log",
                     "/opt/github-deploy/homeassistant/config/home-assistant.log*",
                     "/opt/github-deploy/homeassistant/config/backups"):
            self.assertIn(path, excludes)
        for kept in ("Plug-in Support", "Preferences.xml"):
            self.assertFalse(any(item.endswith(kept) for item in excludes))

    def test_chris_quiesces_state_owning_containers(self):
        self.assertEqual(set(self.host_vars()["backup_quiesce_containers"]), {
            "home-assistant", "zigbee2mqtt", "plex", "jellyfin", "qbittorrent", "jackett",
            "grafana",
        })


if __name__ == "__main__":
    unittest.main()
