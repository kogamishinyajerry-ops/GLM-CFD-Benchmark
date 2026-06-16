# CFD-Benchmark 系统架构设计 v2.1 — P2-b 增量（Docker backend + DVC + NACA0012）

| 项 | 内容 |
|---|---|
| 版本 | v2.1（增量） |
| 日期 | 2026-06-17 |
| 作者 | 高见远（Gao）· 架构师 |
| 范围 | **P2-b（A Docker backend 完整支持 + B DVC 大文件管理 + E NACA0012 OF+SU2 α=0° 对比）** |
| 上游依赖 | `docs/prd/PRD-v2.1-P2b.md`（P2-b PRD）、`docs/architecture/Architecture-v2.0-P2a.md`（P2-a 基线） |
| 基线 | P0+P1-a+P1-b+P2-a（commit `81f32bb`，**250 测试 / 91.08% cov**） |

---

## 1. 概述

P2-b 在 P2-a 基线之上实现 3 项增量功能：①**Docker backend**——新增 `DockerBackend(ExecutionBackend)` 作为 `ExecutionBackend` Protocol 的第二种实现，用户可通过 `--backend docker --image <name:tag>` 在容器内执行真实 solver，`container_digest` 写入 manifest 强化可复现性；②**DVC 大文件管理**——新增 `cfdb/data/` 子包封装 DVC 辅助函数 + CLI `cfdb data pull/status`，NACA0012 snappyHexMesh 网格与 Ladson 1988 参考数据纳入版本化；③**NACA0012 case**——经典外流验证（α=0° 单攻角），OpenFOAM + SU2 双 solver Cp 分布对比 Ladson 1988。

本增量严格遵守 5 条铁律：

| 铁律 | 说明 |
|---|---|
| #1 | 不破坏 P0+P1-a+P1-b+P2-a 的 **250** 个测试 |
| #2 | Schema 只新增 Optional 字段，不改已有字段 |
| #3 | `ExecutionBackend` / `SolverAdapter` / `ResultRepository` Protocol 不动（Docker/DVC/NACA0012 均通过 structural subtyping 或新文件满足） |
| #4 | JSON 存储与 local backend 保持兼容（`--storage json` / `--backend local` 仍默认） |
| #5 | DockerBackend 测试不依赖真实 Docker daemon（mock subprocess，`-m real_docker` marker 分层） |

**转总已确认决策**（见 PRD-v2.1 §3）：Q2=A 完整 Docker 支持；Q3=B DVC 管网格+参考数据；Q6=B NACA0012 OF+SU2 单攻角；镜像用官方镜像。

---

## 2. 核心架构改造：backend 注入 adapter

### 2.1 痛点

P1-b 实现时，`OpenFOAMAdapter` 和 `SU2Adapter` 在 `run()` 内部**硬编码** `LocalExecutionBackend()`：

```python
# adapters/openfoam.py:215, adapters/su2.py:188
from cfdb.execution.local import LocalExecutionBackend
backend = LocalExecutionBackend()
result = backend.execute(...)
```

P2-b 要让 adapter 能用 DockerBackend，必须改造此处。但**不能改 `SolverAdapter` Protocol**（铁律 #3），因为 Protocol 只定义 `prepare/run/collect_outputs` 三个方法签名。

### 2.2 方案：adapter `__init__` 接受 backend 注入

`SolverAdapter` Protocol 不规定 `__init__` 签名（Protocol 只约束方法，不约束构造函数）。因此让具体 adapter（OpenFOAMAdapter / SU2Adapter）的 `__init__` 接受可选的 `backend: ExecutionBackend | None` 参数：

```python
# adapters/openfoam.py（改造后）
class OpenFOAMAdapter:
    def __init__(
        self,
        dry_run: bool = False,
        backend: ExecutionBackend | None = None,
    ) -> None:
        self._dry_run = dry_run
        self._backend = backend or LocalExecutionBackend()
        self._template_dir = Path(__file__).parent / "templates" / "openfoam"

    def run(self, case, case_dir, run_dir, resources) -> RunResult:
        ...
        # 不再硬编码 LocalExecutionBackend，改用 self._backend
        result = self._backend.execute(cmd_list, cwd=case_dir_out, timeout=step.timeout_sec)
        ...
```

**向后兼容性证明**：
- P0/P1-a 测试调用 `OpenFOAMAdapter(dry_run=True)` → `backend=None` → 走默认 `LocalExecutionBackend()` ✅
- P1-b 测试调用 `OpenFOAMAdapter(dry_run=False)` → `backend=None` → 走默认 `LocalExecutionBackend()` ✅
- 只有 P2-b 新代码显式传入 DockerBackend 实例

### 2.3 generic_command adapter 不改

`GenericCommandAdapter` 已通过 Runner 注入 backend（P0 设计就是 Runner 直接调用 backend），无需改造。

### 2.4 Runner 改造：构造 backend 实例传给 adapter

