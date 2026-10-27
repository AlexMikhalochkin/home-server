from fnmatch import fnmatchcase
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest

from jinja2 import Environment, StrictUndefined
import yaml


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "ansible/roles/docker_stack/templates"
JINJA = Environment(undefined=StrictUndefined)
JINJA.filters["bool"] = lambda value: str(value).lower() in ("true", "yes", "1")
JINJA.filters["quote"] = shlex.quote


def load(path):
    return yaml.safe_load((ROOT / path).read_text())


def render(value, variables):
    return JINJA.from_string(value).render(variables)


def run(args, **kwargs):
    return subprocess.run(args, text=True, capture_output=True, **kwargs)


def compose(*args, **kwargs):
    command = ["docker-compose"] if shutil.which("docker-compose") else ["docker", "compose"]
    return run([*command, *args], cwd=ROOT, **kwargs)


def start_script(base, **overrides):
    variables = {
        "deploy_branch": "fix/$HOME",
        "deploy_base_dir": str(base),
        "docker_compose_files": [str(base / "home-server/docker-compose.yaml")],
        "docker_compose_profiles": ["chris"],
        "home_server_configuration_enabled": False,
        "private_home_server_enabled": False,
        "docker_stack_storage_containers": [],
        "docker_stack_storage_mount_path": str(base / "media"),
        "docker_stack_storage_restart_policy": "on-failure",
    }
    return render((TEMPLATES / "start.sh.j2").read_text(), variables | overrides)


