"""
agent_jobs.py
-------------
Job classes that run on the Press Agent (frappe/agent fork).
Copy this file into your agent fork under agent/nextjs_jobs.py
and import + register them in agent/job.py.

Each class follows the agent's Job contract:
  - Inherits from Job
  - Sets job_type class attribute
  - Implements run() decomposed into self.step() calls
"""
import os
import subprocess
import time

import docker
import requests


# ───────────────────────────────────────────────────────────────────
# Helpers shared across job classes
# ───────────────────────────────────────────────────────────────────

def _docker_client():
    return docker.from_env()


def _container_name(site_name: str) -> str:
    return f"nextjs_{site_name.replace('.', '_')}"


def _ensure_cache_dir(site_name: str) -> str:
    path = f"/home/frappe/nextjs_cache/{site_name}"
    os.makedirs(path, exist_ok=True)
    try:
        os.chown(path, 1001, 1001)  # nextjs user inside the container
    except PermissionError:
        pass
    return path


def _wait_healthy(url: str, retries: int = 20, delay: float = 5.0):
    for attempt in range(retries):
        try:
            r = requests.get(url, timeout=4)
            if r.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(delay)
    raise RuntimeError(f"Container did not become healthy after {retries * delay}s at {url}")


def _write_nginx_conf(site_name: str, container_name: str, port: int):
    """
    Write a per-site nginx upstream + location block and reload nginx.
    This runs on the application server's nginx.
    """
    safe = site_name.replace(".", "_").replace("-", "_")
    conf = f"""\
# Managed by next_frontend_provisioner — do not edit manually
upstream nextjs_{safe} {{
    server {container_name}:3000;
    keepalive 32;
}}

server {{
    listen 80;
    server_name {site_name};

    location /app/ {{
        proxy_pass         http://nextjs_{safe}/;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade          $http_upgrade;
        proxy_set_header   Connection       "upgrade";
        proxy_set_header   Host             $host;
        proxy_set_header   X-Real-IP        $remote_addr;
        proxy_set_header   X-Forwarded-For  $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
        proxy_connect_timeout 10s;
    }}
}}
"""
    conf_path = f"/etc/nginx/conf.d/{site_name}.nextjs.conf"
    with open(conf_path, "w") as f:
        f.write(conf)
    subprocess.run(["nginx", "-t"], check=True)
    subprocess.run(["systemctl", "reload", "nginx"], check=True)


def _remove_nginx_conf(site_name: str):
    conf_path = f"/etc/nginx/conf.d/{site_name}.nextjs.conf"
    if os.path.exists(conf_path):
        os.remove(conf_path)
    try:
        subprocess.run(["nginx", "-t"], check=True)
        subprocess.run(["systemctl", "reload", "nginx"], check=True)
    except subprocess.CalledProcessError:
        pass


# ───────────────────────────────────────────────────────────────────
# Job: Provision Next.js Site
# ───────────────────────────────────────────────────────────────────

class ProvisionNextjsSiteJob:
    """
    Full provision: clone repo → build image → start container → nginx.
    Register in agent/job.py as:
        "Provision Next.js Site": ProvisionNextjsSiteJob
    """
    job_type = "Provision Next.js Site"

    def __init__(self, job, server):
        self.job    = job
        self.server = server
        self.params = job.get("params", {})

    def run(self):
        site_name = self.job["site"]
        params    = self.params

        repo_dir  = self._clone_repo(site_name, params)
        tag       = self._build_image(site_name, repo_dir, params)
        cache_dir = _ensure_cache_dir(site_name)
        name      = _container_name(site_name)
        port      = params["container_port"]

        self._start_container(name, tag, port, params["env_vars"], cache_dir)
        _wait_healthy(f"http://localhost:{port}/api/health")
        _write_nginx_conf(site_name, name, port)
        return {"status": "Running", "container": name, "port": port}

    def _clone_repo(self, site_name: str, params: dict) -> str:
        repo_dir = f"/home/frappe/nextjs/{site_name}"

        if os.path.exists(repo_dir):
            subprocess.run(
                ["git", "-C", repo_dir, "fetch", "--all"],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "-C", repo_dir, "checkout", params.get("branch", "main")],
                check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "-C", repo_dir, "pull"],
                check=True, capture_output=True,
            )
        else:
            subprocess.run(
                [
                    "git", "clone",
                    "-b", params.get("branch", "main"),
                    "--depth", "1",
                    params["repo_url"],
                    repo_dir,
                ],
                check=True, capture_output=True,
            )

        # Inject managed files after every clone/pull
        inject_templates(repo_dir, site_name, params)

        return repo_dir

    def _build_image(self, site_name: str, repo_dir: str, params: dict) -> str:
        os.environ["DOCKER_BUILDKIT"] = "1"
        client    = _docker_client()
        tag       = f"{_container_name(site_name)}:latest"
        buildargs = params.get("build_args", {})

        image, logs = client.images.build(
            path=repo_dir,
            tag=tag,
            buildargs=buildargs,
            rm=True,
            pull=True,
        )
        return tag

    def _start_container(
        self,
        name: str,
        tag: str,
        port: int,
        env_vars: dict,
        cache_dir: str,
    ):
        client = _docker_client()
        try:
            old = client.containers.get(name)
            old.stop(timeout=10)
            old.remove()
        except docker.errors.NotFound:
            pass

        client.containers.run(
            image=tag,
            name=name,
            environment=env_vars,
            ports={"3000/tcp": port},
            network="frappe_net",
            volumes={
                cache_dir: {"bind": "/app/.next/cache", "mode": "rw"},
            },
            labels={"managed_by": "next_frontend_provisioner", "slot": "blue"},
            detach=True,
            restart_policy={"Name": "unless-stopped"},
        )