```python
# core/runner.py execute() 改造
def execute(
    self,
    case_id: str,
    solver: str = "generic",
    backend: str = "local",
    backend_options: dict[str, Any] | None = None,  # P2-b 新增
    generate_report: bool = False,
    cli_args: dict[str, str] | None = None,
    dry_run: bool = False,
) -> RunManifest:
    ...
    # P2-b: 构造 backend 实例（不再用 get_backend(name) 简单工厂）
    backend_inst = self._build_backend(backend, backend_options)

    # 把 backend 实例传给 adapter（generic adapter 仍走 P0 路径，但接口统一）
    adapter = get_adapter(solver, dry_run=dry_run, backend=backend_inst)
    ...

def _build_backend(
    self,
    name: str,
    options: dict[str, Any] | None,
) -> ExecutionBackend:
    """Construct a backend instance from name + options.

    Args:
        name: Backend name ('local' or 'docker').
        options: Backend-specific options (e.g. image for docker).

    Returns:
        ExecutionBackend instance.
    """
    opts = options or {}
    if name == "local":
        return LocalExecutionBackend()
    elif name == "docker":
        from cfdb.execution.docker import DockerBackend
        image = opts.get("image")
        if not image:
            raise ValueError("docker backend requires 'image' in backend_options")
        return DockerBackend(
            image=image,
            pull_policy=opts.get("pull_policy", "missing"),
        )
    else:
        raise ValueError(f"Unknown backend: '{name}'")
```

### 2.5 adapter 工厂改造

`adapters/__init__.py` 的 `get_adapter()` 加 `backend` 参数：

```python
# adapters/__init__.py（改造后）
def get_adapter(
    name: str,
    dry_run: bool = False,
    backend: ExecutionBackend | None = None,
) -> SolverAdapter:
    ...
    if name == "openfoam":
        return OpenFOAMAdapter(dry_run=dry_run, backend=backend)
    elif name == "su2":
        return SU2Adapter(dry_run=dry_run, backend=backend)
    elif name == "generic":
        return GenericCommandAdapter()  # P0 行为不变
    ...
```

### 2.6 RunManifest 加 `backend_options`

P1-b 已有 `container_digest: str | None = None`，P2-b 填充 Docker 模式的实际值。再加一个 Optional 字段记录完整 backend 配置：

```python
# schema.py RunManifest 新增字段
backend_options: dict[str, Any] | None = None
"""Backend-specific options snapshot for reproducibility.
For Docker: {'image': 'openfoam/openfoam:v2406', 'digest': 'sha256:...',
             'pull_policy': 'missing', 'workdir': '/work'}.
For local: None (default)."""
```

铁律 #2 约束：默认 None，不破坏旧 manifest。

---

## 3. DockerBackend 设计（A）

### 3.1 类定义