class InfrastructureTests(unittest.TestCase):
    def test_ci_python_installers_refuse_source_distributions(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name in ("python", "python3"):
                executable = base / name
                executable.write_text(
                    '#!/bin/bash\nprintf "%s\\0" "$@" >> "$CAPTURE"\n'
                    'printf "\\n" >> "$CAPTURE"\n'
                )
                executable.chmod(0o755)
            galaxy = base / "ansible-galaxy"
            galaxy.write_text("#!/bin/bash\nexit 0\n")
            galaxy.chmod(0o755)
            calls = base / "calls"
            for filename in ("ansible-configure.yaml", "deploy.yaml", "lint-docker.yaml"):
                workflow = load(f".github/workflows/{filename}")
                for job in workflow["jobs"].values():
                    for step in job.get("steps", []):
                        if step.get("name") != "Install Ansible":
                            continue
                        result = run(["bash", "-c", step["run"]], env=os.environ | {
                            "PATH": f"{base}:{os.environ['PATH']}", "CAPTURE": str(calls),
                        })
                        self.assertEqual(result.returncode, 0, result.stderr)
            installers = [line.split("\0")[:-1] for line in calls.read_text().splitlines()]
            self.assertEqual(len(installers), 5)
            for arguments in installers:
                with self.subTest(arguments=arguments):
                    self.assertEqual(arguments[:3], ["-m", "pip", "install"])
                    self.assertIn("--only-binary=:all:", arguments)

    def test_stack_data_defaults_deny_world_access_and_preserve_service_modes(self):
        task = next(task for task in load("ansible/roles/docker_stack/tasks/main.yml")
                    if task["name"] == "Create stack data directories")
        expression = task["ansible.builtin.file"]["mode"]
        default_mode = int(render(expression, {"item": {}}), 8)
        self.assertEqual(default_mode & 0o007, 0)
        self.assertEqual(default_mode & 0o770, 0o750)
        for service_mode in ("0700", "2770"):
            with self.subTest(mode=service_mode):
                self.assertEqual(render(expression, {"item": {"mode": service_mode}}), service_mode)

    def test_sonar_classifies_tests_separately_from_production_sources(self):
        properties = dict(
            line.split("=", 1) for line in (ROOT / "sonar-project.properties").read_text().splitlines()
            if line and not line.startswith("#")
        )
        self.assertEqual(properties.get("sonar.sources"), ".")
        self.assertEqual(properties.get("sonar.tests"), "tests")
        exclusions = properties.get("sonar.exclusions", "").split(",")
        for path in (ROOT / "tests").glob("*.py"):
            with self.subTest(path=path.name):
                relative = path.relative_to(ROOT).as_posix()
                self.assertTrue(any(fnmatchcase(relative, pattern) for pattern in exclusions),
                                "Tests must not also count as uncovered production files")

    def test_default_inventory_targets_only_chris(self):
        for directory in ("bootstrap", "github"):
            with self.subTest(directory=directory):
                result = run(
                    ["ansible-inventory", "--list"],
                    cwd=ROOT / "ansible" / directory,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    json.loads(result.stdout)["home_servers"]["hosts"], ["chris"]
                )

    def test_explicit_newton_inventory_targets_only_newton(self):
        for directory in ("bootstrap", "github"):
            with self.subTest(directory=directory):
                result = run(
                    ["ansible-inventory", "-i", "inventory/hosts-newton.yml", "--list"],
                    cwd=ROOT / "ansible" / directory,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    json.loads(result.stdout)["home_servers"]["hosts"], ["newton"]
                )

    def test_example_environment_renders_all_chris_profiles(self):
        result = compose(
            "--env-file", ".env.example", "-f", "docker-compose.yaml",
            "--profile", "*", "config", "--quiet",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("variable is not set", result.stderr)

    def test_newton_only_exposes_authenticated_http(self):
        result = compose(
            "--env-file", "/dev/null", "-f", "docker-compose.newton.yaml",
            "--profile", "newton", "config", "--format", "json",
            env=os.environ | {
                "FRIGATE_CONFIG_DIRECTORY": "/tmp/infrastructure-test/config",
                "FRIGATE_STORAGE_DIRECTORY": "/tmp/infrastructure-test/storage",
                "TIMEZONE": "UTC",
                "HOST_IP": "127.0.0.1",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        services = json.loads(result.stdout)["services"]
        self.assertEqual(set(services), {"frigate"})
        ports = {port["target"] for port in services["frigate"]["ports"]}
        self.assertIn(8971, ports)
        self.assertNotIn(5000, ports)

    def test_workflows_use_the_requested_branch_and_serialize_each_host(self):
        groups = []
        for filename in ("deploy.yaml", "ansible-configure.yaml"):
            with self.subTest(workflow=filename):
                workflow = load(f".github/workflows/{filename}")
                groups.append(workflow.get("concurrency", {}).get("group"))
                steps = next(iter(workflow["jobs"].values()))["steps"]
                checkout = next(step for step in steps if step.get("name") == "Checkout")
                self.assertEqual(
                    checkout.get("with", {}).get("ref"),
                    "${{ inputs.branch || 'main' }}",
                )
                dispatch = workflow.get("on", workflow.get(True))["workflow_dispatch"]
                self.assertEqual(
                    dispatch["inputs"]["environment"]["options"], ["chris", "newton"]
                )
                concurrency = workflow.get("concurrency", {})
                self.assertIn("inputs.environment", concurrency.get("group", ""))
                self.assertIs(concurrency.get("cancel-in-progress"), False)
        self.assertEqual(groups[0], groups[1])

    def test_workflow_shell_preserves_branch_arguments_and_host_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            executable = base / "ansible-playbook"
            executable.write_text('#!/bin/bash\nprintf "%s\\0" "$@" > "$CAPTURE"\n')
            executable.chmod(0o755)
            for filename in ("deploy.yaml", "ansible-configure.yaml"):
                workflow = load(f".github/workflows/{filename}")
                steps = next(iter(workflow["jobs"].values()))["steps"]
                step = steps[-1]
                # Public run logs must not dump ok/skipped results.
                self.assertEqual(step["env"]["ANSIBLE_DISPLAY_OK_HOSTS"], "false")
                self.assertEqual(step["env"]["ANSIBLE_DISPLAY_SKIPPED_HOSTS"], "false")
                for host in ("chris", "newton"):
                    with self.subTest(workflow=filename, host=host):
                        values = {
                            "inputs.environment": host,
                            "inputs.branch || 'main'": "fix/$HOME",
                            "secrets.TAILSCALE_TARGET_HOST": "127.0.0.1",
                            "vars.SSH_USER": "github",
                            "vars.ADMIN_SSH_USER": "alex",
                            "secrets.ANSIBLE_BECOME_PASSWORD": "test-only",
                        }

                        def actions(value):
                            return re.sub(
                                r"\$\{\{\s*(.*?)\s*\}\}",
                                lambda match: values[match[1]],
                                value,
                            )

                        environment = os.environ | {
                            "PATH": f"{base}:{os.environ['PATH']}",
                            "CAPTURE": str(base / "args"),
                            "RUNNER_TEMP": str(base),
                        }
                        environment.update(
                            {key: actions(value) for key, value in step.get("env", {}).items()}
                        )
                        result = run(["bash", "-c", actions(step["run"])], env=environment)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        arguments = (base / "args").read_text().split("\0")
                        self.assertIn("deploy_branch=fix/$HOME", arguments)
                        self.assertIn(
                            "inventory/hosts-newton.yml" if host == "newton"
                            else "inventory/hosts.yml",
                            arguments,
                        )

    def test_deploy_account_does_not_recursively_own_application_data(self):
        tasks = load("ansible/roles/deploy_user/tasks/main.yml")
        for task in tasks:
            attributes = task.get("ansible.builtin.file", {})
            if attributes.get("recurse"):
                path = render(attributes["path"], {"deploy_user_home": "/opt/github-deploy"})
                self.assertNotEqual(path, "/opt/github-deploy", task["name"])

    def test_samba_password_is_passed_literally(self):
        task = next(
            task for task in load("ansible/roles/samba/tasks/main.yml")
            if task["name"] == "Set Samba user password"
        )
        password = 'example$HOME"with\'quotes'
        variables = {"samba_password": password, "samba_user": "test-user"}
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            executable = base / "smbpasswd"
            executable.write_text('#!/bin/bash\ncat > "$CAPTURE"\n')
            executable.chmod(0o755)
            environment = os.environ | {
                "PATH": f"{base}:{os.environ['PATH']}",
                "CAPTURE": str(base / "password"),
            }
            if "ansible.builtin.shell" in task:
                result = run(
                    ["bash", "-c", render(task["ansible.builtin.shell"], variables)],
                    env=environment,
                )
            else:
                command = task["ansible.builtin.command"]
                result = run(
                    [render(argument, variables) for argument in command["argv"]],
                    input=render(command["stdin"], variables) + "\n",
                    env=environment,
                )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((base / "password").read_text(), f"{password}\n{password}\n")

    def test_private_branch_lookup_only_falls_back_for_a_missing_branch(self):
        tasks = load("ansible/roles/docker_stack/tasks/main.yml")
        lookups = [task for task in tasks if task["name"].startswith("Check whether deploy_branch")]
        self.assertEqual(len(lookups), 2)
        for task in lookups:
            for code, should_fail in ((0, False), (2, False), (128, True)):
                with self.subTest(task=task["name"], code=code):
                    condition = task["failed_when"]
                    failed = (
                        JINJA.compile_expression(condition)(
                            **{task["register"]: {"rc": code}}
                        )
                        if isinstance(condition, str) else condition
                    )
                    self.assertEqual(failed, should_fail)

    def test_manual_fallback_preserves_branches_and_stops_on_lookup_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            for name in ("home-server", "home-server-configuration"):
                (base / name).mkdir()
            for name in ("git", "docker"):
                executable = base / name
                executable.write_text(
                    '#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALLS"\n'
                    'if [ "$1" = ls-remote ]; then\n'
                    '  if [ "$LOOKUP_RC" = 2 ] && [ "${PWD##*/}" = home-server ]; then exit 0; fi\n'
                    '  if [ "$LOOKUP_RC" = 0 ]; then printf "dummy\\trefs/heads/fix/$HOME\\n"; fi\n'
                    '  exit "$LOOKUP_RC"\nfi\n'
                )
                executable.chmod(0o755)
            script = start_script(base, home_server_configuration_enabled=True)
            for code in (0, 2, 128):
                with self.subTest(code=code):
                    calls = base / "calls"
                    calls.write_text("")
                    result = run(
                        ["bash", "-c", script],
                        env=os.environ | {
                            "PATH": f"{base}:{os.environ['PATH']}",
                            "CALLS": str(calls),
                            "LOOKUP_RC": str(code),
                        },
                    )
                    commands = calls.read_text().splitlines()
                    if code == 128:
                        self.assertNotEqual(result.returncode, 0)
                        self.assertFalse(any(command.startswith("checkout") for command in commands))
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertIn("checkout -B fix/$HOME origin/fix/$HOME", commands)
                        self.assertEqual("checkout -B main origin/main" in commands, code == 2)

    def test_unmounted_storage_stops_manual_deploy_before_git_or_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            mountpoint = base / "mountpoint"
            mountpoint.write_text("#!/bin/bash\nexit 1\n")
            mountpoint.chmod(0o755)
            result = run(
                ["bash", "-c", start_script(base, docker_stack_storage_containers=["plex"])],
                env=os.environ | {"PATH": f"{base}:{os.environ['PATH']}"},
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not mounted", result.stdout)
            self.assertNotIn("No such file or directory", result.stderr)

    def test_core_deploy_waits_for_readiness(self):
        tasks = load("ansible/roles/docker_stack/tasks/main.yml")
        task = next(task for task in tasks if task["name"] == "Start Docker stack")
        arguments = shlex.split(render(task["ansible.builtin.command"]["cmd"], {
            "docker_compose_files": ["/test/docker-compose.yaml"],
            "home_server_dir": "/test",
            "docker_compose_profiles": ["chris"],
        }))
        self.assertIn("--wait", arguments)
        self.assertIn("--wait-timeout", arguments)
        self.assertGreater(int(arguments[arguments.index("--wait-timeout") + 1]), 0)

    def test_fast_deploy_does_not_load_vault_or_prepare_the_host(self):
        result = run(
            ["ansible-playbook", "playbooks/site.yml", "-i", "inventory/hosts-newton.yml",
             "--tags", "deploy", "--list-tasks"],
            cwd=ROOT / "ansible/github",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Start Docker stack", result.stdout)
        self.assertNotIn("Load encrypted", result.stdout)
        self.assertNotIn("Create deploy system user", result.stdout)
        self.assertNotIn("Render Frigate configuration", result.stdout)
        self.assertLess(
            result.stdout.index("Require media storage"),
            result.stdout.index("Clone or update home-server repository"),
        )

    def test_frigate_camera_policy_is_unchanged(self):
        config = yaml.safe_load(render((TEMPLATES / "frigate-config.yaml.j2").read_text(), {
            "newton_frigate_vault": {
                camera: {"rtsp_main": "rtsp://example/main", "rtsp_detect": "rtsp://example/sub"}
                for camera in ("camera1", "camera2")
            },
        }))
        expected = {
            "detect": {"enabled": False, "width": 704, "height": 576, "fps": 1},
            "motion": {"enabled": False},
            "record": {"enabled": True, "alerts": {"retain": {"days": 14, "mode": "motion"}}},
            "snapshots": {"enabled": True, "retain": {"default": 14}},
        }
        for camera in config["cameras"].values():
            self.assertEqual(
                {key: camera.get(key, config.get(key)) for key in expected}, expected
            )

    def test_security_scan_includes_both_compose_files(self):
        workflow = load(".github/workflows/lint-docker.yaml")
        step = next(
            step for step in workflow["jobs"]["kics"]["steps"]
            if step.get("uses", "").startswith("checkmarx/kics-github-action@")
        )
        self.assertEqual(
            set(step["with"]["path"].split(",")),
            {"docker-compose.yaml", "docker-compose.newton.yaml"},
        )


if __name__ == "__main__":
    unittest.main()
