"""Bounded scene inspection and per-UUID Cycles capability probes."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.blender.command import ADAPTER_ROOT, RenderCommand
from app.blender.devices import GpuDevice, GpuDiscovery, GpuScheduler
from app.blender.inspection_schemas import DeviceCapabilities, SceneMetadata
from app.blender.runner import RunnerError, SandboxRunner
from app.blender.sandbox import build_worker_environment
from app.jobs.schemas import JobUpdate
from app.jobs.types import ComputeDevice, FrameMode, GpuRenderMode, RenderEngine
from app.storage.jobs import contained_path

PREFIX = "RENDER_NODE_INSPECTION "


class InspectionError(RuntimeError):
    pass


def choose_configuration(
    scene: SceneMetadata,
    *,
    name: str,
    devices: list[GpuDevice],
    capabilities: dict[str, DeviceCapabilities],
) -> JobUpdate:
    engine_name = "BLENDER_EEVEE" if scene.engine == "BLENDER_EEVEE_NEXT" else scene.engine
    try:
        engine = RenderEngine(engine_name)
    except ValueError as exc:
        raise InspectionError("The scene's render engine is not supported by Render Node") from exc

    device = ComputeDevice.CPU
    selected: list[GpuDevice] = []
    # CUDA/OptiX controls belong to Cycles. Eevee/Workbench select their graphics
    # device themselves; CPU here means no Cycles allocation, not CPU rasterization.
    if engine is RenderEngine.CYCLES and not scene.use_guiding:
        for backend in (ComputeDevice.OPTIX, ComputeDevice.CUDA):
            if scene.use_osl and backend is not ComputeDevice.OPTIX:
                continue
            compatible = [
                gpu
                for gpu in devices
                if backend.value
                in capabilities.get(gpu.uuid, DeviceCapabilities(backends=[])).backends
            ]
            if compatible:
                device = backend
                selected = sorted(compatible, key=lambda gpu: (-gpu.memory_total_bytes, gpu.id))[
                    :16
                ]
                break
    if scene.frame_count > 1:
        selected = selected[: scene.frame_count]
    # Distributed workers rewrite their frame bounds through RNA, whose setters
    # clamp negative values to zero. Keep such scenes in one Blender process so
    # ALL can preserve the loaded bounds and every saved frame is rendered once.
    distributed = len(selected) >= 2 and scene.frame_count > 1 and scene.frame_start >= 0
    # ALL preserves saved zero/negative bounds without rewriting them via RNA
    # setters (which clamp to zero). Positive bounds remain editable in the form.
    frame_mode = (
        FrameMode.ALL
        if scene.frame_start < 1
        else FrameMode.SINGLE
        if scene.frame_count == 1
        else FrameMode.RANGE
    )
    return JobUpdate(
        name=name,
        engine=engine,
        device=device,
        gpu_ids=[gpu.id for gpu in selected],
        gpu_render_mode=(
            GpuRenderMode.FRAME_PER_GPU if distributed else GpuRenderMode.ALL_GPUS_PER_FRAME
        ),
        frame_mode=frame_mode,
        frame_start=scene.frame_start if frame_mode is not FrameMode.ALL else None,
        frame_end=scene.frame_end if frame_mode is FrameMode.RANGE else None,
    )


class SceneInspector:
    def __init__(
        self,
        runner: SandboxRunner,
        discovery: GpuDiscovery,
        scheduler: GpuScheduler,
        *,
        timeout_seconds: float,
    ) -> None:
        self._runner = runner
        self._discovery = discovery
        self._scheduler = scheduler
        self._timeout_seconds = timeout_seconds

    async def initialize(self) -> None:
        await self._runner.initialize()

    async def inspect(
        self, *, binary: Path, job_directory: Path, scene_path: str, name: str
    ) -> tuple[SceneMetadata, JobUpdate]:
        scene_file = contained_path(job_directory, job_directory / scene_path)
        scratch = contained_path(job_directory, job_directory / "temp" / f"analysis-{uuid4()}")
        await asyncio.to_thread(scratch.mkdir)
        try:
            async with asyncio.timeout(self._timeout_seconds):
                raw_scene = await self._script(binary, scratch, "inspect_scene.py", scene_file)
                scene = SceneMetadata.model_validate_json(raw_scene)
                capabilities: dict[str, DeviceCapabilities] = {}
                devices: list[GpuDevice] = []
                if scene.engine == "CYCLES" and not scene.use_guiding:
                    startup = {gpu.uuid for gpu in self._scheduler.inventory}
                    devices = [
                        gpu
                        for gpu in await asyncio.to_thread(self._discovery.discover)
                        if gpu.uuid in startup
                    ]
                    for gpu in devices:
                        try:
                            raw = await self._script(
                                binary, scratch, "inspect_devices.py", gpu_uuid=gpu.uuid
                            )
                            capabilities[gpu.uuid] = DeviceCapabilities.model_validate_json(raw)
                        except (InspectionError, ValidationError):
                            task = asyncio.current_task()
                            if task is not None and task.cancelling():
                                raise
                            # A broken device must not hide other usable GPUs.
                            continue
                configuration = choose_configuration(
                    scene, name=name, devices=devices, capabilities=capabilities
                )
                return scene, configuration
        except TimeoutError as exc:
            raise InspectionError(
                "Scene analysis timed out; adjust settings manually or replace the file"
            ) from exc
        except ValidationError as exc:
            raise InspectionError("Blender returned incomplete scene information") from exc
        finally:
            await asyncio.to_thread(shutil.rmtree, scratch, True)

    async def _script(
        self,
        binary: Path,
        scratch: Path,
        script_name: str,
        scene_file: Path | None = None,
        *,
        gpu_uuid: str | None = None,
    ) -> str:
        output = scratch / "output"
        await asyncio.to_thread(output.mkdir, exist_ok=True)
        arguments = [str(binary), "--background", "--factory-startup", "--disable-autoexec"]
        if scene_file is not None:
            arguments.append(str(scene_file))
        arguments.extend(("--python-exit-code", "1", "--python", str(ADAPTER_ROOT / script_name)))
        payload: str | None = None
        duplicate = False

        async def on_line(line: str) -> None:
            nonlocal payload, duplicate
            if line.startswith(PREFIX):
                duplicate = payload is not None or duplicate
                payload = line[len(PREFIX) :] if len(line) <= 16_384 else ""

        async def on_started(_pid: int) -> None:
            return None

        environment = await build_worker_environment(
            scratch, device=ComputeDevice.CUDA, gpu_uuids=[gpu_uuid] if gpu_uuid else []
        )
        try:
            result = await self._runner.run(
                RenderCommand(tuple(arguments), scratch, output, scratch / "inspection.log"),
                job_id=UUID(scratch.name.removeprefix("analysis-")),
                environment=environment,
                cancellation=asyncio.Event(),
                on_started=on_started,
                on_line=on_line,
            )
        except RunnerError as exc:
            raise InspectionError(
                "Blender scene analysis is unavailable in the current resource boundary"
            ) from exc
        if result.timed_out:
            raise InspectionError("Blender scene analysis timed out")
        if result.limit_exceeded:
            raise InspectionError("Scene analysis exceeded its resource limits")
        if result.exit_code or result.cancelled or not payload or duplicate:
            raise InspectionError(
                "Blender could not inspect this file; check the scene and Blender version"
            )
        return payload