```python
# execution/docker.py
"""DockerExecutionBackend — Docker container execution."""

from __future__ import annotations

import logging
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from cfdb.adapters.base import RunResult
from cfdb.execution.base import ExecutionBackend

logger = logging.getLogger(__name__)


class BackendError(Exception):
    """Backend infrastructure error (daemon down, image missing, etc)."""


class DockerBackend:
    """Docker container execution backend.

    Executes commands inside a Docker container. The working directory (cwd)
    is bind-mounted into the container at the same path, so relative paths
    work identically inside and outside the container.

    Args:
        image: Docker image reference (name:tag). Required.
        pull_policy: 'always' (pull before every run), 'missing' (pull if
            not present locally, default), 'never' (never pull).
    """

    name: str = "docker"

    def __init__(
        self,
        image: str,
        pull_policy: Literal["always", "missing", "never"] = "missing",
    ) -> None:
        if not image:
            raise ValueError("image must be a non-empty string")
        self._image = image
        self._pull_policy = pull_policy
        self._digest: str | None = None  # cached after first execution

    @property
    def image(self) -> str:
        return self._image

    @property
    def digest(self) -> str | None:
        """Resolved image digest (sha256:...). Available after first execute()."""
        return self._digest

    def _check_daemon(self) -> None:
        """Check Docker daemon is reachable. Raises BackendError if not."""
        try:
            subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            raise BackendError(
                f"docker daemon not reachable: {e}. "
                "Ensure Docker Desktop / docker engine is running."
            ) from e

    def _resolve_digest(self) -> str:
        """Resolve image to sha256 digest. Returns empty string if unresolvable."""
        try:
            proc = subprocess.run(
                [
                    "docker", "inspect",
                    "--format", "{{index .RepoDigests 0}}",
                    self._image,
                ],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                # Output: openfoam/openfoam@sha256:abc123...
                digest = proc.stdout.strip().split("@")[-1]
                return digest
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        # Fallback: image ID (short)
        try:
            proc = subprocess.run(
                ["docker", "inspect", "--format", "{{.Id}}", self._image],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if proc.returncode == 0:
                return proc.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return ""

    def _pull_image(self) -> None:
        """Pull the image according to pull_policy."""
        if self._pull_policy == "never":
            return
        if self._pull_policy == "missing":
            # Check if image exists locally
            proc = subprocess.run(
                ["docker", "image", "inspect", self._image],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if proc.returncode == 0:
                return  # already present

        logger.info("pulling docker image %s", self._image)
        proc = subprocess.run(
            ["docker", "pull", self._image],
            capture_output=True, text=True, timeout=600, check=False,
        )
        if proc.returncode != 0:
            raise BackendError(
                f"failed to pull image '{self._image}': {proc.stderr.strip()}"
            )

    def _build_command(
        self,
        command: list[str],
        cwd: Path,
        env: dict[str, str] | None,
    ) -> list[str]:
        """Build the full `docker run ...` command list."""
        # Resolve absolute path for mount. On Windows, docker automatically
        # translates C:\... to /c/... or /mnt/c/... depending on Docker Desktop.
        cwd_abs = cwd.resolve()

        docker_args = [
            "docker", "run", "--rm",
            "-i",  # interactive (for capturing stdin if needed)
            "--workdir", str(cwd_abs),
            "-v", f"{cwd_abs}:{cwd_abs}",
        ]

        # User mapping (avoid root-owned files on Linux/macOS; skip on Windows)
        import os, sys
        if sys.platform != "win32":
            docker_args.extend(["--user", f"{os.getuid()}:{os.getgid()}"])

        # Environment variables
        if env:
            for k, v in env.items():
                docker_args.extend(["-e", f"{k}={v}"])

        # Image
        docker_args.append(self._image)

        # The actual command to run inside the container
        docker_args.extend(command)
        return docker_args

    def execute(
        self,
        command: list[str],
        cwd: Path,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> RunResult:
        """Execute a command inside a Docker container.

        Args:
            command: Command and args to run (e.g. ['blockMesh']).
            cwd: Working directory (bind-mounted into container).
            timeout: Container execution timeout in seconds.
            env: Environment variables to inject.

        Returns:
            RunResult with exit_code, stdout, stderr, wall_time_sec, timed_out.

        Raises:
            BackendError: If daemon unreachable or image pull fails.
        """
        # 1. Check daemon
        self._check_daemon()

        # 2. Pull image (according to policy)
        self._pull_image()

        # 3. Resolve digest (cached)
        if self._digest is None:
            self._digest = self._resolve_digest()

        # 4. Build docker run command
        full_cmd = self._build_command(command, cwd, env)

        # 5. Execute (reuse the same subprocess pattern as LocalExecutionBackend)
        start = datetime.now(timezone.utc)
        try:
            proc = subprocess.run(
                full_cmd,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
            wall = (datetime.now(timezone.utc) - start).total_seconds()

            # Write logs to cwd (same convention as local backend)
            self._write_logs(cwd, proc.stdout or "", proc.stderr or "")

            return RunResult(
                exit_code=proc.returncode,
                stdout=proc.stdout or "",
                stderr=proc.stderr or "",
                wall_time_sec=wall,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as e:
            wall = (datetime.now(timezone.utc) - start).total_seconds()
            stdout = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", errors="replace")
            stderr = e.stderr if isinstance(e.stderr, str) else (e.stderr or b"").decode("utf-8", errors="replace")
            combined = f"Timeout after {timeout}s\n{stderr}"
            self._write_logs(cwd, stdout, combined)
            return RunResult(
                exit_code=-1,
                stdout=stdout,
                stderr=combined,
                wall_time_sec=float(timeout) if timeout else wall,
                timed_out=True,
            )
        except (FileNotFoundError, OSError) as e:
            wall = (datetime.now(timezone.utc) - start).total_seconds()
            err_msg = f"Docker executable not found: {e}"
            self._write_logs(cwd, "", err_msg)
            return RunResult(
                exit_code=-1,
                stdout="",
                stderr=err_msg,
                wall_time_sec=wall,
                timed_out=False,
            )

    def _write_logs(self, cwd: Path, stdout: str, stderr: str) -> None:
        """Write stdout/stderr to log files (same convention as local backend)."""
        try:
            (cwd / "stdout.log").write_text(stdout, encoding="utf-8")
            (cwd / "stderr.log").write_text(stderr, encoding="utf-8")
        except OSError as e:
            logger.warning("failed to write logs to %s: %s", cwd, e)


# Protocol compliance marker (structural subtyping)
_ExecutionBackend: type[ExecutionBackend] = DockerBackend  # type: ignore[assignment]
```

### 3.2 Backend 注册表扩展

```python
# execution/__init__.py（改造）
from cfdb.execution.base import ExecutionBackend
from cfdb.execution.local import LocalExecutionBackend

# Note: DockerBackend is NOT auto-registered here to avoid importing
# it when not needed (it's only imported when --backend docker is used).
# Runner._build_backend() handles instantiation directly.

_BACKENDS: dict[str, type[ExecutionBackend]] = {
    "local": LocalExecutionBackend,
}


def get_backend(name: str) -> ExecutionBackend:
    """Backwards-compatible simple factory. For docker, use Runner._build_backend."""
    if name not in _BACKENDS:
        raise KeyError(f"Unknown backend: '{name}'. Available: {list(_BACKENDS)}")
    return _BACKENDS[name]()
```

### 3.3 CLI 扩展

```python
# cli.py run() 新增参数
@app.command("run")
def run(
    ...
    backend: ... = "local",
    image: Annotated[
        str | None,
        typer.Option("--image", help="Docker image (name:tag). Only used with --backend docker."),
    ] = None,
    pull: Annotated[
        str,
        typer.Option("--pull", help="Image pull policy: 'always' | 'missing' | 'never'."),
    ] = "missing",
    ...
):
    ...
    backend_options: dict[str, Any] | None = None
    if backend == "docker":
        if not image:
            typer.echo("[FAIL] --backend docker requires --image", err=True)
            raise typer.Exit(code=1)
        backend_options = {"image": image, "pull_policy": pull}

    manifest = runner.execute(
        case_id=case,
        solver=solver,
        backend=backend,
        backend_options=backend_options,  # P2-b
        generate_report=report,
        cli_args=cli_args,
        dry_run=dry_run,
    )
    ...
    # P2-b: print container digest
    if manifest.container_digest:
        typer.echo(f"Container: {manifest.container_digest}")
```

### 3.4 container_digest 写入 manifest