# ───────────────────────────────────────────────────────────────────
# Job: Teardown Next.js Site
# ───────────────────────────────────────────────────────────────────

class TeardownNextjsSiteJob:
    job_type = "Teardown Next.js Site"

    def __init__(self, job, server):
        self.job    = job
        self.server = server
        self.params = job.get("params", {})

    def run(self):
        site_name      = self.job["site"]
        container_name = self.params.get("container_name", _container_name(site_name))
        client         = _docker_client()

        for name in [container_name, f"{container_name}_blue", f"{container_name}_green"]:
            try:
                c = client.containers.get(name)
                c.stop(timeout=15)
                c.remove()
            except docker.errors.NotFound:
                pass

        _remove_nginx_conf(site_name)
        return {"status": "Stopped"}


# ───────────────────────────────────────────────────────────────────
# Job: Redeploy Next.js Site  (blue/green zero-downtime)
# ───────────────────────────────────────────────────────────────────

class RedeployNextjsSiteJob:
    job_type = "Redeploy Next.js Site"

    def __init__(self, job, server):
        self.job    = job
        self.server = server
        self.params = job.get("params", {})

    def run(self):
        site_name = self.job["site"]
        params    = self.params
        client    = _docker_client()
        base_name = _container_name(site_name)
        port      = params["container_port"]

        # Determine current live slot
        try:
            live = client.containers.get(base_name)
            current_slot = live.labels.get("slot", "blue")
        except docker.errors.NotFound:
            current_slot = "blue"

        next_slot = "green" if current_slot == "blue" else "blue"
        next_name = f"{base_name}_{next_slot}"
        temp_port = port + 1

        # Pull latest code
        repo_dir = ProvisionNextjsSiteJob(self.job, self.server)._clone_repo(
            site_name, params
        )

        # Build new image
        os.environ["DOCKER_BUILDKIT"] = "1"
        new_tag = f"{base_name}:{next_slot}"
        image, _ = client.images.build(
            path=repo_dir,
            tag=new_tag,
            buildargs=params.get("build_args", {}),
            rm=True,
            pull=True,
        )

        # Start new slot on temp port
        cache_dir = _ensure_cache_dir(site_name)
        try:
            old_next = client.containers.get(next_name)
            old_next.stop(timeout=5)
            old_next.remove()
        except docker.errors.NotFound:
            pass

        env = dict(params["env_vars"])
        env["PORT"] = str(temp_port)

        client.containers.run(
            image=new_tag,
            name=next_name,
            environment=env,
            ports={"3000/tcp": temp_port},
            network="frappe_net",
            volumes={cache_dir: {"bind": "/app/.next/cache", "mode": "rw"}},
            labels={"managed_by": "next_frontend_provisioner", "slot": next_slot},
            detach=True,
            restart_policy={"Name": "unless-stopped"},
        )

        # Health-check new slot
        _wait_healthy(f"http://localhost:{temp_port}/api/health")

        # Cut nginx over to new slot on temp port
        _write_nginx_conf(site_name, next_name, temp_port)

        # Drain + remove old live container
        try:
            old_live = client.containers.get(base_name)
            old_live.stop(timeout=15)
            old_live.remove()
        except docker.errors.NotFound:
            pass

        # Rename new container to canonical name and switch to real port
        new_env = dict(params["env_vars"])
        client.containers.get(next_name).stop(timeout=5)
        client.containers.get(next_name).remove()

        client.containers.run(
            image=new_tag,
            name=base_name,
            environment=new_env,
            ports={"3000/tcp": port},
            network="frappe_net",
            volumes={cache_dir: {"bind": "/app/.next/cache", "mode": "rw"}},
            labels={"managed_by": "next_frontend_provisioner", "slot": next_slot},
            detach=True,
            restart_policy={"Name": "unless-stopped"},
        )

        _wait_healthy(f"http://localhost:{port}/api/health")
        _write_nginx_conf(site_name, base_name, port)

        return {"status": "Running", "slot": next_slot, "port": port}
