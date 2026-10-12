"""Validate app-owned artifacts; optionally install/start on a disposable CI runner."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from flamoris_updater_adapters.managed import Catalog, Manager, Start


def command(*args):
    return subprocess.check_output(args, stderr=subprocess.STDOUT, timeout=60).decode().strip()


def verify(catalog_path, install):
    catalog = Catalog.model_validate_json(catalog_path.read_bytes())
    if install and (os.geteuid() != 0 or os.environ.get("GITHUB_ACTIONS") != "true"):
        raise SystemExit("Actual installation is restricted to a disposable root CI runner")
    for recipe in catalog.recipes:
        with tempfile.TemporaryDirectory(prefix="flamoris-install-check-") as temporary:
            root = Path(temporary)
            root.chmod(0o700)
            manager = Manager(root / "state", root / "apps")
            manager.catalog = lambda refresh=False: catalog
            manager.accept_catalog(catalog)

            def local_download(url, limit, destination, expected):
                source = catalog_path.parent / url.rsplit("/", 1)[1]
                if source.stat().st_size > limit:
                    raise ValueError("Artifact exceeds its installer limit")
                with source.open("rb") as stream:
                    actual = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
                if actual != expected:
                    raise ValueError("Artifact digest mismatch")
                shutil.copyfile(source, destination)

            manager._download = local_download
            values = {}
            models = None
            for setting in recipe.settings:
                if setting.key in {"ADMIN_DSN", "PGHOST"}:
                    from psycopg.conninfo import conninfo_to_dict

                    dsn = os.environ.get("FLAMORIS_INSTALL_TEST_POSTGRES_DSN", "")
                    if not dsn:
                        raise ValueError("A disposable PostgreSQL service is required")
                    values[setting.key] = (
                        dsn if setting.key == "ADMIN_DSN" else conninfo_to_dict(dsn)["host"]
                    )
                elif setting.key == "MODEL_ROOT":
                    models = tempfile.TemporaryDirectory(prefix="flamoris-empty-models-")
                    Path(models.name).chmod(0o755)
                    values[setting.key] = models.name
                elif setting.required and not setting.default and not setting.generate:
                    raise ValueError("Missing a required CI setting: " + setting.key)
            cfg = None
            try:
                job = manager.start(
                    "install",
                    Start(
                        application_id=recipe.application_id,
                        release=recipe.release,
                        request_key="repository-release-verification",
                        settings=values,
                    ),
                )
                # Preview uses the same settings validation/profile as actual installation.
                if not install:
                    cfg = manager._profile(recipe, job["job_id"])
                else:
                    manager.run_job(job["job_id"])
                    stored = manager.journal.get("managed_job", job["job_id"])
                    if stored["phase"] != "awaiting_setup":
                        raise ValueError("Installer failed: " + stored.get("error", "unknown"))
                    installed = manager.journal.get("managed_app", recipe.application_id)
                    cfg = manager._profile(recipe, "cleanup-profile")
                    assert installed["release"] == recipe.release
                    manager.start_setup(recipe.application_id)
                    result = manager.complete(recipe.application_id)
                    assert result["phase"] == "succeeded"
                assert cfg.applications[0].application_id == recipe.application_id
                assert cfg.applications[0].release == recipe.release
                settings = json.loads(
                    (
                        manager.state / "settings" / recipe.application_id / "values.json"
                    ).read_bytes()
                )
                if recipe.application_id == "flamoris-studio":
                    assert settings["GENERATION_TOKEN"] == settings["AGENT_TOKEN"] == ""
            finally:
                if install:
                    app = recipe.application
                    if app["kind"] == "docker":
                        subprocess.run(
                            ["docker", "rm", "--force", app["container_name"]],
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=60,
                        )
                    else:
                        for unit in app["units"]:
                            name = unit["name"]
                            subprocess.run(
                                ["systemctl", "disable", "--now", name],
                                check=False,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                timeout=60,
                            )
                            (Path("/etc/systemd/system") / name).unlink(missing_ok=True)
                        command("systemctl", "daemon-reload")
                if models:
                    models.cleanup()
    print(
        json.dumps(
            {
                "validated_recipes": len(catalog.recipes),
                "installation_performed": install,
            }
        )
    )


def verify_update(catalog_path, previous_path):
    """Exercise the real published predecessor with retained operator state."""
    if os.geteuid() != 0 or os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Actual update is restricted to a disposable root CI runner")
    current = Catalog.model_validate_json(catalog_path.read_bytes())
    previous = Catalog.model_validate_json(previous_path.read_bytes())
    if len(current.recipes) != 1 or len(previous.recipes) != 1:
        raise ValueError("One platform recipe per update check is required")
    target, before = current.recipes[0], previous.recipes[0]
    if (
        target.application_id != before.application_id
        or target.platform != before.platform
        or before.release not in target.compatible_from
    ):
        raise ValueError("Predecessor must be explicitly compatible")
    catalog = Catalog(catalog_version=1, recipes=[before, target])
    with tempfile.TemporaryDirectory(prefix="flamoris-update-check-") as temporary:
        root = Path(temporary)
        manager = Manager(root / "state", root / "apps")
        manager.catalog = lambda refresh=False: catalog
        manager.accept_catalog(catalog)
        original_download = manager._download
        candidate_urls = {item.url for item in target.downloads.values()}

        def local_or_published(url, limit, destination, expected):
            source = catalog_path.parent / url.rsplit("/", 1)[1]
            if url in candidate_urls:
                if source.stat().st_size > limit:
                    raise ValueError("Artifact exceeds its installer limit")
                with source.open("rb") as stream:
                    actual = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
                if actual != expected:
                    raise ValueError("Artifact digest mismatch")
                shutil.copyfile(source, destination)
            else:
                # Only the recorded published predecessor's immutable payload
                # is fetched. New candidate bytes stay local until publication.
                original_download(url, limit, destination, expected)

        manager._download = local_or_published
        models = root / "models"
        models.mkdir(mode=0o755)
        container = target.application["container_name"]
        try:
            initial = manager.start(
                "install",
                Start(
                    application_id=before.application_id,
                    release=before.release,
                    request_key="published-predecessor",
                    settings={"MODEL_ROOT": str(models)},
                ),
            )
            manager.run_job(initial["job_id"])
            stored = manager.journal.get("managed_job", initial["job_id"])
            if stored["phase"] != "awaiting_setup":
                raise ValueError("Predecessor install failed: " + stored.get("error", "unknown"))
            manager.start_setup(before.application_id)
            assert manager.complete(before.application_id)["phase"] == "succeeded"
            installed = manager.journal.get("managed_app", before.application_id)
            old_profile = installed["profile"]
            app_root = manager.root / before.application_id
            environment = Path(old_profile["environment_file"])
            with environment.open("ab") as stream:
                stream.write(
                    b"\n# retained operator configuration\nFLAMORIS_TEST_MARKER=preserved\n"
                )
            retained_environment = environment.read_bytes()
            settings_file = manager.state / "settings" / before.application_id / "values.json"
            retained_settings = settings_file.read_bytes()
            probes = [
                app_root / "workflows" / "operator-check.txt",
                app_root / "outputs" / "operator-check.txt",
            ]
            for probe in probes:
                probe.write_bytes(b"retained generation data")
            upgrade = manager.start(
                "update",
                Start(
                    application_id=target.application_id,
                    release=target.release,
                    request_key="retained-update",
                ),
            )
            manager.run_job(upgrade["job_id"])
            result = manager.journal.get("managed_job", upgrade["job_id"])
            if result["phase"] != "succeeded":
                raise ValueError("Update failed: " + result.get("error", "unknown"))
            updated = manager.journal.get("managed_app", target.application_id)
            assert updated["release"] == target.release
            assert updated["profile"]["mounts"] == old_profile["mounts"]
            assert environment.read_bytes() == retained_environment
            assert settings_file.read_bytes() == retained_settings
            assert all(probe.read_bytes() == b"retained generation data" for probe in probes)
            versions = json.loads(
                command(
                    "docker",
                    "exec",
                    container,
                    "python",
                    "-c",
                    "import json, importlib.metadata as m; "
                    "print(json.dumps({name:m.version(name) for name in "
                    "['flamoris-generation-mcp','flamoris-generation-controller']}))",
                )
            )
            assert versions == {
                "flamoris-generation-mcp": target.release,
                "flamoris-generation-controller": "1.0.2",
            }
            print(
                json.dumps(
                    {
                        "update_from": before.release,
                        "update_to": target.release,
                        "environment_preserved": True,
                        "data_preserved": True,
                    }
                )
            )
        finally:
            subprocess.run(
                ["docker", "rm", "--force", container],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--upgrade-from-catalog", type=Path)
    args = parser.parse_args()
    if args.upgrade_from_catalog:
        verify_update(args.catalog, args.upgrade_from_catalog)
    else:
        verify(args.catalog, args.install)


if __name__ == "__main__":
    main()