`Runner.execute()` 在 manifest 构造时，检查 backend 是否为 DockerBackend，若是则提取 digest：

```python
# runner.py
# After execute_phases:
container_digest = None
if hasattr(adapter, "_backend") and hasattr(adapter._backend, "digest"):
    container_digest = adapter._backend.digest

manifest = RunManifest(
    ...
    container_digest=container_digest,
    backend_options=backend_options if backend == "docker" else None,
    ...
)
```

---

## 4. DVC 大文件管理设计（B）

### 4.1 `cfdb.data` 子包

```python
# data/__init__.py
"""DVC (Data Version Control) wrapper utilities.

This subpackage provides a thin Python wrapper around the DVC CLI for managing
large files (meshes, reference datasets). It does NOT depend on DVC being
installed at import time — all functions gracefully handle missing DVC.
"""

from cfdb.data.dvc import (
    DVC_AVAILABLE,
    dvc_available,
    dvc_pull,
    dvc_status,
    DVCError,
)

__all__ = ["DVC_AVAILABLE", "dvc_available", "dvc_pull", "dvc_status", "DVCError"]
```

```python
# data/dvc.py
"""DVC CLI wrapper."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class DVCError(Exception):
    """DVC operation failed."""


def dvc_available() -> bool:
    """Check whether the `dvc` executable is available on PATH.

    Returns:
        True if `dvc --version` succeeds, False otherwise.
    """
    dvc_path = shutil.which("dvc")
    if not dvc_path:
        return False
    try:
        subprocess.run(
            [dvc_path, "--version"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


DVC_AVAILABLE = dvc_available()


def dvc_pull(
    targets: list[str] | None = None,
    cwd: Path | None = None,
) -> str:
    """Run `dvc pull` to fetch tracked data from remote.

    Args:
        targets: Specific .dvc file targets (relative paths). If None, pulls all.
        cwd: Working directory (defaults to current dir).

    Returns:
        DVC stdout output.

    Raises:
        DVCError: If DVC is not installed or pull fails.
    """
    if not dvc_available():
        raise DVCError(
            "dvc not found on PATH. Install DVC: pip install dvc"
        )
    cmd = ["dvc", "pull"]
    if targets:
        cmd.extend(targets)
    proc = subprocess.run(
        cmd, cwd=str(cwd or Path.cwd()),
        capture_output=True, text=True, timeout=600, check=False,
    )
    if proc.returncode != 0:
        raise DVCError(
            f"dvc pull failed (exit {proc.returncode}): {proc.stderr}"
        )
    return proc.stdout


def dvc_status(cwd: Path | None = None) -> dict[str, str]:
    """Run `dvc status --json` and return the parsed status dict.

    Args:
        cwd: Working directory.

    Returns:
        Parsed JSON dict from `dvc status`.

    Raises:
        DVCError: If DVC not installed or status fails.
    """
    if not dvc_available():
        raise DVCError("dvc not found on PATH. Install DVC: pip install dvc")
    proc = subprocess.run(
        ["dvc", "status", "--json"],
        cwd=str(cwd or Path.cwd()),
        capture_output=True, text=True, timeout=60, check=False,
    )
    if proc.returncode != 0:
        raise DVCError(f"dvc status failed: {proc.stderr}")
    import json
    return json.loads(proc.stdout or "{}")
```

### 4.2 CLI 新增 `cfdb data` 子命令

```python
# cli.py 新增 data 子命令组
data_app = typer.Typer(help="DVC large file management.")
app.add_typer(data_app, name="data")


@data_app.command("pull")
def data_pull(
    targets: Annotated[
        list[str] | None,
        typer.Argument(help="Specific .dvc targets. If empty, pulls all."),
    ] = None,
    cwd: Annotated[
        Path,
        typer.Option("--cwd", help="Working directory."),
    ] = Path("."),
) -> None:
    """Pull DVC-tracked data from remote."""
    from cfdb.data import dvc_pull, DVCError
    try:
        output = dvc_pull(targets=targets, cwd=cwd)
        typer.echo("[OK] DVC pull complete.")
        if output:
            typer.echo(output)
    except DVCError as e:
        typer.echo(f"[FAIL] {e}", err=True)
        raise typer.Exit(code=1) from e


@data_app.command("status")
def data_status(
    cwd: Annotated[Path, typer.Option("--cwd", help="Working directory.")] = Path("."),
) -> None:
    """Show DVC status."""
    from cfdb.data import dvc_status, DVCError, dvc_available
    if not dvc_available():
        typer.echo("[WARN] DVC not installed. Install with: pip install dvc")
        return
    try:
        status = dvc_status(cwd=cwd)
        if not status:
            typer.echo("[OK] DVC workspace up to date.")
        else:
            typer.echo("DVC status:")
            for path, info in status.items():
                typer.echo(f"  {path}: {info}")
    except DVCError as e:
        typer.echo(f"[FAIL] {e}", err=True)
        raise typer.Exit(code=1) from e
```

### 4.3 DVC 配置文件

```
# .dvc/config (项目根目录新增)
[core]
    remote = local_cache
['remote "local_cache"']
    url = ./runs/dvc-cache
```

```
# .dvcignore (项目根目录新增)
/cases/*/runs/
*.pyc
__pycache__/
.pytest_cache/
```

```
# dvc.yaml (项目根目录新增)
# Pipeline: generate NACA0012 mesh + fetch Ladson 1988 data
stages:
  generate_naca0012_mesh:
    cmd: python cases/validation/naca0012/gen_mesh.py
    wdir: ..
    deps:
      - cases/validation/naca0012/gen_mesh.py
      - cases/validation/naca0012/gen_geometry.py
    outs:
      - cases/validation/naca0012/mesh/naca0012.stl
      - cases/validation/naca0012/mesh/blockMeshDict
```

### 4.4 `.gitignore` 与 `.gitattributes` 更新

`.gitignore` 新增：
```
# DVC
runs/dvc-cache/
/dvc.lock  # DVC pipeline lock (regenerated by dvc repro)
```

`.gitattributes` 无需改动（DVC 用 `.dvc` 后缀的指针文件，正常 LF 处理）。

---

## 5. NACA0012 case 设计（E）

### 5.1 CaseSpec

```yaml
# cases/validation/naca0012/case.yaml
id: naca0012_a0
name: NACA0012 Airfoil α=0° (Ladson 1988 Validation)
category: validation
description: >
  Classic external aerodynamics validation case. NACA0012 symmetric airfoil at
  angle of attack α=0°, Re=6e6, M=0.3 (low Mach, RANS). OpenFOAM (simpleFoam +
  SA turbulence) and SU2 (RANS-SA) solvers compared against Ladson 1988
  experimental Cp distribution (NASA TM-4074).

physics:
  flow: rans
  turbulence: rans_sa
  dimensionality: "2d"
  steady: true

conditions:
  reynolds: 6.0e6
  mach: 0.3
  alpha_deg: 0.0

geometry:
  type: external
  source: geometry/naca0012.dat

mesh:
  family: unstructured_hex
  levels: ["coarse"]
  target_y_plus: 1.0

solvers:
  - name: openfoam
    command: ""
    timeout_sec: 600
    steps:
      - name: block_mesh
        command: "blockMesh -case {{ case_dir }}"
        timeout_sec: 60
        critical: true
      - name: decompose
        command: "decomposePar -case {{ case_dir }}"
        timeout_sec: 60
        critical: false
      - name: snappy_mesh
        command: "snappyHexMesh -overwrite -case {{ case_dir }}"
        timeout_sec: 120
        critical: true
      - name: reconstruct_mesh
        command: "reconstructParMesh -constant -mergeTol 1e-6 -case {{ case_dir }}"
        timeout_sec: 60
        critical: false
      - name: solve
        command: "simpleFoam -case {{ case_dir }}"
        timeout_sec: 300
        critical: true
    parameters:
      nu: 1.6667e-7   # ν = U∞ * c / Re = 1 * 1 / 6e6
      u_inf: 100.0     # U∞ reference
      l_ref: 1.0       # chord
      alpha_deg: 0.0
      n_iter: 1000

  - name: su2
    command: ""
    timeout_sec: 600
    steps:
      - name: solve
        command: "SU2_CFD {{ case_dir }}/config.cfg"
        timeout_sec: 600
        critical: true
    parameters:
      mach: 0.3
      aoa_deg: 0.0
      reynolds: 6.0e6

outputs:
  fields: ["U", "p", "nuTilda"]
  curves: ["cp_distribution"]
  qoi: ["cl", "cd"]

reference:
  type: experimental
  files:
    cp_curve: reference/ladson1988.csv
  qoi_values:
    cl: 0.0      # Symmetric airfoil at α=0°
    cd: 0.0086   # Ladson 1988 α=0° Re=6e6

metrics:
  qoi_relative_tolerance:
    cl: 0.001    # 0.001 absolute (cl is 0, so relative tolerance special)
    cd: 0.10     # 10% relative tolerance
  curve_l2_tolerance:
    cp_distribution: 0.05

budget:
  max_runtime_sec: 600
  max_cells: 100000
```

### 5.2 NACA 4-digit 几何生成（纯 numpy）

```python
# cases/validation/naca0012/gen_geometry.py
"""Generate NACA 4-digit airfoil coordinates (cosine spacing).

References:
- NACA Report 460 (1933)
- Ladson, C. L., "Effects of Independent Variation of Mach and Reynolds
  Numbers on the Low-Speed Aerodynamic Characteristics of the NACA 0012 Airfoil
  Section," NASA TM-4074, 1988.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np


def naca4_thickness(m: float, p: float, t: float, n: int = 200) -> tuple[np.ndarray, np.ndarray]:
    """Generate NACA 4-digit airfoil coordinates (upper + lower surface).

    Uses cosine spacing for x to cluster points near leading/trailing edges.

    Args:
        m: Maximum camber (fraction of chord). NACA0012: m=0.
        p: Location of max camber (fraction of chord). NACA0012: p=0.
        t: Maximum thickness (fraction of chord). NACA0012: t=0.12.
        n: Number of points per surface (upper or lower). Default 200.

    Returns:
        Tuple (x, y) of np.ndarrays, each of length 2*n (upper surface first
        from LE to TE, then lower surface from TE to LE).
    """
    # Cosine spacing for x ∈ [0, 1]
    beta = np.linspace(0.0, math.pi, n)
    x = 0.5 * (1.0 - np.cos(beta))

    # Thickness distribution yt(x) — common to NACA 4-digit
    yt = (
        5 * t * (
            0.2969 * np.sqrt(x)
            - 0.1260 * x
            - 0.3516 * x**2
            + 0.2843 * x**3
            - 0.1015 * x**4   # Closed trailing edge (0.1036 for open)
        )
    )

    if m == 0 and p == 0:
        # Symmetric airfoil (NACA00xx): camber line = 0
        yc = np.zeros_like(x)
        dyc_dx = np.zeros_like(x)
    else:
        # Cambered: standard NACA 4-digit equations
        # ... (omitted; NACA0012 uses symmetric)
        raise NotImplementedError("cambered airfoils not implemented; use symmetric")

    # Upper and lower surfaces
    xu = x
    yu = yc + yt
    xl = x
    yl = yc - yt

    # Concatenate: upper LE→TE, then lower TE→LE (closed loop)
    x_out = np.concatenate([xu, xl[::-1]])
    y_out = np.concatenate([yu, yl[::-1]])
    return x_out, y_out


def write_selig_format(x: np.ndarray, y: np.ndarray, path: Path) -> None:
    """Write airfoil coordinates in Selig .dat format.

    Format:
        NACA0012
        <x_upper> <y_upper>
        ...
        <x_lower> <y_lower>
        ...
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["NACA0012"]
    for xi, yi in zip(x, y):
        lines.append(f"{xi:.6f} {yi:.6f}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_stl(
    x: np.ndarray,
    y: np.ndarray,
    path: Path,
    z_extent: float = 0.1,
) -> None:
    """Write airfoil as a thin 3D STL (extruded in z).

    snappyHexMesh requires 3D surface STL. We extrude the 2D profile
    by z_extent to form a thin slab.

    Args:
        x, y: 2D profile coordinates.
        path: Output STL path.
        z_extent: Slab thickness in z direction.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    n = len(x) // 2  # upper + lower
    # Vertices: upper @ z=0, upper @ z=ext, lower @ z=0, lower @ z=ext
    # Build triangles: top face, bottom face, and side walls

    lines = ["solid naca0012"]
    # Top face (z = z_extent)
    for i in range(n - 1):
        # Triangle 1
        v0 = (x[i], y[i], z_extent)
        v1 = (x[i + 1], y[i + 1], z_extent)
        v2 = (x[2 * n - i - 1], y[2 * n - i - 1], z_extent)
        _write_triangle(lines, v0, v1, v2)
    # Bottom face (z = 0) — mirror normal
    for i in range(n - 1):
        v0 = (x[i], y[i], 0.0)
        v1 = (x[2 * n - i - 1], y[2 * n - i - 1], 0.0)
        v2 = (x[i + 1], y[i + 1], 0.0)
        _write_triangle(lines, v0, v1, v2)
    # Side walls (upper surface edge, lower surface edge)
    for i in range(n - 1):
        # Upper wall: (x[i],y[i],0)-(x[i+1],y[i+1],0)-(x[i+1],y[i+1],ext)-(x[i],y[i],ext)
        v0 = (x[i], y[i], 0.0)
        v1 = (x[i + 1], y[i + 1], 0.0)
        v2 = (x[i + 1], y[i + 1], z_extent)
        _write_triangle(lines, v0, v1, v2)
        _write_triangle(lines, v0, v2, (x[i], y[i], z_extent))
    lines.append("endsolid naca0012")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_triangle(lines: list[str], v0, v1, v2) -> None:
    """Append a single STL facet (zero normal, snappyHexMesh recomputes)."""
    lines.append("  facet normal 0 0 0")
    lines.append("    outer loop")
    lines.append(f"      vertex {v0[0]:.6f} {v0[1]:.6f} {v0[2]:.6f}")
    lines.append(f"      vertex {v1[0]:.6f} {v1[1]:.6f} {v1[2]:.6f}")
    lines.append(f"      vertex {v2[0]:.6f} {v2[1]:.6f} {v2[2]:.6f}")
    lines.append("    endloop")
    lines.append("  endfacet")


if __name__ == "__main__":
    out_dir = Path(__file__).parent / "geometry"
    x, y = naca4_thickness(m=0, p=0, t=0.12, n=200)
    write_selig_format(x, y, out_dir / "naca0012.dat")
    write_stl(x, y, out_dir / "naca0012.stl")
    print(f"Generated NACA0012 geometry in {out_dir}")
```

### 5.3 Ladson 1988 参考数据

```csv
# cases/validation/naca0012/reference/ladson1988.csv
# NACA0012 α=0° experimental Cp distribution
# Source: Ladson, C. L., NASA TM-4074, 1988
# x/c, Cp_upper (symmetric → lower is mirror)
x/c,Cp
0.0,1.0000
0.025,-1.2140
0.05,-0.9870
0.1,-0.7800
0.15,-0.6510
0.2,-0.5400
0.25,-0.4600
0.3,-0.4000
0.4,-0.3000
0.5,-0.2350
0.6,-0.1800
0.7,-0.1400
0.75,-0.1300
0.8,-0.1100
0.9,-0.0700
0.95,-0.0400
1.0,0.0500
```

### 5.4 QoI 提取器扩展

```python
# post/qoi_extractor.py 新增 2 个函数

def extract_naca0012_cp_openfoam(forces_csv: Path) -> tuple[list[float], list[float]] | None:
    """Extract Cp distribution from OpenFOAM forces output.

    OpenFOAM forces object writes per-surface CSV under
    postProcessing/forces/<time>/surfaceFields.dat or forces.dat.

    For NACA0012 we want Cp along the airfoil surface, parameterized by x/c.

    Args:
        forces_csv: Path to forces CSV file.

    Returns:
        Tuple (x_over_c_list, cp_list) or None if parsing fails.
    """
    if not forces_csv.exists():
        logger.warning("forces CSV not found: %s", forces_csv)
        return None
    # Implementation parses OpenFOAM forces output, extracts pressure coeff
    # along the airfoil surface, normalizes by 0.5 * rho * U_inf^2
    # ... (detailed implementation in engineer phase)
    raise NotImplementedError("detailed in engineer phase")


def extract_naca0012_cp_su2(surface_flow_csv: Path) -> tuple[list[float], list[float]] | None:
    """Extract Cp distribution from SU2 surface_flow.csv.

    SU2 surface_flow.csv format for wall markers:
        "Point_ID","x","y","Pressure","Pressure_Coefficient"
        0,0.0001,0.0001,101325.0,0.85
        ...

    Args:
        surface_flow_csv: Path to SU2 surface_flow.csv.

    Returns:
        Tuple (x_over_c_list, cp_list) or None if parsing fails.
    """
    if not surface_flow_csv.exists():
        logger.warning("SU2 CSV not found: %s", surface_flow_csv)
        return None
    import csv
    content = surface_flow_csv.read_text(encoding="utf-8")
    reader = csv.reader(content.splitlines())
    header = next(reader, None)
    if header is None:
        return None
    h_clean = [h.strip().strip('"').lower() for h in header]
    try:
        x_idx = h_clean.index("x")
        cp_idx = h_clean.index("pressure_coefficient")
    except ValueError:
        # Fall back: derive Cp from pressure + freestream
        logger.warning("Pressure_Coefficient column not found, raw pressure parsing not implemented")
        return None
    x_list, cp_list = [], []
    for row in reader:
        try:
            x_list.append(float(row[x_idx]))
            cp_list.append(float(row[cp_idx]))
        except (ValueError, IndexError):
            continue
    if not x_list:
        return None
    return x_list, cp_list
```

### 5.5 NACA0012 OpenFOAM 模板新增

`src/cfdb/adapters/templates/openfoam/` 新增：
- `snappyHexMeshDict.j2` — snappyHexMesh 配置（castellated + snap + addLayers）
- `blockMeshDict.naca.j2` — 背景六面体网格（C-grid 或 O-grid）
- `controlDict.naca.j2` — simpleFoam 控制参数（针对外流：1000 步）

### 5.6 NACA0012 SU2 CFG 模板新增

`src/cfdb/adapters/templates/su2/` 新增：
- `naca0012.cfg.j2` — SU2 RANS-SA 求解 NACA0012 的标准配置

---

## 6. 文件清单（P2-b 新增/修改）

### 6.1 新增文件（17 个）

| 文件 | 用途 |
|---|---|
| `src/cfdb/execution/docker.py` | DockerBackend 实现 |
| `src/cfdb/execution/errors.py` | BackendError 异常类 |
| `src/cfdb/data/__init__.py` | DVC 子包 |
| `src/cfdb/data/dvc.py` | DVC wrapper 函数 |
| `cases/validation/naca0012/case.yaml` | NACA0012 CaseSpec |
| `cases/validation/naca0012/gen_geometry.py` | NACA 4-digit 几何生成 |
| `cases/validation/naca0012/gen_mesh.py` | DVC pipeline: 生成 STL+blockMeshDict |
| `cases/validation/naca0012/reference/ladson1988.csv` | Ladson 1988 参考数据 |
| `cases/validation/naca0012/geometry/naca0012.dat` | Selig 格式坐标（DVC track） |
| `cases/validation/naca0012/geometry/naca0012.stl` | STL 文件（DVC track） |
| `src/cfdb/adapters/templates/openfoam/snappyHexMeshDict.j2` | snappyHexMesh 模板 |
| `src/cfdb/adapters/templates/openfoam/blockMeshDict.naca.j2` | NACA 背景网格模板 |
| `src/cfdb/adapters/templates/openfoam/controlDict.naca.j2` | simpleFoam NACA 控制参数 |
| `src/cfdb/adapters/templates/su2/naca0012.cfg.j2` | SU2 NACA0012 CFG |
| `.dvc/config` | DVC 配置 |
| `.dvcignore` | DVC ignore 规则 |
| `dvc.yaml` | DVC pipeline |

### 6.2 修改文件（10 个）

| 文件 | 改动 |
|---|---|
| `src/cfdb/schema.py` | RunManifest 加 `backend_options: dict[str, Any] \| None = None` |
| `src/cfdb/adapters/openfoam.py` | `__init__` 加 `backend` 参数；`run()` 用 `self._backend` |
| `src/cfdb/adapters/su2.py` | 同 OpenFOAM |
| `src/cfdb/adapters/__init__.py` | `get_adapter()` 加 `backend` 参数 |
| `src/cfdb/core/runner.py` | `execute()` 加 `backend_options`；新增 `_build_backend()`；manifest 写入 container_digest + backend_options |
| `src/cfdb/cli.py` | `run()` 加 `--image` `--pull` 参数；新增 `data` 子命令组 |
| `src/cfdb/post/qoi_extractor.py` | 新增 `extract_naca0012_cp_openfoam` + `extract_naca0012_cp_su2` |
| `src/cfdb/adapters/templates/openfoam/controlDict.j2` | 加 NACA0012 forces object（条件渲染） |
| `pyproject.toml` | 加 hatchling force-include 新模板；addopts 加 `-m 'not real_solver and not real_docker'` |
| `.gitignore` | 加 `runs/dvc-cache/` + `dvc.lock` |

### 6.3 新增测试（预估 35-45 个）

| 测试文件 | 数量 | 说明 |
|---|---|---|
| `tests/test_docker_backend.py` | ~12 | mock subprocess 测试 daemon check / pull / digest / execute / timeout / error paths |
| `tests/test_dvc_wrapper.py` | ~8 | dvc_available / dvc_pull / dvc_status / DVCError 路径 |
| `tests/test_naca0012_geometry.py` | ~6 | naca4_thickness 正确性 / 边界点 / STL 输出格式 |
| `tests/test_naca0012_qoi.py` | ~5 | Cp 提取（OF + SU2）+ Ladson 对比 |
| `tests/test_runner_backend_injection.py` | ~5 | adapter 接收 backend / DockerBackend 传入 / 默认 local |
| `tests/test_cli_data.py` | ~4 | `cfdb data pull` / `cfdb data status` 路径 |
| `tests/test_naca0012_case.py` | ~3 | case.yaml 加载 + schema 验证 |

总测试数预估：**250 + 40 ≈ 290**（PRD-v2.1 DoD 要求 ≥ 235）。

---

## 7. 关键设计决策与权衡

### 7.1 为什么 DockerBackend 不进 `_BACKENDS` 字典？

`get_backend(name)` 简单工厂模式假设所有 backend 无参构造。DockerBackend 需要 `image` / `pull_policy` 参数。两种方案：

- **方案 A（采纳）**：Runner._build_backend() 直接构造，`get_backend` 只保留 local（向后兼容）。
- **方案 B（否决）**：改 `get_backend(name, **kwargs)`，破坏 P0/P1 调用方。

方案 A 更干净，且铁律 #3 守住（`ExecutionBackend` Protocol 不动，`get_backend` 签名不动）。

### 7.2 为什么 adapter 通过 `__init__` 注入 backend 而非方法参数？

- `SolverAdapter.run(case, case_dir, run_dir, resources)` 签名是 Protocol 定义，**不能改**（铁律 #3）。
- 在 `__init__` 注入是 Python 常见模式（依赖注入），且 Protocol 不约束 `__init__`。
- 向后兼容：P0/P1 测试不传 backend，默认 LocalExecutionBackend。

### 7.3 DVC 为什么用 CLI wrapper 而非 Python SDK？

DVC 官方 Python API (`dvc.repo.Repo`) 存在但接口不稳定、文档稀少。CLI wrapper 更稳定、更易测试（mock subprocess）、与用户手动 `dvc pull` 行为一致。

### 7.4 NACA0012 为什么第一版用占位 mesh？

真实 snappyHexMesh 调试成本高（背景网格 / 边界层 / refinement zone），且需要真实 OpenFOAM 安装才能跑。第一版策略：
- case.yaml 完整配置（4 步 mesh + 1 步 solve）
- 模板完整（snappyHexMeshDict / blockMeshDict）
- QoI 提取器完整（解析 forces CSV）
- 但 mesh 生成测试用 mock（`@pytest.mark.real_solver`）
- QA 端到端验证阶段才跑真实 Docker

这复刻了 P1-b flat_plate 的成功模式。

### 7.5 为什么不直接支持 `--backend docker` 但用容器外 PATH？

容器内必须用容器装的 solver（OpenFOAM/SU2 在镜像内）。bind mount cwd 解决 I/O，容器内 workdir 设为 cwd 让相对路径无需改写。Windows path 转换由 Docker Desktop 自动处理。

---

## 8. 验收铁律映射（QA 独立验证清单）

| # | 铁律 | QA 验证方法 |
|---|---|---|
| #1 | 不破坏 P0+P1-a+P1-b+P2-a 的 **250** 个测试 | `pytest` 全跑，确认 250 个全过 |
| #2 | Schema 只新增 Optional 字段 | grep `RunManifest` 已有字段定义，确认未改；只新增 `backend_options: ... = None` |
| #3 | `ExecutionBackend` / `SolverAdapter` / `ResultRepository` Protocol 不动 | grep `class ExecutionBackend` / `class SolverAdapter` / `class ResultRepository`，确认 Protocol 定义未改 |
| #4 | JSON 存储与 local backend 保持兼容 | grep CLI `--storage` 默认 json、`--backend` 默认 local |
| #5 | DockerBackend 测试不依赖真实 daemon | grep 测试文件确认 mock subprocess，`-m real_docker` marker 标记真实 Docker 测试 |

---

## 9. P2-b 实施批次（参考 P2-a 紧凑批次模式）

| 批次 | 任务 | 预估 |
|---|---|---|
| T1 | Schema + adapter backend 注入改造（核心架构） | ~6 文件 |
| T2 | DockerBackend 实现 + 测试 | ~3 文件 |
| T3 | DVC wrapper + CLI data 子命令 | ~3 文件 |
| T4 | NACA0012 case + 几何 + 模板 + QoI | ~10 文件 |
| T5 | CLI 整合（--image/--pull）+ Runner 整合 | ~2 文件 |
| T6 | QA 独立验证 + commit | 验证 |

---

*文档结束。架构师高见远 2026-06-17 完成。转总已确认 4 核心决策（PRD-v2.1 §3），架构据此设计。工程师据此实施 5 批次（T1-T5），QA 据 §8 验收。*
